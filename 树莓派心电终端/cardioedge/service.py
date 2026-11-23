"""Separate model PROCESS: avoids contention with Pi 2 Tk/analysis's GIL.

Parent-only collector records results; shared generations discard stale work.
Queues, history and shutdown are bounded. Acquisition never runs model code.
"""
from copy import deepcopy
from collections import deque
import multiprocessing as mp
import queue
import threading
import time
from .stream import StreamingClassifier


def _worker(inputs, outputs, generation, cancelled, vref):
    def emit(value):
        try:
            outputs.put(value,timeout=.5)
            return True
        except queue.Full:
            return False
    try:
        engine=StreamingClassifier(vref=vref);active=-1
        emit((None,'loaded',None))
        while not cancelled.is_set():
            try:
                version,batch=inputs.get(timeout=.1)
            except queue.Empty:
                continue
            if version != generation.value:
                continue
            if version != active:
                engine.reset();active=version
            start=time.monotonic();wait=max(0.,start-batch.received_monotonic)
            if wait>1.5:
                if not emit((version,'overload',len(batch.points))):return
                continue
            if not emit((version,'processing',None)):return
            rows=engine.process(batch)
            rejected=engine.take_rejections()
            if version != generation.value or cancelled.is_set():
                continue
            if not emit((version,'result',(engine.snapshot(),rows,wait*1000,(time.monotonic()-start)*1000,rejected))):return
    except Exception as exc:
        emit((None,'error',f'{type(exc).__name__}: {exc}'))


