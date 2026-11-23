"""Application lifecycle; all GUI calls are non-blocking snapshots or control requests."""
import logging
import re
import csv
import queue
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import numpy as np

from acquisition import Acquisition, utc_now
from recording import Recorder, list_sessions, export_session
from signal_analysis import SignalAnalyzer
from serial_connection import list_serial_ports, resolve_serial_port
from protocol import BAUD_RATE, BLOCK_SAMPLES, FRAME_BYTES, PROTOCOL_NAME
from preprocessing import PREPROCESSING_CONFIG
from signal_quality import QUALITY_CONFIG
from contracts import INVALID, SATURATED, DISCONTINUITY, MODE_CHANGE, STREAM_RESYNC
from cardioedge.stream import CONFIG as CLASSIFICATION_CONFIG
from cardioedge.service import ClassificationService
from settings_store import load_settings, save_settings
from storage_monitor import storage_status as read_storage_status
from time_sync import clock_status as read_clock_status, request_time_sync, manual_set_time, authorize_time
import session_management
import classification_review
from measurement import MeasurementStats

LOG = logging.getLogger(__name__)


class _WaveBuffer:
    """Display-only numeric ring; caller holds the controller lock.

    Convert each small analysis batch once, instead of converting 30 seconds
    of Python/NumPy scalar tuples on every UI snapshot. Never feeds recording
    or inference. Snapshots own their memory, including across wraparound.
    """
    def __init__(self, maxlen):
        self.maxlen = maxlen
        self._data = np.empty((maxlen, 4), dtype=float)
        self.clear()

    def clear(self):
        self._end = self._count = 0

    def __len__(self):
        return self._count

    def __getitem__(self, index):
        if index < 0:
            index += self._count
        if not 0 <= index < self._count:
            raise IndexError(index)
        return self._data[(self._end - self._count + index) % self.maxlen].copy()

    def append(self, row):
        self.extend((row,))

    def extend(self, rows):
        rows = [(*row, -1) if len(row) == 3 else row for row in rows]
        if not rows:
            return
        values = np.asarray(rows, dtype=float)
        if values.ndim != 2 or values.shape[1] != 4:
            raise ValueError('Wave buffer requires time, raw, filtered, segment')
        count = len(values)
        if count >= self.maxlen:
            self._data[:] = values[-self.maxlen:]
            self._end, self._count = 0, self.maxlen
            return
        first = min(count, self.maxlen - self._end)
        self._data[self._end:self._end + first] = values[:first]
        self._data[:count - first] = values[first:]
        self._end = (self._end + count) % self.maxlen
        self._count = min(self.maxlen, self._count + count)

    def array(self):
        if self._count < self.maxlen:
            return self._data[:self._count].copy()
        if not self._end:
            return self._data.copy()
        return np.concatenate((self._data[self._end:], self._data[:self._end]))


