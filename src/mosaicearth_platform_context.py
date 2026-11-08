"""Local identity for the shared Desktop UI; cloud identity is separate."""
import json
import os
import secrets
import hashlib
import subprocess
import time
import shutil
import threading
import uuid
from pathlib import Path
from mosaicearth_license import verify_grant
from mosaicearth_desktop import Workspace, ODX_IMAGE
from mosaicearth_sync import Client

_data = None
workspace = None
catalog_status = {'state': 'not_loaded'}
catalog_lock = threading.Lock()
stop_worker = threading.Event()


def initialize(data, port):
    global _data, workspace
    _data = Path(data)
    workspace = Workspace(_data)
    auth = _data / 'nodeodx-auth.json'
    if not auth.exists():
        with auth.open('x') as stream:
            os.chmod(auth, 0o600)
            json.dump({'token': secrets.token_hex(32)}, stream)
    token = json.loads(auth.read_text())['token']
    os.environ.setdefault('MOSAICEARTH_LOCAL_NODEODX', f'http://127.0.0.1:{port + 1}')
    os.environ['MOSAICEARTH_LOCAL_NODEODX_TOKEN'] = token


def start_local_worker():
    """Run local jobs without importing the production payments/email/Pod loop."""
    from homelab.db_store import get_store
    import logging
    store = get_store()
    store.get_job_queue().ensure_workers_started()
    stop_worker.clear()

    def poll():
        while not stop_worker.wait(3):
            try:
                with store.lock:
                    tasks = [(int(p['ID']), int(p.get('CREATE_ID') or 0))
                             for p in store.tables.get('dbo.WEB_PROJECTS', [])
                             if not p.get('DESKTOP_PUBLIC_CATALOG') and p.get('NODEODM_UUID')
                             and int(p.get('STATE_ID') or 0) in (1, 2)]
                for pid, uid in tasks:
                    if stop_worker.is_set():
                        return
                    store.poll_photogrammetry_nodeodm(pid, uid)
            except Exception:
                logging.getLogger(__name__).exception('Local processing status could not be refreshed')
    threading.Thread(target=poll, name='desktop-local-worker', daemon=True).start()


def start_node():
    if os.environ.get('MOSAICEARTH_PLATFORM_CONTAINER') == '1':
        from homelab.db_store import get_store
        status = get_store().check_nodeodm_engine(allow_embedded_fallback=False)
        return {**status, 'ok': bool(status.get('online')), 'message': 'NodeODX is ready.' if status.get('online') else 'Restart Desktop to restart its local engine.'}
    workspace.require_engine('odx')
    identity = hashlib.sha256(str(_data).encode()).hexdigest()[:16]
    name = 'mosaicearth-nodeodx-' + identity
    inspect = subprocess.run([*workspace.docker, 'inspect', name], capture_output=True, timeout=20)
    if inspect.returncode == 0:
        container = json.loads(inspect.stdout)[0]
        if container['Config'].get('Labels', {}).get('mosaicearth.workspace') != identity:
            raise ValueError('A different container already uses this engine name.')
        subprocess.run([*workspace.docker, 'start', name], check=True, capture_output=True, timeout=45)
    else:
        node_data = _data / 'nodeodx-data'
        node_data.mkdir(exist_ok=True)
        port = os.environ['MOSAICEARTH_LOCAL_NODEODX'].rsplit(':', 1)[1]
        subprocess.run([*workspace.docker, 'run', '-d', '--pull=never', '--name', name,
                        '--label', 'mosaicearth.workspace=' + identity,
                        '-p', f'127.0.0.1:{port}:3000',
                        *workspace.mount(node_data, '/var/www/data'), ODX_IMAGE,
                        '--max-concurrency', '1', '--token', os.environ['MOSAICEARTH_LOCAL_NODEODX_TOKEN']],
                       check=True, capture_output=True, timeout=60)
    from homelab.db_store import get_store
    for _ in range(30):
        status = get_store().check_nodeodm_engine(allow_embedded_fallback=False)
        if status.get('online'):
            return {**status, 'ok': True, 'message': 'NodeODX / ODX is ready for local processing.'}
        time.sleep(1)
    raise ValueError('NodeODX is starting. Check its status again in a moment.')


