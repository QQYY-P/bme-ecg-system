// Isolated DOM/state regression probes; no browser, records or network access.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness() {
  const elements=new Map();
  function element(id) {
    if(!elements.has(id)) elements.set(id,{textContent:'',hidden:false,value:'10',width:100,
      classList:{add(){},remove(){},toggle(){}},setAttribute(){},replaceChildren(){},reset(){},close(){},
      getBoundingClientRect(){return {width:0}},append(){}});
    return elements.get(id);
  }
  const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
    sessionStorage:{getItem:()=>'',removeItem(){},setItem(){}},ResizeObserver:class{observe(){}},
    URLSearchParams,AbortSignal,console,fetch:async()=>({ok:false}),setTimeout,clearTimeout,setInterval(){}});
  const root=path.join(__dirname,'..','web');
  vm.runInContext(fs.readFileSync(path.join(root,'connection.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'desktop_core.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'devices.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'measurement.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'app.js'),'utf8'),context);
  const source=fs.readFileSync(path.join(root,'workspace.js'),'utf8');
  vm.runInContext(source.slice(0,source.indexOf('initialize();setInterval')),context);
  return {context,element,state:vm.runInContext('state',context)};
}
test('switching account clears text, device status, forms and pagination before fetch',()=>{
  const h=harness();h.state.user={id:'alice'};
  for(const id of ['record-name','record-note','record-id','model-message','device-signal','session-count']) h.element(id).textContent='Alice private';
  h.context.clearAccount();
  for(const id of ['record-name','record-note','record-id','model-message']) assert.equal(h.element(id).textContent,'');
  assert.equal(h.element('content').hidden,true);assert.equal(h.element('session-count').textContent,'0');
  assert.equal(h.element('records-page').textContent,'1 / 1');
  assert.ok(!h.element('device-signal').textContent.includes('Alice'));assert.equal(h.state.user,null);
});
test('late 401 body cannot log out a newer account',async()=>{
  const h=harness();h.state.auth=true;h.state.user={id:'alice'};
  let resolveBody,headersSeen;
  const body=new Promise(resolve=>resolveBody=resolve), seen=new Promise(resolve=>headersSeen=resolve);
  h.context.fetch=async()=>({ok:false,status:401,json:()=>{headersSeen();return body;}});
  const request=h.context.api('/api/sessions');await seen;
  h.state.epoch++;h.state.user={id:'bob'};resolveBody({detail:'expired'});
  await assert.rejects(request,/登录状态已改变/);assert.equal(h.state.user.id,'bob');
});
test('late filtered list cannot replace a newer filter result',async()=>{
  const h=harness();h.state.ready=true;let resolveList;
  h.context.api=async url=>({json:async()=>url.startsWith('/api/sessions')?new Promise(resolve=>resolveList=resolve):null});
  const pending=h.context.refresh();await Promise.resolve();await Promise.resolve();
  h.state.listEpoch++;h.state.sessions=[{session_id:'new-selection'}];resolveList({sessions:[{session_id:'old'}],total:1});
  await pending;assert.equal(h.state.sessions[0].session_id,'new-selection');
});
test('persistent task progress uses the server task field',()=>{
  const h=harness();h.state.selected='sid';
  h.context.renderAnalysis({session_id:'sid',analysis_status:'running',model_status:'ready',task:{stage:'计算中',processed:12,total:100}});
  assert.match(h.element('model-message').textContent,/12 \/ 100/);
  assert.equal(h.element('analyze').disabled,true);
});
test('another tab changing the account clears the previous workspace',async()=>{
  const h=harness();h.state.auth=true;h.state.user={id:'alice'};h.element('record-note').textContent='alice private';
  h.context.fetch=async()=>({ok:true,headers:{get:()=> 'bob'}});
  await assert.rejects(h.context.api('/api/sessions'),/登录账号已改变/);
  assert.equal(h.state.user,null);assert.equal(h.element('record-note').textContent,'');
  assert.equal(h.element('workspace-shell').hidden,true);
});

test('password confirmation mismatch never sends a request',async()=>{
  const h=harness();let called=false;
  h.context.jsonRequest=async()=>{called=true;};
  h.element('replacement-password').value='new-password-A';h.element('confirm-password').value='new-password-B';
  await h.element('password-form').onsubmit({preventDefault(){}});
  assert.equal(called,false);assert.match(h.element('password-error').textContent,/不一致/);
});
test('late password-change response does not log out a newer account',async()=>{
  const h=harness();h.state.user={id:'alice'};let finish;
  h.context.jsonRequest=()=>new Promise(resolve=>finish=resolve);
  h.element('replacement-password').value=h.element('confirm-password').value='new-password-A';
  const pending=h.element('password-form').onsubmit({preventDefault(){}});
  h.state.epoch++;h.state.user={id:'bob'};finish({ok:true});await pending;
  assert.equal(h.state.user.id,'bob');
});

test('initial connection failure can be retried without leaving login disabled',async()=>{
  const h=harness();h.context.fetch=async()=>{throw new TypeError('Failed to fetch');};
  await h.context.initialize();assert.equal(h.element('login-submit').disabled,true);
  h.context.fetch=async url=>({ok:true,json:async()=>url.endsWith('/config')?{enabled:true}:{user:{id:'alice',username:'alice'},csrf_token:'test'},headers:{get:()=>null}});
  h.context.refresh=async()=>{};
  await h.context.initialize();assert.equal(h.element('login-submit').disabled,false);
  assert.equal(h.state.user.id,'alice');assert.equal(h.element('workspace-shell').hidden,false);
});

test('a failed authentication probe is not presented as an ordinary signed-out session',async()=>{
  const h=harness();h.context.fetch=async url=>{
    if(url.endsWith('/config'))return {ok:true,json:async()=>({enabled:true})};
    throw new TypeError('Failed to fetch');
  };
  await h.context.initialize();assert.match(h.element('login-error').textContent,/连接|网络|服务/);
  assert.equal(h.element('login-submit').disabled,true);
});

test('late login failure cannot overwrite a newer account login panel',async()=>{
  const h=harness();let reject;
  h.context.jsonRequest=()=>new Promise((_,r)=>reject=r);
  const pending=h.element('login-form').onsubmit({preventDefault(){}});
  h.state.epoch++;h.element('login-error').textContent='new account message';
  reject(new Error('old failure'));await pending;
  assert.equal(h.element('login-error').textContent,'new account message');
});
