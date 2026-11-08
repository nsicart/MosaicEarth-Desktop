"""Desktop adapters used by the shared platform interface."""
import os
import shutil
import subprocess
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from pathlib import Path
from starlette.concurrency import run_in_threadpool
import mosaicearth_platform_context as context
from mosaicearth_sync import SyncError

router = APIRouter()


@router.get('/Default.aspx')
@router.get('/Logout.ashx')
def local_account_entry():
    return RedirectResponse('/en/Projects#desktopCloudProjectsHost', status_code=303)


@router.get('/Client/Profile.aspx')
@router.get('/Client/CreditPacks.aspx')
@router.get('/{lang}/Profile')
@router.get('/{lang}/CreditPacks')
@router.get('/{lang}/Desktop')
def cloud_account_page(request: Request):
    page = request.url.path.rsplit('/', 1)[-1].removesuffix('.aspx')
    return RedirectResponse('https://app.mosaicearth.app/en/' + page, status_code=303)


@router.get('/desktop-platform-ui.js')
def desktop_ui_script():
    return FileResponse(Path(__file__).resolve().parents[1] / 'resources/platform-ui.js', media_type='text/javascript')


@router.get('/api/desktop/share-origin')
def share_origin(request: Request):
    return {'ok': True, 'shareOrigin': str(request.base_url).rstrip('/'), 'local_only': True}


def workspace():
    if not context.local_user():
        raise HTTPException(401, 'Activate this official installation to open the local workspace.')
    return context.workspace


@router.get('/api/desktop/host-controls')
def host_controls():
    workspace()
    return {'url': os.environ.get('MOSAICEARTH_LAUNCHER_URL', ''),
            'token': os.environ.get('MOSAICEARTH_LAUNCHER_TOKEN', '')}


@router.get('/api/desktop/version')
def version():
    return {'ok': True, 'version': '0.3.0-dev', 'engine': 'NodeODX / ODX + COLMAP / OpenSplat',
            'desktop_local': True, 'automatic_update': False,
            'download_url': 'https://app.mosaicearth.app/en/Desktop'}


@router.post('/api/desktop/shutdown')
@router.post('/api/local/shutdown')
def preserve_running_workspace():
    workspace()
    return JSONResponse({'ok': False, 'message': 'Close the Desktop window when finished. Local processing and transfers continue in the background.'}, status_code=409)


@router.get('/api/desktop/public-catalog-status')
def public_catalog_status():
    return dict(context.catalog_status)


@router.post('/api/desktop/refresh-public-catalog')
def refresh_public_catalog():
    return context.refresh_public_catalog()


@router.get('/api/desktop/nodeodm-status')
def engine_status():
    workspace()
    status = ({'docker': True, 'engines': {'OpenSplat': bool(shutil.which('opensplat'))}}
              if os.environ.get('MOSAICEARTH_PLATFORM_CONTAINER') == '1' else workspace().installation_status())
    from homelab.db_store import get_store
    node = get_store().check_nodeodm_engine(allow_embedded_fallback=False)
    return {**node, 'docker': status['docker'], 'installed_engines': status['engines'],
            'message': node.get('error') or ('NodeODX is ready.' if node.get('online') else 'Set up the local engines before processing.')}


@router.get('/api/desktop/installation')
def installation():
    if os.environ.get('MOSAICEARTH_PLATFORM_CONTAINER') == '1':
        return engine_status()
    return workspace().installation_status()


@router.post('/api/desktop/start-nodeodm')
def start_node():
    workspace()
    try:
        return context.start_node()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'Could not start Docker / NodeODX. Check the local engine installation.'
        return JSONResponse({'ok': False, 'online': False, 'message': message}, status_code=503)


@router.post('/api/desktop/cloud-login')
async def cloud_login(request: Request):
    data = await request.json()
    try:
        await run_in_threadpool(workspace().cloud_signin, str(data.get('email', '')).strip(), str(data.get('password', '')))
        return {'ok': True, 'account': context.cloud_account()}
    except (SyncError, ValueError) as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=401)


