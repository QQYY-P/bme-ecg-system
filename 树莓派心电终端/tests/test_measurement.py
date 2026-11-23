import gc
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from controller import Controller
from contracts import SamplePoint, SampleBatch


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/'work/application-priority/tmp'
        base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_serial_without_any_data_stops_at_deadline_and_keeps_owner(self):
        import threading
        class SilentSource(threading.Thread):
            error=None
            finished_reason='stopped'
            def __init__(self,*args):super().__init__(daemon=True);self.done=threading.Event()
            def run(self):self.done.wait(5)
            def stop(self):self.done.set()
        c=Controller(self.root,classification_enabled=False)
        self.addCleanup(lambda:(c.stop(),c.wait(6),c.shutdown_environment()))
        with mock.patch('controller.resolve_serial_port',return_value='COM4'),mock.patch('controller.Acquisition',SilentSource):
            sid=c.start('serial',port='COM4',duration=.2,cloud_owner_id='a'*32,cloud_owner_name='alice')
            self.assertTrue(c.wait(3),'串口无数据也应到时停止')
        from measurement import build_summary
        summary=build_summary(self.root,sid)
        self.assertEqual(summary['end_reason'],'duration_complete')
        self.assertEqual(summary['sample_count'],0)
        self.assertIsNone(summary['hr'])
        self.assertEqual(summary['owner_name'],'alice')
        self.assertEqual(c.list_sessions()[0]['cloud_owner_id'],'a'*32)

    def test_only_valid_real_windows_contribute_to_heart_rate(self):
        from measurement import MeasurementStats
        stats=MeasurementStats()
        def batch(mode):return SampleBatch('s','serial',tuple(SamplePoint(i,0,i,i%256,2000,mode=mode) for i in range(36)),0,'now')
        def result(usable,hr):
            return {'measurement':{'analyzed_real_samples':36,'usable_real_samples':usable,
                'hr_weight':36 if hr else 0,'hr_total':36*hr if hr else 0,'hr_min':hr,'hr_max':hr}}
        stats.observe(batch(1),result(36,72))
        stats.observe(batch(0),result(0,None))
        stats.observe(batch(0),result(36,60))
        stats.observe(batch(0),result(36,80))
        result=stats.snapshot()
        self.assertEqual(result['hr']['mean'],70)
        self.assertEqual(result['hr']['min'],60)
        self.assertEqual(result['hr']['max'],80)
        self.assertEqual(result['usable_real_samples'],72)
        self.assertEqual(result['analyzed_real_samples'],108)

    def test_quality_permission_is_counted_per_sample_not_last_in_batch(self):
        import numpy as np
        from signal_analysis import SignalAnalyzer
        from measurement import MeasurementStats
        from test_signal_analysis import batch
        analyzer=SignalAnalyzer()
        sample=batch([2000]*36,source='serial',mode=0)
        permissions=np.array([False]*35+[True])
        with mock.patch.object(analyzer.quality_gate,'process',return_value=permissions), \
             mock.patch.object(analyzer.quality_gate,'snapshot',return_value=dict(hr_allowed=True,signal_state='good',signal_text='good')), \
             mock.patch.object(analyzer.preprocessor,'process',side_effect=lambda values:(values,np.zeros(len(values),dtype=bool))):
            result=analyzer.process(sample)
        stats=MeasurementStats();stats.observe(sample,result)
        self.assertEqual(stats.snapshot()['usable_real_samples'],1)
        self.assertIsNone(stats.snapshot()['hr'])

    def test_measurement_statistics_do_not_depend_on_batch_size(self):
        from signal_analysis import SignalAnalyzer
        from measurement import MeasurementStats
        from test_signal_analysis import batch,ecg
        values=ecg(12)
        a=SignalAnalyzer();whole=MeasurementStats();sample=batch(values,source='serial',mode=0)
        whole.observe(sample,a.process(sample))
        a=SignalAnalyzer();parts=MeasurementStats()
        for start in range(0,len(values),36):
            sample=batch(values[start:start+36],start=start,source='serial',mode=0)
            parts.observe(sample,a.process(sample))
        self.assertEqual(whole.snapshot(),parts.snapshot())

    def test_summary_of_demo_has_no_clinical_heart_rate_and_raw_unchanged(self):
        from measurement import build_summary
        c=Controller(self.root,classification_enabled=False)
        self.addCleanup(lambda:(c.stop(),c.wait(5),c.shutdown_environment()))
        sid=c.start('demo',duration=.15)
        self.assertTrue(c.wait(5))
        directory=self.root/'sessions'/sid
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*.bin')}
        summary=build_summary(self.root,sid)
        self.assertGreater(summary['sample_count'],0)
        self.assertIsNone(summary['hr'])
        self.assertEqual(summary['source'],'demo')
        self.assertEqual(summary['classification_counts'],{'N':0,'S':0,'V':0})
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*.bin')})

    def test_preflight_blocks_missing_port_or_low_disk_but_not_offline(self):
        from measurement import preflight_checks
        checks=preflight_checks(source='serial',port='COM4',storage={'state':'ok','free_bytes':999999999},cloud={'connection':'offline'},owner_name='alice')
        self.assertFalse(any(row['blocking'] for row in checks))
        self.assertIn('待检查',next(r['detail'] for r in checks if r['key']=='signal'))
        checks=preflight_checks(source='serial',port='',storage={'state':'critical'},cloud={},owner_name='未分配')
        self.assertTrue(any(r['blocking'] for r in checks if r['key']=='port'))
        self.assertTrue(any(r['blocking'] for r in checks if r['key']=='storage'))

    def test_pdf_export_keeps_raw_and_rejects_raw_destination(self):
        from measurement import export_pdf
        c=Controller(self.root,classification_enabled=False)
        self.addCleanup(lambda:(c.stop(),c.wait(5),c.shutdown_environment()))
        sid=c.start('demo',duration=.1)
        self.assertTrue(c.wait(5))
        directory=self.root/'sessions'/sid
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file() and p.suffix not in ('.sqlite','-shm','-wal') and not p.name.endswith(('-shm','-wal'))}
        target=self.root/'exports'/'summary.pdf'
        self.assertEqual(export_pdf(self.root,sid,target),str(target))
        self.assertTrue(target.read_bytes().startswith(b'%PDF-'))
        with self.assertRaises(ValueError):export_pdf(self.root,sid,directory/'bad.pdf')
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file() and p.suffix not in ('.sqlite','-shm','-wal') and not p.name.endswith(('-shm','-wal'))})

    def test_manual_stop_keeps_manual_reason(self):
        c=Controller(self.root,classification_enabled=False)
        self.addCleanup(lambda:(c.stop(),c.wait(5),c.shutdown_environment()))
        sid=c.start('demo',duration=180)
        time.sleep(.1);c.stop();self.assertTrue(c.wait(5))
        self.assertEqual(c.measurement_summary(sid)['end_reason'],'stopped')

    def test_old_report_generation_does_not_hold_new_capture_start(self):
        import threading
        c=Controller(self.root,classification_enabled=False)
        entered,release=threading.Event(),threading.Event()
        def slow_export(*args):entered.set();release.wait(3);return 'report.pdf'
        with mock.patch('measurement.export_pdf',side_effect=slow_export):
            worker=threading.Thread(target=lambda:c.export_measurement_pdf('old','report.pdf'))
            worker.start();self.assertTrue(entered.wait(2))
            started=time.monotonic()
            try:
                c.start('demo',duration=.1)
                self.assertLess(time.monotonic()-started,.7)
            finally:release.set();worker.join(4);c.stop();c.wait(5);c.shutdown_environment()


if __name__=='__main__': unittest.main()
