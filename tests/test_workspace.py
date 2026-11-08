import sys
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mosaicearth_desktop import Workspace, docker_command
from mosaicearth_sync import build_manifest, checksum, Client, SyncError, extract_products


class WorkspaceTest(unittest.TestCase):
    def test_unexpected_transfer_error_does_not_leave_permanent_running_state(self):
        import threading
        with tempfile.TemporaryDirectory() as temp:
            workspace = Workspace(Path(temp))
            threads = []
            thread_class = threading.Thread
            def thread(*args, **kwargs):
                instance = thread_class(*args, **kwargs)
                threads.append(instance)
                return instance
            def fails(progress, canceled):
                raise RuntimeError('unexpected fixture failure')
            with patch('mosaicearth_desktop.threading.Thread', side_effect=thread), patch('logging.Logger.exception'):
                workspace.start_transfer('fixture', fails)
                threads[0].join(3)
            self.assertFalse(threads[0].is_alive())
            self.assertEqual(workspace.transfers['fixture']['status'], 'failed')

    def test_range_download_recovers_without_accepting_partial_bytes(self):
        client = Client()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 206
        response.headers = {'Content-Range': 'bytes 4-7/8'}
        response.read.side_effect = [b'ab', b'abcd']
        with patch.object(client, 'request', return_value=response) as request, patch('mosaicearth_sync.time.sleep'):
            self.assertEqual(client.download_range('/bundle', 4, 7, 8), b'abcd')
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(c.kwargs['headers']['Range'] == 'bytes=4-7' for c in request.call_args_list))

    def test_range_download_rejects_wrong_offset_and_honors_pause(self):
        client = Client()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 206
        response.headers = {'Content-Range': 'bytes 0-3/8'}
        with patch.object(client, 'request', return_value=response):
            with self.assertRaisesRegex(SyncError, 'requested file range'):
                client.download_range('/bundle', 4, 7, 8)
        client.canceled = lambda: True
        with patch.object(client, 'request') as request:
            with self.assertRaisesRegex(SyncError, 'paused'):
                client.download_range('/bundle', 4, 7, 8)
            request.assert_not_called()

    def test_snapshot_pull_resumes_saved_bytes_and_retries_body_timeout(self):
        import hashlib
        client = Client()
        payload = b'abcdefghij'
        state = {'status': 'COMPLETE', 'bytes': len(payload), 'manifest': {'files': [
            {'path': 'models/model.bin', 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}]}}
        responses = []
        def request(method, path, **kwargs):
            if not kwargs.get('raw'):
                return state
            start, end = map(int, kwargs['headers']['Range'][6:].split('-'))
            responses.append((start, end))
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 206
            response.headers = {'Content-Range': f'bytes {start}-{end}/{len(payload)}'}
            response.read.side_effect = TimeoutError('read timed out') if len(responses) == 2 else None
            response.read.return_value = payload[start:end+1]
            return response
        with tempfile.TemporaryDirectory() as temp, patch.object(client, 'request', side_effect=request), \
                patch('mosaicearth_sync.CHUNK', 4), patch('mosaicearth_sync.time.sleep'):
            partial = Path(temp) / 'models/model.bin.mosaicearth-part'
            partial.parent.mkdir()
            partial.write_bytes(payload[:2])
            client.pull('fixture', temp)
            self.assertEqual((Path(temp) / 'models/model.bin').read_bytes(), payload)
            self.assertFalse(partial.exists())
            self.assertEqual(responses, [(2,5),(6,9),(6,9)])
            client.pull('fixture', temp)
            self.assertEqual(len(responses), 3, 'Completed files must not download again')

    def test_snapshot_parallel_files_preserve_hashes_and_monotonic_progress(self):
        import hashlib, threading, time
        body = b'fixture data'
        files = [{'path': f'tiles/{i}.bin', 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for i in range(12)]
        progress = []
        client = Client(progress=lambda done, total, message: progress.append(done))
        active = peak = 0
        lock = threading.Lock()
        def receive(*args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.01)
            with lock:
                active -= 1
            return body
        with tempfile.TemporaryDirectory() as temp, patch.object(client, 'request', return_value={
                'status': 'COMPLETE', 'bytes': len(body)*len(files), 'manifest': {'files': files}}), \
                patch.object(client, 'download_range', side_effect=receive):
            client.pull('fixture', temp)
            self.assertTrue(1 < peak <= 4)
            self.assertEqual(progress, sorted(progress))
            self.assertEqual(progress[-1], len(body)*len(files))
            for f in files:
                self.assertEqual((Path(temp)/f['path']).read_bytes(), body)

    def test_windows_docker_installed_after_session_started(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / 'Programs/DockerDesktop/resources/bin/docker.exe'
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.dict('os.environ', {'LOCALAPPDATA': temp}, clear=True), \
                    patch('mosaicearth_desktop.sys.platform', 'win32'), \
                    patch('mosaicearth_desktop.shutil.which', return_value=None):
                self.assertEqual(docker_command(), [str(executable)])

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
