"""Session management checks use disposable synthetic records only."""
import csv
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from contracts import SampleBatch, SamplePoint
from recording import Recorder
import session_management as management


class SessionManagementTests(unittest.TestCase):
    def test_upload_selector_reads_metadata_without_walking_raw_files(self):
        self.recorder()
        with patch.object(management, '_files', side_effect=AssertionError('raw scan')):
            rows = management.list_history(self.root, include_size=False)
        self.assertEqual(rows[0]['sample_count'], 360)
        self.assertIsNone(rows[0]['size_bytes'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix=".test-session-management-", dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.finish()
        self.temp.cleanup()

    def recorder(self, sid="test-session", *, finish=True, trusted=None):
        metadata = {"session_id": sid, "source": "serial", "vref": 3.3,
                    "preprocessing": {"notch_hz": 50, "lowpass_hz": 40},
                    "classification": {"weights_sha256": "test-only"}}
        if trusted is not None:
            metadata["time_info"] = {"trusted": trusted, "source": "test"}
        recorder = Recorder(self.root, metadata).start()
        self.recorders.append(recorder)
        points = tuple(SamplePoint(i, 2, i, i % 256, 2048, 0, 0) for i in range(360))
        self.assertTrue(recorder.submit(SampleBatch(
            sid, "serial", points, 1.0, "2026-10-29T02:00:00Z", b"UART-original-bytes")))
        self.assertTrue(recorder.add_event("signal_quality_changed", {
            "signal_state": "interference", "quality_metrics": {"noise_ratio": 0.6}}))
        self.assertTrue(recorder.add_event("model_error", {"error": "synthetic error"}))
        self.assertTrue(recorder.add_event("cardioedge_prediction", {
            "r_sample": 0, "segment_id": 2, "label": "V", "prediction_id": 1,
            "scores": {"N": 0.1, "S": 0.2, "V": 0.7}, "source": "serial",
            "classified_at_sample": 100, "research_only": True}))
        self.assertTrue(recorder.add_event("cardioedge_prediction", {
            "r_sample": 90, "segment_id": 9, "prediction_id": 2}))
        self.assertTrue(recorder.add_event("cardioedge_prediction", {
            "r_sample": 719, "segment_id": 2, "label": "S", "prediction_id": 3}))
        if finish:
            self.assertIsNone(recorder.finish()["error"])
        return recorder

    @staticmethod
    def csv_rows(bundle, name):
        return list(csv.DictReader(io.StringIO(bundle.read(name).decode("utf-8-sig"))))

    def test_complete_bundle_preserves_samples_events_parameters_and_hashes(self):
        recorder = self.recorder(trusted=True)
        output = management.export_bundle(self.root, recorder.session_id, self.root / "out.zip")
        with zipfile.ZipFile(output) as bundle:
            self.assertIsNone(bundle.testzip())
            self.assertTrue({"samples.csv", "beats.csv", "events.csv", "session.json",
                             "quality.json", "README.txt", "manifest.json", "raw/index.sqlite",
                             "raw/chunks/00000000.bin", "raw/wire/00000000.bin"} <= set(bundle.namelist()))
            self.assertEqual(bundle.read("raw/wire/00000000.bin"), b"UART-original-bytes")
            samples = self.csv_rows(bundle, "samples.csv")
            self.assertEqual(len(samples), 360)
            self.assertEqual(samples[-1]["sample_id"], "359")
            metadata = json.loads(bundle.read("session.json"))
            self.assertEqual(metadata["preprocessing"]["notch_hz"], 50)
            self.assertEqual(metadata["classification"]["weights_sha256"], "test-only")
            events = self.csv_rows(bundle, "events.csv")
            quality = next(row for row in events if row["kind"] == "signal_quality_changed")
            self.assertEqual(json.loads(quality["detail_json"])["quality_metrics"]["noise_ratio"], 0.6)
            self.assertTrue(any(row["kind"] == "model_error" for row in events))
            manifest = json.loads(bundle.read("manifest.json"))
            self.assertTrue(manifest["time_trusted"])
            self.assertEqual(manifest["sample_count"], 360)
            self.assertEqual({row["path"] for row in manifest["files"]},
                             set(bundle.namelist()) - {"manifest.json"})
            for entry in manifest["files"]:
                data = bundle.read(entry["path"])
                self.assertEqual(entry["size_bytes"], len(data))
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())

    def test_beats_align_to_raw_ids_and_never_invent_normal_labels(self):
        self.recorder()
        path = management.export_bundle(self.root, "test-session", self.root / "out.zip", False)
        with zipfile.ZipFile(path) as bundle:
            rows = self.csv_rows(bundle, "beats.csv")
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["sample_id"], "0")
            self.assertEqual(rows[0]["r_sample"], "0")
            self.assertEqual(rows[0]["alignment_status"], "matched")
            self.assertEqual(rows[0]["label"], "V")
            self.assertEqual(rows[0]["score_N"], "0.1")
            self.assertEqual(rows[1]["label"], "")
            self.assertEqual(rows[1]["alignment_status"], "segment_mismatch")
            self.assertEqual(rows[2]["sample_id"], "")
            self.assertEqual(rows[2]["r_sample"], "719")
            self.assertEqual(rows[2]["alignment_status"], "missing_sample")
            self.assertFalse(any(name.startswith("raw/") for name in bundle.namelist()))

    def test_database_backup_includes_committed_wal_events(self):
        recorder = self.recorder()
        with closing(sqlite3.connect(recorder.path / "index.sqlite")) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO events(utc,kind,detail_json) VALUES(?,?,?)",
                           ("later", "wal_only_event", '{"kept":true}'))
            writer.commit()
            self.assertTrue((recorder.path / "index.sqlite-wal").stat().st_size > 0)
            output = management.export_bundle(self.root, "test-session", self.root / "wal.zip")
            with zipfile.ZipFile(output) as bundle:
                database = self.root / "verify.sqlite"
                database.write_bytes(bundle.read("raw/index.sqlite"))
                self.assertTrue(any(row["kind"] == "wal_only_event"
                                    for row in self.csv_rows(bundle, "events.csv")))
            with closing(sqlite3.connect(database)) as verification:
                self.assertEqual(verification.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(verification.execute(
                    "SELECT detail_json FROM events WHERE kind='wal_only_event'").fetchone()[0], '{"kept":true}')

    def test_history_size_and_clock_trust_require_explicit_boolean(self):
        first = self.recorder("old-session")
        self.recorder("trusted-session", trusted=True)
        self.recorder("manual-session", trusted=False)
        self.recorder("string-session", trusted="true")
        rows = {row["session_id"]: row for row in management.list_history(self.root)}
        self.assertFalse(rows["old-session"]["time_trusted"])
        self.assertTrue(rows["trusted-session"]["time_trusted"])
        self.assertFalse(rows["manual-session"]["time_trusted"])
        self.assertFalse(rows["string-session"]["time_trusted"])
        self.assertEqual(rows["old-session"]["sample_count"], 360)
        self.assertEqual(rows["old-session"]["size_bytes"],
                         sum(p.stat().st_size for p in first.path.rglob("*") if p.is_file()))

    def test_trash_restore_and_purge_only_selected_session(self):
        selected = self.recorder()
        other = self.recorder("keep-session")
        original_raw = (selected.path / "chunks/00000000.bin").read_bytes()
        trashed = Path(management.trash_session(self.root, "test-session"))
        self.assertEqual(trashed, self.root / "trash/test-session")
        self.assertFalse(selected.path.exists())
        self.assertTrue(other.path.exists())
        rows = management.list_history(self.root, trashed=True)
        self.assertEqual(rows[0]["session_id"], "test-session")
        self.assertTrue(rows[0]["trashed"])
        self.assertEqual(Path(management.restore_session(self.root, "test-session")), selected.path)
        self.assertEqual((selected.path / "chunks/00000000.bin").read_bytes(), original_raw)
        with self.assertRaises(ValueError):
            management.purge_session(self.root, "test-session")
        management.trash_session(self.root, "test-session")
        self.assertEqual(management.purge_session(self.root, "test-session"), str(trashed))
        self.assertFalse(trashed.exists())
        self.assertTrue(other.path.exists())

    def test_restore_collision_keeps_both_directories(self):
        self.recorder()
        trashed = Path(management.trash_session(self.root, "test-session"))
        original = self.root / "sessions/test-session"
        original.mkdir()
        with self.assertRaisesRegex(ValueError, "同名"):
            management.restore_session(self.root, "test-session")
        self.assertTrue(trashed.exists())
        self.assertEqual(list(original.iterdir()), [])

    def test_active_session_rejects_export_and_trash(self):
        recorder = self.recorder(finish=False)
        operations = [lambda: management.trash_session(self.root, "test-session"),
                      lambda: management.export_bundle(self.root, "test-session", self.root / "out.zip"),
                      lambda: management.export_samples(self.root, "test-session", self.root / "out.csv")]
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(RuntimeError, "仍在使用"):
                    operation()
        self.assertTrue(recorder.path.is_dir())

    def test_corrupt_history_remains_visible_and_can_be_trashed(self):
        recorder = self.recorder()
        (recorder.path / "session.json").write_text("not json", encoding="utf-8")
        rows = management.list_history(self.root)
        self.assertEqual(rows[0]["status"], "unreadable")
        self.assertTrue(rows[0]["error"])
        self.assertGreater(rows[0]["size_bytes"], 0)
        self.assertFalse(rows[0]["time_trusted"])
        with self.assertRaises(ValueError):
            management.export_bundle(self.root, "test-session", self.root / "out.zip")
        management.trash_session(self.root, "test-session")
        management.purge_session(self.root, "test-session")

    def test_paths_and_extensions_cannot_overwrite_recordings_or_config(self):
        recorder = self.recorder()
        for sid in ("../test-session", "..", "", "test/session", "C:\\evil"):
            with self.subTest(sid=sid):
                with self.assertRaises(ValueError):
                    management.trash_session(self.root, sid)
        for target in (recorder.path / "session.json", recorder.path / "exports/out.zip",
                       self.root / "trash/other/out.zip", self.root / "settings.json"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    management.export_bundle(self.root, "test-session", target)
        with self.assertRaises(ValueError):
            management.export_samples(self.root, "test-session", recorder.path / "exports/out.csv")
        self.assertTrue((recorder.path / "session.json").is_file())

    def test_corrupt_chunk_leaves_existing_target_untouched(self):
        recorder = self.recorder()
        (recorder.path / "chunks/00000000.bin").write_bytes(b"broken")
        target = self.root / "out.zip"
        target.write_bytes(b"previous export")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            management.export_bundle(self.root, "test-session", target)
        self.assertEqual(target.read_bytes(), b"previous export")
        self.assertFalse(list(self.root.glob(".ecg-export-*")))

    def test_safe_csv_has_complete_samples(self):
        self.recorder()
        output = management.export_samples(self.root, "test-session", self.root / "exports/full.csv")
        with open(output, encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 360)

    def test_csv_failure_preserves_old_target_and_no_partial_export(self):
        recorder = self.recorder()
        (recorder.path / "chunks/00000000.bin").write_bytes(b"broken")
        output = self.root / "out.csv"
        output.write_bytes(b"old csv")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            management.export_samples(self.root, "test-session", output)
        self.assertEqual(output.read_bytes(), b"old csv")
        self.assertFalse(list(self.root.glob(".ecg-export-*")))

    def test_unfinished_recording_requires_recovery_before_export(self):
        recorder = self.recorder()
        with closing(sqlite3.connect(recorder.path / "index.sqlite")) as connection:
            connection.execute("UPDATE sessions SET status='recording'")
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "恢复"):
            management.export_bundle(self.root, "test-session", self.root / "out.zip")

    def test_malformed_prediction_details_preserved_without_invented_class(self):
        recorder = self.recorder()
        with closing(sqlite3.connect(recorder.path / "index.sqlite")) as connection:
            connection.execute("INSERT INTO events(utc,kind,detail_json) VALUES(?,?,?)",
                               ("later", "cardioedge_prediction", "invalid-json"))
            connection.commit()
        output = management.export_bundle(self.root, "test-session", self.root / "out.zip", False)
        with zipfile.ZipFile(output) as bundle:
            row = self.csv_rows(bundle, "beats.csv")[-1]
            self.assertEqual(row["detail_json"], "invalid-json")
            self.assertEqual(row["label"], "")
            self.assertEqual(row["alignment_status"], "invalid_r_sample")

    def test_management_operations_serialize_export_before_delete(self):
        self.recorder()
        entered = threading.Event()
        release = threading.Event()
        moved = threading.Event()
        failures = []
        original_export = management.export_session

        def slow_export(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timeout")
            return original_export(*args)

        def run_export():
            try:
                management.export_bundle(self.root, "test-session", self.root / "out.zip")
            except Exception as exc:
                failures.append(exc)

        def run_trash():
            try:
                management.trash_session(self.root, "test-session")
                moved.set()
            except Exception as exc:
                failures.append(exc)

        with patch.object(management, "export_session", side_effect=slow_export):
            export_thread = threading.Thread(target=run_export)
            trash_thread = threading.Thread(target=run_trash)
            export_thread.start()
            self.assertTrue(entered.wait(5))
            trash_thread.start()
            self.assertFalse(moved.wait(0.1))
            release.set()
            export_thread.join(5)
            trash_thread.join(5)
        self.assertFalse(failures)
        self.assertTrue(moved.is_set())
        self.assertTrue((self.root / "out.zip").exists())

    def test_session_symlink_is_listed_as_unreadable_and_never_followed(self):
        self.recorder("real-session")
        linked = self.root / "sessions/linked-session"
        try:
            linked.symlink_to(self.root / "sessions/real-session", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        rows = {row["session_id"]: row for row in management.list_history(self.root)}
        self.assertEqual(rows["linked-session"]["status"], "unreadable")
        with self.assertRaises(ValueError):
            management.trash_session(self.root, "linked-session")
        self.assertTrue((self.root / "sessions/real-session/session.json").is_file())

    def test_nested_link_blocks_export_and_purge_without_touching_target(self):
        self.recorder()
        external = self.root / "keep.txt"
        external.write_text("keep")
        linked = self.root / "sessions/test-session/chunks/linked.bin"
        try:
            linked.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(ValueError):
            management.export_bundle(self.root, "test-session", self.root / "out.zip")
        linked.unlink()
        management.trash_session(self.root, "test-session")
        (self.root / "trash/test-session/chunks/linked.bin").symlink_to(external)
        with self.assertRaises(ValueError):
            management.purge_session(self.root, "test-session")
        self.assertEqual(external.read_text(), "keep")

    def test_hardlinked_export_target_rejected(self):
        self.recorder()
        external = self.root / "keep.zip"
        external.write_bytes(b"keep")
        target = self.root / "out.zip"
        try:
            os.link(external, target)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        with self.assertRaises(ValueError):
            management.export_bundle(self.root, "test-session", target)
        self.assertEqual(external.read_bytes(), b"keep")

    def test_missing_old_quality_report_is_optional(self):
        recorder = self.recorder()
        (recorder.path / "quality.json").unlink()
        output = management.export_bundle(self.root, "test-session", self.root / "out.zip")
        with zipfile.ZipFile(output) as bundle:
            self.assertNotIn("quality.json", bundle.namelist())

    def test_link_guards_without_platform_link_creation_permission(self):
        recorder = self.recorder()
        raw = recorder.path / "chunks/00000000.bin"
        actual = Path.is_symlink

        def linked(path):
            return path == raw or actual(path)

        with patch.object(Path, "is_symlink", linked):
            with self.assertRaisesRegex(ValueError, "符号链接"):
                management.export_bundle(self.root, "test-session", self.root / "out.zip")
            with self.assertRaisesRegex(ValueError, "符号链接"):
                management.trash_session(self.root, "test-session")
        self.assertTrue(raw.is_file())

    def test_output_parent_link_is_rejected(self):
        self.recorder()
        parent = self.root / "exports"
        parent.mkdir()
        actual = Path.is_symlink

        def linked(path):
            return path == parent or actual(path)

        with patch.object(Path, "is_symlink", linked):
            with self.assertRaisesRegex(ValueError, "符号链接"):
                management.export_bundle(self.root, "test-session", parent / "out.zip")
        self.assertFalse((parent / "out.zip").exists())


if __name__ == "__main__":
    unittest.main()
