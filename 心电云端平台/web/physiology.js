/* Independent QRS/RR view. No classification labels are treated as NN evidence. */
(function(root,factory){
  const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.ECGPhysiology=value;
})(globalThis,function(){
  'use strict';
  function create({element,context,request}){
    let generation=0,pending=false,current=null;
    const ids=['qrs-count','rr-mean','rr-heart-rate','qrs-quality','rr-sd','rr-rmssd','qrs-effective'];
    const text=(id,value)=>element(id).textContent=value;
    const number=(value,suffix='')=>Number.isFinite(value)?value.toFixed(1)+suffix:'—';
    function updateAvailability(){
      const state=context(),record=state.wave?.session;
      element('physiology-analyze').disabled=pending || !current?.available || !state.selected ||
        record?.session_id!==state.selected || record.upload_status!=='complete' || !!record.trashed_at ||
        ['queued','running'].includes(current.analysis_status);
    }
    function resetMetrics(){
      for(const id of ids)text(id,'—');
      element('physiology-metrics').hidden=true;text('hrv-message','');
      text('qrs-coverage-note','');text('qrs-reasons','');
    }
    function clear(){
      generation++;pending=false;current=null;resetMetrics();
      text('physiology-status','待分析');text('physiology-message','');text('physiology-version','');
      text('physiology-analyze','开始 QRS/RR 分析');updateAvailability();
    }
    function render(result){
      if(result.session_id!==context().selected)return;
      current=result;resetMetrics();
      const status=result.analysis_status;
      text('physiology-status',({complete:'已完成',ineligible:'条件不足',queued:'排队中',running:'分析中',
        failed:'未完成',not_run:'待分析',not_configured:'未启用'})[status] || '待确认');
      text('physiology-message',result.task && ['queued','running'].includes(status)
        ?result.task.stage+' · '+result.task.processed+' / '+result.task.total+' 点':result.message || '');
      if(['complete','ineligible'].includes(status)){
        const summary=result.summary || {},rr=result.rr || {};
        element('physiology-metrics').hidden=false;
        text('qrs-count',Number.isFinite(summary.qrs_count)?String(summary.qrs_count):'—');
        text('rr-mean',number(rr.mean_ms,' ms'));text('rr-heart-rate',number(rr.mean_rate_bpm,' 次/分'));
        text('qrs-quality',Number.isFinite(summary.quality_coverage)?number(summary.quality_coverage*100,'%'):'—');
        text('rr-sd',number(rr.sdrr_ms,' ms'));text('rr-rmssd',number(rr.rmssd_ms,' ms'));
        text('qrs-effective',Number.isFinite(summary.quality_allowed_samples)?number(summary.quality_allowed_samples/360,' 秒'):'—');
        const whole=value=>Number.isFinite(value)?String(value):'—';
        text('qrs-coverage-note','质量门控通过 '+whole(summary.quality_allowed_samples)+' / 共 '+whole(summary.sample_count)+' 点；对应时长不包含断流等待。');
        const labels={source_not_real:'非实采或来源未知',board_simulated:'板端模拟',mode_unknown:'采集模式未知',invalid_adc:'无效或饱和 ADC'};
        const reasons=Object.entries(summary.skipped_reasons || {}).filter(([,value])=>value>0)
          .map(([key,value])=>(labels[key] || '其他跳过原因')+' '+value+' 点');
        if(Number.isFinite(summary.eligible_samples) && Number.isFinite(summary.quality_allowed_samples)
          && summary.eligible_samples>summary.quality_allowed_samples)
          reasons.push('连续长度不足或质量未通过 '+(summary.eligible_samples-summary.quality_allowed_samples)+' 点');
        if(!summary.sample_count)reasons.push('尚无可分析的采样点');
        text('qrs-reasons',reasons.length?'未参与分析：'+reasons.join('；')+'。':'');
        text('hrv-message','HRV 暂不可用：'+(result.hrv?.message || '尚无可信的正常窦性间期标记。'));
      }
      text('physiology-version',result.algorithm_version?'分析版本：'+result.algorithm_version:'');
      text('physiology-analyze',status==='failed'?'重试 QRS/RR 分析':'开始 QRS/RR 分析');
      updateAvailability();
    }
    async function load(){
      const {selected:sid,epoch}=context();if(!sid || pending)return;
      const ticket=++generation;
      const valid=()=>ticket===generation && sid===context().selected && epoch===context().epoch;
      try{
        const result=await (await request('/api/sessions/'+encodeURIComponent(sid)+'/physiology')).json();
        if(valid())render(result);
      }catch(error){
        if(!valid())return;
        current=null;resetMetrics();text('physiology-status','暂不可用');text('physiology-version','');
        text('physiology-message',error.status===404?'当前服务尚未提供 QRS/RR 分析，请等待服务更新。':'QRS/RR 状态读取失败：'+error.message);
        updateAvailability();
      }
    }
    async function submit(){
      updateAvailability();if(element('physiology-analyze').disabled)return;
      const {selected:sid,epoch}=context(),ticket=++generation;
      const valid=()=>ticket===generation && sid===context().selected && epoch===context().epoch;
      pending=true;updateAvailability();text('physiology-message','正在提交后台分析…');
      try{
        await request('/api/sessions/'+encodeURIComponent(sid)+'/physiology-jobs',{method:'POST'});
      }catch(error){
        if(valid())text('physiology-message','分析请求未完成：'+error.message);
        return;
      }finally{
        if(valid()){pending=false;updateAvailability();}
      }
      if(valid())await load();
    }
    element('physiology-analyze').onclick=submit;
    clear();return {clear,load,submit,updateAvailability};
  }
  return {create};
});
