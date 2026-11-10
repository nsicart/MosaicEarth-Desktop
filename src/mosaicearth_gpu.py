"""Automatic local acceleration. A physical GPU alone does not select CUDA."""
import json
import subprocess
import uuid
from pathlib import Path

PLATFORM_CPU = 'mosaicearth-desktop/platform:0.4.4'
PLATFORM_CUDA = 'mosaicearth-desktop/platform:0.4.4-cuda'
NODE_CPU = 'mosaicearth-desktop/nodeodx:0.1.0'
NODE_CUDA = 'mosaicearth-desktop/nodeodx:0.4.0-cuda'


def detect(docker, resources, *, disabled=False):
    state = {'mode': 'cpu', 'opensplat': 'cpu', 'nodeodx': 'cpu', 'device': None,
             'reason': 'No compatible NVIDIA GPU is available through Docker. Processing uses CPU.'}
    if disabled:
        state['reason'] = 'CPU processing selected in Desktop settings.'
        return state
    name = 'mosaicearth-gpu-probe-' + uuid.uuid4().hex
    try:
        script = (Path(resources) / 'gpu-probe.py').read_text()
        result = subprocess.run([*docker, 'run', '--rm', '--pull=never', '--name', name,
                                 '--network=none', '--read-only', '--cap-drop=ALL',
                                 '--security-opt=no-new-privileges', '--gpus', 'all',
                                 '--entrypoint', 'python', PLATFORM_CPU, '-c', script],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
        if result.returncode:
            return state
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get('devices'), list):
            return state
        devices = [device for device in data.get('devices', [])
                   if 60 <= int(device['capability']) <= 89 and int(device['memory_mib']) >= 2048]
        if not devices or int(data.get('driver_cuda', 0)) < 11080:
            return state
        state.update(mode='cuda', opensplat='cuda', device=max(devices, key=lambda d: d['memory_mib']),
                     reason='GPU computation verified inside Docker. CPU remains available as a fallback.')
        if int(data['driver_cuda']) >= 12090:
            state['nodeodx'] = 'cuda'
        else:
            state['reason'] = 'OpenSplat uses GPU. NodeODX uses CPU because its GPU engine needs a newer NVIDIA driver.'
        return state
    except subprocess.TimeoutExpired:
        state['reason'] = 'GPU verification timed out while Docker was busy. This launch uses CPU; retry Automatic in Processing settings when Docker is ready.'
        return state
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return state
    finally:
        # A timed-out Docker client can leave its disposable probe running.
        try:
            subprocess.run([*docker, 'rm', '-f', name], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass


def preferences(root):
    try:
        value = json.loads((Path(root) / 'processing-settings.json').read_text()).get('acceleration')
        return value if value in {'auto', 'cpu'} else 'auto'
    except (OSError, ValueError, AttributeError):
        return 'auto'


def required_images(state):
    images = []
    if state['opensplat'] == 'cuda':
        images.append(PLATFORM_CUDA)
    if state['nodeodx'] == 'cuda':
        images.append(NODE_CUDA)
    return images
