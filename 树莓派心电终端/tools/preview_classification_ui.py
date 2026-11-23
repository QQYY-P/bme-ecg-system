"""Fake-data classification UI review: never reads a serial port or user records."""
from copy import deepcopy
from pathlib import Path
import json
import os
import sys
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui.main_window import MainWindow, DEFAULT_UI_SETTINGS


class PreviewController:
    def __init__(self):
        self.session_id = "classification-preview"
        self.rows = []
        for index in range(20):
            label = ("N", "N", "S", "N", "V")[index % 5]
            self.rows.append({"session_id": self.session_id, "segment_id": 1, "prediction_id": f"preview-{index}", "r_sample": 600+index*324,
                              "label": label, "scores": {key: .9 if key == label else .05 for key in "NSV"}, "sample_latency_ms": 1100+index*9})
        x = np.arange(7200)/360
        wave = 25*np.sin(2*np.pi*.3*x)
        for row in self.rows:
            r = row["r_sample"]/360
            wave += 900*np.exp(-((x-r)/.012)**2) - 180*np.exp(-((x-r-.03)/.017)**2) + 130*np.exp(-((x-r-.2)/.05)**2)
        self.wave = {"x": x, "raw": 2048+wave, "filtered": wave, "segment_ids": np.ones(len(x), dtype=int)}
        self.running = True

    def get_settings(self):
        return dict(DEFAULT_UI_SETTINGS)

    def list_ports(self):
        return []

    def snapshot(self, **_):
        return {"session_id": self.session_id, "source": "serial", "state": "RECORDING" if self.running else "STOPPED", "running": self.running,
                "port": "/dev/ttyUSB0", "board_mode": 0, "analysis_generation": 1, "serial_aligned": True, "signal_state": "good", "signal_text": "信号良好", "hr_allowed": True, "hr": 67, "rr_ms": 900,
                "received": 7200, "saved": 7200, "elapsed_s": 20, "preprocessing_active": True, "wave": self.wave,
                "classification": {"state": "ready", "detail": "模型已就绪", "last": dict(self.rows[-1]), "recent": deepcopy(self.rows), "counts": {key: sum(row["label"] == key for row in self.rows) for key in "NSV"}, "classified": 20, "rejected": 5}}

    def classification_details(self, session_id=None):
        return {"session_id": self.session_id, "counts": {key: sum(row["label"] == key for row in self.rows) for key in "NSV"}, "classified": 20, "rejected": 5, "rejected_source": "live",
                "rows": deepcopy(self.rows), "total_rows": len(self.rows), "truncated": False, "reason_counts": {}, "time_info": {"trusted": False}}

    def review_beat(self, session_id, r_sample, segment_id, before_s=2, after_s=2):
        row = next(row for row in self.rows if row["r_sample"] == r_sample)
        begin, end = r_sample/360-before_s, r_sample/360+after_s
        selected = (self.wave["x"] >= begin) & (self.wave["x"] <= end)
        return {"wave": {key: value[selected] for key, value in self.wave.items()}, "prediction": dict(row), "vref": 3.3,
                "begin_s": begin, "end_s": end, "partial": end > self.wave["x"][-1], "note": "示例记录：对应 R 点已标记；S/V 研究分类需人工复核。"}


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
    root.tk.call("tk", "scaling", 96/72)
    root.geometry("800x480+0+0")
    controller = PreviewController()
    view = MainWindow(root, controller)
    captures, errors = [], []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    def pump(seconds=.5):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.02)
    def capture(name, widget=root):
        widget.lift()
        pump()
        x, y, w, h = widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height()
        options = {"xdisplay": os.environ["DISPLAY"]} if "DISPLAY" in os.environ else {}
        ImageGrab.grab(bbox=(x, y, x+w, y+h), **options).save(output/(name+".png"))
        overflow = []
        for child in descendants(widget):
            if isinstance(child, tk.Toplevel):
                continue
            if child.winfo_ismapped() and (child.winfo_x() < 0 or child.winfo_y() < 0 or child.winfo_x()+child.winfo_width() > child.master.winfo_width()+1 or child.winfo_y()+child.winfo_height() > child.master.winfo_height()+1):
                overflow.append({"widget": str(child), "bounds": [child.winfo_x(), child.winfo_y(), child.winfo_width(), child.winfo_height()], "parent": [child.master.winfo_width(), child.master.winfo_height()]})
        captures.append({"name": name, "size": [w, h], "overflow": overflow})
    try:
        capture("classification-live-800")
        view.toggle_pause()
        controller.rows[-1].update(label="N", scores={"N": .9, "S": .05, "V": .05})
        capture("classification-paused-800")
        view.show_classification_details(); pump()
        dialog = view._classification_dialog
        capture("classification-overview", dialog)
        tree = next(w for w in descendants(dialog) if isinstance(w, ttk.Treeview))
        tree.selection_set("0"); tree.event_generate("<<TreeviewSelect>>"); pump()
        button = next(w for w in descendants(dialog) if w.winfo_class() == "TButton" and w.cget("text") == "回看选中心搏")
        button.invoke(); pump()
        capture("classification-beat-review", dialog)
        dialog.destroy()
        controller.running = False
        view.toggle_pause(); pump()
        view.show_classification_details(); pump()
        capture("classification-stopped-details", view._classification_dialog)
        result = {"captures": captures, "errors": errors, "data": "fake records only", "physical_hdmi": False}
        (output/"classification-ui-preview.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return int(bool(errors) or any(row["overflow"] for row in captures))
    finally:
        view._closed = True
        for callback in root.tk.call("after", "info"):
            root.after_cancel(callback)
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
