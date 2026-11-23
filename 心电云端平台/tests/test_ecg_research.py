import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import tempfile
import numpy as np

from research.physiology import interval_statistics
from research.streaming_qrs import detect_adc
from research.evaluate_ecg import matching_metrics
from research import evaluate_ecg


class IntervalTests(unittest.TestCase):
    def test_known_nn_numbers(self):
        result=interval_statistics([0,360,756,1080],normal=[True]*4,observation_seconds=300)
        self.assertAlmostEqual(result['nn']['mean_ms'],1000)
        self.assertAlmostEqual(result['nn']['sdnn_ms'],100)
        self.assertAlmostEqual(result['nn']['rmssd_ms'],np.sqrt(25000))
        self.assertEqual(result['nn']['pnn50_percent'],100)
        self.assertEqual(result['nn']['successive_pairs'],2)

    def test_unconfirmed_beats_are_rr_not_nn(self):
        result=interval_statistics([0,360,720])
        self.assertIsNone(result['nn']);self.assertEqual(result['rr']['sdrr_ms'],0)

    def test_excluded_beat_does_not_bridge_nn_pairs(self):
        result=interval_statistics([0,360,720,1080,1440],normal=[True,True,False,True,True])
        self.assertEqual(result['nn']['intervals'],2)
        self.assertEqual(result['nn']['successive_pairs'],0)
        self.assertIsNone(result['nn']['rmssd_ms'])

    def test_segment_boundary_does_not_form_rr_or_successive_difference(self):
        result=interval_statistics([0,360,720,1080],segments=[0,0,1,1],normal=[True]*4)
        self.assertEqual(result['rr']['intervals'],2);self.assertEqual(result['excluded']['segment_boundary'],1)
        self.assertEqual(result['nn']['successive_pairs'],0)

    def test_implausible_interval_is_excluded_without_joining_neighbors(self):
        result=interval_statistics([0,360,396,756],normal=[True]*4)
        self.assertEqual(result['excluded']['rr_range'],1)
        self.assertEqual(result['nn']['successive_pairs'],0)

    def test_empty_singleton_and_one_interval_are_explicit(self):
        for peaks in ([],[0]):
            self.assertIsNone(interval_statistics(peaks)['rr']['mean_ms'])
        one=interval_statistics([0,360],normal=[True,True])
        self.assertIsNone(one['nn']['sdnn_ms']);self.assertIsNone(one['nn']['rmssd_ms'])

    def test_bad_inputs_rejected(self):
        for peaks in ([1,1],[2,1],[0,float('nan')],[-1,2],[1.5,2]):
            with self.assertRaises(ValueError):interval_statistics(peaks)
        with self.assertRaises(ValueError):interval_statistics([0,360],segments=[0])
        with self.assertRaises(ValueError):interval_statistics([0,360],normal=['N','N'])

    def test_reference_nn_does_not_cross_missing_raw_samples(self):
        adc=np.full(1200,2048.);adc[700:710]=np.nan
        result=evaluate_ecg.reference_hrv([360,1080],['N','N'],adc,[0],['(N'])
        self.assertEqual(result['nn']['intervals'],0)
        self.assertEqual(result['excluded']['segment_boundary'],1)

    def test_reference_does_not_use_detector_quality_gate(self):
        # Flat raw data are rejected by the detector gate, but annotation-based
        # reference statistics are not conditioned on detector success.
        result=evaluate_ecg.reference_hrv([360,720,1080],['N','N','N'],np.full(1200,2048.),[0],['(N'])
        self.assertEqual(result['nn']['intervals'],2)

    def test_n_morphology_in_afib_or_unknown_rhythm_is_not_sinus_nn(self):
        for starts,labels in (([],[]),([0],['(AFIB'])):
            result=evaluate_ecg.reference_hrv([360,720,1080],['N']*3,np.full(1200,2048.),starts,labels)
            self.assertEqual(result['nn']['intervals'],0)

    def test_rhythm_transition_breaks_nn_even_without_a_beat_in_the_gap(self):
        result=evaluate_ecg.reference_hrv([360,720,1080],['N']*3,np.full(1200,2048.),[0,800,900],['(N','(AFIB','(N'])
        self.assertEqual(result['nn']['intervals'],1)


