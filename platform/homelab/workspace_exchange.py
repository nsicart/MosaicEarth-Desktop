"""Portable MyEarth/MyMosaics snapshots, independent of accounts and engines.

Only the allowlisted document graph and its files travel. Sessions, billing,
service credentials and executable queue jobs never belong to a workspace copy.
Imported revisions are private copies and never overwrite an existing revision.
"""
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

PROJECTS = 'dbo.WEB_PROJECTS'
ASSETS = 'dbo.MOSAICEARTH_EXTERNAL_ASSETS'
MAPS = 'dbo.WEB_MYMAPS'
FIELDS = {
    PROJECTS: 'ID NAME DESCRIPTION TASK_DESCRIPTION TASK_CREATION_DATE CAPTURE_METHOD CATEGORY_ID TAGS FOLDER PARENT_PROJECT_ID SURVEY_ID IS_PROJECT_CONTAINER IS_SURVEY_CONTAINER STATE_ID ZIP_UPLOADED LATITUD LONGITUD ASSET_METADATA ASSET_TITLES CANONICAL_RESULT_CONTRACT PROCESSING_PRESET ENABLE_DSM ENABLE_DTM CREATE_DATE',
    'dbo.WEB_PROJECTS_FILES': 'PROJECT_FILE_ID PROJECT_ID FILENAME SIZE CONTENT_TYPE RESOLUTION LATITUD LONGITUD ALTITUD ALTURA CAPTURE_DATE PROCESSED CREATE_DATE',
    'dbo.WEB_PROJECTS_LOCATION': 'ID PROJECT_ID LATITUD LONGITUD ALTITUD MAX_LATITUD MAX_LONGITUD MAX_ALTITUD MIN_LATITUD MIN_LONGITUD MIN_ALTITUD',
    'dbo.WEB_PROJECTS_VALUES': 'ID PROJECT_ID AREA CAPTURE_DATE GSD GSD_AVERAGE GPS_AVERAGE AVERAGE_GPS',
    'dbo.WEB_CESIUM_MODELS': 'Id Project_id ModelType Height GeodesicOffset VerticalStatus',
    ASSETS: 'ID PROJECT_ID TITLE DESCRIPTION DISPLAY_NAME SOURCE_FILE_NAME SOURCE_FORMAT SOURCE_ORIGIN SOURCE_SHA256 STORAGE_RELATIVE_PATH SOURCE_BLOB_NAME STORED_FILE_NAME FILE_SIZE_BYTES PREVIEW_RELATIVE_PATH DISPLAY_BLOB_PREFIX DISPLAY_ENTRY_PATH DISPLAY_KIND OUTPUT_FORMAT RESULT_KIND ASSET_CLASS ASSET_SUBTYPE ASSET_TYPE USER_CLASS_OVERRIDE VIEWER_KIND STATUS STATUS_DETAIL CANONICAL_STATUS LATITUDE LONGITUDE LONGITUD ALTITUDE HEADING PITCH ROLL SCALE CRS EPSG GEOREFERENCE_MODE HAS_EMBEDDED_GEOREF LOCATION_NAME BOUNDS_EAST BOUNDS_NORTH BOUNDS_SOUTH BOUNDS_WEST ELEVATION_META INSPECTION_META DERIVATIVES_MANIFEST DERIVATIVE_KIND CONTOUR_INTERVAL_M SOURCE_ASSET_ID VECTOR_LAYERS CAPTURE_DATE CAPTURE_METHOD CREATED_AT',
    'dbo.MOSAICEARTH_EXTERNAL_PROJECTS': 'PROJECT_ID PROJECT_KIND CREATED_AT',
    'dbo.MOSAICEARTH_GAUSSIAN_PROJECTS': 'PROJECT_ID MODE REVISION SOURCE_MANIFEST STATUS STATUS_DETAIL ASSET_ID CREATED_AT',
    MAPS: 'ID NAME VISIBILITY CREATE_DATE',
    'dbo.WEB_MYMAPS_ITEMS': 'MYMAP_ID PUBLICATION_ID PUBLISH_ID',
    'dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS': 'MYMAP_ID ASSET_ID',
    'dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS': 'MYMAP_ID PROJECT_ID FILE_TYPE_ID',
    'dbo.MOSAICEARTH_ASSET_ANNOTATIONS': 'ID ASSET_KEY PROJECT_ID TITLE BODY LINK VISIBILITY ANCHOR_KIND X Y Z REVISION CREATED_AT',
    'dbo.MOSAICEARTH_ASSET_TEMPORAL': 'ASSET_KEY SOURCE_KIND PROJECT_ID FILE_TYPE_ID EXTERNAL_ASSET_ID CAPTURE_START_UTC CAPTURE_END_UTC CAPTURE_PRECISION CAPTURE_SOURCE IS_MANUAL CREATED_DATE_UTC UPDATED_AT_UTC EFFECTIVE_START_UTC EFFECTIVE_END_UTC',
}
FIELDS = {name: set(fields.split()) for name, fields in FIELDS.items()}
PRIMARY = {name: 'ID' for name in FIELDS}
PRIMARY.update({'dbo.WEB_PROJECTS_FILES': 'PROJECT_FILE_ID', 'dbo.WEB_CESIUM_MODELS': 'Id'})
NO_PRIMARY = {'dbo.MOSAICEARTH_EXTERNAL_PROJECTS', 'dbo.MOSAICEARTH_GAUSSIAN_PROJECTS',
              'dbo.WEB_MYMAPS_ITEMS', 'dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS', 'dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS',
              'dbo.MOSAICEARTH_ASSET_TEMPORAL'}
