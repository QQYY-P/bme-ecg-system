"""Read-only acceptance of one new Pi capture and its cloud copy.

All remote requests are GET. No capture, upload, model analysis, clock adjustment,
credential lookup, or cloud mutation is performed. Only local verification output
is written after the source and run evidence have been checked.
"""
import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import urllib.parse
import urllib.request

from protocol import decode_chunk, signal_origin, valid
from uploader import atomic_json


ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def instant(value):
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(moment.tzinfo is not None, 'Evidence timestamp must contain a timezone')
    return moment.astimezone(timezone.utc)


def close_number(actual, expected, tolerance=1e-6):
    return (type(actual) in (int, float) and type(expected) in (int, float)
            and math.isfinite(actual) and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance))


def same_provenance(actual, expected):
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return False
    for key, value in expected.items():
        other = actual[key]
        if key == 'clock_checked_at' and value is not None and other is not None:
            if instant(other) != instant(value):
                return False
        elif key == 'clock_offset_seconds' and value is not None:
            if not close_number(other, value):
                return False
        elif other != value:
            return False
    return True


class ReadOnlyClient:
    def __init__(self, url, timeout=30):
        self.url = url.rstrip('/')
        address = urllib.parse.urlparse(self.url)
        require(address.scheme in ('http', 'https') and address.hostname and not address.username
                and not address.password and not address.path and not address.query and not address.fragment,
                'Expected an HTTP service base URL without credentials or a path')
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.requests = []

    def get(self, path, raw=False):
        require(path.startswith('/') and not path.startswith('//'), 'Invalid relative API path')
        self.requests.append({'method': 'GET', 'path': path})
        request = urllib.request.Request(self.url + path, method='GET')
        with self.opener.open(request, timeout=self.timeout) as response:
            require(response.status == 200, 'GET did not return HTTP 200')
            body = response.read(64 * 1024 * 1024 + 1)
        require(len(body) <= 64 * 1024 * 1024, 'Response exceeds the verification size bound')
        return body if raw else json.loads(body)


