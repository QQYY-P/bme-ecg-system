import csv
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from dataclasses import replace

import numpy as np

from controller import Controller, _WaveBuffer
from contracts import SampleBatch, SamplePoint, INVALID, SATURATED


def serial_batch(modes, start=0):
    return SampleBatch("mode-test", "serial", tuple(
        SamplePoint(start + i, 0, start + i, (start + i) % 256, 2000 + mode * 100, mode=mode)
        for i, mode in enumerate(modes)), time.monotonic(), "2026-10-27T00:00:00+00:00")


def controller_for_mode_test():
    c = Controller()
    c._state.update(session_id="mode-test", source="serial", board_mode=0,
                    state="RECORDING", running=True, serial_aligned=True,
                    connection_state="receiving", connection_text="串口接收正常")
    c._recorder = mock.Mock()
    c._recorder.submit.return_value = True
    c._recorder.status.return_value = {}
    return c


class ControllerTests(unittest.TestCase):
    def test_display_ring_matches_bounded_history_across_wrap_and_reset(self):
        from collections import deque
        ring, expected = _WaveBuffer(7), deque(maxlen=7)
        for count in (0, 1, 5, 3, 7, 16, 2):
            rows = [(i / 360, np.float64(i), np.nan if i % 3 == 0 else i / 2, i % 2)
                    for i in range(count)]
            ring.extend(iter(rows))
            expected.extend(rows)
            np.testing.assert_equal(ring.array(), np.asarray(expected, dtype=float).reshape(-1, 4))
            self.assertEqual(len(ring), len(expected))
            if expected:
                np.testing.assert_equal(ring[-1], expected[-1])
        copy = ring.array()
        copy[:] = -999
        self.assertNotEqual(ring[-1][0], -999)
        ring.clear()
        self.assertEqual(ring.array().shape, (0, 4))
        ring.append((1., 2., 3.))
        np.testing.assert_equal(ring.array(), [[1., 2., 3., -1.]])

    def test_display_ring_snapshot_preserves_window_gaps_and_isolation(self):
        c = Controller(classification_enabled=False)
        c._wave.extend([(0., 1., 1., 0), (np.nan, np.nan, np.nan, -1),
                        (28., 4., 3., 1), (29., np.nan, np.nan, -1), (30., 5., 4., 2)])
        result = c.snapshot(window_s=2)['wave']
        np.testing.assert_equal(result['x'], [np.nan, 28., 29., 30.])
        np.testing.assert_equal(result['segment_ids'], [-1, 1, -1, 2])
        result['raw'][:] = 0
        self.assertEqual(c.snapshot(window_s=2)['wave']['raw'][-1], 5.)

    def test_mode_check_observes_new_mode_without_sending_a_command(self):
        c = controller_for_mode_test()
        c._source = mock.Mock()
        with mock.patch("controller.time.monotonic", return_value=100):
            self.assertEqual(c.begin_mode_check(), 1)
            self.assertIn("请按 STM32 的 SW2", c.snapshot()["mode_feedback"])
        self.assertEqual(c._source.mock_calls, [])
        with mock.patch("controller.time.monotonic", return_value=101):
            c._on_event("mode_changed", {"previous_mode": 0, "mode": 1})
            guard = replace(serial_batch([1]), points=(SamplePoint(0, 0, 0, 0, 65535, INVALID, 1),))
            c._on_batch(guard)
            state = c.snapshot()
        self.assertEqual(state["mode_check_state"], "confirmed")
        self.assertEqual(state["mode_change_count"], 1)
        self.assertEqual(state["board_mode"], 1)
        self.assertEqual(state["signal_state"], "invalid")
        self.assertFalse(state["hr_allowed"])
        self.assertEqual(c._source.mock_calls, [])

    def test_mode_check_times_out_exactly_once_and_late_change_is_not_success(self):
        c = controller_for_mode_test()
        with mock.patch("controller.time.monotonic", return_value=100) as clock:
            c.begin_mode_check(10)
            clock.return_value = 109.999
            c._on_event("mode_changed", {"mode": 0})
            self.assertEqual(c.snapshot()["mode_check_state"], "waiting")
            clock.return_value = 110
            self.assertEqual(c.snapshot()["mode_check_state"], "timeout")
            for _ in range(3):
                c.snapshot()
            clock.return_value = 111
            c._on_event("mode_changed", {"mode": 1})
            self.assertNotEqual(c.snapshot()["mode_check_state"], "confirmed")
        events = [call.args[0] for call in c._recorder.add_event.call_args_list]
        self.assertEqual(events.count("mode_check_timeout"), 1)
        self.assertNotIn("mode_check_confirmed", events)

    def test_mode_check_interrupted_by_loss_resync_or_stop(self):
        for action in ("timeout", "protocol_resync", "stop"):
            with self.subTest(action=action):
                c = controller_for_mode_test()
                c.begin_mode_check()
                if action == "stop":
                    c.stop()
                else:
                    c._on_event(action, {"locked": False, "message": "测试断流"})
                state = c.snapshot()
                self.assertEqual(state["mode_check_state"], "interrupted")
                self.assertIsNone(state["hr"])
                self.assertFalse(state["hr_allowed"])
                if action == "stop":
                    self.assertEqual(state["connection_state"], "stopped")
                    self.assertEqual(state["signal_state"], "waiting")
                    self.assertEqual(state["signal_text"], "采集已停止")

    def test_mode_check_requires_live_confirmed_serial_and_valid_duration(self):
        for updates in ({"source": "demo"}, {"running": False}, {"state": "STOPPING"},
                        {"serial_aligned": False}, {"board_mode": None}):
            c = controller_for_mode_test(); c._state.update(updates)
            with self.assertRaises(RuntimeError):
                c.begin_mode_check()
        c = controller_for_mode_test()
        for duration in (0, -1, float("nan"), float("inf"), 61):
            with self.assertRaises(ValueError):
                c.begin_mode_check(duration)
        c.begin_mode_check(1)
        with self.assertRaises(RuntimeError):
            c.begin_mode_check(1)

    def test_initial_or_reconnected_mode_confirmation_is_not_a_switch(self):
        c = controller_for_mode_test(); c._state["board_mode"] = None
        c._on_event("mode_changed", {"mode": 0, "previous_mode": None})
        self.assertEqual(c.snapshot()["mode_feedback"], "已确认实采")
        self.assertEqual(c.snapshot()["mode_change_count"], 0)
        c._on_event("timeout", {"message": "断流"})
        c._on_event("aligned", {})
        c._on_event("mode_changed", {"mode": 1, "previous_mode": None})
        self.assertEqual(c.snapshot()["mode_feedback"], "已确认板端模拟")
        self.assertEqual(c.snapshot()["mode_change_count"], 0)

    def test_connection_health_is_independent_of_invalid_signal(self):
        c = controller_for_mode_test()
        c._on_event("opened", {})
        self.assertEqual(c.snapshot()["connection_state"], "open_waiting")
        self.assertNotIn("正常", c.snapshot()["connection_text"])
        c._on_event("aligned", {})
        c._on_batch(replace(serial_batch([0]), points=(SamplePoint(0, 0, 0, 0, 65535, INVALID, 0),)))
        state = c.snapshot()
        self.assertEqual(state["connection_state"], "receiving")
        self.assertEqual(state["connection_text"], "串口接收正常")
        self.assertEqual(state["signal_state"], "invalid")
        self.assertIn("协议未给出具体原因", state["signal_detail"])
        self.assertFalse(state["hr_allowed"])

    def test_known_bad_points_clear_old_hr_before_analysis_catches_up(self):
        for value, flag, expected in ((65535, INVALID, "invalid"), (4095, SATURATED, "saturated")):
            c = controller_for_mode_test()
            c._state.update(hr=72, rr_ms=833, hr_allowed=True)
            generation = c._generation
            c._on_batch(replace(serial_batch([0]), points=(SamplePoint(0, 0, 0, 0, value, flag, 0),)))
            state = c.snapshot()
            self.assertIsNone(state["hr"])
            self.assertIsNone(state["rr_ms"])
            self.assertFalse(state["hr_allowed"])
            self.assertEqual(state["signal_state"], expected)
            self.assertGreater(c._generation, generation)
            self.assertEqual(state["received"], 1)

    def test_quality_events_only_record_state_transitions_and_snapshots_are_copies(self):
        c = controller_for_mode_test()
        with c._lock:
            c._set_signal_state("good", "可分析", metrics={"rms": 10})
            c._set_signal_state("good", "可分析", metrics={"rms": 11})
            c._set_signal_state("noisy", "噪声较强", metrics={"rms": 100})
        transitions = [call for call in c._recorder.add_event.call_args_list if call.args[0] == "signal_quality_changed"]
        self.assertEqual(len(transitions), 2)
        state = c.snapshot(); state["quality_metrics"]["rms"] = -1
        self.assertEqual(c.snapshot()["quality_metrics"]["rms"], 100)

    def test_invalid_sample_rejects_a_good_result_already_being_analyzed(self):
        c = controller_for_mode_test()
        finished = threading.Event()

        class RacedAnalyzer:
            def reset(self):
                pass

            def snapshot(self):
                return {"hr": None, "rr_ms": None, "hr_allowed": False}

            def process(self, batch):
                bad = replace(serial_batch([0], start=1),
                              points=(SamplePoint(1, 1, 0, 1, 65535, INVALID, 0),))
                c._on_batch(bad)
                # Keep only this in-flight old result in the worker for this test.
                c._analysis_queue.get_nowait()
                finished.set()
                return {"hr": 72, "rr_ms": 833, "hr_allowed": True,
                        "signal_state": "good", "signal_text": "可分析"}

        c._on_batch(serial_batch([0]))
        c._analyze(RacedAnalyzer(), c._analysis_queue, finished, "mode-test")
        state = c.snapshot()
        self.assertEqual(state["signal_state"], "invalid")
        self.assertFalse(state["hr_allowed"])
        self.assertIsNone(state["hr"])
        self.assertEqual(c._recorder.submit.call_count, 2)

    def test_stop_rejects_late_good_analysis_result(self):
        c = controller_for_mode_test()
        finished = threading.Event()

        class StoppingAnalyzer:
            def reset(self):
                pass

            def process(self, batch):
                c.stop()
                finished.set()
                return {"hr": 72, "rr_ms": 833, "hr_allowed": True,
                        "signal_state": "good", "signal_text": "可分析"}

        c._on_batch(serial_batch([0]))
        c._analyze(StoppingAnalyzer(), c._analysis_queue, finished, "mode-test")
        state = c.snapshot()
        self.assertEqual(state["signal_state"], "waiting")
        self.assertEqual(state["signal_text"], "采集已停止")
        self.assertFalse(state["hr_allowed"])
        self.assertIsNone(state["hr"])

    def test_late_source_events_are_logged_without_reopening_stopped_state(self):
        c = controller_for_mode_test()
        c.begin_mode_check()
        c.stop()
        before = c.snapshot()
        for kind in ("opened", "aligned", "resumed", "mode_changed", "protocol_resync",
                     "timeout", "no_data", "sync_wait", "protocol_stats"):
            with self.subTest(kind=kind):
                detail = {"mode": 1, "locked": True, "message": "late event"}
                c._on_event(kind, detail)
                c._recorder.add_event.assert_called_with(kind, detail)
                current = c.snapshot()
                for field in ("state", "connection_state", "signal_state", "signal_text",
                              "mode_check_state", "board_mode", "mode_change_count", "hr_allowed"):
                    self.assertEqual(current[field], before[field])

    def test_late_error_is_still_reported_after_stop(self):
        c = controller_for_mode_test()
        c.stop()
        with self.assertLogs("controller", level="ERROR"):
            c._on_event("error", {"message": "final read failed"})
        state = c.snapshot()
        self.assertEqual(state["error"], "final read failed")
        self.assertEqual(state["connection_state"], "error")
        self.assertFalse(state["hr_allowed"])

    def test_last_block_after_stop_is_saved_without_reviving_mode_or_signal(self):
        c = controller_for_mode_test()
        c.stop()
        before = c.snapshot()
        final_block = replace(serial_batch([1]), wire=b"final-wire",
                              points=(SamplePoint(0, 0, 0, 0, 65535, INVALID, 1),))
        self.assertTrue(c._on_batch(final_block))
        c._recorder.submit.assert_called_once_with(final_block)
        state = c.snapshot()
        self.assertEqual(state["received"], 1)
        self.assertEqual(state["serial_bytes"], len(b"final-wire"))
        self.assertTrue(c._analysis_queue.empty())
        for field in ("state", "connection_state", "signal_state", "signal_text", "board_mode",
                      "mode_check_state", "mode_change_count", "hr_allowed", "analysis_generation"):
            self.assertEqual(state[field], before[field])

    def test_stop_flag_itself_blocks_analysis_before_state_transition(self):
        c = controller_for_mode_test()
        finished = threading.Event()

        class StopFlagAnalyzer:
            def reset(self):
                pass

            def process(self, batch):
                # Model the short interval between cancellation and taking _lock.
                c._stop.set()
                finished.set()
                return {"hr": 72, "rr_ms": 833, "hr_allowed": True,
                        "signal_state": "good", "signal_text": "可分析",
                        "wave": {"x": [0], "raw": [2000], "filtered": [2000]}}

        c._on_batch(serial_batch([0]))
        c._analyze(StopFlagAnalyzer(), c._analysis_queue, finished, "mode-test")
        state = c.snapshot()
        self.assertEqual(state["state"], "RECORDING")
        self.assertEqual(state["signal_state"], "waiting")
        self.assertFalse(state["hr_allowed"])
        self.assertIsNone(state["hr"])
        self.assertEqual(len(state["wave"]["x"]), 0)
        with self.assertRaises(RuntimeError):
            c.begin_mode_check()

    def test_serial_metadata_describes_framed_mode_protocol(self):
        c = Controller()
        with mock.patch("controller.resolve_serial_port", return_value="test-port"), \
             mock.patch("controller.threading.Thread") as thread:
            c.start("serial", port="auto")
        metadata = thread.call_args.kwargs["args"][0]
        self.assertEqual(metadata["signal_origin"], "per_sample_mode")
        self.assertEqual(metadata["serial_protocol"]["frame_bytes"], 7)
        self.assertEqual(metadata["serial_protocol"]["header"], "AA55")
        self.assertTrue(metadata["serial_protocol"]["mode_field"])
        self.assertIn("sum8", metadata["serial_protocol"]["checksum"])
        self.assertIn("signal_quality", metadata)
        self.assertEqual(metadata["signal_quality"]["sample_rate_hz"], 360)

    def test_mode_change_and_timeout_clear_old_metrics_and_trace(self):
        c = controller_for_mode_test()
        c._state.update(hr=72, rr_ms=833)
        c._wave.append((0., 2048., 2048.))
        c._on_event("mode_changed", {"previous_mode": 0, "mode": 1})
        current = c.snapshot()
        self.assertEqual(current["board_mode"], 1)
        self.assertIsNone(current["hr"])
        self.assertEqual(len(current["wave"]["x"]), 0)
        generation = current["analysis_generation"]
        c._on_event("timeout", {"message": "test timeout"})
        current = c.snapshot()
        self.assertIsNone(current["board_mode"])
        self.assertFalse(current["serial_aligned"])
        self.assertGreater(current["analysis_generation"], generation)

    def test_mixed_mode_batch_saves_all_points_but_only_analyzes_latest_mode(self):
        c = controller_for_mode_test()
        batch = serial_batch([0, 0, 1, 1])
        self.assertTrue(c._on_batch(batch))
        c._recorder.submit.assert_called_once_with(batch)
        generation, analyzed = c._analysis_queue.get_nowait()
        self.assertEqual([point.mode for point in analyzed.points], [1, 1])
        self.assertEqual([point.sample_id for point in analyzed.points], [2, 3])
        self.assertEqual(c.snapshot()["received"], 4)
        self.assertEqual(c.snapshot()["board_mode"], 1)

    def test_in_flight_analysis_cannot_publish_after_mode_switch(self):
        c = controller_for_mode_test()
        entered, release, processed_new, finished = (threading.Event() for _ in range(4))

        class SlowAnalyzer:
            def reset(self):
                pass

            def snapshot(self):
                return {"hr": None, "rr_ms": None, "quality": "等待"}

            def process(self, batch):
                mode = batch.points[-1].mode
                if mode == 0:
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("test timed out")
                else:
                    processed_new.set()
                return {"hr": 99 if mode == 0 else None, "rr_ms": 606 if mode == 0 else None,
                        "hr_allowed": mode == 0,
                        "wave": {"x": [mode], "raw": [2000 + mode * 100], "filtered": [2000 + mode * 100]}}

        worker = threading.Thread(target=c._analyze, args=(SlowAnalyzer(), c._analysis_queue, finished, "mode-test"))
        worker.start()
        try:
            c._on_batch(serial_batch([0]))
            self.assertTrue(entered.wait(3))
            c._on_event("mode_changed", {"previous_mode": 0, "mode": 1})
            c._on_batch(serial_batch([1], start=1))
            release.set()
            self.assertTrue(processed_new.wait(3))
        finally:
            release.set()
            finished.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        state = c.snapshot()
        self.assertIsNone(state["hr"])
        self.assertEqual(state["wave"]["raw"].tolist(), [2100])

    def test_mode_switch_while_waiting_for_queue_keeps_first_new_batch(self):
        c = controller_for_mode_test()
        finished = threading.Event()
        analyzer = mock.Mock()
        analyzer.process.return_value = {"hr": None, "rr_ms": None}

        class SwitchingQueue:
            first = True

            def get(self, timeout):
                if self.first:
                    self.first = False
                    c._on_event("mode_changed", {"previous_mode": 0, "mode": 1})
                    return c._generation, serial_batch([1])
                finished.set()
                raise queue.Empty

        c._analyze(analyzer, SwitchingQueue(), finished, "mode-test")
        analyzer.process.assert_called_once()
        self.assertEqual(c.snapshot()["analysis_dropped_samples"], 0)

    def test_corrupt_stream_reports_resync_without_reusing_mode(self):
        c = controller_for_mode_test()
        c._on_event("protocol_stats", {"bad_frames": 2, "checksum_errors": 1})
        c._on_event("protocol_resync", {"locked": False})
        state = c.snapshot()
        self.assertEqual(state["state"], "WAITING_SYNC")
        self.assertIsNone(state["board_mode"])
        self.assertEqual(state["protocol_stats"]["checksum_errors"], 1)
        c._on_event("protocol_resync", {"locked": True})
        self.assertTrue(c.snapshot()["serial_aligned"])

    def test_complete_demo_session_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Controller(directory)
            session = c.start("demo", duration=.3)
            self.assertTrue(c.wait(8))
            state = c.snapshot()
            self.assertIsNone(state["error"])
            self.assertEqual(state["received"], 108)
            self.assertEqual(state["saved"], 108)
            self.assertFalse(state["running"])
            self.assertEqual(state["connection_state"], "stopped")
            self.assertEqual(state["signal_state"], "waiting")
            self.assertFalse(state["hr_allowed"])
            path = Path(directory) / "complete.csv"
            c.export_session(session, path)
            self.assertEqual(len(path.read_text(encoding="utf-8-sig").splitlines()) - 1, 108)

    def test_snapshots_keep_nan_segment_separators(self):
        c = Controller()
        c._wave.extend([(1., 100., 100.), (np.nan, np.nan, np.nan), (1.01, 101., 101.)])
        wave = c.snapshot()["wave"]
        self.assertEqual(len(wave["x"]), 3)
        self.assertTrue(np.isnan(wave["raw"][1]))

    def test_stopping_before_startup_finishes_does_not_leave_workers_running(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Controller(directory)
            c.start("demo")
            c.stop()
            self.assertTrue(c.wait(8))
            state = c.snapshot()
            self.assertFalse(state["running"])
            self.assertEqual(state["received"], state["saved"])

    def test_negative_replay_row_still_saves_and_exports_the_valid_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "negative.csv"
            source.write_text("sequence,raw_adc\n0,2000\n1,-1\n", encoding="utf-8")
            c = Controller(directory)
            with self.assertLogs("controller", level="ERROR"):
                session = c.start("replay", replay_path=source)
                self.assertTrue(c.wait(8))
            state = c.snapshot()
            self.assertIn("非法 ADC", state["error"])
            self.assertEqual(state["received"], 1)
            self.assertEqual(state["saved"], 1)
            self.assertEqual(state["pending"], 0)
            output = Path(directory) / "valid_prefix.csv"
            c.export_session(session, output)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([int(row["raw_adc"]) for row in rows], [2000])

    def test_frequently_segmented_replay_saves_every_sample_without_queue_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "segments.csv"
            lines = ["sequence,raw_adc,segment_id"]
            lines += [f"{i % 256},{2000 if i % 2 == 0 else 65535},{i}" for i in range(1000)]
            source.write_text("\n".join(lines) + "\n", encoding="utf-8")
            c = Controller(directory)
            started = time.monotonic()
            session = c.start("replay", replay_path=source)
            self.assertTrue(c.wait(12))
            state = c.snapshot()
            self.assertIsNone(state["error"])
            self.assertEqual(state["received"], 1000)
            self.assertEqual(state["saved"], 1000)
            self.assertEqual(state["pending"], 0)
            self.assertGreaterEqual(time.monotonic() - started, 1000 / 360 - .02)
            output = Path(directory) / "all_segments.csv"
            c.export_session(session, output)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1000)
            self.assertEqual(sum(int(row["raw_adc"]) == 65535 for row in rows), 500)


if __name__ == "__main__":
    unittest.main()
