"""Read-only storage estimates; failed measurements are never reported as zero."""
from __future__ import annotations

from pathlib import Path
import shutil
import stat


MIB = 1024 * 1024
# Raw samples, wire bytes, sidecars, SQLite/WAL and metadata at 360 Hz. This is
# intentionally above ordinary recording use, but not a guarantee about other
# programs writing to the same disk. Exporting a ZIP also requires extra space.
BASED_BYTES_PER_SECOND = 64 * 1024


def storage_status(data_dir, warn_mb=512, stop_mb=128):
    """Measure the nearest existing parent and reserve the stop threshold.

    Numeric fields are None when measurement fails. estimated_recording_seconds
    is only a budget estimate, excluding ZIP exports and unrelated disk writes.
    No directories or files are created by this function.
    """
    result = {
        "free_bytes": None,
        "total_bytes": None,
        "state": "unknown",
        "estimated_recording_seconds": None,
        "based_bytes_per_second": BASED_BYTES_PER_SECOND,
        "detail": "无法读取存储空间",
        "path": None,
        "reserved_bytes": None,
    }
    if (type(warn_mb) is not int or type(stop_mb) is not int
            or not 1 <= stop_mb < warn_mb):
        result["detail"] = "空间阈值无效：需要 1 ≤ 停止阈值 < 提醒阈值"
        return result
    try:
        path = Path(data_dir).expanduser().resolve()
        while True:
            try:
                mode = path.stat().st_mode
                break
            except FileNotFoundError:
                parent = path.parent
                if parent == path:
                    raise OSError("没有可读取的存储目录")
                path = parent
        if not stat.S_ISDIR(mode):
            path = path.parent
        usage = shutil.disk_usage(path)
        if usage.total <= 0 or usage.free < 0 or usage.free > usage.total:
            raise OSError("存储空间读数无效")
        reserved = stop_mb * MIB
        state = ("critical" if usage.free <= reserved else
                 "warning" if usage.free <= warn_mb * MIB else "ok")
        result.update({
            "free_bytes": usage.free,
            "total_bytes": usage.total,
            "state": state,
            "estimated_recording_seconds": max(0, (usage.free - reserved) // BASED_BYTES_PER_SECOND),
            "path": str(path),
            "reserved_bytes": reserved,
            "detail": {
                "ok": "存储空间充足",
                "warning": "剩余空间偏低，请导出并整理历史记录",
                "critical": "剩余空间达到停止阈值，请先释放空间",
            }[state] + "；可录时长为预留停止空间后的估算，不含导出副本",
        })
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result["detail"] = "无法读取存储空间：" + str(exc)
    return result
