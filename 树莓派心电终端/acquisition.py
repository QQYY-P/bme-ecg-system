"""Cancellable serial, synthetic, and CSV replay sources for the same acquisition pipeline."""
import csv
import math
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from contracts import (SampleBatch, SamplePoint, SAMPLE_RATE, INVALID, SATURATED,
                       DISCONTINUITY, MODE_CHANGE, STREAM_RESYNC)
from protocol import BLOCK_SAMPLES, FRAME_BYTES, Sample, StreamDecoder, encode_frame, MODE_SIMULATED
from serial_connection import open_serial_port, serial_error_message


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class PointBuilder:
    """Keep received ordinal and continuity separate; never infer absolute lost samples."""

    def __init__(self):
        self.sample_id = 0
        self.segment_id = 0
        self.segment_index = 0
        self.previous_valid = None
        self.previous_mode = None
        self.have_previous = False
        self.pending_gap = False

    def break_segment(self):
        self.pending_gap = True

    def build(self, samples, segment_starts=()):
        points = []
        segment_starts = set(segment_starts)
        for index, sample in enumerate(samples):
            valid = 0 < sample.value < 4095
            mode_changed = self.have_previous and sample.mode != self.previous_mode
            boundary = self.pending_gap or sample.discontinuity or index in segment_starts
            boundary |= mode_changed or sample.resynchronized
            boundary |= self.previous_valid is not None and valid != self.previous_valid
            if boundary and self.sample_id:
                self.segment_id += 1
                self.segment_index = 0
            flags = (INVALID if sample.value == 65535 else 0)
            flags |= SATURATED if sample.value in (0, 4095) else 0
            flags |= DISCONTINUITY if sample.discontinuity else 0
            flags |= MODE_CHANGE if mode_changed else 0
            flags |= STREAM_RESYNC if sample.resynchronized else 0
            points.append(SamplePoint(self.sample_id, self.segment_id, self.segment_index,
                                      sample.sequence, sample.value, flags, sample.mode))
            self.sample_id += 1
            self.segment_index += 1
            self.previous_valid = valid
            self.previous_mode = sample.mode
            self.have_previous = True
            self.pending_gap = False
        return tuple(points)


def demo_value(index, rng):
    """Synthetic ADC counts for a 72 bpm display demonstration, not clinical reference data."""
    t = index / SAMPLE_RATE
    phase = (t % (60 / 72))
    def pulse(center, width, amplitude):
        return amplitude * math.exp(-0.5 * ((phase - center) / width) ** 2)
    value = (2048 + pulse(.12, .025, 65) + pulse(.275, .012, -110)
             + pulse(.30, .009, 680) + pulse(.325, .013, -190)
             + pulse(.51, .055, 150) + 55 * math.sin(2 * math.pi * .23 * t)
             + 14 * math.sin(2 * math.pi * 50 * t) + rng.gauss(0, 2))
    return max(1, min(4094, round(value)))


