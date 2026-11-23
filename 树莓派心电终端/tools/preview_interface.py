"""Render the real interface in an isolated Linux X display for visual review."""
from pathlib import Path
import json
import os
import sys
import time
import tkinter as tk

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controller import Controller
from ui.main_window import MainWindow


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    root.geometry("1000x620+0+0")
    controller = Controller(output / "data")
    view = MainWindow(root, controller)
    errors, captures = [], []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))

    def pump(seconds=0.5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(0.015)

    def capture(name, window=root):
        window.update_idletasks()
        x, y = window.winfo_rootx(), window.winfo_rooty()
        w, h = window.winfo_width(), window.winfo_height()
        image = ImageGrab.grab(bbox=(x, y, x+w, y+h), xdisplay=os.environ["DISPLAY"])
        image.save(output / (name + ".png"))
        overflow = []
        # Check important controls against the root and their immediate parent.
        for label in ("source_combo", "port_combo", "file_button", "refresh_button", "pause_button",
                      "start_button", "stop_button", "hr_label", "saved_label", "clock_label", "mode_combo", "window_combo"):
            widget = getattr(view, label)
            if not widget.winfo_ismapped():
                continue
            parent = widget.master
            if (widget.winfo_x() < 0 or widget.winfo_y() < 0 or
                widget.winfo_x()+widget.winfo_width() > parent.winfo_width()+1 or
                widget.winfo_y()+widget.winfo_height() > parent.winfo_height()+1):
                overflow.append(label)
        captures.append({"name": name, "size": [w, h], "overflow": overflow})

    try:
        pump(1)
        root.geometry("800x480+0+0"); pump(); capture("idle-800")
        view.source.set("合成模拟"); view._update_controls(); view.start()
        pump(6)
        capture("demo-800")
        root.geometry("1000x620+0+0"); pump(); capture("demo-1000")
        root.geometry("1920x1080+0+0"); pump(); capture("demo-1920")
        view.toggle_pause(); pump(); capture("paused-1920")
        before = controller.snapshot()["received"]
        pump(1)
        pause_continues = controller.snapshot()["received"] > before
        view.toggle_pause()
        root.geometry("800x480+0+0"); pump()
        view.settings(); pump()
        settings = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        capture("settings-active", settings); settings.destroy()
        view.stop(); pump(2)
        final = controller.snapshot(); final.pop("wave", None)
        capture("stopped-800")
        view.show_history(); pump(1)
        history = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        capture("history", history); history.destroy()
        view.source.set("文件回放"); view._update_controls(); pump(); capture("replay-800")
        report = {"captures": captures, "errors": errors, "pause_kept_recording": pause_continues,
                  "final": final, "physical_hdmi": False}
        (output / "preview-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"errors": errors, "pause_kept_recording": pause_continues,
                          "saved": final["saved"], "pending": final["pending"], "captures": captures}))
        return int(bool(errors) or any(c["overflow"] for c in captures) or not pause_continues or final["pending"] != 0)
    finally:
        controller.stop(); controller.wait(15)
        view._closed = True
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
