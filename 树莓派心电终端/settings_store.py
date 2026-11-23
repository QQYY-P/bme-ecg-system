"""Validated, atomic user preferences; corrupt files are left intact on load."""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading


DEFAULTS = {
    "display_seconds": 5,
    "display_mode": "filtered",
    "auto_scale": True,
    "vref": 3.3,
    "classification_enabled": True,
    "export_dir": "",  # Resolved to data_dir / exports by load/save.
    "last_export_dir": "",
    "remember_export_dir": True,
    "export_include_raw": True,
    "low_space_warn_mb": 512,
    "low_space_stop_mb": 128,
    "auto_time_sync": True,
}
SETTINGS_FILENAME = "settings.json"
MAX_SETTINGS_BYTES = 64 * 1024
_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)
_BOOL_KEYS = {
    "auto_scale", "classification_enabled", "remember_export_dir",
    "export_include_raw", "auto_time_sync",
}


def _defaults(data_dir):
    result = dict(DEFAULTS)
    result["export_dir"] = str(Path(data_dir).expanduser().resolve() / "exports")
    return result


def _value(key, value, data_dir):
    if key in _BOOL_KEYS:
        if type(value) is not bool:
            raise ValueError(f"{key} 必须为开关值")
    elif key == "display_seconds":
        if type(value) is not int or value not in (2, 5, 10):
            raise ValueError("显示时长只能为 2、5 或 10 秒")
    elif key == "display_mode":
        if value not in ("filtered", "raw", "both"):
            raise ValueError("显示模式必须为 filtered、raw 或 both")
    elif key == "vref":
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not 0.1 <= value <= 5.0):
            raise ValueError("参考电压必须在 0.1 至 5.0 V 之间")
        value = float(value)
    elif key in ("low_space_warn_mb", "low_space_stop_mb"):
        if type(value) is not int or value < 1:
            raise ValueError("空间阈值必须为正整数（MiB）")
    elif key in ("export_dir", "last_export_dir"):
        if not isinstance(value, str) or "\0" in value or len(value) > 32767:
            raise ValueError("导出目录无效")
        if not value.strip():
            if key == "last_export_dir":
                return ""
            raise ValueError("默认导出目录不能为空")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(data_dir).expanduser().resolve() / path
        value = str(path.resolve())
    return value


def _validate(values, data_dir):
    unknown = set(values) - DEFAULTS.keys()
    if unknown:
        raise ValueError("不支持的设置项：" + ", ".join(sorted(map(str, unknown))))
    result = {key: _value(key, value, data_dir) for key, value in values.items()}
    if result["low_space_stop_mb"] >= result["low_space_warn_mb"]:
        raise ValueError("停止采集阈值必须小于空间提醒阈值")
    if not result["remember_export_dir"]:
        result["last_export_dir"] = ""
    return result


def load_settings(data_dir):
    """Return complete preferences, defaulting invalid fields without writing.

    Relative export directories resolve against data_dir. Unknown saved keys are
    ignored so a newer configuration can still be read by an older application.
    """
    result = _defaults(data_dir)
    path = Path(data_dir).expanduser().resolve() / SETTINGS_FILENAME
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SETTINGS_BYTES + 1)
        if len(raw) > MAX_SETTINGS_BYTES:
            raise ValueError("设置文件过大")
        values = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(values, dict):
            raise ValueError("设置文件必须为对象")
    except FileNotFoundError:
        return result
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        _LOG.warning("无法读取设置，使用默认值（保留原文件）：%s", exc)
        return result
    for key in DEFAULTS.keys() & values.keys():
        try:
            result[key] = _value(key, values[key], data_dir)
        except (TypeError, ValueError, OSError, OverflowError) as exc:
            _LOG.warning("忽略无效设置 %s：%s", key, exc)
    if result["low_space_stop_mb"] >= result["low_space_warn_mb"]:
        result["low_space_stop_mb"] = DEFAULTS["low_space_stop_mb"]
        result["low_space_warn_mb"] = DEFAULTS["low_space_warn_mb"]
        _LOG.warning("空间提醒/停止阈值冲突，使用默认阈值")
    if not result["remember_export_dir"]:
        result["last_export_dir"] = ""
    return result


def save_settings(data_dir, values):
    """Validate a partial update and atomically replace settings.json.

    The fixed export_dir is always saved. last_export_dir is cleared when the
    user disables remembering their last destination. A failed validation never
    changes the saved file; callers receive I/O errors rather than false success.
    """
    if not isinstance(values, dict):
        raise ValueError("设置必须为对象")
    with _LOCK:
        unknown = set(values) - DEFAULTS.keys()
        if unknown:
            raise ValueError("不支持的设置项：" + ", ".join(sorted(map(str, unknown))))
        candidate = load_settings(data_dir)
        candidate.update(values)
        result = _validate(candidate, data_dir)
        payload = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
                   + "\n").encode("utf-8")
        if len(payload) > MAX_SETTINGS_BYTES:
            raise ValueError("设置内容过长，请缩短导出目录")
        directory = Path(data_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / SETTINGS_FILENAME
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=directory,
                                             prefix=".settings-", suffix=".tmp",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
            if os.name != "nt":
                descriptor = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return result
