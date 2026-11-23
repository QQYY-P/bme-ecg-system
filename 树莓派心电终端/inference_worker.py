"""Bounded, separate-process inference without blocking acquisition or recording.

Only this child process imports the model; all three bundled models are retained.
Generation tags, explicit indices and an independent shared generation invalidate
queued work after gaps without relying on enqueueing a RESET sentinel.
"""
from collections import deque
from datetime import datetime, timezone
import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty, Full
import threading
import time

FS = 360
BLOCK_SAMPLES = 360
QUEUE_BLOCKS = 8
INVALID, SATURATED, DISCONTINUITY = 1, 2, 4


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _worker_main(inputs, outputs, generation_value, feedback_fault, cancelled, directory):
    # Must precede NumPy/SciPy imports in the spawned interpreter.
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"

    def emit(message):
        try:
            outputs.put_nowait(message)
            return True
        except Full:
            feedback_fault.value = 1
            return False

    try:
        import numpy as np
        from ecg_classifier import AcquisitionClassifier
        from numpy_predictor import Predictor
        predictor = Predictor(directory)
        engine = AcquisitionClassifier(predictor)
        if not emit({"type": "status", "status": "ready", "model_id": predictor.model_id}):
            return
        current_key = None
        expected = None
        last_message = None

        def result_message(message, rows, elapsed, queue_wait):
            produced = _utc()
            offset = message["first_sample_id"] - message["start"]
            for row in rows:
                row.update(session_id=message["session_id"], segment_id=message["segment_id"],
                           generation=message["generation"], source_kind=message["source"],
                           peak_sample_id=offset + row["peak"], sample_time_s=row["peak"] / FS,
                           model_id=predictor.model_id, produced_utc=produced,
                           queue_wait_ms=queue_wait * 1000, compute_ms=elapsed * 1000,
                           algorithm_confirmation_delay_ms=row["latency_s"] * 1000)
            covered = None
            if engine.stream is not None and engine.stream.core > engine.stream.segment_start:
                covered = offset + min(engine.stream.core, engine.stream.received) - 1
            return {"type": "result", "task_id": message.get("task_id"),
                    "session_id": message["session_id"], "segment_id": message["segment_id"],
                    "generation": message["generation"], "rows": rows,
                    "processed_through": covered, "compute_ms": elapsed * 1000,
                    "queue_wait_ms": queue_wait * 1000}

        while not cancelled.is_set():
            try:
                message = inputs.get(timeout=0.2)
            except Empty:
                continue
            if message["type"] == "stop":
                if (message["normal_end"] and last_message is not None
                        and last_message["generation"] == generation_value.value):
                    started = time.monotonic()
                    rows = engine.flush()
                    if not emit(result_message(last_message, rows, time.monotonic() - started, 0)):
                        return
                return
            if message["generation"] != generation_value.value:
                if not emit({"type": "ack", "task_id": message["task_id"]}):
                    return
                continue
            key = (message["session_id"], message["segment_id"], message["generation"])
            if key != current_key or message["start"] != expected:
                engine.reset()
                current_key = key
            started = time.monotonic()
            queue_wait = max(0.0, started - message["enqueued_at"])
            raw = np.asarray(message["raw"], dtype=np.float64)
            times = (message["start"] + np.arange(len(raw))) / FS
            rows = engine.process(times, raw)
            expected = message["start"] + len(raw)
            last_message = message
            if not emit(result_message(message, rows, time.monotonic() - started, queue_wait)):
                return
    except Exception as exc:
        emit({"type": "error", "error": f"{type(exc).__name__}: {exc}"})


