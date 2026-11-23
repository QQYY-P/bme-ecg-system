import unittest
from dataclasses import replace

import numpy as np

from contracts import SampleBatch, SamplePoint, DISCONTINUITY, INVALID, SATURATED, MODE_CHANGE, STREAM_RESYNC
from signal_analysis import SignalAnalyzer


def batch(values, start=0, segment=0, segment_start=None, flags=None, received=None, session="test",
          mode=None, source="synthetic"):
    segment_start = start if segment_start is None else segment_start
    flags = [0] * len(values) if flags is None else flags
    points = tuple(SamplePoint(start + i, segment, segment_start + i,
                               (start + i) % 256, int(v), int(flags[i]), mode)
                   for i, v in enumerate(values))
    return SampleBatch(session, source, points,
                       (start + len(values)) / 360 if received is None else received,
                       "2026-10-26T00:00:00+00:00")


def ecg(seconds=8):
    times = np.arange(int(seconds * 360)) / 360
    values = 2048 + 40 * np.sin(2 * np.pi * .3 * times) + 80 * np.sin(2 * np.pi * 50 * times)
    for peak in np.arange(.7, seconds - .5, .8):
        values += 900 * np.exp(-((times - peak) / .015) ** 2)
        values -= 220 * np.exp(-((times - peak - .04) / .02) ** 2)
    return np.rint(values).astype(int)


