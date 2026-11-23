import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from contracts import SamplePoint, SampleBatch
from recording import Recorder
from cloud.service import CloudService
from cloud.uploader import Uploader, UploadError, RetryableUploadError, Cancelled


class MemoryCloud(Uploader):
    remote = {}
    calls = []
    offline = False
    wrong_session = False
    hold = None

    def request(self, method, path, payload=None):
        if self.cancelled():
            raise Cancelled()
        cls = type(self)
        cls.calls.append((method, path))
        if cls.offline:
            raise RetryableUploadError('offline')
        if path == '/health':
            return {'ok': True, 'service': 'ecg-course-demo', 'deployment': 'cloud', 'model_status': 'ready'}
        if path == '/api/sessions?limit=1':
            return {'sessions':[], 'total':0}
        sid = path.split('/')[3]
        if path.endswith('/analysis'):
            return {'session_id': 'wrong' if cls.wrong_session else sid, 'analysis_status': 'complete',
                    'model_name': 'test', 'model_version': 'test-v1',
                    'model_result': {'counts': {'N': 1, 'S': 0, 'V': 0}, 'beat_count': 1,
                                     'beats': [{'label': 'N'}], 'quality_rejections': []}}
        if method == 'PUT' and '/chunks/' in path:
            if cls.hold:
                cls.hold.wait(3)
            chunk_id = int(path.rsplit('/', 1)[1])
            receipt = {'session_id': sid, 'chunk_id': chunk_id,
                       'sha256': payload['sha256'], 'sample_count': payload['sample_count']}
            cls.remote[sid][chunk_id] = receipt
            return receipt
        if path.endswith('/complete'):
            return {'upload_status': 'complete', 'model_status': 'ready'}
        cls.remote.setdefault(sid, {})
        return {'chunks': list(cls.remote[sid].values()), 'model_status': 'ready'}


