import unittest

import numpy as np

from signal_quality import SignalQualityGate, QUALITY_CONFIG


def ecg(seconds=8):
    t = np.arange(round(seconds*360))/360
    values = 2048+40*np.sin(2*np.pi*.3*t)+80*np.sin(2*np.pi*50*t)
    for peak in np.arange(.7, seconds-.5, .8):
        values += 900*np.exp(-((t-peak)/.015)**2)
        values -= 220*np.exp(-((t-peak-.04)/.02)**2)
    return np.rint(values)


class SignalQualityTests(unittest.TestCase):
    def test_normal_ecg_with_moderate_mains_is_not_rejected(self):
        gate = SignalQualityGate()
        values = ecg()
        mask = gate.process(values)
        self.assertFalse(mask[:719].any())
        self.assertTrue(mask[719:].all())
        self.assertEqual(gate.snapshot()['signal_state'], 'good')
        self.assertLess(gate.snapshot()['quality_metrics']['mains_fraction'], .55)

    def test_flat_and_low_variation_need_full_two_seconds(self):
        for values in (np.full(720, 2048), 2048+np.tile([0, 1, -1], 240)):
            gate = SignalQualityGate()
            self.assertFalse(gate.process(values[:719]).any())
            self.assertEqual(gate.state, 'collecting')
            self.assertFalse(gate.process(values[719:]).any())
            self.assertEqual(gate.state, 'flat')

    def test_near_rail_plateau_is_saturation_not_flat(self):
        for value in (8, 4088):
            gate = SignalQualityGate()
            gate.process(np.full(720, value))
            self.assertEqual(gate.state, 'saturated')
            self.assertFalse(gate.allowed)

    def test_dominant_mains_hf_broadband_and_repeated_artifacts_block_hr(self):
        t = np.arange(1440)/360
        rng = np.random.default_rng(812)
        signals = {
            'mains': 2048+120*np.sin(2*np.pi*50*t),
            'hf': 2048+120*np.sin(2*np.pi*90*t),
            'white': 2048+rng.normal(0, 80, len(t)),
            'spikes': 2048+np.tile([900, 0, 0, 0, 0, 0], 240),
        }
        for name, values in signals.items():
            with self.subTest(name=name):
                gate = SignalQualityGate()
                gate.process(values)
                self.assertEqual(gate.state, 'noisy')
                self.assertFalse(gate.allowed)

    def test_severe_new_artifact_blocks_at_next_100ms_evaluation(self):
        gate = SignalQualityGate(); gate.process(ecg())
        self.assertTrue(gate.allowed)
        t = np.arange(36)/360
        mask = gate.process(2048+800*np.sin(2*np.pi*90*t))
        self.assertFalse(mask[-1])
        self.assertEqual(gate.state, 'noisy')

    def test_recovery_waits_for_new_clean_context(self):
        gate = SignalQualityGate()
        gate.process(2048+800*np.sin(2*np.pi*90*np.arange(720)/360))
        self.assertEqual(gate.state, 'noisy')
        recovery = ecg(8)
        mask = gate.process(recovery[:720])
        self.assertFalse(mask.any())
        self.assertIn(gate.state, ('noisy', 'recovering'))
        tail = gate.process(recovery[720:])
        self.assertTrue(tail[-720:].all())
        self.assertEqual(gate.state, 'good')

    def test_quality_decisions_are_independent_of_chunks_and_memory_is_bounded(self):
        values = np.r_[ecg(), 2048+600*np.sin(2*np.pi*50*np.arange(1080)/360), ecg()]
        full = SignalQualityGate(); expected = full.process(values)
        pieces = SignalQualityGate(); actual = []
        cuts = [1, 35, 36, 37, 719, 720, 721, 2900, 3400, 4500, len(values)-1]
        for part in np.split(values, cuts):
            actual.extend(pieces.process(part))
            self.assertLessEqual(len(pieces.values), QUALITY_CONFIG['window_samples'])
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(full.snapshot(), pieces.snapshot())

    def test_no_future_values_affect_prior_quality(self):
        prefix = ecg(3)
        a = SignalQualityGate().process(np.r_[prefix, np.full(720, 2048)])
        b = SignalQualityGate().process(np.r_[prefix, ecg(3)])
        np.testing.assert_array_equal(a[:len(prefix)], b[:len(prefix)])

    def test_reset_and_invalid_values_are_explicit(self):
        gate = SignalQualityGate(); gate.process(ecg())
        gate.reset('invalid')
        status = gate.snapshot()
        self.assertFalse(status['hr_allowed'])
        self.assertEqual(status['signal_state'], 'invalid')
        self.assertIn('协议未给出', status['signal_detail'])
        self.assertEqual(status['quality_metrics'], {})
        for values in ([0], [4095], [65535], [np.nan], [[2000]]):
            with self.assertRaises(ValueError):
                gate.process(values)
        original = np.array([2000., 2001.]); gate.process(original)
        np.testing.assert_array_equal(original, [2000., 2001.])


if __name__ == '__main__':
    unittest.main()
