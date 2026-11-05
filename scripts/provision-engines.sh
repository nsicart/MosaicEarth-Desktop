#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker build --platform linux/amd64 -f engines/Dockerfile.nodeodx -t mosaicearth-desktop/nodeodx:0.1.0 .
docker build --platform linux/amd64 -f engines/Dockerfile.local -t mosaicearth-desktop/local-engine:0.1.0 .
