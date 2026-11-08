"""Run the shared MosaicEarth platform against an isolated local workspace.

This entry point is under integration. The processing workspace remains available
while the platform storage and synchronization adapters are being completed.
"""
import argparse
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def create_app(data, port):
    data = Path(data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    platform_data = data / 'platform'
    platform_data.mkdir(exist_ok=True)
    os.environ.update({
        'MOSAICEARTH_DESKTOP_LOCAL': '1',
        'MOSAICEARTH_DESKTOP_MODE': '1',
        'MOSAICEARTH_DESKTOP_SHARED_UI': '1',
        'MOSAICEARTH_DATA_DIR': str(platform_data),
        'MOSAICEARTH_WEB_ROOT': os.environ.get('MOSAICEARTH_WEB_ROOT', str(ROOT / 'build/platform-web')),
        'MOSAICEARTH_CLOUD_ORIGIN': os.environ.get('MOSAICEARTH_CLOUD_ORIGIN', 'https://app.mosaicearth.app'),
        'MOSAICEARTH_PUBLIC_URL': f'http://127.0.0.1:{port}',
        'PUBLIC_SITE_URL': f'http://127.0.0.1:{port}',
        'MOSAICEARTH_STORAGE_MODE': 'local_only',
        'MOSAICEARTH_GAUSSIAN_ENGINE': 'opensplat',
        'MOSAICEARTH_ENABLE_ONDEMAND_3DGS_RUNPOD': '0',
        'MOSAICEARTH_ZERO_LOCAL_DISK': '0',
        'PROJ_NETWORK': 'OFF',
    })
    sys.path.insert(0, str(ROOT / 'platform'))
    import mosaicearth_platform_context as context
    context.initialize(data, port)
    from homelab.app import app as platform_app
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from mosaicearth_platform_api import router
    @asynccontextmanager
    async def lifespan(app):
        context.start_local_worker()
        try:
            yield
        finally:
            context.stop_worker.set()

    app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
    app.include_router(router)
    app.mount('/', platform_app)

    @app.middleware('http')
    async def local_origin(request, call_next):
        expected = f'127.0.0.1:{port}'
        if request.headers.get('host') != expected:
            return JSONResponse({'detail': 'Open this Desktop on its local address.'}, status_code=403)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if (origin and origin != f'http://{expected}') or request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Cross-origin local changes are not allowed.'}, status_code=403)
        return await call_next(request)

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--port', type=int, default=19757)
    parser.add_argument('--container', action='store_true', help='Bind inside the isolated Desktop container; the launcher exposes only host loopback.')
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.data, args.port), host='0.0.0.0' if args.container else '127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
