const test=require('node:test'),assert=require('node:assert/strict');
const {describeFailure}=require('../web/connection.js');
const {filteredBeats,windowEnd}=require('../web/desktop_core.js');
test('offline, timeout, server failure and expired login have distinct guidance',()=>{
  assert.equal(describeFailure({name:'TypeError'},0,false).kind,'offline');
  assert.equal(describeFailure({name:'TimeoutError'},0,true).kind,'timeout');
  assert.equal(describeFailure(null,503,true).kind,'server');
  assert.equal(describeFailure(null,401,true).kind,'expired');
  assert.equal(describeFailure({name:'TypeError'},0,true).kind,'unreachable');
});
test('heartbeat filters preserve source results and distinguish S/V from N',()=>{
  const beats=[{label:'N'},{label:'S'},{label:'V'},{label:'N',requires_review:true}];
  assert.deepEqual(filteredBeats(beats,'SV'),[beats[1],beats[2]]);
  assert.deepEqual(filteredBeats(beats,'review'),[beats[3]]);
  assert.equal(filteredBeats(beats,'all').length,4);assert.equal(beats.length,4);
});
test('history navigation clamps to available data even for a short record',()=>{
  assert.equal(windowEnd(3600,-1800,0,7200,1800),1800);
  assert.equal(windowEnd(3600,-7200,0,7200,1800),1799);
  assert.equal(windowEnd(3600,7200,0,7200,1800),7200);
  assert.equal(windowEnd(100,1800,0,100,1800),100);
});
