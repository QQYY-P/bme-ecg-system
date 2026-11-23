"""Synthetic committed-history review tests; never read patient recordings."""
from contextlib import closing
from copy import deepcopy
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos

import classification_review as review
from contracts import SampleBatch, SamplePoint, INVALID, DISCONTINUITY
from preprocessing import PREPROCESSING_CONFIG, RealECGPreprocessor
from recording import Recorder


class ClassificationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix=".test-classification-review-", dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.finish()
        self.temp.cleanup()

    @staticmethod
    def adc(index):
        return round(2048 + 180 * math.sin(2 * math.pi * index / 360)
                     + 35 * math.sin(2 * math.pi * 50 * index / 360))

    def record(self, *, seconds=12, metadata=None, points=None, finish=True, events=None):
        meta = {"session_id": "review-test", "source": "serial", "vref_v": 3.3,
                "preprocessing": deepcopy(PREPROCESSING_CONFIG), "classification": {},
                "time_info": {"trusted": False, "source": "manual"}}
        if metadata:
            meta.update(metadata)
        recorder = Recorder(self.root, meta, queue_size=100).start()
        self.recorders.append(recorder)
        if points is None:
            points = [SamplePoint(i, 0, i, i % 256, self.adc(i), 0, 0) for i in range(seconds * 360)]
        for offset in range(0, len(points), 360):
            self.assertTrue(recorder.submit(SampleBatch(
                meta["session_id"], meta["source"], tuple(points[offset:offset + 360]),
                offset / 360, "2026-10-29T02:00:00Z", b"test-uart")))
        if events is None:
            events = [("cardioedge_prediction", {
                "session_id": meta["session_id"], "segment_id": 0, "r_sample": 8 * 360,
                "label": "V", "scores": {"N": .1, "S": .2, "V": .7}, "mode": 0})]
        for kind, detail in events:
            self.assertTrue(recorder.add_event(kind, detail))
        if finish:
            self.assertIsNone(recorder.finish()["error"])
        return recorder

    def insert_raw_event(self, recorder, kind, raw):
        with closing(sqlite3.connect(recorder.path / "index.sqlite")) as connection:
            connection.execute("INSERT INTO events(utc,kind,detail_json) VALUES(?,?,?)", ("later", kind, raw))
            connection.commit()

    def test_all_session_counts_and_latest_rows_have_explicit_event_order(self):
        events = [("cardioedge_prediction", {"r_sample": 100 - i, "segment_id": 0,
                                            "label": ("N", "S", "V")[i % 3]}) for i in range(9)]
        events += [("cardioedge_rejected", {"r_sample": 110, "segment_id": 0, "reason": "rr_invalid"}),
                   ("cardioedge_rejected", {"r_sample": 120, "segment_id": 0, "reason": "rr_invalid"}),
                   ("cardioedge_rejected", {"r_sample": 130, "segment_id": 0, "reason": "clipped"})]
        self.record(events=events)
        result = review.classification_details(self.root, "review-test", limit=2)
        self.assertEqual(result["counts"], {"N": 3, "S": 3, "V": 3})
        self.assertEqual(result["classified"], 9)
        self.assertEqual(result["total_rows"], 9)
        self.assertTrue(result["truncated"])
        self.assertEqual([row["r_sample"] for row in result["rows"]], [93, 92])
        self.assertEqual(result["row_order"], "event_id_ascending_latest")
        self.assertEqual(result["rejected"], 3)
        self.assertEqual(result["reason_counts"], {"rr_invalid": 2, "clipped": 1})
        self.assertEqual(result["rejected_source"], "events")
        self.assertFalse(result["time_info"]["trusted"])

    def test_legacy_rejected_uses_latest_valid_summary_without_making_up_beats(self):
        self.record(events=[("cardioedge_summary", {"rejected": 5}),
                            ("cardioedge_summary", {"rejected": 7})])
        result = review.classification_details(self.root, "review-test")
        self.assertEqual(result["classified"], 0)
        self.assertEqual(result["rejected"], 7)
        self.assertEqual(result["rejected_source"], "legacy_summary")
        self.assertEqual(result["reason_counts"], {"legacy_unspecified": 7})
        self.assertIn("旧记录", result["note"])

    def test_missing_rejection_evidence_is_unknown_not_zero(self):
        self.record()
        result = review.classification_details(self.root, "review-test")
        self.assertIsNone(result["rejected"])
        self.assertEqual(result["rejected_source"], "unavailable")

    def test_old_or_malformed_clock_metadata_never_implies_trusted_time(self):
        self.record(metadata={"time_info": "invalid"})
        result = review.classification_details(self.root, "review-test")
        self.assertEqual(result["time_info"], {"trusted": False, "source": "unknown"})

    def test_new_rejection_schema_can_report_true_zero(self):
        self.record(metadata={"classification": {"rejection_event_schema": 1}})
        result = review.classification_details(self.root, "review-test")
        self.assertEqual(result["rejected"], 0)
        self.assertEqual(result["rejected_source"], "events")

    def test_corrupt_and_foreign_predictions_are_not_counted_as_normal(self):
        recorder = self.record(events=[("cardioedge_prediction", {"label": "unknown", "r_sample": 1}),
                                       ("cardioedge_prediction", {"label": "N", "session_id": "other"})])
        self.insert_raw_event(recorder, "cardioedge_prediction", "not-json")
        result = review.classification_details(self.root, "review-test")
        self.assertEqual(result["counts"], {"N": 0, "S": 0, "V": 0})
        self.assertEqual(result["classified"], 0)
        self.assertEqual(result["total_rows"], 3)
        self.assertEqual(result["malformed_events"], 2)
        self.assertEqual(result["foreign_session_events"], 1)
        self.assertTrue(all(row["integrity_error"] for row in result["rows"]))

    def test_new_rejection_events_take_priority_over_legacy_summary(self):
        self.record(events=[("cardioedge_rejected", {"reason": "input_range", "r_sample": 100, "segment_id": 0}),
                            ("cardioedge_summary", {"rejected": 30})])
        result = review.classification_details(self.root, "review-test")
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["reason_counts"], {"input_range": 1})

    def test_old_valid_predictions_get_containing_session_id_for_ui_review(self):
        self.record(events=[("cardioedge_prediction", {"r_sample": 8 * 360, "segment_id": 0, "label": "N"})])
        details = review.classification_details(self.root, "review-test")
        self.assertEqual(details["rows"][0]["session_id"], "review-test")
        self.assertIsNone(details["rows"][0]["integrity_error"])
        result = review.review_beat(self.root, "review-test", 8 * 360, 0)
        self.assertEqual(result["prediction"]["session_id"], "review-test")

    def test_damaged_sample_ids_cannot_be_counted_as_predictions_or_rejected_candidates(self):
        self.record(metadata={"classification": {"rejection_event_schema": 1}}, events=[
            ("cardioedge_prediction", {"r_sample": -1, "segment_id": 0, "label": "N"}),
            ("cardioedge_prediction", {"r_sample": True, "segment_id": 0, "label": "N"}),
            ("cardioedge_prediction", {"r_sample": 100, "segment_id": "0", "label": "S"}),
            ("cardioedge_rejected", {"reason": "missing_ids"}),
            ("cardioedge_rejected", {"r_sample": 100, "segment_id": False, "reason": "bad_id"}),
            ("cardioedge_rejected", {"r_sample": 101, "segment_id": 0, "reason": "valid_rejection"}),
            ("cardioedge_prediction", {"session_id": "other", "r_sample": 100, "segment_id": 0, "label": "V"}),
        ])
        details = review.classification_details(self.root, "review-test")
        self.assertEqual(details["classified"], 0)
        self.assertEqual(details["rejected"], 1)
        self.assertEqual(details["reason_counts"], {"valid_rejection": 1})
        self.assertEqual(details["malformed_events"], 5)
        self.assertTrue(all(row["integrity_error"] for row in details["rows"]))
        self.assertEqual(details["rows"][-1]["session_id"], "other")
        self.assertIn("不计入", details["note"])

    def test_review_retains_adc_units_and_matches_display_filter_from_warmup(self):
        recorder = self.record()
        before = (recorder.path / "chunks/00000000.bin").read_bytes()
        result = review.review_beat(self.root, "review-test", 8 * 360, 0)
        self.assertFalse(result["partial"])
        self.assertEqual(result["wave_units"], "adc_counts")
        wave = result["wave"]
        self.assertEqual(len(wave["x"]), 4 * 360 + 1)
        self.assertEqual(wave["sample_ids"][0], 6 * 360)
        self.assertEqual(wave["sample_ids"][-1], 10 * 360)
        self.assertGreater(np.mean(wave["raw"]), 1900)
        self.assertEqual(wave["raw_adc"], [self.adc(i) for i in range(6 * 360, 10 * 360 + 1)])
        preprocessor = RealECGPreprocessor()
        expected, _ = preprocessor.process([self.adc(i) for i in range(360, 10 * 360 + 1)])
        np.testing.assert_allclose(wave["filtered"], expected[5 * 360:], atol=1e-8)
        self.assertEqual(result["prediction"]["label"], "V")
        self.assertEqual((recorder.path / "chunks/00000000.bin").read_bytes(), before)
        self.assertIn("重新计算", result["note"])

    def test_review_reads_only_window_chunks_with_five_second_warmup(self):
        self.record(seconds=30, events=[("cardioedge_prediction", {"r_sample": 20 * 360,
                                                                  "segment_id": 0, "label": "N"})])
        used = []
        original = review._chunk_rows

        def capture(directory, descriptor):
            used.append(descriptor["chunk_id"])
            return original(directory, descriptor)

        with patch.object(review, "_chunk_rows", side_effect=capture):
            result = review.review_beat(self.root, "review-test", 20 * 360, 0)
        self.assertEqual(used, list(range(13, 23)))
        self.assertFalse(result["partial"])

    def test_saved_custom_filter_parameters_are_used_instead_of_current_defaults(self):
        config = deepcopy(PREPROCESSING_CONFIG)
        config["highpass"]["cutoff_hz"] = 1.0
        config["lowpass"]["cutoff_hz"] = 25.0
        config["notch"]["center_hz"] = 60.0
        self.record(metadata={"preprocessing": config})
        result = review.review_beat(self.root, "review-test", 8 * 360, 0)
        b, a = iirnotch(60, 30, fs=360)
        sos = np.vstack((butter(2, 1, btype="highpass", fs=360, output="sos"), tf2sos(b, a),
                         butter(4, 25, btype="lowpass", fs=360, output="sos")))
        values = np.array([self.adc(i) for i in range(360, 10 * 360 + 1)], dtype=float)
        expected, _ = sosfilt(sos, values, zi=sosfilt_zi(sos) * values[0])
        np.testing.assert_allclose(result["wave"]["filtered"], expected[5 * 360:], atol=1e-9)

    def test_legacy_vref_is_supported_and_missing_filter_is_not_guessed(self):
        recorder = self.record()
        with closing(sqlite3.connect(recorder.path / "index.sqlite")) as connection:
            metadata = json.loads(connection.execute("SELECT metadata_json FROM sessions").fetchone()[0])
            metadata.pop("vref_v")
            metadata.pop("preprocessing")
            metadata["vref"] = 3.0
            connection.execute("UPDATE sessions SET metadata_json=?", (json.dumps(metadata),))
            connection.commit()
        result = review.review_beat(self.root, "review-test", 8 * 360, 0)
        self.assertEqual(result["vref"], 3.0)
        self.assertFalse(result["filter_recomputed"])
        self.assertTrue(np.isnan(result["wave"]["filtered"]).all())
        self.assertTrue(np.isfinite(result["wave"]["raw"]).all())

    def test_record_boundary_returns_partial_window(self):
        self.record(seconds=2, events=[("cardioedge_prediction", {"r_sample": 360, "segment_id": 0, "label": "N"})])
        result = review.review_beat(self.root, "review-test", 360, 0)
        self.assertTrue(result["partial"])
        self.assertEqual(result["begin_s"], 0)
        self.assertEqual(result["end_s"], 3)
        self.assertEqual(result["wave"]["sample_ids"][-1], 719)

    def test_missing_saved_reference_voltage_is_not_silently_assumed(self):
        recorder = self.record()
        with closing(sqlite3.connect(recorder.path / "index.sqlite")) as connection:
            metadata = json.loads(connection.execute("SELECT metadata_json FROM sessions").fetchone()[0])
            metadata.pop("vref_v", None)
            metadata.pop("vref", None)
            connection.execute("UPDATE sessions SET metadata_json=?", (json.dumps(metadata),))
            connection.commit()
        with self.assertRaisesRegex(ValueError, "未保存参考电压"):
            review.review_beat(self.root, "review-test", 8 * 360, 0)

    def test_segment_gap_and_invalid_markers_are_preserved(self):
        points = []
        for index in range(12 * 360):
            if index == 3000:
                continue
            segment = 0 if index < 2900 else 1
            flags = DISCONTINUITY if index == 2900 else INVALID if index == 2950 else 0
            adc = 65535 if index == 2950 else self.adc(index)
            points.append(SamplePoint(index, segment, index if segment == 0 else index - 2900,
                                      index % 256, adc, flags, 0))
        self.record(points=points)
        result = review.review_beat(self.root, "review-test", 8 * 360, 0)
        self.assertTrue(result["partial"])
        wave = result["wave"]
        self.assertTrue(np.isnan(wave["x"]).any())
        self.assertTrue({0, 1} <= set(wave["segment_ids"]))
        position = wave["sample_ids"].index(2950)
        self.assertEqual(wave["raw_adc"][position], 65535)
        self.assertTrue(math.isnan(wave["raw"][position]))
        self.assertTrue(math.isnan(wave["filtered"][position]))

    def test_wrong_segment_or_foreign_session_cannot_pair_with_waveform(self):
        self.record(events=[("cardioedge_prediction", {"r_sample": 8 * 360, "segment_id": 3, "label": "V"}),
                            ("cardioedge_prediction", {"r_sample": 8 * 360, "segment_id": 0,
                                                       "label": "N", "session_id": "other"})])
        with self.assertRaisesRegex(ValueError, "分段.*不一致"):
            review.review_beat(self.root, "review-test", 8 * 360, 3)
        with self.assertRaisesRegex(ValueError, "没有.*匹配"):
            review.review_beat(self.root, "review-test", 8 * 360, 0)

    def test_committed_event_with_not_yet_committed_r_sample_returns_no_unrelated_wave(self):
        self.record(seconds=2, events=[("cardioedge_prediction", {"r_sample": 900, "segment_id": 0, "label": "N"})])
        result = review.review_beat(self.root, "review-test", 900, 0)
        self.assertTrue(result["partial"])
        self.assertEqual(result["wave"]["x"], [])
        self.assertIn("尚未提交或缺失", result["note"])

    def test_active_recording_is_read_without_taking_writer_lock(self):
        recorder = self.record(seconds=12, finish=False)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if recorder.status()["saved"] == 12 * 360:
                details = review.classification_details(self.root, "review-test")
                if details["classified"] == 1:
                    break
            time.sleep(.02)
        else:
            self.fail("synthetic record did not commit in time")
        self.assertEqual(details["status"], "recording")
        result = review.review_beat(self.root, "review-test", 8 * 360, 0)
        self.assertFalse(result["partial"])
        self.assertTrue(recorder._thread.is_alive())

    def test_corrupt_selected_chunk_is_rejected(self):
        recorder = self.record()
        (recorder.path / "chunks/00000008.bin").write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            review.review_beat(self.root, "review-test", 8 * 360, 0)

    def test_corruption_outside_bounded_window_does_not_force_full_record_loading(self):
        recorder = self.record(seconds=30, events=[("cardioedge_prediction", {
            "r_sample": 20 * 360, "segment_id": 0, "label": "V"})])
        (recorder.path / "chunks/00000000.bin").write_bytes(b"broken outside review window")
        self.assertFalse(review.review_beat(self.root, "review-test", 20 * 360, 0)["partial"])

    def test_linked_selected_raw_file_is_rejected(self):
        recorder = self.record()
        target = recorder.path / "chunks/00000008.bin"
        actual = Path.is_symlink

        def linked(path):
            return path == target or actual(path)

        with patch.object(Path, "is_symlink", linked):
            with self.assertRaisesRegex(ValueError, "符号链接"):
                review.review_beat(self.root, "review-test", 8 * 360, 0)

    def test_parameter_and_session_traversal_guards(self):
        self.record()
        for limit in (0, 501, True, "10"):
            with self.assertRaises(ValueError):
                review.classification_details(self.root, "review-test", limit)
        for args in ((-1, 0), (True, 0), (2880, -1), (2880, True)):
            with self.assertRaises(ValueError):
                review.review_beat(self.root, "review-test", *args)
        for value in (-1, float("inf"), float("nan"), 31, True):
            with self.assertRaises(ValueError):
                review.review_beat(self.root, "review-test", 2880, 0, before_s=value)
        with self.assertRaises(ValueError):
            review.classification_details(self.root, "../review-test")
        with self.assertRaises(ValueError):
            review.review_beat(self.root, "review-test", 2880, 0, before_s=0, after_s=0)


if __name__ == "__main__":
    unittest.main()
