#!/bin/sh
# Physical console / desktop entry. Standby never opens a recording on its own.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(id -u)" -eq 0 ]; then
    echo '请使用普通用户启动待机界面，不要使用 sudo。' >&2
    exit 2
fi
mkdir -p "$HOME/.cache/ecg-terminal"
exec 9>"$HOME/.cache/ecg-terminal/device.lock"
if ! flock -n 9; then
    echo '设备界面已运行，请返回已有窗口。' >&2
    exit 1
fi
exec /bin/sh "$script_dir/start-screen.sh" --standby --fullscreen "$@"
