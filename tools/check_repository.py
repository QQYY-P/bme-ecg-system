"""Check tracked source syntax and prevent common private/generated files in Git.

This is a baseline guard, not a complete secret or medical-data detector.
Only inspect tracked paths, so local records and developer environments stay private.
"""
from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRS = {
    '.venv', 'venv', 'node_modules', '__pycache__', '.cloud_private',
    '.test_runs', 'data', 'demo_data', 'evidence', 'logs', 'work', 'dist',
    'cloud_sources', 'historical_sources', 'upload_state',
}
FORBIDDEN_SUFFIXES = {
    '.pem', '.key', '.p12', '.pfx', '.jks', '.keystore', '.sqlite',
    '.sqlite3', '.db', '.apk', '.aab', '.exe', '.zip', '.pyc', '.pyo',
}
PRIVATE_KEY = re.compile(rb'^-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----\s*$', re.MULTILINE)


def path_problem(name: str) -> str | None:
    path = PurePosixPath(name.replace('\\', '/'))
    parts = [part.lower() for part in path.parts]
    if set(parts[:-1]) & FORBIDDEN_DIRS:
        return 'private or generated directory'
    basename = parts[-1]
    if (basename == '.env' or basename.startswith('.env.')) and basename != '.env.example':
        return 'environment credentials file'
    if path.suffix.lower() in FORBIDDEN_SUFFIXES or basename.endswith('.tar.gz'):
        return 'private or generated file type'
    if any(marker in basename for marker in ('.sqlite-', '.sqlite3-', '.db-')):
        return 'database journal'
    if basename.endswith('.json') and ('credentials' in basename or 'admin-access' in basename):
        return 'credentials file'
    return None


def check_file(name: str, body: bytes) -> list[str]:
    problems = []
    reason = path_problem(name)
    if reason:
        problems.append(reason)
    if PRIVATE_KEY.search(body):
        problems.append('embedded private key')
    if name.endswith('.py'):
        try:
            ast.parse(body, filename=name)
        except (SyntaxError, ValueError) as exc:
            problems.append('Python syntax error at line ' + str(getattr(exc, 'lineno', '?')))
    return problems


def main() -> int:
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True)
    names = [name.decode('utf-8') for name in result.stdout.split(b'\0') if name]
    if not names:
        print('No tracked files: this check requires a Git checkout.', file=sys.stderr)
        return 1
    failures = []
    for name in names:
        path = ROOT / name
        if path.is_symlink():
            failures.append((name, ['symlinks require an explicitly reviewed policy']))
            continue
        if not path.is_file():
            failures.append((name, ['tracked file is missing']))
            continue
        problems = check_file(name, path.read_bytes())
        if problems:
            failures.append((name, problems))
    for name, problems in failures:
        # Never print file content, credentials, or medical data in CI output.
        print(name + ': ' + '; '.join(problems), file=sys.stderr)
    print(f'Checked {len(names)} tracked files; {len(failures)} failures.')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
