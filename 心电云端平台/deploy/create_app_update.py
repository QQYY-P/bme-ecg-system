"""Update the existing cloud app without changing its model environment or data."""
import base64
import hashlib
import json
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    archive = ROOT / 'deploy/dist/ecg-cloud-linux.zip'
    raw = archive.read_bytes()
    # Validate the exact staged file set before producing any root-run commands.
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or bundle.testzip() is not None:
            raise ValueError('Archive has duplicate or damaged members')
        for item in bundle.infolist():
            if (not item.filename.startswith('ecg-cloud/') or '\\' in item.filename
                    or any(part in ('', '.', '..') for part in item.filename.split('/'))
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Archive contains an unsafe path')
        manifest = bundle.read('ecg-cloud/MANIFEST.sha256').decode('utf-8')
        expected = {}
        for line in manifest.splitlines():
            checksum, relative = line.split('  ', 1)
            name = 'ecg-cloud/' + relative
            if name in expected or hashlib.sha256(bundle.read(name)).hexdigest() != checksum:
                raise ValueError('Archive manifest is inconsistent')
            expected[name] = checksum
        if set(names) != set(expected) | {'ecg-cloud/MANIFEST.sha256'}:
            raise ValueError('Archive contains files outside its manifest')
    digest = hashlib.sha256(raw).hexdigest()
    tag = digest[:12]
    target = f'/home/ubuntu/ecg-cloud-app-{tag}'
    out = ROOT / 'deploy/dist/app-update'
    out.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, offset in enumerate(range(0, len(raw), 27000)):
        piece = raw[offset:offset+27000]
        remote = f'{target}.part{i:03d}'
        parts.append(remote)
        encoded = base64.encodebytes(piece).decode('ascii')
        script = f'''#!/usr/bin/env bash
set -euo pipefail
umask 077
test ! -L '{remote}'
if test -e '{remote}'; then
    printf '%s\\n' '{hashlib.sha256(piece).hexdigest()}  {remote}' | sha256sum --check
else
    ecg_part=$(mktemp '{remote}.uploading.XXXXXX')
    base64 --decode > "$ecg_part" <<'ECG_PACKAGE_PART'
{encoded}ECG_PACKAGE_PART
    printf '%s  %s\\n' '{hashlib.sha256(piece).hexdigest()}' "$ecg_part" | sha256sum --check
    mv -nT -- "$ecg_part" '{remote}'
    printf '%s\\n' '{hashlib.sha256(piece).hexdigest()}  {remote}' | sha256sum --check
fi
'''
        assert len(base64.b64encode(script.encode())) < 65536
        (out/f'{i+1:02d}-upload-part.sh').write_text(script, encoding='utf-8', newline='\n')
    install = r'''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test -d /home/ubuntu/ecg-cloud && test ! -L /home/ubuntu/ecg-cloud
test -d /home/ubuntu/ecg-cloud-data && test ! -L /home/ubuntu/ecg-cloud-data
for ecg_reserved in TARGET.stage TARGET.zip TARGET.previous TARGET.failed; do
    test ! -e "$ecg_reserved" && test ! -L "$ecg_reserved"
done
for ecg_part in PARTS; do test -f "$ecg_part" && test ! -L "$ecg_part"; done
cat PARTS > TARGET.zip
printf '%s\n' 'DIGEST  TARGET.zip' | sha256sum --check
mkdir TARGET.stage
unzip -q -n TARGET.zip -d TARGET.stage
chown -R ubuntu:ubuntu TARGET.stage
sudo -u ubuntu bash -c 'cd TARGET.stage/ecg-cloud && sha256sum --check --quiet MANIFEST.sha256'
cmp TARGET.stage/ecg-cloud/requirements.txt /home/ubuntu/ecg-cloud/requirements.txt
cmp TARGET.stage/ecg-cloud/requirements-model.txt /home/ubuntu/ecg-cloud/requirements-model.txt
ecg_venv=$(readlink -f /home/ubuntu/ecg-cloud/.venv)
case "$ecg_venv" in /home/ubuntu/ecg-cloud-*.venv) ;; *) exit 1;; esac
test -f "$ecg_venv/pyvenv.cfg"
test ! -e TARGET.stage/ecg-cloud/.venv
ln -s "$ecg_venv" TARGET.stage/ecg-cloud/.venv
chown -h ubuntu:ubuntu TARGET.stage/ecg-cloud/.venv
ecg_uid=$(id -u ubuntu)
ecg_systemctl() { sudo -u ubuntu env XDG_RUNTIME_DIR="/run/user/$ecg_uid" systemctl --user "$@"; }
ecg_systemctl is-active --quiet ecg-cloud.service
ecg_health() {
    python3 - "$1" <<'PY'
import json,sys,urllib.request
try:
    h=json.load(urllib.request.urlopen('http://127.0.0.1:8766/health',timeout=3))
    assert h['ok'] and h['deployment']=='cloud' and h['model_status']=='ready'
    if sys.argv[1]=='new':
        rows=json.load(urllib.request.urlopen('http://127.0.0.1:8766/api/sessions',timeout=3))['sessions']
        assert rows and all(all(key in r for key in ('provenance','first_uploaded_at','last_uploaded_at')) for r in rows)
    print(json.dumps({'service':h['service'],'model_status':h['model_status'],'version_check':sys.argv[1]}))
except Exception:raise SystemExit(1)
PY
}
ecg_health previous
ecg_armed=0
ecg_committed=0
ecg_finish() {
    ecg_code=$1
    trap - EXIT HUP INT TERM
    if test "$ecg_armed" -eq 1 && test "$ecg_committed" -eq 0; then
        set +e
        ecg_systemctl stop ecg-cloud.service
        if test -d TARGET.previous; then
            if test -e /home/ubuntu/ecg-cloud; then mv -T /home/ubuntu/ecg-cloud TARGET.failed; fi
            if test ! -e /home/ubuntu/ecg-cloud; then mv -T TARGET.previous /home/ubuntu/ecg-cloud; fi
        fi
        ecg_systemctl start ecg-cloud.service
        ecg_restored=0
        for ecg_attempt in $(seq 1 15); do
            if ecg_systemctl is-active --quiet ecg-cloud.service && ecg_health previous; then ecg_restored=1; break; fi
            sleep 1
        done
        if test "$ecg_restored" -eq 1; then printf '\nAPP_UPDATE_FAILED_PREVIOUS_SERVICE_RESTORED\n';
        else printf '\nAPP_UPDATE_FAILED_MANUAL_RECOVERY_REQUIRED\n'; fi
        test "$ecg_code" -ne 0 || ecg_code=1
    fi
    exit "$ecg_code"
}
trap 'ecg_finish $?' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
ecg_armed=1
ecg_systemctl stop ecg-cloud.service
mv -T /home/ubuntu/ecg-cloud TARGET.previous
mv -T TARGET.stage/ecg-cloud /home/ubuntu/ecg-cloud
ecg_systemctl start ecg-cloud.service
for ecg_attempt in $(seq 1 30); do
    if ecg_systemctl is-active --quiet ecg-cloud.service && ecg_health new; then
        ecg_committed=1
        printf '\nECG_APP_UPDATED\n'
        exit 0
    fi
    sleep 1
done
exit 1
'''.replace('TARGET',target).replace('DIGEST',digest).replace('PARTS',' '.join(parts))
    assert len(base64.b64encode(install.encode())) < 65536
    (out/f'{len(parts)+1:02d}-update-app.sh').write_text(install,encoding='utf-8',newline='\n')
    metadata={'archive_sha256':digest,'archive_bytes':len(raw),'part_count':len(parts),'remote_prefix':target,
              'contains_credentials':False,'contains_recordings':False,'reuses_model_environment':True}
    (out/'manifest.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print(json.dumps(metadata))

if __name__=='__main__':main()
