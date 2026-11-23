"""Small device lifecycle helpers; importing this module performs no actions."""
from pathlib import Path
import subprocess
import sys


def save_problem(snapshot):
    if snapshot.get('error'):
        return str(snapshot['error'])
    if snapshot.get('pending') or int(snapshot.get('received') or 0) != int(snapshot.get('saved') or 0):
        return '仍有数据未完成保存，请检查记录和存储空间'
    recording = snapshot.get('recording') or {}
    if recording.get('error') or recording.get('rejected'):
        return str(recording.get('error') or '有记录数据未能写入')
    if snapshot.get('session_id') and recording and not recording.get('finished'):
        return '记录文件尚未关闭'
    return None


def poweroff_supported():
    if not sys.platform.startswith('linux'):
        return False
    try:
        return b'Raspberry Pi' in Path('/proc/device-tree/model').read_bytes()
    except OSError:
        return False


def request_poweroff():
    """Use existing Pi permission only; never prompt for a password in the UI."""
    if not poweroff_supported():
        raise RuntimeError('安全关机仅在树莓派上可用；笔记本不会关机')
    try:
        result = subprocess.run(['sudo', '-n', '/usr/bin/systemctl', 'poweroff'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('关机指令未完成，可退出界面后在树莓派命令行关机') from exc
    if result.returncode:
        raise RuntimeError('系统未允许关机。记录已保留；请退出界面后执行 sudo poweroff。')
