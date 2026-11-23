"""Reversible tty1 standby boot setup. Run only on the Pi; never restarts a session."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tempfile

BEGIN = '# BEGIN ECG STANDBY (managed)'
END = '# END ECG STANDBY (managed)'
STATE = Path('/var/lib/ecg-standby/install.json')
DROPIN = Path('/etc/systemd/system/getty@tty1.service.d/zz-ecg-standby.conf')


def atomic_write(path, content, mode=0o644, owner=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError('拒绝覆盖符号链接：' + str(path))
    fd, tmp = tempfile.mkstemp(prefix='.ecg-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        if owner is not None and hasattr(os, 'chown'):
            os.chown(tmp, *owner)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def profile_path(home):
    # bash reads the first existing login profile, not necessarily .profile.
    for name in ('.bash_profile', '.bash_login', '.profile'):
        candidate = Path(home) / name
        if candidate.is_symlink():
            raise RuntimeError('登录文件是符号链接，请人工检查：' + str(candidate))
        if candidate.exists():
            return candidate
    return Path(home) / '.profile'


def fragment(app):
    launcher = shlex.quote(str(Path(app) / 'start-device.sh'))
    return ('\n' + BEGIN + '\n'
            'if [ -z "${SSH_CONNECTION:-}${SSH_TTY:-}${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] &&\n'
            '   [ "${ECG_SKIP_AUTOSTART:-0}" != 1 ] && [ "$(tty 2>/dev/null)" = /dev/tty1 ]; then\n'
            f'    /bin/sh {launcher}\n'
            "    printf '%s\\n' '心电界面已退出，可在此命令行操作。'\n"
            'fi\n' + END + '\n').encode('utf-8')


def install(home, app, dropin, state, user, owner=None):
    home, app, dropin, state = map(Path, (home, app, dropin, state))
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', user):
        raise ValueError('请使用普通 Linux 用户名')
    block = fragment(app)
    unit = ('[Service]\nExecStart=\n'
            f'ExecStart=-/sbin/agetty --autologin {user} --noclear %I $TERM\n').encode()
    if state.exists():
        saved = json.loads(state.read_text(encoding='utf-8'))
        profile = Path(saved['profile'])
        if (saved['user'] == user and saved['app'] == str(app) and dropin.exists()
                and dropin.read_bytes() == unit and profile.read_bytes().count(block) == 1):
            return '已配置，无需重复安装'
        raise RuntimeError('已有配置与当前文件不同；请先检查或卸载旧配置')
    if dropin.exists() or dropin.is_symlink():
        raise RuntimeError('目标启动配置已存在，未覆盖：' + str(dropin))
    profile = profile_path(home)
    original = profile.read_bytes() if profile.exists() else b''
    if BEGIN.encode() in original or END.encode() in original:
        raise RuntimeError('登录文件中已有待机标记，但备份缺失，请人工检查')
    metadata = profile.stat() if profile.exists() else None
    mode = stat.S_IMODE(metadata.st_mode) if metadata else 0o644
    file_owner = (metadata.st_uid, metadata.st_gid) if metadata and os.name == 'posix' else owner
    saved = dict(user=user, app=str(app), profile=str(profile), dropin=str(dropin),
                 original=base64.b64encode(original).decode(), profile_existed=profile.exists(),
                 mode=mode, owner=file_owner, block=block.decode(), unit=unit.decode())
    # Save recovery information BEFORE modifying either boot file.
    atomic_write(state, json.dumps(saved, ensure_ascii=False, indent=2).encode('utf-8'), 0o600)
    atomic_write(profile, original + block, mode, file_owner)
    atomic_write(dropin, unit)
    return '已配置；当前会话不变，下次启动进入全屏待机'


def uninstall(state):
    state = Path(state)
    if not state.exists():
        return '没有本工具的配置，无需卸载'
    saved = json.loads(state.read_text(encoding='utf-8'))
    profile, dropin = Path(saved['profile']), Path(saved['dropin'])
    if profile.is_symlink() or dropin.is_symlink():
        raise RuntimeError('配置被替换为符号链接，未自动修改')
    block = saved['block'].encode('utf-8')
    original = base64.b64decode(saved['original'])
    current = profile.read_bytes() if profile.exists() else b''
    if dropin.exists() and dropin.read_bytes() != saved['unit'].encode():
        raise RuntimeError('系统启动配置已被修改，未自动覆盖，请人工检查')
    if current.count(block) == 1:
        restored = current.replace(block, b'', 1)
    elif current == original:
        restored = current  # interrupted installation, no profile mutation
    else:
        raise RuntimeError('登录文件中的待机段已变化，未自动覆盖，请人工检查')
    if not saved['profile_existed'] and not restored:
        profile.unlink(missing_ok=True)
    else:
        atomic_write(profile, restored, saved['mode'], saved['owner'])
    dropin.unlink(missing_ok=True)
    state.unlink()
    return '已移除待机启动段，保留原配置和其他编辑；下次启动生效'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'uninstall', 'status'))
    parser.add_argument('--user', default=os.environ.get('SUDO_USER'))
    args = parser.parse_args(argv)
    if os.name != 'posix' or not Path('/proc/device-tree/model').exists() or b'Raspberry Pi' not in Path('/proc/device-tree/model').read_bytes():
        parser.error('此工具只在树莓派上执行，不修改笔记本启动设置')
    if os.geteuid() != 0:
        parser.error('需要 sudo，仅用于配置启动文件；应用仍以普通用户运行')
    if args.action == 'status':
        print('已配置（文件存在，实际开机另行验收）' if STATE.exists() else '尚未配置')
        return 0
    if args.action == 'uninstall':
        print(uninstall(STATE))
    else:
        import pwd
        if not args.user or args.user == 'root':
            parser.error('必须指定普通用户，例如 --user pi2')
        account = pwd.getpwnam(args.user)
        if account.pw_uid == 0 or Path(account.pw_shell).name != 'bash':
            parser.error('只支持使用 bash 的普通用户')
        for program in ('startx', 'flock', 'xrandr'):
            if not shutil.which(program):
                parser.error('缺少依赖：' + program)
        if not Path('/sbin/agetty').exists():
            parser.error('未找到 /sbin/agetty')
        target = subprocess.check_output(['systemctl', 'get-default'], text=True).strip()
        if target != 'multi-user.target':
            parser.error('此工具用于当前 Lite 文字控制台系统；已有桌面启动，请先检查启动方式')
        home = Path(account.pw_dir)
        # Avoid mixing a second launcher into an existing custom login setup.
        if not STATE.exists():
            for path in (profile_path(home), home / '.bashrc'):
                if path.exists() and re.search(r'^\s*(?!#).*\b(startx|start-screen\.sh|ecg-screen\.sh)\b', path.read_text(errors='replace'), re.M):
                    parser.error('发现已有图形启动命令，请先核对：' + str(path))
        app = Path(__file__).resolve().parents[1]
        print(install(home, app, DROPIN, STATE, args.user, (account.pw_uid, account.pw_gid)))
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('未重启、未中断当前采集。请在数据保存完成后自行重启验证。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
