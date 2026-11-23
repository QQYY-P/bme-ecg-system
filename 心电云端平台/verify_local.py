"""Real HTTP smoke test and export comparison, with a saved evidence report."""
import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from demo import create_demo
from protocol import decode_chunk
from uploader import Uploader, UploadError, atomic_json

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8766')
    args = parser.parse_args()
    source = create_demo(ROOT / 'demo_data', seconds=30)
    client = Uploader(args.url, ROOT / 'upload_state')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': 'local_http_synthetic_only',
              'session_id': source.name, 'sample_rate_hz': 360, 'seconds': 30}
    try:
        Uploader('http://127.0.0.1:1', ROOT/'evidence/offline_state', timeout=.2).sync(source)
        raise AssertionError('Offline endpoint unexpectedly succeeded')
    except UploadError:
        report['offline_attempt_failed_without_deleting_source'] = (source/'index.sqlite').exists()
    first = client.sync(source)
    repeated = client.sync(source)
    with closing(sqlite3.connect(source/'index.sqlite')) as db:
        descriptors = [json.loads(r[0]) for r in db.execute('SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
    desc = descriptors[0]
    raw = (source/'chunks'/f'{desc["chunk_id"]:08d}.bin').read_bytes()
    duplicate = client.request('PUT',f'/api/sessions/{source.name}/chunks/{desc["chunk_id"]}',{
        'payload_base64':base64.b64encode(raw).decode(), **{k:desc[k] for k in
        ('sha256','sample_count','first_sample_id','last_sample_id','received_first_utc','received_last_utc')}})
    detail = client.request('GET',f'/api/sessions/{source.name}')
    import urllib.request
    request=urllib.request.Request(args.url+f'/api/sessions/{source.name}/export',headers={'X-API-Key':client.token})
    with client.opener.open(request,timeout=10) as response:
        exported = response.read()
    rows = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))
    original = []
    for d in descriptors:
        original.extend(decode_chunk((source/'chunks'/f'{d["chunk_id"]:08d}.bin').read_bytes(),d['sha256']))
    assert len(rows)==len(original)==10800
    for a,b in zip(original,rows):
        assert tuple(int(b[k]) for k in ('sample_id','segment_id','segment_index','sequence','raw_adc','flags','mode'))==a
        assert b['source']=='demo' and b['signal_origin']=='local_simulated'
    assert first['complete'] and repeated['sent_chunks']==0 and duplicate['duplicate']
    assert detail['sample_count']==10800 and detail['chunk_count']==len(descriptors)
    assert detail['model_status']=='not_configured' and detail['model_result'] is None
    report.update(uploaded_samples=detail['sample_count'],uploaded_chunks=detail['chunk_count'],
        resumed_upload_completed=True,duplicate_acknowledged=True,duplicate_did_not_increase_count=True,
        csv_all_rows_equal=True,export_sha256=hashlib.sha256(exported).hexdigest(),model_status='not_configured',
        source_directory=str(source),service_url=args.url)
    evidence=ROOT/'evidence';evidence.mkdir(exist_ok=True)
    (evidence/'verified_export.csv').write_bytes(exported)
    atomic_json(evidence/'local_http_verification.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
