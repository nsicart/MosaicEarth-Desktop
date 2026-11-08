import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('release_signing', Path(__file__).parents[1] / 'scripts/sign-release.py')
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


class ManifestVerification(unittest.TestCase):
    def test_valid_then_modified_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / 'installer.deb'
            artifact.write_bytes(b'verified release')
            manifest = root / 'SHA256SUMS'
            manifest.write_text(hashlib.sha256(artifact.read_bytes()).hexdigest() + '  installer.deb\n')
            self.assertEqual(signing.release_files(root, manifest), [artifact])
            artifact.write_bytes(b'modified release')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                signing.release_files(root, manifest)

    def test_unsafe_and_duplicate_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / 'installer.deb'
            artifact.write_bytes(b'fixture')
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            manifest = root / 'SHA256SUMS'
            for text in [f'{digest}  ../installer.deb\n', f'{digest}  installer.deb\n' * 2, '']:
                manifest.write_text(text)
                with self.assertRaises(ValueError):
                    signing.release_files(root, manifest)
            link = root / 'linked.deb'
            link.symlink_to(artifact)
            manifest.write_text(f'{digest}  linked.deb\n')
            with self.assertRaises(ValueError):
                signing.release_files(root, manifest)


if __name__ == '__main__':
    unittest.main()
