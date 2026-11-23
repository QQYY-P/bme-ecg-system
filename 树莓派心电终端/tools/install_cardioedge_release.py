"""Install a verified staging tree with backup; preserve all recordings/settings."""
import argparse,hashlib,json,tarfile,time
from pathlib import Path

EXPECTED_OLD={
 'controller.py':'bfe7159050eaf3e144bbd9768c2324f9e01c333abc2eefc8870781ab3256ee46',
 'app.py':'f3c8455fda58c0a4a734218eaccee42c4ad67a8821e9f5efeba47fde5cf930ba',
 'ui/main_window.py':'04f57984d330e1bcfd20ecdb2726911c8e26eb94fbe718b0eef1e4e71f11ebc8'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--target',type=Path,required=True)
    p.add_argument('--apply',action='store_true');a=p.parse_args();stage=a.stage.resolve();target=a.target.resolve()
    if not target.is_dir() or stage==target:raise ValueError('existing separate target required')
    manifest=json.loads((stage/'CARDIOEDGE_MANIFEST.json').read_text())
    for name,digest in manifest.items():
        source=(stage/name).resolve();dest=(target/name).resolve()
        if not source.is_relative_to(stage) or not dest.is_relative_to(target):raise ValueError('path escape')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:raise ValueError('staged hash mismatch: '+name)
    for name,digest in EXPECTED_OLD.items():
        if hashlib.sha256((target/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Current application changed after inspection: '+name)
    for name in ('start-screen.sh','start-demo.sh','acquisition.py','contracts.py','protocol.py','preprocessing.py',
                 'signal_analysis.py','signal_quality.py','recording.py','rpeak_detector.py','serial_connection.py',
                 'notch_filter.py','ui/waveform.py'):
        if hashlib.sha256((target/name).read_bytes()).hexdigest()!=manifest[name]:
            raise ValueError('Unmodified runtime differs from tested staging tree: '+name)
    if not a.apply:print('Verified staging hashes and original target; no files changed.');return
    stamp=time.strftime('%Y%m%d-%H%M%S')
    backup=target.parent/('ecg-before-cardioedge-'+stamp+'.tar.gz')
    with tarfile.open(backup,'w:gz') as archive:
        for name in manifest:
            if (target/name).is_file():archive.add(target/name,arcname=name,recursive=False)
    installed=[]
    try:
        for name in manifest:
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True)
            temp=dest.with_name(dest.name+'.cardioedge-new');temp.write_bytes((stage/name).read_bytes())
            if dest.exists():temp.chmod(dest.stat().st_mode & 0o777)
            temp.replace(dest)
            installed.append(name)
        (target/'CARDIOEDGE_MANIFEST.json').write_text(json.dumps(manifest,indent=2))
        for name,digest in manifest.items():
            assert hashlib.sha256((target/name).read_bytes()).hexdigest()==digest
    except Exception:
        # Existing app files can always be restored from the backup; recordings
        # and settings were outside the allowlist and were never opened for writing.
        with tarfile.open(backup) as archive:
            for member in archive.getmembers():
                dest=(target/member.name).resolve()
                if member.isfile() and dest.is_relative_to(target):
                    dest.write_bytes(archive.extractfile(member).read())
        raise
    report=dict(target=str(target),backup=str(backup),file_count=len(installed),hashes=manifest,
                recordings_changed=False,screen_launcher_changed=False,automatic_start_changed=False)
    (target.parent/'cardioedge-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