class Acquisition(threading.Thread):
    def __init__(self, session_id, source, on_batch, on_event, port=None,
                 replay_path=None, duration=None):
        super().__init__(name="ecg-acquisition", daemon=True)
        if source not in ("demo", "serial", "replay"):
            raise ValueError("未知数据源")
        self.session_id, self.source = session_id, source
        self.on_batch, self.on_event = on_batch, on_event
        self.port, self.replay_path = port, replay_path
        self.duration = duration
        self.stop_event = threading.Event()
        self.builder = PointBuilder()
        self.decoder = StreamDecoder()
        self._board_mode = None
        self.error = None
        self.finished_reason = "stopped"

    def stop(self):
        self.stop_event.set()

    def _emit(self, samples, wire=b"", segment_starts=()):
        batch = SampleBatch(self.session_id, self.source, self.builder.build(samples, segment_starts),
                            time.monotonic(), utc_now(), wire)
        if not self.on_batch(batch):
            self.stop_event.set()
            self.finished_reason = "downstream_stopped"
            return False
        return True

    def _decode(self, wire):
        was_locked = self.decoder.locked
        statistics_before = (self.decoder.discarded, self.decoder.bad_frames,
                             self.decoder.checksum_errors, self.decoder.resync_count)
        samples = self.decoder.feed(wire)
        if samples and not was_locked:
            self.on_event("aligned", {"discarded_bytes": self.decoder.discarded})
        # Preserve every received byte once, including rejected frames. Split
        # before source/resync boundaries so old analysis cannot acquire a new label.
        groups = []
        for sample in samples:
            if (not groups or sample.resynchronized
                    or sample.mode != groups[-1][-1].mode):
                groups.append([])
            groups[-1].append(sample)
        pending_wire = wire
        for group in groups:
            first = group[0]
            if first.resynchronized:
                self.on_event("protocol_resync", {"locked": True,
                              "message": "串口校验后重新对齐 · 重新积累有效心拍"})
            if self.source == "serial" and first.mode != self._board_mode:
                self.on_event("mode_changed", {"previous_mode": self._board_mode,
                                                "mode": first.mode})
                self._board_mode = first.mode
            if not self._emit(group, pending_wire):
                return False
            pending_wire = b""
        if not groups:
            if not self._emit([], wire):
                return False
        if not self.decoder.locked and (was_locked or samples):
            self._board_mode = None
            self.on_event("protocol_resync", {"locked": False,
                          "message": "串口帧校验失败 · 正在重新寻找七字节帧"})
        statistics = {"discarded_bytes": self.decoder.discarded,
                      "bad_frames": self.decoder.bad_frames,
                      "checksum_errors": self.decoder.checksum_errors,
                      "resync_count": self.decoder.resync_count}
        if tuple(statistics.values()) != statistics_before:
            self.on_event("protocol_stats", statistics)
        if self.decoder.fault:
            raise ValueError(self.decoder.fault)
        return True

    def run(self):
        try:
            if self.source == "serial":
                self._serial()
            elif self.source == "demo":
                self._demo()
            else:
                self._replay()
        except Exception as exc:
            self.error = str(exc)
            self.finished_reason = "source_error"
            self.on_event("error", {"message": self.error})
        finally:
            self.on_event("source_finished", {"reason": self.finished_reason})

    def _serial(self):
        import serial
        try:
            with open_serial_port(self.port) as port:
                port.reset_input_buffer()
                self.on_event("opened", {"port": self.port,
                                          "message": "串口已打开 · 等待 STM32 数据自动对齐"})
                started = last_data = time.monotonic()
                interrupted, received_bytes = False, 0
                silence_reported = False
                last_hint = None
                while not self.stop_event.is_set():
                    wire = port.read(BLOCK_SAMPLES * FRAME_BYTES)
                    now = time.monotonic()
                    if wire:
                        received_bytes += len(wire)
                        was_locked = self.decoder.locked
                        if not self._decode(wire):
                            break
                        last_data = now
                        silence_reported = False
                        if was_locked and not self.decoder.locked:
                            started = now
                            last_hint = None
                        if interrupted and self.decoder.locked:
                            self.on_event("resumed", {})
                            interrupted = False
                    elif received_bytes and not silence_reported and now - last_data > 2:
                        self.builder.break_segment()
                        # A pause can span a board reset or a lost byte. Never join
                        # a partial old frame to the next stream after reconnection.
                        discarded = len(self.decoder.buffer)
                        self.decoder = StreamDecoder()
                        self._board_mode = None
                        interrupted = True
                        silence_reported = True
                        started = now
                        last_hint = None
                        self.on_event("timeout", {"message": "串口数据中断 · 检查 STM32 供电与 TX 接线",
                                                   "discarded_partial_bytes": discarded})
                    if not self.decoder.locked and now - started >= 3:
                        kind = "no_data" if now - last_data >= 3 else "sync_wait"
                        if kind != last_hint:
                            message = ("串口已打开但没有数据 · 检查 PA9→RX、共地及 STM32 供电"
                                       if kind == "no_data" else
                                       "收到字节但尚未对齐 · 确认 115200/8N1 与 AA55 七字节固件")
                            self.on_event(kind, {"message": message, "received_bytes": received_bytes})
                            last_hint = kind
        except (serial.SerialException, OSError) as exc:
            raise RuntimeError(serial_error_message(self.port, exc)) from exc

    def _demo(self):
        self.on_event("opened", {"message": "合成模拟 · 72 bpm · 非实采"})
        rng = random.Random(42)
        index = 0
        start = time.monotonic()
        limit = None if self.duration is None else max(1, round(self.duration * SAMPLE_RATE))
        while not self.stop_event.is_set() and (limit is None or index < limit):
            count = min(36, limit - index) if limit is not None else 36
            wire = bytearray()
            for n in range(index, index + count):
                value = demo_value(n, rng)
                wire.extend(encode_frame(n % 256, value, MODE_SIMULATED))
            if not self._decode(bytes(wire)):
                break
            index += count
            self.stop_event.wait(max(0, start + index / SAMPLE_RATE - time.monotonic()))
        if not self.stop_event.is_set():
            self.finished_reason = "duration_complete"

    def _replay(self):
        path = Path(self.replay_path)
        self.on_event("opened", {"message": "文件回放 · " + path.name})
        previous_seq, previous_segment = None, None
        samples, wire = [], bytearray()
        segment_starts, pending_break = [], False
        start, count = time.monotonic(), 0

        def flush_pending(pace=True):
            nonlocal samples, wire, segment_starts
            if not samples:
                return not self.stop_event.is_set()
            pending, pending_wire, pending_starts = samples, bytes(wire), segment_starts
            samples, wire, segment_starts = [], bytearray(), []
            if not self._emit(pending, pending_wire, pending_starts):
                return False
            # Keep fixed-size queue items even when every point starts a new
            # segment, so a slow SD-card commit has several seconds of buffering.
            if pace:
                self.stop_event.wait(max(0, start + count / SAMPLE_RATE - time.monotonic()))
            return not self.stop_event.is_set()

        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = csv.DictReader(handle)
                if not rows.fieldnames or "raw_adc" not in rows.fieldnames:
                    raise ValueError("回放 CSV 必须包含 raw_adc 列")
                for row in rows:
                    if self.stop_event.is_set():
                        break
                    if not row.get("raw_adc", "").strip():
                        pending_break = True
                        continue
                    segment = row.get("segment_id", "0")
                    value = int(row["raw_adc"])
                    if not StreamDecoder.legal(value):
                        raise ValueError("回放文件包含非法 ADC 值")
                    seq = int(row.get("sequence") or (count % 256))
                    if not 0 <= seq <= 255:
                        raise ValueError("回放序号超出 0..255")
                    flags = int(row.get("flags") or 0)
                    delta = 1 if previous_seq is None else (seq - previous_seq) & 255
                    if pending_break or (previous_segment is not None and segment != previous_segment):
                        segment_starts.append(len(samples))
                    pending_break = False
                    mode_text = (row.get("mode") or "").strip()
                    mode = None if not mode_text else int(mode_text)
                    if mode not in (None, 0, 1):
                        raise ValueError("回放模式须为0、1或空值")
                    if flags & MODE_CHANGE:
                        segment_starts.append(len(samples))
                    samples.append(Sample(seq, value, max(1, delta),
                                          delta != 1 or bool(flags & DISCONTINUITY),
                                          mode, bool(flags & STREAM_RESYNC)))
                    # CSV contains decoded values, not the original serial bytes.
                    # Do not fabricate a wire stream (especially for legacy unknown modes).
                    previous_seq, previous_segment = seq, segment
                    count += 1
                    if len(samples) == 36:
                        if not flush_pending():
                            break
            flush_pending()
        finally:
            # Preserve the already-parsed tail even if Stop or a malformed CSV row interrupted replay.
            flush_pending(pace=False)
        if not self.stop_event.is_set():
            self.finished_reason = "replay_complete"
