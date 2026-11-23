"""Audit saved human ECG locally; no diagnosis and no upload to third parties."""
import os
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'): os.environ[name]='1'
import argparse,csv,json,sys
from pathlib import Path
from collections import Counter
import numpy as np
from scipy.signal import welch
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from contracts import SamplePoint,SampleBatch
from signal_analysis import SignalAnalyzer
from cardioedge.stream import StreamingClassifier


def main():
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);a=p.parse_args();d=a.directory
    capture=json.loads((d/'capture.json').read_text(encoding='utf8'))
    events=json.loads((d/'events.json').read_text(encoding='utf8'))
    rows=list(csv.DictReader((d/'raw.csv').open(encoding='utf-8-sig')))
    raw=np.array([int(r['raw_adc']) for r in rows]);n=len(raw);t=np.arange(n)/360
    valid=np.array([r['valid']=='1' for r in rows]);mode=np.array([int(r['mode']) for r in rows])
    flags=np.array([int(r['flags']) for r in rows]);segment=np.array([int(r['segment_id']) for r in rows])
    pred=[r['detail'] for r in events if r['kind']=='cardioedge_prediction']
    analyzer=SignalAnalyzer();model=StreamingClassifier();processed=np.full(n,np.nan)
    peaks=[];offline=[];states=[]
    for i in range(0,n,36):
        points=tuple(SamplePoint(int(r['sample_id']),int(r['segment_id']),int(r['segment_index']),
            int(r['sequence']),int(r['raw_adc']),int(r['flags']),int(r['mode'])) for r in rows[i:i+36])
        batch=SampleBatch(rows[0]['session_id'],'serial',points,i/360,'saved capture audit')
        out=analyzer.process(batch);w=out['wave'];keep=np.isfinite(w['x'])
        ids=np.rint(w['x'][keep]*360).astype(int);processed[ids]=w['filtered'][keep]
        peaks.extend(out['peaks']);offline.extend(model.process(batch))
        states.append(dict(time=i/360,signal=out['signal_state'],hr=out['hr'],rr=out['rr_ms']))
    snaps=capture['snapshots'];hrs=[s['hr'] for s in snaps if s['hr'] is not None]
    def stats(x):
        x=np.asarray(x,float);x=x[np.isfinite(x)]
        return dict(min=float(x.min()),median=float(np.median(x)),max=float(x.max())) if len(x) else None
    # Welch only on a contiguous, valid interval, excluding filter startup.
    mask=valid & (mode==0) & np.isfinite(processed) & ((flags & 28)==0)
    edges=np.flatnonzero(np.diff(np.r_[False,mask,False]));runs=list(zip(edges[::2],edges[1::2]))
    begin,end=max(runs,key=lambda q:q[1]-q[0]);begin+=720
    spectrum={}
    if end-begin>=1440:
        f,pr=welch(raw[begin:end]*3.3/4095,fs=360,nperseg=1440)
        _,pf=welch(processed[begin:end]*3.3/4095,fs=360,nperseg=1440)
        for name,lo,hi in [('baseline',0,.5),('mains',48,52),('high_frequency',40,180),('ecg_band',.5,40)]:
            ix=(f>=lo)&(f<hi);r=float(np.sum(pr[ix])*.25);v=float(np.sum(pf[ix])*.25)
            spectrum[name]=dict(raw_power_v2=r,filtered_power_v2=v,change_db=10*np.log10(v/r) if r>0 and v>0 else None)
        fig,ax=plt.subplots(figsize=(10,4));ax.semilogy(f,pr,label='Raw ADC input');ax.semilogy(f,pf,label='Display preprocessing')
        ax.set(xlim=(0,100),xlabel='Frequency (Hz)',ylabel='PSD (V²/Hz)',title='Same recorded interval: before/after display preprocessing')
        ax.legend();ax.grid(alpha=.25);fig.tight_layout();fig.savefig(d/'spectrum.png',dpi=150);plt.close(fig)
    result=dict(samples=n,sample_duration_s=n/360,wall_capture_seconds=capture['capture_elapsed_s'],
        valid_samples=int(valid.sum()),invalid_markers=int((raw==65535).sum()),saturated_samples=int(np.isin(raw,[0,4095]).sum()),
        mode_counts=dict(Counter(map(int,mode))),flag_counts=dict(Counter(map(int,flags))),segments=int(len(set(segment))),
        raw_adc_stats=stats(raw[valid]),raw_adc_input_volts=stats(raw[valid]*3.3/4095),
        live_signal_snapshot_counts=dict(Counter(s['signal_state'] for s in snaps)),
        live_model_snapshot_counts=dict(Counter(s['classification']['state'] for s in snaps)),
        heart_rate_display=stats(hrs),classified=len(pred),class_counts=dict(Counter(r['label'] for r in pred)),
        inference_ms=stats([r['inference_ms'] for r in pred]),sample_latency_ms=stats([r['sample_latency_ms'] for r in pred]),
        classifier_rr_s=stats([r['rr_previous_s'] for r in pred]),quantization_clipped_fraction=stats([r['quantization_clipped_fraction'] for r in pred]),
        published_matches_offline=[(r['r_sample'],r['label'],r['scores']) for r in pred]==[(r['r_sample'],r['label'],r['scores']) for r in offline],
        offline_classified=len(offline),display_detector_peak_count=len(peaks),spectrum=spectrum,
        spectrum_interval_seconds=[begin/360,end/360],errors=capture['errors'],final=capture['final'],
        clinically_verified=False,ground_truth_labels_available=False)
    (d/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    (d/'offline-predictions.json').write_text(json.dumps(offline,ensure_ascii=False,indent=2),encoding='utf8')
    np.savez_compressed(d/'processed-waveforms.npz',time=t,raw_adc=raw,display_filtered_adc=processed,display_peaks=peaks)
    # Explicit ADC input voltage units, not uncalibrated electrode millivolts.
    fig,axes=plt.subplots(3,1,figsize=(13,8));axes[0].plot(t,np.where(valid,raw*3.3/4095,np.nan),lw=.45)
    axes[0].set(title='Human electrode capture: raw ADC input (not calibrated electrode voltage)',ylabel='ADC input (V)')
    axes[1].plot(t,processed*3.3/4095,lw=.45,color='#008f70');axes[1].set(ylabel='Filtered (V)')
    for label,color,y in [('N','#007f5f',2),('S','#d18b00',1),('V','#cc3333',0)]:
        xs=[r['r_sample']/360 for r in pred if r['label']==label];axes[2].scatter(xs,[y]*len(xs),s=12,color=color,label=label)
    axes[2].set(yticks=[0,1,2],yticklabels=['V','S','N'],ylim=(-.5,2.5),xlabel='Sample time (s)',ylabel='Research output')
    for ax in axes: ax.grid(alpha=.2);ax.set_xlim(0,n/360)
    fig.tight_layout();fig.savefig(d/'two-minute-overview.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(3,1,figsize=(13,9))
    for ax,start in zip(axes,[15,55,max(0,n/360-15)]):
        ix=(t>=start)&(t<=start+10);ax.plot(t[ix],processed[ix]*3.3/4095,color='#008f70',lw=.9)
        for r in pred:
            x=r['r_sample']/360
            if start<=x<=start+10:
                ax.axvline(x,color='gray',alpha=.3,lw=.7);ax.text(x,1.01,r['label'],transform=ax.get_xaxis_transform(),ha='center',fontsize=9)
        for peak in peaks:
            if start<=peak/360<=start+10:ax.scatter(peak/360,processed[peak]*3.3/4095,s=13,color='#d04b27')
        ax.set(xlim=(start,start+10),ylabel='Filtered ADC input (V)',xlabel='Time (s)');ax.grid(alpha=.25)
    fig.suptitle('Display waveform / detected peaks (orange) / model R locations and labels (gray)\nDifferent display/model filtering can shift peaks; labels are not clinical ground truth',fontsize=11)
    fig.tight_layout();fig.savefig(d/'waveform-details.png',dpi=150);plt.close(fig)
    print(json.dumps({k:v for k,v in result.items() if k not in ('final',)},ensure_ascii=True,indent=2))


if __name__=='__main__':main()
