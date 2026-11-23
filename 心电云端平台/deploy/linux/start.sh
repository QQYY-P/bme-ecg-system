#!/usr/bin/env bash
# A foreground process; systemd owns backgrounding and restart behavior.
set -euo pipefail
umask 077

ecg_app_dir="${HOME}/ecg-cloud"
ecg_data_dir="${HOME}/ecg-cloud-data"
if [[ ! -x "${ecg_app_dir}/.venv/bin/python" ]]; then
    printf '%s\n' 'Run: bash ~/ecg-cloud/deploy/linux/install.sh' >&2
    exit 1
fi
if [[ -L "${ecg_data_dir}" ]]; then
    printf '%s\n' 'Refusing a symbolic link as the data directory.' >&2
    exit 1
fi
mkdir -p -- "${ecg_data_dir}"
chmod 700 -- "${ecg_data_dir}"
cd -- "${ecg_app_dir}"
export ECG_DEVICE_ID=pi2-demo
export ECG_DATA_DIR="${ecg_data_dir}"
export ECG_DEPLOYMENT=cloud
# The service is reachable only through the server's loopback/SSH tunnel.
exec "${ecg_app_dir}/.venv/bin/python" "${ecg_app_dir}/run_server.py" \
    --host 127.0.0.1 --port 8766 --data-dir "${ecg_data_dir}"
