import unittest

from check_repository import check_file, path_problem


class RepositoryPolicyTests(unittest.TestCase):
    def test_private_records_and_builds_are_rejected(self):
        for name in ('app/data/raw.csv', 'app/.cloud_private/config.json', 'app/.env',
                     'app/.env.production', 'APP/SECRET.JKS', 'cloud.sqlite-wal',
                     'app/dist/release.apk', 'device-credentials.json', 'app/node_modules/a.js'):
            with self.subTest(name=name):
                self.assertIsNotNone(path_problem(name))

    def test_sources_models_and_public_reports_are_allowed(self):
        for name in ('app/.env.example', 'app/device_store.py', 'docs/report.json',
                     'cardioedge/portable_weights.npz', 'tests/test_credentials.py'):
            with self.subTest(name=name):
                self.assertIsNone(path_problem(name))

    def test_private_key_block_is_rejected_without_echoing_secret(self):
        body = ('-----BEGIN ' + 'RSA PRIVATE KEY-----\nsecret\n').encode()
        self.assertEqual(check_file('config.txt', body), ['embedded private key'])

    def test_python_syntax_failure_is_reported(self):
        self.assertTrue(check_file('broken.py', b'if True\n    pass\n'))
        self.assertEqual(check_file('valid.py', b'# coding: utf-8\nvalue = 1\n'), [])

    def test_windows_separators_are_checked(self):
        self.assertIsNotNone(path_problem(r'app\data\record.csv'))


if __name__ == '__main__':
    unittest.main()
