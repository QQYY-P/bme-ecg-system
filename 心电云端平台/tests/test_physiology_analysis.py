import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from physiology_analysis import PhysiologyService, MAX_SAMPLES


class PhysiologyAnalysisTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.test_runs'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = PhysiologyService(self.root)
        self.metadata = {'source': 'serial', 'sample_rate_hz': 360, 'vref_v': 3.3}

    @staticmethod
    def rows(seconds=24, start=10000):
        times = np.arange(seconds * 360) / 360
        raw = 2048 + 15 * np.sin(2 * np.pi * .7 * times)
        for second in range(1, seconds):
            raw += 350 * np.exp(-.5 * ((times - second) / .025) ** 2)
        return [(start+i, 0, i, i % 256, int(value), 0, 0) for i, value in enumerate(raw)]

    def analyze(self, rows, name='record', metadata=None, chunk_size=900):
        metadata = metadata or self.metadata
        digest = self.service.digest(metadata, True, [{'sha256': name, 'sample_count': len(rows)}])
        batches = (({}, rows[i:i+chunk_size]) for i in range(0, len(rows), chunk_size))
        return self.service.run(name, metadata, True, len(rows), digest, batches)

    def test_complete_peaks_are_chunk_independent_and_hrv_is_not_inferred(self):
        rows = self.rows()
        first = self.analyze(rows, chunk_size=113)
        second = self.analyze(rows, name='other', chunk_size=1729)
        self.assertEqual(first['analysis_status'], 'complete')
        self.assertEqual(first['r_peaks'], second['r_peaks'])
        self.assertGreater(len(first['r_peaks']), 15)
        self.assertTrue(all(abs((p['sample_id']-10000) % 360) < 20 for p in first['r_peaks']))
        self.assertAlmostEqual(first['rr']['mean_ms'], 1000, delta=10)
        self.assertEqual(first['hrv']['status'], 'unavailable')
        self.assertEqual(first['hrv']['reason'], 'normality_not_confirmed')
        self.assertIsNone(first['hrv']['metrics'])
        self.assertEqual(rows, self.rows())
        self.assertEqual(self.service.read('record', first['source_digest']), first)
        json.dumps(first, allow_nan=False)

    def test_discontinuities_and_invalid_samples_do_not_bridge_rr(self):
        for boundary in ('segment', 'sample_gap', 'flags', 'invalid', 'mode', 'segment_index'):
            with self.subTest(boundary=boundary):
                rows = [list(r) for r in self.rows(30)]
                split = 15*360
                if boundary == 'segment':
                    for r in rows[split:]: r[1] = 1
                elif boundary == 'sample_gap':
                    for r in rows[split:]: r[0] += 10
                elif boundary == 'segment_index':
                    for r in rows[split:]: r[2] += 10
                elif boundary == 'flags': rows[split][5] = 4
                elif boundary == 'invalid': rows[split][5] = 1
                elif boundary == 'mode': rows[split][6] = 1
                result = self.analyze(rows, name=boundary)
                self.assertGreater(result['excluded']['segment_boundary'], 0)
                crossing = [r for r in result['rr_intervals'] if r['start_sample'] < 10000+split <= r['end_sample']]
                self.assertTrue(crossing)
                self.assertTrue(all(not r['accepted'] and r['reason'] == 'segment_boundary' for r in crossing))

    def test_simulation_unknown_mode_and_invalid_data_have_no_physiology(self):
        for source, mode, flags in [('demo',0,0), ('unknown',0,0), ('serial',1,0), ('serial',None,0), ('serial',0,1)]:
            with self.subTest(source=source,mode=mode,flags=flags):
                rows = [(*r[:5],flags,mode) for r in self.rows(5)]
                result = self.analyze(rows, metadata={**self.metadata,'source':source})
                self.assertEqual(result['analysis_status'], 'ineligible')
                self.assertEqual(result['summary']['eligible_samples'], 0)
                self.assertEqual(result['r_peaks'], [])
                self.assertIsNone(result['rr']['mean_ms'])
                self.assertIsNone(result['hrv']['metrics'])

    def test_flat_input_and_empty_record_report_unknown_not_normal(self):
        for rows in ([], [(i,0,i,i%256,2048,0,0) for i in range(3600)]):
            result = self.analyze(rows)
            self.assertEqual(result['analysis_status'], 'ineligible')
            self.assertEqual(result['summary']['quality_allowed_samples'], 0)
            self.assertIsNone(result['rr']['mean_rate_bpm'])

    def test_bad_count_order_and_sample_rate_do_not_publish_result(self):
        rows = self.rows(5)
        for data, count, metadata in [(rows,len(rows)+1,self.metadata),
                ([rows[1],rows[0]],2,self.metadata), (rows,len(rows),{**self.metadata,'sample_rate_hz':250})]:
            with self.assertRaises(ValueError):
                self.service.run('bad',metadata,True,count,'a'*64,[({},data)])
        self.assertEqual(self.service.read('bad','a'*64)['analysis_status'], 'not_run')

    def test_oversized_input_is_rejected_before_consuming_batches(self):
        def batches():
            raise AssertionError('oversized input was loaded')
            yield
        with self.assertRaises(ValueError):
            self.service.run('huge',self.metadata,True,MAX_SAMPLES+1,'b'*64,batches())

    def test_results_preserve_prior_versions_and_existing_classification(self):
        legacy = self.root/'analysis'/'record.json'
        legacy.parent.mkdir()
        legacy.write_text('{"old_model_result":true}')
        before = legacy.read_bytes()
        first = self.analyze(self.rows(6))
        second = self.analyze(self.rows(8))
        self.assertNotEqual(first['source_digest'], second['source_digest'])
        self.assertEqual(self.service.read('record',first['source_digest']), first)
        self.assertEqual(self.service.read('record',second['source_digest']), second)
        self.assertEqual(legacy.read_bytes(),before)
