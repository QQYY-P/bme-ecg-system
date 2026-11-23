"""Bounded, delayed streaming inference. No future data or expert R points.

Only identified real ADC input is eligible. Display filtering is deliberately
separate: this model needs its own 0.5-40 Hz morphology path. A sliding window
uses only received samples; a one-second right guard limits filter-edge effects.
Whole-record normalization in training cannot be reproduced online. The rolling
version below must be evaluated independently, not assigned the offline scores.
"""
from collections import deque
from pathlib import Path
import hashlib
import time

import numpy as np
from scipy.signal import butter, iirnotch, sosfiltfilt, tf2sos

from contracts import SAMPLE_RATE, DISCONTINUITY, MODE_CHANGE, STREAM_RESYNC
from preprocessing import adc_to_volts
from rpeak_detector import RPeakDetector
from signal_quality import SignalQualityGate
from .numpy_candidate import Candidate

FS = 360
LABELS = ("N", "S", "V")
WEIGHT_HASH = "1398ea833ff65c90d63ed3174722c9280fa6606ab2c165bfa7c53ca8fe264a41"
CONFIG = {
    "version": 1, "model": "CardioEdgeAI v1 INT8", "classes": list(LABELS),
    "weights_sha256": WEIGHT_HASH, "fs_hz": FS, "buffer_seconds": 20,
    "minimum_good_context_seconds": 8, "right_guard_seconds": 1,
    "filter": "50 Hz Q30 notch + Butterworth order4 0.5-40 Hz; sliding received-data sosfiltfilt",
    "normalization": "rolling filtered mean/std, exclude first/last 1s of available window",
    "voltage": "ADC * Vref/4095, ADC input volts; electrode gain uncalibrated",
    "rr": "previous,next,mean(previous up to 5),previous/mean; fixed source normalization",
    "r_detector": "existing 4s energy QRS detector on raw ADC, with 0.4s confirmation",
    "source_policy": "serial/replay mode0 only; simulation/unknown modes never classified",
    "quality": "existing raw-ADC signal gate plus finite/quantization checks",
    "result_stale_seconds": 3, "clinical_use": False,
}


