"""Independent full-record measurement of the pinned application classifier.

No training, threshold search or production-model changes. The reference-R
condition replaces peak locations only, retaining all actual streaming gates.
"""
import argparse
from collections import Counter
from pathlib import Path
import time

import numpy as np

from cloud_model.vendor.contracts import SamplePoint, SampleBatch
from cloud_model.vendor.cardioedge.stream import StreamingClassifier, CONFIG
from research import benchmark_common as common

VERSION = 'cardioedge-independent-evaluation-v1'
MODES = ('end_to_end', 'reference_r')


class ReferenceDetector:
    """Diagnostic only: release annotated positions after 0.4 s of received data.

    No class labels. Reset excludes peaks from prior source/quality segments.
    This is not a detector and must never be used by the application.
    """
    def __init__(self, peaks):
        self.reference = np.asarray(peaks, dtype=int)
        if self.reference.ndim != 1 or np.any(np.diff(self.reference) <= 0):
            raise ValueError('Expected unique, chronological reference peaks')
        self.reset()

    def reset(self):
        self.first = None
        self.last = -1

    def process(self, times, values):
        if not len(times):
            return np.array([], dtype=float)
        if self.first is None:
            self.first = int(round(times[0]*common.FS))
        cutoff = int(round(times[-1]*common.FS)) - 144
        left = np.searchsorted(self.reference, max(self.first, self.last+1))
        right = np.searchsorted(self.reference, cutoff, side='right')
        peaks = self.reference[left:max(left, right)]
        if len(peaks):
            self.last = int(peaks[-1])
        return peaks/common.FS


class QualityAudit:
    """Observe the existing gate; do not alter decisions or reset semantics."""
    def __init__(self, gate):
        self.gate = gate
        self.allowed_samples = 0
        self.evaluated_samples = 0

    def process(self, values):
        permissions = self.gate.process(values)
        self.allowed_samples += int(permissions.sum())
        self.evaluated_samples += len(values)
        return permissions

    def __getattr__(self, name):
        return getattr(self.gate, name)


def classification_metrics(confusion, support, predicted):
    cm = np.asarray(confusion, dtype=int)
    support, predicted = np.asarray(support, dtype=int), np.asarray(predicted, dtype=int)
    if cm.shape != (3, 3) or support.shape != (3,) or predicted.shape != (3,):
        raise ValueError('Expected three-class counts')
    if (cm < 0).any() or (support < cm.sum(axis=1)).any() or (predicted < cm.sum(axis=0)).any():
        raise ValueError('Inconsistent classification counts')
    tp = cm.diagonal()
    precision = [float(t/p) if p else None for t, p in zip(tp, predicted)]
    recall = [float(t/s) if s else None for t, s in zip(tp, support)]
    f1 = [float(2*t/(s+p)) if s+p else None for t, s, p in zip(tp, support, predicted)]
    defined = [value for value in f1 if value is not None]
    return {'precision': precision, 'recall': recall, 'f1': f1,
            'macro_f1': float(np.mean(defined)) if defined else None,
            'macro_classes': [c for c, value in zip(common.LABELS, f1) if value is not None]}


def summarize_counts(confusion, support, predicted, outside, unmatched):
    cm = np.asarray(confusion, dtype=int)
    support, predicted = np.asarray(support, dtype=int), np.asarray(predicted, dtype=int)
    matched = classification_metrics(cm, cm.sum(axis=1), cm.sum(axis=0))
    matched['accuracy'] = float(np.trace(cm)/cm.sum()) if cm.sum() else None
    return {'labels': list(common.LABELS), 'support': support.tolist(),
            'predicted': predicted.tolist(), 'confusion': cm.tolist(),
            'unclassified_target_beats': (support-cm.sum(axis=1)).tolist(),
            'out_of_scope_predictions': list(map(int, outside)),
            'unmatched_predictions': list(map(int, unmatched)),
            'target_coverage': float(cm.sum()/support.sum()) if support.sum() else None,
            'correct_over_all_target_beats': float(np.trace(cm)/support.sum()) if support.sum() else None,
            'matched_only': matched, 'end_to_end': classification_metrics(cm, support, predicted)}


