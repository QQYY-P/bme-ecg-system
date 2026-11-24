"""Generate offline README.txt files and the public report summary from one source."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('树莓派心电终端', '心电云端平台')
PAGES = ('快速开始.md', '使用说明.md', '故障排查.md')


def offline_readme(root: Path, module: str) -> str:
    sections = []
    for page in PAGES:
        body = (root / module / 'docs' / page).read_text(encoding='utf-8')
        # Keep labels; local file links in Markdown become usable online URLs in
        # the standalone text bundled by historical and current package scripts.
        def link(match):
            label, target = match.groups()
            if not re.match(r'[a-z][a-z0-9+.-]*:', target, re.I):
                import posixpath
                path, sep, anchor = target.partition('#')
                path = posixpath.normpath(f'{module}/docs/{path or page}')
                target = 'https://github.com/XiongBoT/bme-ecg-system/blob/main/' + path
                if sep:
                    target += '#' + anchor
            return f'{label}（{target}）'
        body = re.sub(r'\[([^\]\n]+)\]\(([^)\n]+)\)', link, body)
        sections.append(body.strip())
    return ('由 tools/sync_docs.py 从本模块 docs/ 下的快速开始、使用说明、故障排查生成。\n'
            '请修改 Markdown 原文后重新生成，不单独编辑本文件。\n'
            '本文说明完整仓库当前代码；早期精简发布包不一定包含全部功能。\n\n'
            + '\n\n'.join(sections) + '\n')


def report_summary(report: dict) -> str:
    config, total = report['configuration'], report['aggregate']
    lines = [
        '# 公开数据评估结果摘要', '',
        '[报告说明](README.md) · [完整 HTML](report.html) · [原始 JSON](report.json) · '
        '[复跑方法](../../心电云端平台/research/README.md)', '',
        '本页由 `tools/sync_docs.py` 根据同目录 `report.json` 生成，不重新运行实验。', '',
        f"报告时间（UTC）：`{report['created_utc']}`；格式：`{report['version']}`。", '',
        f"MIT-BIH 记录：`{' '.join(config['records'])}`；每条前 {config['seconds']} 秒，"
        f"通道 {config['channel']}，采样率 {config['sample_rate_hz']} Hz，"
        f"QRS 匹配容差 {config['match_tolerance_ms']} ms。", '',
        '## 汇总', '', '| 指标 | 数值 |', '| --- | ---: |',
        f"| TP / FP / FN | {total['tp']} / {total['fp']} / {total['fn']} |",
        f"| 灵敏度 | {total['sensitivity']:.2%} |",
        f"| 阳性预测值 | {total['positive_predictivity']:.2%} |",
        f"| F1 | {total['f1']:.2%} |", '',
        '## 按记录查看', '',
        '| 记录 | 导联 | TP | FP | FN | F1 | R 点平均绝对误差（ms） | RR 平均绝对误差（ms） |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    def number(value):
        return '不适用' if value is None else f'{value:.3f}'
    for item in report['records']:
        qrs = item['qrs']
        lines.append(f"| {item['record']} | {item['lead']} | {qrs['tp']} | {qrs['fp']} | "
                     f"{qrs['fn']} | {qrs['f1']:.2%} | {number(qrs['r_mae_ms'])} | "
                     f"{number(qrs['rr_mae_ms'])} |")
    lines += ['', '## 解释与范围', '',
              '汇总指标来自全部记录的合并计数，不是逐记录百分比的简单平均。'
              'RR 误差仅对满足报告匹配条件的相邻心搏计算；HRV/NN 的节律筛选、'
              '覆盖范围和详细数值请查完整报告。', '',
              '这些数值评价本报告的 QRS/RR 检测路径，不是 N/S/V 分类准确率。'
              '本次仅为指定公开记录的离线片段评估，不能代表全数据库、实体设备、'
              '临床验证或云端已部署 HRV 功能。', '',
              '更新报告时同时更新 HTML 与 JSON，再运行 `python tools/sync_docs.py --write`。'
              '输入文件和算法代码摘要、运行环境保留在 JSON 中；原始数据库不加入仓库。', '']
    return '\n'.join(lines)


def generated_files(root: Path) -> dict[str, str]:
    result = {module + '/README.txt': offline_readme(root, module) for module in MODULES}
    report = json.loads((root / 'docs/algorithm-validation/report.json').read_text(encoding='utf-8'))
    result['docs/algorithm-validation/结果摘要.md'] = report_summary(report)
    return result


def stale_files(root: Path, expected: dict[str, str]) -> list[str]:
    return [name for name, text in expected.items()
            if not (root / name).is_file() or (root / name).read_text(encoding='utf-8') != text]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--write', action='store_true')
    args = parser.parse_args()
    expected = generated_files(ROOT)
    stale = stale_files(ROOT, expected)
    if args.write:
        for name in stale:
            (ROOT / name).write_bytes(expected[name].encode('utf-8'))
        print(f'Updated {len(stale)} generated documents.')
        return 0
    for name in stale:
        print(f'{name}: out of date; run python tools/sync_docs.py --write')
    print(f'Checked {len(expected)} generated documents; {len(stale)} stale.')
    return int(bool(stale))


if __name__ == '__main__':
    raise SystemExit(main())
