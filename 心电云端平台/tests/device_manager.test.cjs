const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness(){
 const elements=new Map(),make=()=>({textContent:'',value:'',hidden:false,disabled:false,children:[],listeners:{},append(...v){this.children.push(...v)},replaceChildren(){this.children=[]},reset(){},showModal(){this.open=true},close(){this.open=false;this.listeners.close?.()},addEventListener(k,fn){this.listeners[k]=fn}});
 const el=id=>{if(!elements.has(id))elements.set(id,make());return elements.get(id)};
 const ctx=vm.createContext({document:{getElementById:el,createElement:make,querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){}},setInterval(){},AbortSignal,URL,Blob,setTimeout,console,fetch:async()=>{},confirm:()=>true});
 let source=fs.readFileSync(path.join(__dirname,'../web/device-manager.js'),'utf8');source=source.replace(/initializeManager\(\);\s*$/,'');vm.runInContext(source,ctx);
 return {ctx,el,state:vm.runInContext('manager',ctx)};
}
test('closing credential dialog erases the secret from state and text',()=>{
 const h=harness();h.ctx.showCredentials({device_id:'pi',token:'temporary-test-secret'});h.el('device-credentials').close();
 assert.equal(h.state.credentials,null);assert.equal(h.el('credential-value').value,'');
});
test('different signed-in user clears the editor and credentials before response data',async()=>{
 const h=harness();h.state.user={id:'admin-a'};h.ctx.showCredentials({token:'old-private'});
 h.ctx.fetch=async()=>({headers:{get:()=> 'admin-b'},ok:true,json:async()=>({devices:[]})});
 await assert.rejects(h.ctx.managerRequest('/api/admin/devices'),/账号/);
 assert.equal(h.state.credentials,null);assert.equal(h.el('manager-content').hidden,true);
});
test('late body from old account cannot repopulate new account',async()=>{
 const h=harness();let resolve,seen;const wait=new Promise(r=>seen=r);
 h.ctx.fetch=async()=>({headers:{get:()=>null},ok:true,status:200,json:()=>{seen();return new Promise(r=>resolve=r)}});
 const request=h.ctx.managerRequest('/api/admin/devices');await wait;h.state.epoch++;resolve({devices:[{name:'private'}]});
 await assert.rejects(request,/登录状态已改变/);
});
test('initial permission failure replaces the loading message with a useful error',async()=>{
 const h=harness();h.el('manager-notice').textContent='正在检查登录…';
 h.ctx.fetch=async()=>({headers:{get:()=>null},ok:false,status:403,json:async()=>({detail:'需要用户登录'})});
 await h.ctx.initializeManager();assert.doesNotMatch(h.el('manager-notice').textContent,/正在检查/);
});

test('session recheck erases credentials when another page has signed out',async()=>{
 const h=harness();h.state.user={id:'admin-a',is_admin:true};h.ctx.showCredentials({token:'old-private'});
 h.ctx.fetch=async()=>({headers:{get:()=>null},ok:false,status:401,json:async()=>({detail:'需要登录'})});
 assert.equal(await h.ctx.recheckManagerSession(),false);
 assert.equal(h.state.credentials,null);assert.equal(h.el('device-credentials').open,false);
});

test('download rechecks the session before exposing a saved credential',async()=>{
 const h=harness();h.state.user={id:'admin-a',is_admin:true};h.ctx.showCredentials({token:'old-private'});
 h.ctx.fetch=async()=>({headers:{get:()=> 'admin-b'},ok:true,status:200,json:async()=>({user:{id:'admin-b',is_admin:true}})});
 await h.el('download-credentials').onclick();
 assert.equal(h.state.credentials,null);assert.equal(h.el('manager-content').hidden,true);
});

test('a rejected write clears credentials even without an identity header',async()=>{
 const h=harness();h.state.user={id:'admin-a',is_admin:true};h.ctx.showCredentials({token:'old-private'});
 h.ctx.fetch=async()=>({headers:{get:()=>null},ok:false,status:403,json:async()=>({detail:'登录校验已失效'})});
 await assert.rejects(h.ctx.managerRequest('/api/admin/devices','POST',{}));
 assert.equal(h.state.credentials,null);assert.equal(h.el('device-credentials').open,false);
});

test('a late credential response cannot reopen a dialog while the page is hidden',()=>{
 const h=harness();h.ctx.document.hidden=true;h.ctx.showCredentials({token:'late-private'});
 assert.equal(h.state.credentials,null);assert.notEqual(h.el('device-credentials').open,true);
 assert.match(h.el('manager-notice').textContent,/重新/);
});

test('returning to the page does not restore credentials requested before hiding',()=>{
 const h=harness(),version=h.state.credentialEpoch;
 h.ctx.hideManagerCredentials();h.ctx.document.hidden=false;h.ctx.showCredentials({token:'late-private'},version);
 assert.equal(h.state.credentials,null);assert.notEqual(h.el('device-credentials').open,true);
});
