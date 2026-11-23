"""Recoverable history management and complete, verified session exports.

All mutations take a per-data-directory OS lock and the recorder's writer lock.
Only a selected, stopped session can be moved or exported. No operation follows
symbolic links/junctions, and permanent deletion is restricted to the trash.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import zipfile

from recording import (FS, SESSION_ID, _SessionLock, _json_bytes,
                       _readonly_connection, _sync_directory, export_session,
                       utc_now)

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _plain_path(path):
    """Check lexical ancestors before resolve(), which would hide a link."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("不能操作符号链接或目录联接")
    return path


def _root(data_dir):
    root = _plain_path(data_dir)
    if root == root.parent:
        raise ValueError("数据目录不能是磁盘根目录")
    return root


def _directory(root, sid, *, trashed=False):
    if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
        raise ValueError("无效会话编号")
    directory = _plain_path(root / ("trash" if trashed else "sessions") / sid)
    if not directory.is_dir():
        raise ValueError("会话不存在")
    return directory


def _files(directory):
    """Reject links anywhere in a session, including dangling links."""
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories + files:
            path = _plain_path(Path(parent) / name)
            if name in files:
                if not path.is_file():
                    raise ValueError("会话中包含非普通文件")
                yield path


@contextmanager
def _management_lock(root):
    root = _plain_path(root)
    if not root.is_dir():
        raise ValueError("数据目录不存在")
    with _LOCKS_GUARD:
        mutex = _LOCKS.setdefault(os.path.normcase(str(root)), threading.RLock())
    with mutex:
        lock_path = _plain_path(root / ".session-management.lock")
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("另一个程序正在管理历史记录，请稍后重试") from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _metadata(directory):
    metadata = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("会话信息格式错误")
    with _readonly_connection(directory / "index.sqlite") as connection:
        row = connection.execute(
            "SELECT metadata_json,status,sample_count FROM sessions WHERE session_id=?",
            (directory.name,)).fetchone()
    if not row:
        raise ValueError("数据库中没有该会话")
    indexed = json.loads(row[0])
    if not isinstance(indexed, dict) or indexed.get("session_id") != directory.name:
        raise ValueError("会话编号与数据库不一致")
    metadata.update(indexed)
    metadata.update(status=row[1], sample_count=row[2])
    return metadata


def _time_trusted(metadata):
    info = metadata.get("time_info")
    return isinstance(info, dict) and info.get("trusted") is True


def list_history(data_dir, trashed=False, *, include_size=True):
    """List unreadable entries too; old records never imply a trusted clock."""
    root = _root(data_dir)
    container = _plain_path(root / ("trash" if trashed else "sessions"))
    if not container.exists():
        return []
    result = []
    for directory in container.iterdir():
        if not SESSION_ID.fullmatch(directory.name):
            continue
        if not directory.is_dir() and not directory.is_symlink():
            continue
        item = {"session_id": directory.name, "source": "unknown", "started_utc": "",
                "status": "unreadable", "sample_count": 0, "path": str(directory),
                "error": None, "size_bytes": 0, "time_trusted": False,
                "trashed": bool(trashed)}
        try:
            _plain_path(directory)
            if include_size:
                item["size_bytes"] = sum(p.stat().st_size for p in _files(directory))
            metadata = _metadata(directory)
            item.update(source=metadata.get("source", "unknown"),
                        started_utc=metadata.get("started_utc", ""),
                        status=metadata.get("status", "incomplete"),
                        sample_count=metadata.get("sample_count", 0),
                        cloud_owner_id=metadata.get('cloud_owner_id'),cloud_owner_name=metadata.get('cloud_owner_name'),
                        error=metadata.get("error"), time_trusted=_time_trusted(metadata))
            # Reading a WAL database can create a shared-memory sidecar.
            if include_size:
                item["size_bytes"] = sum(p.stat().st_size for p in _files(directory))
            else:
                item["size_bytes"] = None
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            item["error"] = str(exc)
        result.append(item)
    return sorted(result, key=lambda item: str(item["started_utc"]), reverse=True)


def _move(data_dir, sid, *, restore):
    root = _root(data_dir)
    with _management_lock(root):
        source = _directory(root, sid, trashed=restore)
        parent = _plain_path(root / ("sessions" if restore else "trash"))
        target = _plain_path(parent / sid)
        if target.exists():
            raise ValueError("目标位置已存在同名会话，未覆盖任何数据")
        _plain_path(source / "writer.lock")
        lock = _SessionLock(source)
        try:
            # Reject a live recorder before scanning its atomically replaced
            # checkpoint files; keep link checks before opening the lock.
            list(_files(source))
            parent.mkdir(exist_ok=True)
            # Windows cannot rename a directory containing an open lock file.
            # The directory-wide mutex remains held across close and rename.
            if os.name == "nt":
                lock.close()
            os.rename(source, target)
            _sync_directory(source.parent)
            _sync_directory(parent)
        finally:
            lock.close()
    return str(target)


