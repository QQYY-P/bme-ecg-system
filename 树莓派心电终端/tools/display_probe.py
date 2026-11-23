"""Short, dependency-free Tk display probe; optional Pillow screenshot only.

Run through display_probe.sh on the Pi's physical console. This does not load
the ECG application or change display modes, power management, or boot settings.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont


DURATION_SECONDS = 45
REPORT_PATH = Path.home() / "ecg-display-probe.json"
SCREENSHOT_PATH = Path.home() / "ecg-display-probe.png"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Report:
    def __init__(self, root):
        self._lock = threading.Lock()
        self.data = {
            "started_utc": utc_now(),
            "pid": os.getpid(),
            "display": os.environ.get("DISPLAY", ""),
            "screen_width": root.winfo_screenwidth(),
            "screen_height": root.winfo_screenheight(),
            "requested_window_geometry": "800x480+0+0",
            "duration_seconds": DURATION_SECONDS,
            "state": "running",
            "exit_reason": None,
            "display_queries": {"state": "pending"},
            "screenshot": {
                "state": "pending",
                "path": str(SCREENSHOT_PATH),
                "planned_delay_seconds": 4,
            },
            "changes_display_mode": False,
            "changes_power_management": False,
        }
        self.update()

    def update(self, **values):
        with self._lock:
            self.data.update(values)
            temporary = REPORT_PATH.with_suffix(".json.tmp")
            try:
                temporary.write_text(
                    json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(REPORT_PATH)
            except OSError as exc:
                print(f"Unable to write display report: {exc}", file=sys.stderr, flush=True)


def query_display(report):
    results = {"state": "complete", "checked_utc": utc_now()}
    for name, command in (("xrandr", ["xrandr", "--current"]), ("xset", ["xset", "q"])):
        try:
            process = subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=5,
            )
            results[name] = {
                "command": command,
                "returncode": process.returncode,
                "stdout": process.stdout[:50000],
                "stderr": process.stderr[:10000],
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            results[name] = {"command": command, "error": str(exc)}
    report.update(display_queries=results)


def capture_display(report):
    result = {"path": str(SCREENSHOT_PATH), "attempted_utc": utc_now()}
    try:
        from PIL import ImageGrab

        picture = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY") or None)
        picture.save(SCREENSHOT_PATH)
        result.update(state="captured", captured_utc=utc_now(), width=picture.width,
                      height=picture.height)
    except Exception as exc:
        # Pillow and an available capture backend are optional for this probe.
        # A failed capture must not prevent the physical window test.
        result.update(state="unavailable", error=f"{type(exc).__name__}: {exc}")
    report.update(screenshot=result)


def main():
    os.umask(0o077)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Unable to create Tk display: {exc}", file=sys.stderr, flush=True)
        return 2

    root.title("显示测试 / DISPLAY TEST")
    root.geometry("800x480+0+0")
    root.resizable(False, False)
    root.configure(background="#ffffff")
    available_fonts = set(tkfont.families(root))
    family = next((candidate for candidate in (
        "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Microsoft YaHei", "DejaVu Sans"
    ) if candidate in available_fonts), "TkDefaultFont")
    report = Report(root)

    tk.Label(root, text="显示测试 / DISPLAY TEST", background="#ffffff",
             foreground="#11243a", font=(family, 26, "bold")).pack(pady=(22, 10))
    tk.Label(root, text="看见文字、彩色块和变化数字，说明窗口正在显示。",
             background="#ffffff", foreground="#11243a",
             font=(family, 14)).pack(pady=(0, 5))
    tk.Label(root, text="Visible text, colour blocks and a moving counter confirm display.",
             background="#ffffff", foreground="#11243a",
             font=(family, 12)).pack(pady=(0, 16))
    counter = tk.Label(root, background="#e6f6ff", foreground="#123b5a",
                       font=(family, 28, "bold"), padx=18, pady=10)
    counter.pack(fill="x", padx=24)
    colour_row = tk.Frame(root, background="#ffffff")
    colour_row.pack(fill="x", padx=24, pady=18)
    for colour, label in (("#ff5a5f", "红 RED"), ("#55d6a1", "绿 GREEN"), ("#69b7ff", "蓝 BLUE")):
        tk.Label(colour_row, text=label, background=colour, foreground="#122033",
                 font=(family, 17, "bold"), pady=20).pack(side="left", fill="x", expand=True)
    tk.Label(root, text="45 秒后自动退出；按 Esc 提前退出。",
             background="#ffffff", foreground="#11243a", font=(family, 14)).pack(pady=(0, 4))
    tk.Label(root, text="Closes automatically in 45 seconds. Press Esc to exit early.",
             background="#ffffff", foreground="#11243a", font=(family, 12)).pack()

    started = time.monotonic()
    closing = False

    def finish(reason):
        nonlocal closing
        if closing:
            return
        closing = True
        report.update(state="finished", exit_reason=reason, ended_utc=utc_now(),
                      elapsed_seconds=round(time.monotonic() - started, 3))
        root.destroy()

    def tick():
        if closing:
            return
        elapsed = min(DURATION_SECONDS, int(time.monotonic() - started))
        counter.configure(text=f"{elapsed:02d} / {DURATION_SECONDS} 秒 · seconds",
                          background="#e6f6ff" if elapsed % 2 == 0 else "#fff4d6")
        root.after(1000, tick)

    def worker(target):
        threading.Thread(target=target, args=(report,), daemon=True).start()

    root.bind("<Escape>", lambda _event: finish("escape"))
    root.protocol("WM_DELETE_WINDOW", lambda: finish("window_close"))
    root.after(0, tick)
    # The temporary startx session intentionally has no window manager.
    root.after(100, root.focus_force)
    root.after(250, lambda: worker(query_display))
    root.after(4000, lambda: worker(capture_display))
    root.after(DURATION_SECONDS * 1000, lambda: finish("timeout_45_seconds"))
    print(f"Display probe started. Report: {REPORT_PATH}", flush=True)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        finish("keyboard_interrupt")
    finally:
        if not closing:
            report.update(state="finished", exit_reason="mainloop_returned",
                          ended_utc=utc_now(), elapsed_seconds=round(time.monotonic() - started, 3))
    print(f"Display probe finished. Report: {REPORT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
