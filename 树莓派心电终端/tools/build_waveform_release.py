"""Build and verify the standalone waveform release from an explicit allowlist.

Run with a Python that has NumPy, SciPy, pyserial and Tk available. Tests run
from a newly extracted tree under evidence/07_release_check with isolated
Python import paths. No model, weights or recorded data are packaged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    "app.py", "controller.py", "acquisition.py", "contracts.py", "protocol.py",
    "notch_filter.py", "recording.py", "rpeak_detector.py", "signal_analysis.py", "signal_quality.py", "serial_connection.py", "preprocessing.py",
    "ui/__init__.py", "ui/main_window.py", "ui/waveform.py",
)
TEST_FILES = (
    "tests/test_acquisition.py", "tests/test_controller.py",
    "tests/test_notch_filter.py", "tests/test_protocol.py",
    "tests/test_recording.py", "tests/test_signal_analysis.py", "tests/test_waveform.py",
    "tests/test_serial_connection.py", "tests/test_preprocessing.py", "tests/test_signal_quality.py",
)
SUPPORT_FILES = (
    "README.txt", "requirements-pi.txt", "start-screen.sh", "start-demo.sh",
    "tools/verify_waveform_runtime.py",
    "tools/verify_stm32_serial.py", "tools/verify_status_feedback.py",
    "docs/STM32串口对接说明.txt", "docs/实采预处理说明.txt", "docs/状态与信号质量说明.txt",
)
RELEASE_FILES = RUNTIME_FILES + TEST_FILES + SUPPORT_FILES
ARCHIVE_ROOT = "ecg_terminal"

TEST_RUNNER = r'''
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

root, report_file, temporary = map(Path, sys.argv[1:4])
root = root.resolve()
temporary.mkdir(parents=True, exist_ok=False)
tempfile.tempdir = str(temporary)
sys.path.insert(0, str(root))
for forbidden in ("models", "data", "numpy_predictor.py", "ecg_classifier.py", "inference_worker.py"):
    if (root / forbidden).exists():
        raise RuntimeError("Forbidden model/history item in release: " + forbidden)
suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
modules = ("app", "controller", "acquisition", "contracts", "protocol", "notch_filter",
           "recording", "rpeak_detector", "signal_analysis", "signal_quality", "serial_connection", "preprocessing", "ui.main_window", "ui.waveform")
module_files = {}
for name in modules:
    path = Path(importlib.import_module(name).__file__).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("Runtime module imported outside release: " + name + " from " + str(path))
    module_files[name] = str(path.relative_to(root))
model_imports = sorted(name for name in sys.modules
                       if name.split(".", 1)[0] in ("numpy_predictor", "ecg_classifier",
                                                   "inference_worker", "torch", "onnxruntime"))
successful = result.wasSuccessful() and not result.skipped and not model_imports
report = {"successful": successful, "tests_run": result.testsRun,
          "failures": [{"test": str(test), "traceback": trace} for test, trace in result.failures],
          "errors": [{"test": str(test), "traceback": trace} for test, trace in result.errors],
          "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
          "module_files": module_files, "model_modules_imported": model_imports,
          "isolated_python": bool(sys.flags.isolated), "python": sys.version,
          "executable": sys.executable, "release_root": str(root)}
report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("RELEASE_TEST_RESULT " + json.dumps({"tests_run": result.testsRun, "successful": successful,
                                           "failures": len(result.failures), "errors": len(result.errors),
                                           "skipped": len(result.skipped)}))
raise SystemExit(0 if successful else 1)
'''


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_snapshot(root):
    contents = {}
    for name in RELEASE_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Release input must be a regular file: " + name)
        contents[name] = path.read_bytes()
    manifest = "".join(f"{sha256(contents[name])}  {name}\n" for name in sorted(contents))
    contents["MANIFEST.sha256"] = manifest.encode("utf-8")
    return contents


def make_archives(contents, zip_path, tar_path, release_date):
    stamp = (release_date.year, release_date.month, release_date.day, 0, 0, 0)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(contents):
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{name}", date_time=stamp)
            info.create_system = 3
            info.external_attr = ((0o100755 if name.endswith(".sh") else 0o100644) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents[name])
    with tar_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name in sorted(contents):
                    info = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{name}")
                    info.size = len(contents[name])
                    info.mode = 0o755 if name.endswith(".sh") else 0o644
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    archive.addfile(info, io.BytesIO(contents[name]))


def verify_and_extract(contents, zip_path, tar_path, directory):
    expected = {f"{ARCHIVE_ROOT}/{name}" for name in contents}
    with zipfile.ZipFile(zip_path) as archive:
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError("ZIP membership differs from the explicit release list")
        for name, original in contents.items():
            member = f"{ARCHIVE_ROOT}/{name}"
            data = archive.read(member)
            if data != original:
                raise ValueError("ZIP data mismatch: " + name)
            # Extract only known relative names, never arbitrary paths from an archive.
            destination = directory / ARCHIVE_ROOT / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    with tarfile.open(tar_path, "r:gz") as archive:
        members = archive.getmembers()
        if {item.name for item in members} != expected or len(members) != len(expected):
            raise ValueError("tar membership differs from the explicit release list")
        for item in members:
            if not item.isfile():
                raise ValueError("Unexpected non-file tar member")
            name = item.name.removeprefix(ARCHIVE_ROOT + "/")
            with archive.extractfile(item) as stream:
                if stream.read() != contents[name]:
                    raise ValueError("tar data mismatch: " + name)
    extracted = directory / ARCHIVE_ROOT
    for line in (extracted / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        expected_hash, name = line.split("  ", 1)
        if sha256((extracted / name).read_bytes()) != expected_hash:
            raise ValueError("Extracted manifest check failed: " + name)
    return extracted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="20261026", help="Release label YYYYMMDD")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter for isolated tests")
    parser.add_argument("--suffix", default="", help="Optional lowercase release variant, e.g. protocol-v2")
    args = parser.parse_args(argv)
    try:
        release_date = datetime.strptime(args.date, "%Y%m%d")
    except ValueError:
        parser.error("--date must be a valid YYYYMMDD date")
    if len(args.date) != 8 or release_date.year < 1980:
        parser.error("--date must use eight digits and year >= 1980")
    if args.suffix and (len(args.suffix) > 40 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.suffix)):
        parser.error("--suffix must contain at most 40 lowercase letters, digits or hyphens")
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
    run_dir = ROOT / "evidence" / "07_release_check" / tag
    run_dir.mkdir(parents=True, exist_ok=False)
    basename = f"ecg-waveform-no-model-{args.date}"
    if args.suffix:
        basename += "-" + args.suffix
    zip_path = run_dir / (basename + ".zip")
    tar_path = run_dir / (basename + ".tar.gz")
    contents = load_snapshot(ROOT)
    make_archives(contents, zip_path, tar_path, release_date)
    isolated = verify_and_extract(contents, zip_path, tar_path, run_dir / "extracted")
    print(f"Prepared {len(contents)} package files; testing isolated tree: {isolated}", flush=True)
    test_report = run_dir / "isolated-test-results.json"
    test_log = run_dir / "isolated-test.log"
    with test_log.open("wb") as output:
        result = subprocess.run([args.python, "-I", "-B", "-X", "utf8", "-c", TEST_RUNNER,
                                 str(isolated), str(test_report), str(run_dir / "temp")],
                                cwd=isolated, stdout=output, stderr=subprocess.STDOUT, timeout=300)
    report = json.loads(test_report.read_text(encoding="utf-8")) if test_report.exists() else {}
    changed = [name for name in RELEASE_FILES if not (ROOT / name).is_file()
               or (ROOT / name).read_bytes() != contents[name]]
    summary = {"release": basename, "created_utc": datetime.now(timezone.utc).isoformat(),
               "archive_root": ARCHIVE_ROOT, "file_count_including_manifest": len(contents),
               "test_file_count": len(TEST_FILES), "tests_run": report.get("tests_run"),
               "tests_successful": result.returncode == 0 and report.get("successful", False),
               "source_changed_during_check": changed, "test_report": str(test_report),
               "test_log": str(test_log), "isolated_directory": str(isolated),
               "model_files_included": False, "published": False}
    if summary["tests_successful"] and not changed:
        destination = ROOT / "dist"
        destination.mkdir(exist_ok=True)
        archives = []
        for path in (zip_path, tar_path):
            target = destination / path.name
            temporary = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex[:10])
            shutil.copyfile(path, temporary)
            temporary.replace(target)
            archives.append({"path": str(target), "bytes": target.stat().st_size,
                             "sha256": sha256(target.read_bytes())})
        summary.update(published=True, archives=archives)
    (run_dir / "release-check.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)
                                               + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if not summary["published"]:
        print("Release not published; inspect isolated-test.log and source_changed_during_check.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
