"""Apply a verified ECG cloud bundle on the existing Ubuntu host with code rollback."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import time
import urllib.request
import urllib.error
import zipfile


def extract_verified(package, destination):
    with zipfile.ZipFile(package) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or archive.testzip():
            raise ValueError('Duplicate or damaged archive entries')
        for entry in archive.infolist():
            path=PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename or
                    not entry.filename.startswith('ecg-cloud/') or
                    stat.S_ISLNK(entry.external_attr>>16)):
                raise ValueError('Unsafe archive entry')
        manifest=archive.read('ecg-cloud/MANIFEST.sha256').decode('utf-8').splitlines()
        expected={}
        for line in manifest:
            digest,name=line.split('  ',1)
            if name in expected:raise ValueError('Duplicate manifest entry')
            expected[name]=digest
        if set(names)!={'ecg-cloud/'+name for name in expected}|{'ecg-cloud/MANIFEST.sha256'}:
            raise ValueError('Manifest does not cover the archive')
        for name,digest in expected.items():
            if hashlib.sha256(archive.read('ecg-cloud/'+name)).hexdigest()!=digest:
                raise ValueError('Manifest hash mismatch')
        archive.extractall(destination)
    return Path(destination)/'ecg-cloud'


def venv_after_switch(venv, app, previous):
    """Internal environments move with the old app; external environments stay put."""
    try:
        return previous/venv.relative_to(app)
    except ValueError:
        return venv


def apply_update(directory):
    os.umask(0o077)
    home=Path.home()
    if str(home)!='/home/ubuntu':raise RuntimeError('Run as the existing ubuntu service user')
    os.environ['XDG_RUNTIME_DIR']='/run/user/'+str(os.getuid())
    source=Path(directory).resolve();metadata=json.loads((source/'update.json').read_text())
    for name,digest in metadata['files'].items():
        file=source/name
        if file.parent!=source or file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Update artifact checksum mismatch')
    app=home/'ecg-cloud';data=home/'ecg-cloud-data'
    if not app.is_dir() or app.is_symlink() or not data.is_dir() or data.is_symlink():
        raise RuntimeError('Unexpected application or data path')
    venv=(app/'.venv').resolve(strict=True)
    backup=home/'ecg-backups'/('desktop-110-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
    backup.mkdir(parents=True,exist_ok=False)
    candidate=extract_verified(source/'ecg-cloud-linux.zip',backup/'stage')
    downloads=candidate/'deploy/dist/desktop';downloads.mkdir(parents=True)
    for name in ['心电工作台-Windows免安装.zip','心电工作台-Windows免安装.zip.sha256']:
        shutil.copy2(source/name,downloads/name)
    subprocess.run([str(venv/'bin/python'),'-m','compileall','-q',str(candidate)],check=True)
    # Compile source before adding the link so compileall cannot walk the venv.
    (candidate/'.venv').symlink_to(venv_after_switch(venv,app,backup/'previous-app'),target_is_directory=True)
    def service(verb):subprocess.run(['systemctl','--user',verb,'ecg-cloud.service'],check=True)
    def counts():
        with sqlite3.connect(data/'cloud.sqlite') as db:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Database check failed')
            return {t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('sessions','chunks','users')}
    def raw_hashes():
        return {str(p.relative_to(data)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (data/'sessions').glob('*/chunks/*.bin')}
    service('is-active');service('stop');switched=False
    try:
        before=counts();raw=raw_hashes();shutil.copytree(data,backup/'data',symlinks=True)
        (backup/'before.json').write_text(json.dumps({'counts':before,'raw_files':raw}),encoding='utf-8')
        for name,digest in raw.items():
            if hashlib.sha256((backup/'data'/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Backup verification failed')
        app.rename(backup/'previous-app');switched=True;candidate.rename(app)
        service('start')
        for _ in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8766/health',timeout=2) as r:health=json.load(r)
                if health.get('auth_required') and health.get('model_status')=='ready':break
            except (OSError,ValueError):pass
            time.sleep(1)
        else:raise RuntimeError('Authenticated service/model did not recover')
        try:urllib.request.urlopen('http://127.0.0.1:8766/api/sessions',timeout=3)
        except urllib.error.HTTPError as error:
            if error.code!=401:raise
        else:raise RuntimeError('Unauthenticated access was not rejected')
        with urllib.request.urlopen('http://127.0.0.1:8766/downloads/ecg-workspace-windows.zip',timeout=10) as r:
            if hashlib.sha256(r.read()).hexdigest()!=metadata['files']['心电工作台-Windows免安装.zip']:
                raise RuntimeError('Download checksum mismatch')
        after=counts();current=raw_hashes()
        if any(after[t]<before[t] for t in before) or any(current.get(p)!=digest for p,digest in raw.items()):
            raise RuntimeError('Existing records changed unexpectedly')
        result={'backup':str(backup),'before':before,'after':after,'preserved_raw_files':len(raw),'version':'1.1.0'}
        (home/'ecg-desktop-deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print('DESKTOP_DEPLOYMENT_OK',json.dumps(result),flush=True)
    except Exception:
        service('stop')
        if switched:
            if app.exists():app.rename(backup/'failed-app')
            (backup/'previous-app').rename(app)
        # Retain current data: new uploads may have arrived after service start.
        service('start')
        print('ROLLED_BACK_CODE; current data retained; backup:',backup,flush=True)
        raise


if __name__=='__main__':apply_update(Path(__file__).parent)