def trash_session(data_dir, sid):
    """Move exactly one stopped session to data/trash, without erasing it."""
    return _move(data_dir, sid, restore=False)


def restore_session(data_dir, sid):
    """Restore the original id; an existing session is never overwritten."""
    return _move(data_dir, sid, restore=True)


def purge_session(data_dir, sid):
    """Permanently remove only this explicitly selected trash entry."""
    root = _root(data_dir)
    with _management_lock(root):
        directory = _directory(root, sid, trashed=True)
        list(_files(directory))
        lock = _SessionLock(directory)
        try:
            if os.name == "nt":
                lock.close()
            shutil.rmtree(directory)
            _sync_directory(directory.parent)
        finally:
            lock.close()
    return str(directory)


def _export_target(root, output_path, suffix):
    target = _plain_path(output_path)
    if target.suffix.lower() != suffix:
        raise ValueError(f"导出文件应使用 {suffix} 后缀")
    if any(target.is_relative_to(root / name) for name in ("sessions", "trash")):
        raise ValueError("导出位置不能位于会话或回收站目录")
    if target.exists() and (not target.is_file() or target.stat().st_nlink > 1):
        raise ValueError("导出目标不是独立普通文件")
    return target


@contextmanager
def _export_guard(data_dir, sid, output_path, suffix):
    root = _root(data_dir)
    with _management_lock(root):
        directory = _directory(root, sid)
        target = _export_target(root, output_path, suffix)
        lock = _SessionLock(directory)
        try:
            metadata = _metadata(directory)
            if metadata.get("status") == "recording":
                raise RuntimeError("会话尚未正常结束，请先停止或恢复记录再导出")
            # Acquire the writer lock before enumerating transient files.
            # A live recorder atomically renames checkpoints during this scan.
            list(_files(directory))
            target.parent.mkdir(parents=True, exist_ok=True)
            yield root, directory, target, metadata
        finally:
            lock.close()


def export_samples(data_dir, sid, output_path):
    """Safe compatibility CSV export, with the same stopped-session guards."""
    with _export_guard(data_dir, sid, output_path, ".csv") as (root, _, target, _):
        with tempfile.TemporaryDirectory(prefix=".ecg-export-", dir=target.parent) as temporary:
            staged = Path(temporary) / "samples.csv"
            export_session(root, sid, staged)
            _export_target(root, target, ".csv")
            os.replace(staged, target)
            _sync_directory(target.parent)
    return str(target)


