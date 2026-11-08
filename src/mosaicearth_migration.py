"""Copy an earlier full Desktop workspace without modifying its database/files.

Only local owner 1 is migrated. Cached public users, credentials and remote jobs
are excluded by workspace_exchange's document allowlist. Unfinished legacy tasks
become unprocessed copies; they never inherit a runnable remote engine reference.
"""
import copy
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace


def prepare_legacy(root):
    source = Path(os.environ.get('MOSAICEARTH_LEGACY_WORKSPACE', str(Path.home() / 'MosaicEarth_Local_Data')))
    database = source / 'db/mosaicearth_state.sqlite3'
    if not database.is_file() or not (source / 'blobs').is_dir():
        return None
    target = Path(root) / 'legacy-import'
    target.mkdir(mode=0o700, exist_ok=True)
    temporary = target / 'snapshot.tmp'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as old:
        with sqlite3.connect(temporary) as new:
            old.backup(new)
    temporary.replace(target / 'snapshot.sqlite3')
    return source / 'blobs'


def read_legacy(root):
    database = Path(root) / 'legacy-import/snapshot.sqlite3'
    if not database.is_file():
        return {}
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        return {name: json.loads(value) for name, value in db.execute('SELECT table_name, rows_json FROM table_overrides')}


def legacy_status(root):
    tables = read_legacy(root)
    projects = [p for p in tables.get('dbo.WEB_PROJECTS', [])
                if p.get('ACTIVO', True) and int(p.get('CREATE_ID') or 0) == 1]
    maps = [m for m in tables.get('dbo.WEB_MYMAPS', [])
            if m.get('ACTIVO', True) and int(m.get('CREATE_ID') or 0) == 1 and not m.get('IS_CONTAINER_PREVIEW')]
    return {'available': bool(projects or maps), 'projects': len(projects), 'mymosaics': len(maps),
            'unfinished': sum(int(p.get('STATE_ID') or 0) in (1, 2) for p in projects)}


class ReadOnlyLegacyBlobs:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def ensure_local(self, container, relative, force_download=False):
        from homelab.workspace_exchange import safe_relative, CONTAINERS
        if container not in CONTAINERS:
            raise ValueError('Unsupported legacy storage')
        path = self.root / container / safe_relative(relative)
        if not path.resolve().is_relative_to(self.root / container) or path.is_symlink():
            raise ValueError('A legacy file points outside its workspace')
        return path if path.is_file() else None

    def list_stored_files(self, container, prefix):
        from homelab.workspace_exchange import safe_relative
        base = self.root / container
        folder = base / safe_relative(prefix)
        if not folder.resolve().is_relative_to(base):
            raise ValueError('Invalid legacy folder')
        return sorted(path.relative_to(base).as_posix() for path in folder.rglob('*') if path.is_file()) if folder.is_dir() else []


def migrate_legacy(root, legacy_blobs, store, blobs, progress, canceled):
    from homelab.workspace_exchange import export_workspace, import_workspace, digest
    tables = copy.deepcopy(read_legacy(root))
    projects = [p for p in tables.get('dbo.WEB_PROJECTS', [])
                if p.get('ACTIVO', True) and int(p.get('CREATE_ID') or 0) == 1]
    maps = [m for m in tables.get('dbo.WEB_MYMAPS', [])
            if m.get('ACTIVO', True) and int(m.get('CREATE_ID') or 0) == 1 and not m.get('IS_CONTAINER_PREVIEW')]
    unfinished = 0
    for project in projects:
        if int(project.get('STATE_ID') or 0) in (1, 2):
            unfinished += 1
            project.update(STATE_ID=3, ZIP_UPLOADED=False, CANONICAL_RESULT_CONTRACT={})
            project['TASK_DESCRIPTION'] = (str(project.get('TASK_DESCRIPTION') or '') +
                '\nImported from an unfinished task in the earlier Desktop. Source files are preserved; start processing again in this copy.').strip()
    entities = [('project', int(p['ID'])) for p in projects if not p.get('PARENT_PROJECT_ID') and not p.get('SURVEY_ID')]
    entities += [('mymosaic', int(m['ID'])) for m in maps]
    if not entities:
        raise ValueError('No local projects or MyMosaics were found in the earlier Desktop.')
    source = SimpleNamespace(tables=tables, lock=threading.RLock())
    source_blobs = ReadOnlyLegacyBlobs(legacy_blobs)
    directory = Path(root) / 'legacy-import/export'
    merged, paths, rows_seen = {}, {}, {}
    package = None
    for kind, entity in entities:
        if canceled():
            raise ValueError('Migration paused. The earlier Desktop has not been changed.')
        progress(0, 0, 'Reading previous local projects, photos and layers')
        _, part = export_workspace(source, source_blobs, 1, kind, entity, directory / f'{kind}-{entity}')
        document = json.loads(part['workspace.json'].read_text(encoding='utf-8'))
        package = package or document
        for table, rows in document['tables'].items():
            seen = rows_seen.setdefault(table, set())
            for row in rows:
                key = json.dumps(row, sort_keys=True)
                if key not in seen:
                    merged.setdefault(table, []).append(row)
                    seen.add(key)
        paths.update({key: value for key, value in part.items() if key != 'workspace.json'})
    package.update(tables=merged, files=sorted(key.removeprefix('blobs/') for key in paths))
    document = directory / 'workspace.json'
    document.write_text(json.dumps(package, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    paths['workspace.json'] = document
    total = sum(path.stat().st_size for path in paths.values())
    hashes = []
    for index, (key, path) in enumerate(sorted(paths.items())):
        if canceled():
            raise ValueError('Migration paused. The earlier Desktop has not been changed.')
        progress(0, 0, f'Verifying previous local files: {index + 1} / {len(paths)}')
        hashes.append((key, path.stat().st_size, digest(path)))
    revision = hashlib.sha256(json.dumps(hashes).encode()).hexdigest()
    progress(0, total, 'Restoring a separate local copy; original files remain unchanged')
    result = import_workspace(store, blobs, 1, revision, paths)
    progress(total, total, 'Previous local workspace copied and verified')
    return {'workspace': result, 'unfinished_tasks': unfinished, 'original_preserved': True}