@router.post('/api/desktop/cloud-logout')
async def cloud_logout():
    local = workspace()
    if local.cloud_session:
        await run_in_threadpool(local.cloud_signout)
    return {'ok': True}


@router.post('/api/desktop/sync-to-cloud')
@router.post('/api/desktop/sync-from-cloud')
async def transfer_workspace(request: Request):
    workspace()
    body = await request.json()
    kind = body.get('kind') or body.get('type') or 'project'
    if kind not in {'project', 'mymosaic'}:
        raise HTTPException(400, 'Choose a project or MyMosaic')
    try:
        operation = context.push_workspace if request.url.path.endswith('sync-to-cloud') else context.pull_workspace
        result = await run_in_threadpool(operation, kind, int(body.get('id') or 0))
        return {'ok': True, **result}
    except ValueError as exc:
        return JSONResponse({'ok': False, 'login_required': not context.cloud_account()['logged_in'], 'message': str(exc)}, status_code=400)


@router.get('/api/desktop/transfers')
def transfers():
    local = workspace()
    with local.transfer_lock:
        return {'ok': True, 'transfers': {key: dict(value) for key, value in local.transfers.items()}}


@router.get('/api/desktop/legacy-workspace')
def legacy_workspace():
    from mosaicearth_migration import legacy_status
    local = workspace()
    if not os.environ.get('MOSAICEARTH_LEGACY_IMPORT_BLOBS'):
        return {'available': False}
    return legacy_status(local.root)


@router.post('/api/desktop/import-legacy-workspace')
def import_legacy_workspace():
    from mosaicearth_migration import migrate_legacy
    from homelab.db_store import get_store
    from homelab.blob_store import get_blob_store
    local = workspace()
    source = os.environ.get('MOSAICEARTH_LEGACY_IMPORT_BLOBS')
    if not source:
        raise HTTPException(404, 'No earlier local workspace was found.')
    return {'ok': True, **local.start_transfer('migration:legacy:1',
        lambda progress, canceled: migrate_legacy(local.root, source, get_store(), get_blob_store(), progress, canceled))}


@router.post('/api/desktop/pause-transfer')
async def pause_transfer(request: Request):
    body = await request.json()
    local = workspace()
    with local.transfer_lock:
        state = local.transfers.get(str(body.get('id') or ''))
        if not state or state['status'] != 'running':
            raise HTTPException(409, 'This transfer is not running')
        state['pause'] = True
    return {'ok': True}


@router.get('/api/desktop/cloud-workspace')
def cloud_workspace():
    local = workspace()
    with local.cloud_lock:
        session = dict(local.cloud_session or {})
    if not session:
        return {'ok': True, 'connected': False, 'projects': [], 'mymosaics': []}
    try:
        data = local.cloud_client().request('POST', '/api/v2/desktop/cloud-workspace-data',
            {'token': session['token'], 'cloud_uid': session['cloud_uid']})
        return {**data, 'connected': True, 'cloudOrigin': 'https://app.mosaicearth.app',
                'projects': [{**p, 'ID': p['id'], 'NAME': p['name'], 'Surveys': [None] * p.get('surveys', 0),
                              'TOTAL_LAYERS': p.get('tasks', 0)} for p in data.get('projects', [])]}
    except SyncError as exc:
        return JSONResponse({'ok': False, 'connected': True, 'message': str(exc)}, status_code=502)


@router.post('/api/desktop/force-update')
def check_update():
    from mosaicearth_sync import Client
    workspace()
    try:
        data = Client().request('GET', '/api/desktop/version')
        return {**data, 'ok': True, 'automatic_update': False,
                'download_url': 'https://app.mosaicearth.app/en/Desktop',
                'message': 'Published version: ' + str(data.get('version') or 'unavailable') + '. Download updates from Desktop & Connectors.'}
    except SyncError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=502)
