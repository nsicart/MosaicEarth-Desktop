#!/usr/bin/env python3
"""Build all seven installable open-source connectors from one versioned synchronization client."""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / 'installers'
out.mkdir(exist_ok=True)
sums = []


def write_deterministic_zip(staging: Path, archive: Path, timestamp: tuple[int, int, int, int, int, int]):
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target_zip:
        for file in sorted(staging.rglob('*')):
            if file.is_file():
                rel = file.relative_to(staging).as_posix()
                info = zipfile.ZipInfo(rel, timestamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                mode = 0o755 if file.suffix == '.sh' else 0o644
                info.external_attr = mode << 16
                target_zip.writestr(info, file.read_bytes())


# 1. Existing 0.2.2 GIS & ODM plugins (deterministic byte-identical build)
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
                    'version': '0.2.2', 'author': 'MosaicEarth', 'email': 'support@mosaicearth.app',
                    'repository': 'https://github.com/nsicart/MosaicEarth-Desktop',
                    'homepage': 'https://app.mosaicearth.app/en/Desktop', 'experimental': False, 'deprecated': False,
                    ('webodmMinVersion' if target == 'webodm' else 'webuiMinVersion'): ('2.9.4' if target == 'webodm' else '3.2.5')}
        (package / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    archive = out / f'MosaicEarth-{target}-0.2.2.zip'
    write_deterministic_zip(staging, archive, (2026, 11, 7, 0, 0, 0))
    sums.append(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name)

# 2. 3D, Photogrammetry & Lidar Connectors 0.3.0 (Blender, Metashape, CloudCompare, RealityCapture)
NEW_TARGETS = (
    ('blender', 'connectors/blender', 'mosaicearth_blender', True),
    ('metashape', 'connectors/metashape', 'mosaicearth_metashape', False),
    ('cloudcompare', 'connectors/cloudcompare', 'mosaicearth_cloudcompare', False),
    ('realitycapture', 'connectors/realitycapture', 'mosaicearth_realitycapture', False),
)

for target, src_rel, pkg_module, wrap_single_folder in NEW_TARGETS:
    staging = root / 'build' / ('connector-' + target)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / src_rel, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    mod_dir = staging / pkg_module
    shutil.copyfile(root / 'src/mosaicearth_sync.py', mod_dir / 'mosaicearth_sync.py')
    shutil.copyfile(root / 'LICENSE', (mod_dir if wrap_single_folder else staging) / 'LICENSE')
    archive = out / f'MosaicEarth-{target}-0.3.0.zip'
    write_deterministic_zip(staging, archive, (2026, 11, 12, 0, 0, 0))
    sums.append(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name)

# 3. Wave 2 (0.4.0) — MosaicEarth Bridge for iOS (Apple Shortcut + SwiftUI + ShareExtension)
import subprocess
import sys

ios_src = root / 'connectors' / 'ios-bridge'
if (ios_src / 'scripts' / 'generate_apple_shortcut.py').is_file():
    subprocess.run([sys.executable, str(ios_src / 'scripts' / 'generate_apple_shortcut.py')], check=True)
    ios_staging = root / 'build' / 'connector-ios-bridge'
    if ios_staging.exists():
        shutil.rmtree(ios_staging)
    pkg_dir = ios_staging / 'mosaicearth_ios_bridge'
    shutil.copytree(ios_src, pkg_dir, ignore=shutil.ignore_patterns('__pycache__', '.build', '*.xcodeproj'))
    shutil.copyfile(root / 'LICENSE', pkg_dir / 'LICENSE')
    ios_archive = out / 'MosaicEarth-ios-bridge-0.4.0.zip'
    write_deterministic_zip(ios_staging, ios_archive, (2026, 11, 12, 12, 0, 0))
    sums.append(hashlib.sha256(ios_archive.read_bytes()).hexdigest() + '  ' + ios_archive.name)

# 4. Wave 2 (0.4.0) — DJI Terra, PIX4D, ArcGIS Pro (.pyt Toolbox) & Jawset Postshot / LichtFeld 3DGS
WAVE2_TARGETS = (
    ('djiterra', 'connectors/djiterra', 'mosaicearth_djiterra'),
    ('pix4d', 'connectors/pix4d', 'mosaicearth_pix4d'),
    ('arcgispro', 'connectors/arcgispro', 'mosaicearth_arcgispro'),
    ('postshot', 'connectors/postshot', 'mosaicearth_postshot'),
)

for target, src_rel, pkg_module in WAVE2_TARGETS:
    staging = root / 'build' / ('connector-' + target)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / src_rel, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    mod_dir = staging / pkg_module
    shutil.copyfile(root / 'src/mosaicearth_sync.py', mod_dir / 'mosaicearth_sync.py')
    shutil.copyfile(root / 'LICENSE', staging / 'LICENSE')
    archive = out / f'MosaicEarth-{target}-0.4.0.zip'
    write_deterministic_zip(staging, archive, (2026, 11, 12, 18, 0, 0))
    sums.append(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name)

(out / 'SHA256SUMS.connectors').write_text('\n'.join(sums) + '\n')
print('Built all 12 open-source connector ZIPs (WebODM, WebUI, QGIS, Blender, Metashape, CloudCompare, RealityCapture, iOS Bridge, DJI Terra, PIX4D, ArcGIS Pro, Postshot/LichtFeld)')
