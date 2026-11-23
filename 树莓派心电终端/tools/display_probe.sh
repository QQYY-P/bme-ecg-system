#!/bin/sh
# Run a short display-only probe, without changing boot or graphics settings.
set -eu
umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$script_dir/ecg-display-probe.py" ]; then
    probe_file="$script_dir/ecg-display-probe.py"
elif [ -f "$script_dir/display_probe.py" ]; then
    probe_file="$script_dir/display_probe.py"
else
    printf '%s\n' '找不到同目录的显示测试程序 / Display probe Python file not found.' >&2
    exit 2
fi
log_file="$HOME/ecg-display-probe-x.log"
python_command=/usr/bin/python3

if [ "$(id -u)" -eq 0 ]; then
    printf '%s\n' '请使用普通用户运行，不要加 sudo / Run as your normal user, without sudo.' >&2
    exit 2
fi
if [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]; then
    printf '%s\n' '请在树莓派所接显示器和键盘上的命令行运行本测试。' \
        'Run this test on the Raspberry Pi physical screen and keyboard, not through SSH.' >&2
    exit 2
fi
if [ ! -x "$python_command" ]; then
    printf '%s\n' '找不到 /usr/bin/python3，请先安装 Python 3。' >&2
    exit 2
fi

probe_status=0
if [ -n "${DISPLAY:-}" ]; then
    printf '%s\n' '正在当前图形会话中测试；45 秒后自动退出 / Testing current display for 45 seconds.'
    "$python_command" "$probe_file" >"$log_file" 2>&1 || probe_status=$?
else
    console_tty=$(tty 2>/dev/null || true)
    case "$console_tty" in
        /dev/tty[0-9]*) ;;
        *)
            printf '%s\n' '未检测到物理控制台，请在树莓派屏幕上登录后运行。' \
                'No physical console detected. Log in on the Raspberry Pi screen first.' >&2
            exit 2
            ;;
    esac
    if ! command -v startx >/dev/null 2>&1; then
        printf '%s\n' '找不到 startx，无法建立显示测试会话。' >&2
        exit 2
    fi
    printf '%s\n' '正在启动显示测试；45 秒后自动返回命令行 / Starting a 45-second display test.'
    startx "$python_command" "$probe_file" -- -nolisten tcp >"$log_file" 2>&1 || probe_status=$?
fi

printf '显示测试结束，退出码 / Exit code: %s\n' "$probe_status"
printf '启动日志 / Startup log: %s\n' "$log_file"
printf '显示报告 / Display report: %s\n' "$HOME/ecg-display-probe.json"
printf '可选截图 / Optional screenshot: %s\n' "$HOME/ecg-display-probe.png"
exit "$probe_status"
