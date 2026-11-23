import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_app
from protocol import HEADER, ROWS
from uploader import Uploader, UploadError
from demo import create_demo

STAMP = '2026-10-27T08:00:00+00:00'


def block(start=0, count=36, adc=2100, version=2, invalid=False, segment=0):
    rows = []
    for i in range(count):
        row = (start+i, segment, i, (start+i)%256, 65535 if invalid and i == 3 else adc, 1 if invalid and i == 3 else 0)
        rows.append(row + (1,) if version == 2 else row)
    raw = HEADER.pack(b'ECGRAW01',version,ROWS[version].size,360,count)+b''.join(ROWS[version].pack(*r) for r in rows)
    return {'payload_base64':base64.b64encode(raw).decode(), 'sha256':hashlib.sha256(raw).hexdigest(),
        'sample_count':count,'first_sample_id':start,'last_sample_id':start+count-1,
        'received_first_utc':STAMP,'received_last_utc':STAMP}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / '.test_runs'
        test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.data = Path(self.temp.name) / 'cloud'
        self.client = TestClient(create_app(self.data, token=''))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.client.close)
        self.meta = {'source':'demo','started_utc':STAMP}
        self.assertEqual(self.client.put('/api/sessions/session_1', json=self.meta).status_code,200)

    def put(self, payload=None, chunk_id=0):
        return self.client.put(f'/api/sessions/session_1/chunks/{chunk_id}',json=payload or block())

    def finish(self, items, total):
        return self.client.post('/api/sessions/session_1/complete',json={
            'chunks':items,'sample_count':total,'source_status':'complete'})

    def test_idempotency_and_persistence(self):
        self.assertFalse(self.put().json()['duplicate'])
        self.assertTrue(self.put().json()['duplicate'])
        with TestClient(create_app(self.data, token='')) as restarted:
            data = restarted.get('/api/sessions/session_1').json()
            self.assertEqual((data['sample_count'],data['chunk_count']),(36,1))
            self.assertEqual(len(restarted.get('/api/sessions/session_1/waveform').json()['points']),36)

    def test_checksum_and_header_rejected_without_indexing(self):
        payload = block();payload['sha256']='a'*64
        self.assertEqual(self.put(payload).status_code,400)
        payload=block();payload['sample_count']=37
        self.assertEqual(self.put(payload).status_code,400)
        payload=block();payload['payload_base64']='!!!!'
        self.assertEqual(self.put(payload).status_code,400)
        self.assertEqual(self.client.get('/api/sessions/session_1').json()['sample_count'],0)

    def test_conflicting_content_does_not_overwrite(self):
        self.put()
        self.assertEqual(self.put(block(adc=2200)).status_code,409)
        points=self.client.get('/api/sessions/session_1/waveform').json()['points']
        self.assertEqual(points[0][1],2100)

    def test_missing_manifest_not_complete(self):
        self.put()
        self.assertEqual(self.finish([],0).status_code,409)
        self.assertEqual(self.client.get('/api/sessions/session_1').json()['upload_status'],'receiving')

    def test_complete_and_no_append(self):
        payload=block();self.put(payload)
        item={'chunk_id':0,'sha256':payload['sha256'],'sample_count':36}
        self.assertEqual(self.finish([item],36).json()['upload_status'],'complete')
        self.assertTrue(self.put(payload).json()['duplicate'])
        self.assertEqual(self.put(block(start=36),1).status_code,409)

    def test_out_of_order_and_overlap(self):
        self.assertEqual(self.put(block(start=36),1).status_code,200)
        self.assertEqual(self.put(block(start=0),0).status_code,200)
        self.assertEqual(self.put(block(start=50),2).status_code,409)
        self.assertEqual(self.client.get('/api/sessions/session_1').json()['sample_count'],72)

    def test_invalid_and_segment_gap_not_joined(self):
        self.put(block(invalid=True))
        self.put(block(start=50,segment=1),1)
        waveform=self.client.get('/api/sessions/session_1/waveform').json()
        self.assertIsNone(waveform['points'][3][1])
        self.assertTrue(waveform['points'][36][5])
        self.assertEqual(waveform['session']['valid_count'],71)

    def test_v1_export_preserves_unknown_mode(self):
        self.put(block(version=1))
        response=self.client.get('/api/sessions/session_1/export')
        rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows),36)
        self.assertEqual(rows[0]['mode'],'')
        self.assertEqual(rows[0]['source'],'demo')
        self.assertEqual(rows[0]['raw_adc'],'2100')

    def test_corrupt_storage_is_reported(self):
        self.put()
        (self.data/'sessions/session_1/chunks/00000000.bin').write_bytes(b'bad')
        self.assertEqual(self.client.get('/api/sessions/session_1/export').status_code,409)
        self.assertEqual(self.client.get('/api/sessions/session_1/waveform').status_code,409)
        self.assertEqual(self.put().status_code,409)

    def test_identity_and_no_models(self):
        self.assertEqual(self.client.put('/api/sessions/session_1',json={**self.meta,'source':'serial'}).status_code,409)
        result=self.client.get('/api/sessions/session_1/analysis').json()
        self.assertEqual(result['model_status'],'not_configured')
        self.assertIsNone(result['model_result'])
        self.assertNotIn('numpy_predictor',sys.modules)
        self.assertNotIn('torch',sys.modules)

    def test_access_and_request_limits(self):
        with TestClient(create_app(self.data,token='test-token')) as guarded:
            self.assertEqual(guarded.get('/api/sessions').status_code,401)
            self.assertEqual(guarded.get('/api/sessions',headers={'X-API-Key':'test-token'}).status_code,200)
        self.assertEqual(self.client.put('/api/sessions/another',json=self.meta,headers={'Origin':'https://unrelated.example'}).status_code,403)
        self.assertEqual(self.client.put('/api/sessions/another',content=b'x'*(2*1024*1024+1)).status_code,413)

    def test_real_recorder_uploader_resume_and_csv(self):
        source=create_demo(Path(self.temp.name)/'source',seconds=2)
        client=Uploader('http://127.0.0.1:1',Path(self.temp.name)/'state',timeout=.1)
        with self.assertRaises(UploadError):
            client.sync(source)
        self.assertFalse(list((Path(self.temp.name)/'state').glob('*.json')))
        def request(method,path,payload=None):
            response=self.client.request(method,path,json=payload)
            if response.status_code>=400:
                raise UploadError(str(response.json()))
            return response.json()
        client.request=request
        first=client.sync(source)
        self.assertEqual(first['confirmed_samples'],720)
        self.assertTrue(first['complete'])
        self.assertEqual(client.sync(source)['sent_chunks'],0)
        sid=source.name
        exported=self.client.get(f'/api/sessions/{sid}/export')
        rows=list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows),720)
        self.assertTrue(all(r['source']=='demo' and r['mode']=='1' for r in rows))
        # A new cloud database must trigger retransmission despite saved local receipts.
        with TestClient(create_app(Path(self.temp.name)/'reset',token='')) as reset:
            old=self.client;self.client=reset
            self.assertEqual(client.sync(source)['sent_chunks'],2)
            self.client=old


if __name__ == '__main__':
    unittest.main(verbosity=2)
