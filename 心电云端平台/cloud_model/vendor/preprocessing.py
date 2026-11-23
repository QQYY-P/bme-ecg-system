"""Causal, stateful display preprocessing for identified real ADC samples.

Outputs retain ADC-equivalent units: the caller converts once with Vref / 4095.
High-pass removal of the ADC bias makes processed voltages zero-centred; it
does not calibrate electrode voltage or compensate an unknown amplifier gain.
These monitor display defaults can alter morphology and are not a diagnostic
ECG bandwidth specification.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi, tf2sos

from .notch_filter import B, A

SAMPLE_RATE = 360.0
WARMUP_SAMPLES = 720
PREPROCESSING_CONFIG = {
    "version": 1,
    "applies_when": "source in serial/replay and per-sample mode == 0",
    "sample_rate_hz": 360,
    "voltage_conversion": "adc_equivalent * vref_v / 4095",
    "voltage_reference": "ADC input; uncalibrated; amplifier gain unknown",
    "highpass": {"family": "Butterworth", "order": 2, "cutoff_hz": 0.5},
    "notch": {"center_hz": 50.0, "quality_factor": 30.0, "order": 2},
    "lowpass": {"family": "Butterworth", "order": 4, "cutoff_hz": 40.0},
    "implementation": "causal streaming SOS; state retained across batches",
    "warmup_samples": WARMUP_SAMPLES,
    "raw_samples_modified": False,
    "processed_output": "zero-centred ADC-equivalent counts, converted to V for display",
    "heart_rate_input": "processed counts after warmup; detector retains internal notch/QRS filtering",
}


def adc_to_volts(values, vref=3.3):
    """Convert raw or signed processed ADC-equivalent values once, without mutation."""
    if not np.isfinite(vref) or not 0.1 <= vref <= 10:
        raise ValueError("参考电压须在 0.1～10 V")
    return np.asarray(values, dtype=float) * float(vref) / 4095.0


class RealECGPreprocessor:
    """0.5 Hz high-pass -> 50 Hz notch -> 40 Hz low-pass, with bounded state."""

    def __init__(self, sample_rate=SAMPLE_RATE):
        if not np.isclose(sample_rate, SAMPLE_RATE, rtol=0, atol=1e-9):
            raise ValueError("实采预处理仅适用于 360 Hz 采样")
        self.sos = np.vstack((
            butter(2, 0.5, btype="highpass", fs=SAMPLE_RATE, output="sos"),
            tf2sos(B, A),
            butter(4, 40.0, btype="lowpass", fs=SAMPLE_RATE, output="sos"),
        ))
        self.initial_state = sosfilt_zi(self.sos)
        self.reset()

    def reset(self):
        self.state = None
        self.segment_samples = 0

    def process(self, values):
        values = np.asarray(values, dtype=float)
        if values.ndim != 1:
            raise ValueError("预处理输入必须是一维采样序列")
        output = np.full(values.size, np.nan)
        warmup = np.zeros(values.size, dtype=bool)
        if not values.size:
            return output, warmup
        finite = np.isfinite(values)
        edges = np.flatnonzero(np.diff(np.r_[False, finite, False]))
        for start, stop in zip(edges[::2], edges[1::2]):
            if start > 0:
                self.reset()
            if self.state is None:
                # Starting from the first actual ADC level avoids interpreting
                # the ~Vref/2 amplifier bias as a large turn-on pulse.
                self.state = self.initial_state * values[start]
            output[start:stop], self.state = sosfilt(self.sos, values[start:stop], zi=self.state)
            count = stop - start
            warm = min(count, max(0, WARMUP_SAMPLES - self.segment_samples))
            warmup[start:start + warm] = True
            self.segment_samples += count
        if not finite[-1]:
            self.reset()
        return output, warmup