class StreamingTests(unittest.TestCase):
    @staticmethod
    def pulses():
        t=np.arange(20*360)/360
        raw=2048+15*np.sin(2*np.pi*.7*t)
        for p in range(1,20):raw+=350*np.exp(-.5*((t-p)/.025)**2)
        return raw

    def test_flat_signal_has_no_false_heartbeats(self):
        result=detect_adc(np.full(8*360,2048.))
        self.assertEqual(result['r_samples'],[]);self.assertEqual(result['allowed_samples'],0)

    def test_chunk_boundaries_do_not_change_peaks_or_modify_input(self):
        raw=self.pulses();before=raw.copy()
        a=detect_adc(raw,block_size=36);b=detect_adc(raw,block_size=113)
        self.assertGreater(len(a['r_samples']),10)
        self.assertEqual(a['r_samples'],b['r_samples']);np.testing.assert_array_equal(raw,before)
        self.assertLess(max(abs(np.asarray(a['r_samples'])%360)),20)

    def test_invalid_span_breaks_the_interval_sequence(self):
        raw=self.pulses();raw[9*360:10*360]=np.nan
        result=detect_adc(raw)
        self.assertGreater(len(set(result['segments'])),1)
        stats=interval_statistics(result['r_samples'],segments=result['segments'])
        self.assertGreater(stats['excluded']['segment_boundary'],0)
        self.assertEqual(result['invalid_samples'],360)

    def test_confirmation_latency_uses_the_received_block_end(self):
        with patch('research.streaming_qrs.SignalQualityGate') as gate,patch('research.streaming_qrs.RPeakDetector') as detector:
            gate.return_value.process.return_value=np.array([True]*10+[False]*26)
            detector.return_value.process.return_value=np.array([0.])
            result=detect_adc(np.full(36,2048.))
        self.assertAlmostEqual(result['performance']['confirmation_max_ms'],35*1000/360)


class MatchingTests(unittest.TestCase):
    def test_learning_beats_are_reference_beats(self):
        self.assertIn('?',evaluate_ecg.BEAT_SYMBOLS)
        self.assertNotIn('!',evaluate_ecg.BEAT_SYMBOLS)
    def test_missed_peak_does_not_create_a_false_rr_comparison(self):
        result=matching_metrics([0,360,720,1080],[0,720,1082],[0,2,3],[0,1,2],[0,0,0])
        self.assertEqual((result['tp'],result['fp'],result['fn']),(3,0,1))
        self.assertEqual(result['matched_adjacent_rr_pairs'],1)
        self.assertAlmostEqual(result['rr_mae_ms'],2000/360)

    def test_segment_reset_and_empty_reference(self):
        result=matching_metrics([0,360],[0,361],[0,1],[0,1],[0,1])
        self.assertIsNone(result['rr_mae_ms'])
        empty=matching_metrics([],[],[],[],[])
        self.assertIsNone(empty['f1']);self.assertIsNone(empty['r_mae_ms'])

    def test_duplicate_matching_is_rejected(self):
        with self.assertRaises(ValueError):matching_metrics([0,360],[0],[0,1],[0,0],[0])

    def test_empty_annotation_window_is_a_valid_evaluation(self):
        fake=SimpleNamespace(
            rdheader=lambda *a:SimpleNamespace(fs=360,n_sig=1,sig_len=3600,file_name=['empty.dat']),
            rdrecord=lambda *a,**k:SimpleNamespace(p_signal=np.zeros((3600,1)),units=['mV'],sig_name=['MLII']),
            rdann=lambda *a,**k:SimpleNamespace(sample=np.array([],dtype=int),symbol=[],aux_note=[]))
        with patch.dict('sys.modules',{'wfdb':fake,'wfdb.processing':SimpleNamespace(compare_annotations=None)}),patch.object(evaluate_ecg,'checksum',return_value='test-hash'):
            result=evaluate_ecg.evaluate_record(Path('.').resolve(),'100',10,0)
        self.assertEqual(result['qrs']['tp'],0);self.assertEqual(result['reference_hrv']['nn']['intervals'],0)

    def test_changed_source_prevents_writing_a_mislabeled_report(self):
        root=Path(__file__).resolve().parents[1]/'work'/'multi-device'/'tmp';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            output=Path(folder)/'report'
            args=['evaluate','--data',str(Path(folder)/'data'),'--records','100','--output',str(output)]
            with patch('sys.argv',args),patch.dict('sys.modules',{'wfdb':SimpleNamespace(__version__='test')}),\
                    patch.object(evaluate_ecg,'checksum',side_effect=['before']*len(evaluate_ecg.SOURCES)+['after']),\
                    patch.object(evaluate_ecg,'evaluate_record',return_value={'qrs':{'tp':0,'fp':0,'fn':0}}):
                with self.assertRaisesRegex(RuntimeError,'Source code changed'):evaluate_ecg.main()
            self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
