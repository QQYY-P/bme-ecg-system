"""Verify the real serial -> recording -> CSV path with Linux pseudo-terminals.

No physical serial device is opened. A PTY master sends the same seven-byte
sample format as UART_EncodeSampleFrame in the STM32 project. These are software
transport tests, not STM32 hardware, baud-accuracy, or human ECG evidence.

Example:
  python3 tools/verify_stm32_serial.py --data-dir /tmp/ecg-serial-check \
      --output /tmp/ecg-serial-result.json
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import select
import sys
import threading
import time
import traceback


def frames(pairs):
    """Equivalent wire order to UART_EncodeSampleFrame, not a text protocol."""
    output = bytearray()
    for row in pairs:
        seq, value = row[:2]
        mode = row[2] if len(row) == 3 else 0
        body = bytes((mode, seq & 255, value >> 8, value & 255))
        output.extend(b"\xaa\x55" + body + bytes((sum(body) & 255,)))
    return bytes(output)


def wait_until(predicate, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise TimeoutError(description)


def snapshot(controller):
    value = controller.snapshot()
    value.pop("wave", None)
    return value


class Harness:
    def __init__(self, controller_type, data_dir):
        import pty
        self.master, self.slave = pty.openpty()
        self.port = os.ttyname(self.slave)
        if not self.port.startswith("/dev/pts/"):
            self.close_fds()
            raise RuntimeError("Refusing to use anything except a newly allocated /dev/pts port")
        os.set_blocking(self.master, False)
        self.controller = controller_type(data_dir)
        self.session_id = None
        self.sent_bytes = 0
        self.sent_wire = bytearray()
        self.config = None

    def start(self):
        import termios
        self.session_id = self.controller.start(source="serial", port=self.port)
        wait_until(lambda: self.controller.has_event("opened"), 10, "Serial open event was not observed")
        attributes = termios.tcgetattr(self.slave)
        cflag = attributes[2]
        self.config = {"input_baud_115200": attributes[4] == termios.B115200,
                       "output_baud_115200": attributes[5] == termios.B115200,
                       "eight_data_bits": cflag & termios.CSIZE == termios.CS8,
                       "no_parity": not bool(cflag & termios.PARENB),
                       "one_stop_bit": not bool(cflag & termios.CSTOPB)}
        if not all(self.config.values()):
            raise AssertionError("Serial configuration differs from the STM32 contract")

    def write(self, data):
        view = memoryview(data)
        deadline = time.monotonic() + 5
        while view:
            if time.monotonic() >= deadline:
                raise TimeoutError("PTY sender stalled")
            if not select.select([], [self.master], [], 0.1)[1]:
                continue
            try:
                written = os.write(self.master, view)
            except BlockingIOError:
                continue
            self.sent_bytes += written
            self.sent_wire.extend(view[:written])
            view = view[written:]

    def send(self, pairs, fragmented=False):
        """Pace 36-point blocks at 360 Hz; force several sub-read-sized writes."""
        pairs = list(pairs)
        origin = time.monotonic()
        for offset in range(0, len(pairs), 36):
            wire = frames(pairs[offset:offset + 36])
            if fragmented:
                cuts = (1, 3, 8, 25, len(wire))
                begin = 0
                for end in cuts:
                    end = min(end, len(wire))
                    if end > begin:
                        self.write(wire[begin:end])
                        begin = end
                    # The first short reads exceed pyserial's 100 ms read
                    # timeout, so this exercises actual split reads too.
                    if offset == 0 and end < 8:
                        time.sleep(0.13)
                    else:
                        time.sleep(0.003)
            else:
                self.write(wire)
            target = origin + min(offset + 36, len(pairs)) / 360
            time.sleep(max(0, target - time.monotonic()))

    def wait_received(self, count):
        def ready():
            state = snapshot(self.controller)
            if state.get("error"):
                raise RuntimeError(state["error"])
            return state["received"] >= count
        wait_until(ready, 8, f"Did not receive {count} samples")

    def stop(self):
        self.controller.stop()
        if not self.controller.wait(15):
            raise TimeoutError("Serial/analysis/recording workers did not stop")

    def disconnect(self):
        os.close(self.master)
        self.master = None

    def close_fds(self):
        for name in ("master", "slave"):
            fd = getattr(self, name, None)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def close(self):
        try:
            self.stop()
        finally:
            self.close_fds()


def exported_result(harness, expected, output, expect_error=False):
    final = snapshot(harness.controller)
    csv_path = output / (harness.session_id + ".csv")
    harness.controller.export_session(harness.session_id, csv_path)
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    session = harness.controller.data_dir / "sessions" / harness.session_id
    metadata = json.loads((session / "session.json").read_text(encoding="utf-8"))
    quality = json.loads((session / "quality.json").read_text(encoding="utf-8"))
    recorded_wire = b"".join(path.read_bytes() for path in sorted((session / "wire").glob("*.bin")))
    expected = [tuple(row) if len(row) == 3 else (*row, 0) for row in expected]
    checks = {
        "received_exact": final["received"] == len(expected),
        "saved_exact": final["saved"] == len(expected) and final["pending"] == 0,
        "csv_exact_values_sequence_mode": [(int(r["sequence"]), int(r["raw_adc"]), int(r["mode"])) for r in rows] == expected,
        "csv_contiguous_sample_ids": [int(r["sample_id"]) for r in rows] == list(range(len(expected))),
        "serial_provenance": final["source"] == "serial" and metadata["source"] == "serial"
            and all(r["source"] == "serial" for r in rows),
        "board_mode_recorded": metadata.get("signal_origin") == "per_sample_mode"
            and all(r["signal_origin"] == ("board_simulated" if r["mode"] == "1" else "real_adc") for r in rows),
        "protocol_metadata": all(metadata.get("serial_protocol", {}).get(key) == value
            for key, value in {"baud_rate": 115200,
                               "format": "8N1", "frame_bytes": 7, "block_samples": 36,
                               "mode_field": True}.items()),
        "port_is_test_pty": metadata.get("port") == harness.port,
        "wire_bytes_recorded": metadata.get("wire_bytes") == harness.sent_bytes,
        "wire_exact_bytes_once": recorded_wire == bytes(harness.sent_wire),
        "wire_bytes_counted": final.get("serial_bytes") == harness.sent_bytes,
        "expected_error_state": (bool(final.get("error")) and final["state"] == "FAULT") if expect_error
            else not final.get("error") and metadata.get("status") == "complete",
        "not_running": not final["running"],
        "serial_configuration": all(harness.config.values()),
    }
    return {"checks": checks, "final": final, "session_metadata": metadata, "quality": quality,
            "events": harness.controller.event_copy(), "serial_configuration": harness.config,
            "sent_bytes": harness.sent_bytes, "csv": str(csv_path)}, rows


def run_scenario(name, controller_type, data_dir, output):
    harness = Harness(controller_type, data_dir)
    result = {"name": name, "checks": {}, "errors": []}
    try:
        harness.start()
        if name == "fragmented_midstream":
            prefix = b"noise\x08\x55\xaa"
            expected = [((241 + i) & 255, 65535 if 144 <= i < 180 else 0 if i == 360
                         else 4095 if i == 361 else 1900 + (i * 17) % 400,
                         1 if 144 <= i < 540 else 0) for i in range(720)]
            harness.write(prefix)
            harness.send(expected, fragmented=True)
            harness.wait_received(len(expected))
            harness.stop()
            result, rows = exported_result(harness, expected, output)
            guard = rows[144:180]
            result["checks"].update(
                invalid_guard_36=len(guard) == 36 and all(int(r["flags"]) & 1 and r["valid"] == "0" for r in guard),
                invalid_marker_not_assumed_lead_off=result["quality"].get("counts", {}).get("invalid_marker_samples") == 36,
                guard_separates_segments=rows[143]["segment_id"] != rows[144]["segment_id"]
                    and rows[179]["segment_id"] != rows[180]["segment_id"],
                saturated_endpoints_masked=all(int(rows[i]["flags"]) & 2 and rows[i]["valid"] == "0" for i in (360, 361)),
                no_false_sequence_gaps=all(not int(r["flags"]) & 4 for r in rows),
                sequence_wraps_preserved=rows[14]["sequence"] == "255" and rows[15]["sequence"] == "0",
                mode_change_without_guard_separates=rows[539]["segment_id"] != rows[540]["segment_id"]
                    and bool(int(rows[540]["flags"]) & 8),
                mode_events=[e["detail"]["mode"] for e in result["events"] if e["kind"] == "mode_changed"] == [0, 1, 0],
                initial_noise_discarded=any(e["kind"] == "aligned" and e["detail"].get("discarded_bytes", 0) == len(prefix)
                                                for e in result["events"]))
        elif name == "timeout_partial_frame":
            before = [(i, 2000 + i) for i in range(36)]
            harness.send(before)
            harness.wait_received(len(before))
            # Only header and mode arrive. Retaining these across the gap
            # would corrupt the resumed frame alignment.
            harness.write(b"\xaa\x55\x01")
            wait_until(lambda: harness.controller.has_event("timeout"), 6, "2 s interruption was not reported")
            interrupted = snapshot(harness.controller)
            after = [((80+i)&255, 2600 + i % 100, 1) for i in range(144)]
            harness.send(after, fragmented=True)
            harness.wait_received(len(before) + len(after))
            harness.stop()
            result, rows = exported_result(harness, before + after, output)
            result["interrupted_snapshot"] = interrupted
            aligned = [e for e in result["events"] if e["kind"] == "aligned"]
            result["checks"].update(
                timeout_cleared_alignment=interrupted.get("serial_aligned") is False,
                timeout_cleared_hr=interrupted.get("hr") is None and interrupted.get("rr_ms") is None,
                aligned_again=len(aligned) == 2,
                resumed_event=harness.controller.has_event("resumed"),
                partial_three_bytes_discarded=any(e["kind"] == "timeout" and e["detail"].get("discarded_partial_bytes") == 3
                                                for e in result["events"]),
                resumed_segment_is_new=rows[35]["segment_id"] != rows[36]["segment_id"],
                old_partial_frame_not_joined=int(rows[36]["sequence"]) == 80 and int(rows[36]["raw_adc"]) == 2600)
        elif name == "corruption_recovery":
            before = [(i, 2000 + i, 1) for i in range(72)]
            harness.send(before)
            harness.wait_received(len(before))
            corrupted = bytearray(frames([(72, 3200, 1)])); corrupted[-1] ^= 1
            # Broken checksum, invalid mode with valid checksum, illegal ADC,
            # then a truncated frame, arbitrary noise, and valid frames at any seq.
            harness.write(bytes(corrupted) + frames([(73, 2000, 2), (74, 6000, 1)])
                          + frames([(75, 2100, 1)])[:5] + b"noise")
            wait_until(lambda: harness.controller.has_event("protocol_resync"), 4, "Corruption not reported")
            damaged = snapshot(harness.controller)
            after = [(i, 2200 + i % 100, 1) for i in range(76, 148)]
            harness.send(after, fragmented=True)
            harness.wait_received(len(before) + len(after))
            # Noise between sequential frames must also break analysis, even
            # when the 8-bit sequence does not reveal a missing sample.
            harness.write(b"garbage")
            tail = [(i, 2300 + i % 100, 0) for i in range(148, 184)]
            harness.send(tail)
            harness.wait_received(len(before) + len(after) + len(tail))
            harness.stop()
            result, rows = exported_result(harness, before + after + tail, output)
            result["checks"].update(
                damage_clears_display=damaged["hr"] is None and damaged["rr_ms"] is None,
                first_recovered_marks_resync=bool(int(rows[72]["flags"]) & 16),
                first_recovered_marks_seq_gap=bool(int(rows[72]["flags"]) & 4),
                corrupt_bytes_not_samples=len(rows) == 180,
                segments_not_bridged=rows[71]["segment_id"] != rows[72]["segment_id"],
                noise_with_contiguous_seq_marks_resync=bool(int(rows[144]["flags"]) & 16),
                checksum_error_counted=any(e["kind"] == "protocol_stats" and e["detail"].get("checksum_errors", 0) >= 1
                                           for e in result["events"]))
        elif name == "disconnect":
            expected = [(i & 255, 2100 + i % 100) for i in range(144)]
            harness.send(expected)
            harness.wait_received(len(expected))
            harness.disconnect()
            wait_until(lambda: not snapshot(harness.controller)["running"], 15, "Disconnect did not stop the session")
            harness.stop()
            result, _ = exported_result(harness, expected, output, expect_error=True)
            result["checks"]["disconnect_has_readable_error"] = len(str(result["final"].get("error") or "").strip()) > 10
            result["checks"]["failed_session_not_complete"] = result["session_metadata"].get("status") != "complete"
        else:
            raise ValueError("Unknown scenario")
    except Exception:
        result.setdefault("errors", []).append(traceback.format_exc())
    finally:
        try:
            harness.close()
        except Exception:
            result.setdefault("errors", []).append(traceback.format_exc())
    result.update(name=name, software_pty_only=True)
    result["passed"] = bool(result.get("checks")) and all(result["checks"].values()) and not result.get("errors")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", choices=("all", "fragmented_midstream", "timeout_partial_frame", "corruption_recovery", "disconnect"), default="all")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        parser.error("This verification needs Linux PTYs; no hardware ports are used")
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[key] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from controller import Controller

    class ObservedController(Controller):
        def __init__(self, directory):
            super().__init__(directory)
            self._observed = []
            self._observed_lock = threading.Lock()

        def _on_event(self, kind, detail):
            super()._on_event(kind, detail)
            with self._observed_lock:
                self._observed.append({"kind": kind, "detail": dict(detail), "monotonic": time.monotonic()})

        def has_event(self, kind):
            with self._observed_lock:
                return any(row["kind"] == kind for row in self._observed)

        def event_copy(self):
            with self._observed_lock:
                return list(self._observed)

    args.data_dir = args.data_dir.resolve()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    exports = args.output.parent / (args.output.stem + "-csv")
    exports.mkdir(parents=True, exist_ok=True)
    scenarios = ("fragmented_midstream", "timeout_partial_frame", "corruption_recovery", "disconnect") if args.scenario == "all" else (args.scenario,)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
              "verification": "Linux PTY through real pyserial / Controller / Recorder / CSV",
              "hardware_verified": False, "data_is_synthetic": True,
              "limitations": "PTYs do not validate voltage levels, physical UART baud timing, wiring, or flashed firmware.",
              "scenarios": []}
    for name in scenarios:
        result = run_scenario(name, ObservedController, args.data_dir, exports)
        report["scenarios"].append(result)
        print(json.dumps({"scenario": name, "passed": result["passed"], "checks": result["checks"],
                          "errors": result.get("errors", [])}, ensure_ascii=False), flush=True)
    report["passed"] = all(item["passed"] for item in report["scenarios"])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Report:", args.output, flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
