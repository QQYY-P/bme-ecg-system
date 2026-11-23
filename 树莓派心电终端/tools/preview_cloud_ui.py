"""Layout proof: synthetic waveform and an explicitly labelled public cloud result."""
import json
import os
from pathlib import Path
import sys
import tkinter as tk
from unittest import mock
from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.preview_classification_ui import PreviewController, descendants
from ui.main_window import MainWindow
from ui.cloud_dialog import show_cloud


def main():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    output = ROOT / 'evidence/26_ui_cloud_20261029'
    response = json.loads((output / 'cloud-roundtrip.json').read_text(encoding='utf-8'))['analysis']
    controller = PreviewController()
    controller.data_dir = Path('unused-preview')
    sid = response['session_id']
    controller.list_sessions = lambda: [{'session_id': sid, 'sample_count': 21600}]
    service = mock.Mock()
    service.snapshot.return_value = {'connection': 'online', 'phase': 'complete', 'session_id': sid,
        'message': '公开数据库回放 · 云端分析完成', 'progress': [61, 61], 'result': response}
    root = tk.Tk(); root.tk.call('tk', 'scaling', 96/72); root.geometry('800x480+0+0')
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    with mock.patch('cloud.service.CloudService', return_value=service):
        view = MainWindow(root, controller)
    view._task = lambda operation, success=None, failure=None: success(operation()) if success else operation()
    view.title_label.configure(text='ECG 布局预览')
    view._show_snapshot(controller.snapshot(), 5)
    root.update()
    def capture(widget, name):
        widget.update_idletasks()
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        options = {'xdisplay': os.environ['DISPLAY']} if 'DISPLAY' in os.environ else {}
        ImageGrab.grab(bbox=(x, y, x+widget.winfo_width(), y+widget.winfo_height()), **options).save(output / name)
    capture(root, 'cloud-button-800.png')
    show_cloud(view); root.update()
    # Let the desktop compositor finish painting the newly opened test dialog.
    root.after(400, root.quit); root.mainloop()
    capture(view._cloud_dialog, 'cloud-result.png')
    (output / 'cloud-preview.json').write_text(json.dumps({'errors': errors,
        'waveform': 'synthetic layout preview', 'cloud_result': 'actual public MIT-BIH record 233 replay',
        'physical_hdmi_tested': False}, ensure_ascii=False, indent=2), encoding='utf-8')
    view._closed = True; root.destroy()
    print(errors)


if __name__ == '__main__':
    main()
