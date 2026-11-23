"""Manage local accounts. Password input is hidden; device secrets go to a new file."""
import argparse
import getpass
import json
import os
from pathlib import Path

from server import create_app


def main():
    parser = argparse.ArgumentParser(description='心电账号管理（在云服务器本机运行）')
    parser.add_argument('--data-dir', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    user = commands.add_parser('create-user', help='创建用户；首个管理账号添加 --admin')
    user.add_argument('username'); user.add_argument('--admin', action='store_true')
    key = commands.add_parser('device-key', help='轮换设备凭证并写入新文件，不显示密钥')
    key.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app = create_app(args.data_dir, auth_enabled=True)
    if args.command == 'create-user':
        first = getpass.getpass('密码（至少 10 个字符）：')
        if first != getpass.getpass('再次输入密码：'): parser.error('两次密码不同')
        try: result = app.state.auth.create_user(args.username, first, args.admin)
        except ValueError as exc: parser.error(str(exc))
        print('已创建账号：'+result['username']+('（管理员）' if result['is_admin'] else ''))
    else:
        # O_EXCL prevents overwriting an existing credential or following a symlink.
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            token = app.state.auth.create_device_key(os.getenv('ECG_DEVICE_ID','pi2-demo'))
            json.dump({'token': token}, file); file.flush(); os.fsync(file.fileno())
        print('新凭证已写入指定文件，旧设备凭证已失效。请通过现有 SSH 安全通道复制到树莓派。')


if __name__ == '__main__': main()