class SignalAnalysisTests(unittest.TestCase):
    def test_noise_hides_existing_hr_and_recovery_builds_new_rr(self):
        analyzer = SignalAnalyzer()
        first = analyzer.process(batch(ecg(8), source="serial", mode=0))
        self.assertIsNotNone(first['hr'])
        self.assertTrue(first['hr_allowed'])
        t = np.arange(36)/360
        result = analyzer.process(batch(np.rint(2048+800*np.sin(2*np.pi*90*t)),
                                        start=2880, source="serial", mode=0))
        self.assertEqual(result['signal_state'], 'noisy')
        self.assertFalse(result['hr_allowed'])
        self.assertIsNone(result['hr']); self.assertIsNone(result['rr_ms'])
        self.assertTrue(np.isfinite(result['wave']['raw']).all())
        # Raw waveform remains inspectable even while metrics are withheld.
        result = analyzer.process(batch(ecg(1), start=2916, source="serial", mode=0))
        self.assertIsNone(result['hr'])
        result = analyzer.process(batch(ecg(9), start=3276, source="serial", mode=0))
        self.assertEqual(result['signal_state'], 'good')
        self.assertAlmostEqual(result['hr'], 75, delta=1)

    def test_invalid_flat_saturated_and_timeout_have_distinct_signal_states(self):
        cases = (([65535], 'invalid'), ([4095], 'saturated'),
                 ([0], 'saturated'), ([2048]*720, 'flat'), ([4088]*720, 'saturated'))
        for values, expected in cases:
            with self.subTest(expected=expected):
                result = SignalAnalyzer().process(batch(values, source="serial", mode=0))
                self.assertEqual(result['signal_state'], expected)
                self.assertFalse(result['hr_allowed'])
                self.assertIsNone(result['hr']); self.assertIsNone(result['rr_ms'])
        analyzer = SignalAnalyzer()
        analyzer.process(batch(ecg(8), source='serial', mode=0, received=100))
        result = analyzer.snapshot(now=103)
        self.assertEqual(result['signal_state'], 'interrupted')
        self.assertFalse(result['hr_allowed'])

    def test_quality_and_peaks_preserve_chunk_invariance_through_noise_modes_and_gap(self):
        values = np.r_[ecg(8), np.rint(2048+800*np.sin(2*np.pi*90*np.arange(1800)/360)),
                       ecg(10), ecg(8)]
        switch = 2880+1800+3600
        points = tuple(SamplePoint(i, 0, i, i%256, int(value),
                                  DISCONTINUITY if i == 1500 else 0,
                                  0 if i < switch else 1) for i, value in enumerate(values))
        full_batch = SampleBatch('mixed', 'serial', points, len(points)/360, 'test')
        whole = SignalAnalyzer().process(full_batch)
        split = SignalAnalyzer(); xs, raw, filtered, peaks = [], [], [], []
        start = 0
        sizes = [1, 17, 72, 113, 300, 7, 36]
        index = 0
        while start < len(points):
            stop = min(len(points), start+sizes[index%len(sizes)])
            result = split.process(SampleBatch('mixed', 'serial', points[start:stop], stop/360, 'test'))
            xs.extend(result['wave']['x']); raw.extend(result['wave']['raw'])
            filtered.extend(result['wave']['filtered']); peaks.extend(result['peaks'])
            start = stop; index += 1
        np.testing.assert_allclose(xs, whole['wave']['x'], equal_nan=True)
        np.testing.assert_allclose(raw, whole['wave']['raw'], equal_nan=True)
        np.testing.assert_allclose(filtered, whole['wave']['filtered'], atol=1e-9, equal_nan=True)
        self.assertEqual(peaks, whole['peaks'])
        for name in ('hr', 'rr_ms', 'signal_state', 'hr_allowed', 'quality_metrics'):
            self.assertEqual(result[name], whole[name])

    def test_mode_change_resets_filter_and_heart_rate_even_without_segment_flag(self):
        analyzer = SignalAnalyzer()
        old = batch(ecg(8), source="serial", mode=0)
        self.assertIsNotNone(analyzer.process(old)["hr"])
        new = batch([2400] * 36, start=2880, source="serial", mode=1)
        result = analyzer.process(new)
        fresh = SignalAnalyzer().process(new)
        self.assertIsNone(result["hr"])
        self.assertIsNone(result["rr_ms"])
        self.assertTrue(np.isnan(result["wave"]["raw"][0]))
        np.testing.assert_allclose(result["wave"]["filtered"][1:], fresh["wave"]["filtered"])
        self.assertFalse(result["preprocessing_active"])

    def test_only_known_real_serial_or_replay_uses_preprocessing(self):
        values = ecg(3)
        for source, mode, active in (("serial", 0, True), ("replay", 0, True),
                                     ("serial", 1, False), ("serial", None, False),
                                     ("demo", 0, False), ("demo", 1, False),
                                     ("replay", None, False), ("synthetic", 0, False)):
            with self.subTest(source=source, mode=mode):
                input_batch = batch(values, source=source, mode=mode)
                result = SignalAnalyzer().process(input_batch)
                self.assertEqual(result["preprocessing_active"], active)
                np.testing.assert_array_equal([p.raw_adc for p in input_batch.points], values)
                np.testing.assert_array_equal(result["wave"]["raw"], values)
                if active:
                    self.assertLess(abs(np.mean(result["wave"]["filtered"])), 100)
                else:
                    np.testing.assert_array_equal(result["wave"]["filtered"], values)

    def test_real_preprocessing_chunk_invariance_and_heart_rate(self):
        values = ecg(10)
        full = SignalAnalyzer().process(batch(values, source="serial", mode=0))
        fragmented = SignalAnalyzer()
        filtered, peaks = [], []
        for start in range(0, len(values), 36):
            result = fragmented.process(batch(values[start:start + 36], start=start, source="serial", mode=0))
            filtered.extend(result["wave"]["filtered"])
            peaks.extend(result["peaks"])
        np.testing.assert_allclose(filtered, full["wave"]["filtered"], rtol=0, atol=1e-9)
        self.assertEqual(peaks, full["peaks"])
        self.assertAlmostEqual(result["hr"], 75, delta=1)
        self.assertAlmostEqual(result["rr_ms"], 800, delta=6)
        self.assertFalse(result["preprocessing_warmup"])

    def test_real_warmup_suppresses_hr_and_resets_after_invalid(self):
        analyzer = SignalAnalyzer()
        result = analyzer.process(batch(ecg(2), source="serial", mode=0))
        self.assertTrue(result["preprocessing_warmup"])
        self.assertEqual(result["quality"], "滤波稳定中")
        self.assertIsNone(result["hr"])
        self.assertEqual(result["peaks"], [])
        result = analyzer.process(batch([65535], start=720, source="serial", mode=0))
        self.assertFalse(result["preprocessing_active"])
        self.assertTrue(np.isnan(result["wave"]["filtered"][0]))
        result = analyzer.process(batch([2100] * 36, start=721, source="serial", mode=0))
        self.assertTrue(result["preprocessing_warmup"])
        self.assertIsNone(result["hr"])
        self.assertLess(np.nanmax(np.abs(result["wave"]["filtered"])), 1e-7)

    def test_real_filter_resets_at_each_discontinuity_kind(self):
        for flag in (MODE_CHANGE, STREAM_RESYNC, DISCONTINUITY):
            with self.subTest(flag=flag):
                analyzer = SignalAnalyzer()
                analyzer.process(batch(ecg(8), source="serial", mode=0))
                next_batch = batch([2700] * 36, start=2880, source="serial", mode=0, flags=[flag] + [0] * 35)
                result = analyzer.process(next_batch)
                self.assertTrue(result["preprocessing_warmup"])
                self.assertIsNone(result["hr"])
                self.assertLess(np.nanmax(np.abs(result["wave"]["filtered"])), 1e-7)

    def test_mode_and_resync_flags_break_context_without_sequence_anomaly(self):
        for flag in (MODE_CHANGE, STREAM_RESYNC):
            with self.subTest(flag=flag):
                analyzer = SignalAnalyzer()
                self.assertIsNotNone(analyzer.process(batch(ecg(5)))["hr"])
                result = analyzer.process(batch([2048] * 36, start=1800, flags=[flag] + [0] * 35))
                self.assertIsNone(result["hr"])
                self.assertTrue(np.isnan(result["wave"]["x"][0]))

    def test_known_ecg_rr_and_chunk_invariance(self):
        values = ecg()
        full = SignalAnalyzer().process(batch(values))
        fragmented = SignalAnalyzer()
        output, raw, filtered, peaks = [], [], [], []
        for start in range(0, len(values), 36):
            result = fragmented.process(batch(values[start:start + 36], start=start))
            output.extend(result["wave"]["x"])
            raw.extend(result["wave"]["raw"])
            filtered.extend(result["wave"]["filtered"])
            peaks.extend(result["peaks"])
        np.testing.assert_allclose(filtered, full["wave"]["filtered"])
        np.testing.assert_array_equal(raw, values)
        self.assertEqual(peaks, full["peaks"])
        self.assertAlmostEqual(result["hr"], 75, delta=1)
        self.assertAlmostEqual(result["rr_ms"], 800, delta=6)
        self.assertGreater(np.mean(filtered), 2000)  # Display stays in ADC units.

    def test_duplicate_flag_and_segment_change_do_not_bridge_rr(self):
        for use_flag in (True, False):
            analyzer = SignalAnalyzer()
            values = ecg(5)
            self.assertIsNotNone(analyzer.process(batch(values))["hr"])
            flags = [DISCONTINUITY] + [0] * 35 if use_flag else [0] * 36
            result = analyzer.process(batch([2048] * 36, start=len(values),
                segment=0 if use_flag else 1, segment_start=len(values) if use_flag else 0,
                flags=flags))
            self.assertIsNone(result["hr"])
            self.assertIsNone(result["rr_ms"])
            self.assertTrue(np.isnan(result["wave"]["x"][0]))

    def test_skipped_analysis_block_breaks_wave_and_clears_metrics(self):
        analyzer = SignalAnalyzer()
        analyzer.process(batch(ecg(5)))
        result = analyzer.process(batch([2048] * 36, start=1836))
        self.assertTrue(np.isnan(result["wave"]["raw"][0]))
        self.assertIsNone(result["hr"])

    def test_invalid_and_saturated_samples_are_masked_and_state_resets(self):
        for value, flag in ((65535, INVALID), (0, SATURATED), (4095, 0), (2048, INVALID)):
            analyzer = SignalAnalyzer()
            analyzer.process(batch(ecg(5)))
            result = analyzer.process(batch([value], start=1800, flags=[flag]))
            self.assertTrue(np.isnan(result["wave"]["raw"][0]))
            self.assertTrue(np.isnan(result["wave"]["filtered"][0]))
            self.assertIsNone(result["hr"])

    def test_receive_timeout_does_not_leave_old_hr(self):
        analyzer = SignalAnalyzer()
        analyzer.process(batch(ecg(5), received=100))
        self.assertIsNone(analyzer.snapshot(now=103)["hr"])
        self.assertEqual(analyzer.snapshot(now=103)["quality"], "数据中断")
        result = analyzer.process(batch([2048] * 36, start=1800, received=103.1))
        self.assertTrue(np.isnan(result["wave"]["x"][0]))

    def test_flat_continuing_stream_expires_previous_rr(self):
        analyzer = SignalAnalyzer()
        analyzer.process(batch(ecg(5)))
        for start in range(1800, 3600, 36):
            result = analyzer.process(batch([2048] * 36, start=start))
        self.assertIsNone(result["hr"])
        self.assertIsNone(result["rr_ms"])


if __name__ == "__main__":
    unittest.main()