def inspect_local_run(run_dir):
    board = read_json(run_dir/'board-result.json')
    copied = read_json(run_dir/'source-copy.json')
    outage = read_json(run_dir/'outage-test.json')
    provenance = read_json(run_dir/'run-provenance.json')
    status = read_json(run_dir/'status.json')
    events = [json.loads(line) for line in (run_dir/(run_dir.name+'.jsonl')).read_text(encoding='utf-8').splitlines()
              if line.strip()]
    require(status.get('stage') == 'finished' and status.get('exit_code') == 0,
            'Capture/download process has not finished successfully')
    require(board.get('acquisition_mode') == 'new_serial_capture' and board.get('source') == 'serial'
            and board.get('status') == 'complete' and board.get('error') is None,
            'Expected a newly acquired complete serial session, not an attached/historical recording')
    require(board.get('loaded_model_modules') == [], 'Independent collector unexpectedly loaded a model module')
    require(all(copied.get(key) is True for key in ('download_complete', 'all_file_hashes_match', 'complete_recording')),
            'Source copy has not passed its download checks')
    sid = board['session_id']
    require(isinstance(sid, str) and re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}', sid), 'Invalid session ID')
    require(copied.get('session_id') == sid and copied.get('source_status') == 'complete', 'Copied source identity differs')
    source = Path(copied['local_source_directory']).resolve()
    require(source == (ROOT/'cloud_sources'/run_dir.name/sid).resolve(), 'Source copy is outside this run directory')
    snapshot = read_json(source/'source-manifest.json')
    require(snapshot.get('session_id') == sid and snapshot.get('source_status') == 'complete'
            and snapshot.get('snapshot_method') == 'sqlite_readonly_backup', 'Source snapshot is incomplete or unexpected')
    with closing(sqlite3.connect((source/'index.sqlite').as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Source index integrity check failed')
        sessions = db.execute('SELECT session_id,metadata_json,status,sample_count FROM sessions').fetchall()
        require(len(sessions) == 1, 'Source index must contain one session')
        source_id, metadata_json, source_status, total = sessions[0]
        descriptors = [json.loads(row[0]) for row in db.execute('SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
    metadata = json.loads(metadata_json)
    require(source_id == sid and source_status == 'complete' and type(total) is int and total > 0,
            'Source index is not the completed run')
    require(metadata.get('source') == 'serial' and metadata.get('sample_rate_hz') == 360, 'Unexpected acquisition source/rate')
    manifest = [{key: item[key] for key in ('chunk_id', 'sha256', 'sample_count')} for item in descriptors]
    require(1 <= len(manifest) <= 300, 'Source chunk count exceeds this capture/download contract')
    require(manifest == board['manifest'] == copied['board_manifest'] == copied['snapshot_manifest'] == snapshot['manifest'],
            'Board, copy and original index manifests differ')
    require(total == board['sample_count'] == copied['samples'] == snapshot['sample_count'], 'Source sample counts differ')
    allowed = {'index.sqlite', 'session.json'}
    original = []
    counts = dict(real_adc=0, board_simulated=0, unknown=0, valid=0, invalid=0)
    valid_real_adc = 0
    previous_id = None
    for item in descriptors:
        cid = item['chunk_id']
        require(type(cid) is int and 0 <= cid <= 99999999, 'Invalid source chunk id')
        base = source/'chunks'/f'{cid:08d}'
        allowed.update((f'chunks/{cid:08d}.bin', f'chunks/{cid:08d}.json'))
        require(read_json(base.with_suffix('.json')) == item, 'Chunk descriptor differs from source index')
        rows = decode_chunk(base.with_suffix('.bin').read_bytes(), item['sha256'])
        require(len(rows) == item['sample_count'] and rows[0][0] == item['first_sample_id']
                and rows[-1][0] == item['last_sample_id'], 'Decoded block range/count differs from source index')
        require(previous_id is None or rows[0][0] > previous_id, 'Source chunks overlap or descend')
        previous_id = rows[-1][0]
        original.extend(rows)
        for row in rows:
            counts[{0: 'real_adc', 1: 'board_simulated'}.get(row[-1], 'unknown')] += 1
            counts['valid' if valid(row) else 'invalid'] += 1
            valid_real_adc += int(valid(row) and row[-1] == 0)
    require(set(snapshot['files']) == allowed, 'Snapshot file manifest is incomplete or contains unexpected paths')
    for name, digest in snapshot['files'].items():
        path = source/name
        require(not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                f'Downloaded source hash changed: {name}')
    require(len(original) == total and counts == board['counts'], 'Decoded source totals/origin counts differ')
    require(same_provenance(metadata.get('provenance'), provenance), 'Source provenance differs from run evidence')
    session_json = read_json(source/'session.json')
    require(session_json.get('session_id') == sid and same_provenance(session_json.get('provenance'), provenance),
            'Original session.json provenance/identity differs')
    decisions = [item['action'] for item in events if item.get('stage') == 'decision']
    require(decisions and decisions[-1] in ('continue', 'calibrate_then_continue'), 'Capture was not explicitly released after preflight')
    calibrated = decisions[-1] == 'calibrate_then_continue'
    before = read_json(run_dir/'preflight-before-capture.json')
    preflight = read_json(run_dir/'preflight-after-clock.json') if calibrated else before
    if calibrated:
        require(before.get('calibration', {}).get('performed') is True, 'Chosen clock calibration did not complete')
    else:
        require(before.get('calibration', {}).get('performed') is False, 'Keep-time choice unexpectedly calibrated the clock')
    offset = preflight['clock_offset_seconds']
    certain = preflight['clock_uncertainty_seconds'] <= 5
    expected_clock = {'kind': 'live', 'clock_status': ('ok' if abs(offset) <= 5 else 'offset') if certain else 'unknown',
                      'clock_offset_seconds': offset if certain else None,
                      'clock_reference': 'computer' if certain else 'unknown',
                      'clock_checked_at': preflight['clock_checked_at'] if certain else None,
                      'origin_device': preflight['host'][:96], 'note': provenance.get('note')}
    require(same_provenance(provenance, expected_clock), 'Live provenance does not match the post-decision clock measurement')
    remote = [item['detail'] for item in events if item.get('stage') == 'remote']
    captures = [item for item in remote if item.get('event') == 'capture_started']
    require(len(captures) == 1 and captures[0].get('session_id') == sid
            and not any(item.get('event') == 'attached' for item in remote), 'Run did not start exactly one new capture')
    require(outage.get('requested') is True and outage.get('kind') == 'local_forwarding_pause'
            and outage.get('scope') == 'this_run_only' and outage.get('physical_network_disconnected') is False,
            'Outage evidence is not the requested isolated forwarding pause')
    start, end, recovered = (instant(outage[name]) for name in ('started_at', 'ended_at', 'recovered_at'))
    pause_seconds = (end-start).total_seconds()
    require(outage.get('requested_pause_seconds') == 15 and 14.5 <= pause_seconds <= 20
            and outage.get('interrupted') is False, 'The 15-second pause did not finish normally')
    failures = [item for item in outage['retry_events'] if item.get('during_pause') is True
                and start <= instant(item['pc_utc']) <= end]
    require(failures and outage.get('recovered') is True and recovered >= end, 'No actual failed upload and recovery were observed')
    source_before = outage['source_samples_before_pause']
    require(max(item.get('source_saved_samples', 0) for item in failures) > source_before,
            'Source acquisition was not observed to progress during the upload pause')
    require(outage['source_samples_after_recovery'] > source_before
            and outage['confirmed_samples_after_recovery'] > source_before,
            'Restored upload did not advance past the pre-pause position')
    observation = board.get('upload_observation', {})
    retries = [item for item in remote if item.get('event') == 'retry']
    require(observation.get('failed_attempts') == len(retries) == len(outage['retry_events'])
            and observation.get('recovery_events', 0) >= 1
            and observation.get('final_source_samples') == total
            and observation.get('collector_changed_clock_during_capture') is False,
            'Collector retry/recovery observations are inconsistent')
    completed = [item for item in remote if item.get('event') == 'final_upload']
    require(completed and completed[-1].get('complete') is True and completed[-1].get('confirmed_samples') == total,
            'Final uploader confirmation is incomplete')
    summary = {'session_id': sid, 'samples': total, 'chunks': len(manifest), 'source_directory': str(source),
               'origin_counts': counts, 'valid_real_adc_samples': valid_real_adc,
               'all_downloaded_file_hashes_match': True, 'source_manifest_matches_board_and_snapshot': True,
               'new_capture_not_attached': True, 'collector_loaded_model_modules': [],
               'provenance_matches_post_decision_preflight': True, 'provenance': provenance,
               'clock_action': decisions[-1], 'pause_seconds': pause_seconds,
               'failed_uploads_during_pause': len(failures), 'source_progressed_during_pause': True,
               'source_samples_before_pause': source_before,
               'source_samples_after_recovery': outage['source_samples_after_recovery'],
               'upload_recovered': True, 'physical_network_disconnected': False}
    return summary, metadata, manifest, descriptors, original


def check_csv(exported, summary, metadata, descriptors, original):
    rows = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))
    require(len(rows) == len(original) == summary['samples'], 'Cloud CSV row count differs from source')
    offset = 0
    vref = metadata.get('vref_v', metadata.get('vref', 3.3))
    for descriptor in descriptors:
        for raw, row in zip(original[offset:offset+descriptor['sample_count']], rows[offset:offset+descriptor['sample_count']]):
            fields = tuple(int(row[key]) for key in ('sample_id', 'segment_id', 'segment_index', 'sequence', 'raw_adc', 'flags'))
            mode = int(row['mode']) if row['mode'] else None
            require(fields+(mode,) == raw, f'Cloud CSV sampling fields differ at sample {raw[0]}')
            require(row['session_id'] == summary['session_id'] and row['source'] == 'serial'
                    and row['signal_origin'] == signal_origin('serial', mode), 'CSV source identity/origin label differs')
            require(int(row['valid']) == int(valid(raw)), 'CSV validity flag differs')
            require(close_number(float(row['time_s']), raw[0]/360, 1e-8)
                    and close_number(float(row['segment_time_s']), raw[2]/360, 1e-8), 'CSV sample time differs')
            require(close_number(float(row['vref_v']), vref), 'CSV Vref differs')
            require(close_number(float(row['voltage_v']), raw[4]*vref/4095, 1e-8) if valid(raw)
                    else row['voltage_v'] == '', 'CSV voltage differs')
            for key in ('received_first_utc', 'received_last_utc'):
                require(instant(row[key]) == instant(descriptor[key]), 'CSV original block timestamp differs')
        offset += descriptor['sample_count']


def verify(args, client_factory=ReadOnlyClient):
    run_dir = args.run_dir.resolve()
    require(run_dir.is_dir(), 'Run evidence directory does not exist')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'verification': 'live_capture_readonly', 'run_id': run_dir.name,
              'service_url': args.url, 'performed_upload': False, 'performed_analysis': False,
              'performed_capture': False, 'cloud_mutated': False, 'checks': {}}
    try:
        summary, metadata, manifest, descriptors, original = inspect_local_run(run_dir)
        report['local_evidence'] = summary
        report['checks']['local_source_pause_recovery_and_provenance_verified'] = True
        if args.offline:
            report['status'] = 'offline_passed'
            report['cloud_verification_performed'] = False
        else:
            client = client_factory(args.url, timeout=args.timeout)
            health = client.get('/health')
            require(health.get('ok') is True and health.get('deployment') == 'cloud'
                    and health.get('service') == 'ecg-course-demo' and health.get('model_status') == args.expected_model,
                    'Cloud health/model status differs from the expected deployment')
            sid = summary['session_id']
            detail = client.get(f'/api/sessions/{sid}')
            require(detail.get('source') == 'serial' and detail.get('upload_status') == 'complete'
                    and detail.get('source_status') == 'complete' and detail.get('sample_count') == summary['samples']
                    and detail.get('chunks') == manifest, 'Cloud complete session/manifest differs from source')
            require(same_provenance(detail.get('provenance'), summary['provenance']), 'Cloud provenance differs from preflight/source')
            require(instant(detail['started_utc']) == instant(metadata['started_utc']), 'Cloud acquisition start timestamp was changed')
            require(instant(detail['first_uploaded_at']) <= instant(detail['last_uploaded_at']), 'Cloud upload timestamps are reversed')
            exported = client.get(f'/api/sessions/{sid}/export', raw=True)
            check_csv(exported, summary, metadata, descriptors, original)
            exported_path = run_dir/'cloud-readonly-export.csv'
            exported_path.write_bytes(exported)
            atomic_json(run_dir/'cloud-readonly-session.json', detail)
            report['cloud'] = {'model_status': health['model_status'], 'manifest_matches_source': True,
                               'csv_all_rows_equal': True, 'provenance_matches_source_and_preflight': True,
                               'first_uploaded_at': detail['first_uploaded_at'], 'last_uploaded_at': detail['last_uploaded_at'],
                               'original_started_utc': detail['started_utc'], 'export_file': str(exported_path),
                               'export_sha256': hashlib.sha256(exported).hexdigest(), 'requests': client.requests}
            report['checks']['cloud_manifest_csv_and_provenance_verified'] = True
            report['cloud_verification_performed'] = True
            report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    output = run_dir/('live-verification-offline.json' if args.offline else 'live-verification.json')
    atomic_json(output, report)
    print(json.dumps({'status': report['status'], 'report_file': str(output),
                      'checks': report['checks'], 'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] in ('passed', 'offline_passed') else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True, help='evidence/cloud/pi-link/<run_id>')
    parser.add_argument('--url', default='http://127.0.0.1:8767')
    parser.add_argument('--expected-model', choices=('ready', 'not_configured'), default='ready')
    parser.add_argument('--timeout', type=int, default=30)
    parser.add_argument('--offline', action='store_true', help='Check local evidence only; never report cloud validation as passed')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error('--timeout must be between 1 and 600 seconds')
    return verify(args)


if __name__ == '__main__':
    raise SystemExit(main())
