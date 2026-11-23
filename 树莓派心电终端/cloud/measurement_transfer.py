"""Read the summary from the uploader's committed SQLite snapshot (stdlib only)."""
import json


def _object(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def upload_summary(db, metadata):
    if metadata.get('status') == 'recording':
        return None
    # Old recording exporters may not contain the events table at all.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
        return None
    row = db.execute("SELECT detail_json FROM events WHERE kind='measurement_summary' ORDER BY event_id DESC LIMIT 1").fetchone()
    stats = _object(row[0]) if row else None
    if not stats or stats.get('schema_version') != 1:
        return None  # Keep old measurements explicitly unknown, never fabricate HR.
    if metadata.get('status') != 'complete':
        # Analysis can run ahead of the recorder. A failed save cannot prove
        # that its HR / quality windows belong to the committed raw subset.
        return dict(schema_version=1, requested_duration_s=metadata.get('requested_duration_s'),
                    end_reason=metadata.get('end_reason'), analysis_stats=None,
                    classification_counts=None, classification_enabled=(metadata.get('classification') or {}).get('enabled'))
    counts = dict(N=0, S=0, V=0)
    sid = metadata.get('session_id')
    for (raw,) in db.execute("SELECT detail_json FROM events WHERE kind='cardioedge_prediction'"):
        event = _object(raw)
        if (event and event.get('session_id', sid) == sid and event.get('label') in counts
                and all(type(event.get(k)) is int and 0 <= event[k] < 2**63 for k in ('r_sample','segment_id'))):
            counts[event['label']] += 1
    return dict(schema_version=1, requested_duration_s=metadata.get('requested_duration_s'),
                end_reason=metadata.get('end_reason'), analysis_stats=stats,
                classification_counts=counts, classification_enabled=(metadata.get('classification') or {}).get('enabled'))
