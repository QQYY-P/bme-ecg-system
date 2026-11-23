'use strict';
function clearAccount() {
  if(typeof resetDesktop==='function')resetDesktop();
  clearDevices();
  renderMeasurement(null);
  state.epoch++; state.listEpoch++; state.generation++; state.analysisGeneration++;
  state.ready=false; state.user=null; state.csrf=''; state.sessions=[]; state.selected=null;
  state.wave=null; state.analysis=null; state.analyzing=null; state.views.clear(); state.page=0;
  state.busy=false; state.total=0; state.filters={}; state.analysisKey=''; state.tableKey='';
  state.beatFilter='all';state.amplitude=1;
  $('sessions').replaceChildren(); $('beats-body').replaceChildren(); $('content').hidden=true;
  text('session-count','0'); text('records-page','1 / 1'); $('records-prev').disabled=true; $('records-next').disabled=true;
  $('model-result').hidden=true; $('user-list').replaceChildren(); $('unassigned-list').replaceChildren();
  for(const id of ['record-name','record-note','record-id','source','provenance-kind','samples','chunks','duration','valid',
    'device','capture-time','first-upload','received','source-status','provenance-note','clock-note','wave-origin',
    'model-name','model-message','beat-count','class-N','class-S','class-V','model-time','eligible-count',
    'skipped-count','quality-reasons','position-label','selected-beat','account-name','model-status','upload-status','beats-total','beats-page']) text(id,'');
  $('input-summary').hidden=true; $('beats-panel').hidden=true; $('marker-legend').hidden=true;
  $('skip-reasons').replaceChildren(); $('chart').width=$('chart').width;
  $('record-form').reset(); $('new-user-form').reset(); $('password-form').reset(); renderDevice(null); notice('');
  $('filters').reset(); for(const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  draw();
}
function showLogin(message='') {
  clearAccount(); $('workspace-shell').hidden=true; $('login-panel').hidden=false;
  $('password').value=''; text('login-error',message);
  $('login-submit').disabled=false;
}
function enterWorkspace(payload) {
  clearAccount(); state.user=payload?.user || null; state.csrf=payload?.csrf_token || ''; state.ready=true;
  $('login-panel').hidden=true; $('workspace-shell').hidden=false; $('password').value='';
  $('settings').hidden=state.auth; $('logout').hidden=!state.auth; $('admin').hidden=!state.user?.is_admin;
  $('change-password').hidden=!state.auth;
  $('manage-devices').hidden=!state.user?.is_admin;
  text('account-name',state.user ? state.user.username+' · 我的记录' : '本机工作台');
  $('login-submit').disabled=false;connectionStatus();
  refresh();
}
function renderDevice(d) {
  const connection={online:'终端在线',offline:'终端离线',disabled:'设备已停用',unknown:'尚未收到设备状态'};
  const modes={standby:'待机',acquiring:'采集中',saving:'保存中',error:'设备需要检查',other_user:'终端在线'};
  text('device-state',d?.connection==='online' ? modes[d.state] || '终端在线' : connection[d?.connection] || '设备状态暂不可用');
  text('device-signal',d?.connection==='online' && d.state!=='other_user'
    ? d.signal_text || (d.state==='standby'?'可在树莓派屏幕上手动开始采集':'等待信号状态')
    : '历史记录可继续查看；当前信号状态不显示');
  text('device-seen',d?.last_seen ? '最近心跳 '+date(d.last_seen*1000) : '未收到心跳不代表设备已断电');
}
async function jsonRequest(path,method,body) {
  const epoch=state.epoch;
  const response=await (await api(path,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(epoch!==state.epoch)throw new Error('登录状态已改变');
  return response;
}
$('login-form').onsubmit=async event=>{
  event.preventDefault(); const epoch=state.epoch; $('login-submit').disabled=true; text('login-error','');
  try {enterWorkspace(await jsonRequest('/api/auth/login','POST',{username:$('username').value,password:$('password').value}));}
  catch(e) {if(epoch===state.epoch)text('login-error',e.message);}
  finally {if(epoch===state.epoch)$('login-submit').disabled=false;}
};
$('logout').onclick=async()=>{
  try {await api('/api/auth/logout',{method:'POST'}); showLogin();}
  catch(e) {notice('退出未完成：'+e.message);}
};
function reloadList() {state.listEpoch++; state.busy=false; state.generation++; state.analysisGeneration++; refresh();}
$('filters').onsubmit=event=>{
  event.preventDefault(); const params={trashed:$('trash-filter').value};
  if($('query').value.trim()) params.q=$('query').value.trim();
  if($('filter-source').value) params.source=$('filter-source').value;
  if($('filter-device').value) params.device_id=$('filter-device').value;
  if($('date-from').value) params.started_from=new Date($('date-from').value+'T00:00:00').toISOString();
  if($('date-to').value) {const d=new Date($('date-to').value+'T00:00:00');d.setDate(d.getDate()+1);params.started_before=d.toISOString();}
  state.filters=params; state.page=0; reloadList();
};
$('reset-filters').onclick=()=>{$('filters').reset();state.filters={};state.page=0;reloadList();};
$('records-prev').onclick=()=>{state.page=Math.max(0,state.page-1);reloadList();};
$('records-next').onclick=()=>{state.page++;reloadList();};
let editSession=null;
$('edit-record').onclick=()=>{
  const s=state.wave?.session;if(!s || s.session_id!==state.selected)return;
  editSession=s.session_id;$('edit-name').value=s.name || '';$('edit-note').value=s.note || '';
  text('edit-error','');$('record-editor').showModal();
};
$('record-form').onsubmit=async event=>{
  event.preventDefault();const epoch=state.epoch;
  try {
    await jsonRequest('/api/sessions/'+encodeURIComponent(editSession)+'/details','PATCH',{name:$('edit-name').value,note:$('edit-note').value});
    if(epoch!==state.epoch)return;$('record-editor').close();reloadList();
  } catch(e) {if(epoch===state.epoch)text('edit-error',e.message);}
};
$('trash-record').onclick=async()=>{
  const s=state.wave?.session,epoch=state.epoch;if(!s || s.session_id!==state.selected)return;
  if(!s.trashed_at && !confirm('将此记录移入回收站？原始数据保留，可随时恢复。'))return;
  try {await api('/api/sessions/'+encodeURIComponent(s.session_id)+(s.trashed_at?'/restore':'/trash'),{method:'POST'});if(epoch===state.epoch)reloadList();}
  catch(e) {if(epoch===state.epoch)notice(e.message);}
};
async function loadAdmin() {
  const epoch=state.epoch;
  const [users,records]=await Promise.all([api('/api/admin/users').then(r=>r.json()),api('/api/admin/unassigned-sessions').then(r=>r.json())]);
  if(epoch!==state.epoch)return;
  const userList=$('user-list'); userList.replaceChildren();
  for(const u of users.users) {
    const row=document.createElement('div'); row.className='user-row';
    const label=document.createElement('span'); label.textContent=u.username+' · '+(u.is_admin?'管理员':u.active?'可登录':'已停用');row.append(label);
    if(!u.is_admin) {
      const reset=document.createElement('button');reset.className='button secondary';reset.textContent='重置密码';
      reset.onclick=()=>openPasswordEditor(u);
      const toggle=document.createElement('button');toggle.className='button secondary';toggle.textContent=u.active?'停用':'恢复';
      toggle.onclick=async()=>{
        if(!confirm((u.active?'停用 ':'恢复 ')+u.username+'？已有记录及归属保留。'))return;
        toggle.disabled=true;
        try{await jsonRequest('/api/admin/users/'+encodeURIComponent(u.id),'PATCH',{active:!u.active});if(epoch===state.epoch)await loadAdmin();}
        catch(e){if(epoch===state.epoch){text('admin-error',e.message);toggle.disabled=false;}}
      };
      row.append(reset,toggle);
    }
    userList.append(row);
  }
  const container=$('unassigned-list');container.replaceChildren();
  if(!records.sessions.length) {container.textContent='没有待分配记录';return;}
  for(const s of records.sessions) {
    const row=document.createElement('div');row.className='assign-row';
    const label=document.createElement('p');label.textContent=(s.name || s.session_id)+' · '+date(s.started_utc);
    const select=document.createElement('select');select.setAttribute('aria-label','选择记录所属账号');
    const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='选择所属账号';select.append(placeholder);
    for(const u of users.users.filter(u=>u.active)) {const option=document.createElement('option');option.value=u.id;option.textContent=u.username;select.append(option);}
    const button=document.createElement('button');button.className='button secondary';button.textContent='确认分配';
    button.onclick=async()=>{
      if(!select.value)return;
      if(!confirm('将该记录归属给 '+select.selectedOptions[0].textContent+'？分配后无法在此更改。'))return;
      button.disabled=true;
      try {await jsonRequest('/api/admin/sessions/'+encodeURIComponent(s.session_id)+'/assign','POST',{owner_id:select.value});if(epoch===state.epoch){await loadAdmin();reloadList();}}
      catch(e){if(epoch===state.epoch)text('admin-error',e.message);button.disabled=false;}
    };
    row.append(label,select,button);container.append(row);
  }
}
$('admin').onclick=async()=>{$('admin-dialog').showModal();text('admin-error','');try{await loadAdmin();}catch(e){text('admin-error',e.message);}};
$('new-user-form').onsubmit=async event=>{
  event.preventDefault();const epoch=state.epoch;
  try {
    await jsonRequest('/api/admin/users','POST',{username:$('new-username').value,password:$('new-password').value});
    if(epoch!==state.epoch)return;$('new-user-form').reset();text('admin-error','账号已创建');await loadAdmin();
  } catch(e){if(epoch===state.epoch)text('admin-error',e.message);}
};
let initializeGeneration=0;
async function initialize() {
  const generation=++initializeGeneration,epoch=state.epoch;
  $('login-submit').disabled=true;
  try {
    const config=await (await api('/api/auth/config')).json();if(generation!==initializeGeneration||epoch!==state.epoch)return;state.auth=config.enabled;
    if(state.auth) {
      state.token='';sessionStorage.removeItem('ecg-token');
      try {const payload=await (await api('/api/auth/me')).json();if(generation===initializeGeneration&&epoch===state.epoch)enterWorkspace(payload);}
      catch(e){if(generation!==initializeGeneration||epoch!==state.epoch)return;if(e.status===401){showLogin();connectionStatus();}else throw e;}
    } else enterWorkspace();
  } catch(e) {
    if(generation!==initializeGeneration||epoch!==state.epoch)return;
    showLogin('服务暂时无法连接，请点击上方“重新连接”。');$('login-submit').disabled=true;
    connectionStatus(e.connection || connectionProblem(e));
  }
}
let passwordTarget=null;
function openPasswordEditor(user=null) {
  passwordTarget=user;
  $('password-form').reset();text('password-error','');
  text('password-title',user?'重置 '+user.username+' 的密码':'修改我的密码');
  $('current-password-field').hidden=!!user;$('current-password').required=!user;
  $('password-dialog').showModal();
}
$('change-password').onclick=()=>openPasswordEditor();
$('password-form').onsubmit=async event=>{
  event.preventDefault();const epoch=state.epoch, target=passwordTarget;
  if($('replacement-password').value!==$('confirm-password').value){text('password-error','两次新密码不一致');return;}
  const payload={new_password:$('replacement-password').value};
  if(!target)payload.current_password=$('current-password').value;
  $('password-submit').disabled=true;
  try {
    await jsonRequest(target?'/api/admin/users/'+encodeURIComponent(target.id)+'/password':'/api/auth/password','POST',payload);
    if(epoch!==state.epoch)return;
    $('password-dialog').close();$('password-form').reset();
    if(target)text('admin-error','已重置 '+target.username+' 的密码，其原有登录已撤销。');
    else showLogin('密码已修改，请用新密码重新登录。');
  } catch(e) {if(epoch===state.epoch)text('password-error',e.message);}
  finally {$('password-submit').disabled=false;}
};
initialize();setInterval(()=>{if(!document.hidden)refresh();},3000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
