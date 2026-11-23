"""Display-only filtering and conservative, segment-aware HR/RR estimates.

The R-peak path is the existing windowed energy detector, not Pan-Tompkins.
Raw input objects are never modified; displayed values retain ADC units.
"""
from collections import deque
import time

import numpy as np

from preprocessing import RealECGPreprocessor
from rpeak_detector import RPeakDetector
from signal_quality import SignalQualityGate
from contracts import INVALID, SATURATED, DISCONTINUITY, MODE_CHANGE, STREAM_RESYNC

FS = 360


class SignalAnalyzer:
    def __init__(self, timeout_seconds=2.5, peak_timeout_seconds=3.0):
        self.timeout_seconds = float(timeout_seconds)
        self.peak_timeout_seconds = float(peak_timeout_seconds)
        if self.timeout_seconds <= 0 or self.peak_timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        self.preprocessor = RealECGPreprocessor()
        self.detector = RPeakDetector()
        self.quality_gate = SignalQualityGate()
        self._ever_received = False
        self.reset()

    def _reset_analysis(self, quality="等待有效心拍", signal_state="waiting"):
        self.preprocessor.reset()
        self.detector.reset()
        self.quality_gate.reset(signal_state)
        self._detector_allowed = False
        self._peaks_expired = False
        self._segment = None
        self._expected_index = None
        self._last_sample_id = None
        self._mode = None
        self._mode_seen = False
        self._source_kind = None
        self.preprocessing_active = False
        self.preprocessing_warmup = False
        self._last_peak_time = None
        self._last_sample_time = None
        self._rr = deque(maxlen=5)
        self.hr = self.rr_ms = None
        self.quality = quality
        self._break_pending = self._ever_received

    def _clear_heart(self):
        self.detector.reset()
        self._last_peak_time = None
        self._rr.clear()
        self.hr = self.rr_ms = None
        self._peaks_expired = False

    def reset(self):
        self._session_id = None
        self._received_at = None
        self._reset_analysis()

    def _expire_peaks(self):
        if (self._last_peak_time is not None and self._last_sample_time is not None
                and self._last_sample_time - self._last_peak_time > self.peak_timeout_seconds):
            self.hr = self.rr_ms = None
            self._last_peak_time = None
            self._rr.clear()
            self._peaks_expired = True
            self.quality = "未检测到有效心拍"

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else float(now)
        if self._received_at is not None and now - self._received_at > self.timeout_seconds:
            self._reset_analysis("数据中断", "interrupted")
            self._received_at = None
        self._expire_peaks()
        status = self.quality_gate.snapshot()
        if status["hr_allowed"] and self.preprocessing_warmup:
            status.update(signal_state="settling", signal_text="滤波稳定中",
                          signal_detail="滤波器正在积累稳定数据，暂不计算心率", hr_allowed=False)
        elif self.preprocessing_warmup and status["signal_state"] == "collecting":
            status.update(signal_state="settling", signal_text="滤波稳定中",
                          signal_detail="正在积累滤波与质量评估所需的约2秒数据", hr_allowed=False)
        elif status["hr_allowed"] and self._peaks_expired:
            status.update(signal_state="no_peaks", signal_text="未检测到有效心拍",
                          signal_detail="暂未检测到连续可信心拍，心率暂停显示", hr_allowed=False)
        if not status["hr_allowed"]:
            self.hr = self.rr_ms = None
        return {"hr": self.hr, "rr_ms": self.rr_ms, "quality": self.quality,
                "preprocessing_active": self.preprocessing_active,
                "preprocessing_warmup": self.preprocessing_warmup, **status}

    def process(self, batch):
        if self._session_id != batch.session_id:
            self.reset()
            self._session_id = batch.session_id
        received_at = float(batch.received_monotonic)
        if self._received_at is not None and received_at - self._received_at > self.timeout_seconds:
            self._reset_analysis("数据中断后重新积累", "interrupted")
        if batch.points:
            self._received_at = received_at
        xs, raw_out, filtered_out, segment_out, peaks_out, run = [], [], [], [], [], []
        measurement = dict(analyzed_real_samples=sum(p.mode==0 for p in batch.points) if batch.source=='serial' else 0,
                           usable_real_samples=0,hr_weight=0,hr_total=0.,hr_min=None,hr_max=None)

        def flush_run():
            if not run:
                return
            times = np.asarray([p.sample_id / FS for p in run], dtype=float)
            values = np.asarray([p.raw_adc for p in run], dtype=float)
            self.preprocessing_active = batch.source in ("serial", "replay") and run[0].mode == 0
            permissions = self.quality_gate.process(values)
            if self.preprocessing_active:
                filtered, warmup = self.preprocessor.process(values)
                self.preprocessing_warmup = bool(warmup[-1])
                # Keep warmup output visible, but do not seed heart-rate history
                # with startup transients. The detector also needs its own context.
                permissions &= ~warmup
            else:
                filtered = values.copy()
                self.preprocessing_warmup = False
                # Board simulation and legacy unknown sources bypass display
                # preprocessing. The established HR detector keeps its QRS path.
            real_measurement = batch.source=='serial' and run[0].mode==0
            if real_measurement:
                measurement['usable_real_samples'] += int(np.count_nonzero(permissions))
            xs.extend(times.tolist())
            raw_out.extend(values.tolist())
            filtered_out.extend(filtered.tolist())
            segment_out.extend(point.segment_id for point in run)
            # Consume permissions on the sample clock, not the batch's final
            # state. A read may contain good -> bad -> good data; processing its
            # last quality backwards would leak old peaks into a new context.
            edges = np.r_[0, np.flatnonzero(permissions[1:] != permissions[:-1])+1, len(run)]
            for begin, end in zip(edges[:-1], edges[1:]):
                allowed = bool(permissions[begin])
                if allowed != self._detector_allowed:
                    self._clear_heart()
                    self._detector_allowed = allowed
                if not allowed:
                    continue
                peaks = self.detector.process(times[begin:end], filtered[begin:end])
                for peak in peaks:
                    peak = float(peak)
                    self._peaks_expired = False
                    if self._last_peak_time is not None:
                        rr = peak-self._last_peak_time
                        if 0 < rr <= self.peak_timeout_seconds:
                            self._rr.append(rr)
                            self.rr_ms = rr*1000
                            self.hr = 60/float(np.median(self._rr))
                            if real_measurement:
                                # Each accepted RR is counted once, irrespective
                                # of batch boundaries or the final displayed HR.
                                weight=max(1,int(round(rr*FS)))
                                rate=60/rr
                                measurement['hr_weight'] += weight
                                measurement['hr_total'] += rate*weight
                                measurement['hr_min'] = rate if measurement['hr_min'] is None else min(measurement['hr_min'],rate)
                                measurement['hr_max'] = rate if measurement['hr_max'] is None else max(measurement['hr_max'],rate)
                        else:
                            self._rr.clear()
                            self.hr = self.rr_ms = None
                    self._last_peak_time = peak
                    peaks_out.append(int(round(peak*FS)))
            gate = self.quality_gate.snapshot()
            if not gate["hr_allowed"]:
                self.quality = gate["signal_text"]
            elif self.preprocessing_warmup:
                self.quality = "滤波稳定中"
            else:
                self.quality = "可分析" if self.hr is not None else "等待有效心拍"
            self._last_sample_time = float(times[-1])
            self._expire_peaks()
            run.clear()

        for point in batch.points:
            flags = int(point.flags)
            valid = (not flags & (INVALID | SATURATED)
                     and 0 < int(point.raw_adc) < 4095)
            mode = getattr(point, "mode", None)
            boundary = (bool(flags & (DISCONTINUITY | MODE_CHANGE | STREAM_RESYNC))
                        or (self._mode_seen and mode != self._mode)
                        or (self._mode_seen and batch.source != self._source_kind)
                        or (self._segment is not None and point.segment_id != self._segment)
                        or (self._expected_index is not None and point.segment_index != self._expected_index)
                        or (self._last_sample_id is not None and point.sample_id != self._last_sample_id + 1))
            if boundary or not valid:
                flush_run()
                state = ("saturated" if (int(point.raw_adc) in (0, 4095) or flags & SATURATED)
                         and not flags & INVALID and int(point.raw_adc) != 65535 else "invalid")
                self._reset_analysis("信号无效" if not valid else "重新积累有效心拍",
                                     state if not valid else "waiting")
            if not valid:
                xs.append(point.sample_id / FS)
                raw_out.append(float("nan"))
                filtered_out.append(float("nan"))
                segment_out.append(-1)
                self._ever_received = True
                continue
            if self._break_pending:
                xs.append(float("nan"))
                raw_out.append(float("nan"))
                filtered_out.append(float("nan"))
                segment_out.append(-1)
                self._break_pending = False
            run.append(point)
            self._segment = point.segment_id
            self._expected_index = point.segment_index + 1
            self._last_sample_id = point.sample_id
            self._mode = mode
            self._source_kind = batch.source
            self._mode_seen = True
            self._ever_received = True
        flush_run()
        # Do not compare synthetic replay timestamps to this machine's wall clock.
        state = self.snapshot(now=received_at)
        return {"wave": {"x": np.asarray(xs), "raw": np.asarray(raw_out),
                         "filtered": np.asarray(filtered_out), "segment_ids": np.asarray(segment_out)},
                "peaks": peaks_out, 'measurement':measurement, **state}