def cloud_account():
    if workspace is None:
        return {'logged_in': False}
    with workspace.cloud_lock:
        session = workspace.cloud_session or {}
        return {'logged_in': bool(session), 'email': session.get('user', ''),
                'cloud_uid': session.get('cloud_uid'), 'cloud_url': 'https://app.mosaicearth.app'}


def license_account():
    resources = Path(__file__).resolve().parents[1] / 'resources'
    commercial = (resources / 'edition-commercial').is_file() or os.environ.get('MOSAICEARTH_OFFICIAL_INSTALLATION') == '1'
    if not commercial:
        return {'edition':'source', 'activated':False}
    try:
        grant = verify_grant(json.loads((_data / 'account-grant.json').read_text()),
                             json.loads((resources / 'license-public.json').read_text()),
                             device=os.environ.get('MOSAICEARTH_HOST_DEVICE') or None)
        return {'edition':'official', 'activated':True, 'email':grant['email'], 'expires_at':grant['expires_at']}
    except (OSError, ValueError, KeyError):
        return {'edition':'official', 'activated':False}


def push_workspace(kind, entity_id):
    client = workspace.cloud_client()
    if kind == 'project':
        from homelab.db_store import get_store
        for _ in range(3):
            project = get_store().get_project(int(entity_id))
            if not project or int(project.get('CREATE_ID') or 0) != 1:
                raise ValueError('This project does not belong to the local workspace')
            parent = project.get('PARENT_PROJECT_ID')
            if not parent:
                break
            entity_id = int(parent)
    identity_file = _data / 'workspace-id'
    if not identity_file.exists():
        identity_file.write_text(uuid.uuid4().hex)
    identity = identity_file.read_text().strip()
    key = f'upload:{kind}:{int(entity_id)}'

    def upload(progress, canceled):
        from homelab.workspace_exchange import export_workspace
        from homelab.db_store import get_store
        from homelab.blob_store import get_blob_store
        client.progress, client.canceled = progress, canceled
        capabilities = client.request('GET', '/api/v2/sync/capabilities')
        if capabilities.get('workspace_schema') != 1:
            raise ValueError('The cloud service needs an update before it can receive complete workspaces.')
        progress(0, 0, 'Preparing project structure, layers and original files')
        manifest, paths = export_workspace(get_store(), get_blob_store(), 1, kind, int(entity_id),
                                          _data / 'workspace_sync/outgoing' / uuid.uuid4().hex)
        manifest['origin'] = f'workspace:{identity}:{kind}:{int(entity_id)}'
        result = client.push(manifest, paths)
        return {'cloud_id': result.get('project_id'), 'snapshot_id': result['id'],
                'workspace': result.get('workspace_result'), 'private_copy': True}
    return workspace.start_transfer(key, upload)


def pull_workspace(kind, entity_id):
    client = workspace.cloud_client()
    key = f'download:{kind}:{int(entity_id)}'

    def download(progress, canceled):
        from homelab.workspace_exchange import import_workspace
        from homelab.db_store import get_store
        from homelab.blob_store import get_blob_store
        client.progress, client.canceled = progress, canceled
        state = client.request('POST', f'/api/v2/sync/workspaces/{kind}/{int(entity_id)}/export', {})
        deadline = time.monotonic() + 24 * 3600
        while state['status'] in {'QUEUED', 'VERIFYING'}:
            if canceled() or time.monotonic() > deadline:
                raise ValueError('Export paused. Resume to check the saved cloud revision.')
            progress(0, 0, 'Cloud is preparing all project files and scene references')
            time.sleep(2)
            state = client.request('GET', f"/api/v2/sync/sessions/{state['id']}")
        if state['status'] != 'COMPLETE':
            raise ValueError(state.get('error') or 'Cloud export failed; no local project was changed.')
        directory = _data / 'workspace_sync/incoming' / state['id']
        state = client.pull(state['id'], directory)
        paths = {item['path']: directory / item['path'] for item in state['manifest']['files']}
        progress(state['bytes'], state['bytes'], 'Restoring the complete local project and MyMosaic')
        result = import_workspace(get_store(), get_blob_store(), 1, state['id'], paths)
        return {'snapshot_id': state['id'], 'workspace': result}
    return workspace.start_transfer(key, download)


