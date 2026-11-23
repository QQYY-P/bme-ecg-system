"""Bounded clock inspection/actions, intended to be called off the GUI thread.

Importing this module changes nothing. Only the explicit action functions can
request a clock change, using noninteractive sudo and fixed argument lists.
Manual time is user supplied, never represented as network-verified time.
"""
from __future__ import annotations

from datetime import datetime, timezone
import platform
import re
import subprocess
import threading


COMMAND_TIMEOUT_SECONDS = 4
_MANUAL = None
_LOCK = threading.Lock()
_LOCAL_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?\Z")


def _snapshot():
    utc = datetime.now(timezone.utc)
    local = utc.astimezone()
    return {
        "trusted": False,
        "source": "system_unverified",
        "state": "unknown",
        "utc": utc.isoformat(timespec="milliseconds"),
        "local": local.isoformat(timespec="milliseconds"),
        "timezone": local.tzname() or "unknown",
        "utc_offset_seconds": int(local.utcoffset().total_seconds()),
        "detail": "系统时间尚未经过网络校验",
        "ntp_enabled": None,
        "ntp_synchronized": None,
    }


def _run(arguments):
    return subprocess.run(arguments, capture_output=True, text=True,
                          timeout=COMMAND_TIMEOUT_SECONDS, check=False,
                          stdin=subprocess.DEVNULL, shell=False)


def _error(exc):
    if isinstance(exc, subprocess.TimeoutExpired):
        return "时间服务响应超时"
    return str(exc).strip()[:500] or "时间服务不可用"


def _command_error(completed):
    return ((completed.stderr or completed.stdout or "操作未完成").strip()[:500])


def _requires_authorization(detail):
    detail = detail.casefold()
    return "password is required" in detail or "a terminal is required" in detail


def authorize_time(password):
    """Refresh this process user's temporary sudo credential timestamp only.

    The password is passed solely on standard input. It is never logged, stored,
    included in a command argument, or copied into the returned error details.
    This grants no permanent rule and does not itself change the system clock.
    """
    if (not isinstance(password, str) or not password or len(password) > 1024
            or any(character in password for character in ("\r", "\n", "\0"))):
        return {"success": False, "detail": "请输入有效的系统账户密码（不含换行）"}
    if platform.system() != "Linux":
        return {"success": False, "detail": "当前系统不支持此临时时间授权方式"}
    try:
        completed = subprocess.run(["sudo", "-S", "-p", "", "-v"],
                                   input=password + "\n", capture_output=True,
                                   text=True, timeout=COMMAND_TIMEOUT_SECONDS,
                                   check=False, shell=False)
    except subprocess.TimeoutExpired:
        return {"success": False, "detail": "临时授权超时，请重试"}
    except (OSError, subprocess.SubprocessError):
        return {"success": False, "detail": "临时授权失败，无法访问系统授权服务"}
    if completed.returncode != 0:
        return {"success": False, "detail": "临时授权失败，请确认系统账户密码及账户权限"}
    return {"success": True, "detail": "临时授权完成；尚未修改系统时间"}


def clock_status():
    """Return a timestamp with evidence/provenance, without changing the clock."""
    result = _snapshot()
    if platform.system() != "Linux":
        result["detail"] = "使用本机系统时间；本程序尚未核验本机网络校时状态"
        return result
    try:
        completed = _run(["timedatectl", "show", "-p", "NTPSynchronized",
                          "-p", "NTP", "-p", "Timezone"])
        if completed.returncode != 0:
            raise OSError(_command_error(completed))
        properties = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
        for output, property_name in (("ntp_enabled", "NTP"), ("ntp_synchronized", "NTPSynchronized")):
            value = properties.get(property_name)
            result[output] = True if value == "yes" else False if value == "no" else None
        if properties.get("Timezone"):
            result["timezone"] = properties["Timezone"]
        # An old synchronization bit may remain briefly after NTP is disabled.
        if result["ntp_synchronized"] is True and result["ntp_enabled"] is not False:
            result.update(trusted=True, source="ntp", state="synchronized", detail="系统已确认网络时间同步")
        else:
            with _LOCK:
                manual = dict(_MANUAL) if _MANUAL is not None else None
            if manual:
                result.update(source="manual", state="manual", detail="已手动设置时间，尚未经过网络校验", manual=manual)
            elif result["ntp_enabled"] is True:
                result.update(state="pending", detail="已启用网络校时，等待系统确认同步")
            elif result["ntp_enabled"] is False:
                result.update(state="unsynchronized", detail="网络校时未启用，当前时间未验证")
            else:
                result["detail"] = "时间服务未提供可确认的同步状态"
    except (OSError, subprocess.SubprocessError) as exc:
        result["detail"] = "无法确认网络校时状态：" + _error(exc)
        with _LOCK:
            if _MANUAL is not None:
                result.update(source="manual", state="manual", manual=dict(_MANUAL))
    return result


