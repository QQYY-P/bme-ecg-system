"""Time-domain research statistics; normality must be supplied explicitly.

No interpolation, missing-beat repair, clinical classification or changes to
source data. Non-adjacent accepted intervals never become successive pairs.
"""
import numpy as np

VERSION='rr-hrv-research-v1'


def interval_statistics(r_samples, *, segments=None, normal=None, fs=360,
                        observation_seconds=None, rr_limits_ms=(280.,2500.)):
    peaks=np.asarray(r_samples,dtype=object)
    if peaks.ndim!=1:
        raise ValueError('R points must be finite, increasing, nonnegative integer sample positions')
    positions=[]
    for value in peaks:
        if isinstance(value,(int,np.integer)):
            position=int(value)
        elif isinstance(value,(float,np.floating)) and np.isfinite(value) and value==np.floor(value):
            position=int(value)
        else: raise ValueError('R points must be integer sample positions')
        if position<0 or (positions and position<=positions[-1]):
            raise ValueError('R points must be increasing and nonnegative')
        positions.append(position)
    if not np.isfinite(fs) or fs<=0:raise ValueError('Invalid sample rate')
    lo,hi=rr_limits_ms
    if not 0<lo<hi or not np.isfinite([lo,hi]).all():raise ValueError('Invalid RR bounds')
    segment=np.zeros(len(peaks),dtype=int) if segments is None else np.asarray(segments)
    if segment.ndim!=1 or len(segment)!=len(peaks):raise ValueError('Segment length mismatch')
    if normal is not None:
        normal=np.asarray(normal)
        if normal.shape!=peaks.shape or normal.dtype.kind!='b':raise ValueError('Normality must be an explicit boolean per beat')
    # Subtract exact integers before float conversion: absolute uint64 positions
    # and long gaps must not round ordinary within-segment RR intervals.
    rr=np.asarray([right-left for left,right in zip(positions,positions[1:])],dtype=float)*1000/fs
    same=segment[1:]==segment[:-1]
    within=(rr>=lo)&(rr<=hi)
    accepted=same&within

    def describe(mask,nn=False):
        values=rr[mask]
        # Two adjacent intervals share a beat AND all three beats share a segment.
        pairs=mask[:-1]&mask[1:] if len(mask)>1 else np.zeros(0,dtype=bool)
        differences=np.diff(rr)[pairs]
        result={'intervals':int(mask.sum()),'successive_pairs':int(pairs.sum()),
                'covered_seconds':float(values.sum()/1000),
                'mean_ms':float(values.mean()) if len(values) else None,
                'mean_rate_bpm':float(60000/values.mean()) if len(values) else None,
                'sdnn_ms' if nn else 'sdrr_ms':float(np.std(values,ddof=1)) if len(values)>1 else None,
                'rmssd_ms':float(np.sqrt(np.mean(differences**2))) if len(differences) else None,
                'pnn50_percent' if nn else 'prr50_percent':float(100*np.mean(np.abs(differences)>50)) if len(differences) else None}
        return result

    duration=float(observation_seconds) if observation_seconds is not None else (float((positions[-1]-positions[0])/fs) if len(positions)>1 else 0.)
    if not np.isfinite(duration) or duration<0:raise ValueError('Invalid observation duration')
    nn_mask=accepted&normal[:-1]&normal[1:] if normal is not None else None
    return {'version':VERSION,'sample_rate_hz':float(fs),'beat_count':len(peaks),
            'observation_seconds':duration,'shorter_than_five_minutes':duration<300,
            'rr_limits_ms':[float(lo),float(hi)],'rr':describe(accepted),
            'nn':describe(nn_mask,True) if nn_mask is not None else None,
            'nn_basis':'explicit normal-beat labels' if normal is not None else 'normality not confirmed; RR variability only',
            'excluded':{'segment_boundary':int((~same).sum()),'rr_range':int((same&~within).sum()),
                        'not_normal':int((accepted&~nn_mask).sum()) if nn_mask is not None else None},
            'policy':'No interpolation; no successive differences across exclusions; engineering RR bounds, not diagnostic limits.'}
