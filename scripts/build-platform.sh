#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 scripts/prepare-platform-web.py
docker image inspect mosaicearth-desktop/local-engine:0.1.0 >/dev/null
# Community builds use the runtime built from this repository. Official
# installers separately verify all distributed image IDs in engine-payload.json.
docker build -f engines/Dockerfile.platform -t mosaicearth-desktop/platform:0.4.5 .
