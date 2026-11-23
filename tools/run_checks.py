"""Run the existing project suites in separate processes and working directories."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {"pi": ROOT / "树莓派心电终端", "cloud": ROOT / "心电云端平台"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "pi", "cloud", "web"), default="all")
    args = parser.parse_args()
    suites = ("pi", "cloud", "web") if args.suite == "all" else (args.suite,)
    failed = []
    for suite in suites:
        print(f"\nRunning {suite} checks", flush=True)
        if suite == "web":
            node = shutil.which("node")
            files = sorted((PROJECTS["cloud"] / "tests").glob("*.test.cjs"))
            if not node or not files:
                print("Node.js or web tests unavailable", file=sys.stderr)
                failed.append(suite)
                continue
            command = [node, "--test", *map(str, files)]
            cwd = PROJECTS["cloud"]
        else:
            cwd = PROJECTS[suite]
            if not list((cwd / "tests").glob("test_*.py")):
                print(f"No tests found for {suite}", file=sys.stderr)
                failed.append(suite)
                continue
            command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
        # Keep test scratch files inside the clone, also in restricted workspaces.
        scratch = ROOT / "tools" / ".test_runs"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=suite + "-", dir=scratch) as temporary:
            environment = dict(os.environ, TMP=temporary, TEMP=temporary, TMPDIR=temporary)
            environment.setdefault("PYTHONIOENCODING", "utf-8")
            result = subprocess.run(command, cwd=cwd, env=environment, check=False)
        if result.returncode:
            failed.append(suite)
    if failed:
        print("Failed suites: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("All selected suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
