#!/bin/sh
# Stage B package: bounded public-data replay in an isolated virtual display.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output=${1:-"$HOME/ecg-edge-demo-$(date +%Y%m%d-%H%M%S)"}
input=${2:-"$project_dir/examples/mitbih-233.npz"}
if [ -e "$output" ]; then
    printf '%s\n' '输出目录已存在，请换一个目录，保留原有结果。' >&2
    exit 2
fi
printf '%s\n' '回放公开 MIT-BIH 数据，不接收串口或人体数据。约 75 秒后结束。'
exec xvfb-run -a -s '-screen 0 1100x720x24' python3 "$project_dir/tools/edge_performance.py" \
    --replay "$input" --seconds 60 --output "$output"
