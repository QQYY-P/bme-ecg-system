"""Raspberry Pi serial -> existing recorder -> HTTP uploader, with no model imports.

Installed into a separate directory by pi_link.py. The deployed uploader imports
cloud_protocol, so the terminal's serial protocol module keeps its own name.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

from uploader import Uploader, UploadError


def emit(kind, **values):
    print(json.dumps({'event': kind, 'time': datetime.now(timezone.utc).isoformat(), **values},
                     ensure_ascii=False), flush=True)


def session_info(directory):
    path = Path(directory) / 'index.sqlite'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)) as db:
        row = db.execute('SELECT session_id,metadata_json,status,sample_count FROM sessions').fetchone()
        if not row:
            return None
        return {'session_id': row[0], 'source': json.loads(row[1]).get('source'),
                'status': row[2], 'sample_count': row[3], 'directory': str(directory)}


def active_session(terminal):
    candidates = []
    for directory in (terminal/'data/sessions').glob('*'):
        try:
            info = session_info(directory)
            if info and info['source']=='serial' and info['status']=='recording':
                candidates.append(info)
        except (sqlite3.Error, OSError, ValueError):
            continue
    if not candidates:
        return None
    time.sleep(1.5)
    moving = []
    for old in candidates:
        try:
            current = session_info(old['directory'])
            if current and current['sample_count'] > old['sample_count']:
                moving.append(current)
        except (sqlite3.Error, OSError, ValueError):
            continue
    if len(moving) > 1:
        raise RuntimeError('检测到多个采集中的串口会话，需要先明确上传哪一个')
    return Path(moving[0]['directory']) if moving else None


def existing_port_owners(port):
    owners = []
    real = str(Path(port).resolve())
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc/'fd').iterdir():
                try:
                    if str(fd.resolve()) == real:
                        owners.append(int(proc.name))
                        break
                except OSError:
                    continue
        except (OSError, PermissionError):
            continue
    return owners


def summarize(directory, report_path, mode, error=None, upload_observation=None):
    from cloud_protocol import decode_chunk, valid
    info = session_info(directory)
    manifest = []
    counts = {'real_adc': 0, 'board_simulated': 0, 'unknown': 0, 'valid': 0, 'invalid': 0}
    low, high = None, None
    with closing(sqlite3.connect((directory/'index.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        descriptors = [json.loads(r[0]) for r in db.execute('SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
    for d in descriptors:
        manifest.append({k: d[k] for k in ('chunk_id','sha256','sample_count')})
        for row in decode_chunk((directory/'chunks'/f'{d["chunk_id"]:08d}.bin').read_bytes(), d['sha256']):
            counts[{0:'real_adc',1:'board_simulated'}.get(row[-1], 'unknown')] += 1
            counts['valid' if valid(row) else 'invalid'] += 1
            if valid(row):
                low = row[4] if low is None else min(low,row[4])
                high = row[4] if high is None else max(high,row[4])
    loaded = [name for name in ('torch','tensorflow','numpy_predictor','ecg_classifier','inference_worker',
                                'cardioedge','cardioedge.stream','cardioedge.numpy_candidate') if name in sys.modules]
    report = dict(info, acquisition_mode=mode, counts=counts, adc_min=low, adc_max=high,
                  manifest=manifest, loaded_model_modules=loaded, error=error,
                  upload_observation=upload_observation)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--terminal',type=Path,required=True)
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--url',required=True)
    p.add_argument('--duration',type=int,default=180)
    p.add_argument('--stop-file',type=Path,required=True)
    p.add_argument('--provenance-file',type=Path,help='本次预检后保存的来源和时钟注记，仅用于新采集')
    args=p.parse_args()
    if not 1 <= args.duration <= 600:
        raise ValueError('本次验证时长须为1至600秒')
    terminal=args.terminal.resolve()
    if not all((terminal/name).is_file() for name in ('recording.py','contracts.py','acquisition.py','serial_connection.py')):
        raise RuntimeError('未找到完整树莓派终端程序')
    sys.path.insert(0,str(terminal))
    from serial_connection import list_serial_ports, resolve_serial_port
    emit('preflight', ports=list_serial_ports(), terminal=str(terminal), collector_model_status='not_loaded')
    directory=active_session(terminal)
    recorder=source=None
    events=[]
    own=False
    started=time.monotonic()
    client=Uploader(args.url,state_dir=args.data_dir/'upload_state',timeout=5)
    last_error=None
    capture_error=None
    upload_failures=0
    recovery_events=0
    previous_failed=False
    last_source_count=0
    if directory:
        emit('attached', session_directory=str(directory), note='只读取已有串口会话，不改变现有采集程序')
    else:
        port=resolve_serial_port('auto')
        owners=existing_port_owners(port)
        if owners:
            raise RuntimeError(f'串口已被进程{owners}占用，未启动第二个采集器')
        from contracts import SAMPLE_RATE
        from acquisition import Acquisition
        from recording import Recorder
        sid='pi_serial_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
        metadata={'session_id':sid,'source':'serial','source_kind':'serial','sample_rate_hz':SAMPLE_RATE,
                  'started_utc':datetime.now(timezone.utc).isoformat(),'vref_v':3.3,'vref_calibrated':False,
                  'port':port,'serial_protocol':'AA55 MODE SEQ DATA_H DATA_L SUM8',
                  'note':'硬件串口上传验证，仅采集保存，不运行模型'}
        if args.provenance_file:
            provenance=json.loads(args.provenance_file.read_text(encoding='utf-8'))
            permitted={'kind','clock_status','clock_offset_seconds','clock_reference','clock_checked_at','origin_device','note'}
            if not isinstance(provenance,dict) or set(provenance)-permitted or provenance.get('kind')!='live':
                raise ValueError('新采来源注记不符合约定')
            metadata['provenance']=provenance
        recorder=Recorder(args.data_dir,metadata)
        recorder.start()
        def batch_handler(batch):
            if not recorder.submit(batch):
                source.stop()
                raise RuntimeError('原始记录队列拒绝数据')
            return True
        def event_handler(kind, detail):
            recorder.add_event(kind,detail)
            events.append({'kind':kind,'detail':detail})
            if len(events)>100:
                del events[0]
        source=Acquisition(sid,'serial',batch_handler,event_handler,port=port)
        source.start()
        directory=args.data_dir/'sessions'/sid
        own=True
        emit('capture_started', port=port, session_id=sid, duration=args.duration, collector_model_status='not_loaded')
    started=time.monotonic()
    try:
        while time.monotonic()-started < args.duration and not args.stop_file.exists():
            if own and not source.is_alive():
                capture_error=str(source.error or '串口采集提前结束')
                break
            try:
                if (directory/'index.sqlite').exists():
                    last_source_count=session_info(directory)['sample_count']
                    result=client.sync(directory)
                    emit('uploaded', **result,source_saved_samples=last_source_count,capture_elapsed_seconds=time.monotonic()-started)
                    if previous_failed:
                        recovery_events+=1
                        emit('upload_recovered',source_saved_samples=last_source_count,
                             confirmed_samples=result['confirmed_samples'],capture_elapsed_seconds=time.monotonic()-started)
                    previous_failed=False
                    last_error=None
                    if result['complete']:
                        break
            except (UploadError,OSError,ValueError,sqlite3.Error) as exc:
                last_error=str(exc)
                upload_failures+=1
                previous_failed=True
                emit('retry',message=last_error,source_saved_samples=last_source_count,capture_elapsed_seconds=time.monotonic()-started)
            if own and time.monotonic()-started>8 and recorder.status()['saved']==0:
                capture_error='串口打开后8秒未收到有效协议帧，请检查STM32接线和七字节固件'
                break
            time.sleep(2)
    finally:
        if own:
            source.stop()
            source.join(5)
            capture_error=capture_error or source.error or recorder.status()['error']
            if source.is_alive():
                capture_error='串口线程未能及时退出'
            recorder.finish(reason='upload_validation_finished', complete=not bool(capture_error))
            if capture_error:
                last_error=str(capture_error)
    if directory and (directory/'index.sqlite').exists():
        try:
            result=client.sync(directory)
            last_source_count=session_info(directory)['sample_count']
            emit('final_upload', **result,source_saved_samples=last_source_count,capture_elapsed_seconds=time.monotonic()-started)
            if previous_failed: recovery_events+=1
            last_error=capture_error
        except (UploadError,OSError,ValueError,sqlite3.Error) as exc:
            last_error=str(exc)
        report=summarize(directory,Path(__file__).parent/'board-result.json','new_serial_capture' if own else 'existing_serial_recording',last_error,
                         {'failed_attempts':upload_failures,'recovery_events':recovery_events,
                          'final_source_samples':last_source_count,'collector_changed_clock_during_capture':False})
        emit('finished',session_id=report['session_id'],sample_count=report['sample_count'],
             counts=report['counts'],loaded_model_modules=report['loaded_model_modules'],error=last_error)
    return 1 if last_error or capture_error else 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        emit('error',message=str(exc))
        raise SystemExit(1)
