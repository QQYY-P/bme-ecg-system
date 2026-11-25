"""Run the project suites on a restricted workstation and record machine-readable evidence.

Differences from `tools/run_checks.py`:

* the web suite is executed **one test file per process** instead of `node --test`,
  because `node --test` spawns workers and some restricted environments reject that;
* `--adapt-windows-sandbox` temporarily relaxes Windows permission primitives for
  private temporary directories (0700/0600), which some sandboxes deny to their own
  creator; the adaptation is process-local and never touches repository files;
* results are written as JSON (`local-verification-v1`) so a document can link to
  numbers instead of repeating them by hand.

This tool only reports what happened. It never claims that passing software tests
equals device verification or deployment.

Usage (from the repository root):

    python tools/local_verification.py --suite cloud --json out.json
    python tools/local_verification.py --suite all --adapt-windows-sandbox --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {
    'pi': ROOT / '树莓派心电终端',
    'cloud': ROOT / '心电云端平台',
}
SCHEMA = 'local-verification-v1'

_RAN = re.compile(r'^Ran (\d+) tests? in ', re.MULTILINE)
_FAILED = re.compile(r'^FAILED \((.*)\)$', re.MULTILINE)
_OK = re.compile(r'^OK(?: \((.*)\))?$', re.MULTILINE)
_NODE_OK = re.compile(r'^\s*[✔√]\s')
_NODE_BAD = re.compile(r'^\s*[✖×]\s')

_BLOCKED_MARKERS = (
    ('named pipe', ('PermissionError', 'Pipe')),          # multiprocessing on restricted Windows
    ('process spawn', ('EPERM', 'spawn')),                # node --test worker spawn
)


def parse_unittest_summary(text: str) -> dict:
    """Extract counts from unittest's final summary lines."""
    out = {'ran': 0, 'failed': 0, 'errors': 0, 'skipped': 0, 'ok': False}
    ran = _RAN.search(text)
    if ran:
        out['ran'] = int(ran.group(1))
    failed = _FAILED.search(text)
    if failed:
        for part in failed.group(1).split(','):
            key, _, value = part.strip().partition('=')
            key = key.strip()
            if key in ('failures', 'errors', 'skipped') and value.strip().isdigit():
                out['failed' if key == 'failures' else key] = int(value.strip())
        return out
    ok = _OK.search(text)
    if ok:
        out['ok'] = True
        body = ok.group(1) or ''
        for part in body.split(','):
            key, _, value = part.strip().partition('=')
            if key.strip() == 'skipped' and value.strip().isdigit():
                out['skipped'] = int(value.strip())
    return out


def parse_node_output(text: str) -> dict:
    """Count node:test result lines in a single-file run."""
    passed = failed = 0
    for line in text.splitlines():
        if _NODE_BAD.match(line):
            failed += 1
        elif _NODE_OK.match(line):
            passed += 1
    return {'passed': passed, 'failed': failed}


def blocked_reason(text: str) -> str:
    """Classify a failure as an environment blocker rather than a code defect."""
    for name, needles in _BLOCKED_MARKERS:
        if all(needle in text for needle in needles):
            return name
    return ''


def _child_bootstrap_dir() -> Path:
    """Write a sitecustomize that relaxes private temp permissions in child processes."""
    directory = Path(tempfile.mkdtemp(prefix='local-verification-shim-'))
    (directory / 'sitecustomize.py').write_text(
        'import os\n'
        'if os.name == "nt":\n'
        '    _mkdir, _chmod = os.mkdir, os.chmod\n'
        '    os.mkdir = lambda p, mode=0o777, *, dir_fd=None: (\n'
        '        _mkdir(p, 0o777) if dir_fd is None else _mkdir(p, 0o777, dir_fd=dir_fd))\n'
        '    def _relaxed(p, mode, *, dir_fd=None, follow_symlinks=True):\n'
        '        if mode in (0o700, 0o600, 0o500, 0o400, 0o750, 0o640):\n'
        '            mode = 0o777 if (mode & 0o100) else 0o666\n'
        '        return _chmod(p, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks)\n'
        '    os.chmod = _relaxed\n',
        encoding='utf-8',
    )
    return directory


