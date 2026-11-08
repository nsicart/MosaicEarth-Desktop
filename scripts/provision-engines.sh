#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker build --platform linux/amd64 -f engines/Dockerfile.nodeodx -t mosaicearth-desktop/nodeodx:0.1.0 .
docker build --platform linux/amd64 -f engines/Dockerfile.local -t mosaicearth-desktop/local-engine:0.1.0 .
./scripts/build-platform.sh
if [ "${ME_DESKTOP_CPU_ONLY:-0}" != 1 ]; then
    acceleration=$(python3 -c 'import sys; sys.path.insert(0,"src"); from mosaicearth_gpu import detect; print(detect(["docker"], "resources")["mode"])')
    if [ "$acceleration" = cuda ]; then
        ./scripts/provision-cuda.sh
    fi
fi
