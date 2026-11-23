import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from protocol import HEADER, ROWS
from server import create_app
from cloud_model.vendor.cardioedge.numpy_candidate import Candidate

STAMP = '2026-10-28T08:00:00Z'


class CloudModelTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / '.test_runs'
        test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        with patch.dict(os.environ, {'ECG_MODEL': 'cardioedge'}):
            self.client = TestClient(create_app(self.data, token=''))
        self.addCleanup(self.client.close)

    def session(self, name, source='serial', mode=1, adc=2100, complete=True):
        self.assertEqual(self.client.put(f'/api/sessions/{name}', json={
            'source': source, 'started_utc': STAMP}).status_code, 200)
        rows = [(i, 0, i, i % 256, adc, int(adc == 65535), mode) for i in range(360)]
        raw = HEADER.pack(b'ECGRAW01', 2, ROWS[2].size, 360, 360) + b''.join(ROWS[2].pack(*r) for r in rows)
        descriptor = {'payload_base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest(),
                      'sample_count': 360, 'first_sample_id': 0, 'last_sample_id': 359,
                      'received_first_utc': STAMP, 'received_last_utc': STAMP}
        self.assertEqual(self.client.put(f'/api/sessions/{name}/chunks/0', json=descriptor).status_code, 200)
        if complete:
            result = self.client.post(f'/api/sessions/{name}/complete', json={
                'sample_count': 360, 'source_status': 'complete',
                'chunks': [{'chunk_id': 0, 'sha256': descriptor['sha256'], 'sample_count': 360}]})
            self.assertEqual(result.status_code, 200)

    def test_simulated_unknown_and_invalid_inputs_never_predict(self):
        self.assertEqual(self.client.get('/health').json()['model_status'], 'ready')
        with patch.object(Candidate, 'predict_quantized', side_effect=AssertionError('Ineligible input reached model')):
            for sid, source, mode, adc in (
                ('board_sim', 'serial', 1, 2100), ('demo_signal', 'demo', 0, 2100),
                ('mode_unknown', 'serial', 255, 2100), ('invalid_adc', 'serial', 0, 65535)):
                self.session(sid, source, mode, adc)
                before = self.client.get(f'/api/sessions/{sid}/analysis').json()
                self.assertEqual(before['analysis_status'], 'not_run')
                result = self.client.post(f'/api/sessions/{sid}/analysis')
                self.assertEqual(result.status_code, 200)
                body = result.json()
                self.assertEqual(body['analysis_status'], 'ineligible')
                self.assertIsNone(body['model_result'])
                self.assertEqual(body['input_summary']['eligible_samples'], 0)
                self.assertEqual(self.client.get(f'/api/sessions/{sid}/analysis').json(), body)

    def test_unfinished_session_is_not_analyzed(self):
        self.session('unfinished', complete=False)
        body = self.client.post('/api/sessions/unfinished/analysis').json()
        self.assertEqual(body['analysis_status'], 'ineligible')
        self.assertIn('尚未上传完成', body['message'])
        self.assertIsNone(body['model_result'])

    def test_corrupt_raw_block_rejected_and_raw_unmodified(self):
        self.session('corrupt')
        path = self.data/'sessions/corrupt/chunks/00000000.bin'
        broken = path.read_bytes()[:-1]
        path.write_bytes(broken)
        response = self.client.post('/api/sessions/corrupt/analysis')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(path.read_bytes(), broken)
        self.assertEqual(self.client.get('/api/sessions/corrupt/analysis').json()['analysis_status'], 'not_run')

    def test_disabled_post_keeps_null(self):
        with patch.dict(os.environ, {'ECG_MODEL': ''}):
            with TestClient(create_app(self.data/'disabled', token='')) as client:
                client.put('/api/sessions/plain', json={'source': 'serial', 'started_utc': STAMP})
                result = client.post('/api/sessions/plain/analysis').json()
                self.assertEqual(result['model_status'], 'not_configured')
                self.assertIsNone(result['model_result'])


if __name__ == '__main__':
    unittest.main()