def adapt_windows_sandbox() -> None:
    """Relax private temp permissions in this process (Windows only, opt-in)."""
    if os.name != 'nt':
        return
    import tempfile as _tf

    real_mkdir, real_chmod, real_mkdtemp = os.mkdir, os.chmod, _tf.mkdtemp

    def mkdir(path, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            return real_mkdir(path, 0o777)
        return real_mkdir(path, 0o777, dir_fd=dir_fd)

    def chmod(path, mode, *, dir_fd=None, follow_symlinks=True):
        if mode in (0o700, 0o600, 0o500, 0o400, 0o750, 0o640):
            mode = 0o777 if (mode & 0o100) else 0o666
        return real_chmod(path, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def mkdtemp(suffix=None, prefix=None, dir=None):
        base = dir or _tf.gettempdir()
        names = _tf._RandomNameSequence()
        for _ in range(10000):
            candidate = os.path.join(base, (prefix or 'tmp') + next(names) + (suffix or ''))
            try:
                os.makedirs(candidate, exist_ok=True)
                return candidate
            except FileExistsError:
                continue
        raise FileExistsError('could not create a temporary directory')

    os.mkdir, os.chmod, _tf.mkdtemp = mkdir, chmod, mkdtemp


def run_python_suite(name: str, adapted: bool) -> dict:
    project = PROJECTS[name]
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    if adapted and os.name == 'nt':
        env['PYTHONPATH'] = os.pathsep.join(
            [str(_child_bootstrap_dir())] + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py']
    started = time.time()
    proc = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    seconds = round(time.time() - started, 1)
    text = (proc.stdout or '') + (proc.stderr or '')
    counts = parse_unittest_summary(text)
    reason = blocked_reason(text) if proc.returncode else ''
    record = {
        'suite': name,
        'command': 'python -m unittest discover -s tests -p test_*.py',
        'working_directory': project.relative_to(ROOT).as_posix(),
        'returncode': proc.returncode,
        'seconds': seconds,
        'ran': counts['ran'],
        'passed': max(counts['ran'] - counts['failed'] - counts['errors'] - counts['skipped'], 0),
        'failed': counts['failed'],
        'errors': counts['errors'],
        'skipped': counts['skipped'],
        'blocked': reason,
    }
    return record


def run_web_suite(adapted: bool) -> dict:
    project = PROJECTS['cloud']
    node = shutil.which('node')
    files = sorted((project / 'tests').glob('*.test.cjs'))
    record = {
        'suite': 'web', 'command': 'node <each tests/*.test.cjs>',
        'working_directory': project.relative_to(ROOT).as_posix(),
        'returncode': 0, 'seconds': 0.0, 'ran': 0, 'passed': 0,
        'failed': 0, 'errors': 0, 'skipped': 0, 'blocked': '',
    }
    if not node or not files:
        record['blocked'] = 'node or web tests unavailable'
        return record
    started = time.time()
    for path in files:
        proc = subprocess.run([node, str(path.relative_to(project))], cwd=project,
                              capture_output=True, text=True, encoding='utf-8', errors='replace')
        counts = parse_node_output((proc.stdout or '') + (proc.stderr or ''))
        record['ran'] += counts['passed'] + counts['failed']
        record['passed'] += counts['passed']
        record['failed'] += counts['failed']
        if proc.returncode:
            record['returncode'] = proc.returncode
            record['blocked'] = record['blocked'] or blocked_reason(
                (proc.stdout or '') + (proc.stderr or ''))
    record['seconds'] = round(time.time() - started, 1)
    return record


def summarize(records: list) -> dict:
    keys = ('ran', 'passed', 'failed', 'errors', 'skipped')
    totals = {key: sum(r.get(key, 0) for r in records) for key in keys}
    totals['blocked'] = [r['suite'] for r in records if r.get('blocked')]
    totals['unexpected'] = sum(r.get('failed', 0) + r.get('errors', 0) for r in records
                               if not r.get('blocked'))
    return totals


def node_version(node: str) -> str:
    """Return the Node.js version, never the executable path (it is machine-specific)."""
    if not node:
        return ""
    try:
        proc = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=15)
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def build_report(records: list, adapted: bool, node: str) -> dict:
    return {
        'kind': SCHEMA,
        'generated_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'environment': {
            'platform': platform.platform(),
            'python': platform.python_version(),
            'node': node_version(node),
            'adapted_windows_sandbox': bool(adapted),
        },
        'suites': records,
        'totals': summarize(records),
        'note': ('Software test results only. They do not prove device, deployment or '
                 'clinical verification.'),
    }


def print_table(report: dict) -> None:
    print(f"{'suite':<8}{'ran':>6}{'pass':>7}{'fail':>6}{'error':>6}{'skip':>6}{'sec':>8}  blocked")
    for row in report['suites']:
        print(f"{row['suite']:<8}{row['ran']:>6}{row['passed']:>7}{row['failed']:>6}"
              f"{row['errors']:>6}{row['skipped']:>6}{row['seconds']:>8}  {row['blocked'] or '-'}")
    totals = report['totals']
    print(f"{'total':<8}{totals['ran']:>6}{totals['passed']:>7}{totals['failed']:>6}"
          f"{totals['errors']:>6}{totals['skipped']:>6}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('all', 'cloud', 'pi', 'web'), default='all')
    parser.add_argument('--json', type=Path, help='write the JSON evidence to this path')
    parser.add_argument('--adapt-windows-sandbox', action='store_true',
                        help='relax private temp permissions (Windows sandboxes only)')
    args = parser.parse_args(argv)

    adapted = bool(args.adapt_windows_sandbox and os.name == 'nt')
    if adapted:
        adapt_windows_sandbox()

    records = []
    if args.suite in ('all', 'pi'):
        records.append(run_python_suite('pi', adapted))
    if args.suite in ('all', 'cloud'):
        records.append(run_python_suite('cloud', adapted))
    if args.suite in ('all', 'web'):
        records.append(run_web_suite(adapted))

    report = build_report(records, adapted, shutil.which('node'))
    print_table(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')
        print('written:', args.json)

    if report['totals']['unexpected']:
        return 1
    if report['totals']['blocked']:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

