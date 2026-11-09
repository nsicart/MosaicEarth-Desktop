#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
architectures=${ME_DESKTOP_CUDA_ARCHITECTURES:-60;61;70;75;80;86;89}
[[ "$architectures" =~ ^[0-9]+(\;[0-9]+)*$ ]]
ref=e33f9dde8ef9c9d59852ffe4d12e0adddcf1fc1b
libtorch_sha=f29757bdae8b26583431d5811e627b1b993e97d00d8fe66c51fb9d192490087b
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT
git -C "$src" init -q
git -C "$src" fetch --depth 1 https://github.com/WebODM/OpenSplat.git "$ref"
git -C "$src" checkout --detach -q FETCH_HEAD
test "$(git -C "$src" rev-parse HEAD)" = "$ref"
jobs=${ME_OPENSPLAT_BUILD_JOBS:-2}
[[ "$jobs" =~ ^[1-9][0-9]*$ ]]
sed -i -e "s/^[[:space:]]*ninja[[:space:]]*$/    ninja -j $jobs/" "$src/Dockerfile"
sed -i -e 's/wget --no-check-certificate/wget --https-only/' \
  -e "/^[[:space:]]*unzip -q libtorch.zip/i\    echo '$libtorch_sha  libtorch.zip' | sha256sum -c - \&\& \\\\" \
  "$src/Dockerfile"
grep -Fq "ninja -j $jobs" "$src/Dockerfile"
grep -Fq "echo '$libtorch_sha  libtorch.zip' | sha256sum -c -" "$src/Dockerfile"
docker build --label "org.opencontainers.image.revision=$ref" \
  --build-arg UBUNTU_VERSION=22.04 --build-arg CUDA_VERSION=11.8.0 \
  --build-arg TORCH_VERSION=2.3.1 --build-arg CMAKE_BUILD_TYPE=Release \
  --build-arg "CMAKE_CUDA_ARCHITECTURES=${ME_CUDA_ARCHITECTURES:-75;80;86;89}" \
  -t "mosaicearth-desktop/cuda-build:1.2.2" "$src"
docker build --platform linux/amd64 -f engines/Dockerfile.cuda-runtime \
  --build-arg CUDA_TOOLCHAIN=mosaicearth-desktop/cuda-build:1.2.2 \
  --build-arg "CUDA_ARCHITECTURES=$architectures" \
  -t mosaicearth-desktop/local-engine:0.4.0-cuda .
docker build --platform linux/amd64 -f engines/Dockerfile.nodeodx \
  --build-arg NODEODX_BASE=webodm/nodeodx@sha256:77e913d50929885678b59cf7c0d82aa04289887a025d0516737e5c0f701bedf3 \
  -t mosaicearth-desktop/nodeodx:0.4.0-cuda .
python3 scripts/prepare-platform-web.py
docker build --platform linux/amd64 -f engines/Dockerfile.platform \
  --build-arg LOCAL_ENGINE=mosaicearth-desktop/local-engine:0.4.0-cuda \
  -t mosaicearth-desktop/platform:0.4.3-cuda .
