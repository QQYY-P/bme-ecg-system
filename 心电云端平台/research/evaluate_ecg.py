"""Read-only MIT-BIH replay and versioned QRS/RR/HRV baseline report.

Run as python -m research.evaluate_ecg --data PATH --records 100 101 ...
WFDB is an offline evaluation dependency, not a server runtime requirement.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import html
import json
from pathlib import Path
import platform
import numpy as np
from research.physiology import interval_statistics
from research.streaming_qrs import detect_adc

ROOT=Path(__file__).resolve().parents[1]
BEAT_SYMBOLS={'N','L','R','a','V','F','J','A','S','E','j','/','Q','B','e','n','f','r','?'}
VERSION='mitbih-qrs-hrv-evaluation-v1'
SOURCES=['research/physiology.py','research/streaming_qrs.py','research/evaluate_ecg.py','cloud_model/vendor/rpeak_detector.py','cloud_model/vendor/signal_quality.py','cloud_model/vendor/notch_filter.py']


def checksum(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def matching_metrics(reference,detected,ref_indices,test_indices,segments,fs=360):
    ref,test=np.asarray(reference),np.asarray(detected)
    ri,ti=np.asarray(ref_indices,dtype=int),np.asarray(test_indices,dtype=int)
    if len(ri)!=len(ti) or len(set(ri))!=len(ri) or len(set(ti))!=len(ti):
        raise ValueError('Matching must be one-to-one')
    order=np.argsort(ri);ri,ti=ri[order],ti[order]
    errors=(test[ti]-ref[ri])*1000/fs
    adjacent=(np.diff(ri)==1)&(np.diff(ti)==1)
    if len(ti)>1:adjacent&=np.asarray(segments)[ti[1:]]==np.asarray(segments)[ti[:-1]]
    rr_errors=(np.diff(test[ti])-np.diff(ref[ri]))[adjacent]*1000/fs
    tp=len(ri);fp=len(test)-tp;fn=len(ref)-tp
    return {'tp':tp,'fp':fp,'fn':fn,
        'sensitivity':tp/len(ref) if len(ref) else None,
        'positive_predictivity':tp/len(test) if len(test) else None,
        'f1':2*tp/(len(ref)+len(test)) if len(ref)+len(test) else None,
        'r_mae_ms':float(np.abs(errors).mean()) if len(errors) else None,
        'r_p95_absolute_ms':float(np.percentile(np.abs(errors),95)) if len(errors) else None,
        'matched_adjacent_rr_pairs':int(adjacent.sum()),
        'reference_rr_pairs':max(0,len(ref)-1),
        'rr_mae_ms':float(np.abs(rr_errors).mean()) if len(rr_errors) else None,
        'rr_rmse_ms':float(np.sqrt(np.mean(rr_errors**2))) if len(rr_errors) else None}


def reference_hrv(reference,symbols,adc,rhythm_samples=(),rhythm_labels=()):
    raw=np.asarray(adc,dtype=float);positions=np.asarray(reference,dtype=int)
    valid=np.isfinite(raw)&(raw>0)&(raw<4095)
    # Source discontinuities, not detector decisions, define reference segments.
    segments=np.cumsum(np.r_[0,valid[1:]!=valid[:-1]])
    rhythms=np.asarray(rhythm_samples,dtype=int)
    if len(rhythms)!=len(rhythm_labels) or np.any(np.diff(rhythms)<0):raise ValueError('Invalid rhythm annotations')
    labels=['unknown']+[str(label).replace('\x00','').strip() for label in rhythm_labels]
    rhythm_index=np.searchsorted(rhythms,positions,side='right')
    sinus=np.asarray([labels[i]=='(N' for i in rhythm_index],dtype=bool)
    # N morphology is insufficient: e.g. record 201 begins in AFIB with N beats.
    combined=segments[positions]*(len(rhythms)+1)+rhythm_index
    normal=(np.asarray(symbols)=='N')&valid[positions]&sinus
    result=interval_statistics(positions,segments=combined,normal=normal,observation_seconds=len(raw)/360)
    result['nn_basis']='N morphology AND explicit (N sinus rhythm AND valid continuous source samples'
    result['sinus_normal_beats']=int(normal.sum())
    return result


def evaluate_record(data,record,seconds,channel):
    import wfdb
    from wfdb.processing import compare_annotations
    base=data/record;header=wfdb.rdheader(str(base))
    if header.fs!=360:raise ValueError(f'{record}: this detector requires 360 Hz')
    if not 0<=channel<header.n_sig:raise ValueError('Invalid channel index')
    stop=min(int(seconds*header.fs),header.sig_len)
    paths={base.with_suffix('.hea'),base.with_suffix('.atr')}
    for filename in header.file_name:
        path=(data/filename).resolve()
        if not path.is_relative_to(data.resolve()):raise ValueError('Signal file outside dataset')
        paths.add(path)
    hashes={p.name:checksum(p) for p in sorted(paths)}
    record_data=wfdb.rdrecord(str(base),sampto=stop,channels=[channel])
    if record_data.units[0]!='mV':raise ValueError('The fixed ADC mapping requires signal units in mV')
    annotation=wfdb.rdann(str(base),'atr',sampto=stop-1)
    keep=np.array([s in BEAT_SYMBOLS for s in annotation.symbol],dtype=bool)
    reference=annotation.sample[keep]
    symbols=np.asarray(annotation.symbol)[keep]
    rhythm_mask=np.asarray(annotation.symbol)=='+'
    rhythm_samples=annotation.sample[rhythm_mask]
    rhythm_labels=np.asarray(annotation.aux_note)[rhythm_mask].tolist()
    adc=2048+record_data.p_signal[:,0]*200  # fixed experiment gain, never fitted to annotations
    detected=detect_adc(adc,block_size=36)
    test=np.asarray(detected['r_samples'],dtype=int)
    if len(reference) and len(test):
        comparison=compare_annotations(reference,test,54)
        ri,ti=comparison.matched_ref_inds,comparison.matched_test_inds
    else:ri,ti=[],[]
    metrics=matching_metrics(reference,test,ri,ti,detected['segments'])
    if any(checksum(p)!=hashes[p.name] for p in paths):raise RuntimeError('Dataset file changed during evaluation')
    return {'record':record,'channel':channel,'lead':record_data.sig_name[0],
        'source_files_sha256':hashes,'signal_seconds':stop/360,
        'reference_beat_symbols':dict(zip(*[a.tolist() for a in np.unique(symbols,return_counts=True)])),
        'ignored_nonbeat_annotations':int((~keep).sum()),
        'nonbeat_symbols':dict(zip(*[a.tolist() for a in np.unique(np.asarray(annotation.symbol)[~keep],return_counts=True)])),
        'rhythms':[{'sample':int(s),'label':v.replace('\x00','').strip()} for s,v in zip(rhythm_samples,rhythm_labels)],
        'qrs':metrics,'detection':detected,
        'detected_rr':interval_statistics(test,segments=detected['segments'],observation_seconds=stop/360),
        'reference_hrv':reference_hrv(reference,symbols,adc,rhythm_samples,rhythm_labels)}


def render_report(report):
    def number(value,digits=2):return '—' if value is None else f'{value:.{digits}f}'
    def pct(value):return '—' if value is None else number(value*100)+'%'
    rows=[];hrv=[]
    for r in report['records']:
        m=r['qrs'];p=r['detection']['performance'];nn=r['reference_hrv']['nn'];rr=r['detected_rr']['rr']
        values=[r['record'],r['lead'],m['tp'],m['fp'],m['fn'],pct(m['sensitivity']),pct(m['positive_predictivity']),pct(m['f1']),
                number(m['r_mae_ms']),number(m['rr_mae_ms']),f"{m['matched_adjacent_rr_pairs']}/{m['reference_rr_pairs']}",
                pct(r['detection']['quality_coverage']),number(p['real_time_factor'],4),number(p['block_p95_ms']),number(p['confirmation_p95_ms'])]
        rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>')
        values=[r['record'],nn['intervals'],nn['successive_pairs'],number(nn['sdnn_ms']),number(nn['rmssd_ms']),number(nn['pnn50_percent']),
                rr['intervals'],number(rr['sdrr_ms']),number(rr['rmssd_ms'])]
        hrv.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>')
    def table(headers,rows):return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    summary=report['aggregate']
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>心电算法验证与性能基线</title><style>body{{font:16px/1.7 system-ui,sans-serif;background:#f3f7f7;color:#16343a;margin:0;padding:30px}}main{{max-width:1300px;margin:auto}}h1{{font-size:30px}}section{{background:white;padding:24px;margin:20px 0;border-radius:12px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}}td,th{{padding:10px;text-align:right;border-bottom:1px solid #dce6e5}}th{{background:#eaf5f2}}td:first-child,th:first-child{{text-align:left}}.muted{{color:#587079}}code{{overflow-wrap:anywhere}}a{{color:#087f71}}@media(max-width:600px){{body{{padding:12px}}section{{padding:16px}}}}</style>
<main><p class="muted">{html.escape(report['version'])} · {html.escape(report['created_utc'])}</p><h1>QRS / RR、HRV 与性能基线</h1>
<p>读取已有 MIT-BIH 记录，复用项目现有检测器与质量门控。原始数据未修改。以下为选定记录的前 {report['configuration']['seconds']} 秒，不代表全数据库或临床性能。</p>
<section><h2>检测结果</h2><p>汇总 {len(report['records'])} 条记录：Se {pct(summary['sensitivity'])} · PPV {pct(summary['positive_predictivity'])} · F1 {pct(summary['f1'])}。TP {summary['tp']} / FP {summary['fp']} / FN {summary['fn']}。</p>
{table(['记录','导联','TP','FP','FN','Se','PPV','F1','R误差ms','RR误差ms','相邻RR匹配/参考','质量通过','处理/信号时长','块P95 ms','确认P95 ms'],rows)}
<p class="muted">150ms 容差一对一匹配。未通过质量门控及启动/末尾窗口的参考心搏仍计入分母。RR误差仅使用双方均相邻、同连续段的匹配对，必须结合覆盖数阅读。节律、伪迹与室扑波标记不是参考QRS心搏；它们不增加TP分母，但对应时段的未匹配检测仍计为FP。非心搏标记类型见JSON。</p></section>
<section><h2>HRV 时间域与检测 RR</h2><p>左侧NN要求同一有效采样及节律连续段、专家心搏标注N、并有明确(N窦性节律标注。仅N形态不够，未知、房颤等节律不进入NN；不按检测器的质量门控筛选参考。右侧来自检测R序列，未确认正常窦性，因此只称RR变异性。不能将两个序列直接视为相同样本，不能将模型N类自动替代专家NN。</p>
{table(['记录','参考NN数','相邻NN对','参考SDNN ms','参考RMSSD ms','参考pNN50 %','检测RR数','检测SDRR ms','检测RMSSD ms'],hrv)}
<p>使用280–2500ms工程范围筛选。断段、异常间期和非正常心搏均不拼接；不插值。SDNN使用样本标准差；pNN50的分母为可比较的连续NN间期对。缺少样本时显示“—”。记录长度与排除比例会影响统计，报告不提供疾病、压力或健康等级。</p></section>
<section><h2>运行条件与复现</h2><p>{html.escape(report['environment']['platform'])} · Python {report['environment']['python']} · WFDB {report['environment']['wfdb']}。这是本机离线回放，耗时不包含文件读取、分类模型、网络传输和队列等待；不代表树莓派或多设备服务器容量。</p>
<p>输入：通道 {report['configuration']['channel']}，360Hz，固定200 counts/mV映射到2048中点；不按测试结果调整增益。检测块36点。算法与输入文件SHA256、逐条R点、连续段及完整数值保存于同目录 <a href="report.json">report.json</a>。</p>
<p><a href="https://physionet.org/content/mitdb/1.0.0/">MIT-BIH数据及引用</a> · <a href="https://archive.physionet.org/tutorials/hrv-toolkit/">HRV统计依据</a> · <a href="https://www.physionet.org/physiotools/wag/bxb-1.htm">匹配容差参考</a>。本报告采用WFDB Python匹配，不宣称等同完整bxb/AAMI评测。</p></section></main></html>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',required=True,type=Path)
    parser.add_argument('--records',nargs='+',required=True)
    parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--channel',type=int,default=0)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs'/'qrs-hrv-evaluation')
    args=parser.parse_args()
    if not 10<=args.seconds<=1800:parser.error('seconds must be 10..1800')
    if len(set(args.records))!=len(args.records) or any(not r.isdigit() or len(r)!=3 for r in args.records):parser.error('unique three-digit record identifiers required')
    if args.output.resolve().is_relative_to(args.data.resolve()):parser.error('outputs must be outside the source dataset')
    import scipy,wfdb
    fingerprints={p:checksum(ROOT/p) for p in SOURCES}
    results=[]
    for name in args.records:
        result=evaluate_record(args.data,name,args.seconds,args.channel);results.append(result)
        print(name, 'TP/FP/FN',*[result['qrs'][k] for k in ('tp','fp','fn')],flush=True)
    aggregate={k:sum(r['qrs'][k] for r in results) for k in ('tp','fp','fn')}
    tp,fp,fn=(aggregate[k] for k in ('tp','fp','fn'))
    aggregate.update(sensitivity=tp/(tp+fn) if tp+fn else None,positive_predictivity=tp/(tp+fp) if tp+fp else None,f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)
    if any(checksum(ROOT/p)!=value for p,value in fingerprints.items()):raise RuntimeError('Source code changed during evaluation; report not written')
    report={'version':VERSION,'created_utc':datetime.now(timezone.utc).isoformat(),
        'configuration':{'records':args.records,'seconds':args.seconds,'channel':args.channel,'match_tolerance_ms':150,'sample_rate_hz':360,'adc_gain_counts_per_mv':200,'adc_midpoint':2048},
        'environment':{'platform':platform.platform(),'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,'wfdb':wfdb.__version__},
        'source_sha256':fingerprints,'aggregate':aggregate,'records':results}
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (args.output/'report.html').write_text(render_report(report),encoding='utf-8')
    print('Report:',args.output/'report.html',flush=True)


if __name__=='__main__':main()
