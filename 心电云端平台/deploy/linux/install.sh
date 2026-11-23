#!/usr/bin/env bash
# Ubuntu 24.04, ordinary SSH login user; no sudo or global Python changes.
set -euo pipefail
umask 077

ecg_app_dir="${HOME}/ecg-cloud"
ecg_data_dir="${HOME}/ecg-cloud-data"
ecg_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
if [[ "${EUID}" -eq 0 ]]; then
    printf '%s\n' 'Use an ordinary SSH login user, not root.' >&2
    exit 1
fi
if [[ "${ecg_source_dir}" != "${ecg_app_dir}" ]]; then
    printf 'Extract the package into %s before installing.\n' "${ecg_app_dir}" >&2
    exit 1
fi
if [[ -L "${ecg_data_dir}" || -L "${ecg_app_dir}/.venv" ]]; then
    printf '%s\n' 'Refusing symbolic links for the venv or data directory.' >&2
    exit 1
fi
cd -- "${ecg_app_dir}"
if [[ ! -f MANIFEST.sha256 ]]; then
    printf '%s\n' 'Missing package manifest; use the prepared deployment archive.' >&2
    exit 1
fi
sha256sum --check --quiet MANIFEST.sha256
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer is required"'
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv || {
        printf '%s\n' 'Install python3-venv with the Ubuntu package manager, then retry.' >&2
        exit 1
    }
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -c 'import fastapi, uvicorn, sqlite3; print("Python dependencies ready; no model dependencies.")'
mkdir -p -- "${ecg_data_dir}"
chmod 700 -- "${ecg_app_dir}" "${ecg_data_dir}"

ecg_unit_dir="${HOME}/.config/systemd/user"
ecg_unit_file="${ecg_unit_dir}/ecg-cloud.service"
mkdir -p -- "${ecg_unit_dir}"
if [[ -e "${ecg_unit_file}" ]] && ! cmp -s deploy/linux/ecg-cloud.service "${ecg_unit_file}"; then
    printf 'A different unit already exists at %s; it was not overwritten.\n' "${ecg_unit_file}" >&2
    exit 1
fi
install -m 600 deploy/linux/ecg-cloud.service "${ecg_unit_file}"
if systemctl --user daemon-reload; then
    printf '%s\n' 'Installed. Start: systemctl --user enable --now ecg-cloud.service'
else
    printf '%s\n' 'No user service manager in this session. Foreground fallback:'
    printf '%s\n' 'bash ~/ecg-cloud/deploy/linux/start.sh'
fi
printf '%s\n' 'Data: ~/ecg-cloud-data; service: 127.0.0.1:8766; models remain absent.'
