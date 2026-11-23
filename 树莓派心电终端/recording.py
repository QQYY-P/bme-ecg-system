"""Durable, bounded, whole-session ECG recording (standard library only).

The receiver never writes files: submit() either accepts a batch immediately or
returns False with an explicit error.  A single writer commits binary files
before their SQLite index.  Export reads every committed block, not the GUI
ring buffer.  The format is versioned little-endian data, never pickle.
"""
from __future__ import annotations

import csv
from contextlib import closing, contextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import sqlite3
import struct
import threading
import time
from datetime import datetime, timezone


FS = 360
RAW_SCHEMA_VERSION = 2
QUALITY_SCHEMA_VERSION = 3
INVALID_MARKER_NOTE = (
    "invalid_marker_samples 统计 ADC=0xFFFF 或 INVALID 标志的无效样本；"
    "协议不区分导联脱落、SW2 模式切换等具体原因。"
    "旧版 lead_off_samples 是已弃用的同类计数名称，不能作为确定导联脱落的计数。"
)
MODE_NOTE = (
    "real_mode_samples/simulated_mode_samples 仅统计每点 mode=0/1；"
    "本地模拟也使用 mode=1，不能仅凭此计数认定为板端数据。"
    "旧记录的模式未知；模式变化和重同步与序号异常分别计数。"
)
MAGIC = b"ECGRAW01"
HEADER = struct.Struct("<8sHHII")  # magic, version, row size, Hz, count
ROW_V1 = struct.Struct("<QIIBHB")  # id, segment, index, sequence, ADC, flags
ROW = struct.Struct("<QIIBHBB")  # v2 appends mode: 0 real, 1 simulated, 255 unknown
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
LOG = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _session_path(data_dir, session_id):
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        raise ValueError("无效会话编号")
    return Path(data_dir).expanduser().resolve() / "sessions" / session_id


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n").encode("utf-8")


def _sync_directory(directory):
    # Windows has no portable directory fsync.  File fsync still precedes rename.
    if os.name != "nt":
        fd = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write_atomic(path, content):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


class _SessionLock:
    """OS-released lock, so recovery cannot change an active recorder."""

    def __init__(self, directory):
        self.stream = (Path(directory) / "writer.lock").open("a+b")
        try:
            self.stream.seek(0, os.SEEK_END)
            if not self.stream.tell():
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.stream.close()
            raise RuntimeError("该会话仍在使用，不能恢复或改写") from None

    def close(self):
        if self.stream.closed:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