CONTAINERS = {'imagenes', 'assets', 'results', 'tasks'}


def safe_relative(value):
    if not isinstance(value, str) or not value or len(value) > 600:
        raise ValueError('Invalid workspace file path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')) or any(c in value for c in '\\:\x00'):
        raise ValueError('Unsafe workspace file path')
    return value


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def export_workspace(store, blobs, uid, kind, entity_id, directory):
    """Prepare a complete owner-scoped graph and references to immutable files."""
    uid, entity_id = int(uid), int(entity_id)
    if kind not in {'project', 'mymosaic'}:
        raise ValueError('Choose a project or MyMosaic to synchronize')
    with store.lock:
        tables = copy.deepcopy(store.tables)
    owned = {int(p['ID']): p for p in tables.get(PROJECTS, [])
             if int(p.get('CREATE_ID') or 0) == uid and p.get('ACTIVO', True) and not p.get('DESKTOP_PUBLIC_CATALOG')}
    maps = {int(m['ID']): m for m in tables.get(MAPS, [])
            if int(m.get('CREATE_ID') or 0) == uid and m.get('ACTIVO', True) and not m.get('IS_CONTAINER_PREVIEW')}
    ids, mids = set(), set()
    if kind == 'project':
        if entity_id not in owned:
            raise ValueError('This project does not belong to the local workspace')
        ids.add(entity_id)
        name = owned[entity_id]['NAME']
    else:
        if entity_id not in maps:
            raise ValueError('This MyMosaic does not belong to the local workspace')
        mids.add(entity_id)
        name = maps[entity_id]['NAME']
        aids = {int(r['ASSET_ID']) for r in tables.get('dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS', []) if int(r['MYMAP_ID']) in mids}
        ids.update(int(a['PROJECT_ID']) for a in tables.get(ASSETS, []) if int(a['ID']) in aids and int(a.get('USER_ID') or 0) == uid)
        ids.update(int(r['PROJECT_ID']) for r in tables.get('dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS', []) if int(r['MYMAP_ID']) in mids)
        publication_ids = {int(r.get('PUBLICATION_ID') or r.get('PUBLISH_ID') or 0) for r in tables.get('dbo.WEB_MYMAPS_ITEMS', []) if int(r['MYMAP_ID']) in mids}
        ids.update(int(p['PROJECT_ID']) for p in tables.get('dbo.WEB_PUBLICATIONS', [])
                   if int(p.get('ID') or 0) in publication_ids and int(p.get('PROJECT_ID') or 0) in owned)
        if not ids <= owned.keys():
            raise ValueError('A private MyMosaic dependency is not owned by this account')
    # Include the project/survey context for every selected task or private layer.
    for _ in range(3):
        ids.update(int(owned[p].get(key) or 0) for p in list(ids) for key in ('PARENT_PROJECT_ID', 'SURVEY_ID')
                   if int(owned[p].get(key) or 0) in owned)
    for _ in range(3):
        ids.update(pid for pid, p in owned.items() if int(p.get('PARENT_PROJECT_ID') or 0) in ids or int(p.get('SURVEY_ID') or 0) in ids)
    if any(int(owned[pid].get('STATE_ID') or 0) in (1, 2) for pid in ids):
        raise ValueError('Wait for processing to finish before synchronizing this workspace')
    selected = {}
    for table, fields in FIELDS.items():
        rows = []
        for row in tables.get(table, []):
            keep = int(row.get('ID') or 0) in ids if table == PROJECTS else (
                int(row.get('ID') or 0) in mids if table == MAPS else (
                    int(row.get('MYMAP_ID') or 0) in mids if 'MYMAP' in table else
                    int(row.get('PROJECT_ID') or row.get('Project_id') or 0) in ids))
            if keep and row.get('ACTIVO', True) and not row.get('DELETED'):
                if table == ASSETS and (int(row.get('USER_ID') or 0) != uid or row.get('STATUS') in {'deleted', 'discarded'}):
                    continue
                rows.append({k: copy.deepcopy(v) for k, v in row.items() if k in fields})
        if rows:
            selected[table] = rows
    for item in list(selected.get('dbo.WEB_MYMAPS_ITEMS', [])):
        pub = next((p for p in tables.get('dbo.WEB_PUBLICATIONS', [])
                    if int(p.get('ID') or 0) == int(item.get('PUBLICATION_ID') or item.get('PUBLISH_ID') or 0)), None)
        if pub and int(pub.get('PROJECT_ID') or 0) in ids:
            selected['dbo.WEB_MYMAPS_ITEMS'].remove(item)
            selected.setdefault('dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS', []).append({
                'MYMAP_ID': item['MYMAP_ID'], 'PROJECT_ID': pub['PROJECT_ID'], 'FILE_TYPE_ID': pub['FILE_TYPE_ID']})
    files = {}

    def add(container, relative):
        relative = safe_relative(relative)
        key = container + '/' + relative
        if key in files:
            return
        path = blobs.ensure_local(container, relative, force_download=True)
        if not path or not path.is_file() or path.is_symlink():
            raise ValueError('A workspace file is unavailable: ' + relative)
        files[key] = path

    for pid in ids:
        folder = safe_relative(owned[pid]['FOLDER'])
        for relative in blobs.list_stored_files('imagenes', folder):
            add('imagenes', relative)
        for photo in selected.get('dbo.WEB_PROJECTS_FILES', []):
            if int(photo['PROJECT_ID']) == pid:
                filename = safe_relative(photo['FILENAME'])
                if 'imagenes/' + folder + '/' + filename not in files:
                    add('imagenes', folder + '/orig/' + filename)
    for asset in selected.get(ASSETS, []):
        if str(asset.get('STATUS') or '').lower() in {'queued', 'processing', 'converting', 'uploading', 'pending'}:
            raise ValueError('Wait for asset processing to finish before synchronizing this workspace')
        if asset.get('STORAGE_RELATIVE_PATH') or asset.get('SOURCE_BLOB_NAME'):
            add('assets', asset.get('STORAGE_RELATIVE_PATH') or asset['SOURCE_BLOB_NAME'])
        for container in ('assets', 'results'):
            for relative in blobs.list_stored_files(container, f"external/{asset['PROJECT_ID']}/{asset['ID']}"):
                add(container, relative)
    package = {'schema': 1, 'kind': kind, 'entity_id': entity_id, 'name': name,
               'tables': selected, 'files': sorted(files)}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    document = directory / 'workspace.json'
    document.write_text(json.dumps(package, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    if document.stat().st_size > 16 * 1024**2:
        raise ValueError('Workspace metadata exceeds 16 MiB')
    paths = {'workspace.json': document, **{'blobs/' + k: v for k, v in files.items()}}
    manifest = {'schema': 1, 'origin': f'workspace:{kind}:{entity_id}', 'name': str(name)[:120],
                'metadata': {'workspace_schema': 1, 'kind': kind}, 'files': [
                    {'path': key, 'role': 'attachment', 'size': path.stat().st_size, 'sha256': digest(path)}
                    for key, path in sorted(paths.items())]}
    return manifest, paths


def import_workspace(store, blobs, uid, snapshot_id, paths, *, root_project_id=0, progress=None):
    """Import one immutable revision, preserving photos, products and scene graph.

    ``paths`` must come from an authenticated, checksum-verified transfer.
    New records remain inactive until every local/cloud file copy succeeds.
    """
    uid = int(uid)
    if not re.fullmatch('[0-9a-f]{64}', snapshot_id):
        raise ValueError('Invalid workspace revision')
    document = Path(paths.get('workspace.json', ''))
    if not document.is_file() or document.stat().st_size > 16 * 1024**2:
        raise ValueError('The workspace document is missing or too large')
    package = json.loads(document.read_text(encoding='utf-8'))
    if package.get('schema') != 1 or package.get('kind') not in {'project', 'mymosaic'}:
        raise ValueError('Unsupported workspace schema')
    tables = package.get('tables')
    if not isinstance(tables, dict) or set(tables) - FIELDS.keys():
        raise ValueError('Unsupported workspace table')
    for table, rows in tables.items():
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ValueError('Invalid workspace records')
        if any(not isinstance(row, dict) or set(row) - FIELDS[table] for row in rows):
            raise ValueError('Unexpected workspace fields')
        if table not in NO_PRIMARY:
            key = PRIMARY[table]
            keys = [row[key] for row in rows if key in row]
            if any(type(key) is not int or not 0 < key < 2**53 for key in keys) or len(keys) != len(set(keys)):
                raise ValueError('Invalid or repeated workspace identifier')
            if table in {PROJECTS, ASSETS, MAPS} and len(keys) != len(rows):
                raise ValueError('Missing workspace identifier')
    files = package.get('files')
    if not isinstance(files, list) or any(not isinstance(k, str) for k in files) or len(files) != len(set(files)) or len(files) > 100000:
        raise ValueError('Invalid workspace files')
    for key in files:
        if '/' not in safe_relative(key):
            raise ValueError('Invalid workspace file path')
        container, relative = key.split('/', 1)
        if container not in CONTAINERS or 'blobs/' + key not in paths:
            raise ValueError('Workspace file is missing')
    # Every storage reference must be supplied by this snapshot. A safe-looking
    # relative path is insufficient: it could name another account's blob.
    supplied = {key.split('/', 1)[1] for key in files}
    directories = {str(parent) for path in supplied for parent in PurePosixPath(path).parents if str(parent) != '.'}

    def check_metadata(value, key='', base=''):
        if isinstance(value, dict):
            for k, item in value.items():
                check_metadata(item, k, base)
        elif isinstance(value, list):
            for item in value:
                check_metadata(item, key, base)
        elif isinstance(value, str) and value:
            if value.startswith(('{', '[')):
                try:
                    nested = json.loads(value)
                except ValueError:
                    return
                check_metadata(nested, key, base)
            elif key.lower().endswith(('_path', '_master', '_blob', '_prefix', '_visualization', '_preview_webp')) or key.lower() in {'path', 'quality_report_pdf'}:
                safe_relative(value)
                candidates = [value] + ([base + '/' + value] if base else [])
                available = any(path in supplied | directories for path in candidates)
                if not available and any('{' in path for path in candidates):
                    # XYZ derivative manifests describe a family of files,
                    # relative to their own asset, never an arbitrary prefix.
                    for path in candidates:
                        pattern = re.escape(path)
                        for coordinate in ('x', 'y', 'z'):
                            pattern = pattern.replace(r'\{' + coordinate + r'\}', r'[0-9]+')
                        if any(re.fullmatch(pattern, item) for item in supplied):
                            available = True
                            break
                if not available:
                    raise ValueError('A metadata file is missing from the workspace')

    for project in tables.get(PROJECTS, []):
        safe_relative(project['FOLDER'])
        if int(project.get('STATE_ID') or 0) in (1, 2):
            raise ValueError('Active processing cannot be imported')
        contract = project.get('CANONICAL_RESULT_CONTRACT') or {}
        if not isinstance(contract, dict):
            raise ValueError('Invalid processed result contract')
        check_metadata(contract)
        for key, value in contract.items():
            if (key.endswith('_master') or key == 'quality_report_pdf') and value:
                if 'imagenes/' + safe_relative(value) not in files:
                    raise ValueError('A processed master is missing from the workspace')
    for photo in tables.get('dbo.WEB_PROJECTS_FILES', []):
        if '/' in safe_relative(photo['FILENAME']):
            raise ValueError('Invalid source photo filename')
    for asset in tables.get(ASSETS, []):
        for field, containers in [('STORAGE_RELATIVE_PATH', ('assets',)), ('SOURCE_BLOB_NAME', ('assets',)), ('PREVIEW_RELATIVE_PATH', ('results', 'assets'))]:
            value = asset.get(field)
            if value and not any(c + '/' + safe_relative(value) in files for c in containers):
                raise ValueError('An asset file is missing from the workspace: ' + field)
        for field in ('DISPLAY_ENTRY_PATH', 'DISPLAY_BLOB_PREFIX', 'STORED_FILE_NAME'):
            if asset.get(field):
                safe_relative(asset[field])
        if '/' in str(asset.get('STORED_FILE_NAME') or ''):
            raise ValueError('Invalid stored filename')
        prefix = asset.get('DISPLAY_BLOB_PREFIX')
        entry = asset.get('DISPLAY_ENTRY_PATH')
        if prefix and prefix not in directories:
            raise ValueError('An asset display directory is missing from the workspace')
        if entry:
            relative = (prefix or f"external/{asset['PROJECT_ID']}/{asset['ID']}") + '/' + entry
            if relative not in supplied:
                raise ValueError('An asset display entry is missing from the workspace')
        for field in ('ELEVATION_META', 'INSPECTION_META', 'DERIVATIVES_MANIFEST', 'VECTOR_LAYERS'):
            check_metadata(asset.get(field), base=f"external/{asset['PROJECT_ID']}/{asset['ID']}")
    project_ids = {int(p['ID']) for p in tables.get(PROJECTS, [])}
    asset_ids = {int(a['ID']) for a in tables.get(ASSETS, [])}
    map_ids = {int(m['ID']) for m in tables.get(MAPS, [])}
    expected_ids = project_ids if package['kind'] == 'project' else map_ids
    if type(package.get('entity_id')) is not int or package['entity_id'] not in expected_ids:
        raise ValueError('The workspace root is missing')
    for table, rows in tables.items():
        for row in rows:
            for key in ('PROJECT_ID', 'Project_id', 'PARENT_PROJECT_ID', 'SURVEY_ID'):
                if int(row.get(key) or 0) and int(row[key]) not in project_ids:
                    raise ValueError('A project dependency is missing')
            if int(row.get('MYMAP_ID') or 0) and int(row['MYMAP_ID']) not in map_ids:
                raise ValueError('A MyMosaic dependency is missing')
    state_dir = Path(store.data_root) / 'workspace_revisions' / str(uid) / snapshot_id
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / 'state.json'

    def save(state):
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state), encoding='utf-8')
        temporary.replace(state_path)

    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get('complete'):
        return state['result']
    with store.lock, store.stripe_transaction():
        mappings = state.setdefault('mappings', {})
        for table, rows in tables.items():
            if table in NO_PRIMARY:
                continue
            key = PRIMARY[table]
            mapping = mappings.setdefault(table, {})
            target_rows = store.tables.setdefault(table, [])
            for index, row in enumerate(rows):
                source_id = str(row.get(key, 'row-' + str(index)))
                if source_id in mapping:
                    continue
                use_root = table == PROJECTS and package['kind'] == 'project' and int(row['ID']) == int(package['entity_id']) and root_project_id
                if use_root:
                    target = next((p for p in target_rows if int(p.get('ID') or 0) == int(root_project_id)
                                   and int(p.get('CREATE_ID') or 0) == uid and p.get('SYNC_ORIGIN')), None)
                    if target is None:
                        raise ValueError('Invalid destination project')
                    new_id = int(root_project_id)
                else:
                    minimum = 1_000_000_000_000 if os.environ.get('MOSAICEARTH_DESKTOP_SHARED_UI') == '1' else 1_000_000
                    new_id = max([int(r.get(key) or 0) for r in target_rows] + [minimum]) + 1
                    target_rows.append({key: new_id, 'ACTIVO': False, 'IMPORT_PENDING': snapshot_id, 'CREATE_ID': uid, 'USER_ID': uid})
                mapping[source_id] = new_id
            store.persist_table(table)
    save(state)
    pmap = mappings.get(PROJECTS, {})
    amap = mappings.get(ASSETS, {})
    mmap = mappings.get(MAPS, {})
    prefixes = []
    for p in tables.get(PROJECTS, []):
        prefixes.append((safe_relative(p['FOLDER']), f"{pmap[str(p['ID'])]}-synced"))
    for a in tables.get(ASSETS, []):
        prefixes.append((f"external/{a['PROJECT_ID']}/{a['ID']}", f"external/{pmap[str(a['PROJECT_ID'])]}/{amap[str(a['ID'])]}"))
    prefixes.sort(key=lambda pair: len(pair[0]), reverse=True)

    def relocate(relative):
        for old, new in prefixes:
            if relative == old or relative.startswith(old + '/'):
                return new + relative[len(old):]
        return f'exchange/{uid}/{snapshot_id}/' + safe_relative(relative)

    exact = {}
    for key in files:
        container, relative = key.split('/', 1)
        exact[relative] = relocate(relative)
        source = Path(paths['blobs/' + key])
        target = blobs.resolve_path(container, exact[relative])
        if target is None:
            raise ValueError('Invalid destination file')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            # A canceled or failed copy cannot become a corrupt final file that
            # prevents resuming the same immutable revision.
            fd, temporary_name = tempfile.mkstemp(prefix='.workspace-', dir=target.parent)
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                shutil.copyfile(source, temporary)
                if digest(source) != digest(temporary):
                    raise ValueError('A workspace source changed during import')
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        if digest(source) != digest(target):
            raise ValueError('A destination file differs from this revision; local edits are preserved')
        if blobs.is_s3_active() and not blobs._upload_file_to_s3(container, exact[relative], target):
            raise ValueError('Cloud storage copy interrupted. Resume this revision.')
        if progress:
            progress(80, 'Copying verified workspace files')

    def rewrite(value):
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if not isinstance(value, str):
            return value
        if value in exact:
            return exact[value]
        for old, new in prefixes:
            if value == old or value.startswith(old + '/'):
                return new + value[len(old):]
        if value.startswith(('[', '{')):
            try:
                return json.dumps(rewrite(json.loads(value)), ensure_ascii=False)
            except ValueError:
                pass
        return value

    with store.lock, store.stripe_transaction():
        for table, rows in tables.items():
            target_rows = store.tables.setdefault(table, [])
            for index, raw in enumerate(rows):
                row = rewrite(raw)
                if table not in NO_PRIMARY:
                    key = PRIMARY[table]
                    row[key] = mappings[table][str(raw.get(key, 'row-' + str(index)))]
                    existing = next((r for r in target_rows if r.get(key) == row[key]), None)
                    if existing and existing.get('IMPORT_PENDING') != snapshot_id and existing.get('IMPORT_REVISION') != snapshot_id and not (
                            table == PROJECTS and row[key] == root_project_id and int(existing.get('CREATE_ID') or 0) == uid and existing.get('SYNC_ORIGIN')):
                        raise ValueError('A destination record changed; preserve it and import a new revision')
                for key in ('PROJECT_ID', 'Project_id', 'PARENT_PROJECT_ID', 'SURVEY_ID'):
                    if int(raw.get(key) or 0):
                        row[key] = pmap[str(raw[key])]
                for key in ('ASSET_ID', 'SOURCE_ASSET_ID', 'EXTERNAL_ASSET_ID'):
                    if int(raw.get(key) or 0):
                        if str(raw[key]) not in amap:
                            # Public references remain references and are validated below.
                            public = store.get_external_asset_by_id(int(raw[key]))
                            if not public or public.get('VISIBILITY') != 'public':
                                raise ValueError('A private asset dependency is missing')
                        else:
                            row[key] = amap[str(raw[key])]
                if raw.get('MYMAP_ID'):
                    row['MYMAP_ID'] = mmap[str(raw['MYMAP_ID'])]
                if table == 'dbo.WEB_MYMAPS_ITEMS':
                    publication_id = int(raw.get('PUBLICATION_ID') or raw.get('PUBLISH_ID') or 0)
                    if not any(int(p.get('ID') or 0) == publication_id and int(p.get('STATUS_ID') or 0) == 2 for p in store.tables.get('dbo.WEB_PUBLICATIONS', [])):
                        raise ValueError('A public MyMosaic reference is unavailable in the destination catalog')
                row.update(CREATE_ID=uid, UPDATE_ID=uid, USER_ID=uid, AUTHOR_ID=uid, ACTIVO=True, IMPORT_REVISION=snapshot_id)
                if table in (MAPS, ASSETS):
                    row.update(VISIBILITY='private', IS_PUBLISHED=False, PRICE_CREDITS=0)
                if table == PROJECTS:
                    if int(row.get('STATE_ID') or 0) in (1, 2):
                        raise ValueError('Active processing cannot be imported')
                    row['SEO_URL_FRIENDLY'] = f"synced-{row['ID']}"
                    if isinstance(row.get('CANONICAL_RESULT_CONTRACT'), dict):
                        row['CANONICAL_RESULT_CONTRACT']['project_id'] = row['ID']
                if table == MAPS:
                    row['SEO_URL_FRIENDLY'] = f"synced-mosaic-{row['ID']}"
                if table in {'dbo.MOSAICEARTH_ASSET_ANNOTATIONS', 'dbo.MOSAICEARTH_ASSET_TEMPORAL'}:
                    match = re.fullmatch(r'(external-|processed-[123]-)([0-9]+)', str(raw.get('ASSET_KEY') or ''))
                    mapping = amap if match and match[1] == 'external-' else pmap
                    if not match or match[2] not in mapping:
                        raise ValueError('An annotation asset is missing from the workspace')
                    row['ASSET_KEY'] = match[1] + str(mapping[match[2]])
                if table not in NO_PRIMARY:
                    key = PRIMARY[table]
                    target_rows[:] = [r for r in target_rows if r.get(key) != row[key]]
                else:
                    target_rows[:] = [r for r in target_rows if not (r.get('IMPORT_REVISION') == snapshot_id and r.get('MYMAP_ID') == row.get('MYMAP_ID') and r.get('PROJECT_ID') == row.get('PROJECT_ID') and r.get('ASSET_ID') == row.get('ASSET_ID') and r.get('ASSET_KEY') == row.get('ASSET_KEY') and r.get('PUBLICATION_ID') == row.get('PUBLICATION_ID') and r.get('FILE_TYPE_ID') == row.get('FILE_TYPE_ID'))]
                target_rows.append(row)
            store.persist_table(table)
    target_id = pmap[str(package['entity_id'])] if package['kind'] == 'project' else mmap[str(package['entity_id'])]
    result = {'kind': package['kind'], 'id': target_id, 'projects': len(pmap), 'assets': len(amap), 'files': len(files), 'private_copy': True}
    state.update(complete=True, result=result)
    save(state)
    return result
