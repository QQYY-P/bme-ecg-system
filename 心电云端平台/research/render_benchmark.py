"""Render the two completed benchmark JSON files as one reviewable Markdown report."""
import argparse
from collections import Counter
import json
from pathlib import Path

DETECTORS = {'current': '现有 QRS（含质量门控）', 'xqrs': 'XQRS（整段离线）',
             'fastnvg': 'FastNVG（整段离线）'}
MODES = {'end_to_end': '实际流式完整链路', 'reference_r': '参考 R 点诊断对照'}


def validate_reports(qrs, classifier):
    if qrs.get('version') != 'qrs-comparison-v1' or classifier.get('version') != 'cardioedge-independent-evaluation-v1':
        raise ValueError('Unsupported report format')
    if not qrs.get('aggregate') or not classifier.get('aggregate'):
        raise ValueError('Incomplete runs cannot be published as complete reports')
    for report in (qrs, classifier):
        if report['configuration']['records'] != [r['record'] for r in report['records']]:
            raise ValueError('Completed records differ from the requested cohort')
    for key in ('fs', 'lead', 'adc_gain_counts_per_mv', 'adc_offset', 'adc_quantization', 'seconds'):
        if qrs['configuration'][key] != classifier['configuration'][key]:
            raise ValueError(f'Inconsistent input protocol: {key}')
    qrs_records = {row['record']: row for row in qrs['records']}
    if not qrs_records or len(qrs_records) != len(qrs['records']):
        raise ValueError('Missing or duplicate QRS records')
    class_ids = [row['record'] for row in classifier['records']]
    if not class_ids or len(set(class_ids)) != len(class_ids):
        raise ValueError('Missing or duplicate classification records')
    for row in classifier['records']:
        other = qrs_records.get(row['record'])
        if other is None or any(row[key] != other[key] for key in
                                ('adc_sha256', 'source_files_sha256', 'sample_count', 'full_record')):
            raise ValueError('QRS and classifier must use exactly the same source and ADC samples')
    shared = qrs['code_sha256'].keys() & classifier['code_sha256'].keys()
    if 'research/benchmark_common.py' not in shared:
        raise ValueError('Missing common scoring provenance')
    if any(qrs['code_sha256'][key] != classifier['code_sha256'][key] for key in shared):
        raise ValueError('Shared scoring or production code differs between reports')
    for key in ('python', 'platform', 'machine', 'thread_limits'):
        if qrs['environment'][key] != classifier['environment'][key]:
            raise ValueError(f'Inconsistent benchmark environment: {key}')
    packages = qrs['environment']['packages'].keys() & classifier['environment']['packages'].keys()
    if any(qrs['environment']['packages'][key] != classifier['environment']['packages'][key] for key in packages):
        raise ValueError('Inconsistent shared library versions')


def validate_report_output(output, inputs):
    if Path(output).resolve() in {Path(path).resolve() for path in inputs}:
        raise ValueError('Markdown output must not overwrite an input JSON report')


def best_detectors(aggregate):
    valid = {key: row['qrs_150ms']['f1'] for key, row in aggregate.items()
             if row['qrs_150ms']['f1'] is not None}
    if not valid:
        return []
    best = max(valid.values())
    return [key for key, value in valid.items() if abs(value-best) < 1e-12]


def pct(value):
    return '—' if value is None else f'{value*100:.2f}%'


def number(value, digits=2):
    return '—' if value is None else f'{value:.{digits}f}'


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |',
                      '| '+' | '.join('---' for _ in headers)+' |',
                      *('| '+' | '.join(str(value) for value in row)+' |' for row in rows)])


def weighted_error(records, name, metric, weight):
    pairs = [(r['algorithms'][name]['qrs_150ms'][metric],
              r['algorithms'][name]['qrs_150ms'][weight]) for r in records]
    pairs = [(value, count) for value, count in pairs if value is not None and count]
    return sum(value*count for value, count in pairs)/sum(count for _, count in pairs) if pairs else None


