import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from controller import Controller


class StorageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock = mock.patch('controller.read_clock_status', return_value={
            'trusted': False, 'source': 'unknown', 'detail': 'test clock'})
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_critical_space_prevents_start_without_creating_session(self):
        c = Controller(self.directory.name)
        with mock.patch.object(c, 'storage_status', return_value={'state': 'critical'}):
            with self.assertRaisesRegex(RuntimeError, '空间'):
                c.start('demo', duration=.2)
        self.assertFalse(c.snapshot()['running'])
        self.assertFalse((Path(self.directory.name)/'sessions').exists())

    def test_low_space_stops_and_saves_accepted_prefix(self):
        c = Controller(self.directory.name)
        states = [{'state': 'ok'}, {'state': 'critical', 'free_bytes': 1}]
        with mock.patch.object(c, 'storage_status', side_effect=states):
            sid = c.start('demo', duration=2)
            self.assertTrue(c.wait(10))
        final = c.snapshot()
        self.assertIsNone(final['error'])
        self.assertEqual(final['received'], final['saved'])
        self.assertGreater(final['saved'], 0)
        self.assertLess(final['saved'], 720)
        self.assertIn('空间不足', final['status'])
        folder = Path(self.directory.name)/'sessions'/sid
        meta = json.loads((folder/'session.json').read_text(encoding='utf8'))
        self.assertEqual(meta['end_reason'], 'storage_low')
        self.assertIn('time_info', meta)
        with closing(sqlite3.connect(folder/'index.sqlite')) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='storage_low_stop'").fetchone()[0], 1)

    def test_settings_persist_and_acquisition_sensitive_changes_are_blocked(self):
        c = Controller(self.directory.name)
        c.update_settings({'display_seconds': 2, 'vref': 3.2, 'classification_enabled': False})
        other = Controller(self.directory.name)
        self.assertEqual(other.get_settings()['display_seconds'], 2)
        self.assertFalse(other.classification_enabled)
        c._state['running'] = True
        for values in ({'vref': 3.3}, {'classification_enabled': True}):
            with self.assertRaises(RuntimeError): c.update_settings(values)
        c.update_settings({'display_seconds': 5})
        self.assertEqual(c.get_settings()['display_seconds'], 5)

    def test_clock_changes_and_management_blocked_while_running(self):
        c = Controller(self.directory.name);c._state['running'] = True
        with mock.patch('controller.request_time_sync') as sync, mock.patch('controller.manual_set_time') as manual, \
             mock.patch('controller.authorize_time') as authorize:
            for call in (c.sync_time, lambda: c.set_time('2026-10-29 12:00:00'), lambda: c.authorize_time('test-only'),
                         lambda: c.trash_session('not-used'), lambda: c.restore_session('not-used'),
                         lambda: c.purge_session('not-used')):
                with self.assertRaises(RuntimeError):call()
            sync.assert_not_called();manual.assert_not_called();authorize.assert_not_called()

    def test_export_remembers_last_directory_without_changing_default(self):
        c = Controller(self.directory.name)
        default = c.get_settings()['export_dir']
        selected = Path(self.directory.name)/'selected'/'record.zip'
        with mock.patch('controller.session_management.export_bundle', return_value=str(selected)):
            self.assertEqual(c.export_bundle('test', selected), str(selected))
        self.assertEqual(c.get_settings()['last_export_dir'], str(selected.parent.resolve()))
        self.assertEqual(c.get_settings()['export_dir'], default)
        c.update_settings({'remember_export_dir': False})
        with mock.patch('controller.session_management.export_bundle', return_value=str(selected)):
            c.export_bundle('test', selected)
        self.assertEqual(c.get_settings()['last_export_dir'], '')

    def test_wall_clock_adjustment_is_recorded_without_altering_samples(self):
        c = Controller(self.directory.name)
        with mock.patch('controller.time.time', side_effect=[100., 110.]):
            sid = c.start('demo', duration=.3)
            self.assertTrue(c.wait(10))
        with closing(sqlite3.connect(Path(self.directory.name)/'sessions'/sid/'index.sqlite')) as db:
            event = db.execute("SELECT detail_json FROM events WHERE kind='clock_adjustment_observed'").fetchone()
        self.assertIsNotNone(event)
        self.assertGreater(json.loads(event[0])['wall_clock_shift_seconds'], 8)
        self.assertEqual(c.snapshot()['saved'],108)

    def test_export_succeeds_even_if_remembering_directory_fails(self):
        c = Controller(self.directory.name)
        target = Path(self.directory.name)/'result.zip'
        with mock.patch('controller.session_management.export_bundle', return_value=str(target)), \
             mock.patch.object(c, 'update_settings', side_effect=OSError('disk is read only')):
            with self.assertLogs(level='WARNING'):
                self.assertEqual(c.export_bundle('test', target), str(target))

    def test_slow_settings_write_does_not_lock_sample_processing(self):
        import threading
        c = Controller(self.directory.name)
        observed = []
        def save(root, values):
            worker = threading.Thread(target=lambda: observed.append(c.snapshot()['running']))
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive(), 'settings fsync holds UI/acquisition lock')
            return values
        with mock.patch('controller.save_settings', side_effect=save):
            c.update_settings({'display_seconds': 2})
        self.assertEqual(observed, [False])


if __name__ == '__main__':unittest.main()
