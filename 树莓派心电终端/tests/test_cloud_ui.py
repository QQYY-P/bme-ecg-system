import gc
from pathlib import Path
from unittest import mock
import tkinter as tk
import unittest
import threading
import time
import sys

from tools.preview_classification_ui import PreviewController, descendants
from ui.main_window import MainWindow
from ui.cloud_dialog import show_cloud


class CloudUiTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'Pi/Linux scheduling')
    def test_short_thread_slice_is_restored_when_window_closes(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        original = sys.getswitchinterval()
        view = None
        try:
            view = MainWindow(root, PreviewController())
            self.assertLessEqual(sys.getswitchinterval(), .001)
            child = tk.Label(root)
            child.destroy()
            self.assertLessEqual(sys.getswitchinterval(), .001)
        finally:
            if view:
                view._closed = True
            root.destroy()
        self.assertAlmostEqual(sys.getswitchinterval(), original)

    def tearDown(self):
        # Collect Tk callback cycles on the UI thread after test locals expire,
        # before a subsequent recorder/upload worker can trigger Python GC.
        gc.collect()

    def test_upload_callback_keeps_tk_responsive_during_slow_command(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.geometry('800x480+10000+10000')
        controller = PreviewController()
        controller.data_dir = Path('unused-cloud-ui-test')
        controller.list_sessions = lambda: [{'session_id': 'slow-upload', 'sample_count': 360}]
        service = mock.Mock()
        state = dict(connection='online', phase='idle', session_id=None, message='ready', result=None)
        service.snapshot.side_effect = lambda: state
        calls, ticks = [], []
        def upload(sid):
            calls.append((sid, threading.get_ident()))
            time.sleep(.6)
        service.upload.side_effect = upload
        try:
            with mock.patch('cloud.service.CloudService', return_value=service):
                view = MainWindow(root, controller)
            show_cloud(view)
            def press():
                buttons = [w for w in descendants(view._cloud_dialog)
                           if w.winfo_class() == 'TButton' and w.cget('text') == '上传并分析']
                buttons[0].invoke()
                buttons[0].invoke()  # duplicate click is disabled
            def heartbeat():
                ticks.append(time.monotonic())
                root.after(20, heartbeat)
            root.after(200, press)
            root.after(20, heartbeat)
            root.after(1100, root.quit)
            root.mainloop()
            self.assertEqual(len(calls), 1)
            self.assertNotEqual(calls[0][1], threading.get_ident())
            self.assertLess(max(b-a for a,b in zip(ticks, ticks[1:])), .4)
        finally:
            view._closed = True
            root.destroy()
            gc.collect()

    def test_cloud_dialog_binds_result_to_record_and_fits_small_screen(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.geometry('800x480+10000+10000')
        controller = PreviewController()
        controller.data_dir = Path('unused-cloud-ui-test')
        controller.list_sessions = lambda: [{'session_id': 'public-test', 'sample_count': 21600}]
        state = {'connection': 'online', 'message': '云端分析完成', 'session_id': 'public-test',
                 'phase': 'complete', 'progress': [61, 61],
                 'result': {'model_name': 'Test', 'model_version': '1',
                            'model_result': {'counts': {'N': 65, 'S': 1, 'V': 19},
                                             'beat_count': 85, 'eligible_samples': 21600}}}
        service = mock.Mock()
        service.snapshot.side_effect = lambda: state
        try:
            with mock.patch('cloud.service.CloudService', return_value=service):
                view = MainWindow(root, controller)
            view._task = lambda operation, success=None, failure=None: success(operation()) if success else operation()
            show_cloud(view)
            view._tick()
            root.update_idletasks()
            self.assertLessEqual(view.cloud_button.winfo_rootx()+view.cloud_button.winfo_width(), root.winfo_rootx()+root.winfo_width())
            dialog = view._cloud_dialog
            labels = [w for w in descendants(dialog) if w.winfo_class() == 'TLabel']
            text = '\n'.join(str(w.cget('text')) for w in labels)
            self.assertIn('public-test', text)
            self.assertIn('85', text)
            self.assertIn('N 类不代表完全健康', text)
            for widget in descendants(dialog):
                if widget.winfo_class() in ('TButton', 'TCombobox'):
                    self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), dialog.winfo_rootx()+dialog.winfo_width()+1)
                    self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), dialog.winfo_rooty()+dialog.winfo_height()+1)
        finally:
            view._closed = True
            root.destroy()
            gc.collect()
