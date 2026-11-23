import argparse
import copy
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cloud_model.engine import AnalysisService, NAME, VERSION
from cloud_model.vendor.cardioedge.numpy_candidate import Candidate
from protocol import HEADER, ROWS
from verify_live_model import load_source, local_analysis, compare_analysis, verify, ReadOnlyClient


class LiveVerificationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.test_runs'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'pi_live_test'
        (self.source / 'chunks').mkdir(parents=True)
        stamp = '2026-10-29T10:00:00Z'
        self.metadata = {'source': 'serial', 'sample_rate_hz': 360, 'vref_v': 3.3,
                         'started_utc': stamp, 'source_status': 'complete'}
        rows = [(i, 0, i, i % 256, 2100, 0, 1) for i in range(360)]
        raw = HEADER.pack(b'ECGRAW01', 2, ROWS[2].size, 360, len(rows)) + b''.join(ROWS[2].pack(*r) for r in rows)
        (self.source / 'chunks/00000000.bin').write_bytes(raw)
        descriptor = {'chunk_id': 0, 'sha256': hashlib.sha256(raw).hexdigest(), 'sample_count': len(rows),
                      'first_sample_id': 0, 'last_sample_id': len(rows)-1,
                      'received_first_utc': stamp, 'received_last_utc': stamp}
        with closing(sqlite3.connect(self.source / 'index.sqlite')) as db, db:
            db.executescript('CREATE TABLE sessions(session_id,metadata_json,status,sample_count);'
                             'CREATE TABLE chunks(chunk_id,descriptor_json);')
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', ('pi_live_test', json.dumps(self.metadata), 'complete', len(rows)))
            db.execute('INSERT INTO chunks VALUES(?,?)', (0, json.dumps(descriptor)))

    def test_get_only_full_verification_preserves_source_and_rejects_simulation(self):
        source = load_source(self.source)
        service = AnalysisService(self.root / 'local-cache', 'cardioedge')
        digest = service.digest(self.metadata, True, source['manifest'])
        with patch.object(Candidate, 'predict_quantized', side_effect=AssertionError('Simulation reached model')):
            cached = service.run('pi_live_test', self.metadata, True, 360, digest, source['batches'])
            responses = {'/health': {'ok': True, 'service': 'ecg-course-demo', **service.identity()},
                         '/api/sessions/pi_live_test': {'session_id': 'pi_live_test', 'upload_status': 'complete',
                             'sample_count': 360, 'chunks': source['manifest'], **self.metadata},
                         '/api/sessions/pi_live_test/analysis': cached}
            with patch.object(ReadOnlyClient, 'get', side_effect=lambda path: copy.deepcopy(responses[path])) as get:
                args = argparse.Namespace(session_dir=self.source, server='http://127.0.0.1:8767',
                                          evidence_dir=self.root / 'evidence', timeout=3)
                with patch('sys.stdout', new=io.StringIO()):
                    self.assertEqual(verify(args), 0)
                self.assertEqual(get.call_count, 4)
        report = json.loads((self.root / 'evidence/verification.json').read_text(encoding='utf-8'))
        self.assertEqual(report['comparison']['beats_compared'], 0)
        self.assertTrue(report['comparison']['ineligible_null_result_matches'])
        self.assertTrue(report['source_files_unchanged'])
        self.assertEqual({r['method'] for r in report['cloud_requests']}, {'GET'})
        self.assertEqual(load_source(self.source)['hashes'], source['hashes'])

    def test_corrupt_source_is_rejected_before_model(self):
        path = self.source / 'chunks/00000000.bin'
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            load_source(self.source)

    def test_uncomputed_cloud_result_is_not_treated_as_verified(self):
        source = load_source(self.source)
        local = local_analysis(source)
        cloud = {'session_id': 'pi_live_test', 'model_status': 'ready', 'model_name': NAME,
                 'model_version': VERSION, 'analysis_status': 'not_run', 'model_result': None}
        with self.assertRaisesRegex(ValueError, 'never requests it'):
            compare_analysis(cloud, local, 'pi_live_test')

    def test_readonly_transport_has_no_auth_header_or_write_method(self):
        client = ReadOnlyClient('http://127.0.0.1:8767')
        response = io.BytesIO(b'{"ok":true}')
        with patch.dict('os.environ', {'ECG_API_TOKEN': 'must-not-be-used'}):
            with patch.object(client.opener, 'open', return_value=response) as opening:
                self.assertEqual(client.get('/health'), {'ok': True})
        request = opening.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertFalse(any(name.lower() in ('authorization', 'x-api-key') for name in request.headers))
        self.assertFalse(hasattr(client, 'post'))


if __name__ == '__main__':
    unittest.main()
