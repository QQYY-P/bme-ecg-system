"""Create a verified, read-only uploader index for an existing hardware recording.

This is a historical relay from the PC's saved copy, not a new acquisition. It
never changes the source database/blocks, never calls a network API, and never
replaces acquisition timestamps with the preparation time.
"""
from __future__ import annotations

from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import decode_chunk, signal_origin, valid

SESSION_ID = 'pi_serial_20261027_015950_e3d04b'
EVIDENCE = ROOT / 'evidence' / 'pi-link'
DESTINATION = ROOT / 'historical_sources' / SESSION_ID


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def collect_verified_source():
    verified = load_json(EVIDENCE / 'verification.json')
    board = load_json(EVIDENCE / 'board-result.json')
    require(verified['session_id'] == board['session_id'] == SESSION_ID, 'Evidence session mismatch')
    require(board['source'] == 'serial' and board['status'] == 'complete', 'Not a completed serial recording')
    require(board['error'] is None and board['loaded_model_modules'] == [], 'Original board verification failed')
    for field in ('pi_manifest_equals_pc', 'every_saved_block_sha256_matches_pi', 'csv_all_rows_equal'):
        require(verified[field] is True, f'Original verification is missing: {field}')
    dbpath = ROOT / 'data' / 'cloud.sqlite'
    with closing(sqlite3.connect(dbpath.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        session_row = db.execute('SELECT * FROM sessions WHERE id=?', (SESSION_ID,)).fetchone()
        require(session_row is not None, 'Historical session is absent from the PC database')
        session = dict(session_row)
        chunks = [dict(row) for row in db.execute(
            'SELECT * FROM chunks WHERE session_id=? ORDER BY chunk_id', (SESSION_ID,))]
    meta = json.loads(session['metadata'])
    require(session['complete'] == 1 and meta['source_status'] == 'complete', 'Source recording is not complete')
    require(meta['source'] == 'serial' and meta['sample_rate_hz'] == 360, 'Source/rate mismatch')
    manifest = [{k: c[k] for k in ('chunk_id', 'sha256', 'sample_count')} for c in chunks]
    require(manifest == board['manifest'], 'PC manifest differs from original Pi manifest')
    require(len(chunks) == verified['chunks'], 'Chunk count differs from original verification')
    expected_samples = verified['samples']
    require(session['sample_count'] == board['sample_count'] == expected_samples, 'Recorded sample counts differ')
    raw_by_chunk = {}
    descriptors = []
    original = []
    previous_id = None
    counts = dict(real_adc=0, board_simulated=0, unknown=0, valid=0, invalid=0)
    chunk_root = ROOT / 'data' / 'sessions' / SESSION_ID / 'chunks'
    for item in chunks:
        cid = item['chunk_id']
        require(type(cid) is int and 0 <= cid <= 99999999, 'Invalid chunk ID')
        path = chunk_root / f'{cid:08d}.bin'
        require(path.is_file() and not path.is_symlink(), f'Not a regular chunk file: {cid}')
        raw = path.read_bytes()
        rows = decode_chunk(raw, item['sha256'])
        descriptor = json.loads(item['descriptor'])
        require(descriptor['sha256'] == item['sha256'], f'Descriptor hash mismatch: {cid}')
        require(len(rows) == descriptor['sample_count'] == item['sample_count'], f'Chunk point count mismatch: {cid}')
        require(rows[0][0] == descriptor['first_sample_id'] == item['first_id'], f'First ordinal mismatch: {cid}')
        require(rows[-1][0] == descriptor['last_sample_id'] == item['last_id'], f'Last ordinal mismatch: {cid}')
        require(previous_id is None or rows[0][0] > previous_id, 'Chunk ordinal ranges overlap or descend')
        previous_id = rows[-1][0]
        raw_by_chunk[cid] = raw
        descriptors.append(dict(descriptor, chunk_id=cid))
        original.extend(rows)
        for row in rows:
            counts[{0: 'real_adc', 1: 'board_simulated'}.get(row[-1], 'unknown')] += 1
            counts['valid' if valid(row) else 'invalid'] += 1
    require(len(original) == expected_samples, 'Decoded total differs from the verified total')
    require(counts == board['counts'] == verified['source_counts'], 'Decoded origin/quality counts differ')
    require(counts['valid'] == session['valid_count'], 'Valid sample count differs from PC index')
    export = (EVIDENCE / 'board-export.csv').read_bytes()
    require(sha256(export) == verified['export_sha256'], 'Original verified CSV has changed')
    exported = list(csv.DictReader(io.StringIO(export.decode('utf-8-sig'))))
    require(len(exported) == len(original), 'Verified CSV length mismatch')
    for raw_row, csv_row in zip(original, exported):
        values = tuple(int(csv_row[k]) for k in
                       ('sample_id', 'segment_id', 'segment_index', 'sequence', 'raw_adc', 'flags'))
        mode = int(csv_row['mode']) if csv_row['mode'] else None
        require(values + (mode,) == raw_row, 'Verified CSV differs from original binary row')
        require(csv_row['source'] == 'serial' and csv_row['signal_origin'] == signal_origin('serial', mode),
                'Verified CSV source label mismatch')
        require(csv_row['session_id'] == SESSION_ID, 'Verified CSV session mismatch')
    offset = 0
    for descriptor in descriptors:
        for csv_row in exported[offset:offset + descriptor['sample_count']]:
            for key in ('received_first_utc', 'received_last_utc'):
                require(csv_row[key] == descriptor[key], f'Original block timestamp mismatch: {key}')
        offset += descriptor['sample_count']
    return session, descriptors, raw_by_chunk, manifest, counts, verified, board


def verify_existing(destination, session, descriptors, raw_by_chunk):
    with closing(sqlite3.connect((destination / 'index.sqlite').resolve().as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT session_id,metadata_json,status,sample_count FROM sessions').fetchall()
        require(rows == [(SESSION_ID, session['metadata'], 'complete', session['sample_count'])],
                'Existing relay index metadata differs; no files were replaced')
        actual = [json.loads(r[0]) for r in db.execute('SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
        require(actual == descriptors, 'Existing relay descriptors differ; no files were replaced')
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Relay SQLite integrity check failed')
    for cid, raw in raw_by_chunk.items():
        require((destination / 'chunks' / f'{cid:08d}.bin').read_bytes() == raw, f'Relay chunk mismatch: {cid}')
    report = load_json(destination / 'historical-relay.json')
    require(report['kind'] == 'historical_relay_from_pc_cloud_copy' and report['session_id'] == SESSION_ID,
            'Existing relay provenance is missing or differs')
    require(report['original_started_utc'] == json.loads(session['metadata'])['started_utc'],
            'Relay acquisition timestamp was changed')
    return report


def prepare():
    session, descriptors, raw_by_chunk, manifest, counts, verified, board = collect_verified_source()
    if DESTINATION.exists():
        report = verify_existing(DESTINATION, session, descriptors, raw_by_chunk)
        return report
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects any existing copy. An interrupted preparation is
    # never silently overwritten: the next run validates and reports its problem.
    DESTINATION.mkdir(mode=0o700)
    (DESTINATION / 'chunks').mkdir(mode=0o700)
    for cid, raw in raw_by_chunk.items():
        with (DESTINATION / 'chunks' / f'{cid:08d}.bin').open('xb') as out:
            out.write(raw)
    temporary_db = DESTINATION / 'index.preparing.sqlite'
    with closing(sqlite3.connect(temporary_db)) as db, db:
        db.execute('CREATE TABLE sessions (session_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL, '
                   'status TEXT NOT NULL, sample_count INTEGER NOT NULL)')
        db.execute('CREATE TABLE chunks (chunk_id INTEGER PRIMARY KEY, descriptor_json TEXT NOT NULL)')
        db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                   (SESSION_ID, session['metadata'], 'complete', session['sample_count']))
        db.executemany('INSERT INTO chunks VALUES (?,?)',
                       [(d['chunk_id'], json.dumps(d, ensure_ascii=False, sort_keys=True)) for d in descriptors])
    temporary_db.rename(DESTINATION / 'index.sqlite')
    report = {
        'kind': 'historical_relay_from_pc_cloud_copy',
        'note': '历史真实开发板串口记录转存；未重新采集。实采模式前段为无效标记，后段为板端模拟。',
        'session_id': SESSION_ID,
        'prepared_at': datetime.now(timezone.utc).isoformat(),
        'original_started_utc': json.loads(session['metadata'])['started_utc'],
        'original_source': 'serial',
        'original_pi_directory': board['directory'],
        'pc_source_database': str(ROOT / 'data' / 'cloud.sqlite'),
        'pc_first_received_at': session['created_at'],
        'pc_last_received_at': session['received_at'],
        'uploader_source_directory': str(DESTINATION),
        'index_note': 'index.sqlite是供只读上传器读取的新索引，不是树莓派原始索引文件。原始时间、块与采样字段保留。',
        'samples': session['sample_count'], 'chunks': len(descriptors),
        'counts': counts, 'manifest': manifest,
        'matches_original_pi_manifest': True,
        'all_chunk_bytes_and_sha256_verified': True,
        'csv_all_sampling_fields_equal': True,
        'original_verified_export_sha256': verified['export_sha256'],
        'evidence_sha256': {name: sha256((EVIDENCE / name).read_bytes())
                            for name in ('verification.json', 'board-result.json')},
        'model_status': 'not_configured',
        'no_network_upload_performed': True,
    }
    (DESTINATION / 'historical-relay.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_existing(DESTINATION, session, descriptors, raw_by_chunk)
    # Upload progress goes elsewhere. The uploader opens this index with mode=ro.
    for path in [DESTINATION / 'index.sqlite', DESTINATION / 'historical-relay.json',
                 *(DESTINATION / 'chunks').glob('*.bin')]:
        path.chmod(stat.S_IRUSR)
    return report


if __name__ == '__main__':
    result = prepare()
    print(json.dumps({k: result[k] for k in (
        'kind', 'session_id', 'original_started_utc', 'uploader_source_directory', 'samples', 'chunks',
        'counts', 'matches_original_pi_manifest', 'all_chunk_bytes_and_sha256_verified',
        'csv_all_sampling_fields_equal', 'no_network_upload_performed')}, ensure_ascii=False, indent=2))
