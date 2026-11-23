"""Bounded-window streaming QRS detector for the project's 360 Hz ECG.

The internal notch and QRS bandpass do not modify displayed/exported voltages.
Peaks are confirmed behind the receive head, never by padding future samples.
This is an observation aid, not a clinically validated annotation system.
"""
from collections import deque

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt

from notch_filter import NotchFilter, SAMPLE_RATE


class RPeakDetector:
    WINDOW = 4.0
    WARMUP = 1.0
    LOOKAHEAD = 0.4
    REFRACTORY = 0.28
    UPDATE_SAMPLES = 36  # 100 ms; independent of serial packet boundaries.

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.notch = NotchFilter(sample_rate)
        self.fs = float(sample_rate)
        self.sos = butter(2, [5, 20], btype="bandpass", fs=self.fs, output="sos")
        self.reset()

    def reset(self):
        self.notch.reset()
        self._reset_segment()

    def _reset_segment(self):
        self.times = deque(maxlen=int(self.WINDOW * self.fs))
        self.values = deque(maxlen=self.times.maxlen)
        self.count = 0
        self.finalized = self.last_peak = -np.inf
        self.last_time = None

    def process(self, times, raw):
        times, raw = np.asarray(times, dtype=float), np.asarray(raw, dtype=float)
        if times.ndim != 1 or raw.ndim != 1 or times.shape != raw.shape:
            raise ValueError("时间与采样值必须为等长的一维数组。")
        peaks = []
        # Process contiguous runs with the vectorized notch, then fixed cadence windows.
        breaks = np.flatnonzero(~np.isfinite(raw) | ~np.isfinite(times))
        jumps = np.flatnonzero(~np.isclose(np.diff(times), 1 / self.fs, rtol=0, atol=1e-6)) + 1
        boundaries = sorted(set([0, len(raw), *breaks, *(breaks + 1), *jumps]))
        for start, stop in zip(boundaries[:-1], boundaries[1:]):
            if start == stop:
                continue
            if not np.isfinite(raw[start]) or not np.isfinite(times[start]):
                self.reset()
                continue
            if self.last_time is not None and not np.isclose(
                    times[start] - self.last_time, 1 / self.fs, rtol=0, atol=1e-6):
                self.reset()
            filtered, _ = self.notch.process(raw[start:stop])
            for timestamp, value in zip(times[start:stop], filtered):
                self.times.append(float(timestamp))
                self.values.append(float(value))
                self.last_time = float(timestamp)
                self.count += 1
                if self.count >= int(self.WARMUP * self.fs) and self.count % self.UPDATE_SAMPLES == 0:
                    peaks.extend(self._detect())
        return np.asarray(peaks, dtype=float)

    def _detect(self):
        times = np.asarray(self.times)
        values = np.asarray(self.values)
        qrs = sosfiltfilt(self.sos, values)
        width = int(0.08 * self.fs) | 1
        energy = np.convolve(qrs * qrs, np.ones(width) / width, mode="same")
        median = float(np.median(energy))
        mad = float(np.median(np.abs(energy - median)))
        # Noise floor + local signal scale; flat traces cannot create detections.
        threshold = max(25.0, median + 6 * mad, 0.16 * float(energy.max()))
        candidates, _ = find_peaks(energy, height=threshold,
                                   prominence=max(10.0, threshold * 0.4),
                                   distance=int(self.REFRACTORY * self.fs))
        safe_end = times[-1] - self.LOOKAHEAD
        radius = round(0.12 * self.fs)
        result = []
        for candidate in candidates:
            # Decide each candidate once, after its refinement neighborhood arrives.
            if not (max(self.finalized, times[0] + 0.18) < times[candidate] <= safe_end):
                continue
            lo, hi = max(0, candidate - radius), min(len(values), candidate + radius + 1)
            baseline = np.median(values[max(0, candidate - 2 * radius):
                                        min(len(values), candidate + 2 * radius + 1)])
            # Dominant deflection supports an inverted lead as well as upright ECG.
            index = lo + int(np.argmax(np.abs(values[lo:hi] - baseline)))
            timestamp = float(times[index])
            if timestamp - self.last_peak >= self.REFRACTORY:
                result.append(timestamp)
                self.last_peak = timestamp
        self.finalized = safe_end
        return result
