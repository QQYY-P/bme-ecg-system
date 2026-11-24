"""Versioned QRS/RR results over immutable uploaded samples, without NN inference."""
from array import array
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

VERSION = 'cloud-qrs-rr-v1'
MAX_SAMPLES = 1000000
ROOT = Path(__file__).resolve().parent
SOURCES = ('physiology_analysis.py', 'research/streaming_qrs.py', 'research/physiology.py',
           'cloud_model/vendor/rpeak_detector.py', 'cloud_model/vendor/signal_quality.py',
           'cloud_model/vendor/notch_filter.py')
PARAMETERS = {'sample_rate_hz': 360, 'block_size': 36, 'rr_limits_ms': [280., 2500.],
              'source_policy': 'serial/replay + mode=0 + valid ADC; reset at every discontinuity',
              'nn_policy': 'normality_not_confirmed'}


class PhysiologyService:
    def __init__(self, data):
        self.directory = Path(data) / 'physiology'
        self.ready = False
        self.fingerprint = 'unavailable'
        try:
            import numpy
            import scipy
            from research.streaming_qrs import detect_adc
            from research.physiology import interval_statistics
            self.detect = detect_adc
            self.statistics = interval_statistics
            digest = hashlib.sha256()
            for name in SOURCES:
                digest.update(name.encode())
                digest.update((ROOT/name).read_bytes())
            digest.update((numpy.__version__ + ':' + scipy.__version__).encode())
            self.fingerprint = digest.hexdigest()
            self.ready = True
        except ImportError:
            # Storage/login stay usable when optional analysis dependencies are absent.
            pass

    def digest(self, metadata, complete, manifest):
        payload = {'metadata': metadata, 'complete': bool(complete), 'manifest': manifest,
                   'algorithm_version': VERSION, 'algorithm_fingerprint': self.fingerprint,
                   'parameters': PARAMETERS}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def response(self, sid, status, message, digest, **fields):
        return {'session_id': sid, 'analysis_kind': 'physiology', 'analysis_status': status,
                'available': self.ready, 'message': message, 'source_digest': digest,
                'algorithm_version': VERSION, 'algorithm_fingerprint': self.fingerprint,
                'parameters': PARAMETERS, 'analyzed_at': None, **fields}

    def path(self, sid, digest):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',sid) or not re.fullmatch(r'[a-f0-9]{64}',digest):
            raise ValueError('分析记录或摘要格式不正确')
        return self.directory/sid/(digest+'.json')

    def read(self, sid, digest):
        path = self.path(sid,digest)
        if not self.ready:
            return self.response(sid,'not_configured','QRS/RR 分析依赖未安装',digest)
        if not path.is_file():
            return self.response(sid,'not_run','尚未分析当前记录版本，可开始 QRS/RR 分析',digest)
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            if (saved['session_id'] != sid or saved['source_digest'] != digest
                    or saved['analysis_status'] not in ('complete','ineligible')):
                raise ValueError('分析结果标识不一致')
            return saved
        except (OSError, ValueError, KeyError):
            return self.response(sid,'failed','QRS/RR 结果无法读取，请重新分析',digest)

    def save(self, sid, digest, response):
        target = self.path(sid,digest)
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary = target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('x',encoding='utf-8') as stream:
                json.dump(response,stream,ensure_ascii=False,allow_nan=False,separators=(',',':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary,target)
            if os.name != 'nt':
                descriptor = os.open(target.parent,os.O_RDONLY)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def run(self, sid, metadata, complete, sample_count, digest, batches):
        self.path(sid,digest)
        if not self.ready:
            return self.response(sid,'not_configured','QRS/RR 分析依赖未安装',digest)
        if not complete: raise ValueError('请先完成上传和清单校验')
        if not 0 <= sample_count <= MAX_SAMPLES: raise ValueError('单次 QRS/RR 分析限100万点，请按会话分段保存')
        if metadata.get('sample_rate_hz') != 360: raise ValueError('QRS/RR 分析仅支持360 Hz记录')
        started = time.perf_counter()
        allowed_source = metadata.get('source') in ('serial','replay')
        previous = None
        total = eligible = allowed = 0
        run_start = epoch_base = 0
        run = array('d')
        peaks, segments = [], []
        exclusions = Counter()
        detector_seconds = 0.

        def flush():
            nonlocal run, allowed, epoch_base, detector_seconds
            if not run: return
            result = self.detect(run,block_size=PARAMETERS['block_size'])
            peaks.extend(run_start+int(p) for p in result['r_samples'])
            segments.extend(epoch_base+int(s) for s in result['segments'])
            epoch_base += max(result['segments'],default=-1)+2
            allowed += result['allowed_samples']
            detector_seconds += result['performance']['wall_seconds']
            run = array('d')

        for _, rows in batches:
            for row in rows:
                sample, segment, index, sequence, adc, flags, mode = row
                if previous is not None and sample <= previous[0]:
                    raise ValueError('采样编号必须严格递增')
                total += 1
                if total > MAX_SAMPLES or total > sample_count: raise ValueError('输入点数与会话索引不同')
                boundary = previous is not None and (sample != previous[0]+1 or segment != previous[1]
                    or index != previous[2]+1 or bool(flags & 28))
                if boundary: flush()
                reason = ('source_not_real' if not allowed_source else 'board_simulated' if mode == 1
                          else 'mode_unknown' if mode != 0 else 'invalid_adc' if not 0 < adc < 4095 or flags & 3 else None)
                if reason:
                    flush()
                    exclusions[reason] += 1
                else:
                    if not run: run_start = sample
                    run.append(adc)
                    eligible += 1
                previous = row
        flush()
        if total != sample_count: raise ValueError('输入点数与会话索引不同')
        statistics = self.statistics(peaks,segments=segments,fs=360,observation_seconds=total/360,
                                     rr_limits_ms=PARAMETERS['rr_limits_ms'])
        intervals = []
        for i,(left,right) in enumerate(zip(peaks,peaks[1:])):
            value = (right-left)*1000/360
            reason = ('segment_boundary' if segments[i] != segments[i+1]
                      else 'rr_range' if not 280 <= value <= 2500 else None)
            intervals.append({'start_sample':left,'end_sample':right,'rr_ms':value,
                              'accepted':reason is None,'reason':reason})
        status = 'complete' if statistics['rr']['intervals'] else 'ineligible'
        message = ('QRS/RR 分析完成；HRV 缺少可靠的正常窦性间期依据' if status == 'complete'
                   else '没有足够的连续有效 R 峰，暂不提供 RR 指标')
        result = self.response(sid,status,message,digest,
            summary={'sample_count':total,'observed_seconds':total/360,'eligible_samples':eligible,
                     'quality_allowed_samples':allowed,'quality_coverage':allowed/total if total else None,
                     'qrs_count':len(peaks),'skipped_reasons':dict(exclusions)},
            r_peaks=[{'sample_id':p,'segment_id':s} for p,s in zip(peaks,segments)],
            rr_intervals=intervals,rr=statistics['rr'],excluded=statistics['excluded'],
            hrv={'status':'unavailable','reason':'normality_not_confirmed','metrics':None,
                 'message':'尚无可信的正常窦性间期标记，仅报告 RR 变异性；模型 N 类不能代替 NN。'},
            performance={'processing_seconds':time.perf_counter()-started,'detector_seconds':detector_seconds,
                         'scope':'record read, QRS and RR processing; excludes queue wait, classification and network'})
        result['analyzed_at'] = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        self.save(sid,digest,result)
        return result
