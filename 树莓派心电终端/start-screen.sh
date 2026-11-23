#!/bin/sh
# Run on the Raspberry Pi's own screen, without changing boot/login settings.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_command=${ECG_PYTHON:-python3}
blas_threads=${ECG_BLAS_THREADS:-1}
xsession=0
if [ "${1:-}" = "--xsession" ]; then
    xsession=1
    shift
fi

if [ "$(id -u)" -eq 0 ]; then
    printf '%s\n' '请使用普通用户运行本程序，不要使用 sudo 启动界面。' >&2
    exit 2
fi
case "$blas_threads" in
    1|2) ;;
    *) printf '%s\n' 'ECG_BLAS_THREADS 只能设为 1 或 2。' >&2; exit 2 ;;
esac
if ! command -v "$python_command" >/dev/null 2>&1; then
    printf '找不到 Python：%s。请先按 README.txt 安装依赖。\n' "$python_command" >&2
    exit 2
fi
export OPENBLAS_NUM_THREADS="$blas_threads"
export OMP_NUM_THREADS="$blas_threads"
export MKL_NUM_THREADS="$blas_threads"
export VECLIB_MAXIMUM_THREADS="$blas_threads"
export NUMEXPR_NUM_THREADS="$blas_threads"

# Headless checks and help do not require a display; SSH may run these modes.
for option in "$@"; do
    case "$option" in
        --headless|--list-ports|--help|-h)
            exec "$python_command" "$script_dir/app.py" --blas-threads "$blas_threads" "$@"
            ;;
    esac
done

if [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]; then
    printf '%s\n' \
        '当前是 SSH 会话。请在树莓派所接显示器和键盘上登录，再运行以下命令：' >&2
    printf '  sh "%s/start-demo.sh" --fullscreen --geometry 800x480\n' "$script_dir" >&2
    printf '%s\n' \
        '如需在 SSH 中做无界面检查，请加 --headless --demo --duration 10。' \
        '不要手工设置 DISPLAY=:0 或使用 sudo 来绕过图形会话权限。' >&2
    exit 2
fi

