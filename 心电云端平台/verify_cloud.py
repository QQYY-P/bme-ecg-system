"""Check a terminal recording against the real cloud service through an SSH tunnel."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import urllib.request

from protocol import decode_chunk, signal_origin
from uploader import Uploader, atomic_json

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8767')
    parser.add_argument('--expected-model', choices=('not_configured','ready'), default='not_configured')
    args = parser.parse_args()
    source = args.session_dir.resolve()
    client = Uploader(args.url, ROOT/'upload_state/cloud')
    health = client.request('GET','/health')
    assert health['deployment'] == 'cloud', 'The target does not identify itself as the cloud service'
    assert health['model_status'] == args.expected_model
    assert health['model_result'] is None, 'Health must not contain per-session model results'
    first = client.sync(source)
    assert first['complete'], 'Finish the source session before validating its complete export'
    repeated = client.sync(source)
    assert repeated['sent_chunks'] == 0
    with closing(sqlite3.connect((source/'index.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
        sid, raw_meta, total = db.execute('SELECT session_id,metadata_json,sample_count FROM sessions').fetchone()
        descriptors = [json.loads(r[0]) for r in db.execute('SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
    metadata = json.loads(raw_meta)
    detail = client.request('GET',f'/api/sessions/{sid}')
    expected = [{k:d[k] for k in ('chunk_id','sha256','sample_count')} for d in descriptors]
    assert detail['chunks'] == expected and detail['sample_count'] == total
    original = []
    for d in descriptors:
        original.extend(decode_chunk((source/'chunks'/f'{d["chunk_id"]:08d}.bin').read_bytes(),d['sha256']))
    request = urllib.request.Request(client.url+f'/api/sessions/{sid}/export',headers={'X-API-Key':client.token})
    with client.opener.open(request,timeout=30) as response:
        exported = response.read()
    rows = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))
    assert len(rows) == len(original) == total > 0
    for a,b in zip(original,rows):
        assert tuple(int(b[k]) for k in ('sample_id','segment_id','segment_index','sequence','raw_adc','flags')) == a[:-1]
        assert (int(b['mode']) if b['mode'] else None) == a[-1]
        assert b['source'] == metadata['source']
        assert b['signal_origin'] == signal_origin(metadata['source'],a[-1])
    analysis = client.request('GET',f'/api/sessions/{sid}/analysis')
    assert analysis['model_status'] == args.expected_model
    if args.expected_model == 'not_configured':
        assert analysis['model_result'] is None
    evidence = ROOT/'evidence/cloud'
    evidence.mkdir(parents=True,exist_ok=True)
    exported_path = evidence/(sid+'.csv')
    exported_path.write_bytes(exported)
    report = {'checked_at':datetime.now(timezone.utc).isoformat(),'session_id':sid,
        'source':metadata['source'],'samples':total,'chunks':len(expected),'service_url':args.url,
        'deployment':health['deployment'],'manifest_matches_source':True,'csv_all_rows_equal':True,
        'repeated_upload_sent_no_new_blocks':True,'model_status':health['model_status'],
        'model_analysis_status':analysis.get('analysis_status','not_configured'),
        'export_sha256':hashlib.sha256(exported).hexdigest(),'export_file':str(exported_path)}
    atomic_json(evidence/(sid+'-verification.json'),report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
