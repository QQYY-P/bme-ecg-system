"""Recompute a downloaded Pi session locally and compare existing cloud GET results.

This verifier never uploads data, requests analysis, reads credentials, or edits
the source session. It validates reproducibility, not clinical accuracy.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import urllib.request
from urllib.parse import urlparse

from cloud_model.engine import NAME, VERSION, MAX_SAMPLES
from protocol import decode_chunk
from uploader import atomic_json

ROOT = Path(__file__).resolve().parent
CLASSES = ('N', 'S', 'V')
SCORE_TOLERANCE = 1e-9


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aware_time(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(stamp.tzinfo is not None, 'Timestamp must include a timezone')
    return stamp


class ReadOnlyClient:
    """GET-only transport, without ambient credentials or HTTP proxies."""
    def __init__(self, server, timeout=30):
        self.server = server.rstrip('/')
        parsed = urlparse(self.server)
        require(parsed.scheme in ('http', 'https') and parsed.hostname
                and not (parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path),
                '--server must be an http(s) origin without credentials or a path')
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path):
        request = urllib.request.Request(self.server + path, method='GET',
                                         headers={'Accept': 'application/json'})
        with self.opener.open(request, timeout=self.timeout) as response:
            return json.load(response)


def load_source(directory):
    directory = directory.resolve()
    sid = directory.name
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', sid), 'Invalid session directory name')
    index = directory / 'index.sqlite'
    hashes = {'index.sqlite': sha256(index)}
    # A downloaded finished recording must be a self-contained SQLite snapshot.
    wal = directory / 'index.sqlite-wal'
    require(not wal.exists() or wal.stat().st_size == 0,
            'Source has an active SQLite WAL; download a consistent finished snapshot first')
    with closing(sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Source index is corrupt')
        sessions = db.execute('SELECT session_id,metadata_json,status,sample_count FROM sessions').fetchall()
        require(len(sessions) == 1, 'Source must contain exactly one session')
        source_sid, raw_metadata, status, total = sessions[0]
        require(source_sid == sid, 'Source directory and session identity differ')
        require(status != 'recording', 'Finish recording before verification')
        require(type(total) is int and 0 <= total <= MAX_SAMPLES, 'Unsupported source point count')
        indexed = db.execute('SELECT chunk_id,descriptor_json FROM chunks ORDER BY chunk_id').fetchall()
    metadata = json.loads(raw_metadata)
    require(metadata.get('source') in ('serial', 'replay', 'demo', 'unknown'), 'Unsupported source type')
    require(metadata.get('sample_rate_hz', 360) == 360, 'Only the original 360 Hz ADC stream is supported')
    vref = metadata.get('vref_v', metadata.get('vref', 3.3))
    require(type(vref) in (int, float) and math.isfinite(vref) and 0 < vref <= 10, 'Invalid source Vref')
    aware_time(metadata['started_utc'])
    manifest, batches = [], []
    count, previous_id, previous_chunk = 0, None, None
    for cid, encoded in indexed:
        descriptor = json.loads(encoded)
        require(type(cid) is int and 0 <= cid <= 99999999
                and descriptor['chunk_id'] == cid and (previous_chunk is None or cid > previous_chunk),
                'Invalid or duplicate source chunk id')
        relative = f'chunks/{cid:08d}.bin'
        path = directory / relative
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        rows = decode_chunk(raw, descriptor['sha256'])
        require(len(rows) == descriptor['sample_count']
                and rows[0][0] == descriptor['first_sample_id']
                and rows[-1][0] == descriptor['last_sample_id'], 'Source descriptor differs from raw data')
        require(previous_id is None or rows[0][0] > previous_id, 'Source chunks overlap')
        require(aware_time(descriptor['received_last_utc']) >= aware_time(descriptor['received_first_utc']),
                'Source block receive times are reversed')
        previous_id, previous_chunk = rows[-1][0], cid
        count += len(rows)
        hashes[relative] = digest
        manifest.append({key: descriptor[key] for key in ('chunk_id', 'sha256', 'sample_count')})
        batches.append((descriptor, rows))
    require(count == total, 'Source manifest point count is incomplete')
    require(sha256(index) == hashes['index.sqlite'], 'Source index changed while reading')
    return {'directory': directory, 'session_id': sid, 'metadata': metadata, 'status': status,
            'sample_count': total, 'vref_v': vref, 'manifest': manifest, 'batches': batches, 'hashes': hashes}


def local_analysis(source):
    # Directly run the existing formal streaming implementation, independently
    # of the HTTP service and its AnalysisService cache/result construction.
    from cloud_model.vendor.cardioedge.stream import StreamingClassifier, WEIGHT_HASH
    from cloud_model.vendor.contracts import SamplePoint, SampleBatch

    classifier = StreamingClassifier(vref=source['vref_v'], sample_rate=360)
    kind = source['metadata']['source']
    beats, rejections = [], []
    skipped, quality = Counter(), Counter()
    eligible = 0
    for descriptor, rows in source['batches']:
        points = tuple(SamplePoint(*row) for row in rows)
        for point in points:
            if kind not in ('serial', 'replay'):
                skipped['source_not_real'] += 1
            elif point.mode == 1:
                skipped['board_simulated'] += 1
            elif point.mode != 0:
                skipped['mode_unknown'] += 1
            elif not point.valid:
                skipped['invalid_adc'] += 1
            else:
                eligible += 1
        batch = SampleBatch(source['session_id'], kind, points, time.monotonic(), descriptor['received_last_utc'])
        beats.extend(classifier.process(batch))
        for rejected in classifier.take_rejections():
            quality[rejected['reason']] += 1
            if len(rejections) < 500:
                rejections.append({'sample_id': rejected['r_sample'], 'reason': rejected['reason']})
    fingerprint = hashlib.sha256()
    model_root = ROOT / 'cloud_model'
    for path in sorted(model_root.rglob('*.py')):
        fingerprint.update(path.relative_to(model_root).as_posix().encode())
        fingerprint.update(path.read_bytes())
    fingerprint.update(WEIGHT_HASH.encode())
    return {'analysis_status': 'complete' if beats else 'ineligible', 'beats': beats,
            'counts': {label: sum(beat['label'] == label for beat in beats) for label in CLASSES},
            'input_summary': {'total_samples': source['sample_count'], 'eligible_samples': eligible,
                              'skipped_samples': source['sample_count'] - eligible, 'skipped_reasons': dict(skipped)},
            'quality_rejections': rejections, 'quality_rejection_counts': dict(quality),
            'quality_rejections_truncated': sum(quality.values()) > len(rejections),
            'model_fingerprint': fingerprint.hexdigest(), 'weights_sha256': WEIGHT_HASH,
            'snapshot': classifier.snapshot()}


def same_number(actual, expected, tolerance=SCORE_TOLERANCE):
    return (type(actual) in (int, float) and type(expected) in (int, float)
            and math.isfinite(actual) and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance))


def compare_analysis(cloud, local, sid):
    require(cloud.get('session_id') == sid and cloud.get('model_status') == 'ready', 'Wrong cloud session/model state')
    require(cloud.get('model_name') == NAME and cloud.get('model_version') == VERSION, 'Cloud model version differs')
    require(cloud.get('analysis_status') == local['analysis_status'],
            'Cloud analysis is absent, stale, running, or differs from local eligibility; this verifier never requests it')
    summary = cloud.get('input_summary')
    require(isinstance(summary, dict) and all(summary.get(k) == v for k, v in local['input_summary'].items()),
            'Cloud/local real-input gating statistics differ')
    reference = local['beats']
    if not reference:
        require('model_result' in cloud and cloud['model_result'] is None, 'Ineligible input must have a null result')
        return {'beats_compared': 0, 'counts': local['counts'], 'eligible_input_without_beats':
                local['input_summary']['eligible_samples'] > 0, 'ineligible_null_result_matches': True}
    result = cloud.get('model_result')
    require(isinstance(result, dict), 'Missing cloud model result')
    actual_beats = result.get('beats')
    require(isinstance(actual_beats, list) and len(actual_beats) == len(reference)
            and result.get('beat_count') == len(reference), 'Cloud/local beat count differs')
    require(result.get('counts') == local['counts'], 'Cloud/local class totals differ')
    max_error = 0.0
    for number, (actual, expected) in enumerate(zip(actual_beats, reference), 1):
        for public, raw in (('sample_id', 'r_sample'), ('label', 'label'), ('segment_id', 'segment_id'),
                            ('requires_review', 'requires_review'), ('classified_at_sample', 'classified_at_sample')):
            require(actual.get(public) == expected[raw], f'Beat {number}: {public} differs')
        require(same_number(actual.get('time_s'), expected['r_sample'] / 360, 1e-6), f'Beat {number}: time differs')
        scores = actual.get('scores')
        require(isinstance(scores, dict) and set(scores) == set(CLASSES), f'Beat {number}: class scores missing')
        for label in CLASSES:
            require(same_number(scores[label], expected['scores'][label]), f'Beat {number}: {label} score differs')
            max_error = max(max_error, abs(scores[label] - expected['scores'][label]))
        require(same_number(actual.get('score'), expected['scores'][expected['label']]),
                f'Beat {number}: selected-class score differs')
    for key in ('eligible_samples', 'skipped_samples'):
        require(result.get(key) == local['input_summary'][key], f'Cloud/local {key} differs')
    for key in ('quality_rejections', 'quality_rejection_counts', 'quality_rejections_truncated'):
        require(result.get(key) == local[key], f'Cloud/local {key} differs')
    require(result.get('segments_analyzed') == len({beat['segment_id'] for beat in reference}), 'Segment total differs')
    return {'beats_compared': len(reference), 'counts': local['counts'], 'all_r_points_equal': True,
            'all_labels_equal': True, 'all_class_scores_equal_within_tolerance': True,
            'max_class_score_absolute_error': max_error, 'score_absolute_tolerance': SCORE_TOLERANCE,
            'all_selected_scores_equal': True, 'all_segments_review_flags_and_classification_samples_equal': True,
            'all_beat_times_match_sample_ids': True, 'quality_gate_results_equal': True}


def verify(args):
    directory = args.session_dir.resolve()
    evidence = (args.evidence_dir or ROOT / 'evidence/live-model' / directory.name).resolve()
    require(evidence != directory and not evidence.is_relative_to(directory), 'Evidence must be outside the source directory')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'server': args.server, 'session_id': directory.name, 'source_directory': str(directory),
              'source_modified': False, 'cloud_requests': [], 'cloud_writes': False,
              'scope': 'Reproducibility and input gating only; not clinical accuracy.'}
    report_path = evidence / 'verification.json'
    try:
        source = load_source(directory)
        report['source'] = {'sample_count': source['sample_count'], 'chunk_count': len(source['manifest']),
                            'metadata': source['metadata'], 'status': source['status'], 'hashes': source['hashes']}
        local = local_analysis(source)
        atomic_json(evidence / 'local-predictions.json', local['beats'])
        atomic_json(evidence / 'local-summary.json', {k: v for k, v in local.items() if k != 'beats'})
        client = ReadOnlyClient(args.server, args.timeout)

        def get(path, filename):
            report['cloud_requests'].append({'method': 'GET', 'path': path})
            result = client.get(path)
            atomic_json(evidence / filename, result)
            return result

        health = get('/health', 'cloud-health.json')
        require(health.get('ok') is True and health.get('service') == 'ecg-course-demo'
                and health.get('model_status') == 'ready' and health.get('model_name') == NAME
                and health.get('model_version') == VERSION, 'Expected service/model is not ready')
        sid = source['session_id']
        detail = get(f'/api/sessions/{sid}', 'cloud-session.json')
        require(detail.get('session_id') == sid and detail.get('upload_status') == 'complete'
                and detail.get('sample_count') == source['sample_count'] and detail.get('chunks') == source['manifest'],
                'Cloud session identity, completion, point count, or raw manifest differs')
        require(detail.get('source') == source['metadata']['source'] and detail.get('sample_rate_hz') == 360
                and detail.get('vref_v') == source['vref_v'] and detail.get('source_status') == source['status']
                and aware_time(detail['started_utc']) == aware_time(source['metadata']['started_utc']),
                'Cloud/source acquisition metadata differs')
        cloud = get(f'/api/sessions/{sid}/analysis', 'cloud-analysis.json')
        metadata = {key: detail[key] for key in ('source', 'sample_rate_hz', 'vref_v', 'started_utc', 'source_status')}
        digest_content = {'metadata': metadata, 'complete': True, 'manifest': source['manifest'],
                          'model_fingerprint': local['model_fingerprint'], 'model_version': VERSION}
        digest = hashlib.sha256(json.dumps(digest_content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        require(cloud.get('source_digest') == digest, 'Cloud/local model fingerprint or bound source digest differs')
        report['comparison'] = compare_analysis(cloud, local, sid)
        require(get(f'/api/sessions/{sid}/analysis', 'cloud-analysis-repeat.json') == cloud,
                'Persisted cloud GET result changed during verification')
        require(all(sha256(directory / name) == value for name, value in source['hashes'].items()),
                'Source files changed during verification')
        report.update(status='passed', source_digest=digest, source_files_unchanged=True,
                      all_source_hashes_verified=True, cloud_manifest_matches=True, persisted_get_stable=True,
                      model_name=NAME, model_version=VERSION, weights_sha256=local['weights_sha256'])
    except Exception as exc:
        report.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)})
    finally:
        atomic_json(report_path, report)
    print(json.dumps({'status': report['status'], 'report_file': str(report_path),
                      'comparison': report.get('comparison'), 'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', required=True, type=Path)
    parser.add_argument('--server', default='http://127.0.0.1:8767')
    parser.add_argument('--evidence-dir', type=Path)
    parser.add_argument('--timeout', type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 120:
        parser.error('--timeout must be between 1 and 120 seconds')
    return verify(args)


if __name__ == '__main__':
    raise SystemExit(main())
