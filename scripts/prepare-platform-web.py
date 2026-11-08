"""Fetch the exact shared web interface; verify every payload before packaging.

Runtime never fetches executable UI code. Public maps and cloud sync use their
normal online data endpoints; UI, fonts, icons and Cesium are bundled offline.
"""
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'build/platform-web'


def fetch(item):
    target = TARGET / item['path']
    if not target.resolve().is_relative_to(TARGET.resolve()):
        raise ValueError('Invalid static payload path')
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == item['sha256']:
        return
    with urllib.request.urlopen(item['url'], timeout=60) as response:
        data = response.read(item['bytes'] + 1)
    if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError('Static payload changed: ' + item['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


if __name__ == '__main__':
    manifest = json.loads((ROOT / 'platform/web-manifest.json').read_text())
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, manifest['files']))
    if not (ROOT / 'resources/cesium/Cesium.js').is_file():
        subprocess.run([str(ROOT / 'scripts/fetch-cesium.sh')], check=True)
    shutil.copytree(ROOT / 'resources/cesium', TARGET / 'vendor/cesium', dirs_exist_ok=True)
    (TARGET / 'vendor/raleway').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'resources/raleway.css', TARGET / 'vendor/raleway/fonts.css')
    print('Verified', len(manifest['files']), 'shared interface assets and pinned Cesium 1.144.0.')
