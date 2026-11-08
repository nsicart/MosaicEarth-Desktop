"""Host supervisor for the complete local platform, without a Docker socket in it.

The source edition opens without an account. Only the official installer checks
its signed device grant. Each data directory owns its own labeled containers.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

from mosaicearth_license import device_identity, verify_grant

PLATFORM_IMAGE = 'mosaicearth-desktop/platform:0.3.0'
NODE_IMAGE = 'mosaicearth-desktop/nodeodx:0.1.0'


class PlatformLauncher:
    def __init__(self, workspace, resources, port, host_port=None, host_token=''):
        self.workspace, self.resources, self.port = workspace, Path(resources), int(port)
        self.identity = hashlib.sha256(str(workspace.root).encode()).hexdigest()[:16]
        self.label = 'mosaicearth.workspace=' + self.identity
        self.network = 'mosaicearth-' + self.identity
        self.node = 'mosaicearth-node-' + self.identity
        self.platform = 'mosaicearth-platform-' + self.identity
        self.url = f'http://127.0.0.1:{port}'
        self.host_port, self.host_token = host_port, host_token
        self.state = {'status': 'waiting', 'message': 'Preparing your local workspace', 'url': self.url}
        self.lock = threading.Lock()

    def command(self, *args, timeout=60, check=True):
        result = subprocess.run([*self.workspace.docker, *args], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout)
        if check and result.returncode:
            # Do not expose Docker command arguments (which contain a node token).
            detail = result.stderr.decode(errors='replace').strip().splitlines()
            raise ValueError('Docker could not complete setup. ' + (detail[-1][:220] if detail else 'Check Docker Desktop.'))
        return result

    def start(self):
        with self.lock:
            if self.state['status'] == 'starting':
                return dict(self.state)
            self.state.update(status='starting', message='Checking local engines and storage')
        threading.Thread(target=self._start, daemon=True).start()
        return dict(self.state)

    def _container(self, name, image, arguments, command):
        existing = self.command('inspect', name, check=False)
        image_id = self.command('image', 'inspect', image, '--format', '{{.Id}}').stdout.decode().strip()
        configuration = hashlib.sha256(json.dumps([arguments, command], separators=(',', ':')).encode()).hexdigest()
        if existing.returncode == 0:
            info = json.loads(existing.stdout)[0]
            if info['Config'].get('Labels', {}).get('mosaicearth.workspace') != self.identity:
                raise ValueError('Another application owns this container name. Your existing containers were preserved.')
            if info['Image'] != image_id or info['Config'].get('Labels', {}).get('mosaicearth.configuration') != configuration:
                # Only replace our own idle containers. Bind-mounted originals,
                # completed models and the database survive runtime updates.
                if info['State']['Running']:
                    if not self._workspace_idle():
                        raise ValueError('Finish the running tasks and transfers before updating this workspace. Your current engines remain running.')
                    self.command('stop', '--time', '30', name)
                self.command('rm', name)
            else:
                self.command('start', name)
                return
        self.command('run', '-d', '--pull=never', '--name', name, '--label', self.label,
                     '--label', 'mosaicearth.configuration=' + configuration,
                     '--network', self.network, *arguments, image, *command, timeout=90)

    def _workspace_idle(self):
        """Fail closed if an existing workspace cannot be inspected coherently."""
        try:
            with urllib.request.urlopen(self.url + '/api/desktop/runtime-idle', timeout=3) as response:
                return json.load(response).get('idle') is True
        except (OSError, ValueError):
            pass
        database = self.workspace.root / 'platform/db/mosaicearth_state.sqlite3'
        if not database.exists():
            return True
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                rows = db.execute('SELECT table_name, rows_json FROM table_overrides').fetchall()
            for table, serialized in rows:
                if table == 'dbo.WEB_PROJECTS':
                    if any(not row.get('DESKTOP_PUBLIC_CATALOG') and int(row.get('STATE_ID') or 0) in (1, 2)
                           for row in json.loads(serialized)):
                        return False
                if table == 'dbo.MOSAICEARTH_BACKGROUND_JOBS':
                    if any(str(row.get('STATUS') or '').upper() in {'QUEUED', 'RUNNING', 'RETRY'}
                           for row in json.loads(serialized)):
                        return False
            # Synchronization runs inside the platform process and is not a
            # photogrammetry job. Preserve it even when every model is finished.
            with urllib.request.urlopen(self.url + '/api/desktop/transfers', timeout=3) as response:
                transfers = json.load(response).get('transfers', {})
            return not any(item.get('status') == 'running' for item in transfers.values())
        except (OSError, ValueError, sqlite3.Error):
            return False

    def _start(self):
        try:
            root = self.workspace.root
            commercial = (self.resources / 'edition-commercial').is_file()
            if commercial:
                verify_grant(json.loads((root / 'account-grant.json').read_text()),
                             json.loads((self.resources / 'license-public.json').read_text()))
            info = self.command('info', '--format', '{{.OSType}}', timeout=45, check=False)
            if info.returncode or info.stdout.strip() != b'linux':
                raise ValueError('Start Docker Desktop with Linux containers, then click Retry.')
            expected = json.loads((self.resources / 'engine-payload.json').read_text())
            for image in (NODE_IMAGE, PLATFORM_IMAGE):
                present = self.command('image', 'inspect', image, '--format', '{{.Id}}', check=False)
                if present.returncode:
                    raise ValueError('Local engines need installation. Use Install engines below, then Retry.')
                if commercial and present.stdout.decode().strip() != expected['image_ids'].get(image):
                    raise ValueError('This runtime does not match the signed-in installer release. Install its verified engines before continuing.')
            network = self.command('network', 'inspect', self.network, check=False)
            if network.returncode:
                self.command('network', 'create', '--label', self.label, self.network)
            elif json.loads(network.stdout)[0].get('Labels', {}).get('mosaicearth.workspace') != self.identity:
                raise ValueError('Another application owns this network name.')
            auth = root / 'nodeodx-auth.json'
            if not auth.exists():
                with auth.open('x') as stream:
                    os.chmod(auth, 0o600)
                    json.dump({'token': secrets.token_hex(32)}, stream)
            token = json.loads(auth.read_text())['token']
            node_data = root / 'nodeodx-data'
            node_data.mkdir(exist_ok=True)
            (root / 'tmp').mkdir(exist_ok=True)
            self.state.update(message='Starting NodeODX and the complete MosaicEarth interface')
            # NodeODX is reachable only on this private Docker network, not LAN.
            self._container(self.node, NODE_IMAGE, self.workspace.mount(node_data, '/var/www/data'),
                            ['--max-concurrency', '1', '--token', token])
            args = ['--cap-drop=ALL', '--security-opt=no-new-privileges',
                    '-p', f'127.0.0.1:{self.port}:{self.port}',
                    '-e', 'MOSAICEARTH_LOCAL_NODEODX=http://' + self.node + ':3000',
                    '-e', 'TMPDIR=/workspace/tmp', *self.workspace.mount(root, '/workspace')]
            from mosaicearth_migration import prepare_legacy
            legacy_blobs = prepare_legacy(root)
            if legacy_blobs:
                args += [*self.workspace.mount(legacy_blobs, '/legacy-blobs', readonly=True),
                         '-e', 'MOSAICEARTH_LEGACY_IMPORT_BLOBS=/legacy-blobs']
            if os.name != 'nt':
                args += ['--user', f'{os.getuid()}:{os.getgid()}']
            else:
                # Docker Desktop's Linux bind mounts support the unprivileged UID.
                args += ['--user', '1000:1000']
            if commercial:
                args += ['-e', 'MOSAICEARTH_OFFICIAL_INSTALLATION=1', '-e', 'MOSAICEARTH_HOST_DEVICE=' + device_identity()]
            if self.host_port and self.host_token:
                args += ['-e', f'MOSAICEARTH_LAUNCHER_URL=http://127.0.0.1:{self.host_port}',
                         '-e', 'MOSAICEARTH_LAUNCHER_TOKEN=' + self.host_token]
            self._container(self.platform, PLATFORM_IMAGE, args, ['--port', str(self.port)])
            for _ in range(90):
                try:
                    with urllib.request.urlopen(self.url + '/api/desktop/version', timeout=3) as response:
                        if json.load(response).get('desktop_local'):
                            self.state.update(status='ready', message='Your local MosaicEarth workspace is ready')
                            return
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            raise ValueError('The local interface did not become ready. Check the engine setup log and retry.')
        except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
            self.state.update(status='failed', message=str(exc))

    def snapshot(self):
        with self.lock:
            return dict(self.state)
