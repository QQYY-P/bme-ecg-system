"""UI contracts use in-memory records; never touch real sessions or the clock."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest import mock

from ui.main_window import MainWindow, DEFAULT_UI_SETTINGS, AMBER


def descendants(widget):
    result = list(widget.winfo_children())
    for child in result:
        result.extend(child.winfo_children())
    return result


class StorageUiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.geometry("800x480+10000+10000")
        self.controller = mock.Mock()
        self.saved = dict(DEFAULT_UI_SETTINGS, export_dir="/tmp/ecg-exports")
        self.controller.get_settings.side_effect = lambda: dict(self.saved)
        def save(values):
            self.saved.update(values)
            return dict(self.saved)
        self.controller.update_settings.side_effect = save
        self.controller.storage_status.return_value = {"state": "ok", "free_bytes": 1024**3, "total_bytes": 8*1024**3}
        self.controller.clock_status.return_value = {"trusted": False, "source": "manual", "utc": "2026-10-29T06:00:00Z", "detail": "手动设置，未联网校验"}
        self.row = {"session_id": "test-session-001", "started_utc": "2026-10-29T06:00:00Z", "source": "serial", "status": "complete", "sample_count": 43200, "size_bytes": 2*1024**2, "time_trusted": False}
        self.controller.list_sessions.side_effect = lambda **_: [dict(self.row)]
        self.window = MainWindow(self.root, self.controller)
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.window._task = lambda operation, success=None, failure=None: success(operation()) if success else operation()
        self.window._show_snapshot({"running": False, "state": "IDLE"}, 5)
        self.root.update_idletasks()

    def tearDown(self):
        self.window._closed = True
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()

    def dialog(self):
        return next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))

    def button(self, text):
        return next(w for w in descendants(self.dialog()) if w.winfo_class() == "TButton" and w.cget("text") == text)

    def select_history(self):
        self.window.show_history()
        tree = next(w for w in descendants(self.dialog()) if isinstance(w, ttk.Treeview))
        tree.selection_set("0")
        tree.event_generate("<<TreeviewSelect>>")
        self.root.update()
        return tree

    def test_settings_four_tabs_fit_800_by_480_and_validate_storage(self):
        self.window.settings()
        dialog = self.dialog()
        notebook = next(w for w in descendants(dialog) if isinstance(w, ttk.Notebook))
        self.assertEqual([notebook.tab(tab, "text") for tab in notebook.tabs()], ["显示", "采集", "存储", "时间"])
        for tab in notebook.tabs():
            notebook.select(tab)
            self.root.update_idletasks()
            self.assertLessEqual(dialog.winfo_width(), 800)
            self.assertLessEqual(dialog.winfo_height(), 440)
            for widget in descendants(dialog):
                if widget.winfo_ismapped():
                    with self.subTest(tab=notebook.tab(tab, "text"), widget=str(widget)):
                        self.assertGreaterEqual(widget.winfo_x(), 0)
                        self.assertGreaterEqual(widget.winfo_y(), 0)
                        self.assertLessEqual(widget.winfo_x()+widget.winfo_width(), widget.master.winfo_width()+1)
                        self.assertLessEqual(widget.winfo_y()+widget.winfo_height(), widget.master.winfo_height()+1)
        entries = [w for w in descendants(dialog) if w.winfo_class() == "TEntry"]
        warning = next(w for w in entries if w.get() == "512")
        warning.delete(0, "end"); warning.insert(0, "100")
        with mock.patch("ui.main_window.messagebox.showerror") as error:
            self.button("应用设置").invoke()
            error.assert_called_once()
        self.controller.update_settings.assert_not_called()
        warning.delete(0, "end"); warning.insert(0, "768")
        self.button("应用设置").invoke()
        self.assertEqual(self.saved["low_space_warn_mb"], 768)
        self.assertFalse(dialog.winfo_exists())

    def test_history_delete_uses_selected_record_and_requires_confirmations(self):
        self.select_history()
        with mock.patch("ui.main_window.messagebox.askyesno", return_value=False) as confirm:
            self.button("移入回收站…").invoke()
            self.assertIn("test-session-001", confirm.call_args.args[1])
            self.assertIn("43,200", confirm.call_args.args[1])
        self.controller.trash_session.assert_not_called()
        with mock.patch("ui.main_window.messagebox.askyesno", return_value=True):
            self.button("移入回收站…").invoke()
        self.controller.trash_session.assert_called_once_with("test-session-001")
        radio = next(w for w in descendants(self.dialog()) if w.winfo_class() == "TRadiobutton" and w.cget("text") == "回收站")
        radio.invoke()
        tree = next(w for w in descendants(self.dialog()) if isinstance(w, ttk.Treeview))
        tree.selection_set("0"); tree.event_generate("<<TreeviewSelect>>"); self.root.update()
        with mock.patch("ui.main_window.messagebox.askyesno", side_effect=[True, False]) as confirm:
            self.button("永久删除…").invoke()
            self.assertEqual(confirm.call_count, 2)
        self.controller.purge_session.assert_not_called()
        self.button("恢复").invoke()
        self.controller.restore_session.assert_called_once_with("test-session-001")

    def test_recording_disables_export_delete_and_clock_operations(self):
        self.window._last_snapshot = {"running": True, "session_id": self.row["session_id"]}
        self.select_history()
        for label in ("完整 ZIP", "原始 CSV", "移入回收站…", "恢复"):
            self.assertEqual(str(self.button(label).cget("state")), "disabled")
        self.dialog().destroy()
        self.window.settings()
        for label in ("设置时间…", "立即联网校时"):
            self.assertEqual(str(self.button(label).cget("state")), "disabled")
        reference = next(w for w in descendants(self.dialog()) if w.winfo_class() == "TEntry")
        self.assertEqual(str(reference.cget("state")), "disabled")

    def test_zip_respects_raw_option_and_keeps_default_export_directory(self):
        self.window._settings.update(export_include_raw=False, last_export_dir="/tmp/recent")
        self.controller.export_bundle.return_value = "/tmp/chosen/ecg.zip"
        self.select_history()
        with mock.patch("ui.main_window.filedialog.asksaveasfilename", return_value="/tmp/chosen/ecg.zip") as choose:
            self.button("完整 ZIP").invoke()
            self.assertEqual(choose.call_args.kwargs["initialdir"], "/tmp/recent")
        self.controller.export_bundle.assert_called_once_with("test-session-001", "/tmp/chosen/ecg.zip", include_raw=False)
        self.assertEqual(self.window._settings["export_dir"], "/tmp/ecg-exports")
        self.assertTrue(str(self.window._settings["last_export_dir"]).replace("\\", "/").endswith("/tmp/chosen"))

    def test_manual_time_confirmation_contains_exact_date_and_cancel_does_nothing(self):
        self.window.settings()
        entry = next(w for w in descendants(self.dialog()) if w.winfo_class() == "TEntry" and len(w.get()) == 19)
        entry.delete(0, "end"); entry.insert(0, "2026-10-29 14:35:00")
        with mock.patch("ui.main_window.messagebox.askyesno", return_value=False) as confirm:
            self.button("设置时间…").invoke()
            self.assertIn("2026-10-29 14:35:00", confirm.call_args.args[1])
            self.assertIn("时区", confirm.call_args.args[1])
        self.controller.set_time.assert_not_called()

    def test_space_warning_is_visible_above_normal_signal_status(self):
        self.window._show_snapshot({"running": True, "state": "RECORDING", "source": "demo", "signal_state": "good", "signal_detail": "信号良好",
                                   "storage": {"state": "warning", "free_bytes": 400*1024**2, "detail": "空间偏低，请导出或清理历史记录"}}, 5)
        self.assertIn("空间偏低", self.window.status_label.cget("text"))
        self.assertEqual(str(self.window.status_label.cget("foreground")), AMBER)
        self.assertIn("400.0 MB", self.window.count_label.cget("text"))

    def test_missing_file_manager_reports_the_real_directory(self):
        with mock.patch("ui.main_window.Path.mkdir"), mock.patch("ui.main_window.subprocess.Popen", side_effect=FileNotFoundError("no file manager")):
            with self.assertRaisesRegex(RuntimeError, "ecg-exports"):
                self.window._open_export_directory()

    def test_file_manager_nonzero_exit_is_not_reported_as_success(self):
        process = mock.Mock()
        process.wait.return_value = 3
        with mock.patch("ui.main_window.Path.mkdir"), mock.patch("ui.main_window.subprocess.Popen", return_value=process) as launch:
            with self.assertRaisesRegex(RuntimeError, "ecg-exports"):
                self.window._open_export_directory()
            self.assertIsInstance(launch.call_args.args[0], list)
            self.assertNotIn("shell", launch.call_args.kwargs)

    def test_clock_password_cancel_does_not_authorize_or_retry(self):
        self.controller.sync_time.return_value = {"authorization_required": True, "success": False}
        self.window.settings()
        with mock.patch("ui.main_window.simpledialog.askstring", return_value=None) as prompt:
            self.button("立即联网校时").invoke()
            self.assertEqual(prompt.call_args.kwargs["show"], "*")
        self.controller.authorize_time.assert_not_called()
        self.controller.sync_time.assert_called_once_with()
        self.assertTrue(any("已取消校时授权" in str(w.cget("text")) for w in descendants(self.dialog()) if w.winfo_class() == "TLabel"))

    def test_clock_password_authorization_retries_once_without_persisting_secret(self):
        self.controller.sync_time.side_effect = [{"authorization_required": True}, {"trusted": True, "source": "ntp", "utc": "2026-10-29T06:00:00Z"}]
        self.controller.authorize_time.return_value = {"success": True}
        self.window.settings()
        with mock.patch("ui.main_window.simpledialog.askstring", return_value="temporary-test-only") as prompt:
            self.button("立即联网校时").invoke()
            prompt.assert_called_once()
        self.controller.authorize_time.assert_called_once_with("temporary-test-only")
        self.assertEqual(self.controller.sync_time.call_count, 2)
        self.assertNotIn("temporary-test-only", str(self.window._settings))
        self.controller.update_settings.assert_not_called()

    def test_clock_repeated_authorization_request_never_prompts_in_a_loop(self):
        self.controller.sync_time.return_value = {"authorization_required": True, "detail": "本次授权未生效"}
        self.controller.authorize_time.return_value = {"success": True}
        self.window.settings()
        with mock.patch("ui.main_window.simpledialog.askstring", return_value="temporary-test-only") as prompt:
            self.button("立即联网校时").invoke()
            prompt.assert_called_once()
        self.assertEqual(self.controller.sync_time.call_count, 2)
        self.assertTrue(any("本次授权未生效" in str(w.cget("text")) for w in descendants(self.dialog()) if w.winfo_class() == "TLabel"))

    def test_clock_recording_started_during_password_prompt_blocks_authorization(self):
        self.controller.sync_time.return_value = {"authorization_required": True}
        self.window.settings()
        def prompt(*_, **__):
            self.window._last_snapshot["running"] = True
            return "temporary-test-only"
        with mock.patch("ui.main_window.simpledialog.askstring", side_effect=prompt):
            self.button("立即联网校时").invoke()
        self.controller.authorize_time.assert_not_called()
        self.controller.sync_time.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
