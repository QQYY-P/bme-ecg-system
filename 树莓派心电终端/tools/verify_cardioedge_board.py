"""On-board UI + real serial pipeline test. PTY replay is labelled separately.

--port auto checks physical STM32 without sending commands to it.
Without --port, --replay-npz feeds public ECG as 7-byte frames through a PTY.
"""
import argparse,csv,json,os,platform,resource,sqlite3,sys,threading,time
from pathlib import Path
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[name]='1'
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from controller import Controller
from protocol import encode_frame
from cardioedge.stream import StreamingClassifier
from contracts import SampleBatch,SamplePoint


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--replay-npz',type=Path)
    p.add_argument('--port');p.add_argument('--seconds',type=int,default=60)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    import tkinter as tk
    from ui.main_window import MainWindow
    from PIL import ImageGrab
    root=tk.Tk();root.geometry('800x480+0+0');c=Controller(a.output/'data');view=MainWindow(root,c)
    errors=[];root.report_callback_exception=lambda *args: errors.append(str(args[1]))
    phases={};snapshots=[];master=slave=None;sender=None;sent=0;sender_error=[];raw=None
    if a.port:
        port=a.port
    else:
        import pty
        master,slave=pty.openpty();port=os.ttyname(slave)
        assert port.startswith('/dev/pts/')
        raw=np.load(a.replay_npz)['raw_adc'][:a.seconds*360]
    def capture(name):
        root.update_idletasks()
        x,y=root.winfo_rootx(),root.winfo_rooty()
        ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height()),xdisplay=os.environ['DISPLAY']).save(a.output/(name+'.png'))
    def send():
        nonlocal sent
        try:
            for phase,values,mode in [('database_replay',raw,0),('board_simulated',raw[:720],1),
                                     ('invalid_marker',np.full(720,65535),0),('saturated',np.full(720,4095),0),
                                     ('flat',np.full(1440,2048),0)]:
                phases[phase]=dict(start_sample=sent,started=time.monotonic())
                start=time.monotonic()
                for i in range(0,len(values),36):
                    wire=b''.join(encode_frame((sent+j)%256,int(v),mode) for j,v in enumerate(values[i:i+36]))
                    remaining=memoryview(wire)
                    while remaining:remaining=remaining[os.write(master,remaining):]
                    sent+=len(values[i:i+36]);time.sleep(max(0,start+min(i+36,len(values))/360-time.monotonic()))
            phases['silence']=dict(start_sample=sent,started=time.monotonic())
        except Exception as exc:sender_error.append(str(exc))
    usage0=resource.getrusage(resource.RUSAGE_SELF);start=time.monotonic();last_tick=start;ticks=[];max_pending=0
    try:
        session=c.start(source='serial',port=port)
        # Wait until serial open; GUI updates remain active during loading.
        deadline=time.monotonic()+10
        while c.snapshot()['state']=='CONNECTING' and time.monotonic()<deadline:
            root.update();time.sleep(.02)
        if not a.port:
            sender=threading.Thread(target=send,daemon=True);sender.start()
        start=time.monotonic();next_snapshot=start;captured=False;end=start+a.seconds+(13 if not a.port else 0)
        while time.monotonic()<end:
            root.update();now=time.monotonic();ticks.append(now-last_tick);last_tick=now
            if now>=next_snapshot:
                state=c.snapshot();state.pop('wave',None);snapshots.append(dict(elapsed=now-start,**state))
                max_pending=max(max_pending,state['classification'].get('pending_blocks',0));next_snapshot+=.2
                if state.get('error'):errors.append(state['error']);break
            if not captured and now-start>min(a.seconds-2,35):capture('live-800');captured=True
            time.sleep(.01)
        capture('final-live-800')
        live=c.snapshot();live.pop('wave',None)
        c.stop()
        while c.snapshot()['running'] and time.monotonic()-end<20:
            root.update();time.sleep(.02)
        if not c.wait(10):errors.append('workers did not stop')
        final=c.snapshot();final.pop('wave',None)
        c.export_session(session,a.output/'raw.csv')
        rows=list(csv.DictReader((a.output/'raw.csv').open(encoding='utf-8-sig')))
        db=a.output/'data/sessions'/session/'index.sqlite'
        with sqlite3.connect(db) as conn:
            predictions=[json.loads(x[0]) for x in conn.execute("SELECT detail_json FROM events WHERE kind='cardioedge_prediction' ORDER BY event_id")]
        checks=dict(no_errors=not errors and not sender_error,received_saved_equal=final['received']==final['saved']==len(rows),
                    workers_stopped=not final['running'],model_no_error=not final['classification'].get('error'),
                    max_model_queue_under_limit=max_pending<12,
                    event_count_matches=final['classification'].get('classified',0)==len(predictions))
        if not a.port:
            switch_at=phases['board_simulated']['started']-start
            checks['no_analysis_drops_during_database_phase']=all(s['analysis_dropped_samples']==0
                for s in snapshots if s['elapsed']<switch_at)
            checks['all_transmitted_samples_saved']=sent==final['saved']
            checks['non_real_phases_never_classified']=all(r['r_sample']<len(raw) for r in predictions)
            checks['results_cleared_after_invalid']=all(not s['classification'].get('last') for s in snapshots
                if a.seconds+3<s['elapsed']<a.seconds+9)
            checks['classified_database_beats']=len(predictions)>10
            # Same stream, no GUI/no UART, automatic peaks: platform-path parity.
            engine=StreamingClassifier();expected=[]
            for i in range(0,len(raw),36):
                b=SampleBatch('reference','serial',tuple(SamplePoint(j,0,j,j%256,int(raw[j]),mode=0)
                    for j in range(i,min(i+36,len(raw)))),i/360,'public-database-replay')
                expected.extend(engine.process(b))
            actual=[(r['r_sample'],r['label'],r['scores']) for r in predictions]
            target=[(r['r_sample'],r['label'],r['scores']) for r in expected]
            checks['uart_matches_direct_stream']=actual==target
        else:
            checks['physical_mode_identified']=all(r.get('mode')=='0' for r in rows) and len(rows)>0
            if rows and all(int(r['raw_adc'])==65535 for r in rows):
                checks['all_invalid_input_suppressed']=(len(predictions)==0 and
                    all(not s['classification'].get('last') for s in snapshots))
        usage1=resource.getrusage(resource.RUSAGE_SELF)
        report=dict(platform=platform.platform(),physical_uart=bool(a.port),electrodes_connected=False,
            test_kind='physical_STM32_no_electrodes' if a.port else 'MIT-BIH_serial_PTY_replay_with_real_Tk_UI',
            source_record=str(np.load(a.replay_npz)['record']) if not a.port else None,
            seconds=a.seconds,sent_samples=sent if not a.port else None,checks=checks,passed=all(checks.values()),
            errors=errors+sender_error,final=final,last_live=live,snapshots=snapshots,predictions=predictions,
            ui_tick_p95_ms=float(np.percentile(ticks[5:],95)*1000),ui_tick_max_ms=float(max(ticks[5:])*1000),
            process_cpu_seconds=(usage1.ru_utime+usage1.ru_stime)-(usage0.ru_utime+usage0.ru_stime),
            peak_rss_mb=usage1.ru_maxrss/1024,max_model_pending_blocks=max_pending,
            adc_invalid_markers=sum(int(r['raw_adc'])==65535 for r in rows),
            observed_modes=sorted(set(r.get('mode') for r in rows)))
        (a.output/'board-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('snapshots','predictions','final','last_live')},ensure_ascii=False,indent=2),flush=True)
        return 0 if report['passed'] else 1
    finally:
        c.stop();c.wait(10);view._closed=True;root.destroy()
        if sender:sender.join(5)
        if master is not None:os.close(master);os.close(slave)

if __name__=='__main__':raise SystemExit(main())
