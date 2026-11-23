from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from demo import create_demo
from server import create_app
from uploader import Uploader, UploadError
from test_pipeline import block

CAPTURED = '2026-10-26T17:59:50Z'
REGISTERED = '2026-10-29T02:00:00+00:00'
LAST_BLOCK = '2026-10-29T02:00:01+00:00'
CHECKED = '2026-10-29T02:00:00Z'


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.test_runs'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'cloud'
        with patch.dict(os.environ, {'ECG_MODEL': ''}):
            self.client = TestClient(create_app(self.data, token=''))
        self.addCleanup(self.client.close)
        self.meta = {'source': 'serial', 'started_utc': CAPTURED}

    def register(self, sid='legacy', extra=None):
        with patch('server.now', return_value=REGISTERED):
            response = self.client.put(f'/api/sessions/{sid}', json={**self.meta, **(extra or {})})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_acquisition_registration_and_last_block_times_are_separate(self):
        initial = self.register()
        self.assertEqual(initial['started_utc'], CAPTURED)
        self.assertEqual(initial['first_uploaded_at'], REGISTERED)
        self.assertIsNone(initial['last_uploaded_at'])
        self.assertEqual(initial['provenance']['kind'], 'unknown')
        self.assertEqual(initial['provenance']['clock_status'], 'unknown')
        with patch('server.now', return_value=LAST_BLOCK):
            self.assertEqual(self.client.put('/api/sessions/legacy/chunks/0', json=block()).status_code, 200)
        detail = self.client.get('/api/sessions/legacy').json()
        self.assertEqual(detail['first_uploaded_at'], REGISTERED)
        self.assertEqual(detail['last_uploaded_at'], LAST_BLOCK)
        self.assertEqual(detail['received_at'], LAST_BLOCK)
        self.assertEqual(detail['started_utc'], CAPTURED)
        self.assertEqual(self.register()['first_uploaded_at'], REGISTERED)

    def test_clock_observation_requires_consistent_evidence(self):
        valid = {'kind': 'live', 'clock_status': 'ok', 'clock_offset_seconds': -5.0,
                 'clock_checked_at': CHECKED, 'clock_reference': 'computer', 'origin_device': 'pi2'}
        good = self.register('clock', {'provenance': valid})
        self.assertEqual(good['provenance']['clock_offset_seconds'], -5.0)
        invalid = [
            {'clock_status': 'ok'},
            {**valid, 'clock_offset_seconds': 5.1},
            {**valid, 'clock_status': 'offset'},
            {**valid, 'clock_reference': 'unknown'},
            {**valid, 'clock_checked_at': '2026-10-29T02:00:00'},
            {**valid, 'clock_offset_seconds': True},
            {'note': 'x' * 301},
            {**valid, 'made_up_field': 'ignored?'}]
        for payload in invalid:
            response = self.client.put('/api/sessions/clock/provenance', json=payload)
            self.assertEqual(response.status_code, 422, payload)
        response = self.client.put('/api/sessions/clock/provenance', json={
            **valid, 'clock_status': 'offset', 'clock_offset_seconds': -117600.2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['provenance']['clock_offset_seconds'], -117600.2)

    def test_explicit_annotation_survives_old_and_new_uploader_registration(self):
        self.register('annotated', {'provenance': {'kind': 'live', 'note': 'original declaration'}})
        annotation = {'kind': 'historical', 'note': 'Earlier board recording relayed from the saved PC copy'}
        result = self.client.put('/api/sessions/annotated/provenance', json=annotation)
        self.assertEqual(result.status_code, 200)
        self.register('annotated')
        self.register('annotated', {'provenance': {'kind': 'live', 'note': 'later retry'}})
        saved = self.client.get('/api/sessions/annotated').json()
        self.assertEqual(saved['provenance']['kind'], 'historical')
        self.assertEqual(saved['provenance']['note'], annotation['note'])
        self.assertEqual(saved['source'], 'serial')
        self.assertEqual(saved['started_utc'], CAPTURED)
        self.assertEqual(self.client.put('/api/sessions/missing/provenance', json=annotation).status_code, 404)

    def test_uploader_forwards_optional_provenance_without_changing_source(self):
        source = create_demo(Path(self.temp.name)/'source', seconds=2)
        expected = {'kind': 'historical', 'clock_status': 'unknown', 'note': 'local generated fixture'}
        with closing(sqlite3.connect(source/'index.sqlite')) as db, db:
            meta = json.loads(db.execute('SELECT metadata_json FROM sessions').fetchone()[0])
            meta['provenance'] = expected
            db.execute('UPDATE sessions SET metadata_json=?', (json.dumps(meta),))
        before = hashlib.sha256((source/'index.sqlite').read_bytes()).hexdigest()
        uploader = Uploader('http://127.0.0.1:1', Path(self.temp.name)/'state')
        def request(method, path, payload=None):
            response = self.client.request(method, path, json=payload)
            if response.status_code >= 400:
                raise UploadError(str(response.json()))
            return response.json()
        uploader.request = request
        self.assertTrue(uploader.sync(source)['complete'])
        self.assertEqual(uploader.sync(source)['sent_chunks'], 0)
        saved = self.client.get(f'/api/sessions/{source.name}').json()
        self.assertEqual(saved['provenance']['kind'], 'historical')
        self.assertEqual(saved['source'], 'demo')
        self.assertEqual(saved['provenance']['note'], expected['note'])
        self.assertEqual(before, hashlib.sha256((source/'index.sqlite').read_bytes()).hexdigest())

    def test_annotation_and_schema_upgrade_preserve_metadata_raw_and_model_cache(self):
        with patch.dict(os.environ, {'ECG_MODEL': 'cardioedge'}):
            with TestClient(create_app(self.data/'model', token='')) as client:
                client.put('/api/sessions/recorded', json=self.meta)
                payload = block()
                client.put('/api/sessions/recorded/chunks/0', json=payload)
                client.post('/api/sessions/recorded/complete', json={
                    'sample_count': 36, 'source_status': 'complete',
                    'chunks': [{'chunk_id': 0, 'sha256': payload['sha256'], 'sample_count': 36}]})
                analysis = client.post('/api/sessions/recorded/analysis').json()
                self.assertEqual(analysis['analysis_status'], 'ineligible')
                database = self.data/'model/cloud.sqlite'
                raw_path = self.data/'model/sessions/recorded/chunks/00000000.bin'
                raw_before = raw_path.read_bytes()
                with closing(sqlite3.connect(database)) as db:
                    meta_before = db.execute('SELECT metadata FROM sessions').fetchone()[0]
                # Emulate an older cloud DB that predates the independent table.
                with closing(sqlite3.connect(database)) as db, db:
                    db.execute('DROP TABLE session_provenance')
            with TestClient(create_app(self.data/'model', token='')) as upgraded:
                self.assertEqual(upgraded.get('/api/sessions/recorded').json()['provenance']['kind'], 'unknown')
                self.assertEqual(upgraded.get('/api/sessions/recorded/analysis').json(), analysis)
                result = upgraded.put('/api/sessions/recorded/provenance', json={
                    'kind': 'historical', 'note': 'historical upload; original source and timestamps retained'})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(upgraded.get('/api/sessions/recorded/analysis').json(), analysis)
                self.assertEqual(raw_path.read_bytes(), raw_before)
                with closing(sqlite3.connect(database)) as db:
                    self.assertEqual(db.execute('SELECT metadata FROM sessions').fetchone()[0], meta_before)


if __name__ == '__main__':
    unittest.main()
