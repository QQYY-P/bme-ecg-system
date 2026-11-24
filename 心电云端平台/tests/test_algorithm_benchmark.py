"""Scoring contracts; no clinical data or training is needed by these tests."""
import importlib.util
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from research import benchmark_common as common
from research import qrs_comparison as qrs


class BenchmarkInputTests(unittest.TestCase):
    def test_fixed_adc_conversion_is_not_record_normalization(self):
        signal = np.array([-20., -1., 0., 1., 20.])
        before = signal.copy()
        raw = common.to_adc(signal)
        np.testing.assert_array_equal(raw, [0, 1848, 2048, 2248, 4095])
        np.testing.assert_array_equal(signal, before)
        with self.assertRaises(ValueError):
            common.to_adc([0, np.nan])

    def test_selects_mlii_instead_of_silently_using_channel_zero(self):
        header = SimpleNamespace(fs=360, sig_name=['V5', 'MLII'])
        self.assertEqual(common.select_channel(header), 1)
        with self.assertRaisesRegex(ValueError, 'MLII'):
            common.select_channel(SimpleNamespace(fs=360, sig_name=['V5']))
        with self.assertRaisesRegex(ValueError, '360'):
            common.select_channel(SimpleNamespace(fs=250, sig_name=['MLII']))

    def test_cohorts_exclude_pacing_and_same_patient_training_record(self):
        self.assertEqual(len(common.QRS_RECORDS), 44)
        self.assertEqual(len(common.CLASSIFICATION_RECORDS), 21)
        self.assertNotIn('202', common.CLASSIFICATION_RECORDS)
        self.assertNotIn('201', common.CLASSIFICATION_RECORDS)
        self.assertFalse(set(common.CLASSIFICATION_RECORDS) & set(common.TRAIN_RECORDS))
        self.assertFalse(set(common.QRS_RECORDS) & {'102', '104', '107', '217'})

    def test_bad_record_names_duplicates_and_output_inside_data_rejected(self):
        with self.assertRaises(ValueError):
            common.validate_records(['100', '100'], common.QRS_RECORDS)
        with self.assertRaises(ValueError):
            common.validate_records(['../100'], common.QRS_RECORDS)
        with self.assertRaises(ValueError):
            common.validate_paths('data', 'data/report')

    def test_rerun_refuses_to_mix_old_complete_report_with_new_details(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'output'
            common.prepare_output(target)
            old = target/'qrs-comparison.json'
            old.write_text('old result', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                common.prepare_output(target)
            self.assertEqual(old.read_text(encoding='utf-8'), 'old result')


class QrsAdapterTests(unittest.TestCase):
    def test_current_detector_receives_exact_adc_input(self):
        raw = np.array([2048, 2248, 1848], dtype=np.int64)
        result = {'r_samples': [], 'segments': [], 'quality_coverage': 0,
                  'performance': {'wall_seconds': .1}}
        with patch.object(qrs, 'detect_adc', return_value=result) as detect:
            got = qrs.run_detector('current', raw)
        np.testing.assert_array_equal(detect.call_args.args[0], raw)
        self.assertEqual(detect.call_args.kwargs, {'block_size': 36})
        self.assertEqual(got['mode'], 'received-data-stream')

    def test_missing_optional_dependency_is_not_a_silent_algorithm_fallback(self):
        with patch.dict('sys.modules', {'neurokit2': None}):
            with self.assertRaises(ImportError):
                qrs.run_detector('fastnvg', np.ones(3600, dtype=int)*2048)
        with self.assertRaises(ValueError):
            qrs.run_detector('typo', [2048])

    @unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('wfdb', 'neurokit2', 'ts2vg')),
                         'optional research detector dependencies')
    def test_installed_candidate_algorithms_detect_known_synthetic_pulses(self):
        samples = np.arange(16*360)
        raw = np.rint(2048+300*np.exp(-((samples % 360-80)/9)**2)).astype(int)
        for method in ('xqrs', 'fastnvg'):
            result = qrs.run_detector(method, raw)
            peaks = np.array(result['r_samples'])
            self.assertGreaterEqual(len(peaks), 12, msg=method)
            self.assertLess(np.median(np.abs(peaks % 360-80)), 10, msg=method)


@unittest.skipUnless(importlib.util.find_spec('wfdb'), 'optional research dependency: wfdb')
class QrsScoringTests(unittest.TestCase):
    def test_all_reference_beats_including_start_and_tail_are_scored(self):
        result = common.qrs_metrics([5, 360, 720, 1080, 1400], [361, 1082], [0, 0], 54)
        self.assertEqual((result['tp'], result['fp'], result['fn']), (2, 0, 3))
        self.assertEqual(result['matched_adjacent_rr_pairs'], 0)

    def test_matching_is_one_to_one_and_tolerance_is_inclusive(self):
        result = common.qrs_metrics([100, 500], [82, 105, 519], [0, 0, 0], 18)
        self.assertEqual((result['tp'], result['fp'], result['fn']), (1, 2, 1))
        empty = common.qrs_metrics([], [], [], 54)
        self.assertIsNone(empty['f1'])

    def test_exact_tolerance_boundary_is_included_but_next_sample_is_not(self):
        for tolerance in (18, 54):
            for offset in (-tolerance, tolerance):
                result = common.qrs_metrics([100], [100+offset], [0], tolerance)
                self.assertEqual(result['tp'], 1)
            for offset in (-tolerance-1, tolerance+1):
                result = common.qrs_metrics([100], [100+offset], [0], tolerance)
                self.assertEqual(result['tp'], 0)

    def test_aggregation_uses_counts_not_record_percentage_average(self):
        result = common.aggregate_qrs([
            {'tp': 90, 'fp': 10, 'fn': 10}, {'tp': 1, 'fp': 0, 'fn': 9}])
        self.assertAlmostEqual(result['sensitivity'], 91/110)
        self.assertAlmostEqual(result['f1'], 182/211)


if __name__ == '__main__':
    unittest.main()
