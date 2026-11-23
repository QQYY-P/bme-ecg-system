import unittest
import tkinter as tk
from unittest import mock

import numpy as np

from ui.waveform import decimate_segments
from ui.main_window import AMBER, GREEN, MUTED, MainWindow, source_label


class SourceLabelTests(unittest.TestCase):
    def test_real_board_simulation_local_demo_and_replay_are_distinct(self):
        self.assertEqual(source_label("serial", 0), "STM32 · 实采")
        self.assertEqual(source_label("serial", 1), "STM32 · 板端模拟")
        self.assertEqual(source_label("serial", None), "STM32 · 等待模式")
        self.assertEqual(source_label("demo", 1), "合成模拟")
        self.assertEqual(source_label("replay", 0), "回放 · 实采")
        self.assertEqual(source_label("replay", 1), "回放 · 模拟")
        self.assertEqual(source_label("replay", None), "文件回放 · 来源未知")


class DecimationTests(unittest.TestCase):
    def test_narrow_positive_and_negative_peaks_survive(self):
        x = np.arange(3600) / 360
        y = np.zeros(3600)
        y[1001], y[1002], y[1003] = 25, -19, 7
        runs = decimate_segments(x, y, 200, 0, 10)
        self.assertEqual(len(runs), 1)
        self.assertIn(25, runs[0][:, 1])
        self.assertIn(-19, runs[0][:, 1])
        self.assertTrue(np.all(np.diff(runs[0][:, 0]) > 0))
        self.assertLessEqual(len(runs[0]), 402)
        self.assertEqual(runs[0][0, 0], x[0])
        self.assertEqual(runs[0][-1, 0], x[-1])

    def test_nan_breaks_trace(self):
        x = np.arange(100) / 360
        y = np.ones(100)
        y[40:45] = np.nan
        runs = decimate_segments(x, y, 10, 0, 1)
        self.assertEqual(len(runs), 2)
        self.assertLess(runs[0][-1, 0], x[40])
        self.assertGreater(runs[1][0, 0], x[44])

    def test_segment_change_breaks_even_with_continuous_time(self):
        x = np.arange(100) / 360
        runs = decimate_segments(x, np.ones(100), 20, 0, 1, np.r_[np.zeros(50), np.ones(50)])
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0][-1, 0], x[49])
        self.assertEqual(runs[1][0, 0], x[50])

    def test_missing_time_and_duplicate_time_are_not_joined(self):
        x = np.r_[np.arange(20) / 360, 0.2 + np.arange(20) / 360]
        runs = decimate_segments(x, np.ones(40), 10, 0, 1)
        self.assertEqual(len(runs), 2)
        x[20] = x[19]
        self.assertGreaterEqual(len(decimate_segments(x, np.ones(40), 10, 0, 1)), 2)

    def test_left_clipped_window_has_first_visible_run(self):
        x = np.arange(3600) / 360
        runs = decimate_segments(x, np.sin(x), 100, 5, 10)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][0, 0], 5)
        self.assertTrue(np.all(runs[0][:, 0] >= 5))

    def test_empty_invalid_and_single_point(self):
        self.assertEqual(decimate_segments([], [], 100, 0, 1), [])
        self.assertEqual(decimate_segments([0, 1], [np.nan, np.nan], 100, 0, 1), [])
        self.assertEqual(decimate_segments([0], [3], 0, 0, 1)[0].tolist(), [[0, 3]])

    def test_does_not_mutate_recorded_inputs(self):
        x = np.arange(3600) / 360
        y = np.sin(x)
        before_x, before_y = x.copy(), y.copy()
        decimate_segments(x, y, 100, 0, 10)
        np.testing.assert_array_equal(x, before_x)
        np.testing.assert_array_equal(y, before_y)

    def test_malformed_snapshots_fail_explicitly(self):
        with self.assertRaises(ValueError):
            decimate_segments([0, 1], [1], 100, 0, 2)
        with self.assertRaises(ValueError):
            decimate_segments([0], [1], 100, 1, 1)
        with self.assertRaises(ValueError):
            decimate_segments([0, 1], [1, 2], 100, 0, 2, [0])


