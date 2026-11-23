#!/bin/sh
# Synthetic display demonstration; never presented as human ECG acquisition.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /bin/sh "$script_dir/start-screen.sh" --demo "$@"