if [ "$xsession" -eq 1 ]; then
    if [ -z "${DISPLAY:-}" ]; then
        printf '%s\n' 'X11 会话没有建立，请检查 startx 输出。' >&2
        exit 2
    fi
    # These settings affect only the temporary X session created below.
    screen_mode=${ECG_SCREEN_MODE:-}
    screen_rate=${ECG_SCREEN_RATE:-}
    if [ -n "$screen_mode" ]; then
        if ! printf '%s\n' "$screen_mode" | grep -Eq '^[0-9]+x[0-9]+$'; then
            printf '%s\n' 'ECG_SCREEN_MODE 格式应为 1024x768。' >&2
            exit 2
        fi
        if ! command -v xrandr >/dev/null 2>&1; then
            printf '%s\n' '缺少 xrandr，请安装 x11-xserver-utils。' >&2
            exit 2
        fi
        if ! display_info=$(xrandr --query); then
            printf '%s\n' '无法读取显示器模式。' >&2
            exit 2
        fi
        screen_output=$(printf '%s\n' "$display_info" | awk '$2 == "connected" { print $1; exit }')
        if [ -z "$screen_output" ]; then
            printf '%s\n' '未检测到已连接的显示输出。' >&2
            exit 2
        fi
        if [ -n "$screen_rate" ]; then
            if ! printf '%s\n' "$screen_rate" | grep -Eq '^[0-9]+([.][0-9]+)?$'; then
                printf '%s\n' 'ECG_SCREEN_RATE 必须是数字，例如 60。' >&2
                exit 2
            fi
            xrandr --output "$screen_output" --mode "$screen_mode" --rate "$screen_rate" || {
                printf '%s\n' '显示模式切换失败，请选择显示器支持的分辨率和刷新率。' >&2
                exit 2
            }
        else
            xrandr --output "$screen_output" --mode "$screen_mode" || {
                printf '%s\n' '显示模式切换失败，请选择显示器支持的分辨率。' >&2
                exit 2
            }
        fi
    elif [ -n "$screen_rate" ]; then
        printf '%s\n' '指定 ECG_SCREEN_RATE 时也需指定 ECG_SCREEN_MODE。' >&2
        exit 2
    fi
    # Work around the confirmed 32-bit Xorg CTM initialization bug: the Pi's
    # original KMS blob was all zero. xrandr 1.5.3 displays nine real values.
    # Only repair that all-zero matrix on active outputs in our own X session.
    if ! command -v xrandr >/dev/null 2>&1; then
        printf '%s\n' '缺少 xrandr，请安装 x11-xserver-utils。' >&2
        exit 2
    fi
    if ! ctm_info=$(LC_ALL=C xrandr --current --prop); then
        printf '%s\n' '无法读取显示器颜色矩阵，已停止本次界面启动。' >&2
        exit 2
    fi
    zero_ctm_outputs=$(printf '%s\n' "$ctm_info" | awk '
        function finish_ctm() {
            if (reading && count == 9 && all_zero) print output
            reading = 0
            count = 0
            all_zero = 1
        }
        /^[^[:space:]]/ {
            finish_ctm()
            output = $1
            active = ($2 == "connected" && $0 ~ /[[:space:]][0-9]+x[0-9]+[+-][0-9]+[+-][0-9]+([[:space:]]|$)/)
            next
        }
        /^[[:space:]]+CTM:/ {
            finish_ctm()
            if (!active) next
            reading = 1
            sub(/^[[:space:]]+CTM:[[:space:]]*/, "")
        }
        reading {
            if ($0 !~ /^[[:space:]0-9.eE+-]*$/) {
                finish_ctm()
                next
            }
            for (i = 1; i <= NF; i++) {
                count++
                if ($i !~ /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$/ || $i + 0 != 0)
                    all_zero = 0
            }
        }
        END { finish_ctm() }
    ')
    if [ -n "$zero_ctm_outputs" ]; then
        printf '%s\n' "$zero_ctm_outputs" | while IFS= read -r ctm_output; do
            if ! LC_ALL=C xrandr --output "$ctm_output" --set CTM '1,0,0,0,1,0,0,0,1'; then
                printf '无法修复显示输出 %s 的全零颜色矩阵，已停止本次界面启动。\n' "$ctm_output" >&2
                exit 2
            fi
            printf '已修复显示输出 %s 的全零颜色矩阵。\n' "$ctm_output"
        done
    fi
    if command -v xset >/dev/null 2>&1; then
        xset dpms force on >/dev/null 2>&1 || true
        xset -dpms >/dev/null 2>&1 || true
        xset s off >/dev/null 2>&1 || true
        xset s noblank >/dev/null 2>&1 || true
    fi
    openbox_pid=
    cleanup() {
        if [ -n "$openbox_pid" ]; then
            kill "$openbox_pid" 2>/dev/null || true
            wait "$openbox_pid" 2>/dev/null || true
        fi
    }
    trap cleanup 0
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    if command -v openbox >/dev/null 2>&1; then
        openbox --sm-disable &
        openbox_pid=$!
    fi
    app_status=0
    "$python_command" "$script_dir/app.py" --blas-threads "$blas_threads" "$@" || app_status=$?
    exit "$app_status"
fi

if [ -n "${DISPLAY:-}" ]; then
    exec "$python_command" "$script_dir/app.py" --blas-threads "$blas_threads" "$@"
fi
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    printf '%s\n' '当前 Wayland 会话未提供 Tk 所需的 X11 DISPLAY；请使用支持 XWayland 的桌面终端。' >&2
    exit 2
fi

console_tty=$(tty 2>/dev/null || true)
case "$console_tty" in
    /dev/tty[0-9]*) ;;
    *)
        printf '%s\n' '未检测到本地图形会话或树莓派物理控制台，请在树莓派屏幕上登录后运行。' >&2
        exit 2
        ;;
esac
if ! command -v startx >/dev/null 2>&1; then
    printf '%s\n' '缺少 startx。请先按 README.txt 安装 xserver-xorg、xinit 和 openbox。' >&2
    exit 2
fi

# An explicit client keeps this temporary X session independent of ~/.xinitrc.
# /bin/sh also permits scripts copied from Windows without executable mode bits.
exec startx /bin/sh "$script_dir/start-screen.sh" --xsession "$@" -- -nolisten tcp
