import unittest

import numpy as np
from scipy.signal import iirnotch, lfilter, lfilter_zi

from notch_filter import NotchFilter, A, B, SAMPLE_RATE, STARTUP_SAMPLES


class NotchTests(unittest.TestCase):
    def test_coefficients_match_design(self):
        b, a = iirnotch(50, 30, fs=360)
        np.testing.assert_array_equal(B, b)
        np.testing.assert_array_equal(A, a)
        self.assertTrue((np.abs(np.roots(A)) < 1).all())

    def test_chunked_output_matches_causal_design(self):
        t = np.arange(4244) / SAMPLE_RATE
        raw = 2048 + 200 * np.sin(2 * np.pi * 5 * t) + 600 * np.sin(2 * np.pi * 50 * t)
        expected, _ = lfilter(B, A, raw, zi=lfilter_zi(B, A) * raw[0])
        for chunk_size in (1, 36, 108, 1000):
            processor = NotchFilter()
            results = [processor.process(raw[i:i + chunk_size])
                       for i in range(0, len(raw), chunk_size)]
            np.testing.assert_allclose(np.concatenate([r[0] for r in results]), expected, atol=1e-9, rtol=0)
            np.testing.assert_array_equal(np.concatenate([r[1] for r in results]),
                                          np.arange(len(raw)) < STARTUP_SAMPLES)

    def test_dc_is_preserved_from_first_sample(self):
        result, _ = NotchFilter().process(np.full(1000, 2048.0))
        np.testing.assert_allclose(result, 2048, atol=1e-8, rtol=0)

    def test_notch_attenuates_50hz_without_removing_5hz(self):
        t = np.arange(3600) / SAMPLE_RATE
        result, _ = NotchFilter().process(2048 + 100 * np.sin(2 * np.pi * 5 * t)
                                          + 600 * np.sin(2 * np.pi * 50 * t))
        y, tt = result[720:], t[720:]
        amplitude = lambda f: abs(2 * np.mean((y - y.mean()) * np.exp(-2j * np.pi * f * tt)))
        self.assertLess(amplitude(50), 0.1)
        self.assertGreater(amplitude(5), 99)
        self.assertLess(amplitude(5), 101)

    def test_gaps_reset_state_and_startup_window(self):
        processor = NotchFilter()
        processor.process(np.full(1000, 2000.0))
        y, startup = processor.process([np.nan, 3000, 3000, np.nan, np.inf, 1000])
        np.testing.assert_allclose(y, [np.nan, 3000, 3000, np.nan, np.nan, 1000], atol=1e-8)
        np.testing.assert_array_equal(startup, [False, True, True, False, False, True])
        processor.process([np.nan])
        result, warm = processor.process([3500])
        self.assertAlmostEqual(result[0], 3500)
        self.assertTrue(warm[0])

    def test_wrong_sample_rate_rejected(self):
        for rate in (100, 250, 500, 0):
            with self.assertRaises(ValueError):
                NotchFilter(rate)

    def test_empty_chunk_preserves_state(self):
        processor = NotchFilter()
        processor.process([2000, 2200, 2300])
        state = processor.state.copy()
        result, _ = processor.process([])
        self.assertEqual(result.size, 0)
        np.testing.assert_array_equal(state, processor.state)