def storage_status_html():
    disk = shutil.disk_usage(_data)
    return ('<section class="myearth-quota-bar" aria-label="Local storage" '
            'style="color:#e8f1e8;background:#132017;border:1px solid #526844;border-radius:10px;padding:18px">'
            '<div><strong>LOCAL WORKSPACE</strong><p style="margin:6px 0 0">'
            'Projects, surveys, tasks and layers below are stored on this computer. '
            'Cloud synchronization is an explicit action.</p></div>'
            f'<div><strong>{disk.free / 1024**3:.1f} GiB free</strong>'
            '<p style="margin:6px 0 0">Available on the local drive · no cloud storage quota</p></div></section>')


def refresh_public_catalog():
    """Refresh only public rows; never mix the cloud login with local ownership."""
    with catalog_lock:
        catalog_status.update(state='loading')
        try:
            payload = Client().request('GET', '/api/v2/desktop/public-catalog')
            from homelab.db_store import get_store
            store = get_store()
            allowed = ('dbo.WEB_PUBLICATIONS', 'dbo.WEB_PROJECTS', 'dbo.WEB_PROJECTS_LOCATION',
                       'dbo.WEB_PROJECTS_VALUES', 'dbo.WEB_CESIUM_MODELS',
                       'dbo.MOSAICEARTH_EXTERNAL_ASSETS', 'dbo.WEB_MYMAPS', 'dbo.WEB_MYMAPS_ITEMS')
            staged = {}
            with store.lock:
                for table in allowed:
                    local = [r for r in store.tables.get(table, []) if not r.get('DESKTOP_PUBLIC_CATALOG')]
                    local_ids = {r.get('ID', r.get('Id')) for r in local if r.get('ID', r.get('Id')) is not None}
                    remote = []
                    for raw in payload.get(table, []):
                        row = dict(raw)
                        if row.get('ID', row.get('Id')) in local_ids:
                            raise ValueError('A legacy local ID conflicts with the public catalog. Migrate the workspace before refreshing.')
                        row['DESKTOP_PUBLIC_CATALOG'] = True
                        for key in ('CREATE_ID', 'USER_ID'):
                            if int(row.get(key) or 0) > 0:
                                row[key] = 3_000_000_000_000 + int(row[key])
                        remote.append(row)
                    staged[table] = local + remote
                for table, rows in staged.items():
                    store.tables[table] = rows
                    store.persist_table(table)
            catalog_status.update(state='ready', refreshed_at=time.time(),
                                  projects=len(payload.get('dbo.WEB_PROJECTS', [])),
                                  mosaics=len(payload.get('dbo.WEB_MYMAPS', [])))
        except ValueError as exc:
            catalog_status.update(state='error', message=str(exc))
        except Exception:
            catalog_status.update(state='offline', message='Could not refresh the public catalog. Previously cached public content remains available.')
        return dict(catalog_status)


def local_user():
    if _data is None:
        return None
    resources = Path(__file__).resolve().parents[1] / 'resources'
    if (resources / 'edition-commercial').is_file() or os.environ.get('MOSAICEARTH_OFFICIAL_INSTALLATION') == '1':
        try:
            verify_grant(json.loads((_data / 'account-grant.json').read_text()),
                         json.loads((resources / 'license-public.json').read_text()),
                         device=os.environ.get('MOSAICEARTH_HOST_DEVICE') or None)
        except (OSError, ValueError):
            return None
    from homelab.db_store import get_store
    store = get_store()
    # The local owner never becomes the cloud account. Signing out from the
    # cloud must not hide or reassign files already stored on this computer.
    with store.lock:
        user = next((u for u in store.tables['dbo.USERS'] if int(u.get('ID') or 0) == 1), None)
        if user is None:
            user = {'ID': 1, 'EMAIL': 'local@mosaicearth.desktop',
                    'NOMBRE': 'Local', 'NAME': 'Local workspace',
                    'USERNAME': 'Local workspace', 'ACTIVO': True, 'ACTIVE': True,
                    'PLAN_KEY': 'studio', 'STORAGE_QUOTA_GB': 10000,
                    'IS_ADMIN': False, 'ROL_ID': 2}
            store.tables['dbo.USERS'].append(user)
            store.persist_table('dbo.USERS')
        return store.get_user_by_id(1)
