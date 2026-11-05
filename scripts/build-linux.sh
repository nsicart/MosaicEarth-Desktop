#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
test -f resources/cesium/Cesium.js || ./scripts/fetch-cesium.sh
source_rev=$(git rev-parse HEAD)
test -z "$(git status --porcelain --untracked-files=normal)"
export SOURCE_DATE_EPOCH=$(git show -s --format=%ct "$source_rev")
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
p="$tmp/package"
mkdir -p "$p/DEBIAN" "$p/usr/bin" "$p/usr/share/applications" "$p/usr/share/doc/mosaicearth-desktop" \
    "$p/opt/mosaicearth-desktop/src" "$p/opt/mosaicearth-desktop/resources"
cat > "$p/DEBIAN/control" <<'EOF'
Package: mosaicearth-desktop
Version: 0.1.1
Architecture: amd64
Maintainer: MosaicEarth
Section: science
Priority: optional
Depends: python3 (>= 3.10)
Recommends: docker.io | docker-ce-cli
Description: MosaicEarth local geospatial processing workspace
 Local projects, Cesium viewer and isolated engines for photogrammetry and Gaussian splatting.
EOF
install -m 755 packaging/mosaicearth-desktop "$p/usr/bin/"
install -m 644 packaging/mosaicearth-desktop.desktop "$p/usr/share/applications/"
cp src/*.py "$p/opt/mosaicearth-desktop/src/"
cp -R resources/. "$p/opt/mosaicearth-desktop/resources/"
cp LICENSE "$p/usr/share/doc/mosaicearth-desktop/copyright"
cp README.md THIRD_PARTY_LICENSES.md NOTICE "$p/usr/share/doc/mosaicearth-desktop/"
printf '%s\n' "$source_rev" > "$p/usr/share/doc/mosaicearth-desktop/SOURCE_REVISION"
find "$p" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
artifact="installers/mosaicearth-desktop_0.1.1_amd64.deb"
dpkg-deb --root-owner-group --build "$p" "$artifact"
(cd installers && sha256sum "$(basename "$artifact")" > SHA256SUMS)
