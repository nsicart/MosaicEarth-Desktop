#!/usr/bin/env python3
"""Build three installable plugins from one versioned synchronization client."""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / 'installers'
out.mkdir(exist_ok=True)
sums = []
for target in ('webodm', 'webui', 'qgis'):
    staging = root / 'build' / ('connector-' + target)
    if staging.exists():
        shutil.rmtree(staging)
    name = 'mosaicearth' if target == 'qgis' else 'mosaicearth-' + target
    package = staging / name
    shutil.copytree(root / ('connectors/qgis/mosaicearth' if target == 'qgis' else 'connectors/odm'),
                    package, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(root / 'src/mosaicearth_sync.py', package / 'mosaicearth_sync.py')
    shutil.copyfile(root / 'LICENSE', package / 'LICENSE')
    if target != 'qgis':
        manifest = {'name': 'MosaicEarth ' + ('WebODM' if target == 'webodm' else 'WebUI'),
                    'description': 'Full private project synchronization with MosaicEarth Cloud',
                    'version': '0.2.0', 'author': 'MosaicEarth', 'email': 'support@mosaicearth.app',
                    'repository': 'https://github.com/nsicart/MosaicEarth-Desktop',
                    'homepage': 'https://app.mosaicearth.app/en/Desktop', 'experimental': False, 'deprecated': False,
                    ('webodmMinVersion' if target == 'webodm' else 'webuiMinVersion'): ('2.9.4' if target == 'webodm' else '0.7.1')}
        (package / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    archive = out / f'MosaicEarth-{target}-0.2.0.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target_zip:
        for file in sorted(package.rglob('*')):
            if file.is_file():
                info = zipfile.ZipInfo(file.relative_to(staging).as_posix(), (2026, 11, 7, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                target_zip.writestr(info, file.read_bytes())
    sums.append(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name)
(out / 'SHA256SUMS.connectors').write_text('\n'.join(sums) + '\n')
print('Built WebODM, WebUI and QGIS connector ZIPs')
