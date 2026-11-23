"""Reproduce signal processing and audit a bounded physical capture, without diagnosis.

Run with the runtime source matching the capture. Never overwrites raw.csv or
the original session. Produces plots, processed CSV, predictions and audit JSON.
"""
import os
for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[_name] = '1'

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.signal import welch
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import SamplePoint, SampleBatch
from signal_analysis import SignalAnalyzer
from cardioedge.stream import StreamingClassifier
from protocol import StreamDecoder

FS = 360


def score_vector(row):
    scores = row['scores']
    return [float(scores[label]) for label in ('N','S','V')] if isinstance(scores,dict) else list(map(float,scores))


def stats(values):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if not len(x):
        return None
    return dict(min=float(x.min()), median=float(np.median(x)),
                max=float(x.max()), p95=float(np.percentile(x, 95)))


def continuous_runs(mask, segments):
    """Do not estimate a spectrum across invalid points or segment boundaries."""
    start = None
    for i, good in enumerate(mask):
        if start is not None and (not good or segments[i] != segments[i-1]):
            yield start, i
            start = None
        if good and start is None:
            start = i
    if start is not None:
        yield start, len(mask)


def analyze(directory):
    d = Path(directory)
    capture = json.loads((d/'capture.json').read_text(encoding='utf-8'))
    events = json.loads((d/'events.json').read_text(encoding='utf-8'))
    with (d/'raw.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError('No recorded samples; cannot generate a successful acquisition report')
    sid = rows[0]['session_id']
    session_dir = d/'data'/'sessions'/sid
    quality = json.loads((session_dir/'quality.json').read_text(encoding='utf-8'))
    metadata = json.loads((session_dir/'session.json').read_text(encoding='utf-8'))
    vref = float(metadata.get('vref_v', 3.3))
    raw = np.array([int(r['raw_adc']) for r in rows])
    ids = np.array([int(r['sample_id']) for r in rows])
    segments = np.array([int(r['segment_id']) for r in rows])
    flags = np.array([int(r['flags']) for r in rows])
    modes = np.array([int(r['mode']) for r in rows])
    valid = np.array([r['valid']=='1' for r in rows])
    if not np.array_equal(ids, np.arange(len(rows))):
        raise ValueError('Sample IDs are not the expected saved-sample ordinals')
    if any(r['session_id'] != sid for r in rows):
        raise ValueError('Mixed session IDs')
    n = len(rows)
    final = capture['final']
    processed_limit = int(final['classification'].get('processed_samples', n))
    times = ids/FS
    analyzer, model = SignalAnalyzer(), StreamingClassifier(vref=vref)
    prefix_model = StreamingClassifier(vref=vref)
    prefix_predictions = []
    processed = np.full(n, np.nan)
    peaks, offline, states = [], [], []
    for i in range(0, n, 36):
        points = tuple(SamplePoint(int(r['sample_id']), int(r['segment_id']),
            int(r['segment_index']), int(r['sequence']), int(r['raw_adc']),
            int(r['flags']), int(r['mode'])) for r in rows[i:i+36])
        batch = SampleBatch(sid, 'serial', points, i/FS, 'saved capture audit')
        output = analyzer.process(batch)
        wave = output['wave']
        keep = np.isfinite(wave['x'])
        indices = np.rint(wave['x'][keep]*FS).astype(int)
        processed[indices] = wave['filtered'][keep]
        peaks.extend(output['peaks'])
        offline.extend(model.process(batch))
        model.take_rejections()
        if i < processed_limit:
            prefix_batch = SampleBatch(sid, 'serial', points[:processed_limit-i], i/FS, 'online processed prefix')
            prefix_predictions.extend(prefix_model.process(prefix_batch))
            prefix_model.take_rejections()
        states.append(dict(sample=i, signal=output['signal_state'], hr=output['hr'], rr_ms=output['rr_ms']))
    predictions = [e['detail'] for e in events if e['kind']=='cardioedge_prediction']
    ref = {(r['segment_id'], r['r_sample']):r for r in offline}
    mismatches = []
    for r in predictions:
        other = ref.get((r['segment_id'], r['r_sample']))
        if other is None or r['label'] != other['label'] or not np.allclose(score_vector(r), score_vector(other), rtol=0, atol=1e-6):
            mismatches.append(dict(r_sample=r['r_sample'], label=r['label'], offline=other))
    live_keys = {(r['segment_id'], r['r_sample']) for r in predictions}
    extra = [r['r_sample'] for r in offline if (r['segment_id'], r['r_sample']) not in live_keys]
    # After each discontinuity, omit the first 2 s from PSD and visual filtered curves.
    mask = valid & (modes==0) & np.isfinite(processed) & ((flags & 28)==0)
    runs = list(continuous_runs(mask, segments))
    plotted = processed.copy()
    for begin, end in runs:
        plotted[begin:min(end,begin+720)] = np.nan
    candidates = [(b+720,e) for b,e in runs if e-b>=2160]
    spectrum, interval = {}, None
    psd = None
    if candidates:
        begin, end = max(candidates, key=lambda q:q[1]-q[0])
        f, pr = welch(raw[begin:end]*vref/4095, fs=FS, nperseg=1440, noverlap=720,
                      window='hann', detrend='constant', scaling='density', average='mean')
        _, pf = welch(processed[begin:end]*vref/4095, fs=FS, nperseg=1440, noverlap=720,
                      window='hann', detrend='constant', scaling='density', average='mean')
        interval = [begin/FS,end/FS]
        for name, lo, hi in [('baseline',0,.5), ('mains',48,52),
                             ('high_frequency',40,180), ('ecg_band',.5,40)]:
            band = (f>=lo)&(f<hi)
            before, after = float(pr[band].sum()*.25), float(pf[band].sum()*.25)
            spectrum[name] = dict(raw_power_v2=before, filtered_power_v2=after,
                change_db=float(10*np.log10(after/before)) if before>0 and after>0 else None)
        psd = f,pr,pf
        np.savez_compressed(d/'spectrum-data.npz', frequency_hz=f, raw_psd=pr, filtered_psd=pf)
    snaps = capture['snapshots']
    wire_bytes = sum(p.stat().st_size for p in (session_dir/'wire').glob('*.bin'))
    decoder = StreamDecoder()
    decoded = []
    for file in sorted((session_dir/'wire').glob('*.bin')):
        decoded.extend(decoder.feed(file.read_bytes()))
    wire_matches = [(s.sequence,s.value,s.mode) for s in decoded] == [(int(r['sequence']),int(r['raw_adc']),int(r['mode'])) for r in rows]
    signature = lambda items: [(r['segment_id'],r['r_sample'],r['label'],r['scores']) for r in items]
    prefix_match = signature(predictions)==signature(prefix_predictions)
    checks = dict(requested_180s=float(capture['requested_seconds'])==180,
        wall_duration_at_least_180s=float(capture['capture_elapsed_s'])>=180,
        sample_duration_at_least_180s=n/FS>=180,
        source_is_physical_serial=metadata.get('source')=='serial' and capture.get('physical_uart') is True,
        all_mode0=bool(np.all(modes==0)), all_samples_saved=n==final['received']==final['saved'],
        session_complete=quality['status']=='complete', no_capture_errors=not capture['errors'] and not final['error'],
        published_predictions_match_replay=bool(predictions) and not mismatches,
        serial_bytes_reproduce_saved_csv=wire_matches,
        online_processed_prefix_exact_match=prefix_match,
        classification_observed=bool(predictions))
    result = dict(session_id=sid, samples=n, sample_duration_s=n/FS,
        wall_capture_seconds=capture['capture_elapsed_s'], requested_seconds=capture['requested_seconds'],
        vref_v=vref, sample_rate_hz=FS, valid_samples=int(valid.sum()), valid_fraction=float(valid.mean()),
        mode_counts=dict(Counter(map(int,modes))), flag_counts=dict(Counter(map(int,flags))),
        segments=len(set(segments)), wire_bytes=wire_bytes, quality=quality,
        raw_adc_stats=stats(raw[valid]), adc_input_volts=stats(raw[valid]*vref/4095),
        live_signal_snapshot_counts=dict(Counter(s['signal_state'] for s in snaps)),
        live_model_snapshot_counts=dict(Counter(s['classification']['state'] for s in snaps)),
        heart_rate=stats([s['hr'] for s in snaps if s['hr'] is not None]),
        classified=len(predictions), class_counts={c:sum(r['label']==c for r in predictions) for c in ['N','S','V']},
        inference_ms=stats([r['inference_ms'] for r in predictions]),
        sample_latency_ms=stats([r['sample_latency_ms'] for r in predictions]),
        rr_previous_s=stats([r['rr_previous_s'] for r in predictions]),
        quantization_clipped_fraction=stats([r['quantization_clipped_fraction'] for r in predictions]),
        first_classified_r_s=min((r['r_sample']/FS for r in predictions),default=None),
        last_classified_r_s=max((r['r_sample']/FS for r in predictions),default=None),
        score_vectors=len({tuple(score_vector(r)) for r in predictions}), offline_classified=len(offline),
        replay_mismatches=mismatches, offline_extra_r_samples=extra,
        online_processed_prefix_samples=processed_limit,
        online_processed_prefix_classified=len(prefix_predictions),
        wire_audit=dict(decoded_samples=len(decoded), discarded_prefix_bytes=decoder.discarded,
            remaining_partial_bytes=len(decoder.buffer), checksum_errors=decoder.checksum_errors,
            bad_frames=decoder.bad_frames, observed_sequence_anomalies=decoder.discontinuities,
            matches_raw_csv=wire_matches),
        analysis_dropped_during_snapshots=max(s['analysis_dropped_samples'] for s in snaps),
        exact_replay_sequence_match=[(r['segment_id'],r['r_sample'],r['label'],r['scores']) for r in predictions]==[(r['segment_id'],r['r_sample'],r['label'],r['scores']) for r in offline],
        display_detector_peak_count=len(peaks), spectrum=spectrum, spectrum_interval_seconds=interval,
        spectrum_method='Welch Hann 1440 points overlap720 constant detrend mean PSD fs360',
        checks=checks, final=final, raw_sha256=hashlib.sha256((d/'raw.csv').read_bytes()).hexdigest(),
        ground_truth_available=False, clinically_verified=False,
        quality_basis='recorded sample flags; runtime signal states are discrete snapshots, not expert labels')
    (d/'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
    (d/'offline-predictions.json').write_text(json.dumps(offline,ensure_ascii=False,indent=2),encoding='utf-8')
    (d/'online-prefix-replay.json').write_text(json.dumps(prefix_predictions,ensure_ascii=False,indent=2),encoding='utf-8')
    (d/'offline-analysis-states.json').write_text(json.dumps(states,ensure_ascii=False,indent=2),encoding='utf-8')
    np.savez_compressed(d/'processed-waveforms.npz', time=times, raw_adc=raw,
                       display_filtered_adc=processed, display_peaks=peaks)
    with (d/'processed.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f); writer.writerow(['sample_id','time_s','segment_id','mode','valid','raw_adc','adc_input_v','filtered_adc','filtered_adc_input_v'])
        for i in range(n):
            writer.writerow([int(ids[i]),float(times[i]),int(segments[i]),int(modes[i]),int(valid[i]),int(raw[i]),float(raw[i]*vref/4095) if valid[i] else '',float(processed[i]) if np.isfinite(processed[i]) else '',float(processed[i]*vref/4095) if np.isfinite(processed[i]) else ''])
    with (d/'classifications.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['segment_id','r_sample','sample_time_s','label','score_N','score_S','score_V','inference_ms','sample_latency_ms'])
        for r in predictions:w.writerow([r['segment_id'],r['r_sample'],r['r_sample']/FS,r['label'],*score_vector(r),r['inference_ms'],r['sample_latency_ms']])
    if n >= FS*180:
        selected = rows[:FS*180]
        with (d/'raw-first-180s.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(selected)
        subset = dict(parent_session=sid, parent_raw_sha256=result['raw_sha256'],
            selection='first 64800 saved rows, sample_id 0..64799; no resampling or synthetic data',
            samples=FS*180, nominal_duration_s=180, sample_rate_hz=FS,
            all_valid=bool(valid[:FS*180].all()), all_mode0=bool((modes[:FS*180]==0).all()),
            segments=int(len(set(segments[:FS*180]))), flagged_samples=int((flags[:FS*180]!=0).sum()),
            sha256=hashlib.sha256((d/'raw-first-180s.csv').read_bytes()).hexdigest())
        (d/'subset-180s.json').write_text(json.dumps(subset,ensure_ascii=False,indent=2),encoding='utf-8')
    draw_plots(d,times,raw,valid,plotted,vref,predictions,peaks,psd)
    return result


def draw_plots(d,t,raw,valid,filtered,vref,predictions,peaks,psd):
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','WenQuanYi Micro Hei','DejaVu Sans'],
                         'axes.unicode_minus':False,'font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    fig, axes=plt.subplots(3,1,figsize=(10.4,7.4),sharex=True,gridspec_kw={'height_ratios':[1,1,.65]},layout='constrained')
    axes[0].plot(t,np.where(valid,raw*vref/4095,np.nan),color='#385e7e',lw=.5)
    axes[0].set_ylabel('原始 ADC 输入电压 / V')
    axes[1].plot(t,filtered*vref/4095,color='#178078',lw=.5);axes[1].set_ylabel('处理后等效电压 / V')
    for c,y,color in [('N',2,'#178078'),('S',1,'#c58d25'),('V',0,'#bb5050')]:
        x=[r['r_sample']/FS for r in predictions if r['label']==c]
        axes[2].scatter(x,[y]*len(x),s=9,color=color,label=f'{c}  {len(x)} 搏')
    axes[2].set(yticks=[0,1,2],yticklabels=['V','S','N'],ylim=(-.5,2.5),xlabel='已保存样本时间 / s',ylabel='在线研究分类')
    axes[2].legend(loc='upper center',bbox_to_anchor=(.5,-.3),ncol=3,frameon=False)
    for ax in axes:ax.grid(alpha=.18);ax.set_xlim(0,len(t)/FS)
    fig.savefig(d/'overview.png',dpi=240);plt.close(fig)
    fig,axes=plt.subplots(3,1,figsize=(10.4,7.4),layout='constrained')
    for ax,start in zip(axes,[15,85,max(0,len(t)/FS-20)]):
        keep=(t>=start)&(t<=start+10)
        ax.plot(t[keep],filtered[keep]*vref/4095,color='#178078',lw=.9)
        for peak in peaks:
            if start<=peak/FS<=start+10:ax.scatter(peak/FS,filtered[peak]*vref/4095,s=16,color='#ca6a28',zorder=4)
        for r in predictions:
            x=r['r_sample']/FS
            if start<=x<=start+10:ax.axvline(x,color='#607182',lw=.6,alpha=.35);ax.text(x,.98,r['label'],transform=ax.get_xaxis_transform(),ha='center',va='top',fontsize=9)
        ax.set(xlim=(start,start+10),xlabel='已保存样本时间 / s',ylabel='处理后等效电压 / V');ax.grid(alpha=.2)
    fig.savefig(d/'waveform-details.png',dpi=240);plt.close(fig)
    if psd is not None:
        f,pr,pf=psd
        fig,ax=plt.subplots(figsize=(10.4,4.2),layout='constrained')
        ax.semilogy(f,pr,label='原始 ADC 输入',color='#385e7e',lw=1.1)
        ax.semilogy(f,pf,label='显示预处理后',color='#178078',lw=1.1)
        ax.axvspan(48,52,color='#c58d25',alpha=.15,label='48–52 Hz 工频带')
        ax.set(xlim=(0,100),ylim=(1e-13,1e-1),xlabel='频率 / Hz',ylabel='功率谱密度 / (V²/Hz)');ax.grid(alpha=.18);ax.legend(frameon=False)
        fig.savefig(d/'spectrum.png',dpi=240);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    result=analyze(args.directory)
    print(json.dumps({k:result[k] for k in ['session_id','samples','classified','class_counts','checks']},ensure_ascii=False,indent=2))
