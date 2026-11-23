const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness(){
  const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{textContent:'',hidden:false});return elements.get(id);};
  const context=vm.createContext({$,text:(id,value)=>$(id).textContent=value});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/measurement.js'),'utf8'),context);
  return {$,render:context.renderMeasurement};
}
const session=()=>({source:'serial',source_status:'complete',upload_status:'complete',sample_count:360,measurement_summary:{
  requested_duration_s:180,end_reason:'stopped',analysis_stats:{analyzed_real_samples:360,usable_real_samples:360,hr:{mean:72,min:70,max:74}},classification_counts:{N:1,S:0,V:0}}});
test('mobile summary distinguishes planned time, sampled time and terminal heart rate',()=>{
  const h=harness();h.render(session());
  assert.match(h.$('measurement-plan').textContent,/180/);assert.match(h.$('measurement-heart').textContent,/72.0/);
  assert.match(h.$('measurement-state').textContent,/手动/);assert.match(h.$('measurement-class').textContent,/终端/);
});
test('no data and legacy summaries do not imply normal measurement',()=>{
  const h=harness();const s=session();s.sample_count=0;s.measurement_summary.analysis_stats.hr=null;h.render(s);
  assert.match(h.$('measurement-state').textContent,/没有收到/);assert.match(h.$('measurement-heart').textContent,/暂无/);
  delete s.measurement_summary;s.sample_count=360;h.render(s);assert.match(h.$('measurement-state').textContent,/未提供/);
  assert.doesNotMatch(h.$('measurement-heart').textContent,/72/);
});
test('failed saving and simulated records cannot look successful',()=>{
  const h=harness();const s=session();s.source_status='failed';h.render(s);
  assert.match(h.$('measurement-state').textContent,/异常/);
  s.source='demo';h.render(s);assert.match(h.$('measurement-heart').textContent,/模拟/);
});
test('clearing the account removes all summary text',()=>{
  const h=harness();h.render(session());h.render(null);
  assert.equal(h.$('measurement-panel').hidden,true);
  for(const id of ['measurement-state','measurement-plan','measurement-heart','measurement-quality','measurement-class','measurement-next'])assert.equal(h.$(id).textContent,'');
});
