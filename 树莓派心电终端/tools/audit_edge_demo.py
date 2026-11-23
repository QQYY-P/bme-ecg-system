"""Recompute the online processed prefix and verify public replay capture."""
import argparse,csv,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from contracts import SamplePoint,SampleBatch
from cardioedge.stream import StreamingClassifier

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('demo',type=Path);p.add_argument('--replay',required=True,type=Path)
    a=p.parse_args();d=json.loads((a.demo/'demo.json').read_text(encoding='utf-8'))
    with (a.demo/'raw.csv').open(encoding='utf-8-sig',newline='') as f:raw=[int(r['raw_adc']) for r in csv.DictReader(f)]
    source=np.load(a.replay,allow_pickle=False)['raw_adc'][:d['samples']]
    n=d['final']['classification']['processed_samples']
    e=StreamingClassifier();rows=[]
    for start in range(0,n,36):
        points=tuple(SamplePoint(i,0,i,i%256,raw[i],mode=0) for i in range(start,min(n,start+36)))
        rows.extend(e.process(SampleBatch('audit','replay',points,start/360,'public-replay')))
    events=json.loads((a.demo/'events.json').read_text(encoding='utf-8'))
    online=[r['detail'] for r in events if r['kind']=='cardioedge_prediction']
    key=lambda r:(r['r_sample'],r['label'],r['scores'])
    snapshots=json.loads((a.demo/'snapshots.json').read_text(encoding='utf-8'))
    drops=[r for r in snapshots if r['classification'].get('dropped_samples',0)]
    result=dict(saved_samples=len(raw),model_processed_prefix=n,unprocessed_tail=len(raw)-n,
        online_beats=len(online),prefix_recomputed_beats=len(rows),
        raw_matches_public_source=bool(np.array_equal(raw,source)),
        prefix_positions_labels_scores_exact=[key(r) for r in online]==[key(r) for r in rows],
        first_nonzero_drop_state=drops[0]['state'] if drops else None,
        first_nonzero_drop_received=drops[0]['received'] if drops else None,
        drops_only_after_receiving_all=all(r['received']==len(raw) and r['state'] in ('STOPPING','IDLE') for r in drops),
        note='Checks saved data and already-published predictions, not completion of every tail prediction.')
    (a.demo/'prefix-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result));assert result['raw_matches_public_source'] and result['prefix_positions_labels_scores_exact'] and result['drops_only_after_receiving_all']

if __name__=='__main__':main()
