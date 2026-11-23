const test=require('node:test'), assert=require('node:assert/strict');
const {createDesktopControls}=require('../web/desktop.js');

function harness(){
  const elements=new Map(), handlers={}, viewport={matches:true,addEventListener(_,fn){this.change=fn;}};
  let identity={epoch:1,selected:'one'}, modal=false;
  function element(id){
    if(!elements.has(id)) elements.set(id,{id,hidden:false,disabled:false,textContent:'',tagName:'DIV',
      children:[],attrs:{},listeners:{},focus(){this.focused=!modal || id!=='desktop-focus' || element('wave-workspace').parentNode===element('desktop-wave-dialog');},scrollIntoView(){this.scrolled=true;},
      setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.listeners[k]=fn;},
      append(child){if(child.parentNode) child.parentNode.children=child.parentNode.children.filter(x=>x!==child);this.children.push(child);child.parentNode=this;},
      insertBefore(child,next){if(child.parentNode) child.parentNode.children=child.parentNode.children.filter(x=>x!==child);let i=this.children.indexOf(next);this.children.splice(i<0?this.children.length:i,0,child);child.parentNode=this;},
      showModal(){modal=true;this.open=true;},close(){modal=false;this.open=false;this.listeners.close?.();}});
    return elements.get(id);
  }
  const wave=element('wave-workspace'),parent=element('content'),next=element('after-wave');
  parent.append(wave);parent.append(next);wave.nextSibling=next;
  const document={getElementById:element,addEventListener(k,fn){handlers[k]=fn;},querySelector(selector){return selector==='dialog[open]'&&modal?element('desktop-wave-dialog'):null;}};
  const controls=createDesktopControls({document,viewport,identity:()=>identity});
  return {controls,element,parent,wave,next,viewport,handlers,setIdentity(v){identity=v;},key(key,extra={}){let prevented=false;handlers.keydown({key,target:{tagName:'BODY'},preventDefault(){prevented=true;},...extra});return prevented;}};
}

test('wave focus uses original controls and restores their position on Escape',()=>{
  const h=harness();h.controls.setFocus(true);assert.equal(h.wave.parentNode,h.element('desktop-wave-dialog'));
  assert.equal(h.element('desktop-wave-dialog').open,true);assert.equal(h.element('desktop-focus').attrs['aria-pressed'],'true');
  h.key('Escape');assert.equal(h.wave.parentNode,h.parent);assert.deepEqual(h.parent.children,[h.wave,h.next]);
  assert.equal(h.element('desktop-wave-dialog').open,false);
  assert.equal(h.element('desktop-focus').focused,true);
});
test('hidden record or narrow viewport cannot open wave focus',()=>{
  const h=harness();h.element('content').hidden=true;h.controls.setFocus(true);assert.notEqual(h.element('desktop-wave-dialog').open,true);
  h.element('content').hidden=false;h.viewport.matches=false;h.controls.setFocus(true);assert.notEqual(h.element('desktop-wave-dialog').open,true);
});
test('changing account or record closes focused data before the new view',()=>{
  const h=harness();h.controls.setFocus(true);h.setIdentity({epoch:2,selected:'one'});h.controls.sync();assert.equal(h.element('desktop-wave-dialog').open,false);
  h.controls.setFocus(true);h.setIdentity({epoch:2,selected:'two'});h.controls.sync();assert.equal(h.wave.parentNode,h.parent);
});
test('resizing to mobile restores ordinary waveform and hides desktop controls',()=>{
  const h=harness();h.controls.setFocus(true);h.viewport.matches=false;h.viewport.change();
  assert.equal(h.wave.parentNode,h.parent);assert.equal(h.element('desktop-focus').hidden,true);
});
test('typing, modifiers, editable content and other dialogs keep keyboard input',()=>{
  const h=harness();
  for(const target of [{tagName:'INPUT'},{tagName:'TEXTAREA'},{tagName:'SELECT'},{tagName:'SPAN',isContentEditable:true}]) assert.equal(h.key('f',{target}),false);
  for(const modifier of ['ctrlKey','altKey','metaKey']) assert.equal(h.key('f',{[modifier]:true}),false);
  h.element('desktop-wave-dialog').showModal();assert.equal(h.key('/'),false);
});
test('search shortcut is available only inside a ready desktop workspace',()=>{
  const h=harness();assert.equal(h.key('/'),true);assert.equal(h.element('query').focused,true);
  h.element('workspace-shell').hidden=true;assert.equal(h.key('/'),false);
});
test('external dialog close restores waveform; focus navigation targets existing content',()=>{
  const h=harness();h.controls.setFocus(true);h.element('desktop-wave-dialog').close();assert.equal(h.wave.parentNode,h.parent);
  h.element('desktop-analysis').listeners.click();assert.equal(h.element('model-panel').scrolled,true);
});