def score_predictions(reference, symbols, predictions, tolerance=54):
    if len(reference) != len(symbols):
        raise ValueError('Reference positions and symbols must have equal lengths')
    indices = {label: i for i, label in enumerate(common.LABELS)}
    if any(p['label'] not in indices for p in predictions):
        raise ValueError('Unknown predicted label')
    peaks = [p['r_sample'] for p in predictions]
    ri, ti = common.match_peaks(reference, peaks, tolerance)
    support = np.array([sum(common.CLASS_MAP.get(s) == label for s in symbols)
                        for label in common.LABELS], dtype=int)
    predicted = np.array([sum(p['label'] == label for p in predictions)
                          for label in common.LABELS], dtype=int)
    cm = np.zeros((3, 3), dtype=int)
    outside, unmatched = np.zeros(3, dtype=int), np.zeros(3, dtype=int)
    for r, t in zip(ri, ti):
        label = common.CLASS_MAP.get(symbols[r])
        column = indices[predictions[t]['label']]
        if label is None:
            outside[column] += 1
        else:
            cm[indices[label], column] += 1
    matched_indices = set(ti)
    for t, prediction in enumerate(predictions):
        if t not in matched_indices:
            unmatched[indices[prediction['label']]] += 1
    result = summarize_counts(cm, support, predicted, outside, unmatched)
    result['reference_out_of_scope_beats'] = sum(s not in common.CLASS_MAP for s in symbols)
    result['matching_tolerance_ms'] = tolerance*1000/common.FS
    if not np.array_equal(cm.sum(axis=0)+outside+unmatched, predicted):
        raise RuntimeError('Prediction accounting did not balance')
    return result


def aggregate_classification(rows):
    keys = ('confusion', 'support', 'predicted', 'out_of_scope_predictions', 'unmatched_predictions')
    result = summarize_counts(*(np.sum([r[key] for r in rows], axis=0) for key in keys))
    result['reference_out_of_scope_beats'] = sum(r['reference_out_of_scope_beats'] for r in rows)
    return result


