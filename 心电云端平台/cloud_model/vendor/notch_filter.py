"""Streaming form of the user's 360 Hz / 50 Hz / Q=30 notch design.

Source: ../优化/design_ecg_notch.py and
../优化/滤波器设计结果/2026-10-14_50Hz陷波器_系数与验证.json.
Bundled coefficients keep the app independent of the design folder.
"""
import numpy as np
from scipy.signal import lfilter, lfilter_zi

SAMPLE_RATE = 360.0
CENTER_HZ = 50.0
QUALITY = 30.0
STARTUP_SAMPLES = 720
B = np.array([0.9856631003631311, -1.2671440564772811, 0.9856631003631311])
A = np.array([1.0, -1.2671440564772811, 0.9713262007262622])


class NotchFilter:
    """Keep state across batches; reset at gaps, preserving the DC baseline."""

    def __init__(self, sample_rate=SAMPLE_RATE):
        if not np.isclose(sample_rate, SAMPLE_RATE, rtol=0, atol=1e-9):
            raise ValueError("此陷波器系数仅适用于 360 Hz 采样。")
        self.initial_state = lfilter_zi(B, A)
        self.reset()

    def reset(self):
        self.state = None
        self.segment_samples = 0

    def process(self, values):
        values = np.asarray(values, dtype=float)
        if values.ndim != 1:
            raise ValueError("滤波输入必须是一维采样序列。")
        output = np.full(values.size, np.nan)
        startup = np.zeros(values.size, dtype=bool)
        if not values.size:
            return output, startup
        finite = np.isfinite(values)
        edges = np.flatnonzero(np.diff(np.r_[False, finite, False]))
        for start, stop in zip(edges[::2], edges[1::2]):
            if start > 0:
                self.reset()
            if self.state is None:
                self.state = self.initial_state * values[start]
            output[start:stop], self.state = lfilter(B, A, values[start:stop], zi=self.state)
            warm = min(stop - start, max(0, STARTUP_SAMPLES - self.segment_samples))
            startup[start:start + warm] = True
            self.segment_samples += stop - start
        if not finite[-1]:
            self.reset()
        return output, startup
