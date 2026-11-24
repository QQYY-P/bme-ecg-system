'use strict';
function clearDevices(){
 state.devices=[];$('filter-device').replaceChildren();$('filter-device').value='';
}
function renderDevices(payload){
 const items=payload?.devices || [],selected=state.filters.device_id || '';
 state.devices=items;
 if(typeof UserExperience!=='undefined')UserExperience.renderDevices(payload);
 const selector=$('filter-device'),pending=selector.value || '';selector.replaceChildren();
 const option=(value,label)=>{const node=document.createElement('option');node.value=value;node.textContent=label;selector.append(node)};
 option('','全部设备');
 for(const item of items)option(item.device_id,(item.name || item.device_id)+(item.active===false?'（已停用）':''));
 if(pending && !items.some(d=>d.device_id===pending))option(pending,pending+'（当前状态不可用）');
 selector.value=pending;
 if(!payload){renderDevice(null);return;}
 if(selected){renderDevice(items.find(d=>d.device_id===selected) || null);return;}
 if(items.length===1){renderDevice(items[0]);return;}
 text('device-state',items.length?`在线 ${items.filter(d=>d.connection==='online').length} / ${items.length} 台设备`:'暂无可查看的设备');
 text('device-signal',items.length?'在下方选择设备并筛选，可查看该设备的当前状态。':'请联系管理员配置设备授权；已有历史记录仍可查看。');
 text('device-seen','设备在线表示终端程序正在上报，不代表采集质量合格。');
}
