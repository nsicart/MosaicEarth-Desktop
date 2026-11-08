"""Fetch the exact shared web interface; verify every payload before packaging.

Runtime never fetches executable UI code. Public maps and cloud sync use their
normal online data endpoints; UI, fonts, icons and Cesium are bundled offline.
"""
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'build/platform-web'


def verified(item):
    target = TARGET / item['path']
    if not target.resolve().is_relative_to(TARGET.resolve()):
        raise ValueError('Invalid static payload path')
    return target.is_file() and target.stat().st_size == item['bytes'] and hashlib.sha256(target.read_bytes()).hexdigest() == item['sha256']


def fetch_archive(manifest):
    archive = manifest['archive']
    request = urllib.request.Request(archive['url'], headers={
        'User-Agent': 'MosaicEarth-Desktop/0.3.0 (source-build)',
    })
    # Immutable public release payload: builds must not depend on the live site's
    # changing JS/CSS. Only explicitly declared regular files are unpacked.
    with tempfile.TemporaryFile() as payload:
        sha = hashlib.sha256()
        count = 0
        with urllib.request.urlopen(request, timeout=90) as response:
            while block := response.read(min(1024 * 1024, archive['bytes'] + 1 - count)):
                count += len(block)
                if count > archive['bytes']:
                    raise ValueError('Static archive exceeds its declared size')
                sha.update(block)
                payload.write(block)
        if count != archive['bytes'] or sha.hexdigest() != archive['sha256']:
            raise ValueError('Static archive checksum mismatch; retry the source build')
        payload.seek(0)
        entries = {item['path']: item for item in manifest['files']}
        seen = set()
        with tarfile.open(fileobj=payload, mode='r:gz') as tar:
            for member in tar:
                item = entries.get(member.name)
                if item is None or member.name in seen or not member.isfile() or member.size != item['bytes']:
                    raise ValueError('Unexpected static archive entry: ' + member.name)
                verified(item)  # Validate the destination before writing.
                with tar.extractfile(member) as source:
                    data = source.read(item['bytes'] + 1)
                if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise ValueError('Static payload changed: ' + item['path'])
                target = TARGET / item['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                seen.add(member.name)
        if seen != set(entries):
            raise ValueError('Static archive is incomplete')


if __name__ == '__main__':
    manifest = json.loads((ROOT / 'platform/web-manifest.json').read_text())
    if not all(verified(item) for item in manifest['files']):
        fetch_archive(manifest)
    if not (ROOT / 'resources/cesium/Cesium.js').is_file():
        subprocess.run([str(ROOT / 'scripts/fetch-cesium.sh')], check=True)
    shutil.copytree(ROOT / 'resources/cesium', TARGET / 'vendor/cesium', dirs_exist_ok=True)
    (TARGET / 'vendor/raleway').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'resources/raleway.css', TARGET / 'vendor/raleway/fonts.css')
    print('Verified', len(manifest['files']), 'shared interface assets and pinned Cesium 1.144.0.')
