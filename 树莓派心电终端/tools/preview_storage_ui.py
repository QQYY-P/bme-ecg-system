"""Capture settings/history at 800x480 using fake data; no system mutations."""
from pathlib import Path
import json
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui.main_window import MainWindow, DEFAULT_UI_SETTINGS


class PreviewController:
    def get_settings(self):
        return dict(DEFAULT_UI_SETTINGS, export_dir="/home/pi2/ecg/data/exports")

    def snapshot(self, **_):
        return {"state": "IDLE", "running": False, "storage": self.storage_status()}

    def list_ports(self):
        return []

    def storage_status(self):
        return {"state": "warning", "free_bytes": 400*1024**2, "total_bytes": 16*1024**3,
                "estimated_recording_seconds": 50000, "detail": "空间偏低，请导出或清理历史记录"}

    def clock_status(self):
        return {"trusted": False, "source": "manual", "utc": "2026-10-29T06:00:00Z", "detail": "已手动设置时间，尚未经过网络校验"}

    def list_sessions(self, trashed=False):
        return [{"session_id": f"20261029T06000{i}Z-preview-session", "started_utc": f"2026-10-29T06:00:0{i}Z", "source": "serial" if i % 2 else "demo", "status": "complete",
                 "sample_count": 43200*(i+1), "size_bytes": 2*1024**2*(i+1), "time_trusted": bool(i % 2)} for i in range(3 if trashed else 7)]

    def sync_time(self):
        raise RuntimeError("Preview never changes the clock")

    def set_time(self, _):
        raise RuntimeError("Preview never changes the clock")


def descendants(widget):
    result = list(widget.winfo_children())
    for child in result:
        result.extend(child.winfo_children())
    return result


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    # Match the Pi's 96-DPI screen; Windows desktop scaling otherwise crops grabs.
    root.tk.call("tk", "scaling", 96 / 72)
    root.geometry("800x480+0+0")
    view = MainWindow(root, PreviewController())
    captures, errors = [], []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))

    def pump():
        end = time.monotonic() + .5
        while time.monotonic() < end:
            root.update()
            time.sleep(.02)

    def capture(name, widget):
        pump()
        x, y, w, h = widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height()
        options = {"xdisplay": os.environ["DISPLAY"]} if "DISPLAY" in os.environ else {}
        ImageGrab.grab(bbox=(x, y, x+w, y+h), **options).save(output / (name+".png"))
        overflow = []
        for child in descendants(widget):
            if child.winfo_ismapped() and (child.winfo_x() < 0 or child.winfo_y() < 0 or child.winfo_x()+child.winfo_width() > child.master.winfo_width()+1 or child.winfo_y()+child.winfo_height() > child.master.winfo_height()+1):
                overflow.append({"widget": str(child), "bounds": [child.winfo_x(), child.winfo_y(), child.winfo_width(), child.winfo_height()], "parent_size": [child.master.winfo_width(), child.master.winfo_height()]})
        captures.append({"name": name, "size": [w, h], "overflow": overflow})

    try:
        pump()
        capture("space-warning-800", root)
        view.settings(); pump()
        dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        notebook = next(w for w in descendants(dialog) if isinstance(w, ttk.Notebook))
        for tab, name in zip(notebook.tabs(), ("display", "acquisition", "storage", "time")):
            notebook.select(tab)
            capture("settings-"+name, dialog)
        dialog.destroy()
        view.show_history(); pump()
        dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        tree = next(w for w in descendants(dialog) if isinstance(w, ttk.Treeview))
        tree.selection_set("0"); tree.event_generate("<<TreeviewSelect>>")
        capture("history", dialog)
        radio = next(w for w in descendants(dialog) if w.winfo_class() == "TRadiobutton" and w.cget("text") == "回收站")
        radio.invoke(); pump()
        tree.selection_set("0"); tree.event_generate("<<TreeviewSelect>>")
        capture("recycle-bin", dialog)
        result = {"captures": captures, "errors": errors, "physical_hdmi": False, "data": "fake records only"}
        (output / "ui-preview.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return int(bool(errors) or any(item["overflow"] for item in captures))
    finally:
        view._closed = True
        for callback in root.tk.call("after", "info"):
            root.after_cancel(callback)
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