class StreamingClassifier:
    def __init__(self, vref=3.3, sample_rate=FS, model=None):
        if sample_rate != FS or SAMPLE_RATE != FS:
            raise ValueError("模型仅接受已确认的 360 Hz 数据；不可静默使用其它采样率")
        adc_to_volts([], vref)
        self.vref = float(vref)
        if model is None:
            path = Path(__file__).with_name("portable_weights.npz")
            if hashlib.sha256(path.read_bytes()).hexdigest() != WEIGHT_HASH:
                raise ValueError("模型权重校验失败")
            model = Candidate(path)
        self.model = model
        b, a = iirnotch(50, 30, fs=FS)
        self.sos = np.vstack([tf2sos(b, a), butter(4, [.5, 40], fs=FS, btype="bandpass", output="sos")])
        self.gate = SignalQualityGate()
        self.detector = RPeakDetector()
        self.total_samples = self.classified = self.rejected = 0
        self._rejection_events = []
        self.counts = dict.fromkeys(LABELS, 0)
        self.reset()

    def _clear_features(self):
        self.values = deque(maxlen=20*FS)
        self.ids = deque(maxlen=20*FS)
        self.pending_ids, self.pending_raw = [], []
        self.peaks = deque(maxlen=64)
        self.finalized = -1
        self.context_first = None
        self.detector.reset()
        self.last = None

    def reset(self, reason="等待实采信号", *, clear_rejections=True):
        if clear_rejections:
            self._rejection_events = []
        self._clear_features()
        self.gate.reset()
        self.allowed = False
        self.previous = None
        self.key = None
        self.state, self.detail = "waiting", reason

    def snapshot(self):
        return dict(state=self.state, detail=self.detail, last=dict(self.last) if self.last else None,
                    classified=self.classified, rejected=self.rejected, counts=dict(self.counts),
                    processed_samples=self.total_samples, model=CONFIG["model"],
                    context_seconds=min(8., len(self.values)/FS), context_required_seconds=8.)

    def take_rejections(self):
        """Drain the latest process() batch, never a growing offline history."""
        result, self._rejection_events = self._rejection_events, []
        return result

    def _reject_candidate(self, r, head, reason):
        self.rejected += 1
        self.last = None
        self.state, self.detail = "rejected", reason
        self._rejection_events.append(dict(
            r_sample=int(r), classified_at_sample=int(head), reason=reason,
            source=self.key[1], mode=0, session_id=self.key[0], segment_id=self.key[2],
            sample_latency_ms=(head-r)*1000/FS, research_only=True))

    def _due(self):
        if len(self.values) < 8*FS or len(self.peaks) < 3:
            self.state, self.detail = "collecting", "积累约 8 秒有效信号与相邻心搏"
            return []
        peaks = list(self.peaks); head = self.ids[-1]; first = self.ids[0]
        due = []
        for k in range(1, len(peaks)-1):
            r = peaks[k]
            if r <= self.finalized:
                continue
            if r < self.context_first+8*FS or r-144 < first+FS:
                self.finalized = r
                continue
            if head-r < FS or r+216 > head+1:
                break
            due.append((k, r))
        if not due:
            if self.last is None:
                self.state, self.detail = "collecting", "等待相邻心搏与稳定窗口"
            return []
        volts = adc_to_volts(self.values, self.vref)
        filtered = sosfiltfilt(self.sos, volts)
        norm = filtered[FS:-FS]
        mean, std = float(norm.mean()), float(norm.std())
        results = []
        for k, r in due:
            self.finalized = r
            previous = (peaks[k]-peaks[k-1])/FS
            following = (peaks[k+1]-peaks[k])/FS
            intervals = np.diff(peaks[max(0,k-5):k+1])/FS
            if not np.isfinite(std) or std <= 1e-7 or np.any((intervals < .28) | (intervals > 2.5)) or not .28 <= following <= 2.5:
                self._reject_candidate(r, head, "心搏间期或幅度异常，暂不分类")
                continue
            local = float(intervals.mean())
            features = np.array([(previous-.8)/.3,(following-.8)/.3,(local-.8)/.3,(previous/local-1)/.3],np.float32)
            x = ((filtered[r-first-144:r-first+216]-mean)/std).astype(np.float32).reshape(360,1)
            sx,zx = self.model.qinfo(0); sr,zr = self.model.qinfo(1)
            wave_unclipped = np.rint(x/sx)+zx
            rr_unclipped = np.rint(features/sr)+zr
            clipping = float(np.mean((wave_unclipped < -128) | (wave_unclipped > 127)))
            if not np.isfinite(x).all() or clipping > .05 or np.any((rr_unclipped < -128) | (rr_unclipped > 127)):
                self._reject_candidate(r, head, "输入超出模型范围，暂不分类")
                continue
            qx = np.clip(wave_unclipped,-128,127).astype(np.int8)
            qr = np.clip(rr_unclipped,-128,127).astype(np.int8)
            started = time.perf_counter()
            output = self.model.predict_quantized(qx,qr)
            elapsed = (time.perf_counter()-started)*1000
            scores = (output.astype(float)+128)/256
            label = LABELS[int(scores.argmax())]
            result = dict(label=label, scores=dict(zip(LABELS,map(float,scores))),
                r_sample=int(r), classified_at_sample=int(head), sample_latency_ms=(head-r)*1000/FS,
                inference_ms=elapsed, rr_previous_s=previous, rr_next_s=following,
                normalization_std_v=std, quantization_clipped_fraction=clipping,
                source=self.key[1], mode=0, session_id=self.key[0], segment_id=self.key[2],
                research_only=True, requires_review=(label in ("S", "V")))
            self.last = result; results.append(result)
            self.classified += 1; self.counts[label] += 1
            self.state, self.detail = "ready", "S 类能力有限；分数不是诊断概率" if label=="S" else "研究输出；N 类包含束支阻滞"
        return results

    def _run(self, points):
        values = np.asarray([p.raw_adc for p in points],float)
        permissions = self.gate.process(values)
        results = []
        for p, value, allowed in zip(points, values, permissions):
            if bool(allowed) != self.allowed:
                self._clear_features()
                self.allowed = bool(allowed)
            if not allowed:
                self.state, self.detail = "quality", self.gate.snapshot()["signal_text"]+"，暂停分类"
                continue
            if self.context_first is None:
                self.context_first = p.sample_id
            self.ids.append(p.sample_id); self.values.append(float(value))
            self.pending_ids.append(p.sample_id); self.pending_raw.append(value)
            if len(self.pending_ids) == 36:
                detected = self.detector.process(np.asarray(self.pending_ids)/FS, self.pending_raw)
                self.peaks.extend(int(round(x*FS)) for x in detected)
                self.pending_ids.clear(); self.pending_raw.clear()
                results.extend(self._due())
            if self.last and p.sample_id-self.last["classified_at_sample"] > 3*FS:
                self.last = None; self.state, self.detail = "stale", "未检测到新的可信心搏"
        return results

    def process(self, batch):
        # Standalone validators need not call take_rejections(); retaining only
        # this batch keeps repeated offline replay bounded too.
        self._rejection_events = []
        results, run = [], []
        def flush():
            if run:
                results.extend(self._run(run)); run.clear()
        for p in batch.points:
            self.total_samples += 1
            key = (batch.session_id,batch.source,p.segment_id,p.mode)
            boundary = (key != self.key or p.flags & (DISCONTINUITY|MODE_CHANGE|STREAM_RESYNC)
                        or (self.previous is not None and (p.sample_id != self.previous.sample_id+1
                            or p.segment_index != self.previous.segment_index+1)))
            eligible = batch.source in ("serial","replay") and p.mode == 0 and p.valid
            if boundary or not eligible:
                flush()
                # Reset only on entry to an invalid span, not at every invalid sample.
                if boundary or self.allowed or self.gate.count:
                    self.reset(clear_rejections=False)
                if not eligible:
                    self.state = "simulation" if p.mode==1 or batch.source=="demo" else "invalid"
                    self.detail = "模拟数据不做分类" if self.state=="simulation" else "等待有效实采信号"
            self.key, self.previous = key, p
            if eligible:
                run.append(p)
        flush()
        return results