def replay_classifier(adc, *, reference_peaks=None):
    """The normal path has no annotation input. Freeze the same model in both paths."""
    engine = StreamingClassifier()
    if reference_peaks is not None:
        engine.detector = ReferenceDetector(reference_peaks)
    audit = QualityAudit(engine.gate)
    engine.gate = audit
    rows, rejections = [], []
    block_ms = []
    started = time.perf_counter()
    for first in range(0, len(adc), 36):
        block_started = time.perf_counter()
        points = tuple(SamplePoint(i, 0, i, i % 256, int(adc[i]), mode=0)
                       for i in range(first, min(first+36, len(adc))))
        batch = SampleBatch('public-database-benchmark', 'replay', points, first/360,
                            'MIT-BIH public data converted to ADC; not live capture')
        rows.extend(engine.process(batch))
        rejections.extend(engine.take_rejections())
        block_ms.append((time.perf_counter()-block_started)*1000)
    elapsed = time.perf_counter()-started
    latency = [r['sample_latency_ms'] for r in rows]
    inference = [r['inference_ms'] for r in rows]
    metadata = {
        'quality_allowed_samples': audit.allowed_samples,
        'quality_evaluated_samples': audit.evaluated_samples,
        'quality_coverage': audit.allowed_samples/len(adc) if len(adc) else None,
        'emitted_predictions': len(rows), 'rejected_candidates': len(rejections),
        'rejection_reasons': dict(Counter(r['reason'] for r in rejections)),
        'final_status': engine.snapshot()['state'],
        'performance': {'wall_seconds': elapsed,
                        'real_time_factor': elapsed/(len(adc)/360) if len(adc) else None,
                        'block_p95_ms': float(np.percentile(block_ms, 95)) if block_ms else None,
                        'confirmation_p95_ms': float(np.percentile(latency, 95)) if latency else None,
                        'inference_p95_ms': float(np.percentile(inference, 95)) if inference else None}}
    return rows, rejections, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--records', nargs='+', default=list(common.CLASSIFICATION_RECORDS))
    parser.add_argument('--seconds', type=float, help='omit for the full record')
    args = parser.parse_args()
    common.validate_records(args.records, common.CLASSIFICATION_RECORDS)
    common.validate_paths(args.data, args.output)
    import wfdb.processing  # noqa: F401 - fail early if missing
    common.prepare_output(args.output)
    sources = [Path(__file__), common.ROOT/'research/benchmark_common.py',
               common.ROOT/'research/evaluate_ecg.py', common.ROOT/'requirements-research.txt',
               common.ROOT/'cloud_model/SOURCE.json',
               common.ROOT/'cloud_model/vendor/cardioedge/portable_weights.npz']
    sources += list((common.ROOT/'cloud_model/vendor').rglob('*.py'))
    hashes = common.fingerprints(sources)
    report = {'version': VERSION, 'created_utc': common.timestamp(),
              'configuration': {'records': args.records, 'seconds': args.seconds,
                                'fs': 360, 'lead': 'MLII', 'adc_gain_counts_per_mv': common.GAIN,
                                'adc_offset': 2048, 'adc_quantization': 'rint, clip 0..4095',
                                'model': CONFIG, 'class_map': common.CLASS_MAP,
                                'scoring_window': 'full record including startup, tail, gate and rejection',
                                'excluded_record': '202: same patient as training record 201',
                                'upstream_training_and_validation_records': list(common.TRAIN_RECORDS),
                                'primary_matching_ms': 150, 'sensitivity_matching_ms': 50,
                                'reference_r_condition': 'reference beat positions released after 0.4s; labels never supplied; all other production gates unchanged'},
              'environment': common.environment(['numpy', 'scipy', 'wfdb']),
              'code_sha256': hashes, 'records': [],
              'limitations': ['Fixed model, no retraining or threshold tuning',
                              'Independent execution, not a newly collected external or untouched blind test',
                              'Reference-R is a diagnostic streaming condition, not the upstream whole-record-normalization protocol',
                              'N is a beat group, not a healthy person or confirmed sinus rhythm',
                              'Scores are uncalibrated; public-data replay is not clinical validation',
                              'Runtime is local and excludes loading, network and server queues']}
    for record in args.records:
        adc, reference, symbols, metadata = common.load_record(args.data, record, args.seconds)
        result = dict(metadata, modes={})
        for mode in MODES:
            rows, rejections, replay = replay_classifier(
                adc, reference_peaks=reference if mode == 'reference_r' else None)
            result['modes'][mode] = dict(replay,
                scores_150ms=score_predictions(reference, symbols, rows, 54),
                scores_50ms=score_predictions(reference, symbols, rows, 18))
            common.verify_inputs(args.data, metadata)
            common.verify_code(hashes)
            common.write_json(args.output/'details'/f'{record}-{mode}.json',
                              {'predictions': rows, 'rejections': rejections})
            print(record, mode, 'predictions', len(rows), 'seconds', round(replay['performance']['wall_seconds'], 2),
                  'F1', result['modes'][mode]['scores_150ms']['end_to_end']['macro_f1'], flush=True)
        report['records'].append(result)
        # Explicit checkpoint name, never confused with the completed report.
        common.write_json(args.output/'incomplete.json', report)
    report['aggregate'] = {}
    for mode in MODES:
        rows = [r['modes'][mode] for r in report['records']]
        report['aggregate'][mode] = {
            key: aggregate_classification([r[key] for r in rows])
            for key in ('scores_150ms', 'scores_50ms')}
        report['aggregate'][mode]['rejected_candidates'] = sum(r['rejected_candidates'] for r in rows)
        report['aggregate'][mode]['performance'] = {
            'wall_seconds': sum(r['performance']['wall_seconds'] for r in rows),
            'signal_seconds': sum(r['signal_seconds'] for r in report['records'])}
    common.verify_code(hashes)
    common.write_json(args.output/'classification-benchmark.json', report)
    print('Completed', len(report['records']), 'records', flush=True)


if __name__ == '__main__':
    main()