class AxisLayoutTests(unittest.TestCase):
    """Actual Tk text bboxes, including the chart's smaller size inside the UI."""

    def test_settings_rejects_reference_voltage_below_backend_minimum(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is unavailable: {exc}")
        root.withdraw()

        class FakeController:
            def list_ports(self):
                return []

        window = MainWindow(root, FakeController())
        try:
            window.settings()
            dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
            widgets = list(dialog.winfo_children())
            for widget in widgets:
                widgets.extend(widget.winfo_children())
            entry = next(w for w in widgets if w.winfo_class() == "TEntry")
            apply = next(w for w in widgets if w.winfo_class() == "TButton" and w.cget("text") == "应用设置")
            entry.delete(0, "end")
            entry.insert(0, "0.05")
            with mock.patch("ui.main_window.messagebox.showerror") as error:
                apply.invoke()
                error.assert_called_once()
                self.assertIn("0.1", error.call_args.args[1])
            self.assertEqual(window.vref, 3.3)
            self.assertTrue(dialog.winfo_exists())
            entry.delete(0, "end")
            entry.insert(0, "0.1")
            apply.invoke()
            self.assertEqual(window.vref, .1)
            self.assertFalse(dialog.winfo_exists())
        finally:
            window._closed = True
            for callback in root.tk.call("after", "info"):
                root.after_cancel(callback)
            root.destroy()

    def test_mode_switch_clears_paused_old_wave_and_updates_badge(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is unavailable: {exc}")
        root.withdraw()

        class FakeController:
            def list_ports(self):
                return []

        window = MainWindow(root, FakeController())
        try:
            wave = {"x": [0., .1], "raw": [2048., 2200.], "filtered": [2048., 2190.]}
            snapshot = {"session_id": "mode-ui", "source": "serial", "state": "RECORDING",
                        "running": True, "board_mode": 0, "analysis_generation": 0,
                        "serial_aligned": True, "wave": wave, "preprocessing_active": True,
                        "preprocessing_warmup": True}
            window._show_snapshot(snapshot, 10)
            self.assertEqual(window.preprocess_label.cget("text"), "实采预处理")
            window.toggle_pause()
            self.assertIsNotNone(window._frozen_wave)
            snapshot.update(board_mode=1, analysis_generation=1, preprocessing_active=False,
                            preprocessing_warmup=False,
                            wave={"x": [], "raw": [], "filtered": []})
            window._show_snapshot(snapshot, 10)
            self.assertIsNone(window._frozen_wave)
            self.assertEqual(window.source_badge.cget("text"), "STM32 · 板端模拟")
            self.assertEqual(window.preprocess_label.cget("text"), "")
            self.assertNotIn("无法", window.status_label.cget("text"))
            snapshot.update(board_mode=None, analysis_generation=2, state="WAITING_SYNC",
                            serial_aligned=False, status="串口数据中断")
            window._show_snapshot(snapshot, 10)
            self.assertEqual(window.source_badge.cget("text"), "STM32 · 等待模式")
        finally:
            window._closed = True
            for callback in root.tk.call("after", "info"):
                root.after_cancel(callback)
            root.destroy()

    def test_axis_text_stays_inside_both_window_sizes(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is unavailable: {exc}")
        root.withdraw()
        x = 192.7 + np.arange(3600) / 360
        raw = 2048 + 700 * np.sin(x * 6)
        wave = {"x": x, "raw": raw, "filtered": raw - 30}

        class FakeController:
            def snapshot(self, window_s=10):
                return {"state": "RECORDING", "running": True, "source": "demo",
                        "session_id": "axis-test", "received": 72972, "saved": 72972,
                        "elapsed_s": 202.7, "hr": 72, "rr_ms": 833, "wave": wave}

            def list_ports(self):
                return []

            def stop(self):
                pass

        window = MainWindow(root, FakeController())
        try:
            for width, height in ((800, 480), (1000, 620)):
                with self.subTest(window_size=(width, height)):
                    root.geometry(f"{width}x{height}+10000+10000")
                    root.deiconify()
                    root.update_idletasks()
                    root.update()
                    display = FakeController().snapshot()
                    display.update(source="serial", board_mode=0, preprocessing_active=True,
                                   preprocessing_warmup=True, serial_aligned=True)
                    window._show_snapshot(display, 10)
                    root.update_idletasks()
                    for widget in (window.title_label, window.source_badge, window.preprocess_label):
                        self.assertGreaterEqual(widget.winfo_x(), 0)
                        self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), window.header.winfo_width())
                    # Compact layouts deliberately hide the preprocessing label.
                    # Only mapped widgets have meaningful layout coordinates.
                    if window.preprocess_label.winfo_ismapped():
                        self.assertLessEqual(window.source_badge.winfo_x() + window.source_badge.winfo_width(),
                                             window.preprocess_label.winfo_x())
                    canvas = window.waveform
                    for mode in ("raw", "filtered", "both"):
                        canvas.render(wave, mode=mode)
                        root.update_idletasks()
                        for item in canvas.find_all():
                            if canvas.type(item) != "text" or canvas.itemcget(item, "state") == "hidden":
                                continue
                            bounds = canvas.bbox(item)
                            with self.subTest(mode=mode, text=canvas.itemcget(item, "text")):
                                self.assertIsNotNone(bounds)
                                self.assertGreaterEqual(bounds[0], 0)
                                self.assertGreaterEqual(bounds[1], 0)
                                self.assertLessEqual(bounds[2], canvas.winfo_width())
                                self.assertLessEqual(bounds[3], canvas.winfo_height())
                        title_top = canvas.bbox(canvas._time_title)[1]
                        for item in canvas._ticks[:6]:
                            self.assertLess(canvas.bbox(item)[3], title_top)
        finally:
            window._closed = True
            for callback in root.tk.call("after", "info"):
                root.after_cancel(callback)
            root.destroy()


class StatusDisplayTests(unittest.TestCase):
    """Real Tk widgets exercise the status contract without touching a device."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is unavailable: {exc}")
        self.root.withdraw()
        self.root.geometry("800x480+10000+10000")
        self.snapshot = {
            "session_id": "status-ui", "source": "serial", "state": "RECORDING",
            "running": True, "board_mode": 0, "analysis_generation": 0,
            "serial_aligned": True, "port": "/dev/ttyUSB0",
            "connection_state": "receiving", "connection_text": "串口接收正常",
            "signal_state": "invalid", "signal_text": "无效数据",
            "signal_detail": "收到无效标记，具体原因未提供", "hr_allowed": False,
            "hr": 72, "rr_ms": 833, "received": 360, "saved": 360, "serial_bytes": 2520,
            "mode_check_state": "idle", "mode_feedback": "已确认实采",
            "wave": {"x": [0., .1], "raw": [2048., 2200.], "filtered": [2048., 2190.]},
        }
        self.controller = mock.Mock()
        self.controller.snapshot.side_effect = lambda **_: dict(self.snapshot)
        self.controller.list_ports.return_value = []
        self.window = MainWindow(self.root, self.controller)
        # These tests deliver snapshots and callbacks explicitly, avoiding
        # timing-dependent background refreshes while inspecting the widgets.
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.deiconify()
        self.root.update_idletasks()
        self.show()

    def tearDown(self):
        self.window._closed = True
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()

    def show(self, **changes):
        self.snapshot.update(changes)
        self.window._show_snapshot(dict(self.snapshot), 10)
        self.root.update_idletasks()

    def test_receiving_invalid_data_is_not_a_connection_failure(self):
        self.assertEqual(self.window.connection_label.cget("text"), "串口接收正常")
        self.assertEqual(str(self.window.connection_label.cget("foreground")), GREEN)
        self.assertIn("无效数据", self.window.quality_label.cget("text"))
        self.assertEqual(self.window.quality_label.cget("foreground"), AMBER)
        self.assertIn("无效标记", self.window.status_label.cget("text"))
        self.assertNotIn("导联脱落", self.window.status_label.cget("text"))
        self.assertEqual(self.window.hr_label.cget("text"), "--")
        self.assertEqual(self.window.rr_label.cget("text"), "--")
        self.assertEqual(self.window.source_badge.cget("text"), "STM32 · 实采")

    def test_hr_is_hidden_until_quality_allows_it_and_after_stream_stops(self):
        self.show(signal_state="good", signal_text="信号良好", signal_detail="实采 · 信号良好", hr_allowed=True)
        self.assertEqual(self.window.hr_label.cget("text"), "72")
        self.assertEqual(self.window.rr_label.cget("text"), "833")
        self.assertEqual(self.window.quality_label.cget("foreground"), GREEN)
        for state, text in (("flat", "幅度过低"), ("noisy", "噪声较多"), ("saturated", "信号饱和"),
                            ("settling", "滤波稳定中"), ("invalid", "无效数据")):
            with self.subTest(signal_state=state):
                self.show(signal_state=state, signal_text=text, hr_allowed=False)
                self.assertEqual(self.window.hr_label.cget("text"), "--")
                self.assertEqual(self.window.rr_label.cget("text"), "--")
                self.assertEqual(self.window.quality_label.cget("foreground"), AMBER)
        self.snapshot.pop("hr_allowed")
        self.show(signal_state="good", signal_text="信号良好")
        self.assertEqual(self.window.hr_label.cget("text"), "--")
        self.show(hr_allowed=True, running=False, state="STOPPED")
        self.assertEqual(self.window.hr_label.cget("text"), "--")
        self.assertEqual(self.window.rr_label.cget("text"), "--")
        self.assertEqual(self.window.quality_label.cget("foreground"), MUTED)
        self.assertNotIn("良好", self.window.quality_label.cget("text"))
        self.show(hr_allowed=True, running=True, state="WAITING_SYNC", serial_aligned=False,
                  connection_state="interrupted", connection_text="串口数据中断")
        self.assertEqual(self.window.hr_label.cget("text"), "--")
        self.assertEqual(self.window.rr_label.cget("text"), "--")
        self.assertNotEqual(self.window.quality_label.cget("foreground"), GREEN)
        self.assertNotIn("良好", self.window.quality_label.cget("text"))

    def test_physical_mode_check_waits_for_confirmed_mode_and_can_timeout(self):
        self.assertNotEqual(str(self.window.mode_feedback_label.cget("foreground")), GREEN)
        self.assertFalse(self.window.mode_check_button.instate(["disabled"]))
        tasks = []
        with mock.patch.object(self.window, "_task", side_effect=lambda *args: tasks.append(args)):
            self.window.mode_check_button.invoke()
            self.window.check_mode_switch()
        self.assertEqual(len(tasks), 1)
        self.assertTrue(self.window.mode_check_button.instate(["disabled"]))
        operation, success, _failure = tasks[0]
        operation()
        self.controller.begin_mode_check.assert_called_once_with(timeout_s=10)
        success(1)
        self.show(mode_check_state="waiting", mode_expected=1, mode_check_remaining_s=9.2,
                  mode_feedback="请按 STM32 的 SW2，等待板端模拟确认")
        self.assertIn("SW2", self.window.mode_feedback_label.cget("text"))
        self.assertIn("10 秒", self.window.mode_feedback_label.cget("text"))
        self.assertEqual(self.window.source_badge.cget("text"), "STM32 · 实采")
        self.assertTrue(self.window.mode_check_button.instate(["disabled"]))
        self.show(mode_check_state="timeout", mode_feedback="未收到模式变化，仍为实采；请检查 SW2 与接线")
        self.assertEqual(self.window.source_badge.cget("text"), "STM32 · 实采")
        self.assertEqual(str(self.window.mode_feedback_label.cget("foreground")), AMBER)
        self.assertFalse(self.window.mode_check_button.instate(["disabled"]))
        self.show(board_mode=1, analysis_generation=1, mode_check_state="confirmed",
                  mode_feedback="切换已确认：实采 → 板端模拟")
        self.assertEqual(self.window.source_badge.cget("text"), "STM32 · 板端模拟")
        self.assertEqual(str(self.window.mode_feedback_label.cget("foreground")), GREEN)

    def test_mode_check_is_unavailable_without_an_active_identified_serial_stream(self):
        for changes in ({"board_mode": None}, {"serial_aligned": False}, {"state": "WAITING_SYNC"},
                        {"running": False}, {"source": "demo"}, {"source": "replay"}):
            with self.subTest(changes=changes):
                self.show(source="serial", running=True, state="RECORDING", serial_aligned=True, board_mode=0)
                self.show(**changes)
                self.assertTrue(self.window.mode_check_button.instate(["disabled"]))
                self.window.check_mode_switch()
                self.controller.begin_mode_check.assert_not_called()
                if changes.get("source") in ("demo", "replay"):
                    self.assertFalse(self.window.mode_feedback_row.winfo_ismapped())

    def test_footer_status_and_switch_action_fit_small_and_large_displays(self):
        detail = "实采 · 数据异常，请检查连接；记录保存在 /home/pi2/very-long-session-directory/" * 5
        for width, height in ((800, 480), (1000, 620)):
            with self.subTest(window_size=(width, height)):
                self.root.geometry(f"{width}x{height}+10000+10000")
                self.root.update_idletasks()
                self.show(signal_detail=detail, received=123456, saved=123456, serial_bytes=864192,
                          mode_check_state="waiting", mode_check_remaining_s=10,
                          mode_feedback="请按 STM32 的 SW2，等待板端模拟确认")
                # A second frame reflects the requested widths after the
                # serial byte counters and labels have changed.
                self.show()
                for widget in (self.window.connection_label, self.window.status_label,
                               self.window.count_label, self.window.mode_feedback_label,
                               self.window.mode_check_button):
                    with self.subTest(widget=widget.cget("text")):
                        parent = widget.master
                        self.assertTrue(widget.winfo_ismapped())
                        self.assertGreaterEqual(widget.winfo_x(), 0)
                        self.assertGreaterEqual(widget.winfo_y(), 0)
                        self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), parent.winfo_width())
                        self.assertLessEqual(widget.winfo_y() + widget.winfo_height(), parent.winfo_height())
                for label in (self.window.status_label, self.window.mode_feedback_label):
                    self.assertLessEqual(self.window._status_font.measure(label.cget("text")), label.winfo_width() - 2)
                self.assertIn("SW2", self.window.mode_feedback_label.cget("text"))
                self.assertIn("10 秒", self.window.mode_feedback_label.cget("text"))
                self.assertTrue(self.window.status_label.cget("text").endswith("…"))
                self.assertGreater(self.window.waveform.winfo_height(), 180)
                self.assertLess(self.window.waveform.winfo_rooty() + self.window.waveform.winfo_height(),
                                self.window.footer.winfo_rooty())


if __name__ == "__main__":
    unittest.main()
