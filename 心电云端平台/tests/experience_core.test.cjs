const test=require('node:test'),assert=require('node:assert/strict');
const UX=require('../web/experience_core.js');
const record={session_id:'one',source:'serial',source_status:'complete',upload_status:'complete',sample_count:1080};
const result=status=>({session_id:'one',analysis_status:status});
test('an upload acknowledgment never implies intact acquisition or a successful analysis',()=>{
  const s=UX.describe({...record,source_status:'failed'},result('complete'),result('complete'));
  assert.match(s.title,/完整性/);assert.equal(s.tone,'attention');
  assert.match(UX.describe({...record,upload_status:'receiving'},result('complete'),null).title,/尚未上传完成/);
});
test('missing, empty and simulated records cannot imply a healthy measurement',()=>{
  assert.match(UX.describe({...record,sample_count:0}).title,/未收到/);
  assert.match(UX.describe({...record,source:'demo'}).title,/演示/);
  assert.equal(UX.describe(record).classification,'正在读取');
  assert.doesNotMatch(UX.describe(record).title,/分析已完成/);
});
test('overview distinguishes partial analysis, failure, unavailable status and ineligible data',()=>{
  assert.match(UX.describe(record,result('complete'),result('not_run')).title,/部分分析已完成/);
  assert.match(UX.describe(record,result('failed'),result('complete')).title,/失败/);
  assert.match(UX.describe(record,result('complete'),result('ineligible')).next,/不代表正常/);
  assert.match(UX.describe(record,result('unavailable'),result('complete')).title,/读取失败/);
  assert.match(UX.describe(record,result('queued'),result('not_run')).title,/正在处理/);
  assert.match(UX.describe(record,result('complete'),result('complete')).title,/两项云端分析已完成/);
});
test('a result for a different record is never summarized as this record',()=>{
  const s=UX.describe(record,{session_id:'other',analysis_status:'complete'},null);
  assert.equal(s.classification,'正在读取');assert.doesNotMatch(s.title,/分析已完成/);
});
test('quick ranges use local calendar days across month and year boundaries',()=>{
  assert.deepEqual(UX.dateRange(7,new Date(2026,0,3,0,15)),{from:'2025-12-28',to:'2026-01-03'});
  assert.deepEqual(UX.dateRange(1,new Date(2026,10,24,23,59)),{from:'2026-11-24',to:'2026-11-24'});
  assert.equal(UX.localDay('invalid'),'');assert.equal(UX.duration(1080),'3.0 秒');
});
test('report HTML escapes all user text and refuses network images',()=>{
  const html=UX.reportHTML({title:'<script>alert(1)</script>',rows:[['备注','<img src=x onerror=alert(1)>']],range:'0–5 秒',note:'A&B',image:'https://example.com/private.png'});
  assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<script>'));assert.ok(!html.includes('src="https://'));assert.ok(html.includes('A&amp;B'));
});
