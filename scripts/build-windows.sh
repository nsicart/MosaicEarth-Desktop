#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
test -f resources/cesium/Cesium.js || ./scripts/fetch-cesium.sh
source_rev=$(git rev-parse HEAD)
test -z "$(git status --porcelain --untracked-files=normal)"
export SOURCE_DATE_EPOCH=$(git show -s --format=%ct "$source_rev")
version=3.12.10
mkdir -p build/windows-python installers
cp /usr/share/doc/nsis/copyright build/NSIS-COPYRIGHT
curl -fsSL "https://www.python.org/ftp/python/$version/python-$version-embed-amd64.zip" -o build/python.zip
echo "$(cat packaging/python-windows.sha256)  build/python.zip" | sha256sum -c -
unzip -oq build/python.zip -d build/windows-python
printf 'python312.zip\r\n.\r\n..\\src\r\n' > build/windows-python/python312._pth
printf '%s\n' "$source_rev" > build/SOURCE_REVISION
python3 -m py_compile src/*.py
if [ -n "${ME_SIGNING_COMMAND:-}" ]; then
    test -x "$ME_SIGNING_COMMAND"
    makensis -V3 "-DME_SIGNING_COMMAND=$ME_SIGNING_COMMAND" packaging/windows.nsi
else
    makensis -V3 packaging/windows.nsi
fi
(cd installers && sha256sum MosaicEarth-Desktop-0.4.1-x64-Setup.exe > SHA256SUMS.windows)
