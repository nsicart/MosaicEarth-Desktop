"""Host supervisor for the complete local platform, without a Docker socket in it.

The source edition opens without an account. Only the official installer checks
its signed device grant. Each data directory owns its own labeled containers.
"""
import hashlib
import json
import os
import platform
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from mosaicearth_license import device_identity, verify_grant
from mosaicearth_gpu import (PLATFORM_CPU, PLATFORM_CUDA, NODE_CPU, NODE_CUDA,
                             detect, preferences, required_images)

PLATFORM_IMAGE = PLATFORM_CPU
NODE_IMAGE = NODE_CPU


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
        self.last_health_check = 0.0

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
                self.command('update', '--restart', 'unless-stopped', name)
                self.command('start', name)
                return
        self.command('run', '-d', '--pull=never', '--name', name, '--label', self.label,
                     '--label', 'mosaicearth.configuration=' + configuration,
                     '--restart', 'unless-stopped',
                     '--network', self.network, *arguments, image, *command, timeout=90)

    def _workspace_idle(self):
        """Fail closed if an existing workspace cannot be inspected coherently."""
        try:
            with urllib.request.urlopen(self.url + '/api/desktop/runtime-idle', timeout=3) as response:
                return json.load(response).get('idle') is True
        except (OSError, ValueError):
            # Never open the live Linux SQLite file through a Windows bind
            # mount. Their WAL/shm locking implementations are incompatible.
            # An unreachable runtime cannot authorize an automatic upgrade.
            return False

    def _stop_outdated_platform(self, image=PLATFORM_IMAGE):
        """Release old Windows bind-mount handles before refreshing migration data."""
        existing = self.command('inspect', self.platform, check=False)
        if existing.returncode:
            return
        info = json.loads(existing.stdout)[0]
        if info['Config'].get('Labels', {}).get('mosaicearth.workspace') != self.identity:
            raise ValueError('Another application owns this container name. Your existing containers were preserved.')
        image_id = self.command('image', 'inspect', image, '--format', '{{.Id}}').stdout.decode().strip()
        if info['Image'] != image_id and info['State']['Running']:
            if not self._workspace_idle():
                raise ValueError('Finish the running tasks and transfers before updating this workspace. Your current engines remain running.')
            self.command('stop', '--time', '30', self.platform)

    def _acceleration(self, commercial):
        self.state.update(message='Checking GPU compatibility inside Docker')
        acceleration = detect(self.workspace.docker, self.resources,
                              disabled=preferences(self.workspace.root) == 'cpu')
        acceleration['preference'] = preferences(self.workspace.root)
        acceleration['gpu_available'] = acceleration['mode'] == 'cuda'
        acceleration['gpu_installed'] = True
        try:
            expected = json.loads((self.resources / 'engine-payload-cuda.json').read_text())
        except (OSError, ValueError):
            expected = {}
        for image in required_images(acceleration):
            present = self.command('image', 'inspect', image, '--format', '{{.Id}}', check=False)
            if present.returncode or (commercial and present.stdout.decode().strip() != expected.get('image_ids', {}).get(image)):
                acceleration['gpu_installed'] = False
                acceleration['opensplat' if image == PLATFORM_CUDA else 'nodeodx'] = 'cpu'
                acceleration['reason'] = 'A compatible GPU was detected. Its verified engines are not installed; CPU processing is available. Open Processing settings to install or resume them.'
                if not commercial:
                    acceleration['reason'] = 'A compatible GPU was detected. CPU processing is ready. Build the GPU engines with scripts/provision-cuda.sh and reopen Desktop; no account is required.'
        acceleration['mode'] = 'cuda' if 'cuda' in (acceleration['opensplat'], acceleration['nodeodx']) else 'cpu'
        return acceleration

    def set_processing(self, preference):
        if preference not in {'auto', 'cpu'}:
            raise ValueError('Choose automatic GPU detection or CPU.')
        if self.state['status'] == 'starting':
            raise ValueError('Wait for the local workspace to finish starting.')
        if self.state['status'] == 'ready' and not self._workspace_idle():
            raise ValueError('Finish the running tasks and transfers before changing processing settings.')
        target = self.workspace.root / 'processing-settings.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps({'acceleration': preference}))
        temporary.replace(target)
        return self.start()

    def _start(self):
        try:
            root = self.workspace.root
            commercial = (self.resources / 'edition-commercial').is_file()
            if commercial:
                try:
                    verify_grant(json.loads((root / 'account-grant.json').read_text()),
                                 json.loads((self.resources / 'license-public.json').read_text()))
                except (OSError, ValueError):
                    raise ValueError('Activate or renew this official installation below, then click Retry.') from None
            try:
                info = self.command('info', '--format', '{{.OSType}}', timeout=45, check=False)
            except FileNotFoundError:
                raise ValueError('Install Docker Desktop, then open MosaicEarth again. Installation help links to the official installer.') from None
            if info.returncode and os.name == 'nt':
                self.state.update(message='Starting Docker Desktop. Your local files are preserved.')
                # Start the official app in the launcher's interactive session.
                # The CLI can report success before its Linux VM is available.
                for variable, suffix in [('LOCALAPPDATA', 'Programs/DockerDesktop/Docker Desktop.exe'),
                                         ('ProgramFiles', 'Docker/Docker/Docker Desktop.exe')]:
                    executable = Path(os.environ.get(variable, '')) / suffix
                    if executable.is_file():
                        subprocess.Popen([str(executable)], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                else:
                    self.command('desktop', 'start', timeout=60, check=False)
                for _ in range(60):
                    info = self.command('info', '--format', '{{.OSType}}', timeout=15, check=False)
                    if not info.returncode:
                        break
                    time.sleep(2)
            if info.returncode or info.stdout.strip() != b'linux':
                raise ValueError('Start Docker Desktop with Linux containers, then click Retry.')
            expected = json.loads((self.resources / 'engine-payload.json').read_text())
            for image in (NODE_IMAGE, PLATFORM_IMAGE):
                present = self.command('image', 'inspect', image, '--format', '{{.Id}}', check=False)
                if present.returncode:
                    raise ValueError('Local engines need installation. Use Install engines below, then Retry.')
                if commercial and present.stdout.decode().strip() != expected['image_ids'].get(image):
                    raise ValueError('This runtime does not match the signed-in installer release. Install its verified engines before continuing.')
            acceleration = self._acceleration(commercial)
            platform_image = PLATFORM_CUDA if acceleration['opensplat'] == 'cuda' else PLATFORM_IMAGE
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
            gpu_args = ['--gpus', 'device=' + str(acceleration['device']['index'])] if acceleration['device'] else []
            node_args = self.workspace.mount(node_data, '/var/www/data')
            node_command = ['--max-concurrency', '1', '--token', token]
            if acceleration['nodeodx'] == 'cuda':
                try:
                    self._container(self.node, NODE_CUDA, [*gpu_args, *node_args], node_command)
                except (ValueError, subprocess.SubprocessError):
                    self._container(self.node, NODE_IMAGE, node_args, node_command)
                    acceleration.update(nodeodx='cpu', reason='NodeODX could not start with GPU. It is running on CPU; local data was preserved.')
            else:
                self._container(self.node, NODE_IMAGE, node_args, node_command)
            # Check/replace NodeODX while the current platform can still report
            # whether its workspace is idle. Stopping it before this check made
            # a switch between CPU and GPU fail closed halfway through.
            self._stop_outdated_platform(platform_image)
            args = ['--cap-drop=ALL', '--security-opt=no-new-privileges',
                    '-p', f'127.0.0.1:{self.port}:{self.port}',
                    '-e', 'MOSAICEARTH_LOCAL_NODEODX=http://' + self.node + ':3000',
                    '-e', 'MOSAICEARTH_HOST_NAME=' + platform.node(),
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
            if acceleration['opensplat'] == 'cuda':
                try:
                    self._container(self.platform, PLATFORM_CUDA,
                                    [*gpu_args, *args, '-e', 'MOSAICEARTH_OPENSPLAT_CPU=0',
                                     '-e', 'MOSAICEARTH_OPENSPLAT_BIN=/opt/opensplat-cuda/bin/opensplat',
                                     '-e', 'MOSAICEARTH_OPENSPLAT_CPU_FALLBACK=/opt/opensplat/bin/opensplat'],
                                    ['--port', str(self.port)])
                except (ValueError, subprocess.SubprocessError):
                    self._container(self.platform, PLATFORM_IMAGE, args, ['--port', str(self.port)])
                    acceleration.update(opensplat='cpu', reason='OpenSplat could not start with GPU. It is running on CPU; local data was preserved.')
            else:
                self._container(self.platform, PLATFORM_IMAGE, args, ['--port', str(self.port)])
            acceleration['mode'] = 'cuda' if 'cuda' in (acceleration['opensplat'], acceleration['nodeodx']) else 'cpu'
            target = root / 'processing-runtime.json'
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(acceleration))
            temporary.replace(target)
            self.state['acceleration'] = acceleration
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
        if self.state['status'] == 'ready' and time.monotonic() - self.last_health_check >= 5:
            self.last_health_check = time.monotonic()
            try:
                with urllib.request.urlopen(self.url + '/api/desktop/version', timeout=3) as response:
                    if not json.load(response).get('desktop_local'):
                        raise ValueError('Local runtime unavailable')
            except (OSError, ValueError):
                self.state.update(status='failed', message='The local engines are not responding. Click Retry to restart Docker and reopen your saved workspace.')
        with self.lock:
            return dict(self.state)
