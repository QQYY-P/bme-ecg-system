"""Do not combine reports made with different inputs or incomplete runs."""
import copy
import unittest
from pathlib import Path

from research import render_benchmark as renderer
from research.render_benchmark import validate_reports


class ReportValidationTests(unittest.TestCase):
    def reports(self):
        config = {'fs': 360, 'lead': 'MLII', 'adc_gain_counts_per_mv': 200,
                  'adc_offset': 2048, 'adc_quantization': 'rint, clip 0..4095', 'seconds': None, 'records': ['100']}
        record = {'record': '100', 'adc_sha256': 'adc', 'source_files_sha256': {'100.dat': 'source'},
                  'sample_count': 650000, 'full_record': True}
        common = {'configuration': config, 'records': [record], 'aggregate': {'present': True},
                  'environment': {'python': '3.12', 'platform': 'same', 'machine': 'AMD64',
                                  'thread_limits': {'OMP_NUM_THREADS': '1'},
                                  'packages': {'numpy': '2.2.6', 'scipy': '1.15.3', 'wfdb': '4.3.1'}},
                  'code_sha256': {'research/benchmark_common.py': 'same-code'}}
        qrs = dict(copy.deepcopy(common), version='qrs-comparison-v1')
        classifier = dict(copy.deepcopy(common), version='cardioedge-independent-evaluation-v1')
        return qrs, classifier

    def test_reports_with_identical_inputs_can_be_combined(self):
        validate_reports(*self.reports())

    def test_mixed_adc_gain_source_or_code_is_rejected(self):
        for change in ('gain', 'source', 'adc', 'code', 'duration'):
            qrs, classifier = self.reports()
            if change == 'gain':
                classifier['configuration']['adc_gain_counts_per_mv'] = 250
            elif change == 'source':
                classifier['records'][0]['source_files_sha256']['100.dat'] = 'changed'
            elif change == 'adc':
                classifier['records'][0]['adc_sha256'] = 'changed'
            elif change == 'code':
                classifier['code_sha256']['research/benchmark_common.py'] = 'changed'
            else:
                classifier['records'][0]['sample_count'] = 108000
            with self.assertRaises(ValueError, msg=change):
                validate_reports(qrs, classifier)

    def test_incomplete_checkpoint_cannot_be_published_as_finished(self):
        qrs, classifier = self.reports()
        del classifier['aggregate']
        with self.assertRaises(ValueError):
            validate_reports(qrs, classifier)

    def test_mixed_environment_and_truncated_record_list_are_rejected(self):
        for change in ('python', 'platform', 'threads', 'package', 'records'):
            qrs, classifier = self.reports()
            if change in ('python', 'platform'):
                classifier['environment'][change] = 'different'
            elif change == 'threads':
                classifier['environment']['thread_limits']['OMP_NUM_THREADS'] = '8'
            elif change == 'package':
                classifier['environment']['packages']['wfdb'] = 'other'
            else:
                classifier['configuration']['records'].append('103')
            with self.assertRaises(ValueError, msg=change):
                validate_reports(qrs, classifier)

    def test_all_tied_methods_are_reported_and_undefined_is_not_a_winner(self):
        scores = {key: {'qrs_150ms': {'f1': value}} for key, value in
                  [('current', .9), ('xqrs', 1.), ('fastnvg', 1.)]}
        self.assertEqual(renderer.best_detectors(scores), ['xqrs', 'fastnvg'])
        self.assertEqual(renderer.best_detectors({'current': {'qrs_150ms': {'f1': None}}}), [])

    def test_markdown_output_cannot_overwrite_either_input_json(self):
        inputs = [Path('qrs.json'), Path('classification.json')]
        for output in inputs:
            with self.assertRaises(ValueError):
                renderer.validate_report_output(output, inputs)
        renderer.validate_report_output(Path('report.md'), inputs)


if __name__ == '__main__':
    unittest.main()
