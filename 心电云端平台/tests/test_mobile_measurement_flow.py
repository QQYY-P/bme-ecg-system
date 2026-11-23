"""The stopped terminal record must keep its summary through phone retrieval."""
import copy
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import create_app
from test_pipeline import block, STAMP
from protocol import HEADER, ROWS


def real_block():
    payload=block()
    raw=base64.b64decode(payload['payload_base64'])
    raw=raw[:HEADER.size]+b''.join(ROWS[2].pack(*row[:-1],0) for row in ROWS[2].iter_unpack(raw[HEADER.size:]))
    payload.update(payload_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
    return payload


def measurement():
    return dict(schema_version=1, requested_duration_s=180, end_reason='stopped',
        analysis_stats=dict(schema_version=1, analyzed_real_samples=36,
            usable_real_samples=36, hr=dict(mean=72.0, min=70.0, max=74.0, window_samples=36)),
        classification_counts=dict(N=1, S=0, V=0), classification_enabled=True)


class MobileMeasurementFlowTests(unittest.TestCase):
    def setUp(self):
        root=ROOT/'work'/'mobile-measurement-flow'/'tmp'
        root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'cloud'
        self.client=TestClient(create_app(self.data,token=''))
        self.addCleanup(self.client.close)
        self.sid='mobile_record'
        self.path='/api/sessions/'+self.sid
        self.client.put(self.path,json=dict(source='serial',started_utc=STAMP))
        raw=real_block()
        self.client.put(self.path+'/chunks/0',json=raw)
        self.payload=dict(chunks=[dict(chunk_id=0,sha256=raw['sha256'],sample_count=36)],
                          sample_count=36,source_status='complete',measurement_summary=measurement())

    def test_summary_survives_complete_retry_restart_and_waveform(self):
        raw_file=self.data/'sessions'/self.sid/'chunks'/'00000000.bin'
        before=hashlib.sha256(raw_file.read_bytes()).hexdigest()
        for _ in range(2):
            response=self.client.post(self.path+'/complete',json=self.payload)
            self.assertEqual(response.status_code,200,response.text)
        with TestClient(create_app(self.data,token='')) as restarted:
            for url in [self.path,self.path+'/waveform','/api/sessions']:
                body=restarted.get(url).json()
                item=body['session'] if 'session' in body else body['sessions'][0] if 'sessions' in body else body
                self.assertEqual(item['measurement_summary']['analysis_stats']['hr']['mean'],72.0)
                self.assertEqual(item['measurement_summary']['requested_duration_s'],180)
        self.assertEqual(before,hashlib.sha256(raw_file.read_bytes()).hexdigest())

    def test_old_client_and_old_records_have_explicit_unknown_summary(self):
        self.payload.pop('measurement_summary')
        self.assertEqual(self.client.post(self.path+'/complete',json=self.payload).status_code,200)
        self.assertIsNone(self.client.get(self.path).json()['measurement_summary'])

    def test_missing_summary_on_retry_does_not_clear_existing(self):
        self.assertEqual(self.client.post(self.path+'/complete',json=self.payload).status_code,200)
        self.payload.pop('measurement_summary')
        self.assertEqual(self.client.post(self.path+'/complete',json=self.payload).status_code,200)
        self.assertIsNotNone(self.client.get(self.path).json()['measurement_summary'])

    def test_invalid_summary_does_not_mark_record_complete(self):
        for mutate in [
            lambda s:s['analysis_stats'].update(usable_real_samples=37),
            lambda s:s['analysis_stats']['hr'].update(min=80),
            lambda s:s['analysis_stats']['hr'].update(window_samples=37),
            lambda s:s['classification_counts'].update(S=-1),
            lambda s:s.update(requested_duration_s=-1),
            lambda s:s.update(owner_id='f'*32),
        ]:
            payload=copy.deepcopy(self.payload);mutate(payload['measurement_summary'])
            response=self.client.post(self.path+'/complete',json=payload)
            self.assertIn(response.status_code,(409,422),response.text)
            self.assertEqual(self.client.get(self.path).json()['upload_status'],'receiving')

    def test_non_real_source_cannot_claim_real_heart_rate(self):
        sid='simulated_record'
        self.client.put('/api/sessions/'+sid,json=dict(source='demo',started_utc=STAMP))
        self.client.put('/api/sessions/'+sid+'/chunks/0',json=real_block())
        response=self.client.post('/api/sessions/'+sid+'/complete',json=self.payload)
        self.assertEqual(response.status_code,409,response.text)

    def test_empty_measurement_is_not_invented_heart_rate(self):
        path='/api/sessions/empty_measurement'
        self.client.put(path,json=dict(source='serial',started_utc=STAMP))
        summary=measurement();summary.update(end_reason='duration_complete',classification_counts=dict(N=0,S=0,V=0))
        summary['analysis_stats']=dict(schema_version=1,analyzed_real_samples=0,usable_real_samples=0,hr=None)
        response=self.client.post(path+'/complete',json=dict(chunks=[],sample_count=0,
            source_status='complete',measurement_summary=summary))
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['measurement_summary']['analysis_stats']['hr'])


class MobileSummaryAccountTests(unittest.TestCase):
    from test_auth import AccountIsolationTests as _Accounts
    setUp=_Accounts.setUp
    login=_Accounts.login

    def test_summary_cannot_cross_account_or_be_written_by_browser_user(self):
        m=measurement();m.update(analysis_stats=None,classification_counts=None)
        payload=dict(chunks=[],sample_count=0,source_status='complete',measurement_summary=m)
        response=self.client.post('/api/sessions/alice_record/complete',headers=self.headers,json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.login()
        self.assertEqual(self.client.get('/api/sessions/alice_record').json()['measurement_summary']['requested_duration_s'],180)
        self.assertEqual(self.client.post('/api/sessions/alice_record/complete',json=payload).status_code,403)
        self.login('bob','Bobby-test-9182!')
        self.assertEqual(self.client.get('/api/sessions/alice_record').status_code,404)
        self.assertEqual(self.client.get('/api/sessions/alice_record/waveform').status_code,404)
        self.assertIsNone(self.client.get('/api/sessions').json()['sessions'][0]['measurement_summary'])


if __name__=='__main__':unittest.main()
