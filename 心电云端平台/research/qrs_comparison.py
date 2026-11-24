"""Compare frozen streaming QRS against native offline XQRS and FastNVG."""
import argparse
from pathlib import Path
import time
import warnings

import numpy as np

from research import benchmark_common as common
from research.streaming_qrs import detect_adc

ALGORITHMS = ('current', 'xqrs', 'fastnvg')
VERSION = 'qrs-comparison-v1'


def run_detector(name, adc):
    raw = np.asarray(adc)
    if name == 'current':
        return dict(detect_adc(raw, block_size=36), mode='received-data-stream',
                    preprocessing='unchanged production quality gate and QRS')
    if name not in ALGORITHMS:
        raise ValueError('Unknown detector')
    # Each algorithm sees the same quantized waveform. Affine conversion only.
    signal = (raw.astype(float)-2048)/common.GAIN
    if name == 'xqrs':
        from wfdb.processing import xqrs_detect
        operation = lambda: xqrs_detect(signal, fs=common.FS, learn=True, verbose=False)
        preprocessing = 'WFDB XQRS built-in filtering and learning; default Conf'
    else:
        import neurokit2 as nk
        def operation():
            cleaned = nk.ecg_clean(signal, sampling_rate=common.FS, method='emrich2023')
            return nk.ecg_findpeaks(cleaned, sampling_rate=common.FS,
                                    method='emrich2023')['ECG_R_Peaks']
        preprocessing = 'NeuroKit2 emrich2023 cleaning + FastNVG; no artifact correction'
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        started = time.perf_counter()
        peaks = np.asarray(operation(), dtype=int)
        elapsed = time.perf_counter()-started
    if np.any(np.diff(peaks) <= 0) or np.any((peaks < 0) | (peaks >= len(raw))):
        raise ValueError(f'{name}: invalid peak sequence')
    return {'r_samples': peaks.tolist(), 'segments': [0]*len(peaks),
            'mode': 'whole-record-offline', 'preprocessing': preprocessing,
            'quality_coverage': None, 'quality_gate': 'none; native algorithm',
            'warnings': sorted(set(str(w.message) for w in caught)),
            'performance': {'wall_seconds': elapsed,
                            'real_time_factor': elapsed/(len(raw)/common.FS),
                            'confirmation_p95_ms': None}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--records', nargs='+', default=list(common.QRS_RECORDS))
    parser.add_argument('--seconds', type=float, help='omit to evaluate full records')
    args = parser.parse_args()
    common.validate_paths(args.data, args.output)
    common.validate_records(args.records, common.QRS_RECORDS)
    # Fail before producing partial results if a required optional dependency is absent.
    import wfdb.processing, neurokit2, ts2vg  # noqa: F401
    common.prepare_output(args.output)
    sources = [Path(__file__), common.ROOT/'research/benchmark_common.py',
               common.ROOT/'research/streaming_qrs.py', common.ROOT/'research/evaluate_ecg.py',
               common.ROOT/'requirements-research.txt']
    sources += list((common.ROOT/'cloud_model/vendor').glob('*.py'))
    hashes = common.fingerprints(sources)
    report = {'version': VERSION, 'created_utc': common.timestamp(),
              'configuration': {'records': args.records, 'seconds': args.seconds,
                                'lead': 'MLII', 'fs': 360, 'adc_gain_counts_per_mv': common.GAIN,
                                'adc_offset': 2048, 'adc_quantization': 'rint, clip 0..4095',
                                'matching': 'WFDB compare_annotations, one-to-one, 150ms and 50ms',
                                'scoring_window': 'all samples including startup/tail/gated periods'},
              'environment': common.environment(['numpy', 'scipy', 'wfdb', 'neurokit2', 'ts2vg']),
              'code_sha256': hashes, 'records': [],
              'limitations': ['Public database replay; not electrode or clinical validation',
                              'Native offline candidates see the full record; current detector sees received blocks',
                              'Different native filters and gates are retained, not a controlled causal algorithm swap',
                              'Runtime excludes input I/O, library imports, network and queue; not server capacity',
                              'This cohort has been inspected before; no claim of untouched test data']}
    for record in args.records:
        adc, reference, _, metadata = common.load_record(args.data, record, args.seconds)
        row = dict(metadata, algorithms={})
        details = {}
        for name in ALGORITHMS:
            detected = run_detector(name, adc)
            peaks, segments = detected['r_samples'], detected['segments']
            row['algorithms'][name] = {
                k: v for k, v in detected.items() if k not in ('r_samples', 'segments')}
            row['algorithms'][name]['qrs_150ms'] = common.qrs_metrics(reference, peaks, segments, 54)
            row['algorithms'][name]['qrs_50ms'] = common.qrs_metrics(reference, peaks, segments, 18)
            details[name] = {'r_samples': peaks, 'segments': segments}
            print(record, name, 'seconds', round(detected['performance']['wall_seconds'], 3), flush=True)
        common.verify_inputs(args.data, metadata)
        common.verify_code(hashes)
        common.write_json(args.output/'details'/f'{record}.json', details)
        report['records'].append(row)
        common.write_json(args.output/'incomplete.json', report)
        print(record, {k: round(v['qrs_150ms']['f1'] or 0, 5)
                       for k, v in row['algorithms'].items()}, flush=True)
    report['aggregate'] = {}
    duration = sum(r['signal_seconds'] for r in report['records'])
    for name in ALGORITHMS:
        rows = [r['algorithms'][name] for r in report['records']]
        elapsed = sum(r['performance']['wall_seconds'] for r in rows)
        report['aggregate'][name] = {
            key: common.aggregate_qrs([r[key] for r in rows]) for key in ('qrs_150ms', 'qrs_50ms')}
        report['aggregate'][name]['performance'] = {
            'wall_seconds': elapsed, 'signal_seconds': duration, 'real_time_factor': elapsed/duration}
    common.verify_code(hashes)
    common.write_json(args.output/'qrs-comparison.json', report)
    print('Completed', len(report['records']), 'records', flush=True)


if __name__ == '__main__':
    main()
