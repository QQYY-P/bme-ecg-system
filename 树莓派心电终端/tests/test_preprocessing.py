import unittest

import numpy as np
from scipy.signal import sosfreqz

from preprocessing import RealECGPreprocessor, WARMUP_SAMPLES, adc_to_volts


class RealPreprocessingTests(unittest.TestCase):
    def test_adc_voltage_conversion_preserves_input_and_signed_output(self):
        values = np.array([0, 2048, 4095, -100, np.nan])
        original = values.copy()
        converted = adc_to_volts(values, 3.3)
        np.testing.assert_array_equal(values, original)
        np.testing.assert_allclose(converted[:4], [0, 2048 * 3.3 / 4095, 3.3, -100 * 3.3 / 4095])
        self.assertTrue(np.isnan(converted[-1]))
        for invalid in (0, -1, np.inf, np.nan):
            with self.assertRaises(ValueError):
                adc_to_volts([2048], invalid)

    def test_frequency_response_targets_and_passband(self):
        f = RealECGPreprocessor()
        _, h = sosfreqz(f.sos, worN=[0.1, 10, 50, 90], fs=360)
        gain = np.abs(h)
        self.assertLess(gain[0], 0.05)   # Baseline wander: at least ~26 dB.
        self.assertGreater(gain[1], 0.99)
        self.assertLess(gain[2], 1e-10)
        self.assertLess(gain[3], 0.04)   # High-frequency muscle-noise component.

    def test_combined_noise_is_attenuated_without_zeroing_ecg_band(self):
        t = np.arange(21600) / 360
        raw = (2048 + 200 * np.sin(2 * np.pi * .1 * t)
               + 120 * np.sin(2 * np.pi * 10 * t)
               + 80 * np.sin(2 * np.pi * 50 * t)
               + 60 * np.sin(2 * np.pi * 90 * t))
        original = raw.copy()
        output, warm = RealECGPreprocessor().process(raw)
        np.testing.assert_array_equal(raw, original)
        # Use a complete number of cycles after startup; phase delay is allowed.
        y, times = output[7200:], t[7200:]
        def amplitude(freq):
            basis = np.column_stack((np.sin(2 * np.pi * freq * times),
                                     np.cos(2 * np.pi * freq * times), np.ones(len(times))))
            coeff = np.linalg.lstsq(basis, y, rcond=None)[0]
            return np.hypot(coeff[0], coeff[1])
        self.assertLess(amplitude(.1), 10)
        self.assertAlmostEqual(amplitude(10), 120, delta=2)
        self.assertLess(amplitude(50), .01)
        self.assertLess(amplitude(90), 2.4)
        self.assertLess(abs(np.mean(y)), .1)
        self.assertEqual(int(warm.sum()), WARMUP_SAMPLES)

    def test_arbitrary_chunking_matches_continuous_causal_processing(self):
        rng = np.random.default_rng(123)
        values = 2048 + rng.normal(0, 100, 3000)
        full, expected_warm = RealECGPreprocessor().process(values)
        f = RealECGPreprocessor()
        out, warm = [], []
        for part in np.split(values, [1, 2, 17, 719, 720, 721, 1500, 2999]):
            y, w = f.process(part)
            out.extend(y); warm.extend(w)
        np.testing.assert_allclose(out, full, rtol=0, atol=1e-10)
        np.testing.assert_array_equal(warm, expected_warm)

    def test_early_output_never_uses_future_samples(self):
        prefix = np.linspace(2000, 2200, 500)
        a, _ = RealECGPreprocessor().process(np.r_[prefix, np.full(400, 1000)])
        b, _ = RealECGPreprocessor().process(np.r_[prefix, np.full(400, 3000)])
        np.testing.assert_array_equal(a[:500], b[:500])

    def test_constant_bias_and_gap_reset_do_not_make_turn_on_pulse(self):
        f = RealECGPreprocessor()
        output, warm = f.process(np.full(1000, 2048.0))
        self.assertLess(np.max(np.abs(output)), 1e-7)
        self.assertEqual(int(warm.sum()), 720)
        values = np.linspace(2300, 2400, 100)
        recovered, marked = f.process(np.r_[np.nan, values])
        fresh, fresh_warm = RealECGPreprocessor().process(values)
        self.assertTrue(np.isnan(recovered[0]))
        np.testing.assert_allclose(recovered[1:], fresh, rtol=0, atol=1e-10)
        np.testing.assert_array_equal(marked[1:], fresh_warm)

    def test_invalid_sample_rate_or_shape_rejected(self):
        with self.assertRaises(ValueError):
            RealECGPreprocessor(250)
        with self.assertRaises(ValueError):
            RealECGPreprocessor().process([[1, 2]])
        out, warm = RealECGPreprocessor().process([])
        self.assertEqual(len(out), 0)
        self.assertEqual(len(warm), 0)


if __name__ == "__main__":
    unittest.main()
