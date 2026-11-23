"""Standby must never acquire implicitly; power actions are always mocked."""
import unittest
from unittest import mock
import tkinter as tk
import gc

import app
from ui.main_window import MainWindow, DEFAULT_UI_SETTINGS


class EntrypointTests(unittest.TestCase):
    def launch(self, args):
        with mock.patch('controller.Controller') as cls, mock.patch('tkinter.Tk') as tk_cls, \
                mock.patch('ui.main_window.MainWindow') as view_cls:
            callbacks = []
            tk_cls.return_value.after.side_effect = lambda delay, cb: callbacks.append(cb)
            app.main(args)
            for cb in callbacks:
                cb()
            return cls.return_value, view_cls, tk_cls.return_value

    def test_standby_with_serial_preference_never_starts(self):
        controller, view, root = self.launch(['--standby', '--port', 'auto', '--fullscreen'])
        controller.start.assert_not_called()
        view.return_value.start.assert_not_called()
        self.assertTrue(view.call_args.kwargs['simple_mode'])
        root.attributes.assert_called_with('-fullscreen', True)

    def test_legacy_demo_still_starts(self):
        _, view, _ = self.launch(['--demo'])
        view.return_value.start.assert_called_once()

    def test_standby_rejects_headless(self):
        with self.assertRaises(SystemExit), mock.patch('controller.Controller') as cls:
            app.main(['--standby', '--headless', '--demo'])
        cls.assert_not_called()


class DeviceUiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.geometry('800x480+10000+10000')
        self.controller = mock.Mock()
        self.controller.get_settings.return_value = dict(DEFAULT_UI_SETTINGS)
        self.controller.list_ports.return_value = []
        self.controller.wait.return_value = True
        self.controller.snapshot.return_value = dict(running=False, state='IDLE')
        self.view = MainWindow(self.root, self.controller, simple_mode=True)
        for cb in self.root.tk.call('after', 'info'):
            self.root.after_cancel(cb)
        self.view._task = lambda op, success=None, failure=None: success(op()) if success else op()

    def tearDown(self):
        try:
            for cb in self.root.tk.call('after', 'info'):
                self.root.after_cancel(cb)
            self.root.destroy()
        except tk.TclError:
            pass
        gc.collect()

    def test_idle_hotplug_selects_one_usb_without_opening_it(self):
        self.view.refresh_ports()
        self.assertEqual(str(self.view.start_button['state']), 'disabled')
        self.controller.list_ports.return_value = [dict(device='COM4', usb=True)]
        self.view.refresh_ports()
        self.assertEqual(self.view.port.get(), 'COM4')
        self.assertEqual(str(self.view.start_button['state']), 'normal')
        self.controller.start.assert_not_called()
        self.controller.list_ports.return_value = []
        self.view.refresh_ports()
        self.assertEqual(self.view.port.get(), '')
        self.assertEqual(str(self.view.start_button['state']), 'disabled')

    def test_multiple_usb_requires_selection_and_scan_does_not_start(self):
        self.controller.list_ports.return_value = [dict(device='COM4', usb=True), dict(device='COM5', usb=True)]
        self.view.refresh_ports()
        self.assertEqual(self.view.port.get(), '')
        self.assertIn('多个', self.view._ports_message)
        self.view.port.set('COM5')
        self.view._update_controls()
        self.assertEqual(str(self.view.start_button['state']), 'normal')
        self.view.start()
        self.controller.start.assert_called_once()
        self.assertEqual(self.controller.start.call_args.kwargs['port'], 'COM5')

    def test_new_second_usb_revokes_automatic_choice_but_preserves_manual_choice(self):
        self.controller.list_ports.return_value = [dict(device='COM4', usb=True)]
        self.view.refresh_ports()
        self.assertEqual(self.view.port.get(), 'COM4')
        self.controller.list_ports.return_value.append(dict(device='COM5', usb=True))
        self.view.refresh_ports()
        self.assertEqual(self.view.port.get(), '')
        self.view.port.set('COM4')
        self.view._port_selected()
        self.view.refresh_ports()
        self.assertEqual(self.view.port.get(), 'COM4')

    def test_parallel_scans_are_bounded_and_recording_does_not_scan(self):
        operations = []
        self.view._task = lambda op, *args: operations.append(op)
        self.view.refresh_ports()
        self.view.refresh_ports()
        self.assertEqual(len(operations), 1)
        self.view._ports_scanning = False
        self.view._last_snapshot = dict(running=True)
        self.view.refresh_ports()
        self.assertEqual(len(operations), 1)

    def test_snapshot_shows_standby_not_receiving_and_failed_save_not_success(self):
        self.view._show_snapshot(dict(running=False, state='IDLE'), 5)
        self.assertIn('待机', self.view.source_badge['text'])
        self.assertIn('未开始', self.view.quality_label['text'])
        self.view._show_snapshot(dict(running=False, state='FAULT', received=10, saved=10, error='保存结束失败'), 5)
        self.assertNotIn('记录已保存', self.view.count_label['text'])

    def test_stopped_demo_remains_labelled_as_demo(self):
        self.view._show_snapshot(dict(running=False, state='IDLE', source='demo', received=10, saved=10), 5)
        self.assertIn('模拟', self.view.source_badge['text'])

    def test_shutdown_cancel_does_not_stop(self):
        with mock.patch('ui.main_window.messagebox.askyesno', return_value=False), \
                mock.patch('ui.main_window.poweroff_supported', return_value=True):
            self.view.shutdown()
        self.controller.stop.assert_not_called()
        self.assertFalse(self.view._closing)

    def test_shutdown_waits_for_saving_and_background_work(self):
        self.view._closing = self.view._shutdown_requested = True
        self.view._last_snapshot = dict(running=True, state='STOPPING', received=12, saved=10)
        with mock.patch('ui.main_window.request_poweroff') as power:
            self.view._advance_close()
            self.view._last_snapshot = dict(running=False, state='IDLE', received=12, saved=12)
            self.view._pending_tasks = 1
            self.view._advance_close()
            power.assert_not_called()
            self.view._pending_tasks = 0
            self.view._advance_close()
            power.assert_called_once()

    def test_shutdown_blocks_on_error_or_unsaved_samples(self):
        for state in (dict(error='保存失败'), dict(received=12, saved=10), dict(pending=2)):
            self.view._closing = self.view._shutdown_requested = True
            self.view._last_snapshot = dict(running=False, state='IDLE', **state)
            with mock.patch('ui.main_window.request_poweroff') as power, \
                    mock.patch('ui.main_window.messagebox.showerror'):
                self.view._advance_close()
                power.assert_not_called()
                self.assertFalse(self.view._closing)

    def test_shutdown_waits_for_cloud_and_recovers_from_permission_failure(self):
        cloud = self.view.cloud = mock.Mock()
        cloud.wait.return_value = False
        self.view._closing = self.view._shutdown_requested = True
        self.view._last_snapshot = dict(running=False, state='IDLE')
        with mock.patch('ui.main_window.request_poweroff') as power:
            self.view._advance_close()
            cloud.close.assert_called_once()
            power.assert_not_called()
        cloud.wait.return_value = True
        def fail_task(operation, success=None, failure=None):
            try:
                operation()
            except RuntimeError as exc:
                failure(str(exc))
        self.view._task = fail_task
        with mock.patch('ui.main_window.request_poweroff', side_effect=RuntimeError('not allowed')):
            self.view._advance_close()
        self.assertFalse(self.view._closing)
        self.assertTrue(self.view._cloud_resume_pending)
        self.assertFalse(self.view._closed)

    def test_second_shutdown_never_resumes_cloud_after_poweroff_is_queued(self):
        self.view.cloud = mock.Mock()
        self.view.cloud.wait.return_value = True
        self.view.cloud.snapshot.return_value = {'connection': 'online'}
        self.view.cloud_button = mock.Mock()
        self.view._closing = self.view._shutdown_requested = True
        self.view._cloud_resume_pending = self.view._cloud_stopping = True
        queued = []
        self.view._task = lambda operation, *args: queued.append(operation)
        with mock.patch('ui.main_window.request_poweroff') as power:
            self.view._tick()
            self.assertEqual(queued, [power])
            self.view.cloud.resume.assert_not_called()

    def test_close_blocks_new_background_commands(self):
        self.view._closing = True
        operation, failure = mock.Mock(), mock.Mock()
        MainWindow._task(self.view, operation, failure=failure)
        operation.assert_not_called()
        failure.assert_called_once()
        self.assertEqual(self.view._pending_tasks, 0)

    def test_exit_cancels_gui_timers_before_destroy(self):
        self.root.after(10000, lambda: None)
        self.view._closing = True
        self.view._last_snapshot = dict(running=False, state='IDLE')
        self.view._advance_close()
        self.assertFalse(self.root.tk.call('after', 'info'))

    def test_shutdown_waits_for_clock_and_storage_monitor(self):
        self.view._closing = self.view._shutdown_requested = True
        self.view._last_snapshot = dict(running=False, state='IDLE')
        self.controller.shutdown_environment.return_value = False
        with mock.patch('ui.main_window.request_poweroff') as power:
            self.view._advance_close()
            power.assert_not_called()
            self.controller.shutdown_environment.return_value = True
            self.view._advance_close()
            power.assert_called_once()

    def test_port_scan_failure_is_retryable_without_repeated_modal_errors(self):
        self.controller.list_ports.side_effect = OSError('enumeration failed')
        with mock.patch('ui.main_window.messagebox.showerror') as error:
            self.view.refresh_ports()
            self.assertFalse(self.view._ports_scanning)
            self.assertIn('检查失败', self.view._ports_message)
            error.assert_not_called()
        self.controller.list_ports.side_effect = None
        self.controller.list_ports.return_value = [dict(device='COM4', usb=True)]
        self.view.refresh_ports()
        self.assertTrue(self.view._port_available())


if __name__ == '__main__':
    unittest.main()
