/* Presentation rules only. Do not infer a diagnosis or merge analysis sources. */
(function(root,factory){
  const value=factory();if(typeof module==='object'&&module.exports)module.exports=value;else root.ECGExperience=value;
})(globalThis,function(){
  'use strict';
  const statuses={loading:'正在读取',not_run:'尚未开始',queued:'等待分析',running:'正在分析',
    complete:'分析完成',ineligible:'条件不足，未得到有效结果',failed:'分析失败',
    stale:'结果需重新计算',not_configured:'服务未启用',unavailable:'暂时无法读取'};
  function analysisLabel(result){return result?statuses[result.analysis_status] || '状态待确认':'正在读取';}
  function describe(record,classification,physiology){
    if(!record)return {title:'正在读取记录',next:'请稍候。',tone:'neutral'};
    const own=result=>result?.session_id===record.session_id?result:null;
    classification=own(classification);physiology=own(physiology);
    const info={classification:analysisLabel(classification),physiology:analysisLabel(physiology),tone:'neutral'};
    if(record.upload_status!=='complete')return {...info,title:'记录尚未上传完成',next:'请在采集终端等待或重试上传，完成后刷新这里。已收到的数据仍可查看。'};
    if(record.source_status!=='complete')return {...info,tone:'attention',title:'上传完成，采集完整性需核对',next:'上传成功不代表采集完整。保留这条记录，检查设备后再测量。'};
    if(!record.sample_count)return {...info,tone:'attention',title:'未收到采样数据',next:'请在设备端检查连接并重新测量；没有数据不能判断结果正常。'};
    if(record.source!=='serial')return {...info,title:'演示或回放记录',next:'此记录不作为本次人体测量结果。实际测量需在终端确认实采模式。'};
    const results=[classification,physiology],known=results.filter(Boolean);
    if(known.some(r=>r.analysis_status==='unavailable'))return {...info,title:'记录已上传，分析状态读取失败',next:'请重试读取结果。下方分别显示两个分析任务的状态。'};
    if(known.some(r=>r.analysis_status==='failed'))return {...info,tone:'attention',title:'记录已上传，部分分析失败',next:'原始记录仍保留，可在对应分析卡片重试。'};
    if(known.some(r=>['queued','running'].includes(r.analysis_status)))return {...info,title:'记录已上传，正在处理分析',next:'可先查看波形，返回记录页不会中止已接受的后台任务。'};
    if(known.some(r=>r.analysis_status==='ineligible'))return {...info,tone:'attention',title:'记录已上传，部分分析条件不足',next:'展开对应分析查看原因；未得到有效结果不代表正常。'};
    if(known.some(r=>r.analysis_status==='stale'))return {...info,title:'记录已上传，分析结果需更新',next:'请在对应分析卡片重新计算，原始记录保留。'};
    if(known.length===2 && known.every(r=>r.analysis_status==='complete'))return {...info,tone:'ready',title:'记录已上传，两项云端分析已完成',next:'终端统计与云端分析分别显示。研究模型结果不作为临床诊断。'};
    if(known.some(r=>r.analysis_status==='complete'))return {...info,title:'记录已上传，部分分析已完成',next:'可查看已完成的结果，其他任务状态见下方。'};
    return {...info,title:'记录已上传',next:'可查看波形；尚未开始的分析需在对应卡片点击启动。'};
  }
  function localDay(value){
    if(!value)return '';
    const d=new Date(value);if(Number.isNaN(d.getTime()))return '';
    return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
  }
  function dateRange(days,now=new Date()){
    const start=new Date(now.getFullYear(),now.getMonth(),now.getDate());start.setDate(start.getDate()-days+1);
    return {from:localDay(start),to:localDay(now)};
  }
  function duration(samples){return Number.isFinite(samples)?(samples/360).toFixed(1)+' 秒':'时长未知';}
  function escape(value){return String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function reportHTML(report){
    const rows=report.rows.map(([label,value])=>'<tr><th>'+escape(label)+'</th><td>'+escape(value)+'</td></tr>').join('');
    const wave=report.image?.startsWith('data:image/png;base64,')?'<img alt="当前窗口的原始波形片段" src="'+escape(report.image)+'">':'<p>当前没有可导出的波形图。</p>';
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>心电测量记录</title><style>'+
      'body{font:15px/1.7 sans-serif;color:#203e46;margin:28px auto;padding:0 20px;max-width:760px}h1{font-size:24px}h2{font-size:18px}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #dce8e5;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{width:32%;font-weight:500}img{width:100%;height:auto;border:1px solid #dce8e5}p{overflow-wrap:anywhere}.note{font-size:12px;color:#526b62}tr,img{break-inside:avoid}</style></head><body><h1>心电测量记录</h1><p>'+escape(report.title)+'</p><table>'+rows+'</table><h2>波形片段</h2>'+wave+'<p>'+escape(report.range)+'</p><p class="note">'+escape(report.note)+'</p><p class="note">课程研究用途。N 类不代表临床正常，模型分数不是诊断概率。此报告仅整理已读取的数据，不替代完整原始记录。</p></body></html>';
  }
  return {analysisLabel,describe,localDay,dateRange,duration,reportHTML};
});
