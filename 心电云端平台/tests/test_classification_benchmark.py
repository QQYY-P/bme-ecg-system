import importlib.util
import unittest
import numpy as np

from research.classification_benchmark import (
    ReferenceDetector, classification_metrics, score_predictions, replay_classifier)


class ReferenceDetectorTests(unittest.TestCase):
    def test_reference_peaks_are_not_delivered_before_received_context(self):
        detector = ReferenceDetector([50, 100, 200, 800])
        first = detector.process(np.arange(50, 200)/360, np.zeros(150))
        np.testing.assert_array_equal(np.rint(first*360), [50])
        second = detector.process(np.arange(200, 360)/360, np.zeros(160))
        np.testing.assert_array_equal(np.rint(second*360), [100, 200])
        detector.reset()
        last = detector.process(np.arange(700, 1000)/360, np.zeros(300))
        np.testing.assert_array_equal(np.rint(last*360), [800])

    def test_duplicate_or_unsorted_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            ReferenceDetector([20, 20])
        with self.assertRaises(ValueError):
            ReferenceDetector([20, 10])

    def test_real_engine_keeps_identical_quality_gates_across_reference_condition(self):
        samples = np.arange(40*360)
        phase = samples % 360
        raw = np.rint(2048+300*np.exp(-((phase-80)/9)**2)).astype(int)
        raw[18*360:19*360] = 4095
        reference = np.arange(80, len(raw), 360)
        normal_rows, _, normal = replay_classifier(raw)
        assisted_rows, _, assisted = replay_classifier(raw, reference_peaks=reference)
        self.assertEqual(normal['quality_allowed_samples'], assisted['quality_allowed_samples'])
        self.assertEqual(normal['quality_evaluated_samples'], assisted['quality_evaluated_samples'])
        self.assertEqual(normal['quality_evaluated_samples'], len(raw)-360)
        self.assertTrue(normal_rows)
        self.assertTrue(assisted_rows)
        for rows in (normal_rows, assisted_rows):
            self.assertFalse(any(18*360 <= r['r_sample'] < 29*360 for r in rows))
            self.assertTrue(all(r['classified_at_sample']-r['r_sample'] >= 360 for r in rows))


class ClassificationMetricTests(unittest.TestCase):
    def test_zero_predictions_have_zero_f1_and_zero_coverage(self):
        got = classification_metrics(np.zeros((3, 3), dtype=int), [10, 2, 3], [0, 0, 0])
        self.assertEqual(got['f1'], [0., 0., 0.])
        self.assertEqual(got['recall'], [0., 0., 0.])
        self.assertEqual(got['precision'], [None, None, None])
        self.assertEqual(got['macro_f1'], 0.)

    def test_no_truth_and_no_predictions_are_undefined_not_perfect(self):
        got = classification_metrics(np.zeros((3, 3), dtype=int), [0, 0, 0], [0, 0, 0])
        self.assertIsNone(got['macro_f1'])
        self.assertEqual(got['f1'], [None, None, None])


@unittest.skipUnless(importlib.util.find_spec('wfdb'), 'optional research dependency: wfdb')
class ClassificationScoringTests(unittest.TestCase):
    def test_out_of_scope_and_unmatched_predictions_lower_precision(self):
        ref = [100, 500, 900, 1300, 1700]
        symbols = ['N', 'A', 'V', 'F', 'N']
        predictions = [dict(r_sample=p, label=l) for p, l in
                       [(100, 'N'), (500, 'V'), (900, 'V'), (1300, 'S'), (1500, 'S')]]
        result = score_predictions(ref, symbols, predictions, tolerance=18)
        self.assertEqual(result['support'], [2, 1, 1])
        self.assertEqual(result['predicted'], [1, 2, 2])
        self.assertEqual(result['confusion'], [[1, 0, 0], [0, 0, 1], [0, 0, 1]])
        self.assertEqual(result['unclassified_target_beats'], [1, 0, 0])
        self.assertEqual(result['out_of_scope_predictions'], [0, 1, 0])
        self.assertEqual(result['unmatched_predictions'], [0, 1, 0])
        self.assertEqual(result['target_coverage'], .75)
        self.assertEqual(result['end_to_end']['recall'], [.5, 0., 1.])
        self.assertEqual(result['end_to_end']['precision'], [1., 0., .5])
        self.assertEqual(result['matched_only']['accuracy'], 2/3)

    def test_start_and_tail_targets_are_in_denominator(self):
        result = score_predictions([1, 100, 1000], ['N', 'A', 'V'], [], tolerance=18)
        self.assertEqual(result['support'], [1, 1, 1])
        self.assertEqual(result['unclassified_target_beats'], [1, 1, 1])
        self.assertEqual(result['target_coverage'], 0.)

    def test_reference_and_label_lengths_must_agree(self):
        with self.assertRaises(ValueError):
            score_predictions([1], [], [], tolerance=18)


if __name__ == '__main__':
    unittest.main()
