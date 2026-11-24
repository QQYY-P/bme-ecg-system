"""Admin maintenance uses synthetic records and never grants waveform access."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from fastapi import HTTPException
from server import create_app
from test_pipeline import block, STAMP


class DatabaseManagementTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / '.test_runs'
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / 'data'
        self.app = create_app(self.data, auth_enabled=True, cookie_secure=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.admin = self.app.state.auth.create_user('manager', 'Management-test-812!', True)
        self.user = self.app.state.auth.create_user('reader', 'Reader-test-812!')
        self.device = {'X-API-Key': self.app.state.auth.create_device_key('pi2-demo')}
        self.payload = block()
        for sid, finished in [('saved', True), ('uploading', False)]:
            self.assertEqual(self.client.put('/api/sessions/' + sid, headers=self.device, json={
                'source': 'demo', 'started_utc': STAMP, 'owner_id': self.user['id']}).status_code, 200)
            self.assertEqual(self.client.put('/api/sessions/' + sid + '/chunks/0', headers=self.device, json=self.payload).status_code, 200)
            if finished:
                self.assertEqual(self.client.post('/api/sessions/' + sid + '/complete', headers=self.device, json={
                    'chunks': [{'chunk_id': 0, 'sha256': self.payload['sha256'], 'sample_count': 36}],
                    'sample_count': 36, 'source_status': 'complete'}).status_code, 200)

    def login(self, admin=True):
        result = self.client.post('/api/auth/login', json={
            'username': 'manager' if admin else 'reader',
            'password': 'Management-test-812!' if admin else 'Reader-test-812!'}).json()
        self.client.headers['X-CSRF-Token'] = result['csrf_token']

    def test_permissions_overview_and_no_backup_download(self):
        self.assertEqual(self.client.get('/api/admin/database').status_code, 401)
        self.assertEqual(self.client.get('/api/admin/database', headers=self.device).status_code, 403)
        self.login(False)
        self.assertEqual(self.client.get('/api/admin/database').status_code, 403)
        self.login()
        result = self.client.get('/api/admin/database')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['counts']['sessions'], 2)
        self.assertEqual(result.json()['counts']['chunks'], 2)
        self.assertNotIn('password_hash', result.text)
        self.assertEqual(self.client.get('/api/sessions/saved/waveform').status_code, 404)
        self.assertEqual(self.client.get('/api/admin/database/backups/arbitrary.zip').status_code, 404)
        self.assertEqual(self.client.post('/api/admin/database/jobs', json={'kind': 'scan'}, headers={'X-CSRF-Token': ''}).status_code, 403)

    def test_pagination_filters_trash_restore_and_audit(self):
        self.login()
        result = self.client.get('/api/admin/database/records', params={'owner_id': self.user['id'], 'limit': 1})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((result.json()['total'], len(result.json()['records'])), (2, 1))
        self.assertEqual(self.client.get('/api/admin/database/records', params={'q': 'saved'}).json()['total'], 1)
        self.assertEqual(self.client.get('/api/admin/database/records', params={'state': 'uploading'}).json()['total'], 1)
        self.assertEqual(self.client.post('/api/admin/database/records/uploading/trash').status_code, 409)
        self.assertEqual(self.client.post('/api/admin/database/records/saved/trash').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/database/records', params={'state': 'trashed'}).json()['total'], 1)
        self.assertEqual(self.client.post('/api/admin/database/records/saved/restore').status_code, 200)
        log = self.client.get('/api/admin/database/activity').json()['events']
        self.assertEqual({x['action'] for x in log}, {'trash', 'restore'})
        self.assertTrue(all(x['actor_id'] == self.admin['id'] for x in log))
        self.assertEqual(self.client.get('/api/admin/database/records', params={'offset': -1}).status_code, 422)

    def test_background_check_backup_and_upgrade(self):
        self.login()
        for kind in ('scan', 'check', 'backup'):
            response = self.client.post('/api/admin/database/jobs', json={'kind': kind})
            self.assertEqual(response.status_code, 202, response.text)
            jid = response.json()['job_id']
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                job = next(x for x in self.client.get('/api/admin/database/jobs').json()['jobs'] if x['job_id'] == jid)
                if job['status'] not in ('queued', 'running'): break
                time.sleep(.1)
            self.assertEqual(job['status'], 'complete', job)
        backups = self.client.get('/api/admin/database/backups').json()['backups']
        self.assertEqual(len(backups), 1)
        self.assertTrue((self.data / 'backups' / backups[0]['filename']).is_file())
        upgraded = create_app(self.data, auth_enabled=True, cookie_secure=False)
        with closing(sqlite3.connect(self.data / 'cloud.sqlite')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 2)
        self.assertTrue(upgraded.state.database)

    def test_active_analysis_prevents_admin_trash(self):
        self.login()
        with closing(sqlite3.connect(self.data / 'cloud.sqlite')) as db, db:
            db.execute("INSERT INTO analysis_jobs(job_id,session_id,source_digest,status,stage,total,created,updated) VALUES('pending','saved','hash','queued','waiting',36,0,0)")
        self.assertEqual(self.client.post('/api/admin/database/records/saved/trash').status_code, 409)

    def test_single_task_slot_and_interrupted_process_recovery(self):
        self.login()
        original = self.app.state.database.enqueue(self.admin['id'], 'scan')
        with self.assertRaises(HTTPException) as failed:
            self.app.state.database.enqueue(self.admin['id'], 'backup')
        self.assertEqual(failed.exception.status_code, 409)
        with closing(sqlite3.connect(self.data / 'cloud.sqlite')) as db, db:
            db.execute("UPDATE management_jobs SET status='running' WHERE job_id=?", (original['job_id'],))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            item = self.client.get('/api/admin/database/jobs').json()['jobs'][0]
            if item['status'] == 'interrupted': break
            time.sleep(.1)
        self.assertEqual(item['status'], 'interrupted')
        events = self.client.get('/api/admin/database/activity').json()['events']
        self.assertIn('interrupted', [event['status'] for event in events])

    def test_revalidation_and_legacy_mode_deny_administration(self):
        self.login()
        with closing(sqlite3.connect(self.data / 'cloud.sqlite')) as db, db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.admin['id'],))
        with self.assertRaises(HTTPException): self.app.state.database.enqueue(self.admin['id'], 'check')
        with self.assertRaises(HTTPException): self.app.state.database.change_trash(self.admin['id'], 'saved', True)
        with TestClient(create_app(Path(self.tmp.name) / 'legacy', auth_enabled=False)) as client:
            self.assertEqual(client.get('/api/admin/database').status_code, 403)
            self.assertEqual(client.post('/api/admin/database/jobs', json={'kind': 'backup'}).status_code, 403)

    def test_startup_cleanup_error_does_not_leave_task_queued(self):
        from maintenance_tasks import worker
        self.app.state.database.enqueue(self.admin['id'], 'scan')
        stop = threading.Event()
        def scanned(*args):
            stop.set()
            return {'complete': True, 'bytes': {}, 'issues': []}
        with patch('maintenance_tasks.cleanup_interrupted', side_effect=PermissionError('private path')):
            with patch('maintenance_tasks.scan_storage', side_effect=scanned), patch('maintenance_tasks.os.nice', create=True):
                worker(self.data, stop)
        self.assertEqual(self.app.state.database.jobs()[0]['status'], 'complete')
        failures = [x for x in self.app.state.database.activity() if x['action'] == 'cleanup']
        self.assertEqual(failures[0]['status'], 'failed')
        self.assertNotIn('private path', failures[0]['detail'])


if __name__ == '__main__': unittest.main()