class Controller:
    def __init__(self, data_dir=None, classification_enabled=None):
        self.data_dir = Path(data_dir or Path(__file__).resolve().parent / "data")
        self._lock = threading.RLock()
        self._operation_lock = threading.RLock()
        self._settings = load_settings(self.data_dir)
        if classification_enabled is not None:
            self._settings['classification_enabled'] = bool(classification_enabled)
        self._storage_info = {'state': 'unknown', 'free_bytes': None, 'detail': '正在检查存储空间'}
        self._clock_info = {'trusted': False, 'source': 'unknown', 'detail': '时间尚未核验'}
        self._environment_stop = threading.Event()
        self._environment_thread = None
        self._storage_stop_reason = False
        self._thread = self._source = self._recorder = self._analysis_thread = None
        self._stop = threading.Event()
        self._analysis_done = threading.Event()
        self._analysis_queue = queue.Queue(maxsize=32)
        self._generation = 0
        self._wave = _WaveBuffer(maxlen=10800 + 256)
        self._started = None
        self._measurement = MeasurementStats()
        self._duration_complete = False
        self._mode_deadline = None
        self._last_received_point = None
        self._rx_bad_state = None
        self.classification_enabled = self._settings['classification_enabled']
        self._classification = None
        self._state = dict(state="IDLE", running=False, source="", status="请选择数据源",
                           error=None, session_id=None, received=0, saved=0, elapsed_s=0.,
                           hr=None, rr_ms=None, quality="等待数据",
                           analysis_dropped_samples=0, vref=3.3,
                           port=None, serial_bytes=0, serial_aligned=False, board_mode=None,
                           protocol_stats={}, preprocessing_active=False, preprocessing_warmup=False,
                           connection_state="idle", connection_text="未连接",
                           mode_check_state="idle", mode_expected=None, mode_feedback="等待确认 STM32 模式",
                           mode_change_count=0, signal_state="waiting", signal_text="等待数据",
                           signal_detail="", hr_allowed=False, quality_metrics={})

    @staticmethod
    def list_ports():
        try:
            return list_serial_ports()
        except ImportError:
            return []

    def start(self, source="demo", port=None, replay_path=None,
              vref=None, duration=None, cloud_owner_id=None, cloud_owner_name=None):
        # Serialize start with exports, deletion and explicit clock changes.
        with self._operation_lock:
            return self._start(source, port, replay_path, vref, duration, cloud_owner_id, cloud_owner_name)

    def _start(self, source, port, replay_path, vref, duration, cloud_owner_id=None, cloud_owner_name=None):
        if cloud_owner_id is not None and (not isinstance(cloud_owner_id,str) or not re.fullmatch(r'[a-f0-9]{32}',cloud_owner_id)):
            raise ValueError('云端所属账号编号无效')
        if cloud_owner_name is not None and (not isinstance(cloud_owner_name,str) or len(cloud_owner_name)>128):
            raise ValueError('云端账号显示名称无效')
        if vref is None:
            vref = self._settings['vref']
        if source not in ("demo", "serial", "replay"):
            raise ValueError("不支持的数据源")
        if source == "serial" and not port:
            raise ValueError("请选择串口")
        if source == "serial":
            port = resolve_serial_port(port)
        if source == "replay" and (not replay_path or not Path(replay_path).is_file()):
            raise ValueError("请选择存在的 CSV 回放文件")
        if not np.isfinite(vref) or not .1 <= vref <= 10:
            raise ValueError("参考电压须在 0.1～10 V")
        if duration is not None and (not np.isfinite(duration) or duration <= 0):
            raise ValueError("录制时长必须为正数")
        capacity = self.storage_status()
        if capacity['state'] == 'critical':
            raise RuntimeError('剩余空间低于安全保留量，请先导出或清理记录')
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("请先停止当前采集并等待保存完成")
            if any(worker and worker.is_alive() for worker in (self._source, self._analysis_thread)):
                raise RuntimeError("上次工作线程尚未结束，暂不能开始新会话")
            if self._classification and any(worker and worker.is_alive() for worker in
                                            (self._classification.thread,self._classification.process)):
                raise RuntimeError("上次分类线程尚未结束")
            self._stop = threading.Event()
            self._analysis_done = threading.Event()
            self._analysis_queue = queue.Queue(maxsize=32)
            self._generation = 0
            self._wave.clear()
            self._started = time.monotonic()
            self._measurement = MeasurementStats()
            self._duration_complete = False
            self._mode_deadline = None
            self._last_received_point = None
            self._rx_bad_state = None
            self._recorder = self._source = self._analysis_thread = None
            self._classification = None
            self._storage_stop_reason = False
            session_id = uuid.uuid4().hex
            self._state.update(state="CONNECTING", running=True, source=source,
                               status="正在启动", error=None, session_id=session_id,
                               received=0, saved=0, elapsed_s=0., hr=None, rr_ms=None, cloud_owner_id=cloud_owner_id,
                               requested_duration_s=duration,cloud_owner_name=cloud_owner_name,
                               quality="等待数据",
                               analysis_dropped_samples=0, vref=float(vref), port=port,
                               serial_bytes=0, serial_aligned=False, board_mode=None,
                               protocol_stats={}, preprocessing_active=False, preprocessing_warmup=False,
                               connection_state="connecting", connection_text="正在打开串口" if source == "serial" else "正在启动",
                               mode_check_state="idle", mode_expected=None, mode_feedback="等待确认 STM32 模式" if source == "serial" else "",
                               mode_change_count=0, signal_state="waiting", signal_text="等待数据",
                               signal_detail="", hr_allowed=False, quality_metrics={})
            metadata = dict(session_id=session_id, source=source, source_kind=source,
                            cloud_owner_id=cloud_owner_id,cloud_owner_name=cloud_owner_name,
                            requested_duration_s=duration,
                            started_utc=utc_now(), sample_rate_hz=360, vref_v=float(vref),
                            vref_calibrated=False, port=port, replay_path=str(replay_path or ""),
                            schema_version=1,
                            preprocessing=deepcopy(PREPROCESSING_CONFIG),
                            signal_quality=deepcopy(QUALITY_CONFIG),
                            classification={**deepcopy(CLASSIFICATION_CONFIG), "enabled": self.classification_enabled,
                                            "rejection_event_schema": 1},
                            time_info=deepcopy(self._clock_info),
                            started_monotonic=self._started,
                            storage_policy={k: self._settings[k] for k in ('low_space_warn_mb', 'low_space_stop_mb')},
                            time_axis="received_sample_ordinal/360; not exact time across gaps")
            if source == "serial":
                metadata.update(signal_origin="per_sample_mode", serial_protocol={
                    "name": PROTOCOL_NAME, "baud_rate": BAUD_RATE, "format": "8N1",
                    "frame_bytes": FRAME_BYTES, "block_samples": BLOCK_SAMPLES,
                    "header": "AA55", "mode_field": True,
                    "modes": {"0": "real_adc", "1": "board_simulated"},
                    "checksum": "sum8(mode,sequence,data_hi,data_lo)",
                    "invalid_marker": "0xFFFF: lead-off or source-switch guard; reason unavailable"})
            self._thread = threading.Thread(target=self._run, name="ecg-lifecycle", daemon=True,
                                            args=(metadata, port, replay_path, duration))
            self._thread.start()
        return session_id

    def _fail(self, message):
        LOG.error("Acquisition failure: %s", message)
        with self._lock:
            self._interrupt_mode_check("采集异常")
            self._reset_context("采集异常", "interrupted", str(message))
            self._state.update(error=str(message), status=str(message), serial_aligned=False,
                               board_mode=None, connection_state="error", connection_text="采集异常，已停止接收")
        self._stop.set()
        if self._source:
            self._source.stop()

    def _run(self, metadata, port, replay_path, duration):
        analysis_thread = None
        reason = "stopped"
        try:
            # Read-only clock check in lifecycle worker, never the Tk thread.
            metadata['time_info'] = self.clock_status()
            model_source = metadata["source"] == "serial"
            if metadata["source"] == "replay":
                with Path(replay_path).open(encoding="utf-8-sig",newline="") as stream:
                    model_source = "mode" in (next(csv.reader(stream), []))
            metadata["classification"]["eligible_source"] = model_source
            self._recorder = Recorder(self.data_dir, metadata)
            self._recorder.start()
            if self.classification_enabled and model_source:
                self._classification = ClassificationService(vref=metadata["vref_v"],
                    on_result=lambda row: self._recorder.add_event("cardioedge_prediction", row),
                    on_rejected=lambda row: self._recorder.add_event("cardioedge_rejected", row))
                self._classification.start()
            analyzer = SignalAnalyzer()
            analysis_thread = threading.Thread(target=self._analyze,
                                               args=(analyzer, self._analysis_queue, self._analysis_done,
                                                     metadata["session_id"]),
                                               name="ecg-analysis", daemon=True)
            self._analysis_thread = analysis_thread
            analysis_thread.start()
            self._source = Acquisition(metadata["session_id"], metadata["source"],
                                       self._on_batch, self._on_event, port, replay_path, duration)
            if self._stop.is_set():
                self._source.stop()
            self._source.start()
            next_storage_check = time.monotonic()
            previous_wall, previous_mono = time.time(), time.monotonic()
            previous_storage_state = None
            while self._source.is_alive():
                self._source.join(.15)
                if duration is not None and time.monotonic()-self._started >= duration and not self._stop.is_set():
                    self._duration_complete = True
                    self.stop()
                if self._stop.is_set():
                    self._source.stop()
                status = self._recorder.status()
                if status.get("error"):
                    self._fail("保存失败：" + str(status["error"]))
                if time.monotonic() >= next_storage_check:
                    capacity = self.storage_status()
                    if capacity['state'] != previous_storage_state:
                        self._recorder.add_event('storage_status_changed', capacity)
                        previous_storage_state = capacity['state']
                    if capacity['state'] == 'critical' and not self._stop.is_set():
                        self._recorder.add_event('storage_low_stop', capacity)
                        self._storage_stop_reason = True
                        self.stop()
                    wall, mono = time.time(), time.monotonic()
                    drift = (wall-previous_wall)-(mono-previous_mono)
                    if abs(drift) > 2:
                        self._recorder.add_event('clock_adjustment_observed', {
                            'wall_clock_shift_seconds': drift, 'received_sample_count': self._state['received'],
                            'note': 'sample_id remains the alignment reference'})
                    previous_wall, previous_mono = wall, mono
                    next_storage_check = mono+5
            reason = 'storage_low' if self._storage_stop_reason else self._source.finished_reason
            if self._duration_complete and not self._source.error:
                reason = 'duration_complete'
            if self._source.error:
                self._fail(self._source.error)
        except Exception as exc:
            LOG.exception("Session failed")
            reason = "application_error"
            self._fail(str(exc))
        finally:
            with self._lock:
                self._interrupt_mode_check("采集已停止")
                self._state.update(state="STOPPING", status=self._state["error"] or "正在保存与结束",
                                   hr=None, rr_ms=None, hr_allowed=False)
            if self._source and self._source.is_alive():
                self._source.stop()
                self._source.join(3)
                if self._source.is_alive():
                    self._fail("串口线程未能正常退出")
            self._analysis_done.set()
            if self._classification:
                self._classification.stop()
                if not self._classification.wait(5):
                    self._recorder.add_event("classification_error", {"message": "分类线程未退出"})
                model_status = self._classification.snapshot()
                self._recorder.add_event("cardioedge_summary", model_status)
            if analysis_thread:
                analysis_thread.join(5)
                if analysis_thread.is_alive():
                    self._fail("分析线程未能正常退出")
            with self._lock:
                if not self._state["error"]:
                    self._state["quality"] = "采集已停止"
                    self._set_signal_state("waiting", "采集已停止", "重新开始采集后再判断信号")
            if self._recorder:
                try:
                    self._recorder.add_event('measurement_summary', self._measurement.snapshot())
                    self._recorder.finish(reason=reason, complete=not bool(self._state["error"]))
                    stored = self._recorder.status()
                    if stored.get("error"):
                        self._fail("保存失败：" + str(stored["error"]))
                    with self._lock:
                        self._state["saved"] = stored.get("saved", 0)
                except Exception as exc:
                    self._fail("保存结束失败：" + str(exc))
            with self._lock:
                failed = bool(self._state["error"])
                self._state.update(state="FAULT" if failed else "IDLE", running=False,
                                   status=self._state["error"] or ("空间不足，已停止并保存" if self._storage_stop_reason else "已停止并保存，可查看历史记录"),
                                   elapsed_s=time.monotonic() - self._started, hr=None, rr_ms=None,
                                   hr_allowed=False, serial_aligned=False,
                                   connection_state="error" if failed else "stopped",
                                   connection_text="采集异常，已停止接收" if failed else "采集已停止")

    def _set_signal_state(self, state, text, detail="", metrics=None):
        """Called under _lock; record transitions only, never a 10 Hz metric log."""
        changed = state != self._state["signal_state"]
        self._state.update(signal_state=state, signal_text=text, signal_detail=detail,
                           quality_metrics=deepcopy(metrics or {}))
        if changed and self._recorder:
            self._recorder.add_event("signal_quality_changed", {
                "signal_state": state, "signal_text": text, "signal_detail": detail,
                "quality_metrics": deepcopy(metrics or {}),
                "received_sample_count": self._state["received"]})

    def _reset_context(self, quality, signal_state="collecting", detail="", clear_wave=True):
        """Called under _lock: reject in-flight results from the previous context."""
        self._generation += 1
        if self._classification:
            self._classification.invalidate(quality)
        if clear_wave:
            self._wave.clear()
        self._state.update(hr=None, rr_ms=None, quality=quality, hr_allowed=False,
                           preprocessing_active=False, preprocessing_warmup=False)
        self._set_signal_state(signal_state, quality, detail)

    @staticmethod
    def _mode_name(mode):
        return {0: "实采", 1: "板端模拟"}.get(mode, "未知模式")

    def _interrupt_mode_check(self, reason):
        if self._state["mode_check_state"] == "waiting":
            self._state.update(mode_check_state="interrupted", mode_feedback="切换检查已中断：" + reason)
            self._mode_deadline = None
            if self._recorder:
                self._recorder.add_event("mode_check_interrupted", {"reason": reason})

    def _expire_mode_check(self, now):
        if (self._state["mode_check_state"] == "waiting" and self._mode_deadline is not None
                and now >= self._mode_deadline):
            self._state.update(mode_check_state="timeout", mode_feedback="未收到模式变化，仍为"
                               + self._mode_name(self._state["board_mode"]) + "；请检查 SW2 与接线")
            self._mode_deadline = None
            if self._recorder:
                self._recorder.add_event("mode_check_timeout", {
                    "expected_mode": self._state["mode_expected"], "received_mode": self._state["board_mode"]})

    def begin_mode_check(self, timeout_s=10):
        """Observe a physical SW2 press; this never sends a command to STM32."""
        if not np.isfinite(timeout_s) or not 0 < timeout_s <= 60:
            raise ValueError("检查时长须在 0～60 秒之间")
        with self._lock:
            now = time.monotonic()
            self._expire_mode_check(now)
            if not (not self._stop.is_set() and self._state["source"] == "serial" and self._state["running"]
                    and self._state["state"] == "RECORDING" and self._state["serial_aligned"]
                    and self._state["board_mode"] in (0, 1)):
                raise RuntimeError("请先接收 STM32 的有效协议帧并确认当前模式")
            if self._state["mode_check_state"] == "waiting":
                raise RuntimeError("正在等待按键切换，请完成当前检查")
            target = 1 - self._state["board_mode"]
            self._mode_deadline = now + float(timeout_s)
            self._state.update(mode_check_state="waiting", mode_expected=target,
                               mode_feedback="请按 STM32 的 SW2，等待" + self._mode_name(target) + "确认")
            if self._recorder:
                self._recorder.add_event("mode_check_started", {
                    "current_mode": self._state["board_mode"], "expected_mode": target,
                    "timeout_s": float(timeout_s), "host_command_sent": False})
            return target

    def _observe_mode(self, mode):
        """A new validated MODE is the only evidence of a successful switch."""
        self._expire_mode_check(time.monotonic())
        old = self._state["board_mode"]
        if mode == old:
            return
        self._reset_context("来源切换 · 等待有效心拍")
        self._state["board_mode"] = mode
        if self._state["source"] != "serial":
            self._state["mode_feedback"] = ""
            return
        if mode not in (0, 1):
            self._interrupt_mode_check("当前模式未知")
            self._state["mode_feedback"] = "等待确认 STM32 模式"
            return
        if old not in (0, 1):
            # A first frame or a reconnect is confirmation, not a switch.
            if self._state["mode_check_state"] != "interrupted":
                self._state["mode_feedback"] = "已确认" + self._mode_name(mode)
            return
        self._state["mode_change_count"] += 1
        if (self._state["mode_check_state"] == "waiting"
                and mode == self._state["mode_expected"]):
            self._state.update(mode_check_state="confirmed", mode_feedback="切换已确认：" + self._mode_name(mode))
            self._mode_deadline = None
            if self._recorder:
                self._recorder.add_event("mode_check_confirmed", {"previous_mode": old, "mode": mode})
        else:
            self._state.update(mode_check_state="idle", mode_expected=None,
                               mode_feedback="已切换为" + self._mode_name(mode))
        if self._recorder:
            self._recorder.add_event("mode_switch_observed", {
                "previous_mode": old, "mode": mode, "received_sample_count": self._state["received"]})

    def _on_event(self, kind, detail):
        if self._recorder:
            self._recorder.add_event(kind, detail)
        with self._lock:
            # A read already in progress may emit events after Stop. Preserve
            # their log, but never let them reopen the stopped live status.
            if self._stop.is_set() and kind != "error":
                return
            if kind == "opened":
                serial = self._state["source"] == "serial"
                self._state.update(state="WAITING_SYNC" if serial else "RECORDING",
                                   status=detail.get("message", "接收中"),
                                   connection_state="open_waiting" if serial else self._state["source"],
                                   connection_text="串口已打开，等待数据" if serial else
                                       ("本地模拟运行中" if self._state["source"] == "demo" else "正在回放文件"))
            elif kind == "aligned":
                self._state.update(state="RECORDING", status="接收中 · 原始数据持续保存",
                                   serial_aligned=self._state["source"] == "serial")
                if self._state["source"] == "serial":
                    self._state.update(connection_state="receiving", connection_text="串口接收正常")
            elif kind == "resumed":
                self._state.update(status="数据恢复 · 重新积累分析上下文")
            elif kind == "mode_changed":
                mode = detail.get("mode")
                if mode not in (0, 1):
                    mode = None
                if self._state["source"] in ("serial", "replay"):
                    self._observe_mode(mode)
            elif kind == "protocol_resync":
                self._interrupt_mode_check("串口重新对齐，请重新开始检查")
                self._reset_context("串口重新对齐", "collecting", "损坏帧已跳过，等待连续有效数据")
                locked = bool(detail.get("locked", False))
                self._state.update(state="RECORDING" if locked else "WAITING_SYNC",
                                   status="已跳过损坏数据 · 重新积累有效心拍" if locked else
                                          "串口数据校验失败 · 正在搜索有效帧",
                                   serial_aligned=locked, board_mode=None,
                                   connection_state="receiving" if locked else "waiting_sync",
                                   connection_text="串口接收正常" if locked else "收到数据，帧校验异常")
            elif kind == "protocol_stats":
                self._state["protocol_stats"] = dict(detail)
            elif kind == "timeout":
                self._interrupt_mode_check("串口数据中断")
                self._reset_context("数据中断", "interrupted", "检查 STM32 供电与串口连接")
                self._last_received_point = None
                self._state.update(state="WAITING_SYNC", status=detail["message"],
                                   serial_aligned=False, board_mode=None,
                                   connection_state="interrupted", connection_text="串口已打开，数据中断")
            elif kind in ("no_data", "sync_wait"):
                self._interrupt_mode_check("尚未收到有效协议帧")
                self._reset_context("等待有效数据", "waiting", detail["message"])
                interrupted = kind == "no_data" and self._state["received"] > 0
                self._state.update(state="WAITING_SYNC", status=detail["message"],
                                   serial_aligned=False, board_mode=None,
                                   connection_state="interrupted" if interrupted else
                                       "open_waiting" if kind == "no_data" else "waiting_sync",
                                   connection_text="串口断流，等待恢复" if interrupted else
                                       "串口已打开，未收到数据" if kind == "no_data" else "收到串口字节，等待有效帧")
            elif kind == "error":
                self._fail(detail["message"])

    def _on_batch(self, batch):
        # A final read may finish after Stop was requested. Persist that received block too.
        with self._lock:
            if batch.session_id != self._state["session_id"]:
                return False
            self._state["received"] += len(batch.points)
            if batch.source == "serial":
                self._state["serial_bytes"] += len(batch.wire)
        if not self._recorder.submit(batch):
            self._fail(self._recorder.status().get("error") or "原始记录队列已满，已停止采集")
            return False
        if not batch.points:
            return True
        with self._lock:
            # The final received block above still belongs in the raw record;
            # it must not revive mode, quality, or analysis after Stop.
            if self._stop.is_set():
                return True
            if batch.source in ("serial", "replay"):
                modes = [getattr(point, "mode", None) for point in batch.points]
                # Acquisition normally splits at MODE changes. Keep recording
                # every point even if another caller supplies a mixed batch,
                # but never label a previous-mode trace as the newest mode.
                last_change = max((i for i in range(1, len(modes))
                                   if modes[i] != modes[i - 1]), default=0)
                for index, mode in enumerate(modes):
                    if index == 0 or mode != modes[index - 1]:
                        self._observe_mode(mode)
                if last_change:
                    self._state["analysis_dropped_samples"] += last_change
                    batch = replace(batch, points=batch.points[last_change:])
            # Do not leave a previous good HR visible while an analysis worker
            # is still processing a queued block preceding a known bad sample.
            barrier = False
            bad_seen = False
            previous = self._last_received_point
            for point in batch.points:
                bad = (not point.valid)
                bad_seen |= bad
                barrier |= bool(point.flags & (DISCONTINUITY | MODE_CHANGE | STREAM_RESYNC))
                if previous is not None:
                    barrier |= (point.sample_id != previous.sample_id + 1
                                or point.segment_id != previous.segment_id
                                or point.segment_index != previous.segment_index + 1)
                previous = point
            self._last_received_point = previous
            last = batch.points[-1]
            bad_state = ("invalid" if last.raw_adc == 65535 or last.flags & INVALID else
                         "saturated" if last.raw_adc in (0, 4095) or last.flags & SATURATED else None)
            needs_barrier = (barrier or bad_state != self._rx_bad_state
                             or (bad_seen and bad_state is None))
            if needs_barrier:
                text = {"invalid": "无效数据", "saturated": "输入饱和"}.get(bad_state, "重新积累有效信号")
                detail = ("收到无效标记；可能为导联状态或模式切换，协议未给出具体原因"
                          if bad_state == "invalid" else "ADC 到达量程边界，暂不计算心率"
                          if bad_state == "saturated" else "数据连续性发生变化，重新积累心拍")
                self._reset_context(text, bad_state or "collecting", detail, clear_wave=False)
            elif bad_state:
                self._state.update(hr=None, rr_ms=None, hr_allowed=False)
                self._set_signal_state(bad_state, "无效数据" if bad_state == "invalid" else "输入饱和",
                    "收到无效标记；可能为导联状态或模式切换，协议未给出具体原因" if bad_state == "invalid"
                    else "ADC 到达量程边界，暂不计算心率")
            self._rx_bad_state = bad_state
            generation = self._generation
            if self._classification:
                self._classification.submit(batch)
        try:
            self._analysis_queue.put_nowait((generation, batch))
        except queue.Full:
            with self._lock:
                self._generation += 1
                self._state["analysis_dropped_samples"] += len(batch.points)
                if not self._stop.is_set():
                    self._state.update(hr=None, rr_ms=None, hr_allowed=False, quality="分析落后，原始数据仍在保存")
                    self._set_signal_state("interrupted", "分析暂时落后", "原始数据仍在保存，心率暂停更新")
            self._recorder.add_event("analysis_gap", dict(first=batch.points[0].sample_id,
                                                         last=batch.points[-1].sample_id))
        return True

    def _analyze(self, analyzer, analysis_queue, finished, session_id):
        active_generation = -1
        try:
            while True:
                try:
                    item_generation, batch = analysis_queue.get(timeout=.1)
                except queue.Empty:
                    if finished.is_set():
                        break
                    item_generation, batch = None, None
                with self._lock:
                    if self._state["session_id"] != session_id:
                        return
                    generation = self._generation
                if active_generation != generation:
                    analyzer.reset()
                    active_generation = generation
                    with self._lock:
                        if self._wave:
                            self._wave.append((self._wave[-1][0] + 1 / 720, np.nan, np.nan, -1))
                if batch is None:
                    result = analyzer.snapshot()
                else:
                    if item_generation != active_generation:
                        with self._lock:
                            self._state["analysis_dropped_samples"] += len(batch.points)
                        continue
                    result = analyzer.process(batch)
                    self._measurement.observe(batch,result)
                    wave = result.get("wave", {})
                    with self._lock:
                        if active_generation == self._generation and not self._stop.is_set():
                            self._wave.extend(zip(wave.get("x", []), wave.get("raw", []),
                                                  wave.get("filtered", []),
                                                  wave.get("segment_ids", [-1]*len(wave.get("x", [])))))
                with self._lock:
                    if (active_generation == self._generation
                            and not self._stop.is_set()
                            and self._state["running"] and self._state["state"] != "STOPPING"
                            and (batch is not None or self._state["state"] != "WAITING_SYNC")):
                        for key in ("hr", "rr_ms", "quality", "preprocessing_active", "preprocessing_warmup", "hr_allowed"):
                            if key in result:
                                self._state[key] = result[key]
                        if "signal_state" in result:
                            self._set_signal_state(result["signal_state"], result.get("signal_text", result.get("quality", "")),
                                                   result.get("signal_detail", ""), result.get("quality_metrics", {}))
                        if (not self._state["hr_allowed"] or self._state["state"] != "RECORDING"
                                or not self._state["running"]):
                            self._state.update(hr=None, rr_ms=None, hr_allowed=False)
        except Exception as exc:
            LOG.exception("Analysis worker failed")
            with self._lock:
                if not self._stop.is_set():
                    self._state.update(hr=None, rr_ms=None, hr_allowed=False, quality="分析失败：" + str(exc))
                    self._set_signal_state("interrupted", "分析异常", str(exc))
            if self._recorder:
                self._recorder.add_event("analysis_error", {"message": str(exc)})

    def stop(self):
        self._stop.set()
        if self._classification:
            self._classification.stop()
        if self._source:
            self._source.stop()
        with self._lock:
            self._interrupt_mode_check("采集已停止")
            if self._state["running"]:
                self._reset_context("采集已停止", "waiting", "重新开始采集后再判断信号", clear_wave=False)
                self._state.update(state="STOPPING", status="正在停止并保存", hr=None, rr_ms=None,
                                   hr_allowed=False, connection_state="stopped", connection_text="正在停止接收")

    def wait(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)
        return not (self._thread and self._thread.is_alive())

    def snapshot(self, window_s=10):
        if window_s not in (2, 5, 10, 30):
            window_s = 10
        stored = self._recorder.status() if self._recorder else {}
        with self._lock:
            now = time.monotonic()
            self._expire_mode_check(now)
            result = dict(self._state)
            result['storage'] = deepcopy(self._storage_info)
            result['time_info'] = deepcopy(self._clock_info)
            result["analysis_generation"] = self._generation
            result["protocol_stats"] = dict(self._state["protocol_stats"])
            result["quality_metrics"] = deepcopy(self._state["quality_metrics"])
            result["classification"] = (self._classification.snapshot() if self._classification else
                {"state": "waiting" if self.classification_enabled else "disabled",
                 "detail": "等待实采信号" if self.classification_enabled else "分类已关闭", "last": None})
            if self._classification is None and result["source"] == "demo":
                result["classification"].update(state="simulation",detail="模拟数据不做分类")
            elif self._classification is None and result["source"] == "replay":
                result["classification"].update(state="waiting",detail="回放来源未标明，不做分类")
            if not result["running"] or result["state"] != "RECORDING":
                result["classification"]["last"] = None
            result["mode_check_remaining_s"] = max(0., self._mode_deadline - now) if self._mode_deadline is not None else 0.
            if result["running"] and self._started is not None:
                result["elapsed_s"] = now - self._started
            duration = result.get('requested_duration_s')
            result['remaining_s'] = max(0.,duration-result['elapsed_s']) if duration is not None else None
            result["saved"] = stored.get("saved", result["saved"])
            result["pending"] = stored.get("pending", 0)
            result["recording"] = dict(stored)
            wave = self._wave.array()
        if len(wave):
            finite = np.isfinite(wave[:, 0])
            if finite.any():
                right = wave[finite, 0][-1]
                # Keep NaN separator rows: removing them would bridge explicit segment breaks.
                wave = wave[(wave[:, 0] >= right - window_s) | ~finite]
        result["wave"] = dict(x=wave[:, 0].copy(), raw=wave[:, 1].copy(), filtered=wave[:, 2].copy(),
                              segment_ids=wave[:, 3].copy())
        return result

    def classification_details(self, session_id=None):
        with self._lock:
            session_id = session_id or self._state.get('session_id')
        if not session_id:
            raise ValueError('请先采集一次，或从历史记录选择会话')
        with self._operation_lock:
            return classification_review.classification_details(self.data_dir, session_id)

    def review_beat(self, session_id, r_sample, segment_id, before_s=2, after_s=2):
        with self._operation_lock:
            return classification_review.review_beat(self.data_dir, session_id, r_sample, segment_id,
                                                     before_s=before_s, after_s=after_s)

    def get_settings(self):
        with self._lock:
            return deepcopy(self._settings)

    def update_settings(self, values):
        with self._operation_lock:
            with self._lock:
                if self._state['running']:
                    for key in ('vref', 'classification_enabled'):
                        if key in values and values[key] != self._settings[key]:
                            raise RuntimeError('请先停止采集再修改参考电压或模型开关')
                candidate = {**self._settings, **values}
            # fsync on a slow SD card must not hold the acquisition/UI lock.
            updated = save_settings(self.data_dir, candidate)
            with self._lock:
                self._settings = updated
                self.classification_enabled = updated['classification_enabled']
            return deepcopy(updated)

    def storage_status(self):
        settings = self.get_settings()
        result = read_storage_status(self.data_dir, settings['low_space_warn_mb'], settings['low_space_stop_mb'])
        with self._lock:
            self._storage_info = result
        return deepcopy(result)

    def clock_status(self):
        result = read_clock_status()
        with self._lock:
            # Preserve provenance of a successful manual setting until NTP is
            # confirmed; a fresh OS read alone cannot certify manual accuracy.
            if (not result.get('trusted') and self._clock_info.get('source') == 'manual'
                    and self._clock_info.get('success')):
                result.update(source='manual', detail='已手动设置，尚未联网核验', trusted=False,
                              last_manual_set=self._clock_info.get('last_manual_set'), success=True)
            self._clock_info = result
        return deepcopy(result)

    def _ensure_idle(self):
        with self._lock:
            if self._state['running'] or (self._thread and self._thread.is_alive()):
                raise RuntimeError('请先停止采集并等待保存完成')

    def sync_time(self):
        with self._operation_lock:
            self._ensure_idle()
            result = request_time_sync()
            with self._lock:
                self._clock_info = result
            return deepcopy(result)

    def authorize_time(self, password):
        with self._operation_lock:
            self._ensure_idle()
            return authorize_time(password)

    def set_time(self, local_iso):
        with self._operation_lock:
            self._ensure_idle()
            result = manual_set_time(local_iso)
            if result.get('success'):
                result['last_manual_set'] = result.get('utc')
            with self._lock:
                self._clock_info = result
            return deepcopy(result)

    def initialize_environment(self):
        if self._environment_thread and self._environment_thread.is_alive():
            return
        self._environment_stop.clear()
        def monitor():
            self.storage_status()
            self.clock_status()
            if self.get_settings()['auto_time_sync']:
                try:
                    self.sync_time()
                except RuntimeError:
                    pass  # A session already started; do not change its clock.
            next_clock = time.monotonic()+60
            while not self._environment_stop.wait(5):
                self.storage_status()
                if time.monotonic() >= next_clock:
                    self.clock_status()
                    next_clock = time.monotonic()+60
        self._environment_thread = threading.Thread(target=monitor, name='ecg-storage-clock', daemon=True)
        self._environment_thread.start()

    def shutdown_environment(self, timeout=1):
        self._environment_stop.set()
        if self._environment_thread:
            self._environment_thread.join(timeout)
        return not (self._environment_thread and self._environment_thread.is_alive())

    def list_sessions(self, trashed=False):
        return session_management.list_history(self.data_dir, trashed=trashed)

    def list_cloud_sessions(self):
        # The upload selector only needs indexed metadata, not a recursive
        # scan of every raw chunk/CSV in every historical recording.
        return session_management.list_history(self.data_dir, include_size=False)

    def _remember_export(self, path):
        if self.get_settings()['remember_export_dir']:
            try:
                self.update_settings({'last_export_dir': str(Path(path).resolve().parent)})
            except (OSError, ValueError) as exc:
                logging.warning('导出已完成，但无法记住导出目录：%s', exc)
        return str(path)

    def export_session(self, session_id, path):
        with self._operation_lock:
            self._ensure_idle()
            return self._remember_export(session_management.export_samples(self.data_dir, session_id, path))

    def export_bundle(self, session_id, path, include_raw=True):
        with self._operation_lock:
            self._ensure_idle()
            return self._remember_export(session_management.export_bundle(self.data_dir, session_id, path, include_raw=include_raw))

    def measurement_summary(self, session_id):
        from measurement import build_summary
        return build_summary(self.data_dir,session_id)

    def export_measurement_pdf(self,session_id,path):
        from measurement import export_pdf
        # The export guard locks its finished source, not the start/stop path.
        # A new session can acquire while an older report is being rendered.
        return export_pdf(self.data_dir,session_id,path)

    def trash_session(self, session_id):
        with self._operation_lock:
            self._ensure_idle()
            result = session_management.trash_session(self.data_dir, session_id)
            self.storage_status()
            return result

    def restore_session(self, session_id):
        with self._operation_lock:
            self._ensure_idle()
            return session_management.restore_session(self.data_dir, session_id)

    def purge_session(self, session_id):
        with self._operation_lock:
            self._ensure_idle()
            result = session_management.purge_session(self.data_dir, session_id)
            self.storage_status()
            return result
