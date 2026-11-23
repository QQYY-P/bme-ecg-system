"""Build a credential-free Tencent console command from the reviewed source bundle."""
import base64
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
archive=ROOT/'deploy/dist/ecg-cloud-linux.zip'
raw=archive.read_bytes()
encoded=base64.encodebytes(raw).decode('ascii')
digest=hashlib.sha256(raw).hexdigest()
script='''#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" = 0
getent passwd ubuntu >/dev/null
test -d /home/ubuntu
test ! -e /home/ubuntu/ecg-cloud
test ! -e /home/ubuntu/ecg-cloud-package-20261029.zip
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv unzip
umask 077
base64 --decode > /home/ubuntu/ecg-cloud-package-20261029.zip <<'ECG_SOURCE_ARCHIVE'
'''+encoded+'''ECG_SOURCE_ARCHIVE
printf '%s\\n' 'DIGEST  /home/ubuntu/ecg-cloud-package-20261029.zip' | sha256sum --check
chown ubuntu:ubuntu /home/ubuntu/ecg-cloud-package-20261029.zip
sudo -iu ubuntu unzip -n /home/ubuntu/ecg-cloud-package-20261029.zip -d /home/ubuntu
sudo -iu ubuntu bash /home/ubuntu/ecg-cloud/deploy/linux/install.sh
loginctl enable-linger ubuntu
ecg_uid=$(id -u ubuntu)
systemctl start "user@${ecg_uid}.service"
sudo -u ubuntu env XDG_RUNTIME_DIR="/run/user/${ecg_uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${ecg_uid}/bus" systemctl --user daemon-reload
sudo -u ubuntu env XDG_RUNTIME_DIR="/run/user/${ecg_uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${ecg_uid}/bus" systemctl --user enable --now ecg-cloud.service
for ecg_attempt in 1 2 3 4 5; do
    if curl --fail --silent http://127.0.0.1:8766/health; then
        printf '\\nECG_CLOUD_DEPLOYED\\n'
        exit 0
    fi
    sleep 1
done
sudo -u ubuntu env XDG_RUNTIME_DIR="/run/user/${ecg_uid}" journalctl --user -u ecg-cloud.service -n 30 --no-pager
exit 1
'''
script=script.replace('DIGEST',digest)
target=ROOT/'deploy/dist/install-cloud-via-console.sh'
target.write_bytes(script.encode('utf-8'))
print(f'Console script: {len(script.encode())} bytes; archive SHA256: {digest}')
