const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function harness({ios=false,standalone=false,worker=true}={}){
 const elements=new Map(),events={},swEvents={},mediaEvents={};let reloads=0;
 const el=id=>{if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,textContent:'',open:false,
  showModal(){this.open=true;},close(){this.open=false;},addEventListener(n,f){this[n]=f;}});return elements.get(id);};
 const registration={waiting:null,addEventListener(){},update:async()=>{}};
 const ctx=vm.createContext({document:{getElementById:el},navigator:{userAgent:ios?'iPhone Safari':'Android Chrome',
  onLine:true,standalone,serviceWorker:worker?{controller:{},register:async()=>registration,
   addEventListener:(n,f)=>swEvents[n]=f}:undefined},
  location:{reload(){reloads++;}},window:{isSecureContext:true,addEventListener:(n,f)=>events[n]=f,
   matchMedia:()=>({matches:standalone,addEventListener:(n,f)=>mediaEvents[n]=f})},console});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/pwa.js'),'utf8'),ctx);
 return {el,events,ctx,registration,swEvents,get reloads(){return reloads;}};
}
test('iPhone help explains manual home-screen installation',()=>{
 const h=harness({ios:true});h.el('pwa-install').onclick();
 assert.equal(h.el('pwa-help').open,true);assert.match(h.el('pwa-instructions').textContent,/Safari/);
 assert.match(h.el('pwa-instructions').textContent,/主屏幕/);assert.equal(h.el('pwa-confirm').hidden,true);
});
test('standalone mode hides repeated installation promotion',()=>{
 const h=harness({standalone:true});assert.equal(h.el('pwa-install').hidden,true);
});
test('native prompt occurs only on explicit install click and cancel permits future visits',async()=>{
 const h=harness();let called=0;h.events.beforeinstallprompt({preventDefault(){},prompt:async()=>{called++;},userChoice:Promise.resolve({outcome:'dismissed'})});
 assert.equal(called,0);h.el('pwa-install').onclick();await h.el('pwa-confirm').onclick();
 assert.equal(called,1);assert.equal(h.el('pwa-confirm').disabled,false);
 assert.match(h.el('pwa-feedback').textContent,/取消/);
});
test('install failure leaves normal web use available',async()=>{
 const h=harness();h.events.beforeinstallprompt({preventDefault(){},prompt:async()=>{throw Error('blocked');}});
 await h.el('pwa-confirm').onclick();assert.match(h.el('pwa-feedback').textContent,/浏览器/);
 assert.equal(h.el('pwa-confirm').disabled,false);
});
test('offline indication does not claim server is reachable when network returns',()=>{
 const h=harness();h.ctx.navigator.onLine=false;h.events.offline();assert.equal(h.el('pwa-network').hidden,false);
 assert.match(h.el('pwa-network').textContent,/此前/);
 h.ctx.navigator.onLine=true;h.events.online();assert.match(h.el('pwa-network').textContent,/刷新/);
});
test('unrequested worker activation never reloads an open form',()=>{
 const h=harness();h.swEvents.controllerchange();assert.equal(h.reloads,0);
});
test('waiting update activates and refreshes only after a deliberate click',async()=>{
 const h=harness();const messages=[];
 h.registration.waiting={postMessage:m=>messages.push(m)};
 await Promise.resolve();await Promise.resolve();
 assert.equal(h.el('pwa-update').hidden,false);assert.equal(h.reloads,0);
 h.el('pwa-update-button').onclick();assert.equal(messages[0].type,'ECG_ACTIVATE_UPDATE');
 assert.equal(h.reloads,0);h.swEvents.controllerchange();assert.equal(h.reloads,1);
});
test('unsupported service worker does not break the installation help',()=>{
 const h=harness({worker:false});h.el('pwa-install').onclick();assert.equal(h.el('pwa-help').open,true);
});
