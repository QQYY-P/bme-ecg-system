import base64
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from server import create_app
from test_pipeline import block, STAMP


class MultiDeviceTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'work'/'multi-device'/'tmp'
        root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup)
        self.app=create_app(self.temp.name,auth_enabled=True,cookie_secure=False)
        self.admin=self.app.state.auth.create_user('admin','Admin-only-test-123!',True)
        self.alice=self.app.state.auth.create_user('alice','Alice-only-test-123!')
        self.bob=self.app.state.auth.create_user('bobby','Bobby-only-test-123!')
        self.client=self.client_for('admin','Admin-only-test-123!')

    def client_for(self,name,password):
        client=TestClient(self.app);self.addCleanup(client.close)
        result=client.post('/api/auth/login',json=dict(username=name,password=password))
        self.assertEqual(result.status_code,200,result.text)
        client.headers['X-CSRF-Token']=result.json()['csrf_token']
        return client

    def device(self,did,users):
        r=self.client.post('/api/admin/devices',json=dict(device_id=did,name='设备 '+did,user_ids=users))
        self.assertEqual(r.status_code,200,r.text)
        c=TestClient(self.app);self.addCleanup(c.close)
        c.headers['X-API-Key']=r.json()['credentials']['token']
        return c

    def register(self,c,sid,uid):
        return c.put('/api/sessions/'+sid,json=dict(source='serial',started_utc=STAMP,owner_id=uid))

    def test_rotation_while_request_body_arrives_rejects_the_old_key(self):
        a=self.device('pi-slow',[self.alice['id']])
        def body():
            yield b'{"state":'
            self.app.state.devices.rotate('pi-slow')
            yield b'"standby"}'
        response=a.put('/api/device/heartbeat',content=body(),headers={'Content-Type':'application/json'})
        self.assertEqual(response.status_code,401,response.text)

    def test_disabled_during_chunk_decode_cannot_write_after_revocation(self):
        import server
        a=self.device('pi-revoke',[self.alice['id']]);self.register(a,'revoke_chunk',self.alice['id'])
        original=server.decode_chunk
        def decode(*args):
            rows=original(*args);self.app.state.devices.update('pi-revoke',active=False);return rows
        with patch('server.decode_chunk',side_effect=decode):
            response=a.put('/api/sessions/revoke_chunk/chunks/0',json=block())
        self.assertEqual(response.status_code,401,response.text)
        alice=self.client_for('alice','Alice-only-test-123!')
        self.assertEqual(alice.get('/api/sessions/revoke_chunk').json()['sample_count'],0)

    def test_analysis_cannot_be_accepted_after_rotation_during_request_body(self):
        with patch.dict(os.environ,{'ECG_MODEL':'cardioedge'}):
            self.app=create_app(self.temp.name,auth_enabled=True,cookie_secure=False)
        for index,suffix in enumerate(('analysis-jobs','analysis')):
            did='pi-analysis-'+str(index);sid='analysis_'+str(index)
            a=self.device(did,[self.alice['id']]);self.register(a,sid,self.alice['id'])
            a.post('/api/sessions/'+sid+'/complete',json=dict(chunks=[],sample_count=0,source_status='complete'))
            def body():
                yield b' ';self.app.state.devices.rotate(did);yield b' '
            with patch.object(self.app.state.jobs,'start'),patch('server.AnalysisService.run') as run:
                response=a.post('/api/sessions/'+sid+'/'+suffix,content=body())
                self.assertEqual(response.status_code,401,response.text);run.assert_not_called()
            self.assertIsNone(self.app.state.jobs.latest(sid))

    def test_analysis_enqueue_rechecks_after_initial_snapshot(self):
        with patch.dict(os.environ,{'ECG_MODEL':'cardioedge'}):
            self.app=create_app(self.temp.name,auth_enabled=True,cookie_secure=False)
        a=self.device('pi-queue',[self.alice['id']]);self.register(a,'queue_gap',self.alice['id'])
        a.post('/api/sessions/queue_gap/complete',json=dict(chunks=[],sample_count=0,source_status='complete'))
        original=self.app.state.jobs.enqueue
        def enqueue(*args,**kwargs):
            self.app.state.devices.rotate('pi-queue');return original(*args,**kwargs)
        with patch.object(self.app.state.jobs,'enqueue',side_effect=enqueue),patch.object(self.app.state.jobs,'start'):
            response=a.post('/api/sessions/queue_gap/analysis-jobs')
        self.assertEqual(response.status_code,401,response.text)
        self.assertIsNone(self.app.state.jobs.latest('queue_gap'))

    def test_two_devices_have_independent_credentials_recipients_and_heartbeats(self):
        a=self.device('pi-a',[self.alice['id']]);b=self.device('pi-b',[self.bob['id']])
        self.assertEqual([u['id'] for u in a.get('/api/device/recipients').json()['users']],[self.alice['id']])
        self.assertEqual([u['id'] for u in b.get('/api/device/recipients').json()['users']],[self.bob['id']])
        for c,uid,state in [(a,self.alice['id'],'acquiring'),(b,self.bob['id'],'saving')]:
            self.assertEqual(c.put('/api/device/heartbeat',json=dict(state=state,owner_id=uid,received=36)).status_code,200)
        self.assertEqual(a.get('/api/device').json()['state'],'acquiring')
        self.assertEqual(b.get('/api/device').json()['state'],'saving')
        self.assertEqual(a.get('/api/device').json()['device_id'],'pi-a')
        devices=self.client.get('/api/devices').json()['devices']
        self.assertIn('pi-b',[d['device_id'] for d in devices])

    def test_authorization_and_cross_device_same_id_never_overwrite(self):
        a=self.device('pi-a',[self.alice['id']]);b=self.device('pi-b',[self.bob['id']])
        self.assertEqual(self.register(a,'a_record',self.bob['id']).status_code,403)
        self.assertEqual(self.register(a,'a_record',self.alice['id']).status_code,200)
        self.assertIn(self.register(b,'a_record',self.bob['id']).status_code,(404,409))
        for path in ['', '/waveform','/export','/analysis']:
            self.assertEqual(b.get('/api/sessions/a_record'+path).status_code,404,path)
        self.assertEqual(b.put('/api/sessions/a_record/chunks/0',json=block()).status_code,404)
        self.assertEqual(b.post('/api/sessions/a_record/analysis-jobs').status_code,404)
        self.assertEqual(a.put('/api/device/heartbeat',json=dict(state='acquiring',owner_id=self.bob['id'])).status_code,403)
        self.assertEqual(a.put('/api/device/heartbeat',json=dict(state='standby',device_id='pi-b')).status_code,422)

    def test_user_can_view_own_records_from_two_devices_but_not_other_user(self):
        a=self.device('pi-a',[self.alice['id']]);b=self.device('pi-b',[self.alice['id'],self.bob['id']])
        self.register(a,'alice_a',self.alice['id']);self.register(b,'alice_b',self.alice['id']);self.register(b,'bob_b',self.bob['id'])
        alice=self.client_for('alice','Alice-only-test-123!')
        result=alice.get('/api/sessions').json()
        self.assertEqual(result['total'],2)
        self.assertEqual({r['device_id'] for r in result['sessions']},{'pi-a','pi-b'})
        filtered=alice.get('/api/sessions?device_id=pi-b').json()
        self.assertEqual([r['session_id'] for r in filtered['sessions']],['alice_b'])
        self.assertEqual(alice.get('/api/sessions/bob_b').status_code,404)
        self.assertEqual(alice.get('/api/admin/devices').status_code,403)
        self.assertEqual(a.get('/api/devices').status_code,403)

    def test_rotated_or_disabled_device_stops_without_deleting_records(self):
        a=self.device('pi-a',[self.alice['id']]);self.register(a,'kept',self.alice['id'])
        fresh=self.client.post('/api/admin/devices/pi-a/credentials')
        self.assertEqual(fresh.status_code,200,fresh.text)
        self.assertEqual(a.get('/api/device/recipients').status_code,401)
        a.headers['X-API-Key']=fresh.json()['credentials']['token']
        self.assertEqual(a.get('/api/device/recipients').status_code,200)
        self.assertEqual(self.client.patch('/api/admin/devices/pi-a',json=dict(active=False)).status_code,200)
        self.assertEqual(a.put('/api/device/heartbeat',json=dict(state='standby')).status_code,401)
        alice=self.client_for('alice','Alice-only-test-123!')
        self.assertEqual(alice.get('/api/sessions/kept').status_code,200)
        self.assertEqual(self.client.patch('/api/admin/devices/pi-a',json=dict(active=True)).status_code,200)
        self.assertEqual(a.get('/api/device/recipients').status_code,200)

    def test_shared_device_does_not_disclose_another_users_current_record(self):
        a=self.device('pi-a',[self.alice['id'],self.bob['id']])
        self.register(a,'private_alice',self.alice['id'])
        a.put('/api/device/heartbeat',json=dict(state='acquiring',owner_id=self.alice['id'],session_id='private_alice',signal_text='private detail',received=123))
        bob=self.client_for('bobby','Bobby-only-test-123!')
        items=bob.get('/api/devices').json()['devices']
        d=next(d for d in items if d['device_id']=='pi-a')
        self.assertIsNone(d['session_id']);self.assertIsNone(d['owner_id'])
        self.assertEqual(d['received'],0);self.assertNotIn('private',json.dumps(d))

    def test_new_device_defaults_to_no_users_and_rejects_unknown_assignment(self):
        a=self.device('pi-a',[])
        self.assertEqual(a.get('/api/device/recipients').json()['users'],[])
        self.assertEqual(self.register(a,'blocked',self.alice['id']).status_code,403)
        r=self.client.patch('/api/admin/devices/pi-a',json=dict(name='must roll back',user_ids=['f'*32]))
        self.assertEqual(r.status_code,422)
        rows=self.client.get('/api/admin/devices').json()['devices']
        self.assertEqual(next(x for x in rows if x['device_id']=='pi-a')['name'],'设备 pi-a')

    def test_interleaved_upload_resume_restart_and_hashes(self):
        a=self.device('pi-a',[self.alice['id']]);b=self.device('pi-b',[self.bob['id']])
        for c,sid,uid in [(a,'upload_a',self.alice['id']),(b,'upload_b',self.bob['id'])]:
            self.assertEqual(self.register(c,sid,uid).status_code,200)
        chunks=[block(adc=2048),block(start=36,adc=2200)]
        def send(args):
            c,sid,i=args
            return c.put('/api/sessions/'+sid+'/chunks/'+str(i),json=chunks[i])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(send,[(a,'upload_a',0),(b,'upload_b',0),(a,'upload_a',1),(b,'upload_b',1)]))
        self.assertTrue(all(r.status_code==200 for r in results))
        self.assertTrue(send((a,'upload_a',0)).json()['duplicate'])
        manifest=[dict(chunk_id=i,sha256=p['sha256'],sample_count=36) for i,p in enumerate(chunks)]
        for c,sid in [(a,'upload_a'),(b,'upload_b')]:
            self.assertEqual(c.post('/api/sessions/'+sid+'/complete',json=dict(chunks=manifest,sample_count=72,source_status='complete')).status_code,200)
            for i,p in enumerate(chunks):
                raw=Path(self.temp.name)/'sessions'/sid/'chunks'/f'{i:08d}.bin'
                self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),p['sha256'])
        with TestClient(create_app(self.temp.name,auth_enabled=True,cookie_secure=False)) as restarted:
            restarted.headers['X-API-Key']=a.headers['X-API-Key']
            self.assertEqual(restarted.get('/api/sessions/upload_a').json()['sample_count'],72)
            self.assertEqual(restarted.get('/api/sessions/upload_b').status_code,404)

    def test_old_database_migrates_heartbeat_and_existing_device_key(self):
        key=self.app.state.auth.create_device_key('pi2-demo')
        with closing(sqlite3.connect(Path(self.temp.name)/'cloud.sqlite')) as db,db:
            db.execute('INSERT OR REPLACE INTO device_heartbeat VALUES(1,?,?)',(2000000000,json.dumps(dict(state='standby'))))
            db.execute('DROP TABLE device_heartbeats');db.execute('DROP TABLE device_users');db.execute('DROP TABLE devices')
        with TestClient(create_app(self.temp.name,auth_enabled=True,cookie_secure=False)) as upgraded:
            upgraded.headers['X-API-Key']=key
            self.assertEqual(upgraded.get('/api/device').json()['state'],'standby')
            self.assertIn(self.alice['id'],[u['id'] for u in upgraded.get('/api/device/recipients').json()['users']])

    def test_other_users_task_cannot_be_read_even_with_valid_job_id(self):
        a=self.device('pi-a',[self.alice['id']]);self.register(a,'queued_alice',self.alice['id'])
        a.post('/api/sessions/queued_alice/complete',json=dict(chunks=[],sample_count=0,source_status='complete'))
        job=self.app.state.jobs.enqueue('queued_alice','isolated-access-test',0)['job_id']
        bob=self.client_for('bobby','Bobby-only-test-123!')
        self.assertEqual(bob.get('/api/analysis-jobs/'+job).status_code,404)


if __name__=='__main__':unittest.main()
