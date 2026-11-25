"""Regression tests for tools/local_verification.py (parsing and summary only)."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import local_verification as lv


class UnittestSummaryTests(unittest.TestCase):
    def test_ok_with_skips(self):
        text = 'Ran 12 tests in 0.5s\n\nOK (skipped=2)\n'
        self.assertEqual(lv.parse_unittest_summary(text),
                         {'ran': 12, 'failed': 0, 'errors': 0, 'skipped': 2, 'ok': True})

    def test_failed_with_all_counters(self):
        text = 'Ran 374 tests in 71.8s\n\nFAILED (errors=9, skipped=3)\n'
        self.assertEqual(lv.parse_unittest_summary(text),
                         {'ran': 374, 'failed': 0, 'errors': 9, 'skipped': 3, 'ok': False})

    def test_failures_and_errors(self):
        text = 'Ran 20 tests in 1s\n\nFAILED (failures=2, errors=1)\n'
        counts = lv.parse_unittest_summary(text)
        self.assertEqual((counts['ran'], counts['failed'], counts['errors']), (20, 2, 1))

    def test_missing_summary_is_zeroed(self):
        self.assertEqual(lv.parse_unittest_summary('no summary here'),
                         {'ran': 0, 'failed': 0, 'errors': 0, 'skipped': 0, 'ok': False})


class NodeOutputTests(unittest.TestCase):
    def test_counts_pass_and_fail_lines(self):
        text = '✔ first (1ms)\n✔ second\n  ✖ third\n# pass 2\n'
        self.assertEqual(lv.parse_node_output(text), {'passed': 2, 'failed': 1})

    def test_empty(self):
        self.assertEqual(lv.parse_node_output(''), {'passed': 0, 'failed': 0})


class BlockedReasonTests(unittest.TestCase):
    def test_named_pipe_is_environment_blocker(self):
        text = 'PermissionError: [WinError 5]\n  self._reader, self._writer = connection.Pipe(duplex=False)'
        self.assertEqual(lv.blocked_reason(text), 'named pipe')

    def test_process_spawn_is_environment_blocker(self):
        self.assertEqual(lv.blocked_reason('Error: spawn EPERM'), 'process spawn')

    def test_assertion_failure_is_not_blocked(self):
        self.assertEqual(lv.blocked_reason('AssertionError: 1 != 0'), '')


class SummaryTests(unittest.TestCase):
    def test_unexpected_excludes_blocked_suite(self):
        records = [
            {'suite': 'cloud', 'ran': 10, 'passed': 10, 'failed': 0, 'errors': 0,
             'skipped': 0, 'blocked': ''},
            {'suite': 'pi', 'ran': 9, 'passed': 0, 'failed': 0, 'errors': 9,
             'skipped': 0, 'blocked': 'named pipe'},
        ]
        totals = lv.summarize(records)
        self.assertEqual(totals['ran'], 19)
        self.assertEqual(totals['unexpected'], 0)
        self.assertEqual(totals['blocked'], ['pi'])

    def test_unexpected_counts_real_failures(self):
        totals = lv.summarize([
            {'suite': 'cloud', 'ran': 5, 'passed': 3, 'failed': 1, 'errors': 1,
             'skipped': 0, 'blocked': ''},
        ])
        self.assertEqual(totals['unexpected'], 2)

    def test_report_shape(self):
        report = lv.build_report([], False, 'v22')
        self.assertEqual(report['kind'], 'local-verification-v1')
        self.assertIn('environment', report)
        self.assertIn('totals', report)
        self.assertIn('do not prove', report['note'])


if __name__ == '__main__':
    unittest.main()
