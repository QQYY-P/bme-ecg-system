#!/bin/sh
# Capture real UART data with the unchanged application pipeline for at least 180 s.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:-"$HOME/ecg-stage-$(date +%Y%m%d-%H%M%S)"}
PYTHON=${ECG_PYTHON:-python3}
command -v xvfb-run >/dev/null || { echo '需要 xvfb-run，请安装系统 xvfb 包。' >&2; exit 2; }
"$PYTHON" -c 'import tkinter, PIL.ImageGrab, numpy, scipy, serial'
if command -v fuser >/dev/null && fuser /dev/ttyUSB* /dev/ttyACM* >/dev/null 2>&1; then
    echo '串口正在使用。请先在现有心电界面停止采集并关闭窗口。' >&2
    exit 2
fi
if [ -e "$OUT" ]; then echo '输出目录已存在，请指定新目录。' >&2; exit 2; fi
printf '开始真实串口记录，约 3 分钟后自动停止：%s\n' "$OUT"
printf '本脚本不会切换 STM32 模式。请事先确认电极连接并选择实采模式。\n'
exec xvfb-run -a -s '-screen 0 1100x720x24' "$PYTHON" "$ROOT/tools/capture_live_ecg.py" --seconds 180 --output "$OUT"
