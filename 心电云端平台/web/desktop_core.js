(function(root){
  'use strict';
  function filteredBeats(beats,filter){
    if(filter==='SV')return beats.filter(b=>b.label==='S'||b.label==='V');
    if(filter==='review')return beats.filter(b=>b.requires_review);
    if(['N','S','V'].includes(filter))return beats.filter(b=>b.label===filter);
    return beats;
  }
  function windowEnd(current,delta,first,last,width){return Math.min(last,Math.max(Math.min(last,first+width-1),current+delta));}
  const api={filteredBeats,windowEnd};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ECGDesktop=api;
})(typeof globalThis!=='undefined'?globalThis:this);