class InferenceClient:
    def __init__(self, directory=None, *, _context=None):
        self.directory = str(directory or Path(__file__).resolve().parent / "models")
        self._ctx = _context or mp.get_context("spawn")
        self._lock = threading.RLock()
        self._process = None
        self._accepting = False
        self._status = "disabled"
        self._error = None
        self._last_result = None
        self._pending = []
        self._key = None
        self._expected = None
        self._last_sample_id = None
        self._generation = 0
        self._task_id = 0
        self._outstanding = {}
        self._events = deque()
        self._dropped_samples = 0
        self._generation_start = None
        self._confirmed_through = None
        self._last_received_at = None
        self._error_reported = False

    def start(self):
        with self._lock:
            if self._process is not None and self._process.is_alive():
                if not self._accepting:
                    raise RuntimeError("模型正在停止，请等待停止完成后重启")
                return
            if hasattr(self, "_generation_value"):
                self._generation += 1
            self._inputs = self._ctx.Queue(maxsize=QUEUE_BLOCKS)
            self._outputs = self._ctx.Queue(maxsize=64)
            self._generation_value = self._ctx.Value("q", self._generation)
            self._feedback_fault = self._ctx.Value("i", 0)
            self._cancelled = self._ctx.Event()
            self._pending.clear()
            self._outstanding.clear()
            self._key = self._expected = self._last_sample_id = None
            self._generation_start = self._confirmed_through = None
            self._last_received_at = None
            self._last_result = None
            self._error = None
            self._error_reported = False
            self._status = "loading"
            self._accepting = True
            self._process = self._ctx.Process(target=_worker_main,
                args=(self._inputs, self._outputs, self._generation_value,
                      self._feedback_fault, self._cancelled, self.directory), daemon=True)
            try:
                self._process.start()
            except Exception as exc:
                self._accepting = False
                self._status, self._error = "error", str(exc)
                raise

    def _push_event(self, event):
        if len(self._events) >= 64:
            self._status = "error"
            self._error = "模型事件缓冲已满，已停止接收模型任务"
            self._accepting = False
            self._cancelled.set()
            return
        self._events.append(event)

    def _invalidate(self, reason):
        first = self._generation_start
        if self._confirmed_through is not None and first is not None:
            first = max(first, self._confirmed_through + 1)
        last = self._last_sample_id
        if first is not None and last is not None and last >= first:
            count = last - first + 1
            self._dropped_samples += count
            self._push_event({"type": "gap", "reason": reason,
                                 "session_id": self._key[0] if self._key else None,
                                 "segment_id": self._key[1] if self._key else None,
                                 "generation": self._generation,
                                 "first_sample_id": first, "last_sample_id": last,
                                 "samples": count})
        self._generation += 1
        self._generation_value.value = self._generation
        self._pending.clear()
        self._outstanding.clear()
        self._key = self._expected = self._last_sample_id = None
        self._generation_start = self._confirmed_through = None
        self._last_result = None

    def _message(self):
        first = self._pending[0]
        self._task_id += 1
        return {"type": "samples", "task_id": self._task_id,
                "session_id": self._key[0], "segment_id": self._key[1],
                "source": self._key[2], "generation": self._generation,
                "start": first.segment_index, "first_sample_id": first.sample_id,
                "raw": [int(p.raw_adc) for p in self._pending],
                "enqueued_at": time.monotonic()}

    def _enqueue_pending(self, timeout=0):
        if not self._pending:
            return True
        message = self._message()
        try:
            if timeout:
                self._inputs.put(message, timeout=timeout)
            else:
                self._inputs.put_nowait(message)
        except Full:
            self._invalidate("模型输入队列已满，重新积累上下文")
            if not self._error:
                self._status = "overloaded"
            return False
        self._outstanding[message["task_id"]] = len(message["raw"])
        self._pending.clear()
        return True

    def submit(self, batch):
        with self._lock:
            if not self._accepting or self._process is None or not self._process.is_alive():
                return False
            accepted = True
            if (self._last_received_at is not None
                    and batch.received_monotonic - self._last_received_at > 2.5):
                self._invalidate("接收超时")
            if batch.points:
                self._last_received_at = batch.received_monotonic
            for point in batch.points:
                key = (batch.session_id, point.segment_id, batch.source)
                valid = not int(point.flags) & (INVALID | SATURATED) and 0 < int(point.raw_adc) < 4095
                boundary = (bool(int(point.flags) & DISCONTINUITY)
                            or (self._key is not None and key != self._key)
                            or (self._expected is not None and point.segment_index != self._expected)
                            or (self._last_sample_id is not None and point.sample_id != self._last_sample_id + 1))
                if (boundary or not valid) and self._key is not None:
                    self._invalidate("信号无效或采样边界")
                if not self._accepting:
                    return False
                if not valid:
                    continue
                self._key = key
                if self._generation_start is None:
                    self._generation_start = point.sample_id
                self._pending.append(point)
                self._expected = point.segment_index + 1
                self._last_sample_id = point.sample_id
                if len(self._pending) >= BLOCK_SAMPLES:
                    accepted = self._enqueue_pending() and accepted
            return accepted

    def _poll_locked(self):
        events = list(self._events)
        self._events.clear()
        if self._process is None:
            return events
        while True:
            try:
                message = self._outputs.get_nowait()
            except Empty:
                break
            kind = message.get("type")
            if kind in ("result", "ack"):
                self._outstanding.pop(message.get("task_id"), None)
            if kind == "ack":
                continue
            if kind == "result":
                if message["generation"] != self._generation:
                    continue
                covered = message.get("processed_through")
                if covered is not None:
                    self._confirmed_through = max(covered, -1 if self._confirmed_through is None
                                                  else self._confirmed_through)
                if message["rows"]:
                    self._last_result = message["rows"][-1]
                self._status = "running"
            elif kind == "status":
                self._status = message["status"]
            elif kind == "error":
                self._invalidate("模型异常，未完成的上下文已丢弃")
                self._status, self._error = "error", message["error"]
                self._accepting = False
                self._error_reported = True
            events.append(message)
        if self._feedback_fault.value:
            if self._error is None:
                self._invalidate("模型结果反馈中断")
            self._status, self._error = "error", "模型结果队列已满，模型已停止"
            self._accepting = False
        elif self._accepting and not self._process.is_alive() and self._process.exitcode is not None:
            self._invalidate("模型进程意外退出")
            self._status, self._error = "error", f"模型进程已退出（{self._process.exitcode}）"
            self._accepting = False
        if self._error is not None and not self._error_reported:
            events.append({"type": "error", "error": self._error})
            self._error_reported = True
        return events

    def poll(self):
        with self._lock:
            return self._poll_locked()

    def status(self):
        with self._lock:
            return {"status": self._status, "last_result": self._last_result,
                    "dropped_samples": self._dropped_samples, "error": self._error}

    def stop(self, normal_end=True, timeout=3.0):
        deadline = time.monotonic() + max(0, float(timeout))
        with self._lock:
            self._accepting = False
            self._status = "stopping"
            process = self._process
            if process is None:
                self._status = "stopped"
                return
            pending_message = self._message() if normal_end and self._pending else None
            self._pending.clear()
            if not normal_end:
                self._invalidate("异常停止")
        if process.is_alive():
            # Blocking shutdown waits happen outside the lock used by submit().
            try:
                if pending_message:
                    self._inputs.put(pending_message, timeout=max(0.001, deadline - time.monotonic()))
                    with self._lock:
                        self._outstanding[pending_message["task_id"]] = len(pending_message["raw"])
                self._inputs.put({"type": "stop", "normal_end": bool(normal_end)},
                                 timeout=max(0.001, deadline - time.monotonic()))
            except Full:
                with self._lock:
                    self._cancelled.set()
                    self._invalidate("模型停止超时")
                    self._error = "模型停止请求超时；存在未分类数据"
            process.join(max(0, deadline - time.monotonic()))
        with self._lock:
            final_events = self._poll_locked()
            for event in final_events:
                self._push_event(event)
            if process.is_alive():
                self._cancelled.set()
                process.terminate()
                process.join(0.2)
                self._invalidate("模型进程停止超时")
                self._error = "模型停止超时，已终止模型进程；存在未分类数据"
            if self._error and not self._error_reported:
                self._push_event({"type": "error", "error": self._error})
                self._error_reported = True
            self._status = "error" if self._error else "stopped"
            self._process = None
            for queue in (self._inputs, self._outputs):
                if hasattr(queue, "cancel_join_thread"):
                    queue.cancel_join_thread()
                if hasattr(queue, "close"):
                    queue.close()
