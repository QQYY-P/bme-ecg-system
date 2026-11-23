import queue
import threading
import unittest
from unittest import mock

import numpy as np

from cardioedge.service import ClassificationService
from cardioedge.stream import StreamingClassifier
from contracts import SampleBatch, SamplePoint, DISCONTINUITY
from signal_analysis import SignalAnalyzer


class ClassificationPresentationTests(unittest.TestCase):
    def test_rejected_candidate_is_identified_once_and_not_inferred_from_missing_samples(self):
        engine = StreamingClassifier(model=object())
        engine.key = ('synthetic-review', 'serial', 5, 0)
        engine.context_first = 0
        engine.values.extend([2048.] * 7200)
        engine.ids.extend(range(7200))
        engine.peaks.extend([2880, 3240, 3600])
        self.assertEqual(engine._due(), [])
        events = engine.take_rejections()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['r_sample'], 3240)
        self.assertEqual(events[0]['segment_id'], 5)
        self.assertIn('幅度', events[0]['reason'])
        self.assertNotIn('label', events[0])
        self.assertEqual(engine._due(), [])
        self.assertEqual(engine.take_rejections(), [])
        self.assertEqual(engine.rejected, 1)

    def test_unclaimed_rejections_do_not_accumulate_in_standalone_replay(self):
        engine=StreamingClassifier(model=object())
        engine.key=('test','serial',0,0)
        engine._reject_candidate(360,720,'test')
        engine.process(SampleBatch('test','serial',(),1.,'test'))
        self.assertEqual(engine.take_rejections(),[])
        engine.key=('test','serial',0,0)
        engine._reject_candidate(720,1080,'test')
        engine.reset()
        self.assertEqual(engine.take_rejections(),[])

    def test_service_history_is_bounded_and_counts_are_committed_callbacks(self):
        accepted, rejected = [], []
        service = ClassificationService(on_result=accepted.append, on_rejected=rejected.append)
        original_output = service.outputs
        service.outputs = queue.Queue()
        rows = [dict(session_id='review',segment_id=1,r_sample=i*360,label='N',scores={'N':.8,'S':.1,'V':.1})
                for i in range(75)]
        service.outputs.put((0,'result',(
            dict(state='ready',last=dict(rows[-1]),classified=999,counts={'N':999},rejected=999),
            rows,0.,1.,[dict(r_sample=99999,segment_id=1,reason='test candidate rejection')]
        )))
        service.outputs.put((None,'error','synthetic end of collector'))
        service._collect()
        snapshot=service.snapshot()
        self.assertEqual(snapshot['classified'],75)
        self.assertEqual(snapshot['counts'],{'N':75,'S':0,'V':0})
        self.assertEqual(snapshot['rejected'],1)
        self.assertEqual(len(snapshot['recent']),60)
        self.assertEqual(snapshot['recent'][0]['prediction_id'],16)
        self.assertEqual(snapshot['recent'][-1]['r_sample'],74*360)
        self.assertIsNone(snapshot['last'])  # error never shows an old live result
        snapshot['recent'][0]['label']='V'
        self.assertEqual(service.snapshot()['recent'][0]['label'],'N')
        self.assertEqual(len(accepted),75)
        self.assertEqual(len(rejected),1)
        service.outputs=original_output
        service.stop();service.wait()

    def test_old_generation_never_enters_recent_or_rejection_statistics(self):
        service=ClassificationService()
        original_output=service.outputs
        service.outputs=queue.Queue()
        service.invalidate('new mode')
        service.outputs.put((0,'result',({},[dict(label='V')],0.,0.,[{}])))
        service.outputs.put((None,'error','synthetic end'))
        service._collect()
        snapshot=service.snapshot()
        self.assertEqual(snapshot['recent'],[])
        self.assertEqual(snapshot['classified'],0)
        self.assertEqual(snapshot['rejected'],0)
        service.outputs=original_output
        service.stop();service.wait()

    def test_failed_result_save_does_not_publish_the_prediction(self):
        service=ClassificationService(on_result=lambda row:False)
        original_output=service.outputs
        service.outputs=queue.Queue()
        row=dict(session_id='test',segment_id=1,r_sample=360,label='V',scores={})
        service.outputs.put((0,'result',({'state':'ready','last':row},[row],0.,0.,[])))
        service._collect()
        state=service.snapshot()
        self.assertEqual(state['recent'],[])
        self.assertEqual(state['classified'],0)
        self.assertIsNone(state['last'])
        self.assertIn('无法保存',state['error'])
        service.outputs=original_output
        service.stop();service.wait()

    def test_analysis_preserves_segment_ids_for_precise_annotation(self):
        points=(SamplePoint(0,3,0,0,2048,0,0),SamplePoint(1,3,1,1,2100,0,0),
                SamplePoint(2,4,0,2,2120,DISCONTINUITY,0))
        wave=SignalAnalyzer().process(SampleBatch('test','serial',points,1.,'test'))['wave']
        self.assertEqual(len(wave['segment_ids']),len(wave['x']))
        self.assertEqual(list(wave['segment_ids'][np.isfinite(wave['x'])]),[3,3,4])
        self.assertTrue(np.isnan(wave['x']).any())


if __name__=='__main__': unittest.main()
