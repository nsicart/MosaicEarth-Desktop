#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL https://registry.npmjs.org/cesium/-/cesium-1.144.0.tgz -o "$tmp/cesium.tgz"
expected=g1TX13KvTUhX9zjlJNn3De5BwuHyzuQzp1+1ITpGJJmIORCGnxVKAiu9/OkQByTWNYKgNtoEuPLrnWFhl0ao5g==
actual=$(openssl dgst -sha512 -binary "$tmp/cesium.tgz" | openssl base64 -A)
test "$expected" = "$actual"
tar -xzf "$tmp/cesium.tgz" -C "$tmp" package/Build/Cesium package/LICENSE.md package/ThirdParty.json
mkdir -p resources/cesium
cp -R "$tmp/package/Build/Cesium/." resources/cesium/
cp "$tmp/package/LICENSE.md" "$tmp/package/ThirdParty.json" resources/cesium/
