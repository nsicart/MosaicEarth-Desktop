#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"
if [[ $# -eq 0 ]]; then
    exec python3 -m mosaicearth_djiterra.connector --gui
else
    exec python3 -m mosaicearth_djiterra.connector "$@"
fi
