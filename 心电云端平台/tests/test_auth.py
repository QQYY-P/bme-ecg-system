import os
from contextlib import closing
import sqlite3
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from server import create_app


class AccountIsolationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.test_runs'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name, token='old-global-token', auth_enabled=True, cookie_secure=False)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.admin = self.app.state.auth.create_user('admin', 'Admin-test-9182!', admin=True)
        self.alice = self.app.state.auth.create_user('alice', 'Alice-test-9182!')
        self.bob = self.app.state.auth.create_user('bob', 'Bobby-test-9182!')
        self.device_key = self.app.state.auth.create_device_key('pi2-demo')
        self.headers = {'X-API-Key': self.device_key}
        for name, owner in [('alice_record', self.alice['id']), ('bob_record', self.bob['id']), ('legacy_record', None)]:
            reply = self.client.put('/api/sessions/' + name, headers=self.headers, json={
                'source': 'serial', 'started_utc': '2026-11-23T08:00:00Z', 'owner_id': owner})
            self.assertEqual(reply.status_code, 200, reply.text)
            self.assertEqual(self.client.post('/api/sessions/' + name + '/complete', headers=self.headers, json={
                'chunks': [], 'sample_count': 0, 'source_status': 'complete'}).status_code, 200)

    def login(self, name='alice', password='Alice-test-9182!'):
        reply = self.client.post('/api/auth/login', json={'username': name, 'password': password})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.client.headers['X-CSRF-Token'] = reply.json()['csrf_token']
        return reply

    def test_login_cookie_logout_and_old_token_cannot_bypass(self):
        self.assertEqual(self.client.get('/api/sessions').status_code, 401)
        self.assertEqual(self.client.get('/api/sessions', headers={'X-API-Key': 'old-global-token'}).status_code, 401)
        reply = self.login()
        self.assertIn('HttpOnly', reply.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/sessions').json()['total'], 1)
        self.assertEqual(self.client.get('/api/sessions').headers['X-ECG-User'],self.alice['id'])
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
        self.assertEqual(self.client.get('/api/sessions').status_code, 401)

    def test_legacy_device_identity_migrates_without_assigning_personal_owner(self):
        with closing(sqlite3.connect(Path(self.temp.name)/'cloud.sqlite')) as db, db:
            db.execute("UPDATE sessions SET ingest_device_id=NULL WHERE id='legacy_record'")
        upgraded=create_app(self.temp.name,auth_enabled=True,cookie_secure=False)
        with TestClient(upgraded) as client:
            replay=client.put('/api/sessions/legacy_record',headers=self.headers,json={
                'source':'serial','started_utc':'2026-11-23T08:00:00Z'})
            self.assertEqual(replay.status_code,200,replay.text)
            self.assertIsNone(replay.json()['owner_id'])
            client.post('/api/auth/login',json={'username':'alice','password':'Alice-test-9182!'})
            self.assertEqual(client.get('/api/sessions/legacy_record').status_code,404)

    def test_shared_standby_is_visible_without_disclosing_another_users_signal(self):
        self.client.put('/api/device/heartbeat',headers=self.headers,json={'state':'standby'})
        self.login()
        status=self.client.get('/api/device').json()
        self.assertEqual(status['state'],'standby')
        self.assertEqual(status['signal_state'],'unknown')
        self.assertIsNone(status['owner_id'])

    def test_foreign_record_ids_are_hidden_for_every_read_and_write(self):
        self.login()
        for suffix in ('', '/waveform', '/export', '/analysis'):
            self.assertEqual(self.client.get('/api/sessions/bob_record' + suffix).status_code, 404, suffix)
        for suffix in ('/analysis', '/analysis-jobs', '/trash', '/restore'):
            self.assertEqual(self.client.post('/api/sessions/bob_record' + suffix).status_code, 404, suffix)
        self.assertEqual(self.client.patch('/api/sessions/bob_record/details', json={'name': 'stolen'}).status_code, 404)
        self.assertEqual(self.client.get('/api/sessions/legacy_record').status_code, 404)
        self.assertEqual(self.client.get('/api/admin/users').status_code, 403)

    def test_cookie_mutation_requires_csrf_and_cannot_forge_owner(self):
        self.login()
        self.assertEqual(self.client.post('/api/sessions/alice_record/trash', headers={'X-CSRF-Token': ''}).status_code, 403)
        self.assertEqual(self.client.patch('/api/sessions/alice_record/details', json={'owner_id': self.bob['id']}).status_code, 422)
        self.assertEqual(self.client.put('/api/sessions/forged', json={'source': 'demo', 'started_utc': '2026-11-23T08:00:00Z', 'owner_id': self.bob['id']}).status_code, 403)

    def test_admin_must_explicitly_assign_unowned_record(self):
        self.login('admin', 'Admin-test-9182!')
        unassigned = self.client.get('/api/admin/unassigned-sessions').json()['sessions']
        self.assertEqual([s['session_id'] for s in unassigned], ['legacy_record'])
        response = self.client.post('/api/admin/sessions/legacy_record/assign', json={'owner_id': self.alice['id']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/admin/sessions/legacy_record/assign', json={'owner_id': self.bob['id']}).status_code, 409)
        self.client.post('/api/auth/logout')
        self.login()
        self.assertEqual(self.client.get('/api/sessions').json()['total'], 2)

    def test_heartbeat_does_not_disclose_other_users_current_signal(self):
        self.client.put('/api/device/heartbeat', headers=self.headers, json={
            'state': 'acquiring', 'owner_id': self.bob['id'], 'session_id': 'bob_record', 'signal_state': 'good'})
        self.login()
        status = self.client.get('/api/device').json()
        self.assertIsNone(status['session_id'])
        self.assertEqual(status['signal_state'], 'unknown')

    def test_password_not_stored_in_plaintext_and_failure_is_rate_limited(self):
        database = Path(self.temp.name) / 'cloud.sqlite'
        self.assertNotIn(b'Alice-test-9182!', database.read_bytes())
        codes = [self.client.post('/api/auth/login', json={'username': 'alice', 'password': 'incorrect'}).status_code for _ in range(7)]
        self.assertEqual(codes[0], 401)
        self.assertIn(429, codes)


if __name__ == '__main__': unittest.main()
