"""Continuous public-database replay through the actual live classifier.

Annotations are used ONLY for scoring after inference. No annotated R peaks,
whole-record statistics, training or threshold search enter the live engine.
"""
import argparse, json, sys, time, hashlib
from pathlib import Path
import numpy as np
import wfdb

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from contracts import SamplePoint,SampleBatch
from cardioedge.stream import StreamingClassifier,CONFIG,LABELS

MAP={s:i for i,syms in enumerate(['NLRej','AaJS','VE']) for s in syms}
BEATS=set('NLRBAaJSVrFejnE/fQ?')
DS2=['100','103','105','111','113','117','121','123','200','210','212','213','214','219','221','222','228','231','232','233','234']

def score(ann, rows, start, stop):
    refs=[(int(r),s) for r,s in zip(ann.sample,ann.symbol) if s in BEATS and start<=r<stop]
    predictions=[r for r in rows if start<=r['r_sample']<stop]
    positions=np.array([r for r,s in refs]);used=set();matches=[];unmatched=[]
    # One-to-one matching at +/-50 ms, deterministic in chronological order.
    for prediction in predictions:
        candidates=np.flatnonzero(np.abs(positions-prediction['r_sample'])<=18)
        candidates=sorted((int(i) for i in candidates if int(i) not in used),key=lambda i:abs(positions[i]-prediction['r_sample']))
        if candidates:
            j=candidates[0];used.add(j);matches.append((refs[j][1],prediction['label']))
        else:unmatched.append(prediction['label'])
    support=np.array([sum(MAP.get(s)==i for r,s in refs) for i in range(3)])
    cm=np.zeros((3,3),int)
    for truth,pred in matches:
        if truth in MAP:cm[MAP[truth],LABELS.index(pred)]+=1
    predicted=np.array([sum(p['label']==label for p in predictions) for label in LABELS])
    tp=cm.diagonal();precision=tp/np.maximum(predicted,1);recall=tp/np.maximum(support,1)
    f1=2*precision*recall/np.maximum(precision+recall,1e-12)
    return dict(annotated_beats=len(refs),emitted_predictions=len(predictions),matched_predictions=len(matches),
        unmatched_predictions=len(unmatched),target_support=support.tolist(),predicted_counts=predicted.tolist(),
        matched_confusion=cm.tolist(),precision=precision.tolist(),end_to_end_recall=recall.tolist(),
        end_to_end_f1=f1.tolist(),target_coverage=float(cm.sum()/max(1,support.sum())),
        correct_over_all_target_beats=float(tp.sum()/max(1,support.sum())),
        matching_tolerance_ms=50)

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--records',nargs='+',default=DS2);p.add_argument('--seconds',type=int,default=300);p.add_argument('--export-record')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);reports={};allrows=[];runtime=[]
    for name in a.records:
        rec=wfdb.rdrecord(str(a.data/name),sampto=a.seconds*360);ann=wfdb.rdann(str(a.data/name),'atr')
        assert rec.fs==360
        channel=rec.sig_name.index('MLII');mv=rec.p_signal[:,channel]
        # Fixed simulated front-end gain, never scaled using whole-record extrema.
        # ADC input voltage = 2048*3.3/4095 + 250*3.3/4095 * signal_mV.
        raw=np.clip(np.rint(2048+250*mv),0,4095).astype(int)
        engine=StreamingClassifier();rows=[];t=time.perf_counter()
        for start in range(0,len(raw),36):
            points=tuple(SamplePoint(i,0,i,i%256,int(raw[i]),mode=0) for i in range(start,min(start+36,len(raw))))
            batch=SampleBatch(name,'replay',points,start/360,'database-replay')
            rows.extend(engine.process(batch))
        seconds=time.perf_counter()-t;runtime.append(seconds)
        scored=score(ann,rows,15*360,(a.seconds-2)*360)
        scored.update(runtime_seconds=seconds,last_status=engine.snapshot(),clipped_adc_samples=int(np.sum((raw==0)|(raw==4095))))
        reports[name]=scored;allrows.extend(dict(record=name,**r) for r in rows)
        print(name,json.dumps({k:v for k,v in scored.items() if k not in ('last_status',)},ensure_ascii=False),flush=True)
        if a.export_record==name:
            np.savez_compressed(a.output/'uart-replay.npz',raw_adc=raw,record=name,fs=360,source='MIT-BIH public data; simulated ADC, NOT patient capture')
    support=np.sum([r['target_support'] for r in reports.values()],axis=0)
    predicted=np.sum([r['predicted_counts'] for r in reports.values()],axis=0)
    cm=np.sum([r['matched_confusion'] for r in reports.values()],axis=0);tp=cm.diagonal()
    pr=tp/np.maximum(predicted,1);se=tp/np.maximum(support,1);f=2*pr*se/np.maximum(pr+se,1e-12)
    report=dict(config=CONFIG,stream_source_sha256=hashlib.sha256((ROOT/'cardioedge/stream.py').read_bytes()).hexdigest(),
        records=a.records,seconds_per_record=a.seconds,scored_interval_seconds=[15,a.seconds-2],
        expert_peaks_used_for_inference=False,whole_record_normalization=False,
        simulated_adc_counts_per_millivolt=250,adc_offset=2048,by_record=reports,
        aggregate=dict(support=support.tolist(),predicted=predicted.tolist(),confusion=cm.tolist(),
            precision=pr.tolist(),recall=se.tolist(),f1=f.tolist(),macro_f1=float(f.mean()),
            coverage=float(cm.sum()/support.sum()),correct_over_all_target_beats=float(tp.sum()/support.sum()),
            unmatched_predictions=sum(r['unmatched_predictions'] for r in reports.values())),
        host_runtime_seconds=sum(runtime),validation_scope='causal delivery of recorded database signal; not live electrodes')
    (a.output/'stream-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with (a.output/'predictions.jsonl').open('w',encoding='utf-8') as f:
        for row in allrows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps(report['aggregate'],indent=2))

if __name__=='__main__':main()
