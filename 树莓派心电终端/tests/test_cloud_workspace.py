from contextlib import closing
import json
import sqlite3
import threading
import time
import unittest
from unittest import mock

import test_cloud
from test_cloud import MemoryCloud
from cloud.service import CloudService
from cloud.uploader import UploadError


class ModernCloud(MemoryCloud):
    reports = []
    registrations = []
    jobs = 0
    def request(self, method, path, payload=None):
        if path == '/health':
            return dict(ok=True,service='ecg-course-demo',deployment='cloud',model_status='ready',workspace_version=1,auth_required=True)
        if path == '/api/device/recipients': return {'users':[{'id':'a'*32,'username':'alice'}], 'auth_required':True}
        if path == '/api/device/heartbeat':
            type(self).reports.append(dict(payload));return {'accepted':True}
        if path.endswith('/analysis-jobs'):
            type(self).jobs += 1;return {'job_id':'b'*32,'status':'queued'}
        if path.endswith('/analysis') and not type(self).jobs:
            return {'session_id':path.split('/')[3], 'analysis_status':'not_run'}
        if method=='PUT' and '/chunks/' not in path: type(self).registrations.append(dict(payload))
        return super().request(method,path,payload)


class WorkspaceClientTests(unittest.TestCase):
    wait_for = test_cloud.CloudTests.wait_for
    def setUp(self):
        test_cloud.CloudTests.setUp(self);ModernCloud.reports=[];ModernCloud.registrations=[];ModernCloud.jobs=0
        ModernCloud.remote={};ModernCloud.calls=[];ModernCloud.hold=None

    def test_public_health_without_device_permission_is_not_online(self):
        class UnauthorizedCloud(ModernCloud):
            def request(self, method, path, payload=None):
                if path.startswith('/api/'):
                    raise UploadError('请先登录', status_code=401)
                return super().request(method,path,payload)
        self.service=CloudService(self.root,client_factory=UnauthorizedCloud,heartbeat_interval=.05)
        self.wait_for('heartbeat_status','offline')
        time.sleep(.1)
        self.assertEqual(self.service.snapshot()['connection'],'unauthorized')
        self.assertIn('凭据',self.service.snapshot()['connection_detail'])

    def test_credential_refresh_recreates_client_and_resumes_pending_upload(self):
        class RotatingCloud(ModernCloud):
            authorized=False
            def __init__(self,*a,**kw):super().__init__(*a,**kw);self.authorized_at_creation=type(self).authorized
            def request(self,method,path,payload=None):
                if path.startswith('/api/') and not self.authorized_at_creation:
                    raise UploadError('invalid token',status_code=401)
                return super().request(method,path,payload)
        self.service=CloudService(self.root,client_factory=RotatingCloud,heartbeat_interval=.05)
        self.service.upload('test-cloud')
        self.wait_for('connection','unauthorized')
        RotatingCloud.authorized=True;self.service.reconnect()
        self.wait_for('phase','complete')
        self.assertIsNone(self.service.job)

    def test_legacy_health_must_validate_protected_endpoint(self):
        class UnauthorizedLegacy(MemoryCloud):
            def request(self,method,path,payload=None):
                if path.startswith('/api/'):raise UploadError('bad shared token',status_code=403)
                return super().request(method,path,payload)
        self.service=CloudService(self.root,client_factory=UnauthorizedLegacy,heartbeat_interval=.05)
        self.wait_for('connection','unauthorized')

    def test_heartbeat_continues_when_upload_blocks_and_wait_stops_both(self):
        ModernCloud.hold=threading.Event()
        self.service=CloudService(self.root,client_factory=ModernCloud,heartbeat_interval=.05)
        self.service.update_device_status({'running':True,'state':'RECORDING','session_id':'test-cloud','cloud_owner_id':'a'*32,'signal_text':'信号稳定','received':360,'saved':360})
        self.service.upload('test-cloud')
        self.wait_for('phase','uploading');time.sleep(.3)
        self.assertGreaterEqual(len(ModernCloud.reports),2)
        self.assertEqual(ModernCloud.reports[-1]['state'],'acquiring')
        self.assertEqual(ModernCloud.reports[-1]['owner_id'],'a'*32)
        self.service.close();ModernCloud.hold.set()
        self.assertTrue(self.service.wait(5))
        self.assertFalse(self.service.heartbeat_thread.is_alive())

    def test_capture_owner_is_uploaded_and_background_job_used(self):
        with closing(sqlite3.connect(self.directory/'index.sqlite')) as db, db:
            meta=json.loads(db.execute('SELECT metadata_json FROM sessions').fetchone()[0]);meta['cloud_owner_id']='a'*32
            db.execute('UPDATE sessions SET metadata_json=?',(json.dumps(meta),))
        self.service=CloudService(self.root,client_factory=ModernCloud,heartbeat_interval=.05)
        self.service.upload('test-cloud');self.wait_for('phase','complete')
        self.assertEqual(ModernCloud.registrations[0]['owner_id'],'a'*32)
        self.assertEqual(ModernCloud.jobs,1)
        self.assertFalse(any(method=='POST' and path.endswith('/analysis') for method,path in ModernCloud.calls))

    def test_bound_record_refuses_legacy_unsegregated_cloud(self):
        with closing(sqlite3.connect(self.directory/'index.sqlite')) as db, db:
            meta=json.loads(db.execute('SELECT metadata_json FROM sessions').fetchone()[0]);meta['cloud_owner_id']='a'*32
            db.execute('UPDATE sessions SET metadata_json=?',(json.dumps(meta),))
        client=MemoryCloud('http://127.0.0.1:8766',self.root/'receipts')
        with self.assertRaisesRegex(UploadError,'账号隔离'): client.sync(self.directory)
        self.assertFalse(any(method=='PUT' for method,path in MemoryCloud.calls))

    def test_legacy_fallback_only_on_missing_job_endpoint(self):
        from cloud.service import submit_analysis
        client=mock.Mock()
        client.request.side_effect=[UploadError('not found',status_code=404),{'analysis_status':'ineligible'}]
        self.assertEqual(submit_analysis(client,'sid')['analysis_status'],'ineligible')
        self.assertEqual(client.request.call_args.args,('POST','/api/sessions/sid/analysis'))
        client.reset_mock();client.request.side_effect=UploadError('forbidden',status_code=403)
        with self.assertRaises(UploadError):submit_analysis(client,'sid')
        self.assertEqual(client.request.call_count,1)

    def test_failed_background_job_finishes_instead_of_retrying_forever(self):
        class BrokenModelCloud(ModernCloud):
            jobs = 0
            def request(self, method, path, payload=None):
                if path.endswith('/analysis') and type(self).jobs:
                    return {'session_id':path.split('/')[3], 'analysis_status':'failed','message':'模型计算失败'}
                return super().request(method,path,payload)
        self.service=CloudService(self.root,client_factory=BrokenModelCloud,heartbeat_interval=.05)
        self.service.upload('test-cloud');self.wait_for('phase','failed')
        self.assertEqual(BrokenModelCloud.jobs,1)
        self.assertIsNone(self.service.job)

    def test_completed_idempotent_job_fetches_actual_result(self):
        from cloud.service import submit_analysis
        client=mock.Mock()
        expected={'session_id':'sid','analysis_status':'complete','model_result':{'beat_count':1}}
        client.request.side_effect=[{'job_id':'j','status':'complete'},expected]
        self.assertEqual(submit_analysis(client,'sid'),expected)
        self.assertEqual(client.request.call_args.args,('GET','/api/sessions/sid/analysis'))
        client.reset_mock();client.request.side_effect=[{'status':'complete'},UploadError('missing result',status_code=404)]
        with self.assertRaises(UploadError):submit_analysis(client,'sid')
        self.assertEqual(client.request.call_count,2)
        self.assertEqual(client.request.call_args.args,('GET','/api/sessions/sid/analysis'))

    def test_controller_records_owner_at_start_and_keeps_it_after_stop(self):
        from controller import Controller
        controller=Controller(data_dir=self.root/'owner-capture',classification_enabled=False)
        try:
            sid=controller.start('demo',duration=.2,cloud_owner_id='a'*32)
            self.assertTrue(controller.wait(5))
            self.assertEqual(controller.snapshot()['cloud_owner_id'],'a'*32)
            with closing(sqlite3.connect(controller.data_dir/'sessions'/sid/'index.sqlite')) as db:
                metadata=json.loads(db.execute('SELECT metadata_json FROM sessions').fetchone()[0])
            self.assertEqual(metadata['cloud_owner_id'],'a'*32)
            self.assertGreater(controller.snapshot()['saved'],0)
        finally:
            controller.stop();controller.wait(5);controller.shutdown_environment()

    def tearDown(self):
        if ModernCloud.hold:ModernCloud.hold.set()
        if self.service:self.service.close();self.service.wait(5)
        test_cloud.CloudTests.tearDown(self)
