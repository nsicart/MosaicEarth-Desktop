import sys
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mosaicearth_desktop import Workspace
from mosaicearth_sync import build_manifest, checksum, Client, SyncError, extract_products


class WorkspaceTest(unittest.TestCase):
    def test_unavailable_engine_preserves_completed_result(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(Path(temp))
            pid = workspace.create('Existing completed project')
            with workspace.db() as db:
                db.execute("UPDATE project SET state='complete',mode='odx',result='previous' WHERE id=?", (pid,))
            before = workspace.project(pid)
            for responses, message in [
                ([subprocess.CompletedProcess([], 1, b'')], 'Start Docker'),
                ([subprocess.CompletedProcess([], 0, b'linux\n'), subprocess.CompletedProcess([], 1, b'')], 'not installed'),
            ]:
                with patch('mosaicearth_desktop.subprocess.run', side_effect=responses):
                    with self.assertRaisesRegex(ValueError, message):
                        workspace.run(pid, 'odx')
                self.assertEqual(workspace.project(pid), before)

    def test_local_workspace_does_not_need_account(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(Path(temp))
            project = workspace.create('Offline project')
            self.assertEqual(Workspace(Path(temp)).project(project)['name'], 'Offline project')
            self.assertIsNone(workspace.cloud_session)
            with self.assertRaises(ValueError):
                workspace.sync_project(project)
            with self.assertRaises(ValueError):
                workspace.project('../private')

    def test_manifest_preserves_names_hashes_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'images').mkdir()
            photo = root / 'images' / 'Foto original.jpg'
            photo.write_bytes(b'byte-preservation fixture')
            product = root / 'map.geojson'
            product.write_text('{"type":"FeatureCollection","features":[]}')
            manifest, files = build_manifest(root, 'fixture:project', 'Offline project', {'crs': 'EPSG:4326'})
            self.assertEqual(manifest['metadata']['crs'], 'EPSG:4326')
            self.assertEqual({f['path'] for f in manifest['files']}, {'images/Foto original.jpg', 'map.geojson'})
            for item in manifest['files']:
                self.assertEqual(item['sha256'], checksum(files[item['path']]))
            self.assertEqual(manifest['files'][0]['role'], 'source')
            self.assertEqual(manifest['files'][1]['role'], 'product')

    def test_cloud_transport_requires_tls(self):
        with self.assertRaises(SyncError):
            Client(origin='http://example.com')
        with self.assertRaises(SyncError):
            Client(origin='https://username:password@example.com')
        self.assertEqual(Client(origin='http://127.0.0.1:1234').origin, 'http://127.0.0.1:1234')

    def test_result_extraction_preserves_edited_local_files(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with zipfile.ZipFile(root / 'all.zip', 'w') as archive:
                archive.writestr('result.txt', b'original')
            extract_products(root / 'all.zip', root / 'products')
            extract_products(root / 'all.zip', root / 'products')
            target = root / 'products/result.txt'
            target.write_bytes(b'my edits')
            with self.assertRaises(SyncError):
                extract_products(root / 'all.zip', root / 'products')
            self.assertEqual(target.read_bytes(), b'my edits')


if __name__ == '__main__':
    unittest.main()