def _r_index(detail):
    value = detail.get("r_sample") if isinstance(detail, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _events_and_beats(connection, stage, sid):
    wanted = set()
    with (stage / "events.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["session_id", "event_id", "utc", "kind", "detail_json"])
        for event_id, utc, kind, detail_json in connection.execute(
                "SELECT event_id,utc,kind,detail_json FROM events ORDER BY event_id"):
            writer.writerow([sid, event_id, utc, kind, detail_json])
            if kind == "cardioedge_prediction":
                try:
                    index = _r_index(json.loads(detail_json))
                except (ValueError, TypeError):
                    index = None
                if index is not None:
                    wanted.add(index)
    present = {}
    with (stage / "samples.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            index = int(row["sample_id"])
            if index in wanted:
                present[index] = int(row["segment_id"])
    fields = ["session_id", "event_id", "utc", "prediction_id", "sample_id", "r_sample",
              "r_time_s", "segment_id", "alignment_status", "label", "score_N", "score_S",
              "score_V", "classified_at_sample", "source", "mode", "rr_previous_s",
              "rr_next_s", "sample_latency_ms", "inference_ms", "queue_wait_ms",
              "normalization_std_v", "quantization_clipped_fraction", "research_only",
              "requires_review", "detail_json"]
    with (stage / "beats.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for event_id, utc, raw in connection.execute(
                "SELECT event_id,utc,detail_json FROM events WHERE kind=? ORDER BY event_id",
                ("cardioedge_prediction",)):
            try:
                detail = json.loads(raw)
            except (ValueError, TypeError):
                detail = {}
            if not isinstance(detail, dict):
                detail = {}
            index = _r_index(detail)
            aligned = index in present
            state = "matched" if aligned else "missing_sample"
            if aligned and detail.get("segment_id") not in (None, present[index]):
                state = "segment_mismatch"
            if index is None:
                state = "invalid_r_sample"
            row = {key: detail.get(key, "") for key in fields}
            scores = detail.get("scores")
            if not isinstance(scores, dict):
                scores = {}
            row.update(session_id=sid, event_id=event_id, utc=utc,
                       sample_id=index if aligned else "", r_sample=index if index is not None else "",
                       r_time_s=f"{index / FS:.9f}" if index is not None else "",
                       alignment_status=state, detail_json=raw,
                       score_N=scores.get("N", ""), score_S=scores.get("S", ""),
                       score_V=scores.get("V", ""))
            writer.writerow(row)


_README = """心电完整记录导出包 / ECG session bundle

samples.csv：全部已提交采样点，与 sample_id 对齐；voltage_v 是 ADC 参考电压换算结果，
不是经过滤波或按模拟前端增益还原的人体心电电压。无效点的电压留空。
beats.csv：仅包含实际保存的 cardioedge_prediction 事件。r_sample 是原始采样 sample_id，
sample_id 仅在原始样本存在时填写；alignment_status 标记跨文件对齐结果。
未得到分类的心搏不会自动填为 N；N/S/V 分别是模型的正常、室上性和室性类别。
未分类样本并不等于正常。分类仅供研究参考，不能作为临床诊断结论。
events.csv：完整事件流水，detail_json 原样保留，包括信号质量、错误、模式切换等。
session.json：原始采集、预处理与模型参数，状态和样本数以 SQLite 为准。
quality.json：保存时的质量报告（旧记录没有此文件时可缺省）。
raw/（如选择包含）：原始 chunks、wire、session.json 和 SQLite backup 数据库快照，
保留 WAL 中已提交事务，未将临时文件当作完整采样。CSV 不包含未保存的在线滤波波形。
manifest.json：每个归档文件的 SHA-256 和字节数（不含 manifest 自身）。
UTC 时间只有 time_info.trusted=true 时标为可信；旧记录或手动校时不视为已同步。
时间轴按 360 Hz 接收采样编号计算，发生丢包、断流或模式改变时请结合 segment_id 和事件分析。
"""


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_bundle(data_dir, sid, output_path, include_raw=True):
    """Create a verified ZIP, preserving the old target if any step fails."""
    with _export_guard(data_dir, sid, output_path, ".zip") as (root, directory, target, metadata):
        with tempfile.TemporaryDirectory(prefix=".ecg-export-", dir=target.parent) as temporary:
            stage = Path(temporary)
            export_session(root, sid, stage / "samples.csv")
            (stage / "session.json").write_bytes(_json_bytes(metadata))
            (stage / "README.txt").write_text(_README, encoding="utf-8")
            quality = directory / "quality.json"
            if quality.exists():
                shutil.copyfile(quality, stage / "quality.json")
            with _readonly_connection(directory / "index.sqlite") as connection:
                connection.execute("BEGIN")
                _events_and_beats(connection, stage, sid)
                if include_raw:
                    raw = stage / "raw"
                    raw.mkdir()
                    # backup() includes committed pages still in the source WAL.
                    with closing(sqlite3.connect(raw / "index.sqlite")) as backup:
                        connection.backup(backup)
                        backup.execute("PRAGMA journal_mode=DELETE")
                    shutil.copyfile(directory / "session.json", raw / "session.json")
                    for name in ("chunks", "wire"):
                        source = directory / name
                        if source.is_dir():
                            shutil.copytree(source, raw / name)
            entries = sorted(p for p in stage.rglob("*") if p.is_file())
            manifest = {"schema_version": 1, "session_id": sid, "created_utc": utc_now(),
                        "include_raw": bool(include_raw), "time_trusted": _time_trusted(metadata),
                        "session_status": metadata["status"], "sample_count": metadata["sample_count"],
                        "files": [{"path": p.relative_to(stage).as_posix(),
                                   "size_bytes": p.stat().st_size, "sha256": _hash_file(p)} for p in entries]}
            manifest_path = stage / "manifest.json"
            manifest_path.write_bytes(_json_bytes(manifest))
            archive = stage / "bundle.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=1, allowZip64=True) as bundle:
                for path in entries + [manifest_path]:
                    bundle.write(path, path.relative_to(stage).as_posix())
            with archive.open("r+b") as stream:
                os.fsync(stream.fileno())
            _export_target(root, target, ".zip")
            os.replace(archive, target)
            _sync_directory(target.parent)
    return str(target)
