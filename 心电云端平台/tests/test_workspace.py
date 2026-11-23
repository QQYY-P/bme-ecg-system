import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from server import create_app


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.test_runs'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        with patch.dict(os.environ, {'ECG_MODEL': 'none'}):
            self.client = TestClient(create_app(self.data, token='test'))
        self.client.headers['X-API-Key'] = 'test'
        self.addCleanup(self.client.close)

    def create(self, sid, day=1, source='serial', finish=True):
        self.assertEqual(self.client.put('/api/sessions/' + sid, json={
            'source': source, 'started_utc': f'2026-11-{day:02}T00:00:00Z'}).status_code, 200)
        if finish:
            self.assertEqual(self.client.post('/api/sessions/' + sid + '/complete', json={
                'chunks': [], 'sample_count': 0, 'source_status': 'complete'}).status_code, 200)

    def test_heartbeat_distinguishes_unknown_standby_recording_and_stale(self):
        self.assertEqual(self.client.get('/api/device').json()['connection'], 'unknown')
        reply = self.client.put('/api/device/heartbeat', json={'state': 'standby', 'signal_state': 'unknown'})
        self.assertEqual(reply.status_code, 200)
        status = self.client.get('/api/device').json()
        self.assertEqual((status['connection'], status['state']), ('online', 'standby'))
        self.client.put('/api/device/heartbeat', json={'state': 'acquiring', 'session_id': 'record_1', 'signal_state': 'good'})
        self.assertEqual(self.client.get('/api/device').json()['state'], 'acquiring')
        with patch('workspace_store.time.time', return_value=4102444800):
            status = self.client.get('/api/device').json()
        self.assertEqual(status['connection'], 'offline')
        self.assertEqual(status['signal_state'], 'unknown')

    def test_device_auth_and_input_validation(self):
        self.assertEqual(self.client.put('/api/device/heartbeat', headers={'X-API-Key': ''}, json={'state': 'standby'}).status_code, 401)
        self.assertEqual(self.client.put('/api/device/heartbeat', json={'state': 'healthy'}).status_code, 422)
        self.assertEqual(self.client.put('/api/device/heartbeat', json={'state': 'standby', 'free_bytes': -1}).status_code, 422)

    def test_search_pagination_dates_and_notes_do_not_change_raw_metadata(self):
        for i in range(5): self.create(f'record_{i}', day=i+1, source='demo' if i == 4 else 'serial')
        before = self.client.get('/api/sessions/record_2').json()
        result = self.client.patch('/api/sessions/record_2/details', json={'name': '晨间记录', 'note': '静坐两分钟'})
        self.assertEqual(result.status_code, 200)
        found = self.client.get('/api/sessions', params={'q': '静坐', 'source': 'serial'}).json()
        self.assertEqual(found['total'], 1)
        self.assertEqual(found['sessions'][0]['name'], '晨间记录')
        self.assertEqual(found['sessions'][0]['started_utc'], before['started_utc'])
        page = self.client.get('/api/sessions', params={'limit': 2, 'offset': 2}).json()
        self.assertEqual((page['total'], len(page['sessions'])), (5, 2))
        dates = self.client.get('/api/sessions', params={'started_from': '2026-11-02T00:00:00Z', 'started_before': '2026-11-04T00:00:00Z'}).json()
        self.assertEqual(dates['total'], 2)
        self.assertEqual(self.client.get('/api/sessions', params={'offset': -1}).status_code, 422)

    def test_trash_restores_and_upload_retry_cannot_restore_implicitly(self):
        self.create('saved')
        self.assertEqual(self.client.post('/api/sessions/saved/trash').status_code, 200)
        self.assertEqual(self.client.get('/api/sessions').json()['total'], 0)
        self.assertEqual(self.client.get('/api/sessions', params={'trashed': 'true'}).json()['total'], 1)
        retry = self.client.put('/api/sessions/saved', json={'source': 'serial', 'started_utc': '2026-11-01T00:00:00Z'})
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(self.client.post('/api/sessions/saved/restore').status_code, 200)
        self.assertEqual(self.client.get('/api/sessions').json()['total'], 1)

    def test_recording_cannot_be_trashed_and_metadata_text_is_bounded(self):
        self.create('recording', finish=False)
        self.assertEqual(self.client.post('/api/sessions/recording/trash').status_code, 409)
        self.assertEqual(self.client.patch('/api/sessions/recording/details', json={'name': 'x'*101}).status_code, 422)
        self.assertEqual(self.client.patch('/api/sessions/recording/details', json={'name': '<script>alert(1)</script>', 'note': '仅文本'}).status_code, 200)
        self.assertEqual(self.client.patch('/api/sessions/recording/details', json={'started_utc': '2020-01-01'}).status_code, 422)

    def test_old_database_keeps_records_after_new_app_initialization(self):
        self.create('old_record')
        with TestClient(create_app(self.data, token='test')) as restarted:
            result = restarted.get('/api/sessions', headers={'X-API-Key': 'test'}).json()
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['sessions'][0]['session_id'], 'old_record')


if __name__ == '__main__': unittest.main()
