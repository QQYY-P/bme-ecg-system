import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import closing

from contracts import SamplePoint, SampleBatch
from recording import Recorder
from cloud.uploader import Uploader


class TransferTests(unittest.TestCase):
    def test_failed_save_sends_unknown_statistics_instead_of_unsaved_counts(self):
        import sqlite3
        from cloud.measurement_transfer import upload_summary
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE events(event_id INTEGER,kind TEXT,detail_json TEXT)')
            stats=dict(schema_version=1,analyzed_real_samples=360,usable_real_samples=360,hr=None)
            db.execute('INSERT INTO events VALUES(1,?,?)',('measurement_summary',json.dumps(stats)))
            result=upload_summary(db,dict(session_id='failed',status='failed',sample_count=0,end_reason='error'))
            self.assertIsNone(result['analysis_stats'])
            self.assertIsNone(result['classification_counts'])
            self.assertEqual(result['end_reason'],'error')

    def test_committed_summary_follows_upload_without_modifying_source(self):
        root=Path(__file__).resolve().parents[1]/'work'/'mobile-measurement-flow'/'tmp'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            directory=Path(folder);calls=[]
            recorder=Recorder(directory,dict(session_id='transfer',source='serial',requested_duration_s=180)).start()
            points=tuple(SamplePoint(i,0,i,i%256,2048,0,0) for i in range(36))
            recorder.submit(SampleBatch('transfer','serial',points,1.,'2026-11-23T00:00:00Z'))
            stats=dict(schema_version=1,analyzed_real_samples=36,usable_real_samples=36,
                       hr=dict(mean=72.,min=72.,max=72.,window_samples=36))
            recorder.add_event('measurement_summary',stats)
            recorder.add_event('cardioedge_prediction',dict(label='S',r_sample=10,segment_id=0,session_id='transfer'))
            recorder.add_event('cardioedge_prediction',dict(label='V',r_sample=20,segment_id=0,session_id='foreign'))
            recorder.finish()
            source=directory/'sessions'/'transfer'
            files=lambda:{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*.bin')}
            before=files()
            class InMemory(Uploader):
                def request(self,method,path,payload=None):
                    calls.append((method,path,payload))
                    if '/chunks/' in path:
                        return dict(session_id='transfer',chunk_id=0,sha256=payload['sha256'],sample_count=payload['sample_count'])
                    if path.endswith('/complete'):return dict(upload_status='complete',model_status='not_configured')
                    return dict(chunks=[],model_status='not_configured')
            result=InMemory('http://127.0.0.1:1',directory/'state').sync(source)
            self.assertTrue(result['complete'])
            payload=next(p for _,url,p in calls if url.endswith('/complete'))
            self.assertEqual(payload['measurement_summary']['analysis_stats'],stats)
            self.assertEqual(payload['measurement_summary']['classification_counts'],dict(N=0,S=1,V=0))
            self.assertEqual(before,files())


if __name__=='__main__':unittest.main()
