"""Read committed terminal recordings and upload them with restart-safe retries.

Standard library only: this script and protocol.py can run on the Raspberry Pi.
Never modifies the acquisition database or its raw files.
"""
import argparse
import base64
from contextlib import closing
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .protocol import decode_chunk
from .measurement_transfer import upload_summary

ROOT = Path(__file__).resolve().parent


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class UploadError(Exception):
    retryable = False
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code

class RetryableUploadError(UploadError):
    retryable = True

class Cancelled(Exception):
    pass


class Uploader:
    def __init__(self, url, state_dir=None, token=None, timeout=5, cancelled=lambda: False, progress=lambda done, total: None):
        self.url = url.rstrip('/')
        parsed = urlparse(self.url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path:
            raise ValueError('服务地址须为 http(s)://主机:端口，不含路径或账号')
        self.state_dir = Path(state_dir or ROOT / 'upload_state')
        self.token = os.getenv('ECG_API_TOKEN', '') if token is None else token
        if token is None and not self.token:
            credentials = Path(os.getenv('ECG_DEVICE_TOKEN_FILE', str(self.state_dir.parent/'device-credentials.json')))
            if credentials.exists():
                if credentials.is_symlink() or credentials.stat().st_size > 4096:
                    raise ValueError('设备凭证文件无效')
                self.token = json.loads(credentials.read_text(encoding='utf-8'))['token']
                if not isinstance(self.token, str) or not 16 <= len(self.token) <= 256:
                    raise ValueError('设备凭证格式无效')
        self.timeout = timeout
        self.cancelled = cancelled
        self.progress = progress
        # Remember validation, never the raw payload. Bound memory for long
        # sessions and re-check file identity on every pass.
        self._verified = OrderedDict()
        self.validation_cache_limit = 4096
        self.stats = dict(validated_chunks=0, reused_chunks=0, raw_bytes_read=0)
        # Do not use ambient HTTP proxy settings for local/LAN collection.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @staticmethod
    def _fingerprint(path, desc):
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                desc['sha256'], desc['sample_count'], desc['first_sample_id'], desc['last_sample_id'])

    def _read_validated(self, path, desc, fingerprint):
        raw = path.read_bytes()
        rows = decode_chunk(raw, desc['sha256'])
        if len(rows) != desc['sample_count'] or rows[0][0] != desc['first_sample_id'] or rows[-1][0] != desc['last_sample_id']:
            raise ValueError('源索引与数据不一致')
        if self._fingerprint(path, desc) != fingerprint:
            raise ValueError('读取期间源数据块发生变化，请重新校验')
        self._verified[path] = fingerprint
        self._verified.move_to_end(path)
        while len(self._verified) > self.validation_cache_limit:
            self._verified.popitem(last=False)
        self.stats['validated_chunks'] += 1
        self.stats['raw_bytes_read'] += len(raw)
        return raw

    def request(self, method, path, payload=None):
        if self.cancelled():
            raise Cancelled()
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8') if payload is not None else None
        req = urllib.request.Request(self.url + path, data=body, method=method,
            headers={'Content-Type': 'application/json', 'X-API-Key': self.token})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise UploadError("云端响应过大")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get('detail', '请求失败')
            except (ValueError, OSError):
                detail = '请求失败'
            error = RetryableUploadError if exc.code >= 500 or exc.code == 429 or (exc.code == 409 and path.endswith('/analysis')) else UploadError
            raise error(f'HTTP {exc.code}: {detail}', status_code=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RetryableUploadError('无法连接服务，原始记录已保留，恢复后可重试') from exc

    def sync(self, session_dir):
        directory = Path(session_dir).resolve()
        # Metadata and committed descriptors are a single consistent DB snapshot.
        dbpath = directory / 'index.sqlite'
        with closing(sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.execute('BEGIN')
            found = db.execute('SELECT session_id,metadata_json,status,sample_count FROM sessions').fetchall()
            if len(found) != 1:
                raise ValueError('源目录必须包含一个会话')
            sid, raw_meta, source_status, total = found[0]
            meta = json.loads(raw_meta)
            descriptors = [json.loads(row[0]) for row in db.execute('SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
            measurement = upload_summary(db, dict(meta, status=source_status)) if source_status != 'recording' else None
        if sid != directory.name:
            raise ValueError('会话目录与索引编号不同')
        # Register before consulting local progress: cloud data may have been reset.
        registration = {
            'source': meta.get('source', 'unknown'), 'sample_rate_hz': meta.get('sample_rate_hz', 360),
            'vref_v': meta.get('vref_v', meta.get('vref', 3.3)), 'started_utc': meta['started_utc'],
            'source_status': source_status}
        if meta.get('provenance') is not None:
            registration['provenance'] = meta['provenance']
        if meta.get('cloud_owner_id'):
            # Never silently publish an account-bound record to a legacy shared server.
            health = self.request('GET', '/health')
            if not health.get('auth_required'):
                raise UploadError('云端尚未启用账号隔离，此记录暂不上传')
            registration['owner_id'] = meta['cloud_owner_id']
        remote = self.request('PUT', f'/api/sessions/{sid}', registration)
        detail = self.request('GET', f'/api/sessions/{sid}')
        remote_chunks = {c['chunk_id']: c for c in detail['chunks']}
        key = hashlib.sha256((self.url + '|' + str(directory)).encode('utf-8')).hexdigest()[:20]
        state_path = self.state_dir / f'{key}.json'
        state = {'service': self.url, 'session_id': sid, 'confirmed_chunks': [], 'complete': False}
        sent = duplicates = 0
        checkpoint_sent, checkpoint_at = 0, time.monotonic()
        self.progress(0, len(descriptors))
        next_progress = time.monotonic() + .2
        for desc in descriptors:
            if self.cancelled():
                raise Cancelled()
            chunk_id = desc['chunk_id']
            if type(chunk_id) is not int or not 0 <= chunk_id <= 99999999:
                raise ValueError('源数据块编号无效')
            path = directory / 'chunks' / f'{chunk_id:08d}.bin'
            receipt = remote_chunks.get(chunk_id)
            fingerprint = self._fingerprint(path, desc)
            if receipt:
                if receipt['sha256'] != desc['sha256'] or receipt['sample_count'] != desc['sample_count']:
                    raise UploadError('云端存在不同内容的同编号数据块')
                if self._verified.get(path) == fingerprint:
                    self._verified.move_to_end(path)
                    self.stats['reused_chunks'] += 1
                else:
                    self._read_validated(path, desc, fingerprint)
                duplicates += 1
            else:
                # A server reset must still cause a fresh read/hash before
                # retransmission, even when validation was cached locally.
                raw = self._read_validated(path, desc, fingerprint)
                receipt = self.request('PUT', f'/api/sessions/{sid}/chunks/{chunk_id}', {
                    'payload_base64': base64.b64encode(raw).decode('ascii'),
                    **{k: desc[k] for k in ('sha256','sample_count','first_sample_id','last_sample_id','received_first_utc','received_last_utc')}})
                if any(receipt.get(k) != value for k, value in
                       {'session_id': sid, 'chunk_id': chunk_id, 'sha256': desc['sha256'], 'sample_count': desc['sample_count']}.items()):
                    raise UploadError('接收确认不匹配，未推进本地进度')
                sent += 1
            state['confirmed_chunks'].append(chunk_id)
            done = len(state['confirmed_chunks'])
            if time.monotonic() >= next_progress or done == len(descriptors):
                self.progress(done, len(descriptors))
                next_progress = time.monotonic() + .2
            if done % 16 == 0:
                # Yield between batches; avoid a tight catch-up loop competing
                # with drawing and acquisition for the interpreter.
                time.sleep(.002)
            # Remote receipts are authoritative after restart. Checkpoint in
            # batches rather than forcing an SD-card flush for every block.
            if sent != checkpoint_sent and (sent - checkpoint_sent >= 10 or time.monotonic() - checkpoint_at >= 2):
                atomic_json(state_path, state)
                checkpoint_sent, checkpoint_at = sent, time.monotonic()
        if source_status != 'recording':
            completion = {
                'chunks': [{k: d[k] for k in ('chunk_id','sha256','sample_count')} for d in descriptors],
                'sample_count': total, 'source_status': source_status}
            if measurement is not None:
                completion['measurement_summary'] = measurement
            result = self.request('POST', f'/api/sessions/{sid}/complete', completion)
            state['complete'] = result['upload_status'] == 'complete'
            remote = result
        atomic_json(state_path, state)
        return {'session_id': sid, 'sent_chunks': sent, 'already_received_chunks': duplicates,
                'confirmed_samples': sum(d['sample_count'] for d in descriptors),
                'complete': state['complete'], 'model_status': remote['model_status']}


def main():
    parser = argparse.ArgumentParser(description='上传树莓派终端已保存的会话，不运行模型')
    parser.add_argument('--session-dir', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8766')
    parser.add_argument('--state-dir', type=Path, default=ROOT / 'upload_state')
    parser.add_argument('--watch', action='store_true', help='每5秒检查并补传，Ctrl+C停止')
    args = parser.parse_args()
    client = Uploader(args.url, args.state_dir)
    while True:
        try:
            print(json.dumps(client.sync(args.session_dir), ensure_ascii=False), flush=True)
        except (UploadError, ValueError, OSError, sqlite3.Error) as exc:
            print(f'上传未完成：{exc}', flush=True)
            if not args.watch:
                raise SystemExit(1)
        if not args.watch:
            break
        time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('上传器已停止，本地采集和记录不受影响。')
