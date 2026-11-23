'use strict';
function renderMeasurement(session) {
  const ids=['measurement-state','measurement-plan','measurement-heart','measurement-quality','measurement-class','measurement-next'];
  for(const id of ids) text(id,'');
  $('measurement-panel').hidden=!session;
  if(!session) return;
  const s=session, m=s.measurement_summary, stats=m?.analysis_stats;
  const real=s.source==='serial', hr=real && s.sample_count>0 ? stats?.hr : null;
  let status='终端未提供测量总结（旧记录或尚未上传总结）';
  if(s.upload_status!=='complete') status='记录仍在上传，完整总结待上传完成后确认';
  else if(s.source_status!=='complete') status='本次采集或保存异常，请先核对记录完整性';
  else if(!s.sample_count) status='本次没有收到采样数据，请检查连接后重测';
  else if(m) status=m.end_reason==='duration_complete'?'定时测量已结束，记录已保存并上传':
      m.end_reason==='stopped'?'已手动结束，记录已保存并上传':'记录已保存并上传';
  text('measurement-state',status);
  text('measurement-plan',m?.requested_duration_s?`计划 ${m.requested_duration_s} 秒 · 已收采样 ${(s.sample_count/360).toFixed(1)} 秒`:
      `手动结束或未提供计划 · 已收采样 ${(s.sample_count/360).toFixed(1)} 秒`);
  text('measurement-heart',hr?`均值 ${hr.mean.toFixed(1)} · 最低 ${hr.min.toFixed(1)} · 最高 ${hr.max.toFixed(1)} 次/分`:
      real?'暂无有效实采心率统计':'模拟或回放记录，不提供本次人体心率');
  text('measurement-quality',stats?`通过质量门控 ${(stats.usable_real_samples/360).toFixed(1)} 秒 / 已分析实采 ${(stats.analyzed_real_samples/360).toFixed(1)} 秒`:
      '暂无终端质量窗口统计');
  const c=m?.classification_counts;
  text('measurement-class',c && c.N+c.S+c.V?`终端已保存分类：N ${c.N} / S ${c.S} / V ${c.V}`:'终端尚无有效分类结果');
  let next='可在下方查看原始波形，并查看或发起云端分析。终端统计与云端分析分别显示。';
  if(s.upload_status!=='complete') next='请在终端等待上传完成；中断后可重试，手机刷新后继续查看。';
  else if(s.source_status!=='complete') next='保留此记录供排查，检查设备状态并重新测量；上传完成不代表采集完整。';
  else if(!real) next='此记录用于流程演示。实际测量请在终端确认实采模式后开始。';
  else if(!s.sample_count || (stats && !stats.usable_real_samples)) next='检查采集连接与电极接触，保持静止后复测；没有可靠数据时不推断结果正常。';
  text('measurement-next',next);
}
