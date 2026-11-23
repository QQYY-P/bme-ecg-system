"""Run the real Tk waveform UI and verify its complete synthetic recording.

Example (Linux without a display):
  xvfb-run -a python3 tools/verify_waveform_runtime.py --duration 180 \
    --data-dir /tmp/ecg-runtime-data --output /tmp/ecg-runtime.json
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
import traceback


def resources():
    if sys.platform != "linux":
        return None
    import resource
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {"cpu_s": usage.ru_utime + usage.ru_stime,
            "peak_rss_bytes": usage.ru_maxrss * 1024}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--geometry", default="800x480")
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or not 1 / 360 <= args.duration <= 86400:
        parser.error("duration must be between 1/360 and 86400 seconds")
    args.data_dir = args.data_dir.resolve()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    summary = {"started_utc": datetime.now(timezone.utc).isoformat(),
               "platform": platform.platform(), "duration_s": args.duration,
               "geometry": args.geometry, "expected_samples": round(360 * args.duration),
               "sampling_interval_ms": 100, "gui_max_interval_ms": None,
               "hr_detected": False, "hr_required": args.duration >= 10,
               "data_dir": str(args.data_dir), "errors": [], "samples": []}
    root = view = controller = None
    initial_resources = resources()
    started = time.monotonic()
    run_started = previous_tick = None
    max_gap = 0.0
    session_id = None
    try:
        import tkinter as tk
        from controller import Controller
        from ui.main_window import MainWindow

        controller = Controller(args.data_dir)
        root = tk.Tk()
        root.geometry(args.geometry)
        view = MainWindow(root, controller)
        view.source.set("合成模拟")

        def abort(message):
            summary["errors"].append(message)
            controller.stop()
            root.quit()

        def callback_error(exc_type, value, tb):
            abort("".join(traceback.format_exception(exc_type, value, tb)))

        root.report_callback_exception = callback_error
        root.protocol("WM_DELETE_WINDOW", lambda: abort("Window closed before verification completed"))

        def sample():
            nonlocal previous_tick, max_gap
            now = time.monotonic()
            gap_ms = (now - previous_tick) * 1000
            previous_tick = now
            max_gap = max(max_gap, gap_ms)
            state = controller.snapshot()
            hr = state.get("hr")
            if hr is not None and math.isfinite(float(hr)) and float(hr) > 0:
                summary["hr_detected"] = True
            summary["samples"].append({
                "elapsed_s": round(now - run_started, 4),
                "gui_interval_ms": round(gap_ms, 3), "state": state["state"],
                "received": state["received"], "saved": state["saved"],
                "analysis_dropped_samples": state.get("analysis_dropped_samples", 0),
                "hr": None if hr is None else float(hr)})
            if not state["running"] and controller.wait(0):
                root.quit()
            elif now - run_started > args.duration + 30:
                abort("Timed out waiting for collection and final save")
            else:
                root.after(100, sample)

        def begin():
            nonlocal run_started, previous_tick, session_id
            session_id = controller.start(source="demo", duration=args.duration)
            run_started = previous_tick = time.monotonic()
            root.after(100, sample)

        root.after(200, begin)
        root.mainloop()
    except Exception:
        summary["errors"].append(traceback.format_exc())
    finally:
        if view is not None:
            view._closed = True
        if root is not None:
            try:
                for callback in root.tk.call("after", "info"):
                    root.after_cancel(callback)
                root.destroy()
            except Exception:
                summary["errors"].append(traceback.format_exc())
        if controller is not None:
            controller.stop()
            summary["stopped_completely"] = controller.wait(15)
            final = controller.snapshot()
            final.pop("wave", None)
            summary["final"] = final

    summary["gui_max_interval_ms"] = round(max_gap, 3) if summary["samples"] else None
    summary["sample_ticks"] = len(summary["samples"])
    summary["session_id"] = session_id
    summary["gui_run_wall_s"] = round(time.monotonic() - started, 4)
    final_resources = resources()
    summary["resources"] = None if final_resources is None else {
        "cpu_s": round(final_resources["cpu_s"] - initial_resources["cpu_s"], 4),
        "peak_rss_bytes": final_resources["peak_rss_bytes"]}

    try:
        if not session_id or not summary.get("stopped_completely"):
            raise RuntimeError("No completed session available for CSV verification")
        csv_path = args.output.with_name(args.output.stem + "-" + session_id + ".csv")
        exported = controller.export_session(session_id, csv_path)
        count, contiguous, matching_session, sources = 0, True, True, set()
        with open(exported, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                contiguous = contiguous and int(row["sample_id"]) == count
                matching_session = matching_session and row["session_id"] == session_id
                sources.add(row["source"])
                count += 1
        summary["csv"] = {"path": exported, "sample_count": count,
                          "sample_ids_contiguous_from_zero": contiguous,
                          "session_ids_match": matching_session, "sources": sorted(sources)}
    except Exception:
        summary["errors"].append(traceback.format_exc())

    model_names = ("ecg_classifier", "numpy_predictor", "inference_worker", "torch", "tensorflow")
    summary["model_modules_imported"] = sorted(
        name for name in sys.modules if name.split(".")[0] in model_names)
    final, exported = summary.get("final", {}), summary.get("csv", {})
    expected = summary["expected_samples"]
    summary["checks"] = {
        "no_runtime_errors": not summary["errors"] and not final.get("error"),
        "stopped_completely": bool(summary.get("stopped_completely")) and not final.get("running", True),
        "received_count": final.get("received") == expected,
        "saved_count": final.get("saved") == expected and final.get("pending") == 0,
        "csv_count": exported.get("sample_count") == expected,
        "csv_ids_contiguous": bool(exported.get("sample_ids_contiguous_from_zero")),
        "csv_session_matches": bool(exported.get("session_ids_match")),
        "source_demo": final.get("source") == "demo" and exported.get("sources") == ["demo"],
        "no_analysis_drops": final.get("analysis_dropped_samples") == 0,
        "hr_observed_or_short_run": summary["hr_detected"] or not summary["hr_required"],
        "gui_timer_sampled": bool(summary["samples"]),
        "no_model_imports": not summary["model_modules_imported"],
    }
    summary["passed"] = all(summary["checks"].values())
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "samples"},
                     ensure_ascii=False, indent=2))
    print("Summary:", args.output)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
