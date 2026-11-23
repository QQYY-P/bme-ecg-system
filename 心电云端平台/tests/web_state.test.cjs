// Isolated DOM/state regression probes; no browser, records or network access.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness() {
  const elements=new Map(); let sequence=0;
  const document={getElementById:element,querySelectorAll:()=>[],addEventListener(){},
    createElement:tag=>element(tag+'-'+(++sequence)),activeElement:null,documentElement:{dataset:{}}};
  function element(id) {
    if(!elements.has(id)) elements.set(id,{textContent:'',hidden:false,value:'',width:100,dataset:{},attributes:{},children:[],
      classList:{add(){},remove(){},toggle(){}},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
      replaceChildren(){this.children=[];},reset(){},close(){},focus(){document.activeElement=this;},click(){return this.onclick?.();},
      getBoundingClientRect(){return {width:0}},append(...children){this.children.push(...children);}});
    return elements.get(id);
  }
  const media={narrow:{matches:true,addEventListener(n,fn){this.changed=fn;}},standalone:{matches:false,addEventListener(){}}};
  const window={scrollY:0,scrollTo({top}){this.scrollY=top;},navigator:{standalone:false},addEventListener(){},
    matchMedia:q=>q.includes('max-width')?media.narrow:media.standalone};
  const context=vm.createContext({document,window,requestAnimationFrame:fn=>fn(),location:{href:''},
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
  return {context,element,media,document,state:vm.runInContext('state',context),
    loadMobile(){vm.runInContext(fs.readFileSync(path.join(root,'mobile.js'),'utf8'),context);}};
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

test('invalid date range retains prior results and makes no request',()=>{
  const h=harness();let reloaded=0;h.context.reloadList=()=>reloaded++;
  h.state.filters={q:'previous'};h.state.page=2;
  h.element('date-from').value='2026-11-23';h.element('date-to').value='2026-11-01';
  h.element('filters').onsubmit({preventDefault(){}});
  assert.equal(reloaded,0);assert.equal(h.state.filters.q,'previous');assert.equal(h.state.page,2);
  assert.equal(h.element('filter-error').hidden,false);
});
test('accepted mobile filter collapses the form without clearing the selected condition',()=>{
  const h=harness();h.loadMobile();let reloads=0;h.context.reloadList=()=>reloads++;
  h.element('query').value='  resting  ';h.element('trash-filter').value='false';h.element('filter-panel').open=true;
  h.element('filters').onsubmit({preventDefault(){}});
  assert.equal(h.state.filters.q,'resting');assert.equal(h.element('filter-panel').open,false);
  assert.match(h.element('filter-summary').textContent,/1/);assert.equal(reloads,1);
});
test('polling mobile records does not fetch waves or navigate to results',async()=>{
  const h=harness();h.loadMobile();h.state.ready=true;let details=0;
  h.context.loadWave=h.context.loadAnalysis=async()=>details++;
  h.context.api=async url=>({json:async()=>url.startsWith('/api/sessions')?{total:1,sessions:[{session_id:'first'}]}:{devices:[]}});
  await h.context.refresh();
  assert.equal(h.state.selected,'first');assert.equal(details,0);
  assert.equal(h.element('workspace-shell').dataset.mobileView,'records');assert.equal(h.element('nav-results').disabled,false);
  h.element('sessions').children[0].onclick();
  assert.equal(h.element('workspace-shell').dataset.mobileView,'results');assert.equal(details,2);
});
test('account view skips detail polling; returning to results refreshes and preserves record selection',()=>{
  const h=harness();h.loadMobile();h.state.ready=true;h.state.selected='one';let details=0;
  h.context.loadWave=h.context.loadAnalysis=()=>details++;
  h.element('nav-account').onclick();assert.equal(h.context.MobileUI.needsDetail(),false);
  h.element('nav-results').onclick();assert.equal(h.state.selected,'one');assert.equal(details,2);
  assert.equal(h.element('nav-results').attributes['aria-current'],'page');
  assert.equal(h.document.activeElement,h.element('results-heading'));
});
test('empty selection cannot open results and disappearance returns mobile to records',()=>{
  const h=harness();h.loadMobile();h.element('nav-results').onclick();
  assert.equal(h.element('workspace-shell').dataset.mobileView,'records');
  h.state.selected='one';h.element('nav-results').onclick();h.context.selectSession(null);
  assert.equal(h.element('workspace-shell').dataset.mobileView,'records');assert.equal(h.element('nav-results').disabled,true);
});
test('account cleanup removes mobile errors and navigation from the previous account',()=>{
  const h=harness();h.loadMobile();h.element('nav-account').onclick();h.context.detailFeedback('private error',true);
  h.element('filter-error').textContent='old error';h.context.clearAccount();
  assert.equal(h.element('workspace-shell').dataset.mobileView,'records');
  assert.equal(h.element('detail-message').textContent,'');assert.equal(h.element('detail-feedback').hidden,true);
  assert.equal(h.element('filter-error').textContent,'');assert.equal(h.element('nav-results').disabled,true);
});
test('identical list updates retain the actual button and keyboard focus',()=>{
  const h=harness();h.state.sessions=[{session_id:'one',name:'A'}];h.state.total=1;
  h.context.renderList();const button=h.element('sessions').children[0];button.focus();
  h.context.renderList();assert.equal(h.element('sessions').children[0],button);assert.equal(h.document.activeElement,button);
  h.state.sessions[0].name='B';h.context.renderList();
  assert.notEqual(h.element('sessions').children[0],button);assert.equal(h.document.activeElement,h.element('sessions').children[0]);
});
test('failed wave provides retry and selecting another record resets the disabled retry button',async()=>{
  const h=harness();h.state.selected='one';h.context.api=async()=>{throw Error('unreachable');};
  await h.context.loadWave();assert.equal(h.element('detail-retry').hidden,false);assert.match(h.element('detail-message').textContent,/重试/);
  h.element('detail-retry').disabled=true;h.context.selectSession('two');assert.equal(h.element('detail-retry').disabled,false);
});
test('late wave error cannot replace a newer selection loading message',async()=>{
  const h=harness();let fail;h.state.selected='one';h.context.api=()=>new Promise((resolve,reject)=>fail=reject);
  const request=h.context.loadWave();h.context.selectSession('two');fail(Error('old error'));await request;
  assert.equal(h.element('detail-retry').hidden,true);assert.doesNotMatch(h.element('detail-message').textContent,/old error/);
});
test('desktop resize restores detail refresh and opens the filter form',()=>{
  const h=harness();h.loadMobile();h.state.ready=true;h.state.selected='one';let details=0;
  h.context.loadWave=h.context.loadAnalysis=()=>details++;
  h.media.narrow.matches=false;h.media.narrow.changed();
  assert.equal(h.context.MobileUI.needsDetail(),true);assert.equal(details,2);assert.equal(h.element('filter-panel').open,true);
});
test('Android export passes only record and user identity to the system save flow',async()=>{
  const h=harness();h.document.documentElement.dataset.nativeApp='android';h.state.selected='session_1';h.state.user={id:'a'.repeat(32)};
  let called=false;h.context.api=()=>{called=true;};await h.element('download').onclick();
  assert.equal(called,false);assert.equal(h.context.location.href,'ecg-export://download/session_1?user='+'a'.repeat(32));
});
