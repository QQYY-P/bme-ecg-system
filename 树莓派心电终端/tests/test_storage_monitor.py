"""Space budget reads are read-only and preserve unknown/error distinctions."""
from collections import namedtuple
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storage_monitor import BASED_BYTES_PER_SECOND, MIB, storage_status


Usage = namedtuple("Usage", "total used free")


class StorageMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def usage(self, free):
        return Usage(8192 * MIB, 8192 * MIB - free, free)

    def test_thresholds_include_exact_boundaries(self):
        for free_mb, expected in ((600, "ok"), (512, "warning"), (129, "warning"),
                                  (128, "critical"), (0, "critical")):
            with self.subTest(free_mb=free_mb):
                with patch("storage_monitor.shutil.disk_usage", return_value=self.usage(free_mb * MIB)):
                    result = storage_status(self.root)
                self.assertEqual(result["state"], expected)
                self.assertEqual(result["free_bytes"], free_mb * MIB)

    def test_nonexistent_directory_uses_existing_parent_without_creating(self):
        target = self.root / "future" / "recordings"
        with patch("storage_monitor.shutil.disk_usage", return_value=self.usage(1024 * MIB)) as read:
            result = storage_status(target)
        read.assert_called_once_with(self.root)
        self.assertEqual(result["state"], "ok")
        self.assertFalse(target.parent.exists())

    def test_file_argument_measures_its_parent(self):
        target = self.root / "record.bin"
        target.write_bytes(b"test")
        with patch("storage_monitor.shutil.disk_usage", return_value=self.usage(1024 * MIB)) as read:
            storage_status(target)
        read.assert_called_once_with(self.root)

    def test_budget_reserves_stop_limit(self):
        free = 128 * MIB + 100 * BASED_BYTES_PER_SECOND
        with patch("storage_monitor.shutil.disk_usage", return_value=self.usage(free)):
            result = storage_status(self.root)
        self.assertEqual(result["estimated_recording_seconds"], 100)
        self.assertEqual(result["based_bytes_per_second"], BASED_BYTES_PER_SECOND)

    def test_read_failure_has_unknown_values(self):
        with patch("storage_monitor.shutil.disk_usage", side_effect=PermissionError("denied")):
            result = storage_status(self.root)
        self.assertEqual(result["state"], "unknown")
        for name in ("free_bytes", "total_bytes", "estimated_recording_seconds"):
            self.assertIsNone(result[name])

    def test_stat_permission_error_does_not_silently_measure_wrong_parent(self):
        with patch("storage_monitor.Path.stat", side_effect=PermissionError("denied")):
            with patch("storage_monitor.shutil.disk_usage") as read:
                result = storage_status(self.root)
        self.assertEqual(result["state"], "unknown")
        read.assert_not_called()

    def test_invalid_thresholds_do_not_measure(self):
        with patch("storage_monitor.shutil.disk_usage") as read:
            for warn, stop in ((128, 128), (10, 0), ("512", 128), (10, True)):
                self.assertEqual(storage_status(self.root, warn, stop)["state"], "unknown")
        read.assert_not_called()

    def test_impossible_disk_reading_is_unknown(self):
        with patch("storage_monitor.shutil.disk_usage", return_value=Usage(100, 1, 101)):
            result = storage_status(self.root)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["free_bytes"])


if __name__ == "__main__":
    unittest.main()