def _schema(connection):
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        PRAGMA user_version=1;
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL,
            status TEXT NOT NULL, sample_count INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id INTEGER PRIMARY KEY, descriptor_json TEXT NOT NULL,
            sample_count INTEGER NOT NULL, first_sample_id INTEGER,
            last_sample_id INTEGER);
        CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, utc TEXT NOT NULL,
            kind TEXT NOT NULL, detail_json TEXT NOT NULL);
    """)


@contextmanager
def _readonly_connection(path):
    # as_uri handles drive letters, Chinese paths, spaces and URI metacharacters.
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield connection
    finally:
        connection.close()


def _chunk_rows(directory, descriptor):
    chunk_id = descriptor["chunk_id"]
    if not isinstance(chunk_id, int) or chunk_id < 0:
        raise ValueError("无效数据块编号")
    relative = f"chunks/{chunk_id:08d}.bin"
    wire_relative = f"wire/{chunk_id:08d}.bin"
    if descriptor["file"] != relative or descriptor["wire_file"] != wire_relative:
        raise ValueError("数据块路径不符合格式")
    raw = (Path(directory) / relative).read_bytes()
    if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise ValueError(f"采样块 {chunk_id} 校验失败")
    wire = (Path(directory) / wire_relative).read_bytes()
    if (len(wire) != descriptor["wire_bytes"]
            or hashlib.sha256(wire).hexdigest() != descriptor["wire_sha256"]):
        raise ValueError(f"串口原始块 {chunk_id} 校验失败")
    if len(raw) < HEADER.size:
        raise ValueError("采样块头不完整")
    magic, version, row_size, fs, count = HEADER.unpack_from(raw)
    row_format = {1: ROW_V1, 2: ROW}.get(version)
    if (magic != MAGIC or row_format is None or row_size != row_format.size or fs != FS
            or count != descriptor["sample_count"]
            or len(raw) != HEADER.size + count * row_size):
        raise ValueError("采样块格式或长度错误")
    rows = list(row_format.iter_unpack(raw[HEADER.size:]))
    if version == 1:
        rows = [row + (None,) for row in rows]
    else:
        if any(row[-1] not in (0, 1, 255) for row in rows):
            raise ValueError("采样块模式字段无效")
        rows = [row[:-1] + (None if row[-1] == 255 else row[-1],) for row in rows]
    if rows:
        if rows[0][0] != descriptor["first_sample_id"] or rows[-1][0] != descriptor["last_sample_id"]:
            raise ValueError("采样块索引与内容不一致")
        if any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
            raise ValueError("采样编号不严格递增")
    return rows


def _quality_counts():
    return {"recorded_samples": 0, "valid_samples": 0, "invalid_marker_samples": 0,
            "saturated_samples": 0, "other_invalid_samples": 0,
            "observed_sequence_anomalies": 0,
            "real_mode_samples": 0, "simulated_mode_samples": 0, "unknown_mode_samples": 0,
            "observed_mode_changes": 0, "stream_resyncs": 0}


def _count_quality(counts, rows):
    for _sample, _segment, _index, _sequence, adc, flags, mode in rows:
        counts["recorded_samples"] += 1
        # INVALID is the on-disk flag used by old and new records. Neither it
        # nor the shared 0xFFFF marker identifies the hardware failure/cause.
        invalid_marker = adc == 65535 or bool(flags & 1)
        saturated = adc in (0, 4095) or bool(flags & 2)
        if invalid_marker:
            counts["invalid_marker_samples"] += 1
        elif saturated:
            counts["saturated_samples"] += 1
        elif 0 < adc < 4095:
            counts["valid_samples"] += 1
        else:
            counts["other_invalid_samples"] += 1
        counts["observed_sequence_anomalies"] += bool(flags & 4)
        counts["observed_mode_changes"] += bool(flags & 8)
        counts["stream_resyncs"] += bool(flags & 16)
        key = "real_mode_samples" if mode == 0 else "simulated_mode_samples" if mode == 1 else "unknown_mode_samples"
        counts[key] += 1


def _disk_mode(mode):
    if mode is None:
        return 255
    if type(mode) is not int or mode not in (0, 1):
        raise ValueError("采样模式必须为0、1或未知")
    return mode


def _signal_origin(source, mode):
    if source == "demo":
        return "local_simulated"
    if source == "serial":
        return {0: "real_adc", 1: "board_simulated"}.get(mode, "unknown")
    # CSV replay may itself come from either a local demo or a board. Its mode
    # survives, but a mode byte alone cannot identify the original producer.
    return "unknown"


class Recorder:
    def __init__(self, data_dir, metadata, *, queue_size=64, flush_interval=1.0):
        if queue_size < 1 or not math.isfinite(flush_interval) or flush_interval <= 0:
            raise ValueError("无效记录队列或提交间隔")
        self.metadata = json.loads(json.dumps(metadata, allow_nan=False))
        self.session_id = self.metadata["session_id"]
        self.path = _session_path(data_dir, self.session_id)
        self.metadata.setdefault("source", "unknown")
        self.metadata.setdefault("started_utc", utc_now())
        self.metadata.setdefault("sample_rate_hz", FS)
        if self.metadata["sample_rate_hz"] != FS:
            raise ValueError("当前记录格式固定使用 360 Hz")
        self.metadata.update(schema_version=RAW_SCHEMA_VERSION, quality_schema_version=QUALITY_SCHEMA_VERSION,
                             mode_encoding={"0": "real", "1": "simulated", "255": "unknown"},
                             status="recording", sample_count=0)
        self._queue = Queue(maxsize=queue_size)
        self._events = Queue(maxsize=256)
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._accepting = False
        self._error = None
        self._accepted = self._saved = 0
        self._rejected_samples = 0
        self._wire_saved = 0
        self._finish_complete = True
        self._finish_reason = "stopped"
        self._flush_interval = float(flush_interval)

    def start(self):
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("记录器只能启动一次")
            self._thread = threading.Thread(target=self._run, name="ecg-recorder", daemon=False)
            self._thread.start()
        if not self._ready.wait(15):
            self._fail("存储初始化超时")
            raise RuntimeError(self._error)
        if self._error:
            raise RuntimeError(self._error)
        return self

    def _fail(self, message):
        with self._lock:
            if not self._error:
                self._error = str(message)
            self._accepting = False
            self._stop.set()

    def submit(self, batch):
        with self._lock:
            if not self._accepting or self._error:
                return False
            if batch.session_id != self.session_id or batch.source != self.metadata["source"]:
                self._fail("数据块会话或来源不匹配")
                return False
            try:
                self._queue.put_nowait(batch)
            except Full:
                self._rejected_samples += len(batch.points)
                self._fail("原始记录队列已满，已停止接收新的记录数据")
                return False
            self._accepted += len(batch.points)
            return True

    def add_event(self, kind, detail):
        try:
            event = (utc_now(), str(kind), json.dumps(detail, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError) as exc:
            self._fail("事件格式错误：" + str(exc))
            return False
        with self._lock:
            if not self._accepting:
                return False
            try:
                self._events.put_nowait(event)
            except Full:
                self._fail("事件记录队列已满，不能保证记录完整")
                return False
            return True

    def status(self):
        with self._lock:
            return {"saved": self._saved, "pending": self._accepted - self._saved,
                    "accepted": self._accepted, "wire_saved_bytes": self._wire_saved,
                    "rejected": self._rejected_samples,
                    "error": self._error, "accepting": self._accepting,
                    "finished": self._thread is not None and not self._thread.is_alive()}

    def finish(self, reason="stopped", complete=True):
        with self._lock:
            self._accepting = False
            self._finish_reason = str(reason)
            self._finish_complete = bool(complete)
            self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
        return self.status()

    def _commit_chunk(self, connection, batches, chunk_id, last_id, quality):
        rows = []
        wire_parts = []
        for batch in batches:
            wire_parts.append(batch.wire)
            for point in batch.points:
                row = (point.sample_id, point.segment_id, point.segment_index,
                       point.sequence, point.raw_adc, point.flags, getattr(point, "mode", None))
                if last_id is not None and row[0] <= last_id:
                    raise ValueError("原始采样编号未严格递增")
                last_id = row[0]
                rows.append(row)
        payload = HEADER.pack(MAGIC, RAW_SCHEMA_VERSION, ROW.size, FS, len(rows)) + b"".join(
            ROW.pack(*row[:-1], _disk_mode(row[-1])) for row in rows)
        wire = b"".join(wire_parts)
        relative = f"chunks/{chunk_id:08d}.bin"
        wire_relative = f"wire/{chunk_id:08d}.bin"
        descriptor = {"schema_version": RAW_SCHEMA_VERSION, "session_id": self.session_id, "chunk_id": chunk_id,
                      "file": relative, "sha256": hashlib.sha256(payload).hexdigest(),
                      "wire_file": wire_relative, "wire_sha256": hashlib.sha256(wire).hexdigest(),
                      "wire_bytes": len(wire), "sample_count": len(rows),
                      "first_sample_id": rows[0][0] if rows else None,
                      "last_sample_id": rows[-1][0] if rows else None,
                      "received_first_utc": batches[0].received_utc,
                      "received_last_utc": batches[-1].received_utc,
                      "received_first_monotonic": batches[0].received_monotonic,
                      "received_last_monotonic": batches[-1].received_monotonic}
        _write_atomic(self.path / relative, payload)
        _write_atomic(self.path / wire_relative, wire)
        # The sidecar lets recovery verify a fully written but unindexed block.
        _write_atomic(self.path / "chunks" / f"{chunk_id:08d}.json", _json_bytes(descriptor))
        saved = self._saved + len(rows)
        with connection:
            connection.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                               (chunk_id, json.dumps(descriptor), len(rows),
                                descriptor["first_sample_id"], descriptor["last_sample_id"]))
            connection.execute("UPDATE sessions SET sample_count=? WHERE session_id=?",
                               (saved, self.session_id))
        with self._lock:
            self._saved = saved
            self._wire_saved += len(wire)
        _count_quality(quality, rows)
        self.metadata.update(sample_count=saved, last_sample_id=last_id,
                             wire_bytes=self._wire_saved, quality=dict(quality))
        _write_atomic(self.path / "session.json", _json_bytes(self.metadata))
        return last_id

    def _drain_events(self, connection):
        rows = []
        for _ in range(256):
            try:
                rows.append(self._events.get_nowait())
            except Empty:
                break
        if rows:
            with connection:
                connection.executemany("INSERT INTO events(utc,kind,detail_json) VALUES(?,?,?)", rows)

    def _run(self):
        connection = lock = None
        created = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.mkdir(exist_ok=False)  # Never overwrite an existing session.
            created = True
            for name in ("chunks", "wire", "exports"):
                (self.path / name).mkdir()
            lock = _SessionLock(self.path)
            connection = sqlite3.connect(self.path / "index.sqlite")
            _schema(connection)
            with connection:
                connection.execute("INSERT INTO sessions VALUES(?,?,?,0)",
                                   (self.session_id, json.dumps(self.metadata), "recording"))
            _write_atomic(self.path / "session.json", _json_bytes(self.metadata))
            with self._lock:
                if not self._stop.is_set():
                    self._accepting = True
            self._ready.set()
            batches, points, chunk_id, last_id = [], 0, 0, None
            quality = _quality_counts()
            deadline = time.monotonic() + self._flush_interval
            while True:
                exhausted = False
                try:
                    batch = self._queue.get(timeout=0.05)
                    batches.append(batch)
                    points += len(batch.points)
                except Empty:
                    exhausted = self._stop.is_set()
                if batches and (points >= FS or time.monotonic() >= deadline or exhausted):
                    last_id = self._commit_chunk(connection, batches, chunk_id, last_id, quality)
                    batches, points, chunk_id = [], 0, chunk_id + 1
                    deadline = time.monotonic() + self._flush_interval
                self._drain_events(connection)
                if exhausted:
                    break
        except Exception as exc:
            self._fail(f"记录失败：{exc}")
            LOG.exception("ECG recording failed for %s", self.session_id)
        finally:
            self._ready.set()
            with self._lock:
                self._accepting = False
            if created:
                state = "failed" if self._error else ("complete" if self._finish_complete else "incomplete")
                self.metadata.update(status=state, ended_utc=utc_now(),
                                     end_reason=self._finish_reason, error=self._error,
                                     sample_count=self._saved, accepted_samples=self._accepted,
                                     rejected_samples=self._rejected_samples,
                                     unsaved_samples=self._accepted - self._saved + self._rejected_samples)
                try:
                    if connection is not None:
                        with connection:
                            connection.execute("UPDATE sessions SET metadata_json=?,status=?,sample_count=? WHERE session_id=?",
                                               (json.dumps(self.metadata), state, self._saved, self.session_id))
                    _write_atomic(self.path / "session.json", _json_bytes(self.metadata))
                    quality_report = {"schema_version": QUALITY_SCHEMA_VERSION, "session_id": self.session_id,
                                      "source": self.metadata["source"], "status": state,
                                      "basis": "committed_samples_only", "sample_rate_hz": FS,
                                      "recorded_sample_count": self._saved,
                                      "sample_count_duration_s": self._saved / FS,
                                      "accepted_samples": self._accepted,
                                      "rejected_samples": self._rejected_samples,
                                      "unsaved_samples": self.metadata["unsaved_samples"],
                                      "counts": self.metadata.get("quality", _quality_counts()),
                                      "note": INVALID_MARKER_NOTE + MODE_NOTE + "序号异常只是协议观察结果；样本计数时长不是跨缺口的精确采集时长。"}
                    _write_atomic(self.path / "quality.json", _json_bytes(quality_report))
                except Exception as exc:
                    self._fail(f"结束记录时写盘失败：{exc}")
                    LOG.exception("Could not persist recording completion")
                    # A late report/manifest failure must not leave a persisted
                    # "complete" state when the caller was told finalization failed.
                    self.metadata.update(status="failed", error=self._error)
                    try:
                        if connection is not None:
                            with connection:
                                connection.execute("UPDATE sessions SET metadata_json=?,status='failed' WHERE session_id=?",
                                                   (json.dumps(self.metadata), self.session_id))
                        _write_atomic(self.path / "session.json", _json_bytes(self.metadata))
                    except Exception:
                        LOG.exception("Could not persist finalization failure; memory status remains failed")
            if connection is not None:
                connection.close()
            if lock is not None:
                lock.close()


def list_sessions(data_dir):
    root = Path(data_dir).expanduser().resolve() / "sessions"
    result = []
    if not root.exists():
        return result
    for directory in root.iterdir():
        if not directory.is_dir() or not SESSION_ID.fullmatch(directory.name):
            continue
        try:
            metadata = json.loads((directory / "session.json").read_text(encoding="utf-8"))
            # SQLite is authoritative if power loss interrupted session.json update.
            with _readonly_connection(directory / "index.sqlite") as connection:
                row = connection.execute("SELECT status,sample_count FROM sessions WHERE session_id=?",
                                         (directory.name,)).fetchone()
            if row:
                metadata["status"], metadata["sample_count"] = row
            result.append({"session_id": directory.name, "source": metadata.get("source", "unknown"),
                           "started_utc": metadata.get("started_utc", ""),
                           "status": metadata.get("status", "incomplete"),
                           "sample_count": metadata.get("sample_count", 0),
                           "path": str(directory), "error": metadata.get("error")})
        except (OSError, ValueError, sqlite3.Error) as exc:
            result.append({"session_id": directory.name, "source": "unknown", "started_utc": "",
                           "status": "unreadable", "sample_count": 0,
                           "path": str(directory), "error": str(exc)})
    return sorted(result, key=lambda item: item["started_utc"], reverse=True)


def export_session(data_dir, session_id, output_path):
    """Export a consistent committed snapshot; never replace recording files."""
    directory = _session_path(data_dir, session_id)
    target = Path(output_path).expanduser().resolve()
    if target.suffix.lower() != ".csv":
        raise ValueError("导出文件应使用 .csv 后缀")
    sessions_root = directory.parent
    if target.is_relative_to(sessions_root) and not target.is_relative_to(directory / "exports"):
        raise ValueError("不能将导出文件写入会话原始数据目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + str(threading.get_ident()) + ".tmp")
    try:
        with _readonly_connection(directory / "index.sqlite") as connection:
            connection.execute("BEGIN")
            meta_row = connection.execute("SELECT metadata_json FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if not meta_row:
                raise ValueError("会话不存在")
            metadata = json.loads(meta_row[0])
            descriptors = connection.execute("SELECT descriptor_json FROM chunks ORDER BY chunk_id")
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["session_id", "source", "sample_id", "segment_id", "segment_index",
                                 "time_s", "segment_time_s", "sequence", "raw_adc", "flags", "valid",
                                 "voltage_v", "vref_v", "received_first_utc", "received_last_utc",
                                 "mode", "signal_origin"])
                vref = float(metadata.get("vref", metadata.get("vref_v", 3.3)))
                if not math.isfinite(vref) or vref <= 0:
                    raise ValueError("会话参考电压无效")
                for row in descriptors:
                    descriptor = json.loads(row[0])
                    for sample_id, segment, index, sequence, adc, flags, mode in _chunk_rows(directory, descriptor):
                        valid = 0 < adc < 4095 and not (flags & 3)
                        writer.writerow([session_id, metadata.get("source", "unknown"), sample_id, segment, index,
                                         f"{sample_id / FS:.9f}", f"{index / FS:.9f}", sequence, adc, flags,
                                         int(valid), f"{adc * vref / 4095:.9f}" if valid else "", f"{vref:.6f}",
                                         descriptor["received_first_utc"], descriptor["received_last_utc"],
                                         "" if mode is None else mode,
                                         _signal_origin(metadata.get("source", "unknown"), mode)])
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, target)
        _sync_directory(target.parent)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return str(target)


def recover_session(data_dir, session_id):
    """Validate committed blocks and index verifiable orphans after interruption.

    Incomplete .tmp files and unverified orphan files are kept for investigation.
    Recovery never labels an interrupted session as normally completed.
    """
    directory = _session_path(data_dir, session_id)
    if not directory.is_dir():
        raise ValueError("会话不存在")
    lock = _SessionLock(directory)
    try:
        metadata = json.loads((directory / "session.json").read_text(encoding="utf-8"))
        quality = _quality_counts()
        verified = recovered = wire_bytes = 0
        errors = []
        last_id = None
        with closing(sqlite3.connect(directory / "index.sqlite")) as connection:
            _schema(connection)
            with connection:
                connection.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?,0)",
                                   (session_id, json.dumps(metadata), metadata.get("status", "incomplete")))
            indexed = {row[0]: json.loads(row[1]) for row in connection.execute("SELECT chunk_id,descriptor_json FROM chunks")}
            candidates = dict(indexed)
            for sidecar in sorted((directory / "chunks").glob("*.json")):
                try:
                    descriptor = json.loads(sidecar.read_text(encoding="utf-8"))
                    if descriptor["session_id"] != session_id:
                        raise ValueError("数据块会话不匹配")
                    if not isinstance(descriptor["chunk_id"], int) or descriptor["chunk_id"] < 0:
                        raise ValueError("数据块编号无效")
                    candidates.setdefault(descriptor["chunk_id"], descriptor)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(f"{sidecar.name}: {exc}")
            for chunk_id in sorted(candidates):
                descriptor = candidates[chunk_id]
                try:
                    rows = _chunk_rows(directory, descriptor)
                    if rows and last_id is not None and rows[0][0] <= last_id:
                        raise ValueError("数据块之间出现重复/倒序采样编号")
                    if rows:
                        last_id = rows[-1][0]
                    if chunk_id not in indexed:
                        with connection:
                            connection.execute("INSERT INTO chunks VALUES(?,?,?,?,?)",
                                               (chunk_id, json.dumps(descriptor), len(rows),
                                                descriptor["first_sample_id"], descriptor["last_sample_id"]))
                        recovered += 1
                    verified += len(rows)
                    wire_bytes += descriptor["wire_bytes"]
                    _count_quality(quality, rows)
                except (OSError, ValueError, KeyError, struct.error, sqlite3.Error) as exc:
                    errors.append(f"chunk {chunk_id}: {exc}")
            known = {f"{int(i):08d}.bin" for i in candidates}
            unverified = [str(p.relative_to(directory)) for p in directory.rglob("*.tmp")]
            unverified += [str(p.relative_to(directory)) for p in (directory / "chunks").glob("*.bin")
                           if p.name not in known]
            unverified += [str(p.relative_to(directory)) for p in (directory / "wire").glob("*.bin")
                           if p.name not in known]
            old_state = metadata.get("status")
            state = "recovery_failed" if errors or unverified else (
                "complete" if old_state == "complete" and not recovered else "recovered_incomplete")
            recovery = {"verified_samples": verified, "recovered_chunks": recovered,
                        "errors": errors, "unverified_files": unverified, "checked_utc": utc_now()}
            # Rebuild counts from verified rows, including v1 sessions whose
            # cached quality used the misleading legacy lead_off_samples key.
            # Read both binary versions without rewriting samples or inventing
            # modes for v1 rows. The unchanged SQLite structure stays at v1.
            metadata.update(status=state, sample_count=verified, quality=quality,
                            quality_schema_version=QUALITY_SCHEMA_VERSION,
                            wire_bytes=wire_bytes, recovery=recovery)
            with connection:
                connection.execute("UPDATE sessions SET metadata_json=?,status=?,sample_count=? WHERE session_id=?",
                                   (json.dumps(metadata), state, verified, session_id))
            _write_atomic(directory / "session.json", _json_bytes(metadata))
            _write_atomic(directory / "quality.json", _json_bytes({
                "schema_version": QUALITY_SCHEMA_VERSION, "session_id": session_id, "source": metadata.get("source", "unknown"),
                "status": state, "basis": "verified_committed_samples_only", "sample_rate_hz": FS,
                "recorded_sample_count": verified, "sample_count_duration_s": verified / FS,
                "counts": quality, "recovery": recovery,
                "note": INVALID_MARKER_NOTE + MODE_NOTE + "恢复会话未标为正常完成；序号异常计数不能证明绝对零丢样。"}))
        return recovery
    finally:
        lock.close()
