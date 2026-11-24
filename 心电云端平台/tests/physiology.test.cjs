const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../web/physiology.js');
function harness(){
  const elements=new Map(),state={selected:'a',epoch:0,wave:{session:{session_id:'a',upload_status:'complete'}}};
  const element=id=>{if(!elements.has(id))elements.set(id,{textContent:'',hidden:false,disabled:false});return elements.get(id);};
  const h={state,element,request:async()=>({json:async()=>result()})};
  h.ui=create({element,context:()=>state,request:(...args)=>h.request(...args)});return h;
}
function result(overrides={}){return {session_id:'a',analysis_status:'complete',available:true,
  message:'QRS 已完成',summary:{qrs_count:10,quality_coverage:.7},
  rr:{mean_ms:1000,mean_rate_bpm:60,sdrr_ms:12,rmssd_ms:17},
  hrv:{message:'缺少正常窦性间期标记'},...overrides};}

test('shows RR metrics separately from unavailable HRV and preserves unknowns',async()=>{
  const h=harness();await h.ui.load();
  assert.equal(h.element('qrs-count').textContent,'10');assert.equal(h.element('rr-mean').textContent,'1000.0 ms');
  assert.equal(h.element('rr-heart-rate').textContent,'60.0 次/分');assert.equal(h.element('qrs-quality').textContent,'70.0%');
  assert.match(h.element('hrv-message').textContent,/缺少/);assert.equal(h.element('physiology-analyze').disabled,false);
  h.request=async()=>({json:async()=>result({analysis_status:'ineligible',summary:{qrs_count:0,quality_coverage:null},rr:{mean_ms:null}})});
  await h.ui.load();assert.equal(h.element('rr-mean').textContent,'—');assert.equal(h.element('qrs-quality').textContent,'—');
});
test('clears metrics while queued and reports durable task progress',async()=>{
  const h=harness();await h.ui.load();
  h.request=async()=>({json:async()=>result({analysis_status:'running',task:{stage:'分析中',processed:36,total:720}})});
  await h.ui.load();assert.equal(h.element('rr-mean').textContent,'—');assert.equal(h.element('physiology-metrics').hidden,true);
  assert.match(h.element('physiology-message').textContent,/36 \/ 720/);assert.equal(h.element('physiology-analyze').disabled,true);
});
test('late response after switching account or record cannot restore old metrics',async()=>{
  for(const change of [h=>h.state.epoch++,h=>h.state.selected='b']){
    const h=harness();let finish;h.request=async()=>({json:()=>new Promise(r=>finish=r)});
    const pending=h.ui.load();await Promise.resolve();change(h);h.ui.clear();finish(result());await pending;
    assert.equal(h.element('qrs-count').textContent,'—');assert.equal(h.element('physiology-message').textContent,'');
  }
});
test('older server and network failure do not manufacture result or enabled analysis',async()=>{
  const h=harness();h.request=async()=>{throw Object.assign(new Error('Not Found'),{status:404});};
  await h.ui.load();assert.match(h.element('physiology-message').textContent,/服务尚未提供/);
  assert.equal(h.element('physiology-analyze').disabled,true);
  h.request=async()=>{throw new Error('连接中断');};await h.ui.load();
  assert.match(h.element('physiology-message').textContent,/连接中断/);assert.equal(h.element('qrs-count').textContent,'—');
});
test('incomplete record cannot enqueue and complete record submits only once',async()=>{
  const h=harness();await h.ui.load();let calls=0,finish;
  h.request=(url,options)=>{if(options?.method==='POST'){calls++;return new Promise(r=>finish=r);}return Promise.resolve({json:async()=>result()});};
  h.state.wave.session.upload_status='receiving';await h.ui.submit();assert.equal(calls,0);
  h.state.wave.session.upload_status='complete';const pending=h.ui.submit();await h.ui.submit();assert.equal(calls,1);
  h.state.epoch++;h.ui.clear();finish({});await pending;assert.equal(h.element('qrs-count').textContent,'—');
});