def request_time_sync():
    """Enable NTP, then query once; pending is not a successful synchronization."""
    if platform.system() != "Linux":
        result = _snapshot()
        result.update(success=False, requested=False, state="unsupported", authorization_required=False,
                      detail="请在当前操作系统的日期和时间设置中同步时间")
        return result
    try:
        completed = _run(["sudo", "-n", "timedatectl", "set-ntp", "true"])
        if completed.returncode != 0:
            raise OSError(_command_error(completed))
    except (OSError, subprocess.SubprocessError) as exc:
        result = _snapshot()
        result.update(success=False, requested=False, state="error",
                      authorization_required=_requires_authorization(_error(exc)),
                      detail="无法启用网络校时：" + _error(exc))
        return result
    result = clock_status()
    result.update(requested=True, success=result["trusted"], authorization_required=False)
    if not result["trusted"]:
        result.update(state="pending", detail="已请求网络校时，尚未确认同步；请检查联网后稍候重试。" + result["detail"])
    return result


def manual_set_time(iso_datetime_local):
    """Set a local wall-clock value (YYYY-MM-DD HH:MM[:SS]) on Linux.

    Any enabled NTP is disabled before manual setting. If any step fails after
    requesting that change, restoring its previous enabled state is attempted,
    including when the disable command times out after partially taking effect.
    The GUI
    should show the returned detail and let the user choose network sync again.
    No timezone or sampling timestamps from existing recordings are rewritten.
    """
    global _MANUAL
    try:
        if not isinstance(iso_datetime_local, str) or not _LOCAL_ISO.fullmatch(iso_datetime_local):
            raise ValueError("请使用 YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS 的本地时间")
        parsed = datetime.fromisoformat(iso_datetime_local)
        target = parsed.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError) as exc:
        result = _snapshot()
        result.update(success=False, state="invalid", authorization_required=False, detail=str(exc))
        return result
    if platform.system() != "Linux":
        result = _snapshot()
        result.update(success=False, state="unsupported", authorization_required=False,
                      detail="请在当前操作系统的日期和时间设置中手动设置时间")
        return result
    before = clock_status()
    disabled = False
    attempted_disable = False
    try:
        if before["ntp_enabled"] is True:
            # The service may apply this request before the subprocess reports
            # success (or times out); compensation must depend on the attempt.
            attempted_disable = True
            completed = _run(["sudo", "-n", "timedatectl", "set-ntp", "false"])
            if completed.returncode != 0:
                raise OSError(_command_error(completed))
            disabled = True
        completed = _run(["sudo", "-n", "timedatectl", "set-time", target])
        if completed.returncode != 0:
            raise OSError(_command_error(completed))
    except (OSError, subprocess.SubprocessError) as exc:
        detail = "手动设置时间失败：" + _error(exc)
        outcome_uncertain = isinstance(exc, subprocess.TimeoutExpired) or (attempted_disable and not disabled)
        if outcome_uncertain:
            detail += "；操作结果不确定，系统可能已执行部分更改，请重新检查时间和校时状态"
        restore_succeeded = None
        if attempted_disable:
            try:
                restored = _run(["sudo", "-n", "timedatectl", "set-ntp", "true"])
                restore_succeeded = restored.returncode == 0
                if restore_succeeded:
                    detail += "；恢复网络校时开关的命令已完成（尚未确认时间同步）"
                else:
                    outcome_uncertain = True
                    detail += "；恢复网络校时开关失败，当前开关状态不确定"
            except (OSError, subprocess.SubprocessError):
                outcome_uncertain = True
                detail += "；恢复网络校时开关的请求未确认完成，当前开关状态不确定"
        result = _snapshot()
        result.update(success=False, state="error", detail=detail,
                      outcome_uncertain=outcome_uncertain,
                      ntp_restore_attempted=attempted_disable,
                      ntp_restore_succeeded=restore_succeeded,
                      authorization_required=_requires_authorization(_error(exc)))
        return result
    result = _snapshot()
    with _LOCK:
        _MANUAL = {"requested_local": target, "applied_at_utc": result["utc"],
                   "timezone": before["timezone"]}
        result["manual"] = dict(_MANUAL)
    result.update(success=True, trusted=False, source="manual", state="manual", authorization_required=False,
                  ntp_enabled=False if disabled else before["ntp_enabled"],
                  ntp_synchronized=False,
                  detail="已手动设置本地时间，尚未经过网络校验" + ("；网络校时已关闭" if disabled else ""))
    return result
