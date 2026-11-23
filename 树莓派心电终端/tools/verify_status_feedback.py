"""Exercise mode feedback and signal quality through a PTY and the real Tk UI.

Uses only synthetic samples and a newly allocated /dev/pts port. It never
opens physical hardware. Run under xvfb-run on the Raspberry Pi.
"""
import argparse
import json
import math
import os
from pathlib import Path
import queue
import random
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('Linux PTYs and a Tk display are required')
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[key] = '1'
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import tkinter as tk
    from PIL import ImageGrab
    from acquisition import demo_value
    from controller import Controller
    from ui.main_window import MainWindow
    from tools.verify_stm32_serial import Harness, frames, snapshot, exported_result

    class ObservedController(Controller):
        def __init__(self, *args):
            super().__init__(*args)
            self.observed = queue.SimpleQueue()
            self.events = []

        def _on_event(self, kind, detail):
            super()._on_event(kind, detail)
            self.observed.put({'kind': kind, 'detail': dict(detail)})

        def event_copy(self):
            while True:
                try: self.events.append(self.observed.get_nowait())
                except queue.Empty: break
            return list(self.events)

        def has_event(self, kind):
            return any(e['kind'] == kind for e in self.event_copy())

    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    h = Harness(ObservedController, out/'data')
    root = tk.Tk(); root.geometry('800x480+0+0')
    view = MainWindow(root, h.controller)
    errors, phases, expected, checks = [], {}, [], {}
    root.report_callback_exception = lambda *a: errors.append(str(a[1]))
    rng = random.Random(19)
    sequence = 0

    def pump(seconds):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            root.update(); time.sleep(.01)

    def wait(predicate, seconds=3):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            root.update()
            state = snapshot(h.controller)
            if predicate(state): return state
            if state.get('error'): raise RuntimeError(state['error'])
            time.sleep(.01)
        raise TimeoutError('State did not arrive: '+str(snapshot(h.controller)))

    def send(kind, seconds, mode):
        nonlocal sequence
        count = round(seconds*360)
        begin = time.monotonic()
        for start in range(0,count,36):
            block=[]
            for n in range(start,min(start+36,count)):
                if kind == 'invalid': value=65535
                elif kind == 'saturated': value=4095
                elif kind == 'flat': value=2048
                elif kind == 'noise': value=round(2048+800*math.sin(2*math.pi*90*n/360))
                else: value=demo_value(n,rng)
                block.append((sequence&255,value,mode)); sequence+=1
            expected.extend(block); h.write(frames(block))
            pump(max(.001,begin+min(start+36,count)/360-time.monotonic()))
        return wait(lambda s:s['received'] >= len(expected))

    def capture(name):
        pump(.2)
        state=snapshot(h.controller); phases[name]=state
        root.update_idletasks()
        x,y=root.winfo_rootx(),root.winfo_rooty()
        ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height()),
                       xdisplay=os.environ['DISPLAY']).save(out/(name+'.png'))
        print(json.dumps({'phase':name,**{k:state.get(k) for k in (
            'connection_state','signal_state','mode_check_state','board_mode','hr','hr_allowed')}},
            ensure_ascii=False),flush=True)
        return state

    try:
        h.start(); pump(3.3)
        s=capture('open-waiting')
        checks['open_is_not_receiving']=s['connection_state']!='receiving' and s['hr'] is None
        send('invalid',.3,0); s=capture('connected-invalid')
        checks['connected_invalid_separated']=s['connection_state']=='receiving' and s['signal_state']=='invalid'
        checks['invalid_hr_blocked']=not s['hr_allowed'] and s['hr'] is None
        checks['first_mode_not_switch']=s['mode_change_count']==0
        h.controller.begin_mode_check(timeout_s=1)
        send('invalid',1.5,0); s=capture('switch-timeout')
        checks['unchanged_mode_times_out']=s['mode_check_state']=='timeout' and s['board_mode']==0
        h.controller.begin_mode_check(timeout_s=10)
        send('invalid',.1,1); s=capture('switch-confirmed')
        checks['wire_mode_confirms_switch']=s['mode_check_state']=='confirmed' and s['board_mode']==1
        send('ecg',8,1); s=wait(lambda s:s.get('hr_allowed') and s.get('hr') is not None)
        s=capture('good-simulated-800')
        checks['healthy_hr_retained']=abs(s['hr']-72)<2
        root.geometry('1000x620+0+0'); capture('good-simulated-1000')
        root.geometry('800x480+0+0')
        send('noise',5,1); s=capture('strong-noise')
        checks['noise_hr_blocked']=s['signal_state']=='noisy' and not s['hr_allowed'] and s['hr'] is None
        send('ecg',8,1); s=wait(lambda s:s.get('hr_allowed') and s.get('hr') is not None)
        s=capture('recovered')
        checks['recovery_rebuilds_hr']=abs(s['hr']-72)<2
        send('flat',3,1); s=capture('flat-signal')
        checks['flat_hr_blocked']=s['signal_state']=='flat' and not s['hr_allowed'] and s['hr'] is None
        send('saturated',.1,1); s=capture('saturated')
        checks['saturation_hr_blocked']=s['signal_state']=='saturated' and not s['hr_allowed']
        h.controller.begin_mode_check(timeout_s=10)
        pump(2.5); s=capture('interrupted')
        checks['interruption_not_signal_fault']=s['connection_state']=='interrupted' and s['hr'] is None
        checks['pending_check_interrupted']=s['mode_check_state']=='interrupted'
        h.stop(); pump(.3); s=capture('stopped')
        checks['stopped_is_neutral']=s['connection_state']=='stopped' and not s['hr_allowed']
        recording,_=exported_result(h,expected,out)
        checks.update(recording['checks'])
        checks['no_tk_errors']=not errors
        result={'synthetic_pty_only':True,'physical_hdmi':False,'checks':checks,
                'phases':phases,'recording':recording,'errors':errors,'passed':all(checks.values())}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':result['passed'],'failed':[k for k,v in checks.items() if not v]}),flush=True)
        return 0 if result['passed'] else 1
    finally:
        h.close(); view._closed=True
        for callback in root.tk.call('after','info'): root.after_cancel(callback)
        root.destroy()


if __name__=='__main__':
    raise SystemExit(main())
