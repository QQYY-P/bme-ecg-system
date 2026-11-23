"""Optional laptop-only check of Pi vectors against the original INT8 model.

Requires ai-edge-litert, which is not required by the Pi application.
"""
from pathlib import Path
import argparse,hashlib,json,importlib.metadata
import numpy as np
from ai_edge_litert.interpreter import Interpreter,OpResolverType

def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,default=root.parent/'model/cardioedge_int8.tflite')
    p.add_argument('--vectors',type=Path,default=root/'cardioedge/pi_parity_vectors.npz')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();v=np.load(a.vectors,allow_pickle=False)
    it=Interpreter(model_content=a.model.read_bytes(),experimental_op_resolver_type=OpResolverType.BUILTIN_REF)
    it.allocate_tensors();pred=[]
    for x,rr in zip(v['wave'],v['rr']):
        it.set_tensor(0,x[None]);it.set_tensor(1,rr[None]);it.invoke();pred.append(it.get_tensor(30)[0])
    got=np.asarray(pred)
    result=dict(model_sha256=hashlib.sha256(a.model.read_bytes()).hexdigest(),
        vectors_sha256=hashlib.sha256(a.vectors.read_bytes()).hexdigest(),
        backend='LiteRT '+importlib.metadata.version('ai_edge_litert')+' BUILTIN_REF',
        vectors=len(got),exact_match=bool(np.array_equal(got,v['output'])),
        max_int8_difference=int(np.abs(got.astype(int)-v['output'].astype(int)).max()),
        note='INT8 reference parity only, not FP32 quantization loss.')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps(result));return 0 if result['exact_match'] else 1

if __name__=='__main__':raise SystemExit(main())
