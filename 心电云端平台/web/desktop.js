'use strict';

// Presentation only: use existing DOM and handlers, never duplicate account data.
function createDesktopControls({document, viewport, identity}) {
  const get=id=>document.getElementById(id);
  const wave=get('wave-workspace'), dialog=get('desktop-wave-dialog'), button=get('desktop-focus');
  const parent=wave.parentNode, next=wave.nextSibling;
  let focused=false, previous=identity();
  const ready=()=>viewport.matches && !get('workspace-shell').hidden && !get('content').hidden;
  function restore() {
    if(!focused) return;
    focused=false;
    parent.insertBefore(wave,next);
    button.textContent='专注查看 · F';button.setAttribute('aria-pressed','false');
    if(ready()) button.focus();
  }
  function setFocus(enabled) {
    if(!enabled) {if(dialog.open) dialog.close();restore();return;}
    if(focused || !ready() || document.querySelector('dialog[open]')) return;
    focused=true;dialog.append(wave);
    button.textContent='退出专注 · Esc';button.setAttribute('aria-pressed','true');
    dialog.showModal();button.focus();
  }
  function sync() {
    const current=identity();
    if(!ready() || previous.epoch!==current.epoch || previous.selected!==current.selected) setFocus(false);
    previous=current;
    button.hidden=!viewport.matches;
    get('desktop-record-nav').hidden=!viewport.matches;
  }
  button.addEventListener('click',()=>setFocus(!focused));
  dialog.addEventListener('close',restore);
  dialog.addEventListener('cancel',event=>{event.preventDefault();setFocus(false);});
  for(const [id,target] of [['desktop-summary','desktop-overview'],['desktop-wave','wave-workspace'],['desktop-analysis','model-panel']]) {
    get(id).addEventListener('click',()=>{if(ready()) get(target).scrollIntoView({block:'start',behavior:'auto'});});
  }
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape' && focused) {event.preventDefault();setFocus(false);return;}
    if(!ready() || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || event.repeat) return;
    const target=event.target;
    if(target?.isContentEditable || ['INPUT','TEXTAREA','SELECT','BUTTON','A'].includes(target?.tagName)) return;
    if(document.querySelector('dialog[open]')) return;
    if(event.key==='/') {event.preventDefault();get('query').focus();}
    else if(event.key==='f') {event.preventDefault();setFocus(true);}
  });
  viewport.addEventListener('change',sync);
  sync();
  return {setFocus,sync};
}

if(typeof module!=='undefined' && module.exports) module.exports={createDesktopControls};
else {
  const desktopControls=createDesktopControls({document,viewport:window.matchMedia('(min-width: 1024px)'),
    identity:()=>({epoch:state.epoch,selected:state.selected})});
  const observer=new MutationObserver(()=>desktopControls.sync());
  for(const id of ['workspace-shell','content']) observer.observe(document.getElementById(id),{attributes:true,attributeFilter:['hidden']});
  observer.observe(document.getElementById('record-id'),{childList:true});
}
