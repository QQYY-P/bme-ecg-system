#!/bin/sh
# Fix the verified zero-CTM defect only in this temporary X session.
set -eu
umask 077
if [ "$(id -u)" -eq 0 ] || [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]; then
    printf '%s\n' 'Run on the Raspberry Pi physical console as pi2, without sudo.' >&2
    exit 2
fi
if [ "${1:-}" = '--xclient' ]; then
    LC_ALL=C xrandr --output HDMI-1 --set CTM '1,0,0,0,1,0,0,0,1'
    LC_ALL=C xrandr --prop > "$HOME/ecg-display-fixed-properties.txt"
    exec /usr/bin/python3 "$HOME/display_probe.py"
fi
if [ -n "${DISPLAY:-}" ]; then
    printf '%s\n' 'Exit the current graphical session before this test.' >&2
    exit 2
fi
case "$(tty 2>/dev/null || true)" in
    /dev/tty[0-9]*) ;;
    *) printf '%s\n' 'A physical text console is required.' >&2; exit 2 ;;
esac
printf '%s\n' 'Testing display with corrected colours for 45 seconds.'
probe_status=0
startx /bin/sh "$HOME/display_probe_fixed.sh" --xclient -- -nolisten tcp > "$HOME/ecg-display-probe-fixed-x.log" 2>&1 || probe_status=$?
printf 'Test exit code: %s\nLog: %s/ecg-display-probe-fixed-x.log\n' "$probe_status" "$HOME"
exit "$probe_status"
