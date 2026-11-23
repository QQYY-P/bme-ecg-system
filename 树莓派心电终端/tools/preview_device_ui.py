"""Exercise real standby/demo/save UI locally; synthetic data only, no power/serial actions."""
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controller import Controller
from ui.main_window import MainWindow


class OfflineCloud:
    def snapshot(self):
        return {'connection': 'offline'}

    def close(self):
        pass

    def wait(self, timeout=0):
        return True


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    root = tk.Tk()
    root.tk.call('tk', 'scaling', 96 / 72)
    root.geometry('800x480+0+0')
    controller = Controller(output / 'synthetic-data', classification_enabled=True)
    controller.storage_status()
    controller.list_ports = lambda: [dict(device='USB-DEMO-PORT', usb=True)]
    initial_sessions = controller.list_sessions()
    with patch('cloud.service.CloudService', return_value=OfflineCloud()):
        view = MainWindow(root, controller, simple_mode=True)
    errors, captures = [], []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    def pump(seconds=.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.01)
    def capture(name, window=root):
        root.update_idletasks()
        x, y = window.winfo_rootx(), window.winfo_rooty()
        w, h = window.winfo_width(), window.winfo_height()
        ImageGrab.grab(bbox=(x, y, x+w, y+h)).save(output / (name + '.png'))
        overflow = []
        widgets = [view.start_button, view.stop_button, view.pause_button, view.simple_controls,
                   view.hr_label, view.class_detail, view.standby_card, view.header, view.sidebar]
        for widget in widgets:
            if widget.winfo_ismapped() and (widget.winfo_x() < 0 or widget.winfo_y() < 0 or
                    widget.winfo_x()+widget.winfo_width() > widget.master.winfo_width()+1 or
                    widget.winfo_y()+widget.winfo_height() > widget.master.winfo_height()+1):
                overflow.append(str(widget))
        captures.append(dict(name=name, size=[w, h], overflow=overflow))
    try:
        pump(1)
        standby_clean = not controller.snapshot()['session_id'] and controller.list_sessions() == initial_sessions
        capture('standby-800')
        root.geometry('1000x620+0+0'); pump(); capture('standby-1000')
        root.geometry('800x480+0+0'); pump()
        view.show_device(); pump(); capture('device-options', view._device_dialog)
        view._device_dialog.destroy()
        view.source.set('合成模拟'); view._update_controls(); view.start(duration=3)
        pump(1.7); capture('demo-800')
        pump(2)
        final = controller.snapshot()
        final.pop('wave', None)
        capture('saved-800')
        view.review_button.invoke(); pump(.5)
        history = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        tree = next(w for w in descendants(history) if isinstance(w, ttk.Treeview))
        selected = tree.selection()
        selected_current = bool(selected) and tree.item(selected[0], 'values')[-1] == final['session_id']
        capture('record-selected', history)
        history.destroy()
        view.close()
        deadline = time.monotonic() + 5
        while not view._closed and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        closed_cleanly = view._closed
        result = dict(standby_created_no_record=standby_clean, errors=errors, captures=captures,
                      final=final, selected_current_record=selected_current, closed_cleanly=closed_cleanly,
                      physical_hdmi=False, poweroff_executed=False)
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(standby_clean=standby_clean, errors=errors, captures=captures,
                             selected_current=selected_current, closed_cleanly=closed_cleanly,
                             received=final['received'], saved=final['saved'], pending=final['pending']), ensure_ascii=False))
        return int(not standby_clean or not selected_current or not closed_cleanly or bool(errors) or bool(final['error']) or final['saved'] != 1080 or
                   final['pending'] != 0 or any(c['overflow'] for c in captures))
    finally:
        controller.stop(); controller.wait(15)
        if not view._closed:
            view._closed = True
            for cb in root.tk.call('after', 'info'):
                root.after_cancel(cb)
            root.destroy()


if __name__ == '__main__':
    raise SystemExit(main())
