const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness(){
 const elements=new Map();const make=()=>({textContent:'',value:'',children:[],append(...v){this.children.push(...v)},replaceChildren(){this.children=[]}});
 const $=id=>{if(!elements.has(id))elements.set(id,make());return elements.get(id)};
 const context=vm.createContext({$,text:(id,value)=>$(id).textContent=value,document:{createElement:make},state:{filters:{},devices:[]},renderDevice(d){context.last=d}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/devices.js'),'utf8'),context);
 return {context,$};
}
test('selected device remains selected when fresh statuses arrive',()=>{
 const h=harness();h.context.state.filters.device_id='pi-b';h.$('filter-device').value='pi-b';h.context.renderDevices({devices:[{device_id:'pi-a',name:'A',connection:'online'},{device_id:'pi-b',name:'B',connection:'offline'}]});
 assert.equal(h.$('filter-device').value,'pi-b');assert.equal(h.context.last.device_id,'pi-b');
});
test('multiple devices show overview instead of one arbitrary device signal',()=>{
 const h=harness();h.context.renderDevices({devices:[{device_id:'a',connection:'online'},{device_id:'b',connection:'offline'}]});
 assert.match(h.$('device-state').textContent,/1.*2/);assert.match(h.$('device-signal').textContent,/选择设备/);
});
test('account cleanup removes cached devices and filter options',()=>{
 const h=harness();h.context.renderDevices({devices:[{device_id:'private',name:'Private'}]});h.context.clearDevices();
 assert.equal(h.context.state.devices.length,0);assert.equal(h.$('filter-device').children.length,0);
});
test('failed status request does not leave previous device signal visible',()=>{
 const h=harness();h.context.state.filters.device_id='pi-b';h.$('filter-device').value='pi-b';h.context.renderDevices({devices:[{device_id:'pi-b',connection:'online'}]});
 h.context.renderDevices(null);assert.equal(h.context.last,null);assert.equal(h.$('filter-device').value,'pi-b');
});

test('background status refresh preserves a device selection not yet submitted',()=>{
 const h=harness(),payload={devices:[{device_id:'pi-a',connection:'online'},{device_id:'pi-b',connection:'offline'}]};
 h.context.state.filters.device_id='pi-a';h.$('filter-device').value='pi-a';h.context.renderDevices(payload);
 h.$('filter-device').value='pi-b';h.context.renderDevices(payload);
 assert.equal(h.$('filter-device').value,'pi-b');assert.equal(h.context.last.device_id,'pi-a');
 h.$('filter-device').value='';h.context.renderDevices(payload);
 assert.equal(h.$('filter-device').value,'');assert.equal(h.context.last.device_id,'pi-a');
});
