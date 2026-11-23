"""Classification presentation tests use synthetic records and no real device."""
from copy import deepcopy
import gc
import tkinter as tk
from tkinter import ttk
import unittest
from unittest import mock
import numpy as np

from ui.main_window import MainWindow, classification_display, classification_events
from ui.waveform import WaveformCanvas
from tools.preview_classification_ui import PreviewController, descendants


class ClassificationPresentationLogicTests(unittest.TestCase):
    def test_progress_only_uses_real_collecting_context(self):
        snapshot = {"classification": {"state": "collecting", "context_seconds": 4.2, "context_required_seconds": 8, "detail": "等待"}}
        self.assertIn("4.2/8s", classification_display(snapshot)[1])
        snapshot["classification"]["state"] = "loading"
        self.assertEqual(classification_display(snapshot)[1], "等待")

    def test_foreign_incomplete_and_corrupt_markers_are_excluded(self):
        source = PreviewController().snapshot()
        valid = dict(source["classification"]["recent"][0])
        source["classification"]["recent"] = [valid, dict(valid, session_id="foreign"), dict(valid, r_sample=None),
                                                dict(valid, segment_id=-1), dict(valid, integrity_error="corrupt"), dict(valid, r_sample=True)]
        self.assertEqual(classification_events(source), [valid])


class ClassificationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(str(exc))
        cls.root.geometry("800x480+10000+10000")
        cls.root.tk.call("tk", "scaling", 96/72)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()
        cls.root = None
        # Tk callbacks own Python closures until root.destroy(). Collect those
        # cycles here, before a later acquisition worker triggers cyclic GC.
        gc.collect()

    def setUp(self):
        self.fake = PreviewController()
        self.controller = mock.Mock(wraps=self.fake)
        self.window = MainWindow(self.root, self.controller)
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.window._task = lambda operation, success=None, failure=None: success(operation()) if success else operation()
        self.window._show_snapshot(self.fake.snapshot(), 5)
        self.root.geometry("800x480+10000+10000")
        self.root.deiconify()
        self.root.update()

    def tearDown(self):
        self.window._closed = True
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        for child in list(self.root.winfo_children()):
            child.destroy()
        self.window = None
        self.controller = None
        self.fake = None
        self.root = None
        gc.collect()

    def details(self):
        self.window.show_classification_details()
        self.root.update_idletasks()
        return self.window._classification_dialog

    def button(self, dialog, text):
        return next(w for w in descendants(dialog) if w.winfo_class() == "TButton" and w.cget("text") == text)

    def test_pause_freezes_card_strip_and_markers_while_connection_stays_live(self):
        original = self.fake.snapshot()
        self.window.toggle_pause()
        self.fake.rows[-1].update(label="N", scores={"N": .8, "S": .1, "V": .1})
        new = self.fake.snapshot()
        new.update(connection_text="连接状态仍实时", connection_state="receiving")
        with mock.patch.object(self.window.waveform, "render") as render:
            self.window._show_snapshot(new, 5)
            self.assertIs(render.call_args.args[0], self.window._frozen_wave)
            self.assertEqual(render.call_args.kwargs["markers"][-1]["label"], "V")
        self.assertEqual(self.window.class_label.cget("text"), "V")
        self.assertEqual(self.window._strip_events[-1]["label"], "V")
        self.assertIn("暂停回看", self.window.status_label.cget("text"))
        self.assertIn("实时", self.window.connection_label.cget("text"))
        self.assertEqual(self.window._frozen_classification["classification"]["last"], original["classification"]["last"])
        self.window.toggle_pause()
        self.window._show_snapshot(new, 5)
        self.assertEqual(self.window.class_label.cget("text"), "N")

    def test_context_change_clears_frozen_classification_and_foreign_session_events(self):
        self.window.toggle_pause()
        changed = self.fake.snapshot()
        changed.update(session_id="new-session", analysis_generation=2)
        self.window._show_snapshot(changed, 5)
        self.assertIsNone(self.window._frozen_wave)
        self.assertIsNone(self.window._frozen_classification)
        self.assertEqual(self.window._strip_events, [])

    def test_details_show_classified_denominator_and_unknown_rejected_count(self):
        data = self.fake.classification_details()
        data.update(rejected=None)
        data["rows"] += [dict(data["rows"][0], r_sample=None), dict(data["rows"][0], integrity_error="bad")]
        self.controller.classification_details.return_value = data
        dialog = self.details()
        text = "\n".join(str(w.cget("text")) for w in descendants(dialog) if w.winfo_class() == "TLabel")
        self.assertIn("60.0%", text)
        self.assertIn("占比仅以 20 个已分类心搏", text)
        self.assertIn("已拒绝候选 未知", text)
        self.assertIn("不可判定期间的心搏数未知", text)
        self.assertIn("已排除 2 条", text)
        self.assertIn("N 不等于完全健康", text)

    def test_selected_sv_record_reviews_its_saved_sample_and_fixed_window(self):
        dialog = self.details()
        tree = next(w for w in descendants(dialog) if isinstance(w, ttk.Treeview))
        self.assertEqual(len(tree.get_children()), 8)
        tree.selection_set("0"); tree.event_generate("<<TreeviewSelect>>"); self.root.update()
        self.button(dialog, "回看选中心搏").invoke()
        self.controller.review_beat.assert_called_once_with(self.fake.session_id, self.fake.rows[-1]["r_sample"], 1, before_s=2, after_s=2)
        text = "\n".join(str(w.cget("text")) for w in descendants(dialog) if w.winfo_class() == "TLabel")
        self.assertIn("采样等待", text)
        self.assertIn("非诊断概率", text)
        self.assertIn("不含全部排队与推理耗时", text)

    def test_stopped_session_details_still_available(self):
        self.fake.running = False
        self.window._show_snapshot(self.fake.snapshot(), 5)
        self.assertEqual(self.window.class_label.cget("text"), "--")
        self.assertEqual(str(self.window.class_details_button.cget("state")), "normal")
        self.window.class_details_button.invoke()
        self.controller.classification_details.assert_called_once_with(session_id=self.fake.session_id)

    def test_old_record_without_filtered_values_falls_back_to_raw(self):
        target = self.fake.rows[-2]
        result = self.fake.review_beat(self.fake.session_id, target["r_sample"], target["segment_id"])
        result["wave"]["filtered"] = np.full(len(result["wave"]["x"]), np.nan)
        self.controller.review_beat.return_value = result
        with mock.patch.object(WaveformCanvas, "render") as render:
            self.window.show_classification_details(target)
            last = render.call_args
            self.assertEqual(last.kwargs["mode"], "raw")
            self.assertEqual(last.kwargs["bounds"], (result["begin_s"], result["end_s"]))
        dialog = self.window._classification_dialog
        labels = "\n".join(str(w.cget("text")) for w in descendants(dialog) if w.winfo_class() == "TLabel")
        self.assertIn("无可用处理波形，已显示原始数据", labels)

    def test_history_opens_selected_session_classification(self):
        self.controller.list_sessions = mock.Mock(return_value=[{"session_id": "older-session", "started_utc": "2026-10-28T06:00:00Z", "source": "serial", "sample_count": 1000, "status": "complete"}])
        data = self.fake.classification_details()
        data.update(session_id="older-session", rows=[])
        self.controller.classification_details.return_value = data
        self.window.show_history()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        tree = next(w for w in descendants(dialog) if isinstance(w, ttk.Treeview))
        tree.selection_set("0"); tree.event_generate("<<TreeviewSelect>>"); self.root.update()
        self.button(dialog, "分类详情").invoke()
        self.controller.classification_details.assert_called_once_with(session_id="older-session")

    def test_800x480_layout_keeps_recent_twenty_letters_and_details_inside(self):
        self.assertGreaterEqual(self.window.beat_strip.winfo_width()/20, 9)
        self.assertEqual(len(self.window._strip_events), 20)
        dialog = self.details()
        tabs = next(w for w in descendants(dialog) if isinstance(w, ttk.Notebook))
        for tab in tabs.tabs():
            tabs.select(tab)
            self.root.update_idletasks()
            for widget in descendants(dialog):
                if widget.winfo_ismapped():
                    with self.subTest(widget=str(widget)):
                        self.assertLessEqual(widget.winfo_x()+widget.winfo_width(), widget.master.winfo_width()+1)
                        self.assertLessEqual(widget.winfo_y()+widget.winfo_height(), widget.master.winfo_height()+1)


if __name__ == "__main__":
    unittest.main()
