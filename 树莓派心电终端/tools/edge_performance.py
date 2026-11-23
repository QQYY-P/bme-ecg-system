"""Pi 2 edge benchmark and real-time public ECG replay demo.

Replay is paced by the actual acquisition code at 360 Hz, never called a
human capture. Profiling samples the GUI/main and model PIDs separately.
"""
import os
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[name]='1'
import argparse,csv,hashlib,json,platform,resource,sqlite3,subprocess,sys,threading,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def stats(values):
    x=np.asarray([v for v in values if v is not None],dtype=float)
    return None if not len(x) else dict(min=float(x.min()),median=float(np.median(x)),
        p95=float(np.percentile(x,95)),max=float(x.max()))


def read_process(pid):
    try:
        p=Path('/proc')/str(pid)
        fields=p.joinpath('stat').read_text().rsplit(')',1)[1].split()
        info=dict(pid=pid,start_ticks=int(fields[19]),cpu_seconds=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK'))
        status=p.joinpath('status').read_text().splitlines()
        for key,out in [('VmRSS:','rss_kib'),('VmHWM:','high_water_rss_kib')]:
            info[out]=next((int(s.split()[1]) for s in status if s.startswith(key)),None)
        try:
            roll=p.joinpath('smaps_rollup').read_text().splitlines()
            info['pss_kib']=next(int(s.split()[1]) for s in roll if s.startswith('Pss:'))
        except (OSError,StopIteration):info['pss_kib']=None
        return info
    except (OSError,ValueError,IndexError):return None


def read_number(path,divisor=1):
    try:return float(Path(path).read_text().strip())/divisor
    except (OSError,ValueError):return None


def throttle():
    try:
        p=subprocess.run(['vcgencmd','get_throttled'],capture_output=True,text=True,timeout=3)
        return p.stdout.strip() if p.returncode==0 else None
    except (OSError,subprocess.SubprocessError):return None


def environment():
    files=['controller.py','signal_analysis.py','signal_quality.py','cardioedge/stream.py',
           'cardioedge/service.py','cardioedge/numpy_candidate.py','cardioedge/portable_weights.npz']
    return dict(platform=platform.platform(),machine=platform.machine(),python=platform.python_version(),
        numpy=np.__version__,logical_cpus=os.cpu_count(),blas_threads=1,
        source_sha256={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},
        board_model=Path('/proc/device-tree/model').read_text().strip('\0') if Path('/proc/device-tree/model').exists() else None,
        throttled=throttle(),power_watts=None,power_status='not_measured_no_power_meter')


class Monitor:
    def __init__(self,controller=None):
        self.controller=controller;self.rows=[];self.stop=threading.Event();self.started=time.monotonic()
        self.thread=threading.Thread(target=self.loop,daemon=True)
    def model_pid(self):
        c=getattr(self.controller,'_classification',None)
        p=getattr(c,'process',None)
        return p.pid if p else None
    def sample(self):
        row=dict(elapsed_s=time.monotonic()-self.started,main=read_process(os.getpid()),
            model=read_process(self.model_pid()) if self.model_pid() else None,
            temperature_c=read_number('/sys/class/thermal/thermal_zone0/temp',1000),
            cpu_frequency_mhz=read_number('/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq',1000))
        present=[row[k] for k in ['main','model'] if row[k]]
        for key in ['rss_kib','pss_kib']:
            vals=[r[key] for r in present]
            row['sum_'+key]=sum(vals) if vals and all(v is not None for v in vals) else None
        self.rows.append(row)
    def loop(self):
        while not self.stop.is_set():
            self.sample();self.stop.wait(.5)
    def start(self):self.thread.start()
    def finish(self):self.stop.set();self.thread.join(3)
    def summarize(self):
        result={'sample_count':len(self.rows),'sampling_target_seconds':.5,
                'scope':'main/Tk process + model process; excludes X server, OS and other applications',
                'rss_note':'sum RSS double-counts shared pages; PSS apportions shared pages',
                'sum_rss_mib':stats([r['sum_rss_kib']/1024 if r['sum_rss_kib'] is not None else None for r in self.rows]),
                'sum_pss_mib':stats([r['sum_pss_kib']/1024 if r['sum_pss_kib'] is not None else None for r in self.rows]),
                'temperature_c':stats([r['temperature_c'] for r in self.rows]),
                'cpu_frequency_mhz':stats([r['cpu_frequency_mhz'] for r in self.rows])}
        for role in ['main','model']:
            rows=[(r['elapsed_s'],r[role]) for r in self.rows if r[role]]
            cpu=[]
            for (t0,p0),(t1,p1) in zip(rows,rows[1:]):
                if p0['pid']==p1['pid'] and p0['start_ticks']==p1['start_ticks'] and t1>t0:
                    cpu.append(100*(p1['cpu_seconds']-p0['cpu_seconds'])/(t1-t0))
            result[role]=dict(rss_mib=stats([p['rss_kib']/1024 if p['rss_kib'] is not None else None for t,p in rows]),
                pss_mib=stats([p['pss_kib']/1024 if p['pss_kib'] is not None else None for t,p in rows]),
                cpu_percent_one_core=stats(cpu),cpu_note='100% means one logical CPU; sampled intervals only')
        return result


def kernel(output,repeats):
    from cardioedge.numpy_candidate import Candidate
    env=environment();monitor=Monitor();monitor.start()
    t=time.perf_counter();model=Candidate();load_ms=1000*(time.perf_counter()-t)
    vector_path=ROOT/'cardioedge/pi_parity_vectors.npz'
    v=np.load(vector_path,allow_pickle=False)
    for wave,rr in zip(v['wave'][:16],v['rr'][:16]):model.predict_quantized(wave,rr)
    times=[];pred=[]
    for turn in range(repeats):
        for wave,rr in zip(v['wave'],v['rr']):
            t=time.perf_counter();pred.append(model.predict_quantized(wave,rr));times.append(1000*(time.perf_counter()-t))
        print(json.dumps({'kernel_round':turn+1,'inferences':len(times)}),flush=True)
    monitor.finish()
    got=np.array(pred).astype(int);expected=np.tile(v['output'],(repeats,1)).astype(int)
    diff=np.abs(got-expected)
    result=dict(environment=env,benchmark='model kernel after 16 warmups; no filters/UI/serial',
        vectors_per_round=len(v['wave']),repeats=repeats,measured_inferences=len(times),weights_load_ms=load_ms,
        latency_ms=stats(times),reference_backend=str(v['reference_backend']),
        vector_sha256=hashlib.sha256(vector_path.read_bytes()).hexdigest(),
        max_int8_difference=int(diff.max()),classification_agreement=float(np.mean(got.argmax(1)==expected.argmax(1))),
        exact_quantized_output_match=bool(np.array_equal(got,expected)),resources=monitor.summarize(),
        throttle_after=throttle())
    (output/'kernel.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'kernel-resources.json').write_text(json.dumps(monitor.rows,indent=2),encoding='utf-8')
    np.savez_compressed(output/'kernel-times.npz',latency_ms=np.array(times),outputs=got)
    return result


def demo(output,replay,seconds,headless=False):
    from controller import Controller
    z=np.load(replay,allow_pickle=False)
    if int(z['fs'])!=360:raise ValueError('Demo requires 360 Hz input')
    raw=z['raw_adc'][:seconds*360]
    if len(raw)!=seconds*360:raise ValueError('Demo file shorter than requested duration')
    record=str(z['record']);csv_path=output/'public-replay.csv'
    with csv_path.open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['raw_adc','sequence','segment_id','flags','mode'])
        for i,val in enumerate(raw):w.writerow([int(val),i%256,0,0,0])
    root=view=None;errors=[];screens=[]
    c=Controller(output/'data',classification_enabled=True)
    if not headless:
        import tkinter as tk
        from PIL import ImageGrab
        from ui.main_window import MainWindow
        root=tk.Tk();root.geometry('1000x620+0+0')
        view=MainWindow(root,c);view.source.set('文件回放');view._replay_path=str(csv_path)
        view.title_label.configure(text='公开数据库推理 Demo',font=(view.family,15,'bold'))
        root.title('MIT-BIH '+record+' 公开数据库回放 非人体实采')
        root.report_callback_exception=lambda *args:errors.append(str(args[1]))
    env=environment();monitor=Monitor(c);monitor.start()
    snapshots=[];started=time.monotonic();first_received=first_loaded=first_prediction=None
    last_tick=started;ticks=[];last_snapshot=0;last_progress=-20;next_shot=20;last_live=None;seen_classes=set();sid=None
    try:
        sid=c.start(source='replay',replay_path=csv_path)
        while time.monotonic()-started<seconds+45:
            if root:root.update()
            now=time.monotonic();ticks.append(now-last_tick);last_tick=now
            if now-last_snapshot>=.25:
                s=c.snapshot();s.pop('wave',None);elapsed=now-started
                snapshots.append(dict(demo_elapsed_s=elapsed,**s));last_snapshot=now
                if s['received'] and first_received is None:first_received=elapsed
                if s['classification'].get('model_loaded') and first_loaded is None:first_loaded=elapsed
                if s['classification'].get('classified') and first_prediction is None:first_prediction=elapsed
                if s['running']:last_live=s
                if elapsed-last_progress>=20:
                    print(json.dumps({'elapsed_s':round(elapsed,2),'received':s['received'],
                        'counts':s['classification'].get('counts'),'state':s['classification'].get('state')}),flush=True);last_progress=elapsed
                if s['error']:raise RuntimeError(s['error'])
                if not s['running']:break
                counts=s['classification'].get('counts') or {}
                current_label=next((k for k in ('N','S','V') if counts.get(k,0) and k not in seen_classes),None)
                new_class=current_label is not None
                timed_shot=first_received is not None and elapsed-first_received>=next_shot and next_shot<seconds
                if root and (timed_shot or new_class):
                    root.update_idletasks();x,y=root.winfo_rootx(),root.winfo_rooty()
                    file=output/(f'demo-count-{current_label}.png' if new_class else f'demo-{next_shot:03}.png')
                    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height()),xdisplay=os.environ['DISPLAY']).save(file)
                    screens.append(file.name)
                    if new_class:seen_classes.add(current_label)
                    if timed_shot:next_shot+=20
            time.sleep(.01)
        else:errors.append('Demo deadline exceeded')
    except Exception as exc:errors.append(f'{type(exc).__name__}: {exc}')
    finally:
        c.stop()
        if not c.wait(20):errors.append('Workers did not stop')
        monitor.finish()
        final=c.snapshot();final.pop('wave',None)
        if root:view._closed=True;root.destroy()
    if sid is None:
        result=dict(environment=env,errors=errors,checks={'started':False})
        (output/'demo.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    db=output/'data/sessions'/sid/'index.sqlite'
    with sqlite3.connect(db) as conn:
        events=[dict(kind=r[0],detail=json.loads(r[1])) for r in conn.execute('SELECT kind,detail_json FROM events ORDER BY event_id')]
    predictions=[e['detail'] for e in events if e['kind']=='cardioedge_prediction']
    c.export_session(sid,output/'raw.csv')
    counts={k:sum(p['label']==k for p in predictions) for k in ['N','S','V']}
    live_drops=max((s['classification'].get('dropped_samples',0) for s in snapshots if s['state']=='RECORDING'),default=0)
    stop_boundary=dict(max_live_dropped_samples=live_drops,
        final_dropped_samples=final['classification'].get('dropped_samples',0),
        saved_samples=final['saved'],processed_samples=final['classification'].get('processed_samples',0),
        note='EOF/stop cancels pending model work; preserve final counters, never call these raw-data losses.')
    checks=dict(no_errors=not errors and not final['error'] and not final['classification'].get('error'),
        all_replay_samples_saved=final['saved']==final['received']==len(raw),
        classifications_present=bool(predictions),all_three_classes_emitted=all(counts.values()),
        live_model_queue_dropped_zero=live_drops==0,
        stopped=not final['running'],event_count_matches=len(predictions)==final['classification']['classified'])
    result=dict(environment=env,test_kind='360Hz paced public MIT-BIH CSV replay, NOT physical UART or electrodes',
        source_record=record,source_file_sha256=hashlib.sha256(replay.read_bytes()).hexdigest(),
        source_seconds=seconds,samples=len(raw),headless=headless,errors=errors,checks=checks,stop_boundary=stop_boundary,
        first_received_s=first_received,first_model_loaded_s=first_loaded,first_prediction_s=first_prediction,
        classification_counts=counts,inference_ms=stats([p['inference_ms'] for p in predictions]),
        context_latency_ms=stats([p['sample_latency_ms'] for p in predictions]),
        queue_wait_ms=stats([p.get('queue_wait_ms') for p in predictions]),
        ui_loop_interval_ms=stats([t*1000 for t in ticks[5:]]) if root else None,
        ui_loop_note='loop/update interval including sleep and screenshots; not an FPS measurement',
        resources=monitor.summarize(),throttle_after=throttle(),last_live=last_live,final=final,screenshots=screens)
    for name,value in [('demo.json',result),('demo-resources.json',monitor.rows),('snapshots.json',snapshots),('events.json',events)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--kernel',action='store_true')
    p.add_argument('--repeats',type=int,default=3);p.add_argument('--replay',type=Path)
    p.add_argument('--seconds',type=int,default=60);p.add_argument('--headless',action='store_true')
    a=p.parse_args()
    if a.repeats<1 or a.seconds<1:p.error('repeats and seconds must be positive')
    if not a.kernel and not a.replay:p.error('demo requires --replay')
    a.output.mkdir(parents=True,exist_ok=False)
    result=kernel(a.output,a.repeats) if a.kernel else demo(a.output,a.replay,a.seconds,a.headless)
    print(json.dumps({k:v for k,v in result.items() if k not in ('final','last_live')},ensure_ascii=True),flush=True)
    raise SystemExit(0 if (result.get('exact_quantized_output_match',True) and all(result.get('checks',{}).values())) else 1)
