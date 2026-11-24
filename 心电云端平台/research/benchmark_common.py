"""Shared, read-only MIT-BIH input and scoring for offline benchmarks."""
from collections import Counter
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform

import numpy as np

from research.evaluate_ecg import BEAT_SYMBOLS, checksum, matching_metrics

ROOT = Path(__file__).resolve().parents[1]
FS = 360
GAIN = 200
TRAIN_RECORDS = ('101', '106', '108', '109', '112', '114', '115', '116',
                 '118', '119', '122', '124', '201', '203', '205', '207',
                 '208', '209', '215', '220', '223', '230')
DS2_RECORDS = ('100', '103', '105', '111', '113', '117', '121', '123',
               '200', '202', '210', '212', '213', '214', '219', '221',
               '222', '228', '231', '232', '233', '234')
QRS_RECORDS = tuple(sorted(TRAIN_RECORDS + DS2_RECORDS))
# MIT-BIH directory: 201 and 202 are the same subject. 201 is in training.
CLASSIFICATION_RECORDS = tuple(r for r in DS2_RECORDS if r != '202')
LABELS = ('N', 'S', 'V')
CLASS_MAP = {symbol: label for label, symbols in
             zip(LABELS, ('NLRej', 'AaJS', 'VE')) for symbol in symbols}


def to_adc(signal):
    values = np.asarray(signal, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError('Expected finite, one-dimensional mV signal')
    return np.clip(np.rint(2048 + GAIN * values), 0, 4095).astype(np.int64)


def select_channel(header):
    if header.fs != FS:
        raise ValueError('This benchmark requires 360 Hz; no silent resampling')
    if 'MLII' not in header.sig_name:
        raise ValueError('MLII is required; no silent lead substitution')
    return header.sig_name.index('MLII')


def validate_records(records, allowed):
    if not records or len(set(records)) != len(records) or any(r not in allowed for r in records):
        raise ValueError('Expected unique record IDs from the declared cohort')


def validate_paths(data, output):
    source, target = Path(data).resolve(), Path(output).resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('Data and output directories must not contain each other')


def prepare_output(output):
    """A failed rerun must not leave a stale success report beside new details."""
    target = Path(output)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise FileExistsError('Output must be a new or empty directory; existing results are preserved')
    target.mkdir(parents=True, exist_ok=True)


def load_record(data, record, seconds=None):
    import wfdb

    validate_records([record], QRS_RECORDS)
    source = Path(data).resolve()
    base = source / record
    header = wfdb.rdheader(str(base))
    channel = select_channel(header)
    if seconds is not None and (not np.isfinite(seconds) or seconds < 10):
        raise ValueError('seconds must be at least 10, or omitted for the full record')
    stop = header.sig_len if seconds is None else min(header.sig_len, int(seconds * FS))
    paths = {base.with_suffix('.hea'), base.with_suffix('.atr')}
    paths.update((source / name).resolve() for name in header.file_name)
    if any(not path.resolve().is_relative_to(source) for path in paths):
        raise ValueError('Signal file outside the dataset')
    hashes = {p.name: checksum(p) for p in sorted(paths)}
    signal = wfdb.rdrecord(str(base), sampto=stop, channels=[channel])
    if signal.units[0] != 'mV':
        raise ValueError('The fixed ADC conversion requires mV')
    annotation = wfdb.rdann(str(base), 'atr', sampto=stop)
    keep = np.array([s in BEAT_SYMBOLS and int(p) < stop
                     for p, s in zip(annotation.sample, annotation.symbol)], dtype=bool)
    peaks = annotation.sample[keep].astype(int)
    symbols = np.asarray(annotation.symbol)[keep].tolist()
    adc = to_adc(signal.p_signal[:, 0])
    metadata = {
        'record': record, 'channel': channel, 'lead': signal.sig_name[0],
        'sample_count': len(adc), 'signal_seconds': len(adc)/FS,
        'full_record': stop == header.sig_len, 'source_files_sha256': hashes,
        'adc_sha256': __import__('hashlib').sha256(adc.astype('<i8').tobytes()).hexdigest(),
        'rail_samples': int(((adc == 0) | (adc == 4095)).sum()),
        'reference_beat_symbols': dict(Counter(symbols)),
        'ignored_nonbeat_annotations': int((~keep).sum()),
    }
    return adc, peaks, symbols, metadata


def verify_inputs(data, metadata):
    for name, expected in metadata['source_files_sha256'].items():
        if checksum(Path(data)/name) != expected:
            raise RuntimeError(f'Input changed during evaluation: {name}')


def match_peaks(reference, detected, tolerance):
    ref, test = np.asarray(reference, dtype=int), np.asarray(detected, dtype=int)
    if np.any(np.diff(ref) <= 0) or np.any(np.diff(test) <= 0):
        raise ValueError('Peak sequences must be strictly increasing')
    if not len(ref) or not len(test):
        return np.array([], dtype=int), np.array([], dtype=int)
    from wfdb.processing import compare_annotations
    # WFDB 4.3.1 uses strict '< window_width'. Peaks are integer samples:
    # adding one implements an inclusive +/- tolerance without admitting 55/19.
    matched = compare_annotations(ref, test, window_width=tolerance+1)
    ri = np.asarray(matched.matched_ref_inds, dtype=int)
    ti = np.asarray(matched.matched_test_inds, dtype=int)
    if len(set(ri)) != len(ri) or len(set(ti)) != len(ti):
        raise RuntimeError('Matcher returned non-unique matches')
    return ri, ti


def qrs_metrics(reference, detected, segments, tolerance=54):
    ri, ti = match_peaks(reference, detected, tolerance)
    result = matching_metrics(reference, detected, ri, ti, segments, FS)
    result['matching_tolerance_ms'] = tolerance * 1000 / FS
    return result


def aggregate_qrs(rows):
    tp, fp, fn = (sum(r[k] for r in rows) for k in ('tp', 'fp', 'fn'))
    return {'tp': tp, 'fp': fp, 'fn': fn,
            'sensitivity': tp/(tp+fn) if tp+fn else None,
            'positive_predictivity': tp/(tp+fp) if tp+fp else None,
            'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}


def environment(packages):
    return {'python': platform.python_version(), 'platform': platform.platform(),
            'machine': platform.machine(),
            'thread_limits': {name: os.environ.get(name) for name in
                              ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')},
            'installed_packages': dict(sorted((d.metadata['Name'], d.version)
                                             for d in importlib.metadata.distributions())),
            'packages': {name: importlib.metadata.version(name) for name in packages}}


def fingerprints(paths):
    return {str(Path(p).relative_to(ROOT)).replace('\\', '/'): checksum(p) for p in paths}


def verify_code(expected):
    if any(checksum(ROOT/name) != digest for name, digest in expected.items()):
        raise RuntimeError('Benchmark or model source changed during evaluation')


def write_json(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def timestamp():
    return datetime.now(timezone.utc).isoformat()
