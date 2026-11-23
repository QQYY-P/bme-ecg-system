"""Prepare bounded Tencent console commands; no credentials or recordings included."""
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
    target = f'/home/ubuntu/ecg-cloud-model-{tag}'
    out = ROOT / 'deploy/dist/model-update'
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
test -d /home/ubuntu/ecg-cloud
test ! -L /home/ubuntu/ecg-cloud
test -d /home/ubuntu/ecg-cloud-data
test ! -L /home/ubuntu/ecg-cloud-data
for ecg_reserved in TARGET.stage TARGET.zip TARGET.venv TARGET.previous TARGET.failed TARGET.backup.tgz TARGET.previous-health.json TARGET.disabled-model.conf; do
    test ! -e "$ecg_reserved" && test ! -L "$ecg_reserved"
done
for ecg_part in PARTS; do
    test -f "$ecg_part" && test ! -L "$ecg_part"
done
cat PARTS > TARGET.zip
printf '%s\n' 'DIGEST  TARGET.zip' | sha256sum --check
mkdir TARGET.stage
unzip -q -n TARGET.zip -d TARGET.stage
chown -R ubuntu:ubuntu TARGET.stage
sudo -u ubuntu bash -c 'cd TARGET.stage/ecg-cloud && sha256sum --check --quiet MANIFEST.sha256'
test ! -e TARGET.stage/ecg-cloud/.venv
# Install into a new fixed path. The old interpreter and dependencies stay intact.
sudo -iu ubuntu python3 -m venv TARGET.venv
sudo -iu ubuntu TARGET.venv/bin/python -m pip install --disable-pip-version-check -r TARGET.stage/ecg-cloud/requirements.txt -r TARGET.stage/ecg-cloud/requirements-model.txt
sudo -iu ubuntu TARGET.venv/bin/python -c 'import fastapi,uvicorn,numpy,scipy'
ln -s TARGET.venv TARGET.stage/ecg-cloud/.venv
chown -h ubuntu:ubuntu TARGET.stage/ecg-cloud/.venv
tar --exclude=ecg-cloud/.venv --exclude='*/__pycache__' --exclude=ecg-cloud/data -czf TARGET.backup.tgz -C /home/ubuntu ecg-cloud
ecg_uid=$(id -u ubuntu)
ecg_systemctl() { sudo -u ubuntu env XDG_RUNTIME_DIR="/run/user/$ecg_uid" systemctl --user "$@"; }
ecg_systemctl is-active --quiet ecg-cloud.service
mkdir -p /home/ubuntu/.config/systemd/user/ecg-cloud.service.d
chown ubuntu:ubuntu /home/ubuntu/.config/systemd/user/ecg-cloud.service.d
ecg_dropin=/home/ubuntu/.config/systemd/user/ecg-cloud.service.d/model.conf
test ! -e "$ecg_dropin" && test ! -L "$ecg_dropin"
ecg_health() {
    python3 - "$1" <<'PY'
import json,sys,urllib.request
from pathlib import Path
try:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8766/health',timeout=2) as response:
        h=json.load(response)
    assert h['ok'] is True and h['service']=='ecg-course-demo' and h['deployment']=='cloud'
    identity={key:h.get(key) for key in ('model_status','model_name','model_version')}
    previous=Path('TARGET.previous-health.json')
    if sys.argv[1]=='save':
        assert h['model_status'] in ('not_configured','ready')
        with previous.open('x',encoding='utf-8') as out: json.dump(identity,out)
    elif sys.argv[1]=='previous':
        assert identity==json.loads(previous.read_text(encoding='utf-8'))
    else:
        assert h['model_status']=='ready' and h['model_name']=='CardioEdgeAI v1 INT8'
    print(json.dumps(identity))
except Exception:
    raise SystemExit(1)
PY
}
ecg_health save
ecg_rollback_armed=0
ecg_committed=0
ecg_finish() {
    ecg_code=$1
    trap - EXIT HUP INT TERM
    if test "$ecg_rollback_armed" -eq 1 && test "$ecg_committed" -eq 0; then
        set +e
        printf '\nMODEL_UPDATE_FAILED_ROLLING_BACK\n' >&2
        ecg_restore_ok=1
        ecg_systemctl stop ecg-cloud.service || ecg_restore_ok=0
        if test -e "$ecg_dropin" || test -L "$ecg_dropin"; then
            mv -T -- "$ecg_dropin" TARGET.disabled-model.conf || ecg_restore_ok=0
        fi
        # Directory existence handles a signal arriving immediately after mv.
        # No removal/extraction touches the independent ecg-cloud-data directory.
        if test -d TARGET.previous; then
            if test -e /home/ubuntu/ecg-cloud || test -L /home/ubuntu/ecg-cloud; then
                mv -T -- /home/ubuntu/ecg-cloud TARGET.failed || ecg_restore_ok=0
            fi
            if test ! -e /home/ubuntu/ecg-cloud && test ! -L /home/ubuntu/ecg-cloud; then
                mv -T -- TARGET.previous /home/ubuntu/ecg-cloud || ecg_restore_ok=0
            else
                ecg_restore_ok=0
            fi
        fi
        ecg_systemctl daemon-reload || ecg_restore_ok=0
        ecg_systemctl start ecg-cloud.service || ecg_restore_ok=0
        ecg_restored=0
        for ecg_attempt in $(seq 1 15); do
            if ecg_systemctl is-active --quiet ecg-cloud.service && ecg_health previous; then
                ecg_restored=1
                break
            fi
            sleep 1
        done
        if test "$ecg_restore_ok" -eq 1 && test "$ecg_restored" -eq 1; then
            printf '\nMODEL_UPDATE_FAILED_PREVIOUS_SERVICE_RESTORED\n' >&2
        else
            printf '\nMODEL_UPDATE_FAILED_MANUAL_RECOVERY_REQUIRED_FILES_PRESERVED\n' >&2
            sudo -u ubuntu env XDG_RUNTIME_DIR="/run/user/$ecg_uid" journalctl --user -u ecg-cloud.service -n 30 --no-pager >&2
        fi
        test "$ecg_code" -ne 0 || ecg_code=1
    fi
    exit "$ecg_code"
}
trap 'ecg_finish $?' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Arm BEFORE stop so every subsequent failure enters the same rollback path.
ecg_rollback_armed=1
ecg_systemctl stop ecg-cloud.service
mv -T -- /home/ubuntu/ecg-cloud TARGET.previous
mv -T -- TARGET.stage/ecg-cloud /home/ubuntu/ecg-cloud
printf '%s\n' '[Service]' 'Environment=ECG_MODEL=cardioedge' > "$ecg_dropin"
chown ubuntu:ubuntu "$ecg_dropin"
ecg_systemctl daemon-reload
ecg_systemctl start ecg-cloud.service
for ecg_attempt in $(seq 1 30); do
    if ecg_systemctl is-active --quiet ecg-cloud.service && ecg_health new; then
        ecg_systemctl show ecg-cloud.service -p MainPID -p MemoryCurrent
        ecg_committed=1
        printf '\nECG_MODEL_DEPLOYED\n'
        exit 0
    fi
    sleep 1
done
exit 1
'''.replace('TARGET', target).replace('DIGEST', digest).replace('PARTS', ' '.join(parts))
    if len(base64.b64encode(install.encode())) >= 65536:
        raise ValueError('Install command exceeds Tencent command size limit')
    (out/f'{len(parts)+1:02d}-install-model.sh').write_text(install, encoding='utf-8', newline='\n')
    metadata = {'archive_sha256':digest,'archive_bytes':len(raw),'part_count':len(parts),
                'remote_prefix':target,'contains_credentials':False,'contains_recordings':False}
    (out/'manifest.json').write_text(json.dumps(metadata,indent=2), encoding='utf-8')
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
