"""Verify cloud CardioEdgeAI results against saved, real-electrode beat evidence.

Run only after the real recording and both negative-control sessions are already
uploaded. This script requests cloud analysis; it does not upload samples, load a
local model, read credentials, or change the original terminal evidence.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from protocol import decode_chunk, valid
from uploader import Uploader, atomic_json


ROOT = Path(__file__).resolve().parent
SESSION_ID = '23c0fb1241a84cdb842c25015c43c7b1'
HISTORICAL_ID = 'pi_serial_20261027_015950_e3d04b'
REFERENCE = (ROOT.parent / '树莓派心电终端/evidence/19_live_electrodes_20261029'
             / 'ecg-live-electrodes-20261029')
REFERENCE_SHA256 = '99c39d5782adf6a48bebfab1e82c1e52b294bb0801b9ede70ee9599170ab8b12'
CLASSES = ('N', 'S', 'V')
MODEL_NAME = 'CardioEdgeAI v1 INT8'
MODEL_VERSION = 'cardioedge-v1-int8-cloud1'
SCORE_TOLERANCE = 1e-9


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same_number(actual, expected, tolerance=SCORE_TOLERANCE):
    return (type(actual) in (int, float) and type(expected) in (int, float)
            and math.isfinite(actual) and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance))


def load_reference(directory):
    """Bind the 148 saved predictions to the original complete source index."""
    reference_file = directory / 'offline-predictions.json'
    raw_reference = reference_file.read_bytes()
    digest = hashlib.sha256(raw_reference).hexdigest()
    require(digest == REFERENCE_SHA256, 'Saved 148-beat reference has changed')
    beats = json.loads(raw_reference)
    require(isinstance(beats, list) and len(beats) == 148, 'Expected 148 saved reference beats')
    source = directory / 'data/sessions' / SESSION_ID
    with closing(sqlite3.connect((source/'index.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Reference index is corrupt')
        sessions = db.execute('SELECT session_id,metadata_json,status,sample_count FROM sessions').fetchall()
        require(len(sessions) == 1, 'Reference index must contain exactly one session')
        sid, raw_metadata, status, total = sessions[0]
        descriptors = [json.loads(row[0]) for row in db.execute(
            'SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
        recorded = [json.loads(row[0]) for row in db.execute(
            "SELECT detail_json FROM events WHERE kind='cardioedge_prediction' ORDER BY event_id")]
    metadata = json.loads(raw_metadata)
    require(sid == SESSION_ID and status == 'complete' and total == 43384,
            'Expected the original complete 43,384-sample recording')
    require(metadata['source'] == 'serial' and metadata['sample_rate_hz'] == 360,
            'Reference is not the expected 360 Hz serial recording')
    require(len(recorded) == len(beats), 'Original recorded beat count differs from offline reference')
    for number, (saved, live) in enumerate(zip(beats, recorded), 1):
        require(saved['session_id'] == live['session_id'] == sid,
                f'Reference beat {number}: session identity mismatch')
        require(saved['source'] == 'serial' and saved['mode'] == 0,
                f'Reference beat {number}: not real ADC mode')
        for key in ('r_sample', 'label', 'scores', 'segment_id', 'requires_review'):
            require(saved[key] == live[key], f'Reference beat {number}: saved/live {key} differs')
        require(saved['label'] in CLASSES and set(saved['scores']) == set(CLASSES),
                f'Reference beat {number}: unexpected class labels')
        require(all(same_number(saved['scores'][label], saved['scores'][label]) for label in CLASSES),
                f'Reference beat {number}: invalid score')
    manifest = []
    sample_count = 0
    previous_sample = None
    source_samples = set()
    for descriptor in descriptors:
        cid = descriptor['chunk_id']
        require(type(cid) is int and 0 <= cid <= 99999999, 'Invalid source chunk id')
        rows = decode_chunk((source/'chunks'/f'{cid:08d}.bin').read_bytes(), descriptor['sha256'])
        require(len(rows) == descriptor['sample_count'], 'Source block point count differs')
        require(rows[0][0] == descriptor['first_sample_id'] and rows[-1][0] == descriptor['last_sample_id'],
                'Source block sample range differs')
        require(previous_sample is None or rows[0][0] > previous_sample, 'Source chunks overlap')
        require(all(valid(row) and row[-1] == 0 for row in rows), 'Source includes invalid or simulated points')
        previous_sample = rows[-1][0]
        source_samples.update(row[0] for row in rows)
        sample_count += len(rows)
        manifest.append({key: descriptor[key] for key in ('chunk_id', 'sha256', 'sample_count')})
    require(len(manifest) == 120 and sample_count == total, 'Reference source manifest is incomplete')
    require(all(beat['r_sample'] in source_samples for beat in beats), 'Reference R point is absent from source')
    provenance = {'file': str(reference_file.resolve()), 'sha256': digest,
                  'source_directory': str(source.resolve()), 'session_id': sid,
                  'samples': total, 'chunks': len(manifest), 'beats': len(beats),
                  'original_recorded_beats_match_saved_reference': True,
                  'all_source_chunk_hashes_verified': True}
    return beats, manifest, provenance


def compare_model_result(result, reference):
    require(isinstance(result, dict), 'Cloud did not return a model result')
    beats = result.get('beats')
    require(isinstance(beats, list) and len(beats) == len(reference), 'Cloud/reference beat count differs')
    counts = {label: sum(beat['label'] == label for beat in reference) for label in CLASSES}
    require(result.get('beat_count') == len(reference), 'Reported cloud beat_count differs')
    require(result.get('counts') == counts, 'Cloud class counts differ from reference')
    max_score_error = 0.0
    for number, (actual, expected) in enumerate(zip(beats, reference), 1):
        require(actual.get('sample_id') == expected['r_sample'], f'Beat {number}: R point differs')
        require(actual.get('label') == expected['label'], f'Beat {number}: class differs')
        require(actual.get('segment_id') == expected['segment_id'], f'Beat {number}: segment differs')
        require(actual.get('requires_review') is expected['requires_review'], f'Beat {number}: review flag differs')
        require(same_number(actual.get('time_s'), expected['r_sample']/360, 1e-6),
                f'Beat {number}: time does not correspond to the R point')
        scores = actual.get('scores')
        require(isinstance(scores, dict) and set(scores) == set(CLASSES), f'Beat {number}: missing scores')
        for label in CLASSES:
            require(same_number(scores[label], expected['scores'][label]),
                    f'Beat {number}: {label} score differs')
            max_score_error = max(max_score_error, abs(scores[label] - expected['scores'][label]))
        require(same_number(actual.get('score'), expected['scores'][expected['label']]),
                f'Beat {number}: selected-class score differs')
    require(dict(Counter(beat['label'] for beat in beats)) == dict(Counter(beat['label'] for beat in reference)),
            'Returned beat labels do not match reference counts')
    return {'beats_compared': len(reference), 'counts': counts, 'all_r_points_equal': True,
            'all_labels_equal': True, 'all_class_scores_equal_within_tolerance': True,
            'max_class_score_absolute_error': max_score_error, 'score_absolute_tolerance': SCORE_TOLERANCE,
            'all_selected_scores_equal': True, 'all_segments_and_review_flags_equal': True,
            'all_beat_times_match_sample_ids': True}


def verify(args):
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'service_url': args.url, 'session_id': SESSION_ID, 'checks': {},
              'local_model_loaded': False, 'samples_uploaded_by_this_script': False,
              'scope': 'Cloud computation reproducibility and input eligibility; not clinical accuracy.'}
    report_path = evidence/'model-verification.json'

    def save_response(name, response):
        path = evidence/name
        atomic_json(path, response)
        return str(path)

    try:
        reference, manifest, provenance = load_reference(args.reference_dir.resolve())
        report['reference'] = provenance
        # The SSH loopback service does not require an API token. Explicitly pass
        # an empty value so no credential environment variable is read.
        client = Uploader(args.url, token='', timeout=args.timeout)
        health = client.request('GET', '/health')
        report['health_response_file'] = save_response('model-health.json', health)
        require(health.get('ok') is True and health.get('service') == 'ecg-course-demo'
                and health.get('deployment') == 'cloud' and health.get('model_status') == 'ready',
                'Expected a healthy cloud service with the model ready')
        require(health.get('model_name') == MODEL_NAME and health.get('model_version') == MODEL_VERSION,
                'Cloud model name/version differs from the expected CardioEdgeAI deployment')
        report['checks']['cloud_model_ready'] = True
        detail = client.request('GET', f'/api/sessions/{SESSION_ID}')
        require(detail.get('source') == 'serial' and detail.get('sample_count') == provenance['samples']
                and detail.get('upload_status') == 'complete' and detail.get('chunks') == manifest,
                'Cloud real recording is incomplete or differs from the original source manifest')
        report['checks']['cloud_manifest_matches_original_source'] = True
        analysis = client.request('POST', f'/api/sessions/{SESSION_ID}/analysis')
        report['model_response_file'] = save_response('model-analysis-real.json', analysis)
        require(analysis.get('session_id') == SESSION_ID and analysis.get('model_status') == 'ready'
                and analysis.get('analysis_status') == 'complete', 'Real-recording analysis did not complete')
        require(analysis.get('model_name') == MODEL_NAME and analysis.get('model_version') == MODEL_VERSION,
                'Analysis used an unexpected model version')
        report['beat_comparison'] = compare_model_result(analysis.get('model_result'), reference)
        report['checks']['all_148_beats_match_saved_reference'] = True
        repeated = client.request('GET', f'/api/sessions/{SESSION_ID}/analysis')
        report['persisted_response_file'] = save_response('model-analysis-real-get.json', repeated)
        require(repeated == analysis, 'Persisted GET response differs from the POST response')
        report['checks']['persisted_get_equals_post'] = True
        sessions = client.request('GET', '/api/sessions')['sessions']
        demo_ids = sorted(item['session_id'] for item in sessions if item.get('source') == 'demo'
                          and item.get('upload_status') == 'complete')
        require(demo_ids, 'A completed demo session is required for the negative control')
        demo_id = args.demo_session or demo_ids[0]
        require(demo_id in demo_ids, 'Selected demo negative control is not a completed demo session')
        historical = client.request('GET', f'/api/sessions/{HISTORICAL_ID}')
        require(historical.get('source') == 'serial' and historical.get('upload_status') == 'complete',
                'Historical invalid/simulated negative control is missing or incomplete')
        negative_results = []
        for name, sid in (('historical-invalid-simulated', HISTORICAL_ID), ('demo', demo_id)):
            response = client.request('POST', f'/api/sessions/{sid}/analysis')
            response_file = save_response('model-analysis-'+name+'.json', response)
            require(response.get('session_id') == sid and response.get('model_status') == 'ready'
                    and response.get('analysis_status') == 'ineligible'
                    and 'model_result' in response and response['model_result'] is None,
                    f'{name}: expected ineligible with model_result=null')
            require(isinstance(response.get('message'), str) and response['message'].strip(),
                    f'{name}: rejection did not explain its reason')
            saved = client.request('GET', f'/api/sessions/{sid}/analysis')
            require(saved == response, f'{name}: persisted rejection differs from POST')
            negative_results.append({'kind': name, 'session_id': sid, 'analysis_status': 'ineligible',
                                     'model_result': None, 'response_file': response_file,
                                     'persisted_get_equals_post': True})
        report['negative_controls'] = negative_results
        report['checks']['historical_invalid_and_board_simulation_rejected'] = True
        report['checks']['demo_rejected'] = True
        report['model_status'] = 'ready'
        report['model_name'] = MODEL_NAME
        report['model_version'] = MODEL_VERSION
        report['status'] = 'passed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        atomic_json(report_path, report)
    print(json.dumps({'status': report['status'], 'report_file': str(report_path),
                      'checks': report['checks'], 'error': report.get('error')}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8767')
    parser.add_argument('--reference-dir', type=Path, default=REFERENCE)
    parser.add_argument('--evidence-dir', type=Path, default=ROOT/'evidence/cloud')
    parser.add_argument('--demo-session', help='Optional already-uploaded completed demo session ID')
    parser.add_argument('--timeout', type=int, default=180, help='HTTP timeout in seconds')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error('--timeout must be between 1 and 600 seconds')
    return verify(args)


if __name__ == '__main__':
    raise SystemExit(main())
