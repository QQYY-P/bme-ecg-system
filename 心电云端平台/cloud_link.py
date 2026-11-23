"""Local SSH tunnel, with an explicit first-install option for the course service.

The password stays in memory. Unknown host keys require a human fingerprint
confirmation, and changed known keys are refused. No model is imported here.
"""
import argparse
import base64
from datetime import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import queue
import re
import shlex
import select
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.request
import webbrowser
import zipfile

import paramiko

ROOT = Path(__file__).resolve().parent
LOCAL_ADDRESS = ('127.0.0.1', 8767)
REMOTE_ADDRESS = ('127.0.0.1', 8766)
WEB_URL = 'http://127.0.0.1:8767/'
HOST_KEYS = ROOT / '.cloud_private' / 'known_hosts'


def fingerprint(key):
    digest = hashlib.sha256(key.asbytes()).digest()
    return 'SHA256:' + base64.b64encode(digest).decode('ascii').rstrip('=')


def relay(left, right, stop):
    """Copy bytes in either direction and close both endpoints on termination."""
    try:
        left.settimeout(5)
        right.settimeout(5)
        while not stop.is_set():
            readable, _, _ = select.select([left, right], [], [], .5)
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    except (OSError, EOFError, ValueError, paramiko.SSHException):
        pass
    finally:
        left.close()
        right.close()


class LocalForwarder:
    """Own the listening socket and every active forwarding connection."""
    def __init__(self, address=LOCAL_ADDRESS):
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.connections = set()
        self.workers = set()
        self.transport = None
        self.accept_thread = None
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Windows must not let another process share this local port.
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.listener.bind(address)
            self.listener.listen(16)
            self.listener.settimeout(.5)
        except Exception:
            self.listener.close()
            raise

    @property
    def address(self):
        return self.listener.getsockname()

    def start(self, transport):
        self.transport = transport
        self.accept_thread = threading.Thread(target=self._accept, daemon=True,
                                              name='cloud-tunnel-listener')
        self.accept_thread.start()

    def _accept(self):
        while not self.stop.is_set():
            try:
                connection, peer = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                if self.stop.is_set():
                    connection.close()
                    break
                self.connections.add(connection)
                worker = threading.Thread(target=self._forward, args=(connection, peer),
                                          daemon=True, name='cloud-tunnel-stream')
                self.workers.add(worker)
                worker.start()

    def _forward(self, connection, peer):
        channel = None
        try:
            if self.stop.is_set():
                return
            channel = self.transport.open_channel('direct-tcpip', REMOTE_ADDRESS,
                                                  peer, timeout=5)
            if channel is None:
                return
            with self.lock:
                if self.stop.is_set():
                    return
                self.connections.add(channel)
            relay(connection, channel, self.stop)
        except (OSError, EOFError, paramiko.SSHException):
            pass
        finally:
            connection.close()
            if channel is not None:
                channel.close()
            with self.lock:
                self.connections.discard(connection)
                self.connections.discard(channel)
                self.workers.discard(threading.current_thread())

    def close(self):
        self.stop.set()
        self.listener.close()
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            connection.close()
        if self.accept_thread and self.accept_thread.ident is not None and self.accept_thread is not threading.current_thread():
            self.accept_thread.join(1)
        with self.lock:
            workers = list(self.workers)
        for worker in workers:
            if worker.ident is not None:
                worker.join(.2)


class ConfirmHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, link):
        self.link = link

    def missing_host_key(self, client, hostname, key):
        reply = {'ready': threading.Event(), 'accepted': False}
        self.link.events.put({'stage': 'trust', 'host': hostname,
                              'key_type': key.get_name(), 'fingerprint': fingerprint(key),
                              'reply': reply})
        while not reply['ready'].wait(.1):
            if self.link.stop.is_set():
                raise paramiko.SSHException('Host confirmation cancelled')
        if not reply['accepted'] or self.link.stop.is_set():
            raise paramiko.SSHException('Host confirmation declined')
        if HOST_KEYS.parent.is_symlink() or HOST_KEYS.is_symlink():
            raise OSError('Host key path must not be a symbolic link')
        HOST_KEYS.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(HOST_KEYS))
        HOST_KEYS.chmod(0o600)


