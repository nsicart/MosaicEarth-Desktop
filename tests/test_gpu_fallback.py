"""Fault injection: only GPU execution faults retry a real CPU reconstruction."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'platform'))
from homelab.opensplat_engine import OpenSplatAdapter, train_with_cpu_fallback


class GPUFallbackTest(unittest.TestCase):
    def test_cuda_failure_preserves_log_and_inputs_and_uses_cpu_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary, cpu, photo = root / 'cuda', root / 'cpu', root / 'input.jpg'
            for file in (binary, cpu, photo):
                file.write_bytes(b'original')
            modes = []
            def train(adapter, project, output, *args, **kwargs):
                modes.append(adapter.cpu)
                output.mkdir()
                (output / 'opensplat.log').write_text('Using CPU' if adapter.cpu else 'CUDA out of memory')
                if not adapter.cpu:
                    (output / 'partial.ply').write_bytes(b'incomplete')
                    raise RuntimeError('OpenSplat exited with code 1')
                self.assertFalse((output / 'partial.ply').exists())
                return output / 'model.ply', [], {'device': 'CPU'}
            with patch.object(OpenSplatAdapter, 'train', train):
                _, _, result = train_with_cpu_fallback(binary, root, root / 'training', 'FAST', [], fallback=str(cpu))
            self.assertEqual(modes, [False, True])
            self.assertEqual(result['device'], 'CPU')
            self.assertIn('gpu_fallback_reason', result)
            self.assertEqual(photo.read_bytes(), b'original')
            self.assertEqual((root / 'training/opensplat-gpu-failed.log').read_text(), 'CUDA out of memory')

    def test_bad_inputs_and_cancellation_are_not_retried(self):
        for message, canceled in [('Invalid image dimensions', False), ('CUDA error during canceled task', True)]:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / 'binary'; binary.touch()
                def train(adapter, project, output, *args, **kwargs):
                    output.mkdir();(output / 'opensplat.log').write_text(message)
                    raise RuntimeError(message)
                with patch.object(OpenSplatAdapter, 'train', autospec=True, side_effect=train) as run:
                    with self.assertRaisesRegex(RuntimeError, message):
                        train_with_cpu_fallback(binary, root, root / 'output', 'FAST', [],
                                                fallback=str(binary), cancelled=lambda: canceled)
                self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
