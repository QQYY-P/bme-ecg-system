#!/bin/sh
# A one-session comparison with Xorg acceleration disabled.
set -eu
umask 077
if [ "$(id -u)" -eq 0 ] || [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]; then
    printf '%s\n' 'Run on the Raspberry Pi physical console as pi2, without sudo.' >&2
    exit 2
fi
if [ -n "${DISPLAY:-}" ]; then
    printf '%s\n' 'Exit the current graphical session before this display test.' >&2
    exit 2
fi
case "$(tty 2>/dev/null || true)" in
    /dev/tty[0-9]*) ;;
    *) printf '%s\n' 'A physical text console is required.' >&2; exit 2 ;;
esac
test -r /etc/X11/ecg-probe-noglamor.conf
test -r "$HOME/display_probe.py"
printf '%s\n' 'Starting a 45-second display test without graphics acceleration.'
probe_status=0
startx /usr/bin/python3 "$HOME/display_probe.py" -- -config ecg-probe-noglamor.conf -nolisten tcp > "$HOME/ecg-display-probe-noglamor-x.log" 2>&1 || probe_status=$?
printf 'Test exit code: %s\nLog: %s/ecg-display-probe-noglamor-x.log\n' "$probe_status" "$HOME"
exit "$probe_status"