def render(qrs, classifier):
    validate_reports(qrs, classifier)
    qrecords, crecords = qrs['records'], classifier['records']
    qhours = sum(r['signal_seconds'] for r in qrecords)/3600
    chours = sum(r['signal_seconds'] for r in crecords)/3600
    complete = all(r['full_record'] for r in qrecords+crecords)
    scope = '全长' if complete else '指定片段（非全长）'
    best = best_detectors(qrs['aggregate'])
    ranking = (f"150 ms 匹配条件下最高 F1 为 {pct(qrs['aggregate'][best[0]]['qrs_150ms']['f1'])}："
               + '、'.join(DETECTORS[key] for key in best) + ('（并列）' if len(best) > 1 else '')
               + '。这只是本轮公开数据条件下的排序。') if best else '没有可计算的 F1，不进行排序。'
    baseline = classifier['aggregate']['end_to_end']['scores_150ms']
    reference = classifier['aggregate']['reference_r']['scores_150ms']
    lines = [
        '# QRS 统一对比与当前分类模型独立评测', '',
        '[报告导航](README.md) · [QRS 原始汇总](qrs-comparison.json) · '
        '[分类原始汇总](classification-benchmark.json) · '
        '[复跑说明](../../心电云端平台/research/README.md)', '',
        '本页由 `research.render_benchmark` 从两份已完成 JSON 生成；原始数据库不随仓库分发。', '',
        '## 本轮结论', '',
        f'- QRS：{len(qrecords)} 条记录、{number(qhours)} 小时、{scope}。'+ranking,
        f"- 当前分类完整链路：宏平均 F1 {pct(baseline['end_to_end']['macro_f1'])}，"
        f"N/S/V F1 分别为 {' / '.join(pct(v) for v in baseline['end_to_end']['f1'])}；"
        f"目标心搏覆盖率 {pct(baseline['target_coverage'])}。",
        f"- 参考 R 点对照：宏平均 F1 {pct(reference['end_to_end']['macro_f1'])}，"
        f"覆盖率 {pct(reference['target_coverage'])}。对照保留质量门控、输入处理与拒绝规则，"
        '用于定位误差来源，不是上游离线准确率的复现，也不保证每个类别都会变好。',
        '- 候选检测器可作为后续云端算法接入依据；当前默认算法和分类权重本轮未替换。'
        '实际采集数据、其他数据库、噪声压力与临床有效性仍待验证。', '',
        '## 统一条件与边界', '',
        '- 数据：MIT-BIH 1.0.0；QRS 默认 44 条非起搏记录；分类默认上游 DS2 排除 202 的 21 条。'
        f'本次实际请求为 QRS {len(qrecords)} 条、分类 {len(crecords)} 条，具体见逐记录表。'
        '201 与 202 来自同一人，而 201 在上游训练集合中。没有重新训练、调阈值或挑选最优测试片段。',
        '- 360 Hz，按名称选择 MLII；记录 114 使用其第二通道。固定 `round(2048 + 200 × mV)`，'
        '限幅至 0–4095。三种检测器和两条分类路径使用同一量化波形，增益不按标注或逐记录统计调整。',
        '- QRS 与分类均以包含边界的 ±54 点（150 ms）为主，±18 点（50 ms）作敏感性检查；'
        '使用 WFDB 一对一匹配，所有启动、末尾、质量拒绝时段的参考心搏均保留在分母。',
        '- 现有 QRS 按 36 点块接收，保留生产质量门控。XQRS 保留内部滤波/学习；'
        'FastNVG 使用 NeuroKit2 `emrich2023` 清理与峰检测，不额外纠正伪迹。'
        '两者按整段输入，未附加生产质量门控。比较的是三条明确的处理路径，不是相同因果条件下只更换一个函数。',
        '- 分类两条路径均执行现有 `StreamingClassifier`。诊断对照只提供专家心搏位置，'
        '待收到至少 0.4 秒后续数据才释放；不提供类别标签，不改变 8 秒上下文、滚动标准化或拒绝条件。',
        '- 数据已公开且此前已用于项目探索；独立评测指独立执行固定实现，不能称为全新盲测、外部验证或实体设备验证。', '',
        '## QRS 汇总', '',
    ]
    rows = []
    for name, label in DETECTORS.items():
        aggregate = qrs['aggregate'][name]
        m = aggregate['qrs_150ms']
        rows.append([label, m['tp'], m['fp'], m['fn'], pct(m['sensitivity']), pct(m['positive_predictivity']),
                     pct(m['f1']), pct(aggregate['qrs_50ms']['f1']),
                     number(weighted_error(qrecords, name, 'r_mae_ms', 'tp')),
                     number(weighted_error(qrecords, name, 'rr_mae_ms', 'matched_adjacent_rr_pairs')),
                     str(sum(r['algorithms'][name]['qrs_150ms']['matched_adjacent_rr_pairs'] for r in qrecords))
                     + '/' + str(sum(r['algorithms'][name]['qrs_150ms']['reference_rr_pairs'] for r in qrecords))])
    lines += [table(['方法', 'TP', 'FP', 'FN', '灵敏度', '阳性预测值', 'F1（150ms）',
                     'F1（50ms）', 'R MAE（ms）', 'RR MAE（ms）', '匹配相邻 RR / 参考 RR'], rows), '',
              '汇总由合并计数计算。R/RR 误差按匹配点/相邻间期对加权；RR 只比较双方均相邻且同连续段的匹配对，'
              '漏检和断段不会被拼接为一次正常 RR。该误差必须与覆盖数一起阅读。', '',
              '节律、伪迹及室扑波等非心搏标注不作为参考 R 点；对应时段的额外检测仍可能计为 FP。'
              '符号计数和排除数量见 JSON。本报告使用所述 WFDB Python 评分口径，不宣称等同完整 bxb/AAMI 标准评测。', '',
              '### 逐记录 QRS', '',
              table(['记录', '导联通道', '现有 F1', 'XQRS F1', 'FastNVG F1', '现有质量通过比例'], [
                  [r['record'], r['channel'], *[pct(r['algorithms'][name]['qrs_150ms']['f1']) for name in DETECTORS],
                   pct(r['algorithms']['current']['quality_coverage'])] for r in qrecords]), '',
              '### 本机处理耗时', '',
              table(['方法', '处理秒数', '信号小时数', '处理时长 / 信号时长'], [
                  [label, number(qrs['aggregate'][name]['performance']['wall_seconds']), number(qhours),
                   number(qrs['aggregate'][name]['performance']['real_time_factor'], 6)]
                  for name, label in DETECTORS.items()]), '',
              '三种方法顺序运行；耗时包含各自预处理与检测，不含输入文件读取、依赖导入、网络和排队。'
              '整段离线耗时不是实时确认延迟，也不是树莓派性能或云端并发容量。', '',
              '## 分类评测', '',
              f'共 {len(crecords)} 条、{number(chours)} 小时、{scope}。N/S/V 映射与固定上游配置一致；'
              'N 包括束支阻滞等心搏，S 是室上性异位组，V 是室性异位组。F/Q 等非目标心搏另列，'
              '不能把 N 输出解释为健康结论。', '',
              '### 完整链路主指标', '',
              '精确率分母包含全部同类输出，包括对非目标心搏和无对应参考心搏的输出。召回率分母包含'
              '全部该类参考心搏，包括启动、质量门控、拒绝与末尾导致的未输出。宏平均 F1 为各类 F1 等权平均；'
              '完全没有参考也没有预测的类别记为不可用，不记为满分。', '']
    for mode, label in MODES.items():
        aggregate = classifier['aggregate'][mode]
        score = aggregate['scores_150ms']
        metrics = score['end_to_end']
        rejection_reasons = Counter()
        for row in crecords:
            rejection_reasons.update(row['modes'][mode]['rejection_reasons'])
        lines += [f'#### {label}', '',
                  table(['类别', '参考心搏', '预测数量', '精确率', '召回率', 'F1', '未输出的参考心搏'], [
                      [c, score['support'][i], score['predicted'][i], pct(metrics['precision'][i]),
                       pct(metrics['recall'][i]), pct(metrics['f1'][i]), score['unclassified_target_beats'][i]]
                      for i, c in enumerate(('N', 'S', 'V'))]), '',
                  f"宏平均 F1：**{pct(metrics['macro_f1'])}**；目标心搏覆盖率：**{pct(score['target_coverage'])}**。",
                  f"±50 ms 宏平均 F1：{pct(aggregate['scores_50ms']['end_to_end']['macro_f1'])}。",
                  f"非目标参考心搏 {score['reference_out_of_scope_beats']} 个；其中产生 N/S/V 输出 "
                  f"{sum(score['out_of_scope_predictions'])} 个；无对应参考的输出 "
                  f"{sum(score['unmatched_predictions'])} 个。拒绝候选 {aggregate['rejected_candidates']} 个。", '',
                  '拒绝原因：'+ ('；'.join(f'{reason}：{count}' for reason, count in rejection_reasons.items()) or '无候选被显式拒绝')+'。'
                  '拒绝候选不等于全部未输出心搏；启动、质量门控或漏检时可能没有候选，仍计入完整链路召回分母。', '',
                  '混淆矩阵仅含成功匹配的目标心搏，行是真实类别、列是预测类别：', '',
                  table(['真实 / 预测', 'N', 'S', 'V'], [[c, *score['confusion'][i]] for i, c in enumerate(('N', 'S', 'V'))]), '',
                  f"仅在这些匹配目标心搏上，准确率 {pct(score['matched_only']['accuracy'])}，"
                  f"宏平均 F1 {pct(score['matched_only']['macro_f1'])}。这是条件指标，不能替代上方完整链路结果。", '']
    lines += ['### 逐记录分类', '',
              table(['记录', '完整链路宏 F1', '参考 R 宏 F1', '完整链路 S F1', '参考 R S F1', '完整链路覆盖率'], [
                  [r['record'], pct(r['modes']['end_to_end']['scores_150ms']['end_to_end']['macro_f1']),
                   pct(r['modes']['reference_r']['scores_150ms']['end_to_end']['macro_f1']),
                   pct(r['modes']['end_to_end']['scores_150ms']['end_to_end']['f1'][1]),
                   pct(r['modes']['reference_r']['scores_150ms']['end_to_end']['f1'][1]),
                   pct(r['modes']['end_to_end']['scores_150ms']['target_coverage'])] for r in crecords]), '',
              '逐记录可能缺少某一类别；不可用项显示“—”，单条宏 F1 的可计算类别见 JSON。'
              '总体结果按合并混淆矩阵和计数计算，不平均每条记录百分比。', '',
              '### 分类处理与延迟', '',
              table(['路径', '处理秒数', '信号小时数', '逐记录确认延迟 P95 范围（ms）'], [
                  [label, number(classifier['aggregate'][mode]['performance']['wall_seconds']), number(chours),
                   ' / '.join(number(fn([r['modes'][mode]['performance']['confirmation_p95_ms'] for r in crecords
                                        if r['modes'][mode]['performance']['confirmation_p95_ms'] is not None]))
                              for fn in (min, max))
                   if any(r['modes'][mode]['performance']['confirmation_p95_ms'] is not None for r in crecords) else '—']
                  for mode, label in MODES.items()]), '',
              '确认延迟按已收到的采样点计算，包含相邻心搏和右侧上下文等待；本机运算耗时另列。'
              '表中是各条记录 P95 的最小/最大值，不是把这些 P95 再平均后的总体分位数。', '',
              '## 复现与溯源', '',
              f"QRS 运行开始（UTC）：`{qrs['created_utc']}`。分类运行开始（UTC）：`{classifier['created_utc']}`。", '',
              f"环境：{qrs['environment']['platform']}，Python {qrs['environment']['python']}。"
              + '；'.join(f'{name} {version}' for name, version in qrs['environment']['packages'].items())+'。', '',
              '依赖完整版本及线程限制、每个输入文件和 ADC 序列摘要、评测代码与模型摘要、逐记录统计均保留在 JSON。'
              '本地输出目录 `details/` 保存逐拍明细；公开汇总不包含原始波形或运行账号。'
              '输出目录必须为空，防止复跑失败后将旧报告与新明细混合。', '',
              '复跑命令与参数见[研究工具](../../心电云端平台/research/README.md)。', '',
              '参考来源：', '',
              '- [MIT-BIH 1.0.0、引用与数据许可](https://physionet.org/content/mitdb/1.0.0/)',
              '- [受试者及导联说明：201/202 同人、114 导联顺序](https://archive.physionet.org/physiobank/database/html/mitdbdir/intro.htm)',
              '- [固定上游分类、分组与输入配置](https://github.com/md-rejoyan-islam/CardioEdgeAI/blob/ec36bbc5f96ea42a93274c499a51e3678742ffcf/src/config.py)',
              '- [WFDB XQRS](https://wfdb.readthedocs.io/en/latest/processing.html)',
              '- [NeuroKit2 ECG / FastNVG](https://neuropsychology.github.io/NeuroKit/functions/ecg.html)',
              '- [FastNVG 原论文](https://doi.org/10.23919/EUSIPCO58844.2023.10290007)', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qrs', required=True, type=Path)
    parser.add_argument('--classification', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--check', action='store_true', help='verify generated Markdown without changing it')
    args = parser.parse_args()
    validate_report_output(args.output, [args.qrs, args.classification])
    result = render(json.loads(args.qrs.read_text(encoding='utf-8')),
                    json.loads(args.classification.read_text(encoding='utf-8')))
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding='utf-8') != result:
            raise SystemExit('Benchmark Markdown is missing or stale; regenerate from both completed JSON files')
        print('Benchmark Markdown matches both completed JSON reports')
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result, encoding='utf-8')


if __name__ == '__main__':
    main()
