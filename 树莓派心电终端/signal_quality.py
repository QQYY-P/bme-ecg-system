"""Bounded engineering quality checks for the project's 360 Hz ADC stream.

These gates reject obvious acquisition/noise problems; they do not validate
ECG morphology, diagnose a person, or produce a clinical confidence score.
"""
from collections import deque

import numpy as np

QUALITY_CONFIG = {
    "version": 1,
    "sample_rate_hz": 360,
    "window_samples": 720,
    "update_samples": 36,
    "recovery_good_samples": 720,
    "flat_robust_span_counts": 8.0,
    "flat_std_counts": 2.0,
    "near_rail_margin_counts": 16,
    "near_rail_fraction": 0.1,
    "noise_min_rms_counts": 12.0,
    "high_frequency_start_hz": 40.0,
    "high_frequency_power_fraction": 0.55,
    "mains_band_hz": [48.0, 52.0],
    "mains_power_fraction": 0.55,
    "fast_jump_counts": 500.0,
    "fast_jump_fraction": 0.1,
    "basis": "raw ADC engineering defaults; not calibrated clinical thresholds",
}

TEXT = {
    "waiting": ("等待数据", "尚未收到可评估的采样点"),
    "collecting": ("质量评估中", "需要连续有效数据完成约2秒的质量评估"),
    "recovering": ("重新评估中", "异常后等待连续稳定数据，再重新积累心率"),
    "good": ("信号可用", "通过基础质量检查；心率仍需有效心拍"),
    "invalid": ("无效数据", "收到无效标记；可能为导联状态或模式切换，协议未给出具体原因"),
    "saturated": ("信号饱和", "ADC到达或持续接近量程边缘，请检查模块输出与接线"),
    "flat": ("信号平直", "连续约2秒变化过小，暂不计算心率；请检查信号输入"),
    "noisy": ("噪声过强", "干扰占比过高，暂不计算心率"),
    "interrupted": ("数据中断", "未持续收到采样数据，恢复后重新评估"),
}


class SignalQualityGate:
    """One sample clock; decisions every 36 samples, irrespective of read size."""

    WINDOW = QUALITY_CONFIG["window_samples"]
    CADENCE = QUALITY_CONFIG["update_samples"]

    def __init__(self):
        self._taper = np.hanning(self.WINDOW)
        self._trend = np.linspace(-1, 1, self.WINDOW)
        self._trend_norm = np.dot(self._trend, self._trend)
        self._freq = np.fft.rfftfreq(self.WINDOW, 1/360)
        self.reset()

    def reset(self, state="waiting"):
        if state not in TEXT:
            raise ValueError("未知信号质量状态")
        self.values = deque(maxlen=self.WINDOW)
        self.count = 0
        self.last_bad_count = None
        self.state = state
        self.detail = TEXT[state][1]
        self.metrics = {}

    @property
    def allowed(self):
        return self.state == "good"

    def snapshot(self):
        return {"signal_state": self.state, "signal_text": TEXT[self.state][0],
                "signal_detail": self.detail, "hr_allowed": self.allowed,
                "quality_metrics": dict(self.metrics)}

    def _set(self, state, detail=None):
        self.state = state
        self.detail = detail or TEXT[state][1]

    def _evaluate(self):
        values = np.asarray(self.values, dtype=float)
        recent = values[-self.CADENCE:]
        jumps = float(np.mean(np.abs(np.diff(recent)) >= QUALITY_CONFIG["fast_jump_counts"]))
        metrics = {"evaluated_samples": self.count, "window_samples": len(values),
                   "fast_jump_fraction": jumps}
        state, detail = "good", None
        if jumps >= QUALITY_CONFIG["fast_jump_fraction"]:
            state, detail = "noisy", "短时间内出现多次大幅突变，暂不计算心率"
        elif len(values) < self.WINDOW:
            state = "collecting"
        else:
            margin = QUALITY_CONFIG["near_rail_margin_counts"]
            rail = float(np.mean((values <= margin) | (values >= 4095-margin)))
            robust_span = float(np.percentile(values, 95)-np.percentile(values, 5))
            standard_deviation = float(np.std(values))
            centred = values-values.mean()
            centred -= np.dot(centred, self._trend)/self._trend_norm*self._trend
            power = np.abs(np.fft.rfft(centred*self._taper))**2
            total = float(np.sum(power[self._freq >= .5]))
            high = float(np.sum(power[self._freq >= 40])/max(total, 1e-20))
            mains = float(np.sum(power[(self._freq >= 48) & (self._freq <= 52)])/max(total, 1e-20))
            rms = float(np.sqrt(np.mean(centred**2)))
            metrics.update(near_rail_fraction=rail, robust_span_counts=robust_span,
                           std_counts=standard_deviation, detrended_rms_counts=rms,
                           high_frequency_fraction=high, mains_fraction=mains)
            if rail >= QUALITY_CONFIG["near_rail_fraction"]:
                state = "saturated"
            elif (robust_span <= QUALITY_CONFIG["flat_robust_span_counts"]
                  or standard_deviation <= QUALITY_CONFIG["flat_std_counts"]):
                state = "flat"
            elif rms >= QUALITY_CONFIG["noise_min_rms_counts"]:
                if mains >= QUALITY_CONFIG["mains_power_fraction"]:
                    state, detail = "noisy", "50Hz附近干扰占比过高，暂不计算心率"
                elif high >= QUALITY_CONFIG["high_frequency_power_fraction"]:
                    state, detail = "noisy", "高频干扰占比过高，暂不计算心率"
        self.metrics = metrics
        if state not in ("good", "collecting"):
            self.last_bad_count = self.count
        elif (state == "good" and self.last_bad_count is not None
              and self.count-self.last_bad_count < QUALITY_CONFIG["recovery_good_samples"]):
            state = "recovering"
        self._set(state, detail)

    def process(self, values):
        """Return a per-sample permission mask; input remains unchanged."""
        values = np.asarray(values, dtype=float)
        if values.ndim != 1 or not np.isfinite(values).all():
            raise ValueError("质量评估需要一维有限的有效ADC序列")
        if np.any((values <= 0) | (values >= 4095)):
            raise ValueError("无效或饱和ADC值须先分段处理")
        allowed = np.empty(len(values), dtype=bool)
        for i, value in enumerate(values):
            self.values.append(float(value))
            self.count += 1
            if self.count == 1:
                self._set("collecting")
            if self.count % self.CADENCE == 0:
                self._evaluate()
            allowed[i] = self.allowed
        return allowed
