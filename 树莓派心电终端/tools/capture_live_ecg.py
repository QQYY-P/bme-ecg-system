"""Bounded physical-UART capture using the installed controller and real Tk UI.

Run under an isolated Xvfb when HDMI has no graphical session. Never injects data.
User reports electrodes connected; this does not establish clinical ground truth.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import tkinter as tk
    from PIL import ImageGrab
    from controller import Controller
    from ui.main_window import MainWindow

    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=120)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = tk.Tk()
    root.geometry('1000x620+0+0')
    controller = Controller(args.output / 'data')
    view = MainWindow(root, controller)
    view.mode.set('双波形')
    errors, snapshots, captures = [], [], []
    root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))
    started = None
    session = None
    try:
        session = controller.start(source='serial', port='auto')
        deadline = time.monotonic() + 20
        while not controller.snapshot()['received']:
            root.update()
            state = controller.snapshot()
            if state['error'] or not state['running']:
                raise RuntimeError(state['error'] or 'acquisition stopped')
            if time.monotonic() > deadline:
                raise RuntimeError('no serial samples within startup timeout')
            time.sleep(.02)
        started = time.monotonic()
        next_snapshot, next_progress = started, started
        shot_times = [30, 60, max(1, args.seconds-5)]
        while time.monotonic()-started < args.seconds:
            root.update()
            now = time.monotonic()
            if now >= next_snapshot:
                state = controller.snapshot()
                state.pop('wave', None)
                snapshots.append(dict(capture_elapsed_s=now-started, **state))
                next_snapshot = now + .5
                if state['error']:
                    raise RuntimeError(state['error'])
            if now >= next_progress:
                state = snapshots[-1]
                print(json.dumps(dict(seconds=round(now-started,1), received=state['received'],
                    saved=state['saved'], mode=state['board_mode'], signal=state['signal_state'],
                    hr=state['hr'], classification=state['classification'].get('counts'),
                    model_state=state['classification'].get('state')), ensure_ascii=True), flush=True)
                next_progress = now+20
            if shot_times and now-started >= shot_times[0]:
                shot = shot_times.pop(0)
                root.update_idletasks()
                x,y=root.winfo_rootx(),root.winfo_rooty()
                file = args.output / ('live-%03d.png' % shot)
                ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height()),
                               xdisplay=os.environ['DISPLAY']).save(file)
                captures.append(file.name)
            time.sleep(.01)
    except Exception as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    finally:
        elapsed = time.monotonic()-started if started is not None else 0
        controller.stop()
        deadline = time.monotonic()+20
        while controller.snapshot()['running'] and time.monotonic()<deadline:
            root.update()
            time.sleep(.02)
        if not controller.wait(10):
            errors.append('workers did not stop')
        final = controller.snapshot()
        final.pop('wave', None)
        if session:
            controller.export_session(session, args.output/'raw.csv')
            db = args.output/'data/sessions'/session/'index.sqlite'
            with sqlite3.connect(db) as conn:
                rows = [dict(kind=r[0], detail=json.loads(r[1])) for r in
                        conn.execute('SELECT kind,detail_json FROM events ORDER BY event_id')]
            (args.output/'events.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        report = dict(electrodes_connected='user_reported', physical_uart=True,
            display='real Tk application on isolated Xvfb; not a physical HDMI screenshot',
            requested_seconds=args.seconds, capture_elapsed_s=elapsed, errors=errors,
            final=final, snapshots=snapshots, screenshots=captures)
        (args.output/'capture.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(elapsed=elapsed, received=final['received'], saved=final['saved'],
            classified=final['classification'].get('classified'), errors=errors)), flush=True)
        view._closed=True
        root.destroy()
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
