"""Password-free loopback bridge, using two forwarding-only SSH identities.

PC :8768 -> cloud 127.0.0.1:8766; Pi 127.0.0.1:8766 -> PC :8768.
All listeners bind only to loopback; unknown or changed host keys are refused.
"""
from datetime import datetime
import json
from pathlib import Path
import socket
import threading
import time
import urllib.request
import paramiko
from cloud_link import LocalForwarder, relay, healthy_cloud_service

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / '.cloud_private'
STOP = threading.Event()
STATE_LOCK = threading.Lock()
STATE = {}


def report(part, status, error=None):
    with STATE_LOCK:
        STATE[part] = {'status': status, 'time': datetime.now().astimezone().isoformat(), 'error': error}
        path = PRIVATE / 'auto_link_status.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)


def connect(host, user, key):
    client = paramiko.SSHClient()
    client.load_host_keys(str(PRIVATE / 'known_hosts'))
    try:
        client.connect(host, username=user, key_filename=str(PRIVATE / key),
                       look_for_keys=False, allow_agent=False, timeout=8,
                       auth_timeout=8, banner_timeout=8)
        client.get_transport().set_keepalive(10)
        return client
    except Exception:
        client.close()
        raise


def ping_transport(transport):
    """Request a reply; a silent cable loss must not look connected forever."""
    finished = threading.Event()
    def ping():
        try:
            transport.global_request('keepalive@openssh.com', wait=True)
        finally:
            finished.set()
    threading.Thread(target=ping, daemon=True).start()
    if not finished.wait(8) or not transport.is_active():
        transport.close()
        raise OSError('SSH keepalive timed out')


def cloud_loop():
    delay = 2
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while not STOP.is_set():
        client = forwarder = None
        try:
            report('cloud', 'connecting')
            client = connect('62.234.150.104', 'ecg-tunnel', 'cloud_tunnel')
            forwarder = LocalForwarder(('127.0.0.1', 8768))
            forwarder.start(client.get_transport())
            while not STOP.is_set():
                if not client.get_transport().is_active():
                    raise OSError('SSH connection closed')
                with opener.open('http://127.0.0.1:8768/health', timeout=5) as response:
                    health = json.load(response)
                if not healthy_cloud_service(health):
                    raise ValueError('Unexpected cloud service')
                report('cloud', 'ready')
                delay = 2
                STOP.wait(10)
        except Exception as exc:
            report('cloud', 'retrying', type(exc).__name__)
        finally:
            if forwarder:
                forwarder.close()
            if client:
                client.close()
        STOP.wait(delay)
        delay = min(delay * 2, 30)


def pi_loop():
    delay = 2
    while not STOP.is_set():
        client = None
        peers = set()
        lock = threading.Lock()
        connection_stop = threading.Event()

        def forward(channel, origin, server):
            def worker():
                local = None
                try:
                    local = socket.create_connection(('127.0.0.1', 8768), 5)
                    with lock:
                        peers.update((channel, local))
                    relay(channel, local, connection_stop)
                except OSError:
                    channel.close()
                finally:
                    if local:
                        local.close()
                    with lock:
                        peers.discard(channel)
                        peers.discard(local)
            threading.Thread(target=worker, daemon=True).start()

        try:
            report('pi', 'connecting')
            client = connect('192.168.137.59', 'ecg-relay', 'pi_tunnel')
            transport = client.get_transport()
            transport.request_port_forward('127.0.0.1', 8766, handler=forward)
            report('pi', 'ready')
            delay = 2
            last_check = time.monotonic()
            while not STOP.wait(2):
                if not transport.is_active():
                    raise OSError('Pi connection closed')
                if time.monotonic() - last_check >= 10:
                    ping_transport(transport)
                    last_check = time.monotonic()
        except Exception as exc:
            report('pi', 'retrying', type(exc).__name__)
        finally:
            connection_stop.set()
            with lock:
                for peer in peers:
                    peer.close()
            if client:
                client.close()
        STOP.wait(delay)
        delay = min(delay * 2, 30)


def main():
    guard = socket.socket()
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
        guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        guard.bind(('127.0.0.1', 18768))
    except OSError:
        guard.close()
        return
    threads = [threading.Thread(target=target, daemon=True) for target in (cloud_loop, pi_loop)]
    try:
        for thread in threads:
            thread.start()
        while not STOP.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        for thread in threads:
            thread.join(12)
        guard.close()


if __name__ == '__main__':
    main()
