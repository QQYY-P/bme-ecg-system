"""Storage tests use accelerated simulated input, never claim physical capture."""
from dataclasses import dataclass
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

import recording
from recording import Recorder, export_session, list_sessions, recover_session


@dataclass(frozen=True)
class Point:
    sample_id: int
    segment_id: int
    segment_index: int
    sequence: int
    raw_adc: int
    flags: int = 0
    mode: int | None = None


@dataclass(frozen=True)
class Batch:
    session_id: str
    source: str
    points: tuple
    received_monotonic: float
    received_utc: str
    wire: bytes = b""


def batch(start, count=360, session_id="test-session", wire=b""):
    points = tuple(Point(i, 0, i, i % 256, 2048) for i in range(start, start + count))
    return Batch(session_id, "demo", points, start / 360,
                 "2026-10-26T08:00:00.000+00:00", wire)


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.finish()
        self.temp.cleanup()

    def recorder(self, **kwargs):
        metadata = {"session_id": "test-session", "source": "demo", "vref": 3.3}
        result = Recorder(self.root, metadata, **kwargs)
        self.recorders.append(result)
        return result.start()

    def test_export_keeps_more_than_120_seconds(self):
        recorder = self.recorder(queue_size=256)
        for index in range(130):
            self.assertTrue(recorder.submit(batch(index * 360)))
        status = recorder.finish()
        self.assertIsNone(status["error"])
        self.assertEqual(status["saved"], 46800)
        self.assertEqual(status["pending"], 0)
        output = export_session(self.root, "test-session", self.root / "full.csv")
        with open(output, encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 46800)
        self.assertEqual(rows[0]["sample_id"], "0")
        self.assertEqual(rows[-1]["sample_id"], "46799")
        self.assertAlmostEqual(float(rows[-1]["time_s"]), 46799 / 360)
        self.assertEqual(rows[-1]["source"], "demo")
        self.assertEqual(list_sessions(self.root)[0]["status"], "complete")

    def test_invalid_values_and_real_sequence_flags_survive_export(self):
        recorder = self.recorder()
        points = (Point(0, 0, 0, 0, 65535, 1), Point(1, 1, 0, 1, 0, 2),
                  Point(2, 2, 0, 2, 4095, 2), Point(3, 3, 0, 2, 2048, 4),
                  Point(4, 4, 0, 3, 2000, 0))
        self.assertTrue(recorder.submit(Batch("test-session", "demo", points, 1.0, "now")))
        recorder.finish()
        report = json.loads((recorder.path / "quality.json").read_text(encoding="utf-8"))
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["counts"]["invalid_marker_samples"], 1)
        self.assertNotIn("lead_off_samples", report["counts"])
        self.assertEqual(report["counts"]["saturated_samples"], 2)
        self.assertEqual(report["counts"]["valid_samples"], 2)
        self.assertEqual(report["counts"]["observed_sequence_anomalies"], 1)
        path = export_session(self.root, "test-session", self.root / "quality.csv")
        with open(path, encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["raw_adc"], "65535")
        self.assertEqual(rows[0]["voltage_v"], "")
        self.assertEqual(rows[3]["flags"], "4")
        self.assertEqual(rows[4]["segment_id"], "4")

    def test_invalid_marker_run_is_counted_without_guessing_its_cause(self):
        recorder = self.recorder()
        # SW2 switching and lead-off use the same marker. Run length cannot
        # distinguish them, and old files may carry INVALID without 0xFFFF.
        points = tuple(Point(i, 0, i, i, 65535, 0) for i in range(36)) + (
            Point(36, 0, 36, 36, 65535, 1),
            Point(37, 0, 37, 37, 2048, 1),
            Point(38, 0, 38, 38, 2048, 0),
        )
        self.assertTrue(recorder.submit(Batch("test-session", "demo", points, 0.0, "now")))
        recorder.finish()
        report = json.loads((recorder.path / "quality.json").read_text(encoding="utf-8"))
        self.assertEqual(report["counts"]["invalid_marker_samples"], 38)
        self.assertEqual(report["counts"]["valid_samples"], 1)
        self.assertEqual(report["counts"]["recorded_samples"], 39)
        self.assertNotIn("lead_off_samples", report["counts"])
        self.assertIn("协议不区分", report["note"])
        self.assertIn("SW2", report["note"])
        metadata = json.loads((recorder.path / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema_version"], 2)
        self.assertEqual(metadata["quality_schema_version"], 3)
        self.assertEqual(metadata["quality"], report["counts"])

    def test_legacy_quality_session_can_be_exported_and_rebuilt_without_changing_raw_data(self):
        recorder = self.recorder()
        points = (Point(0, 0, 0, 0, 65535, 1), Point(1, 1, 0, 1, 2048, 0))
        self.assertTrue(recorder.submit(Batch("test-session", "demo", points, 0.0, "now",
                                            b"\x00\xff\xff\x01\x08\x00")))
        recorder.finish()
        # Construct a genuine old binary v1 chunk rather than only changing
        # cached metadata. No mode exists in these original rows.
        raw_path = recorder.path / "chunks" / "00000000.bin"
        raw = struct.pack("<8sHHII", b"ECGRAW01", 1, 20, 360, 2) + b"".join(
            struct.pack("<QIIBHB", p.sample_id, p.segment_id, p.segment_index,
                        p.sequence, p.raw_adc, p.flags) for p in points)
        raw_path.write_bytes(raw)
        sidecar = recorder.path / "chunks" / "00000000.json"
        descriptor = json.loads(sidecar.read_text(encoding="utf-8"))
        descriptor.update(schema_version=1, sha256=hashlib.sha256(raw).hexdigest())
        sidecar.write_text(json.dumps(descriptor), encoding="utf-8")
        metadata_path = recorder.path / "session.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["schema_version"] = 1
        metadata.pop("mode_encoding")
        metadata.pop("quality_schema_version")
        metadata["quality"].pop("invalid_marker_samples")
        # The cached v1 count is deliberately stale; recovery must count rows.
        metadata["quality"]["lead_off_samples"] = 999
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        (recorder.path / "quality.json").write_text(json.dumps({
            "schema_version": 1, "session_id": "test-session", "counts": metadata["quality"]
        }), encoding="utf-8")
        with sqlite3.connect(recorder.path / "index.sqlite") as connection:
            connection.execute("UPDATE sessions SET metadata_json=?", (json.dumps(metadata),))
            connection.execute("UPDATE chunks SET descriptor_json=? WHERE chunk_id=0",
                               (json.dumps(descriptor),))
        connection.close()
        original = {str(path.relative_to(recorder.path)): path.read_bytes()
                    for folder in ("chunks", "wire") for path in (recorder.path / folder).iterdir()}
        self.assertEqual(list_sessions(self.root)[0]["sample_count"], 2)
        before = Path(export_session(self.root, "test-session", self.root / "legacy.csv")).read_bytes()
        with (self.root / "legacy.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([r["mode"] for r in rows], ["", ""])
        recovery = recover_session(self.root, "test-session")
        self.assertEqual(recovery["verified_samples"], 2)
        self.assertFalse(recovery["errors"])
        self.assertEqual(recovery["recovered_chunks"], 0)
        after = Path(export_session(self.root, "test-session", self.root / "rebuilt.csv")).read_bytes()
        self.assertEqual(before, after)
        for relative, payload in original.items():
            self.assertEqual((recorder.path / relative).read_bytes(), payload)
        rebuilt = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(rebuilt["schema_version"], 1)
        self.assertEqual(rebuilt["quality_schema_version"], 3)
        self.assertEqual(rebuilt["quality"]["invalid_marker_samples"], 1)
        self.assertNotIn("lead_off_samples", rebuilt["quality"])
        report = json.loads((recorder.path / "quality.json").read_text(encoding="utf-8"))
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["counts"]["unknown_mode_samples"], 2)
        self.assertEqual(report["counts"], rebuilt["quality"])
        self.assertIn("已弃用", report["note"])
        self.assertIn("不能作为确定导联脱落", report["note"])
        with sqlite3.connect(recorder.path / "index.sqlite") as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        connection.close()

    def test_modes_flags_and_origins_survive_export_and_recovery(self):
        recorder = Recorder(self.root, {"session_id": "mixed", "source": "serial"}).start()
        self.recorders.append(recorder)
        points = (Point(0, 0, 0, 254, 2048, 0, 0),
                  Point(1, 1, 0, 255, 65535, 1 | 8, 1),
                  Point(2, 2, 0, 0, 2100, 0, 1),
                  Point(3, 3, 0, 1, 2200, 16, 1),
                  Point(4, 4, 0, 3, 2300, 4 | 8, 0))
        self.assertTrue(recorder.submit(Batch("mixed", "serial", points, 0., "now")))
        self.assertIsNone(recorder.finish()["error"])
        original_chunks = {p.name: p.read_bytes() for p in (recorder.path / "chunks").glob("*.bin")}
        path = Path(export_session(self.root, "mixed", self.root / "mixed.csv"))
        original_csv = path.read_bytes()
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([r["mode"] for r in rows], ["0", "1", "1", "1", "0"])
        self.assertEqual([r["signal_origin"] for r in rows],
                         ["real_adc", "board_simulated", "board_simulated", "board_simulated", "real_adc"])
        self.assertEqual([int(r["flags"]) for r in rows], [0, 9, 0, 16, 12])
        with sqlite3.connect(recorder.path / "index.sqlite") as connection:
            connection.execute("DELETE FROM chunks")
        connection.close()
        recovered = recover_session(self.root, "mixed")
        self.assertFalse(recovered["errors"])
        self.assertEqual(recovered["verified_samples"], 5)
        self.assertEqual(recovered["recovered_chunks"], 1)
        self.assertEqual(Path(export_session(self.root, "mixed", self.root / "recovered-mixed.csv")).read_bytes(), original_csv)
        counts = json.loads((recorder.path / "quality.json").read_text(encoding="utf-8"))["counts"]
        self.assertEqual((counts["real_mode_samples"], counts["simulated_mode_samples"], counts["unknown_mode_samples"]), (2, 3, 0))
        self.assertEqual((counts["observed_mode_changes"], counts["stream_resyncs"], counts["observed_sequence_anomalies"]), (2, 1, 1))
        for name, data in original_chunks.items():
            self.assertEqual((recorder.path / "chunks" / name).read_bytes(), data)

    def test_mode_does_not_mislabel_local_demo_or_replay_as_board_data(self):
        for source, expected_origin in (("demo", "local_simulated"), ("replay", "unknown")):
            recorder = Recorder(self.root, {"session_id": source, "source": source}).start()
            self.recorders.append(recorder)
            recorder.submit(Batch(source, source, (Point(0, 0, 0, 0, 2048, 0, 1),), 0., "now"))
            self.assertIsNone(recorder.finish()["error"])
            path = export_session(self.root, source, self.root / (source + ".csv"))
            with open(path, encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["mode"], "1")
            self.assertEqual(row["signal_origin"], expected_origin)

    def test_invalid_input_mode_is_not_saved(self):
        recorder = self.recorder()
        with self.assertLogs("recording", level="ERROR"):
            recorder.submit(Batch("test-session", "demo", (Point(0, 0, 0, 0, 2048, 0, 2),), 0., "now"))
            result = recorder.finish()
        self.assertIn("采样模式", result["error"])
        self.assertEqual(result["saved"], 0)

    def test_invalid_mode_in_v2_binary_rejected_even_with_valid_hash(self):
        recorder = self.recorder()
        recorder.submit(batch(0, 1))
        recorder.finish()
        raw_path = recorder.path / "chunks" / "00000000.bin"
        raw = raw_path.read_bytes()[:-1] + b"\x02"
        raw_path.write_bytes(raw)
        sidecar = recorder.path / "chunks" / "00000000.json"
        descriptor = json.loads(sidecar.read_text(encoding="utf-8"))
        descriptor["sha256"] = hashlib.sha256(raw).hexdigest()
        sidecar.write_text(json.dumps(descriptor), encoding="utf-8")
        with sqlite3.connect(recorder.path / "index.sqlite") as connection:
            connection.execute("UPDATE chunks SET descriptor_json=?", (json.dumps(descriptor),))
        connection.close()
        with self.assertRaisesRegex(ValueError, "模式字段无效"):
            export_session(self.root, "test-session", self.root / "invalid-mode.csv")
        result = recover_session(self.root, "test-session")
        self.assertEqual(result["verified_samples"], 0)
        self.assertTrue(any("模式字段无效" in e for e in result["errors"]))

    def test_wire_only_batches_are_saved(self):
        recorder = self.recorder()
        self.assertTrue(recorder.submit(batch(0, 0, wire=b"before-alignment")))
        self.assertTrue(recorder.submit(batch(0, 36, wire=b"aligned-frames")))
        status = recorder.finish()
        saved_wire = b"".join(p.read_bytes() for p in sorted((recorder.path / "wire").glob("*.bin")))
        self.assertEqual(saved_wire, b"before-alignmentaligned-frames")
        self.assertEqual(status["saved"], 36)
        self.assertEqual(status["wire_saved_bytes"], len(saved_wire))

    def test_queue_overflow_is_visible_and_drains_already_accepted_data(self):
        recorder = self.recorder(queue_size=1)
        entered, release = threading.Event(), threading.Event()
        original = recorder._commit_chunk

        def slow_commit(*args):
            entered.set()
            release.wait(5)
            return original(*args)

        recorder._commit_chunk = slow_commit
        try:
            self.assertTrue(recorder.submit(batch(0)))
            self.assertTrue(entered.wait(3))
            self.assertTrue(recorder.submit(batch(360)))
            self.assertFalse(recorder.submit(batch(720)))
        finally:
            release.set()
        status = recorder.finish()
        self.assertIn("队列已满", status["error"])
        self.assertEqual(status["saved"], 720)
        self.assertEqual(status["rejected"], 360)
        self.assertEqual(list_sessions(self.root)[0]["status"], "failed")

    def test_write_error_is_not_reported_as_saved(self):
        recorder = self.recorder()
        with patch.object(recorder, "_commit_chunk", side_effect=OSError("disk full")):
            self.assertTrue(recorder.submit(batch(0)))
            with self.assertLogs("recording", level="ERROR"):
                status = recorder.finish()
        self.assertIn("disk full", status["error"])
        self.assertEqual(status["saved"], 0)
        self.assertEqual(status["pending"], 360)
        self.assertFalse(recorder.submit(batch(360)))
        self.assertEqual(list_sessions(self.root)[0]["status"], "failed")

    def test_existing_session_is_not_overwritten(self):
        recorder = self.recorder()
        recorder.finish()
        original = (recorder.path / "session.json").read_bytes()
        other = Recorder(self.root, {"session_id": "test-session", "source": "demo"})
        self.recorders.append(other)
        with self.assertLogs("recording", level="ERROR"):
            with self.assertRaises(RuntimeError):
                other.start()
        other.finish()
        self.assertEqual((recorder.path / "session.json").read_bytes(), original)

    def test_final_report_failure_does_not_persist_complete_status(self):
        recorder = self.recorder()
        original = recording._write_atomic

        def failing_report(path, content):
            if Path(path).name == "quality.json":
                raise OSError("report disk full")
            return original(path, content)

        with patch.object(recording, "_write_atomic", side_effect=failing_report):
            self.assertTrue(recorder.submit(batch(0)))
            with self.assertLogs("recording", level="ERROR"):
                status = recorder.finish()
        self.assertEqual(status["saved"], 360)
        self.assertIn("report disk full", status["error"])
        self.assertEqual(list_sessions(self.root)[0]["status"], "failed")

    def test_closed_and_foreign_session_rejected(self):
        recorder = self.recorder()
        self.assertFalse(recorder.submit(batch(0, session_id="foreign-session")))
        status = recorder.finish()
        self.assertIn("会话或来源", status["error"])
        self.assertFalse(recorder.submit(batch(0)))

    def test_corruption_aborts_export_without_replacing_existing_file(self):
        recorder = self.recorder()
        recorder.submit(batch(0))
        recorder.finish()
        data_file = next((recorder.path / "chunks").glob("*.bin"))
        data_file.write_bytes(data_file.read_bytes()[:-1])
        output = self.root / "keep.csv"
        output.write_text("existing user export", encoding="utf-8")
        with self.assertRaises(ValueError):
            export_session(self.root, "test-session", output)
        self.assertEqual(output.read_text(encoding="utf-8"), "existing user export")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_recovery_restores_verified_block_before_index_commit(self):
        recorder = self.recorder()
        recorder.submit(batch(0))
        recorder.finish()
        with sqlite3.connect(recorder.path / "index.sqlite") as connection:
            connection.execute("DELETE FROM chunks")
            connection.execute("UPDATE sessions SET status='recording',sample_count=0")
        connection.close()
        metadata_path = recorder.path / "session.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.update(status="recording", sample_count=0)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        recovery = recover_session(self.root, "test-session")
        self.assertEqual(recovery["verified_samples"], 360)
        self.assertEqual(recovery["recovered_chunks"], 1)
        self.assertFalse(recovery["errors"])
        self.assertEqual(list_sessions(self.root)[0]["status"], "recovered_incomplete")
        export_session(self.root, "test-session", self.root / "recovered.csv")

    def test_recovery_refuses_live_writer(self):
        recorder = self.recorder()
        with self.assertRaises(RuntimeError):
            recover_session(self.root, "test-session")
        recorder.finish()

    def test_session_path_traversal_and_cross_session_export_blocked(self):
        with self.assertRaises(ValueError):
            Recorder(self.root, {"session_id": "../../elsewhere", "source": "demo"})
        recorder = self.recorder()
        recorder.finish()
        with self.assertRaises(ValueError):
            export_session(self.root, "test-session", self.root / "sessions" / "other" / "data.csv")


if __name__ == "__main__":
    unittest.main()