class CloudTests(unittest.TestCase):
    def test_unchanged_receipted_chunk_is_not_read_again(self):
        client = MemoryCloud('http://127.0.0.1:8766', self.root / 'state')
        client.sync(self.directory)
        read_bytes = client.stats['raw_bytes_read']
        with mock.patch('cloud.uploader.decode_chunk', side_effect=AssertionError('duplicate decode')):
            result = client.sync(self.directory)
        self.assertEqual(client.stats['raw_bytes_read'], read_bytes)
        self.assertEqual(client.stats['reused_chunks'], 1)
        self.assertEqual(result['sent_chunks'], 0)

    def test_cached_validation_detects_corruption_and_missing_file(self):
        client = MemoryCloud('http://127.0.0.1:8766', self.root / 'state')
        client.sync(self.directory)
        chunk = self.directory / 'chunks/00000000.bin'
        raw = chunk.read_bytes()
        chunk.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaises(ValueError):
            client.sync(self.directory)
        chunk.unlink()
        with self.assertRaises(OSError):
            client.sync(self.directory)

    def test_cloud_reset_requires_fresh_validation_and_resend(self):
        client = MemoryCloud('http://127.0.0.1:8766', self.root / 'state')
        client.sync(self.directory)
        MemoryCloud.remote.clear()
        result = client.sync(self.directory)
        self.assertEqual(result['sent_chunks'], 1)
        self.assertEqual(client.stats['validated_chunks'], 2)

    def test_validation_cache_eviction_falls_back_to_full_check(self):
        client = MemoryCloud('http://127.0.0.1:8766', self.root / 'state')
        client.validation_cache_limit = 0
        client.sync(self.directory)
        client.sync(self.directory)
        self.assertEqual(len(client._verified), 0)
        self.assertEqual(client.stats['validated_chunks'], 2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix='.test-cloud-')
        self.root = Path(self.temp.name)
        MemoryCloud.remote, MemoryCloud.calls = {}, []
        MemoryCloud.offline = MemoryCloud.wrong_session = False
        MemoryCloud.hold = None
        self.service = None
        metadata = {'session_id': 'test-cloud', 'source': 'serial', 'vref': 3.3}
        recorder = Recorder(self.root, metadata).start()
        points = tuple(SamplePoint(i, 0, i, i % 256, 2048, 0, 0) for i in range(360))
        recorder.submit(SampleBatch('test-cloud', 'serial', points, 1., '2026-10-29T00:00:00Z'))
        recorder.finish()
        self.directory = self.root / 'sessions/test-cloud'

    def tearDown(self):
        if MemoryCloud.hold:
            MemoryCloud.hold.set()
        if self.service:
            self.service.close()
            self.service.thread.join(4)
        self.temp.cleanup()

    def wait_for(self, field, value):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.service.snapshot()[field] == value:
                return
            self.service.reconnect()
            time.sleep(.02)
        self.fail(str(self.service.snapshot()))

    def test_upload_is_idempotent_and_does_not_change_raw_record(self):
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.directory.glob('chunks/*.bin')}
        client = MemoryCloud('http://127.0.0.1:8766', self.root / 'state')
        first = client.sync(self.directory)
        second = client.sync(self.directory)
        self.assertTrue(first['complete'])
        self.assertEqual(second['sent_chunks'], 0)
        self.assertEqual(second['confirmed_samples'], 360)
        self.assertEqual(hashes, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.directory.glob('chunks/*.bin')})
        MemoryCloud.remote['test-cloud'][0]['sha256'] = 'bad'
        with self.assertRaises(UploadError):
            client.sync(self.directory)

    def test_offline_pending_job_survives_restart_then_saves_bounded_ui_result(self):
        MemoryCloud.offline = True
        self.service = CloudService(self.root, client_factory=MemoryCloud)
        self.service.upload('test-cloud')
        self.wait_for('connection', 'offline')
        self.assertEqual(json.loads((self.root / 'cloud/pending.json').read_text())['session_id'], 'test-cloud')
        self.service.close(); self.service.thread.join(2)
        MemoryCloud.offline = False
        self.service = CloudService(self.root, client_factory=MemoryCloud)
        self.wait_for('phase', 'complete')
        self.assertNotIn('beats', self.service.snapshot()['result']['model_result'])
        saved = json.loads((self.root / 'cloud/results/test-cloud.json').read_text())
        self.assertEqual(len(saved['model_result']['beats']), 1)
        self.assertIsNone(json.loads((self.root / 'cloud/pending.json').read_text())['session_id'])

    def test_foreign_session_result_is_never_displayed_or_saved(self):
        MemoryCloud.wrong_session = True
        self.service = CloudService(self.root, client_factory=MemoryCloud)
        self.service.upload('test-cloud')
        self.wait_for('phase', 'failed')
        self.assertIsNone(self.service.snapshot()['result'])
        self.assertFalse((self.root / 'cloud/results/test-cloud.json').exists())

    def test_cancel_inflight_upload_does_not_publish_late_result(self):
        MemoryCloud.hold = threading.Event()
        self.service = CloudService(self.root, client_factory=MemoryCloud)
        self.service.upload('test-cloud')
        self.wait_for('phase', 'uploading')
        self.service.cancel()
        MemoryCloud.hold.set()
        time.sleep(.2)
        self.assertEqual(self.service.snapshot()['phase'], 'cancelled')
        self.assertIsNone(self.service.snapshot()['result'])
        self.assertFalse((self.root / 'cloud/results/test-cloud.json').exists())

    def test_invalid_session_path_rejected(self):
        self.service = CloudService(self.root, client_factory=MemoryCloud)
        with self.assertRaises(ValueError):
            self.service.upload('../outside')

    def test_slow_disk_does_not_block_ui_snapshot(self):
        self.service = CloudService(self.root, client_factory=MemoryCloud)
        entered, release, readable = threading.Event(), threading.Event(), threading.Event()
        from cloud.service import atomic_json as original
        def slow_write(path, value):
            entered.set()
            release.wait(3)
            original(path, value)
        with mock.patch('cloud.service.atomic_json', side_effect=slow_write):
            writer = threading.Thread(target=lambda: self.service.upload('test-cloud'))
            writer.start()
            self.assertTrue(entered.wait(2))
            reader = threading.Thread(target=lambda: (self.service.snapshot(), readable.set()))
            reader.start()
            responsive = readable.wait(.25)
            release.set()
            writer.join(4)
            reader.join(4)
        self.assertTrue(responsive, 'UI snapshot waited for disk persistence')

    def test_result_save_does_not_block_snapshot(self):
        from cloud.service import atomic_json as original
        entered, release, readable = threading.Event(), threading.Event(), threading.Event()
        def slow_write(path, value):
            if path.parent.name == 'results':
                entered.set()
                release.wait(3)
            original(path, value)
        self.service = CloudService(self.root, client_factory=MemoryCloud)
        with mock.patch('cloud.service.atomic_json', side_effect=slow_write):
            self.service.upload('test-cloud')
            self.assertTrue(entered.wait(2))
            reader = threading.Thread(target=lambda: (self.service.snapshot(), readable.set()))
            reader.start()
            responsive = readable.wait(.25)
            release.set()
            reader.join(4)
            self.wait_for('phase', 'complete')
        self.assertTrue(responsive, 'UI snapshot waited for full result save')

    def test_receipt_checkpoint_batches_preserve_restart_idempotency(self):
        from cloud.uploader import atomic_json as original
        # Repeated sync must remain idempotent even if the last local receipt
        # checkpoint has disappeared; the server is the source of truth.
        client = MemoryCloud('http://127.0.0.1:8766', self.root / 'state')
        client.sync(self.directory)
        for path in (self.root / 'state').glob('*.json'):
            path.unlink()
        with mock.patch('cloud.uploader.atomic_json', wraps=original) as writes:
            result = client.sync(self.directory)
        self.assertEqual(result['sent_chunks'], 0)
        self.assertEqual(writes.call_count, 1)
