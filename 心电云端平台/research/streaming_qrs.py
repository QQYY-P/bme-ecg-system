"""Replay the unchanged project QRS detector and quality gate, without a model."""
import time
import numpy as np
from cloud_model.vendor.rpeak_detector import RPeakDetector
from cloud_model.vendor.signal_quality import SignalQualityGate

VERSION='quality-gated-qrs-replay-v1'
FS=360


def detect_adc(adc, *, block_size=36):
    raw=np.asarray(adc,dtype=float)
    if raw.ndim!=1 or not isinstance(block_size,int) or not 1<=block_size<=3600:
        raise ValueError('Expected a 1D ADC stream and a block size from 1 to 3600')
    detector,gate=RPeakDetector(),SignalQualityGate()
    peaks,segments,latencies,block_ms=[],[],[],[]
    epoch=-1;previous_good=False;allowed_total=invalid_total=0
    started=time.perf_counter()
    for first in range(0,len(raw),block_size):
        block_started=time.perf_counter();chunk=raw[first:first+block_size]
        valid=np.isfinite(chunk)&(chunk>0)&(chunk<4095)
        invalid_total+=int((~valid).sum())
        edges=np.r_[0,np.flatnonzero(valid[1:]!=valid[:-1])+1,len(chunk)]
        for start,stop in zip(edges[:-1],edges[1:]):
            if not valid[start]:
                gate.reset();detector.reset();previous_good=False;continue
            allowed=gate.process(chunk[start:stop]);allowed_total+=int(allowed.sum())
            transitions=np.r_[0,np.flatnonzero(allowed[1:]!=allowed[:-1])+1,len(allowed)]
            for left,right in zip(transitions[:-1],transitions[1:]):
                if not allowed[left]:
                    detector.reset();previous_good=False;continue
                if not previous_good:
                    detector.reset();epoch+=1
                previous_good=True
                lo,hi=first+start+left,first+start+right
                found=np.rint(detector.process(np.arange(lo,hi)/FS,raw[lo:hi])*FS).astype(int)
                peaks.extend(found.tolist());segments.extend([epoch]*len(found))
                # Call-return latency includes the caller's current block buffering.
                latencies.extend(((first+len(chunk)-1-found)*1000/FS).tolist())
        block_ms.append((time.perf_counter()-block_started)*1000)
    elapsed=time.perf_counter()-started
    duration=len(raw)/FS
    return {'version':VERSION,'r_samples':peaks,'segments':segments,'sample_count':len(raw),
            'allowed_samples':allowed_total,'invalid_samples':invalid_total,
            'quality_coverage':allowed_total/len(raw) if len(raw) else None,
            'performance':{'wall_seconds':elapsed,'signal_seconds':duration,
                'real_time_factor':elapsed/duration if duration else None,
                'samples_per_second':len(raw)/elapsed if elapsed else None,
                'block_size':block_size,'block_p95_ms':float(np.percentile(block_ms,95)) if block_ms else None,
                'block_max_ms':max(block_ms) if block_ms else None,
                'confirmation_p95_ms':float(np.percentile(latencies,95)) if latencies else None,
                'confirmation_max_ms':max(latencies) if latencies else None,
                'scope':'QRS + quality gate in local replay; excludes input loading, classification, network and queue wait'}}