class ClassificationService:
    INITIAL_WAIT_SECONDS = 8
    STARTUP_TIMEOUT_SECONDS = 60

    def __init__(self,vref=3.3,on_result=None,on_rejected=None):
        self.vref,self.on_result=vref,on_result
        self.on_rejected=on_rejected
        self.recent=deque(maxlen=60)
        self.ctx=mp.get_context('spawn')
        self.queue=self.ctx.Queue(maxsize=12)
        self.outputs=self.ctx.Queue(maxsize=48)
        self.version=self.ctx.Value('q',0)
        self.cancel=self.ctx.Event()
        self.lock=threading.RLock()
        self.ready=threading.Event()
        self.thread=self.process=None
        self.queues_closed=False
        self.startup_deadline=None
        self.last_received=self.last_prediction=None
        self.status=dict(state='loading',detail='模型准备中',last=None,classified=0,
                         counts=dict(N=0,S=0,V=0),rejected=0,dropped_samples=0,error=None,
                         max_queue_wait_ms=0.,max_processing_ms=0.,processing=False,
                         execution='separate_process',model_loaded=False,startup_skipped_samples=0)

    def start(self):
        self.startup_deadline=time.monotonic()+self.STARTUP_TIMEOUT_SECONDS
        self.process=self.ctx.Process(target=_worker,args=(self.queue,self.outputs,self.version,self.cancel,self.vref),
                                      name='ecg-cardioedge',daemon=True)
        self.process.start()
        self.thread=threading.Thread(target=self._collect,name='ecg-classification-results',daemon=True)
        self.thread.start()
        # Called from Controller's lifecycle thread, never the Tk thread. Open
        # Wait briefly before acquisition. Slow Pi cold starts keep loading in
        # the background; raw samples are saved but never queued as stale work.
        if not self.ready.wait(self.INITIAL_WAIT_SECONDS):
            with self.lock:
                if not self.cancel.is_set() and not self.status.get('model_loaded') and not self.status.get('error'):
                    self.status.update(state='loading',detail='模型仍在加载；采集保存继续',last=None)

    def invalidate(self,reason):
        with self.lock:
            self.version.value+=1
            self.status.update(last=None,processing=False)
            if self.status.get('error'):
                self.status.update(state='error',detail='分类不可用：'+self.status['error']+'；波形采集继续')
            elif not self.cancel.is_set() and not self.status.get('model_loaded'):
                self.status.update(state='loading',detail='模型仍在加载；采集保存继续')
            else:
                self.status.update(state='waiting',detail=reason)
            if self.queues_closed:
                return
            while True:
                try:
                    _,batch=self.queue.get_nowait()
                    self.status['dropped_samples']+=len(batch.points)
                except queue.Empty:
                    break

    def submit(self,batch):
        with self.lock:
            if self.cancel.is_set() or self.status.get('error'):
                return
            if not self.status.get('model_loaded'):
                self.status['startup_skipped_samples']+=len(batch.points)
                return
            now=time.monotonic()
            if self.last_received is not None and now-self.last_received>2:
                self.invalidate('数据中断后重新积累')
            self.last_received=now
            try:
                self.queue.put_nowait((self.version.value,batch))
            except queue.Full:
                self.invalidate('分类落后，重新积累；采集保存继续')
                self.status['dropped_samples']+=len(batch.points)

    def _collect(self):
        try:
            while not self.cancel.is_set():
                try:
                    version,kind,payload=self.outputs.get(timeout=.1)
                except queue.Empty:
                    if self.process and not self.process.is_alive():
                        raise RuntimeError('分类进程意外退出')
                    if (not self.status.get('model_loaded') and self.startup_deadline is not None
                            and time.monotonic()>=self.startup_deadline):
                        raise RuntimeError('模型初始化超时，请停止后重试')
                    continue
                with self.lock:
                    if self.cancel.is_set():return
                    if kind=='error':raise RuntimeError(payload)
                    if kind=='loaded':
                        if self.startup_deadline is not None and time.monotonic()>=self.startup_deadline:
                            raise RuntimeError('模型初始化超时，请停止后重试')
                        self.status.update(model_loaded=True,state='waiting',detail='模型已就绪，重新积累有效实采信号',last=None)
                        self.last_received=None
                        self.ready.set()
                        continue
                    if version!=self.version.value:continue
                    if kind=='overload':
                        self.invalidate('分类队列过期，重新积累')
                        self.status['dropped_samples']+=payload
                    elif kind=='processing':
                        self.status['processing']=True
                    elif kind=='result':
                        update,rows,wait,elapsed,rejections=payload
                        update.pop('classified',None);update.pop('counts',None);update.pop('rejected',None)
                        self.status.update(update,processing=False)
                        self.status['max_queue_wait_ms']=max(self.status['max_queue_wait_ms'],wait)
                        self.status['max_processing_ms']=max(self.status['max_processing_ms'],elapsed)
                        for row in rows:
                            row['prediction_id']=self.status['classified']+1
                            row['queue_wait_ms']=wait
                            if self.on_result and self.on_result(row) is False:
                                raise RuntimeError('分类结果无法保存')
                            self.last_prediction=time.monotonic()
                            self.status['classified']+=1
                            self.status['counts'][row['label']]+=1
                            self.recent.append(deepcopy(row))
                            last=self.status.get('last')
                            if last and all(last.get(k)==row.get(k) for k in ('session_id','segment_id','r_sample')):
                                self.status['last']=deepcopy(row)
                        for row in rejections:
                            row['rejection_id']=self.status['rejected']+1
                            if self.on_rejected and self.on_rejected(row) is False:
                                raise RuntimeError('未分类候选记录无法保存')
                            self.status['rejected']+=1
        except Exception as exc:
            with self.lock:
                self.status.update(state='error',detail='分类不可用：'+str(exc)+'；波形采集继续',last=None,error=str(exc),processing=False)
            self.ready.set()

    def snapshot(self):
        with self.lock:
            result=deepcopy(self.status);result['pending_blocks']=0 if self.queues_closed else self.queue.qsize()
            result['recent']=deepcopy(list(self.recent))
            now=time.monotonic()
            if result.get('error') or result.get('state')=='loading':
                result['last']=None
            elif self.last_received is not None and now-self.last_received>2:
                result.update(state='stale',detail='数据中断，暂停分类',last=None)
            elif result.get('last') and (self.last_prediction is None or now-self.last_prediction>3):
                result.update(state='stale',detail='分类结果已过期',last=None)
            return result

    def stop(self):
        self.cancel.set();self.ready.set();self.invalidate('采集已停止')

    def wait(self,seconds=5):
        if self.thread:self.thread.join(seconds)
        if self.process:self.process.join(seconds)
        stopped=(not self.thread or not self.thread.is_alive()) and (not self.process or not self.process.is_alive())
        if self.process and self.process.is_alive():
            self.process.terminate();self.process.join(2)
            with self.lock:self.status.update(state='error',error='分类进程停止超时',last=None)
        with self.lock:
            if not self.queues_closed:
                # Do not let a feeder thread wait forever for a stopped child.
                self.queue.cancel_join_thread();self.outputs.cancel_join_thread()
                self.queue.close();self.outputs.close();self.queues_closed=True
        return stopped