def healthy_cloud_service(health):
    return (isinstance(health, dict) and health.get('ok') is True and health.get('service') == 'ecg-course-demo'
            and health.get('deployment') == 'cloud'
            and health.get('model_status') in ('not_configured', 'ready')
            and 'model_result' in health
            and (health['model_status'] == 'ready' or health['model_result'] is None))


def service_state(opener):
    with opener.open(WEB_URL + 'health', timeout=4) as response:
        if response.status != 200:
            return False
        body = response.read(65537)
    if len(body) > 65536:
        return False
    health = json.loads(body)
    return healthy_cloud_service(health)


class DeployError(Exception):
    pass


class CloudLink:
    def __init__(self, events, deploy=False):
        self.events = events
        self.stop = threading.Event()
        self.client = paramiko.SSHClient()
        self.forwarder = None
        self.thread = None
        self.deploy = deploy

    def report(self, stage, message):
        # Callers supply fixed messages, never passwords, raw exceptions or replies.
        self.events.put({'stage': stage, 'message': message})
        try:
            directory = ROOT / 'logs'
            directory.mkdir(exist_ok=True)
            with (directory/'cloud-link.log').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'time': datetime.now().astimezone().isoformat(),
                                         'stage': stage, 'message': message},
                                        ensure_ascii=False) + '\n')
        except OSError:
            pass

    def disconnect(self):
        self.stop.set()
        self.client.close()
        if self.forwarder:
            self.forwarder.close()

    def remote_command(self, command, phase, timeout=300, required=True):
        """Run only deployment commands; never send the login password to a shell."""
        self.report('deploying', phase)
        channel = self.client.get_transport().open_session(timeout=15)
        channel.settimeout(20)
        captured = bytearray()
        deadline = time.monotonic() + timeout
        try:
            channel.exec_command(command)
            while True:
                for ready, receive in ((channel.recv_ready, channel.recv),
                                       (channel.recv_stderr_ready, channel.recv_stderr)):
                    while ready():
                        part = receive(32768)
                        if not part:
                            break
                        if len(captured) < 200000:
                            captured.extend(part[:200000-len(captured)])
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if self.stop.wait(.1):
                    raise DeployError('部署已停止，云端已生成的文件保留。')
                if time.monotonic() >= deadline:
                    raise DeployError(phase + '超时，已保留云端文件，请检查日志。')
            code = channel.recv_exit_status()
        finally:
            channel.close()
        output = captured.decode('utf-8', errors='replace')
        # New-server setup output has no credentials. Still redact credential-like
        # lines and userinfo URLs before retaining diagnostics.
        safe_lines = []
        for line in output.splitlines():
            if re.search(r'password|passwd|token|secret|authorization', line, re.I):
                safe_lines.append('[已省略可能包含凭证的输出行]')
            else:
                safe_lines.append(re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@', r'\1[凭证已省略]@', line))
        directory = ROOT/'logs'
        directory.mkdir(exist_ok=True)
        with (directory/'cloud-deploy.log').open('a', encoding='utf-8') as log:
            log.write(f'\n{datetime.now().astimezone().isoformat()} {phase} [exit={code}]\n')
            log.write('\n'.join(safe_lines) + '\n')
        if required and code != 0:
            raise DeployError(f'{phase}失败（退出码{code}），文件已保留；请查看logs/cloud-deploy.log。')
        return code, output

    def remote_health(self):
        script = ("import json,urllib.request; "
                  "op=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
                  "r=op.open('http://127.0.0.1:8766/health',timeout=4); "
                  "print(r.read(65536).decode())")
        code, output = self.remote_command('python3 -c ' + shlex.quote(script),
                                           '检查云端服务', timeout=15, required=False)
        try:
            health = json.loads(output) if code == 0 else {}
        except ValueError:
            return False
        return healthy_cloud_service(health)

    def deploy_new(self):
        probe = ("import json,os,pwd; from pathlib import Path; "
                 "print(json.dumps({'uid':os.getuid(),'user':pwd.getpwuid(os.getuid()).pw_name,'home':str(Path.home())}))")
        _, output = self.remote_command('python3 -c ' + shlex.quote(probe), '核对云主机用户', timeout=15)
        try:
            account = json.loads(output)
            remote_home = account['home']
            username = account['user']
            if account['uid'] == 0 or remote_home == '/root':
                raise DeployError('请使用Ubuntu普通用户登录部署，当前root用户不会被修改。')
            if not remote_home.startswith('/home/') or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', username):
                raise DeployError('云主机用户主目录不符合本课程部署约定，未安装。')
        except (ValueError, KeyError, TypeError):
            raise DeployError('无法核对云主机用户，未安装。') from None
        remote_app = remote_home + '/ecg-cloud'
        with self.client.open_sftp() as sftp:
            try:
                sftp.lstat(remote_app)
                exists = True
            except FileNotFoundError:
                exists = False
        if exists:
            if self.remote_health():
                self.report('deploying', '发现已有正常的课程服务，直接复用，不覆盖文件。')
                return
            raise DeployError('~/ecg-cloud已存在但服务未通过检查，未覆盖；请先检查原部署。')
        archive = ROOT/'deploy/dist/ecg-cloud-linux.zip'
        if not archive.is_file():
            raise DeployError('缺少本机部署包，请先运行deploy/package_cloud.py。')
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None or any(not name.startswith('ecg-cloud/') or '..' in name.split('/')
                                                 for name in bundle.namelist()):
                raise DeployError('部署包校验失败，未上传。')
        content = archive.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        remote_archive = remote_home + '/ecg-cloud-upload-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '.zip'
        self.report('deploying', '上传课程部署包（不包含采样数据、密码或模型）…')
        with self.client.open_sftp() as sftp:
            with sftp.file(remote_archive, 'wx') as output:
                output.write(content)
        self.remote_command('sudo -n true', '检查免密安装权限', timeout=15)
        self.remote_command('sudo -n apt-get update', '更新Ubuntu软件目录')
        self.remote_command('sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv unzip',
                            '安装Python运行组件')
        check = shlex.quote(digest + '  ' + remote_archive)
        command = ("printf '%s\\n' " + check + ' | sha256sum --check --quiet && '
                   'test ! -e ' + shlex.quote(remote_app) + ' && test ! -L ' + shlex.quote(remote_app) +
                   ' && unzip -n ' + shlex.quote(remote_archive) + ' -d ' + shlex.quote(remote_home))
        self.remote_command(command, '校验并解压新部署包', timeout=30)
        self.remote_command('bash ' + shlex.quote(remote_app + '/deploy/linux/install.sh'), '安装课程服务依赖')
        self.remote_command('sudo -n loginctl enable-linger ' + shlex.quote(username), '启用当前用户后台服务', timeout=20)
        self.remote_command('systemctl --user enable --now ecg-cloud.service', '启动云端课程服务', timeout=30)
        for attempt in range(5):
            if self.remote_health():
                self.report('deploying', '云端部署完成，上传服务正常，模型状态见网页。')
                return
            if self.stop.wait(1):
                break
        raise DeployError('服务已安装但健康检查未通过，文件已保留，请检查cloud-deploy.log。')

    def run(self, host, username, password_box):
        password = password_box.pop()
        try:
            self.forwarder = LocalForwarder()
            system_keys = Path.home()/'.ssh'/'known_hosts'
            if system_keys.exists():
                self.client.load_system_host_keys(str(system_keys))
            if HOST_KEYS.exists():
                self.client.load_host_keys(str(HOST_KEYS))
            self.client.set_missing_host_key_policy(ConfirmHostKey(self))
            self.report('connecting', '正在连接云服务器；首次连接需要你核对主机指纹。')
            self.client.connect(host, port=22, username=username, password=password,
                                allow_agent=False, look_for_keys=False, timeout=8,
                                banner_timeout=10, auth_timeout=15)
            password = None
            if self.stop.is_set():
                return
            transport = self.client.get_transport()
            transport.set_keepalive(15)
            if self.deploy:
                self.deploy_new()
            self.forwarder.start(transport)
            self.report('connected', '加密通道已连接，正在核对云端服务。')
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            previous = None
            while not self.stop.is_set():
                if not transport.is_active():
                    self.report('error', '云服务器连接已断开，请重新连接。')
                    break
                try:
                    healthy = service_state(opener)
                except (OSError, ValueError, urllib.error.URLError):
                    healthy = False
                if healthy != previous:
                    self.report('ready' if healthy else 'warning',
                                '云端服务正常 · 模型状态见网页 · 网页地址：127.0.0.1:8767'
                                if healthy else '通道已建立，但云端服务尚未通过检查；请确认服务已部署并启动。')
                    previous = healthy
                self.stop.wait(5)
        except paramiko.BadHostKeyException:
            self.report('error', '主机密钥与已保存记录不一致，已拒绝连接；请先在云控制台核实。')
        except paramiko.AuthenticationException:
            self.report('error', '登录未通过，请核对用户名和密码；密码没有保存。')
        except DeployError as exc:
            self.report('error', str(exc))
        except (OSError, paramiko.SSHException, EOFError):
            if not self.stop.is_set():
                self.report('error', '连接未完成：请检查地址、SSH访问、指纹确认及本机8767端口是否被占用。')
        except Exception:
            self.report('error', '连接程序遇到异常，已关闭通道；密码没有保存。')
        finally:
            password = None
            password_box.clear()
            self.disconnect()
            self.events.put({'stage': 'stopped'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='', help='预填云服务器IP；密码仅在窗口输入')
    parser.add_argument('--deploy', action='store_true', help='首次连接时安装课程云端服务；已有目录不覆盖')
    args = parser.parse_args()
    root = tk.Tk()
    root.title(('安装并连接云服务器' if args.deploy else '连接云服务器') + ' · 心电课程演示')
    root.geometry('620x430')
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='电脑 → 云端心电网页', font=('Microsoft YaHei', 17, 'bold')).pack(anchor='w')
    ttk.Label(frame, text=('首次安装课程服务并建立加密通道 · 模型状态见网页' if args.deploy else
                          '只建立加密访问通道 · 模型状态见网页')).pack(anchor='w', pady=(8, 16))
    fields = ttk.Frame(frame)
    fields.pack(fill='x')
    host_value, user_value, password_value = tk.StringVar(value=args.host), tk.StringVar(value='ubuntu'), tk.StringVar()
    entries = []
    for row, (label, value, secret) in enumerate((('云服务器IP', host_value, False),
                                                ('登录用户名', user_value, False),
                                                ('登录密码', password_value, True))):
        ttk.Label(fields, text=label, width=12).grid(row=row, column=0, sticky='w', pady=5)
        entry = ttk.Entry(fields, textvariable=value, width=49, show='●' if secret else '')
        entry.grid(row=row, column=1, sticky='ew', pady=5)
        entries.append(entry)
    ttk.Label(frame, text='密码只用于本次登录，不写入文件或日志。').pack(anchor='w', pady=(7, 12))
    status = tk.StringVar(value='请填写云服务器信息。连接成功后保持此窗口打开。')
    ttk.Label(frame, textvariable=status, wraplength=565).pack(anchor='w', pady=8)
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=10)
    events = queue.Queue()
    current = {'link': None, 'closing': False, 'trust_window': None, 'error': False}

    def connect():
        if current['link'] and current['link'].thread.is_alive():
            return
        try:
            host = str(ipaddress.ip_address(host_value.get().strip()))
        except ValueError:
            status.set('请输入云控制台显示的有效IP地址。')
            return
        username = user_value.get().strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', username):
            status.set('请填写有效的Linux登录用户名。')
            return
        if not password_value.get():
            status.set('请在此窗口输入登录密码。')
            return
        password_box = [password_value.get()]
        password_value.set('')
        current['error'] = False
        for entry in entries:
            entry.configure(state='disabled')
        start.configure(state='disabled')
        link = CloudLink(events, deploy=args.deploy)
        current['link'] = link
        link.thread = threading.Thread(target=link.run, args=(host, username, password_box),
                                       daemon=True, name='cloud-ssh-connection')
        link.thread.start()

    def disconnect(close_window=False):
        if close_window:
            current['closing'] = True
        link = current['link']
        if link and link.thread.is_alive():
            status.set('正在断开通道并释放本机端口…')
            threading.Thread(target=link.disconnect, daemon=True).start()
        elif close_window:
            root.destroy()

    start = ttk.Button(buttons, text='连接云服务器', command=connect)
    start.pack(side='left')
    ttk.Button(buttons, text='打开云端网页', command=lambda: webbrowser.open(WEB_URL)).pack(side='left', padx=10)
    ttk.Button(buttons, text='断开', command=disconnect).pack(side='right')
    root.protocol('WM_DELETE_WINDOW', lambda: disconnect(True))
    entries[-1].bind('<Return>', lambda _: connect())

    def trust(item):
        reply = item['reply']
        if current['closing'] or current['link'].stop.is_set():
            reply['ready'].set()
            return
        dialog = tk.Toplevel(root)
        current['trust_window'] = dialog
        dialog.title('首次连接：核对云主机指纹')
        dialog.transient(root)
        dialog.resizable(False, False)
        inner = ttk.Frame(dialog, padding=22)
        inner.pack(fill='both', expand=True)
        message = ('请与云控制台或可信的管理员核对以下主机指纹。\n'
                   '确认后只保存这台主机的公钥，不保存登录密码。\n\n'
                   f"服务器：{item['host']}\n类型：{item['key_type']}\n指纹：{item['fingerprint']}")
        ttk.Label(inner, text=message, wraplength=610).pack(anchor='w', pady=(0, 20))
        actions = ttk.Frame(inner)
        actions.pack(fill='x')

        def answer(accepted):
            reply['accepted'] = accepted
            reply['ready'].set()
            current['trust_window'] = None
            dialog.destroy()

        ttk.Button(actions, text='取消连接', command=lambda: answer(False)).pack(side='right')
        ttk.Button(actions, text='已核对，信任此指纹', command=lambda: answer(True)).pack(side='left')
        dialog.protocol('WM_DELETE_WINDOW', lambda: answer(False))
        dialog.grab_set()

    def poll():
        while not events.empty():
            item = events.get_nowait()
            if item['stage'] == 'trust':
                trust(item)
            elif item.get('message'):
                status.set(item['message'])
                if item['stage'] == 'error':
                    current['error'] = True
        link = current['link']
        if link and not link.thread.is_alive():
            if current['trust_window']:
                current['trust_window'].destroy()
                current['trust_window'] = None
            current['link'] = None
            if current['closing']:
                root.destroy()
                return
            for entry in entries:
                entry.configure(state='normal')
            start.configure(state='normal')
            if link.stop.is_set() and not current['error']:
                status.set('通道已断开，本机8767端口已释放；云端服务和记录不受影响。')
        root.after(200, poll)

    poll()
    root.mainloop()


if __name__ == '__main__':
    main()
