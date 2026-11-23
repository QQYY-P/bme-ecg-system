"""Verify the Pi manifest, stored bytes and every exported CSV row over HTTP."""
import base64
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import urllib.request

from protocol import decode_chunk, signal_origin, valid
from uploader import Uploader, atomic_json

ROOT = Path(__file__).resolve().parent


def main():
    evidence = ROOT / 'evidence/pi-link'
    source = json.loads((evidence/'board-result.json').read_text(encoding='utf-8'))
    sid = source['session_id']
    assert source['error'] is None, source['error']
    assert source['source'] == 'serial'
    assert source['loaded_model_modules'] == []
    client = Uploader('http://127.0.0.1:8766')
    detail = client.request('GET', f'/api/sessions/{sid}')
    expected = source['manifest']
    actual = [{k:d[k] for k in ('chunk_id','sha256','sample_count')} for d in detail['chunks']]
    assert actual == expected, 'Pi and PC block manifests differ'
    assert detail['sample_count'] == source['sample_count'] > 0
    assert detail['upload_status'] == 'complete'
    original = []
    for item in expected:
        path = ROOT/'data/sessions'/sid/'chunks'/f'{item["chunk_id"]:08d}.bin'
        original.extend(decode_chunk(path.read_bytes(), item['sha256']))
    request = urllib.request.Request(client.url+f'/api/sessions/{sid}/export')
    with client.opener.open(request, timeout=15) as response:
        exported = response.read()
    rows = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))
    assert len(rows) == len(original) == source['sample_count']
    for a,b in zip(original,rows):
        values = tuple(int(b[k]) for k in ('sample_id','segment_id','segment_index','sequence','raw_adc','flags'))
        assert values == a[:-1]
        assert (int(b['mode']) if b['mode'] else None) == a[-1]
        assert b['source'] == 'serial' and b['signal_origin'] == signal_origin('serial',a[-1])
    assert sum(valid(r) for r in original) == source['counts']['valid'] == detail['valid_count']
    first = detail['chunks'][0]
    raw = (ROOT/'data/sessions'/sid/'chunks'/f'{first["chunk_id"]:08d}.bin').read_bytes()
    duplicate = client.request('PUT',f'/api/sessions/{sid}/chunks/{first["chunk_id"]}',{
        'payload_base64':base64.b64encode(raw).decode(),
        'sha256':first['sha256'],'sample_count':first['sample_count'],
        'first_sample_id':original[0][0], 'last_sample_id':original[first['sample_count']-1][0],
        'received_first_utc':rows[0]['received_first_utc'],
        'received_last_utc':rows[0]['received_last_utc']})
    assert duplicate['duplicate']
    assert client.request('GET',f'/api/sessions/{sid}')['sample_count'] == source['sample_count']
    analysis = client.request('GET',f'/api/sessions/{sid}/analysis')
    assert analysis['model_status'] == 'not_configured' and analysis['model_result'] is None
    report = {'checked_at':datetime.now(timezone.utc).isoformat(), 'session_id':sid,
        'path':'STM32 USB serial -> Raspberry Pi -> reverse SSH HTTP tunnel -> PC SQLite and Web',
        'samples':len(rows),'chunks':len(expected),'seconds':len(rows)/360,
        'source_counts':source['counts'],'pi_manifest_equals_pc':True,
        'every_saved_block_sha256_matches_pi':True,'csv_all_rows_equal':True,
        'duplicate_acknowledged_without_increase':True,'model_status':'not_configured',
        'loaded_model_modules':source['loaded_model_modules'],
        'export_sha256':hashlib.sha256(exported).hexdigest()}
    (evidence/'board-export.csv').write_bytes(exported)
    atomic_json(evidence/'verification.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
