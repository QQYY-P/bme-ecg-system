"""Small NumPy execution adapter for this pinned, quantized CNN only.

Uses integer accumulators, Q31 conv/mean requantization, float64 FC/softmax.
Parity is measured against LiteRT BUILTIN_REF, not XNNPACK (which can produce
different INT8 results). This is an experimental, model-specific adapter.
"""
from pathlib import Path
import numpy as np

def fixed_multiplier(values,scales,divisor=1):
    """Q31 two-rounding multiplication, with C-style truncation for negatives."""
    mant,exp=np.frexp(np.asarray(scales,dtype=np.float64))
    mult=np.floor(mant*(1<<31)+.5).astype(np.int64)
    carry=mult==(1<<31);mult=np.where(carry,mult//2,mult);exp=exp+carry
    mult=np.where(exp < -31,0,mult);exp=np.where(exp < -31,0,exp)
    if divisor != 1:
        shift=np.minimum(min(int(divisor).bit_length()-1,32),31+exp)
        mult=(mult<<shift)//divisor
        exp=exp-shift
    left=np.maximum(exp,0);right=np.maximum(-exp,0)
    prod=(np.asarray(values,dtype=np.int64)*(1<<left))*mult
    nudged=prod+np.where(prod>=0,1<<30,1-(1<<30))
    high=np.where(nudged>=0,nudged//(1<<31),-((-nudged)//(1<<31)))
    mask=(1<<right)-1;remainder=high&mask;threshold=(mask>>1)+(high<0)
    return (high>>right)+(remainder>threshold)

class Candidate:
    def __init__(self,path=None):
        z=np.load(path or Path(__file__).with_name('portable_weights.npz'),allow_pickle=False)
        self.w={k:z[k] for k in z.files}
    def qinfo(self,i):return self.w[f's{i}'],self.w[f'z{i}']
    def quant(self,a,i):
        s,z=self.qinfo(i);return np.clip(np.rint(a/s)+z,-128,127).astype(np.int32)
    def dequant(self,a,i):
        s,z=self.qinfo(i);return (a-z)*s
    def conv(self,x,wi,bi,ii,oi):
        w=self.w[f'w{wi}'].squeeze(1).astype(np.int32);b=self.w[f'w{bi}'].astype(np.int32)
        length,channels=x.shape;k=w.shape[1];sin,zin=self.qinfo(ii);sout,zout=self.qinfo(oi);sw,_=self.qinfo(wi)
        padded=np.pad(x.astype(np.int32)-int(zin[0]),((k//2,k//2),(0,0)))
        windows=np.lib.stride_tricks.sliding_window_view(padded,k,axis=0).transpose(0,2,1).reshape(length,-1)
        acc=windows@w.reshape(w.shape[0],-1).T+b
        return np.clip(fixed_multiplier(acc,sin*sw/sout)+zout,-128,127).astype(np.int32)
    def fc(self,x,wi,bi,ii,oi,relu=False):
        sin,zin=self.qinfo(ii);sout,zout=self.qinfo(oi);sw,zw=self.qinfo(wi)
        wz=zw.astype(np.int32).reshape(-1,1)
        acc=(x.astype(np.int32)-zin.astype(np.int32))@(self.w[f'w{wi}'].astype(np.int32)-wz).T+self.w[f'w{bi}'].astype(np.int32)
        low=int(zout[0]) if relu else -128
        scaled=acc.astype(np.float64)*(sin*sw/sout)
        # LiteRT 2.3 BUILTIN_REF per-channel FC uses C++ round(double),
        # which rounds halfway values away from zero, not Q31 or bankers' rounding.
        rounded=np.copysign(np.floor(np.abs(scaled)+.5),scaled)
        return np.clip(rounded+zout,low,127).astype(np.int32)
    def predict_quantized(self,wave,rr,trace=None):
        if np.asarray(wave).shape!=(360,1) or np.asarray(rr).shape!=(4,):raise ValueError('wrong input shape')
        def save(i,a):
            if trace is not None:trace[i]=a.copy()
        x=self.conv(wave,7,4,0,21);save(21,x);x=x.reshape(180,2,16).max(1);save(22,x)
        x=self.conv(x,6,3,22,23);save(23,x);x=x.reshape(90,2,32).max(1);save(24,x)
        x=self.conv(x,5,2,24,25);save(25,x)
        sin,zin=self.qinfo(25);sout,zout=self.qinfo(26)
        pooled=np.clip(fixed_multiplier((x-zin).sum(0),sin/sout,divisor=len(x))+zout,-128,127).astype(np.int32)
        save(26,pooled)
        sin,zin=self.qinfo(26);sout,zout=self.qinfo(31)
        pooled=np.clip(fixed_multiplier(pooled-zin,sin/sout)+zout,-128,127).astype(np.int32);save(31,pooled)
        x=np.concatenate([pooled,rr.astype(np.int32)]);save(27,x)
        x=self.fc(x,9,10,27,28,True);save(28,x);x=self.fc(x,8,11,28,29);save(29,x)
        logits=self.dequant(x,29);p=np.exp(logits-logits.max());p=p/p.sum()
        return self.quant(p,30).astype(np.int8)

if __name__=='__main__':
    import json,time,platform,hashlib
    root=Path(__file__).resolve().parent;m=Candidate();v=np.load(root/'pi_parity_vectors.npz');pred=[];times=[]
    for x,rr in zip(v['wave'],v['rr']):
        t=time.perf_counter();pred.append(m.predict_quantized(x,rr));times.append(1000*(time.perf_counter()-t))
    pred=np.array(pred);diff=np.abs(pred.astype(int)-v['output'].astype(int));agreement=float(np.mean(pred.argmax(1)==v['output'].argmax(1)))
    result={'platform':platform.platform(),'machine':platform.machine(),'python':platform.python_version(),'numpy':np.__version__,
            'samples':len(pred),'max_int8_output_difference':int(diff.max()),'mean_int8_output_difference':float(diff.mean()),
            'classification_agreement':agreement,'median_ms':float(np.median(times)),'p95_ms':float(np.percentile(times,95)),
            'weights_sha256':hashlib.sha256((root/'portable_weights.npz').read_bytes()).hexdigest(),
            'reference_backend':str(v['reference_backend']) if 'reference_backend' in v else 'UNSPECIFIED',
            'adapter_scope':'Model-specific NumPy execution with measured reference-kernel parity; not an official TFLite runtime',
            'parity_passed':bool(agreement>=.995 and diff.max()<=3)}
    (root/'adapter-benchmark.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
