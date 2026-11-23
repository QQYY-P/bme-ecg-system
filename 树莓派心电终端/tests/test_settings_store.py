"""Preference durability and validation, using only isolated directories."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from settings_store import DEFAULTS, load_settings, save_settings


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"

    def tearDown(self):
        self.temp.cleanup()

    def raw(self, content):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "settings.json"
        path.write_text(content, encoding="utf-8")
        return path

    def test_defaults_do_not_create_files(self):
        settings = load_settings(self.root)
        self.assertEqual(settings["display_seconds"], 5)
        self.assertEqual(settings["export_dir"], str(self.root / "exports"))
        self.assertFalse(self.root.exists())

    def test_roundtrip_and_partial_updates(self):
        saved = save_settings(self.root, {"display_seconds": 2, "vref": 3.0})
        self.assertEqual(load_settings(self.root), saved)
        updated = save_settings(self.root, {"display_mode": "both"})
        self.assertEqual(updated["display_seconds"], 2)
        self.assertEqual(updated["display_mode"], "both")
        self.assertEqual(updated["vref"], 3.0)

    def test_corrupt_json_is_not_overwritten(self):
        for value in ("{unfinished", "[]", "null", "x" * 70000):
            with self.subTest(value=value[:30]):
                path = self.raw(value)
                self.assertEqual(load_settings(self.root)["display_seconds"], 5)
                self.assertEqual(path.read_text(encoding="utf-8"), value)

    def test_invalid_fields_fall_back_but_valid_fields_survive(self):
        self.raw(json.dumps({"display_seconds": 20, "vref": float("nan"),
                             "auto_scale": "false", "display_mode": "raw",
                             "future_setting": True}))
        loaded = load_settings(self.root)
        self.assertEqual(loaded["display_seconds"], 5)
        self.assertEqual(loaded["vref"], 3.3)
        self.assertIs(loaded["auto_scale"], True)
        self.assertEqual(loaded["display_mode"], "raw")
        self.assertNotIn("future_setting", loaded)

    def test_conflicting_saved_space_limits_reset_pair(self):
        self.raw('{"low_space_stop_mb": 200, "low_space_warn_mb": 10}')
        loaded = load_settings(self.root)
        self.assertEqual((loaded["low_space_stop_mb"], loaded["low_space_warn_mb"]), (128, 512))

    def test_invalid_updates_never_replace_good_settings(self):
        save_settings(self.root, {"display_mode": "raw"})
        path = self.root / "settings.json"
        original = path.read_bytes()
        for update in ({"vref": float("inf")}, {"vref": False}, {"vref": 5.1},
                       {"display_seconds": True}, {"auto_scale": 1},
                       {"display_mode": "bad"}, {"export_dir": ""},
                       {"export_dir": "ab\0cd"}, {"low_space_stop_mb": 512},
                       {"low_space_warn_mb": 0}, {"sample_rate": 500}):
            with self.subTest(update=update):
                with self.assertRaises(ValueError):
                    save_settings(self.root, update)
                self.assertEqual(path.read_bytes(), original)

    def test_atomic_replace_failure_preserves_previous_and_cleans_temporary(self):
        save_settings(self.root, {"display_mode": "raw"})
        original = (self.root / "settings.json").read_bytes()
        with patch("settings_store.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                save_settings(self.root, {"display_mode": "both"})
        self.assertEqual((self.root / "settings.json").read_bytes(), original)
        self.assertEqual(list(self.root.glob(".settings-*.tmp")), [])

    def test_disabling_last_directory_keeps_fixed_default(self):
        custom = str(self.root / "my-exports")
        chosen = str(self.root / "temporary-destination")
        save_settings(self.root, {"export_dir": custom, "last_export_dir": chosen})
        self.assertEqual(load_settings(self.root)["last_export_dir"], chosen)
        saved = save_settings(self.root, {"remember_export_dir": False})
        self.assertEqual(saved["export_dir"], custom)
        self.assertEqual(saved["last_export_dir"], "")
        self.assertEqual(load_settings(self.root)["export_dir"], custom)
        self.assertEqual(load_settings(self.root)["last_export_dir"], "")

    def test_relative_directory_resolves_under_data_dir(self):
        saved = save_settings(self.root, {"export_dir": "exports-custom"})
        self.assertEqual(saved["export_dir"], str(self.root / "exports-custom"))

    def test_permission_failure_during_load_uses_defaults(self):
        with patch("settings_store.Path.open", side_effect=PermissionError("no permission")):
            self.assertEqual(load_settings(self.root)["display_seconds"], DEFAULTS["display_seconds"])

    def test_saved_payload_must_fit_read_limit(self):
        save_settings(self.root, {"display_seconds": 2})
        path = self.root / "settings.json"
        original = path.read_bytes()
        with patch("settings_store.MAX_SETTINGS_BYTES", len(original) + 10):
            with self.assertRaisesRegex(ValueError, "过长"):
                save_settings(self.root, {"last_export_dir": "a" * 50})
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
