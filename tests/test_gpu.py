"""GPU selection must fail safely without hiding data or stopping active work."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import mosaicearth_gpu as gpu
from mosaicearth_launcher import PlatformLauncher

RESOURCES = Path(__file__).resolve().parents[1] / 'resources'


class GPUSelectionTest(unittest.TestCase):
    def probe(self, payload=None, *, returncode=0, exception=None):
        result = SimpleNamespace(returncode=returncode, stdout=json.dumps(payload).encode())
        with patch.object(gpu.subprocess, 'run', side_effect=[exception or result, result]) as run:
            state = gpu.detect(['docker'], RESOURCES)
        self.assertEqual(run.call_args.args[0][1:3], ['rm', '-f'])
        self.assertNotIn('--mount', run.call_args_list[0].args[0])
        self.assertIn('--network=none', run.call_args_list[0].args[0])
        return state

    @staticmethod
    def device(capability=86, index=0, memory=8192):
        return {'index': index, 'name': 'NVIDIA test device', 'capability': capability, 'memory_mib': memory}

    def test_no_gpu_missing_runtime_timeout_and_malformed_output_use_cpu(self):
        for payload, code, exception in [({'devices': []}, 0, None), ({}, 125, None),
                                         ({}, 0, subprocess.TimeoutExpired('probe', 90)),
                                         (None, 0, None), ({'devices': [{}]}, 0, None)]:
            with self.subTest(payload=payload, code=code, exception=exception):
                state = self.probe(payload, returncode=code, exception=exception)
                self.assertEqual(state['mode'], 'cpu')
                self.assertEqual(gpu.required_images(state), [])

    def test_unsupported_architecture_or_old_driver_uses_cpu(self):
        for capability, driver, memory in [(52, 13000, 8192), (120, 13000, 8192),
                                           (86, 11040, 8192), (86, 13000, 1024)]:
            with self.subTest(capability=capability, driver=driver, memory=memory):
                state = self.probe({'driver_cuda': driver, 'devices': [self.device(capability, memory=memory)]})
                self.assertEqual(state['mode'], 'cpu')

    def test_engine_driver_requirements_are_independent(self):
        state = self.probe({'driver_cuda': 12020, 'devices': [self.device()]})
        self.assertEqual(state['opensplat'], 'cuda')
        self.assertEqual(state['nodeodx'], 'cpu')
        self.assertEqual(gpu.required_images(state), [gpu.PLATFORM_CUDA])

    def test_compatible_devices_select_largest_memory(self):
        state = self.probe({'driver_cuda': 13000,
                            'devices': [self.device(61, 0, 6144), self.device(86, 1, 8192)]})
        self.assertEqual(state['device']['index'], 1)
        self.assertEqual(gpu.required_images(state), [gpu.PLATFORM_CUDA, gpu.NODE_CUDA])

    def test_cpu_preference_does_not_start_a_gpu_container(self):
        with patch.object(gpu.subprocess, 'run') as run:
            self.assertEqual(gpu.detect(['docker'], RESOURCES, disabled=True)['mode'], 'cpu')
        run.assert_not_called()

    def test_busy_workspace_rejects_settings_without_writing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = PlatformLauncher(SimpleNamespace(root=root), RESOURCES, 19757)
            launcher.state['status'] = 'ready'
            with patch.object(launcher, '_workspace_idle', return_value=False), patch.object(launcher, 'start') as start:
                with self.assertRaisesRegex(ValueError, 'Finish the running tasks'):
                    launcher.set_processing('cpu')
                self.assertFalse((root / 'processing-settings.json').exists())
                start.assert_not_called()

    def test_idle_workspace_persists_cpu_and_rechecks_on_next_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = PlatformLauncher(SimpleNamespace(root=root), RESOURCES, 19757)
            with patch.object(launcher, 'start') as start:
                launcher.set_processing('cpu')
                self.assertEqual(gpu.preferences(root), 'cpu')
                launcher.set_processing('auto')
                self.assertEqual(gpu.preferences(root), 'auto')
                self.assertEqual(start.call_count, 2)


if __name__ == '__main__':
    unittest.main()
