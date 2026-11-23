import time
import unittest
from pathlib import Path
from dataclasses import replace
from threading import Event
import random
import numpy as np

from contracts import SamplePoint,SampleBatch,INVALID,DISCONTINUITY
from acquisition import demo_value
from cardioedge.numpy_candidate import Candidate
from cardioedge.stream import StreamingClassifier
from cardioedge.service import ClassificationService
from ui.main_window import classification_display


def batch(values,start=0,mode=0,source='serial',flags=0):
    return SampleBatch('test',source,tuple(SamplePoint(start+i,0,start+i,(start+i)%256,int(v),flags,mode)
        for i,v in enumerate(values)),time.monotonic(),'test')


class CardioEdgeTests(unittest.TestCase):
    def test_slow_start_recovers_and_accepts_fresh_samples(self):
        events=[];service=ClassificationService(on_result=events.append)
        service.INITIAL_WAIT_SECONDS=.001
        try:
            service.start()
            self.assertEqual(service.snapshot()['state'],'loading')
            service.submit(batch([2048]*36))
            self.assertEqual(service.snapshot()['startup_skipped_samples'],36)
            self.assertEqual(service.snapshot()['pending_blocks'],0)
            service.invalidate('mode confirmed')
            self.assertEqual(service.snapshot()['state'],'loading')
            self.assertTrue(service.ready.wait(30))
            self.assertTrue(service.snapshot()['model_loaded'])
            self.assertIsNone(service.snapshot()['error'])
            rng=random.Random(42)
            service.submit(batch([demo_value(i,rng) for i in range(360*12)]))
            deadline=time.monotonic()+15
            while not events and time.monotonic()<deadline:time.sleep(.02)
            self.assertGreater(len(events),0,service.snapshot())
        finally:service.stop();service.wait(20)

    def test_startup_deadline_remains_visible_after_mode_and_disconnect(self):
        service=ClassificationService();service.INITIAL_WAIT_SECONDS=.001
        service.STARTUP_TIMEOUT_SECONDS=.001
        try:
            service.start();self.assertTrue(service.ready.wait(3))
            status=service.snapshot();self.assertEqual(status['state'],'error')
            self.assertIsNotNone(status['error'])
            service.invalidate('mode changed');service.last_received=time.monotonic()-10
            service.submit(batch([2048]*36))
            self.assertEqual(service.snapshot()['state'],'error')
            self.assertEqual(service.snapshot()['error'],status['error'])
            self.assertIn(status['error'],service.snapshot()['detail'])
            self.assertEqual(service.snapshot()['pending_blocks'],0)
            time.sleep(.3)
            self.assertFalse(service.snapshot()['model_loaded'])
        finally:service.stop();service.wait(20)

    def test_stop_during_loading_does_not_reactivate_model(self):
        service=ClassificationService();service.INITIAL_WAIT_SECONDS=.001
        service.start();service.stop();service.wait(20)
        self.assertFalse(service.snapshot()['model_loaded'])
        self.assertEqual(service.snapshot()['classified'],0)
        self.assertIsNone(service.snapshot()['last'])

    def test_pinned_model_matches_all_reference_vectors(self):
        folder=Path(__file__).resolve().parents[1]/'cardioedge';m=Candidate(folder/'portable_weights.npz')
        with np.load(folder/'pi_parity_vectors.npz') as v:
            for x,r,y in zip(v['wave'],v['rr'],v['output']):
                np.testing.assert_array_equal(m.predict_quantized(x,r),y)

    def test_non360_sampling_is_rejected(self):
        with self.assertRaises(ValueError):StreamingClassifier(sample_rate=250)

    def test_model_filter_rejects_baseline_and_mains_preserving_qrs_band(self):
        from scipy.signal import sosfiltfilt
        m=StreamingClassifier();t=np.arange(360*40)/360
        gains={}
        for hz in (.1,10,50,90):
            x=np.sin(2*np.pi*hz*t)
            y=sosfiltfilt(m.sos,x)
            gains[hz]=np.sqrt(np.mean(y[360*10:-360*10]**2))/np.sqrt(np.mean(x[360*10:-360*10]**2))
        self.assertGreater(gains[10],.95)
        self.assertLess(gains[.1],.01)
        self.assertLess(gains[50],.01)
        self.assertLess(gains[90],.01)

    def test_invalid_simulation_and_unknown_never_reach_model(self):
        m=StreamingClassifier()
        for mode,source,value in [(0,'serial',65535),(0,'serial',0),(0,'serial',4095),
                                  (1,'serial',2048),(None,'replay',2048),(0,'demo',2048)]:
            self.assertEqual(m.process(batch([value]*800,mode=mode,source=source)),[])
            self.assertIsNone(m.snapshot()['last'])
        self.assertEqual(m.classified,0)

    def test_packet_size_invariance_and_invalid_reset(self):
        rng=random.Random(42);raw=np.array([demo_value(i,rng) for i in range(360*17)])
        outputs=[]
        for size in (36,157):
            m=StreamingClassifier();rows=[]
            for i in range(0,len(raw),size):rows.extend(m.process(batch(raw[i:i+size],i)))
            self.assertGreater(len(rows),3)
            outputs.append([(r['r_sample'],r['scores']) for r in rows])
            m.process(batch([65535],len(raw),flags=INVALID))
            self.assertIsNone(m.snapshot()['last'])
            self.assertEqual(len(m.peaks),0)
            self.assertEqual(len(m.values),0)
        self.assertEqual(outputs[0],outputs[1])

    def test_long_flat_signal_is_never_normal(self):
        m=StreamingClassifier()
        for i in range(0,360*20,36):self.assertEqual(m.process(batch([2048]*36,i)),[])
        self.assertEqual(m.classified,0)
        self.assertIsNone(m.snapshot()['last'])

    def test_inflight_result_cannot_cross_generation_barrier(self):
        rng=random.Random(42);raw=[demo_value(i,rng) for i in range(360*12)]
        events=[];service=ClassificationService(on_result=events.append)
        service.start();service.submit(batch(raw))
        deadline=time.monotonic()+4
        while not service.snapshot()['processing'] and time.monotonic()<deadline:time.sleep(.005)
        self.assertTrue(service.snapshot()['processing'], service.snapshot())
        service.invalidate('lead off');time.sleep(1.2)
        self.assertIsNone(service.snapshot()['last']);self.assertEqual(events,[])
        service.stop();self.assertTrue(service.wait())

    def test_ui_suppresses_bad_mode_quality_and_stopped_predictions(self):
        good=dict(running=True,state='RECORDING',source='serial',board_mode=0,signal_state='good',
                  classification=dict(state='ready',last=dict(label='V',scores=dict(N=.1,S=.1,V=.8))))
        self.assertEqual(classification_display(good)[0],'V')
        for update in [dict(running=False),dict(board_mode=1),dict(source='demo'),dict(signal_state='invalid')]:
            self.assertEqual(classification_display(dict(good,**update))[0],'--')

    def test_stop_with_queued_input_is_bounded_and_repeatable(self):
        service=ClassificationService();service.start()
        try:
            for i in range(50):service.submit(batch([65535]*36,i*36))
            service.stop();self.assertTrue(service.wait())
            service.stop();self.assertTrue(service.wait())
            self.assertIsNone(service.snapshot()['last'])
            self.assertEqual(service.snapshot()['pending_blocks'],0)
        finally:service.stop();service.wait()

if __name__=='__main__':unittest.main()
