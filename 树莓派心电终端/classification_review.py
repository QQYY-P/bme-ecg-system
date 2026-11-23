"""Read committed ECG classifications and bounded waveform review windows.

Readers use SQLite snapshot transactions, not writer locks: an active recording
can be inspected without interrupting acquisition. Raw blocks are immutable
after commit and their checksums are checked before use.
"""
from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
import json
import math

import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos

from contracts import INVALID, SATURATED, DISCONTINUITY, MODE_CHANGE, STREAM_RESYNC
from recording import FS, _chunk_rows, _readonly_connection
from session_management import _directory, _plain_path, _root

LABELS = ("N", "S", "V")
_NAN = float("nan")
_MAX_INDEX = 2 ** 63 - 1


def _index(value, name):
    if type(value) is not int or not 0 <= value <= _MAX_INDEX:
        raise ValueError(f"{name} 必须为非负整数")
    return value


def _valid_index(value):
    return type(value) is int and 0 <= value <= _MAX_INDEX


def _detail(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _time_info(metadata):
    value = metadata.get("time_info")
    if not isinstance(value, dict):
        return {"trusted": False, "source": "unknown"}
    return {**value, "trusted": value.get("trusted") is True}


@contextmanager
def _snapshot(data_dir, sid):
    directory = _directory(_root(data_dir), sid)
    for name in ("index.sqlite", "index.sqlite-wal", "index.sqlite-shm"):
        path = _plain_path(directory / name)
        if path.exists() and not path.is_file():
            raise ValueError("会话数据库不是普通文件")
    with _readonly_connection(directory / "index.sqlite") as connection:
        connection.execute("BEGIN")
        row = connection.execute(
            "SELECT metadata_json,status,sample_count FROM sessions WHERE session_id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("会话数据库中没有该会话")
        metadata = _detail(row[0])
        if metadata is None or metadata.get("session_id") != sid:
            raise ValueError("会话信息与数据库编号不一致")
        metadata.update(status=row[1], sample_count=row[2])
        yield directory, connection, metadata


def classification_details(data_dir, session_id, limit=500):
    """All-session counts plus latest <=500 prediction events, in event order.

`rejected` counts recorded rejected candidates, never all unclassified beats.
It is None if neither detailed events nor a trustworthy summary provides it.
"""
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("明细数量须为 1 至 500")
    counts = dict.fromkeys(LABELS, 0)
    reasons = Counter()
    recent = deque(maxlen=limit)
    total = rejected_events = malformed = foreign = 0
    legacy_rejected = None
    with _snapshot(data_dir, session_id) as (_, connection, metadata):
        for event_id, utc, kind, raw in connection.execute(
                "SELECT event_id,utc,kind,detail_json FROM events "
                "WHERE kind IN ('cardioedge_prediction','cardioedge_rejected','cardioedge_summary') "
                "ORDER BY event_id"):
            detail = _detail(raw)
            if kind == "cardioedge_prediction":
                total += 1
                row = dict(detail or {})
                error = None
                if detail is None:
                    malformed += 1
                    error = "分类事件内容损坏"
                elif detail.get("session_id", session_id) != session_id:
                    foreign += 1
                    error = "分类事件会话编号不匹配"
                elif not _valid_index(detail.get("r_sample")) or not _valid_index(detail.get("segment_id")):
                    malformed += 1
                    error = "分类事件 R 点或分段编号无效，不能复核波形"
                elif detail.get("label") in LABELS:
                    counts[detail["label"]] += 1
                    # Old valid events were scoped by their containing session
                    # and did not always repeat its id in each JSON payload.
                    row.setdefault("session_id", session_id)
                else:
                    malformed += 1
                    error = "分类标签缺失或不属于 N/S/V"
                row.update(event_id=event_id, event_utc=utc, event_kind=kind,
                           detail_json=raw, integrity_error=error)
                recent.append(row)
            elif kind == "cardioedge_rejected":
                if detail is not None and detail.get("session_id", session_id) != session_id:
                    foreign += 1
                    continue
                if (detail is None or not _valid_index(detail.get("r_sample"))
                        or not _valid_index(detail.get("segment_id"))):
                    malformed += 1
                    continue
                rejected_events += 1
                reason = detail.get("reason") if detail else None
                reasons[reason if isinstance(reason, str) and reason else "unspecified"] += 1
            elif detail is not None and detail.get("session_id", session_id) == session_id:
                value = detail.get("rejected")
                if type(value) is int and value >= 0:
                    legacy_rejected = value
        config = metadata.get("classification")
        has_event_schema = (isinstance(config, dict)
                            and type(config.get("rejection_event_schema")) is int
                            and config["rejection_event_schema"] == 1)
        if rejected_events or has_event_schema:
            rejected, rejected_source = rejected_events, "events"
        elif legacy_rejected is not None:
            rejected, rejected_source = legacy_rejected, "legacy_summary"
            if rejected:
                reasons["legacy_unspecified"] = rejected
        else:
            rejected, rejected_source = None, "unavailable"
        note = "计数来自本会话已保存事件；拒绝数仅指已检测候选被拒绝，不代表所有未分类心搏。"
        if rejected_source == "legacy_summary":
            note += "旧记录拒绝数来自最后一条有效分类汇总，没有逐搏拒绝原因。"
        elif rejected_source == "unavailable":
            note += "该记录没有可确认的拒绝统计，显示未知。"
        if malformed or foreign:
            note += "损坏或会话编号不匹配的事件不计入分类及实际拒绝候选数；损坏明细不能用于波形复核。"
        return {"session_id": session_id, "counts": counts, "classified": sum(counts.values()),
                "rejected": rejected, "rows": list(recent), "truncated": total > len(recent),
                "total_rows": total, "reason_counts": dict(reasons),
                "time_info": _time_info(metadata),
                "rejected_source": rejected_source, "row_order": "event_id_ascending_latest",
                "status": metadata["status"], "malformed_events": malformed,
                "foreign_session_events": foreign, "note": note}


def _saved_filter(metadata):
    """Rebuild only a documented saved display filter; never use current defaults."""
    config = metadata.get("preprocessing")
    if not isinstance(config, dict):
        return None, "旧记录未保存显示预处理参数，无法可靠复算处理波形。"
    try:
        if config.get("version") != 1 or config.get("sample_rate_hz") != FS:
            raise ValueError("预处理版本或采样率不支持")
        high, notch, low = config["highpass"], config["notch"], config["lowpass"]
        for section in (high, low):
            if (not isinstance(section, dict) or section.get("family") != "Butterworth"
                    or type(section.get("order")) is not int or not 1 <= section["order"] <= 12):
                raise ValueError("滤波器类型或阶数不支持")
        hp, lp = float(high["cutoff_hz"]), float(low["cutoff_hz"])
        center, quality = float(notch["center_hz"]), float(notch["quality_factor"])
        if (not 0 < hp < lp < FS / 2 or not 0 < center < FS / 2
                or not math.isfinite(quality) or quality <= 0 or notch.get("order") != 2):
            raise ValueError("滤波器参数无效")
        implementation = config.get("implementation", "")
        if not isinstance(implementation, str) or not implementation.startswith("causal streaming SOS"):
            raise ValueError("仅支持原有因果 SOS 显示预处理")
        b, a = iirnotch(center, quality, fs=FS)
        sos = np.vstack((butter(high["order"], hp, btype="highpass", fs=FS, output="sos"),
                         tf2sos(b, a),
                         butter(low["order"], lp, btype="lowpass", fs=FS, output="sos")))
        return sos, "处理波形按该会话保存参数重新计算，最多借用前 5 秒预热；窗口边界不能保证与在线结果逐点一致。"
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return None, "保存的预处理参数无法复算，仅保留原始波形：" + str(exc)


def _prediction(connection, sid, r_sample, segment_id):
    # Streaming iteration retains one row; no full-history list or JSON1 dependency.
    for event_id, utc, raw in connection.execute(
            "SELECT event_id,utc,detail_json FROM events WHERE kind='cardioedge_prediction' ORDER BY event_id DESC"):
        detail = _detail(raw)
        if detail is None:
            continue
        if (type(detail.get("r_sample")) is int and detail["r_sample"] == r_sample
                and type(detail.get("segment_id")) is int and detail["segment_id"] == segment_id
                and detail.get("session_id", sid) == sid):
            if detail.get("label") not in LABELS:
                raise ValueError("该心搏没有有效的已保存分类标签")
            return {**detail, "session_id": detail.get("session_id", sid),
                    "event_id": event_id, "event_utc": utc, "detail_json": raw}
    raise ValueError("没有与该采样编号和分段编号匹配的已保存分类")


def _window_rows(directory, connection, sid, begin, end):
    previous = None
    for first, last, raw in connection.execute(
            "SELECT first_sample_id,last_sample_id,descriptor_json FROM chunks "
            "WHERE sample_count>0 AND first_sample_id<=? AND last_sample_id>=? ORDER BY chunk_id", (end, begin)):
        descriptor = _detail(raw)
        if (descriptor is None or descriptor.get("session_id") != sid
                or descriptor.get("first_sample_id") != first or descriptor.get("last_sample_id") != last):
            raise ValueError("采样块索引或会话编号不匹配")
        chunk_id = descriptor.get("chunk_id")
        if type(chunk_id) is not int or chunk_id < 0:
            raise ValueError("采样块编号无效")
        for folder in ("chunks", "wire"):
            path = _plain_path(directory / folder / f"{chunk_id:08d}.bin")
            if not path.is_file():
                raise ValueError("已提交采样块缺失")
        for row in _chunk_rows(directory, descriptor):
            if previous is not None and row[0] <= previous:
                raise ValueError("采样块之间存在重复或倒序编号")
            previous = row[0]
            if begin <= row[0] <= end:
                yield row


def review_beat(data_dir, sid, r_sample, segment_id, before_s=2, after_s=2):
    """Return an ADC-count waveform near one saved classification, with breaks.

`wave.raw` and `wave.filtered` are ADC counts (not volts); existing canvases
perform their own vref conversion. `wave.raw_adc` additionally preserves exact
invalid markers, while plotting arrays use NaN to prevent joining gaps.
"""
    _index(r_sample, "R 点采样编号")
    _index(segment_id, "分段编号")
    for value in (before_s, after_s):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 30:
            raise ValueError("复核前后时长各须为 0 至 30 秒")
    if before_s + after_s <= 0:
        raise ValueError("复核窗口不能为零")
    requested_begin = r_sample - int(math.ceil(before_s * FS))
    begin = max(0, requested_begin)
    end = min(_MAX_INDEX, r_sample + int(math.ceil(after_s * FS)))
    warm_begin = max(0, begin - 5 * FS)
    wave = {key: [] for key in ("x", "raw", "filtered", "segment_ids", "sample_ids", "raw_adc", "valid")}
    with _snapshot(data_dir, sid) as (directory, connection, metadata):
        prediction = _prediction(connection, sid, r_sample, segment_id)
        if "vref_v" not in metadata and "vref" not in metadata:
            raise ValueError("会话未保存参考电压，不能可靠复核电压波形")
        vref = metadata.get("vref_v", metadata.get("vref"))
        if type(vref) not in (int, float) or not math.isfinite(vref) or not .1 <= vref <= 10:
            raise ValueError("会话参考电压无效")
        sos, note = _saved_filter(metadata)
        source = metadata.get("source", "unknown")
        initial = sosfilt_zi(sos) if sos is not None else None
        state = None
        previous = None
        matched = False
        partial = requested_begin < 0
        run = []
        last_displayed = None

        def separator():
            if wave["x"] and math.isfinite(wave["x"][-1]):
                for key in ("x", "raw", "filtered", "segment_ids"):
                    wave[key].append(_NAN)
                for key in ("sample_ids", "raw_adc"):
                    wave[key].append(None)
                wave["valid"].append(False)

        def append(row, filtered, valid):
            nonlocal last_displayed, partial
            index, segment, _, _, adc, _, _ = row
            if not begin <= index <= end:
                return
            if last_displayed is not None and index != last_displayed + 1:
                separator()
                partial = True
            wave["x"].append(index / FS)
            wave["raw"].append(float(adc) if valid else _NAN)
            wave["filtered"].append(float(filtered) if valid else _NAN)
            wave["segment_ids"].append(segment)
            wave["sample_ids"].append(index)
            wave["raw_adc"].append(adc)
            wave["valid"].append(valid)
            last_displayed = index

        def flush():
            nonlocal state
            if not run:
                return
            values = np.asarray([row[4] for row in run], dtype=float)
            eligible = source in ("serial", "replay") and run[0][6] == 0
            if not eligible:
                filtered = values
            elif sos is None:
                filtered = np.full(values.size, np.nan)
            else:
                if state is None:
                    state = initial * values[0]
                filtered, state = sosfilt(sos, values, zi=state)
            for row, value in zip(run, filtered):
                append(row, value, True)
            run.clear()

        for row in _window_rows(directory, connection, sid, warm_begin, end):
            index, segment, ordinal, _, adc, flags, mode = row
            if index == r_sample:
                if segment != segment_id:
                    raise ValueError("分类分段与原始采样分段不一致，拒绝错误配对")
                matched = True
            valid = 0 < adc < 4095 and not flags & (INVALID | SATURATED)
            boundary = bool(flags & (DISCONTINUITY | MODE_CHANGE | STREAM_RESYNC)) or (
                previous is not None and (index != previous[0] + 1 or segment != previous[1]
                                          or ordinal != previous[2] + 1 or mode != previous[6]))
            if boundary or not valid:
                flush()
                state = None
                if index >= begin:
                    separator()
                    partial = True
            if valid:
                run.append(row)
            else:
                append(row, _NAN, False)
            previous = row
        flush()
        real_ids = [value for value in wave["sample_ids"] if value is not None]
        if not real_ids or real_ids[0] > begin or real_ids[-1] < end:
            partial = True
        if not matched:
            partial = True
            note += "该分类的 R 点原始样本尚未提交或缺失，不能确认波形配对。"
            # Do not show unrelated neighbors as the requested beat.
            wave = {key: [] for key in wave}
        if partial:
            note += "当前片段存在记录边界、断段或尚未提交的数据；断点保留为空白。"
        return {"wave": wave, "prediction": prediction, "vref": float(vref),
                "begin_s": begin / FS, "end_s": end / FS, "partial": partial, "note": note,
                "session_id": sid, "r_sample": r_sample, "segment_id": segment_id,
                "wave_units": "adc_counts", "filter_recomputed": sos is not None,
                "time_info": _time_info(metadata)}
