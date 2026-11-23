"""Measure Tk callbacks with mainloop, not update() drain-loop duration.

Use a public replay CSV, fixed window and identical source file for comparisons.
The callback delay is an engineering responsiveness measure, not a display FPS.
"""
import os
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
import argparse
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source))
    import numpy as np
    import tkinter as tk
    from controller import Controller
    from ui.main_window import MainWindow
    import ui.main_window, ui.waveform
    args.output.mkdir(parents=True, exist_ok=False)
    root = tk.Tk(); root.geometry('1000x620')
    controller = Controller(args.output / 'data', classification_enabled=True)
    view = MainWindow(root, controller)
    frames, heartbeat, errors = [], [], []
    root.report_callback_exception = lambda *items: errors.append(str(items[1]))
    original = view._show_snapshot
    def show(snapshot, seconds):
        begin = time.perf_counter()
        original(snapshot, seconds)
        if snapshot.get('running') and snapshot.get('elapsed_s', 0) >= 10:
            frames.append((time.perf_counter()-begin)*1000)
    view._show_snapshot = show
    previous = [time.perf_counter()]
    began = time.monotonic()
    def beat():
        now = time.perf_counter()
        state = view._last_snapshot
        if state.get('running') and state.get('elapsed_s', 0) >= 10:
            heartbeat.append((now-previous[0])*1000)
        previous[0] = now
        if time.monotonic()-began > 110 or state.get('received') and not state.get('running'):
            root.quit()
        else:
            root.after(25, beat)
    view.source.set('文件回放'); view._replay_path = str(args.csv)
    root.after(100, view.start); root.after(25, beat)
    root.mainloop()
    controller.stop(); controller.wait(20); controller.shutdown_environment()
    state = controller.snapshot(); state.pop('wave', None)
    if getattr(view, 'cloud', None): view.cloud.close()
    view._closed = True; root.destroy()
    def stats(values):
        return {'count': len(values), 'median': float(np.median(values)),
                'p95': float(np.percentile(values, 95)), 'max': max(values)} if values else None
    result = {'source': str(args.source), 'ui_module': ui.main_window.__file__,
              'wave_module': ui.waveform.__file__, 'frame_callback_ms': stats(frames),
              'heartbeat_interval_ms': stats(heartbeat), 'heartbeat_target_ms': 25,
              'sample_window': 'active recording after first 10 seconds',
              'received': state['received'], 'saved': state['saved'],
              'classification': state['classification'], 'errors': errors}
    (args.output / 'ui-latency.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'classification'}), flush=True)


if __name__ == '__main__':
    main()
