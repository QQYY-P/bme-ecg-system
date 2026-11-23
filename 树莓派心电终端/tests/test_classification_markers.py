"""Delayed classifications attach to exact recorded samples, with bounded UI work."""
import gc
import tkinter as tk
import unittest

import numpy as np

from ui.waveform import (MAX_CLASSIFICATION_MARKERS, WaveformCanvas,
                         classification_marker_positions)


def event(sample, segment=0, label="N", session="test-session"):
    return {"r_sample": sample, "segment_id": segment, "session_id": session,
            "label": label, "scores": {"N": .8, "S": .1, "V": .1}}


class MarkerPositionTests(unittest.TestCase):
    def test_delayed_result_uses_peak_time_not_latest_point(self):
        x = np.arange(3600) / 360
        y = np.arange(3600, dtype=float)
        result = classification_marker_positions(x, y, np.zeros(3600), [event(720)], 0, 10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["x"], 2.0)
        self.assertEqual(result[0]["y"], 720.0)
        self.assertEqual(result[0]["sample_index"], 720)

    def test_same_time_wrong_segment_is_rejected(self):
        result = classification_marker_positions([1.0], [2048], [4], [event(360, 3)], 0, 2)
        self.assertEqual(result, [])

    def test_missing_sample_does_not_snap_or_interpolate(self):
        result = classification_marker_positions(np.array([359, 361]) / 360, [1, 2],
                                                 [0, 0], [event(360)], 0, 2)
        self.assertEqual(result, [])

    def test_outside_empty_and_nan_are_omitted(self):
        cases = (([1], [2], [0], [event(360)], 2, 3),
                 ([], [], [], [event(360)], 0, 2),
                 ([1], [np.nan], [0], [event(360)], 0, 2),
                 ([np.nan], [2], [0], [event(360)], 0, 2),
                 ([1], [2], None, [event(360)], 0, 2))
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(classification_marker_positions(*args), [])

    def test_exact_window_boundaries_are_included(self):
        result = classification_marker_positions([2, 5], [1, 2], [0, 0],
                                                 [event(720), event(1800)], 2, 5)
        self.assertEqual([item["x"] for item in result], [2, 5])

    def test_ambiguous_duplicate_sample_is_not_drawn(self):
        self.assertEqual(classification_marker_positions([1, 1], [2, 3], [0, 0],
                                                          [event(360)], 0, 2), [])

    def test_invalid_events_and_duplicate_events_do_not_add_markers(self):
        malformed = [event(np.nan), event(True), event(360.5), event(360, label="unknown"),
                     event(360, segment=np.nan), {}, None]
        self.assertEqual(classification_marker_positions([1], [2], [0], malformed, 0, 2), [])
        result = classification_marker_positions([1], [2], [0],
                                                 [event(360, label="N"), event(360, label="S")], 0, 2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["event"]["label"], "S")

    def test_event_limit_is_bounded_and_inputs_unchanged(self):
        x = np.arange(1000) / 360
        y = np.ones(1000)
        markers = [event(index) for index in range(1000)]
        original = x.copy()
        result = classification_marker_positions(x, y, np.zeros(1000), markers, 0, 3)
        self.assertEqual(len(result), MAX_CLASSIFICATION_MARKERS)
        self.assertEqual(result[0]["event"]["r_sample"], 900)
        np.testing.assert_array_equal(x, original)
        self.assertEqual(len(markers), 1000)


class MarkerCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display is unavailable: {exc}")
        cls.root.geometry("800x400+10000+10000")

    @classmethod
    def tearDownClass(cls):
        root, cls.root = cls.root, None
        try:
            root.destroy()
        finally:
            root = None
            # Tcl-backed objects must be finalized here on the test/main
            # thread, not during a later acquisition worker's GC cycle.
            gc.collect()

    def setUp(self):
        self.canvas = WaveformCanvas(self.root)
        self.canvas.pack(fill="both", expand=True)
        self.root.update_idletasks()

    def tearDown(self):
        canvas, self.canvas = self.canvas, None
        try:
            canvas.on_marker_click = None
            canvas.destroy()
        finally:
            canvas = None
            gc.collect()

    def wave(self, last=3600):
        x = np.arange(last + 1) / 360
        raw = 2048 + 300 * np.sin(x * 5)
        return {"x": x, "raw": raw, "filtered": raw,
                "segment_ids": np.zeros(len(x), dtype=int)}

    def test_marker_x_is_correct_for_two_five_and_ten_second_windows(self):
        wave = self.wave()
        for seconds in (2, 5, 10):
            with self.subTest(seconds=seconds):
                sample = int((10 - seconds / 2) * 360)
                self.canvas.render(wave, window_s=seconds, markers=[event(sample)])
                left = self.canvas.coords(self.canvas._grid[0])[0]
                right = self.canvas.coords(self.canvas._grid[5])[0]
                dot = self.canvas.coords(self.canvas._marker_pool[0]["dot"])
                self.assertAlmostEqual((dot[0] + dot[2]) / 2, (left + right) / 2)

    def test_history_bounds_control_axis_and_marker_position(self):
        self.canvas.render(self.wave(), window_s=10, bounds=(2, 4), markers=[event(900)])
        left = self.canvas.coords(self.canvas._grid[0])[0]
        right = self.canvas.coords(self.canvas._grid[5])[0]
        dot = self.canvas.coords(self.canvas._marker_pool[0]["dot"])
        self.assertAlmostEqual((dot[0] + dot[2]) / 2, left + .25 * (right - left))
        self.assertEqual(self.canvas.itemcget(self.canvas._ticks[0], "text"), "2.0")
        self.assertEqual(self.canvas.itemcget(self.canvas._ticks[5], "text"), "4.0")

    def test_marker_pool_reuses_items_and_hides_removed_events(self):
        wave = self.wave()
        for shift in range(5):
            markers = [event(index + shift) for index in range(200)]
            self.canvas.render(wave, markers=markers)
            self.assertEqual(len(self.canvas._marker_pool), MAX_CLASSIFICATION_MARKERS)
            self.assertEqual(len(self.canvas.find_withtag("classification_marker")), 3 * MAX_CLASSIFICATION_MARKERS)
        self.canvas.render(wave, markers=[])
        self.assertEqual(self.canvas._marker_data, [])
        for item in self.canvas.find_withtag("classification_marker"):
            self.assertEqual(self.canvas.itemcget(item, "state"), "hidden")

    def test_dense_labels_are_suppressed_but_dots_remain(self):
        self.canvas.render(self.wave(), markers=[event(index) for index in range(100)])
        visible_labels = sum(self.canvas.itemcget(slot["label"], "state") != "hidden"
                             for slot in self.canvas._marker_pool)
        visible_dots = sum(self.canvas.itemcget(slot["dot"], "state") != "hidden"
                           for slot in self.canvas._marker_pool)
        self.assertLess(visible_labels, 100)
        self.assertEqual(visible_dots, 100)

    def test_click_returns_current_event_and_hidden_slot_cannot_be_clicked(self):
        clicked = []
        self.canvas.on_marker_click = clicked.append
        expected = event(720, label="V")
        self.canvas.render(self.wave(), markers=[expected])
        self.canvas._marker_clicked(0)
        self.assertEqual(clicked, [expected])
        self.assertIsNot(clicked[0], expected)
        self.canvas.render(self.wave(), markers=[])
        self.canvas._marker_clicked(0)
        self.assertEqual(len(clicked), 1)

    def test_missing_nan_and_wrong_segment_hide_previous_marker(self):
        wave = self.wave()
        self.canvas.render(wave, markers=[event(720)])
        wave["filtered"][720] = np.nan
        self.canvas.render(wave, markers=[event(720)])
        self.assertEqual(self.canvas._marker_data, [])
        wave["filtered"][720] = 2048
        self.canvas.render(wave, markers=[event(720, 9)])
        self.assertEqual(self.canvas._marker_data, [])


if __name__ == "__main__":
    unittest.main()
