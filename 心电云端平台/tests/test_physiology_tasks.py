import base64
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from analysis_tasks import AnalysisQueue
from physiology_analysis import MAX_SAMPLES
from protocol import HEADER, ROWS
from server import create_app
import test_physiology_analysis
from test_pipeline import STAMP


class PhysiologyTaskTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.test_runs'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        with patch.dict(os.environ, {'ECG_MODEL': ''}):
            self.app = create_app(self.data, auth_enabled=True, cookie_secure=False)
        self.addCleanup(lambda: self.app.state.jobs.close())
        self.alice = self.app.state.auth.create_user('alice', 'Alice-task-test-123!')
        self.bob = self.app.state.auth.create_user('bobby', 'Bobby-task-test-123!')
        key = self.app.state.devices.create('pi-task', 'QRS test', [self.alice['id']])['credentials']['token']
        self.device = TestClient(self.app, headers={'X-API-Key': key})
        self.client = TestClient(self.app)
        self.other = TestClient(self.app)
        for client in (self.device, self.client, self.other): self.addCleanup(client.close)
        for client, name, password in ((self.client, 'alice', 'Alice-task-test-123!'),
                                       (self.other, 'bobby', 'Bobby-task-test-123!')):
            login = client.post('/api/auth/login', json={'username': name, 'password': password})
            client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        self.upload('record')

    def upload(self, sid, complete=True):
        reply = self.device.put('/api/sessions/' + sid, json={
            'source': 'serial', 'started_utc': STAMP, 'owner_id': self.alice['id']})
        self.assertEqual(reply.status_code, 200, reply.text)
        if not complete: return
        rows = test_physiology_analysis.PhysiologyAnalysisTests.rows(12)
        raw = HEADER.pack(b'ECGRAW01', 2, ROWS[2].size, 360, len(rows))
        raw += b''.join(ROWS[2].pack(*row) for row in rows)
        digest = hashlib.sha256(raw).hexdigest()
        reply = self.device.put('/api/sessions/' + sid + '/chunks/0', json={
            'payload_base64': base64.b64encode(raw).decode(), 'sha256': digest,
            'sample_count': len(rows), 'first_sample_id': rows[0][0], 'last_sample_id': rows[-1][0],
            'received_first_utc': STAMP, 'received_last_utc': STAMP})
        self.assertEqual(reply.status_code, 200, reply.text)
        reply = self.device.post('/api/sessions/' + sid + '/complete', json={
            'chunks': [{'chunk_id': 0, 'sha256': digest, 'sample_count': len(rows)}],
            'sample_count': len(rows), 'source_status': 'complete'})
        self.assertEqual(reply.status_code, 200, reply.text)

    def enqueue(self, sid='record'):
        reply = self.client.post('/api/sessions/' + sid + '/physiology-jobs')
        self.assertEqual(reply.status_code, 202, reply.text)
        self.assertEqual(reply.json()['kind'], 'physiology')
        return reply.json()

    def wait_for(self, job):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = self.client.get('/api/analysis-jobs/' + job['job_id']).json()
            if result['status'] not in ('queued', 'running'): return result
            time.sleep(.1)
        self.fail('后台任务未按时完成：' + str(result))

    def test_child_process_without_model_persists_summary_and_series(self):
        before = (self.data/'sessions/record/chunks/00000000.bin').read_bytes()
        job = self.wait_for(self.enqueue())
        self.assertEqual(job['status'], 'complete', job)
        self.assertNotEqual(job['worker_pid'], os.getpid())
        summary = self.client.get('/api/sessions/record/physiology').json()
        self.assertEqual(summary['analysis_status'], 'complete')
        self.assertNotIn('r_peaks', summary)
        self.assertNotIn('rr_intervals', summary)
        self.assertGreater(summary['summary']['qrs_count'], 5)
        series = self.client.get('/api/sessions/record/physiology?include_series=true').json()
        self.assertEqual(len(series['r_peaks']), summary['summary']['qrs_count'])
        self.assertIsNone(series['hrv']['metrics'])
        self.assertEqual(self.enqueue()['job_id'], job['job_id'])
        self.assertEqual(self.client.get('/api/sessions/record/analysis').json()['analysis_status'], 'not_configured')
        self.assertEqual((self.data/'sessions/record/chunks/00000000.bin').read_bytes(), before)

    def test_types_are_independent_idempotent_and_recover_after_restart(self):
        with patch.object(self.app.state.jobs, 'start'):
            first = self.enqueue()
            self.assertEqual(self.enqueue()['job_id'], first['job_id'])
            model = self.app.state.jobs.enqueue('record', 'a'*64, 4320)
            self.assertEqual(model['kind'], 'model')
            self.assertNotEqual(model['job_id'], first['job_id'])
            self.assertEqual(self.app.state.jobs.latest('record')['job_id'], model['job_id'])
            self.assertEqual(self.app.state.jobs.latest('record', kind='physiology')['job_id'], first['job_id'])
            self.assertEqual(self.client.post('/api/sessions/record/trash').status_code, 409)
        with closing(sqlite3.connect(self.data/'cloud.sqlite')) as db, db:
            db.execute("UPDATE analysis_jobs SET status='running',worker_pid=999999 WHERE job_id=?", (first['job_id'],))
        restarted = create_app(self.data, auth_enabled=True, cookie_secure=False)
        try:
            with TestClient(restarted):
                self.assertEqual(self.wait_for(first)['status'], 'complete')
        finally: restarted.state.jobs.close()

    def test_ownership_and_revoked_device_cannot_submit_or_read(self):
        for method, suffix in (('get', '/physiology'), ('post', '/physiology-jobs')):
            self.assertEqual(getattr(self.other, method)('/api/sessions/record' + suffix).status_code, 404)
        with patch.object(self.app.state.jobs, 'start'):
            job = self.enqueue()
        self.assertEqual(self.other.get('/api/analysis-jobs/' + job['job_id']).status_code, 404)
        original = self.app.state.jobs.enqueue
        def revoke(*args, **kwargs):
            self.app.state.devices.rotate('pi-task')
            return original(*args, **kwargs)
        with patch.object(self.app.state.jobs, 'enqueue', side_effect=revoke):
            response = self.device.post('/api/sessions/record/physiology-jobs')
        self.assertEqual(response.status_code, 401, response.text)

    def test_incomplete_and_oversized_inputs_are_rejected_before_queueing(self):
        self.upload('incomplete', complete=False)
        self.assertEqual(self.client.post('/api/sessions/incomplete/physiology-jobs').status_code, 409)
        with closing(sqlite3.connect(self.data/'cloud.sqlite')) as db, db:
            db.execute('UPDATE sessions SET sample_count=? WHERE id=?', (MAX_SAMPLES+1, 'record'))
        self.assertEqual(self.client.post('/api/sessions/record/physiology-jobs').status_code, 409)
        self.assertIsNone(self.app.state.jobs.latest('record', kind='physiology'))

    def test_corrupt_chunk_fails_without_publishing_a_result(self):
        path = self.data/'sessions/record/chunks/00000000.bin'
        path.write_bytes(path.read_bytes()[:-1] + b'x')
        job = self.wait_for(self.enqueue())
        self.assertEqual(job['status'], 'failed', job)
        result = self.client.get('/api/sessions/record/physiology').json()
        self.assertEqual(result['analysis_status'], 'failed')
        self.assertNotIn('rr', result)
        self.assertFalse(list((self.data/'physiology').rglob('*.json')))

    def test_old_database_migrates_existing_jobs_to_model(self):
        with closing(sqlite3.connect(self.data/'cloud.sqlite')) as db, db:
            db.execute('DROP TABLE analysis_jobs')
            db.execute('CREATE TABLE analysis_jobs(job_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,source_digest TEXT NOT NULL,'
                       'status TEXT NOT NULL,stage TEXT NOT NULL,processed INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL,'
                       "created REAL NOT NULL,updated REAL NOT NULL,error TEXT NOT NULL DEFAULT '',worker_pid INTEGER)")
            db.execute("CREATE UNIQUE INDEX one_active_analysis ON analysis_jobs(session_id) WHERE status IN ('queued','running')")
            db.execute("INSERT INTO analysis_jobs(job_id,session_id,source_digest,status,stage,total,created,updated) VALUES('old','record','old','queued','waiting',4320,1,1)")
        migrated = AnalysisQueue(self.data, '')
        self.assertEqual(migrated.get('old')['kind'], 'model')
        self.assertNotEqual(migrated.enqueue('record', 'b'*64, 4320, kind='physiology')['job_id'], 'old')
        self.assertEqual(migrated.enqueue('record', 'c'*64, 4320)['job_id'], 'old')


if __name__ == '__main__': unittest.main()
