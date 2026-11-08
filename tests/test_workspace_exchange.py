import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'platform'))
try:
    from homelab.db_store import MosaicEarthDataStore
    from homelab.blob_store import LocalBlobStore
except ImportError:
    MosaicEarthDataStore = None
from homelab.workspace_exchange import export_workspace, import_workspace, PROJECTS, ASSETS, MAPS, digest


@unittest.skipIf(MosaicEarthDataStore is None, 'Install platform/requirements.txt')
class WorkspaceExchangeTest(unittest.TestCase):
    def setUp(self):
        os.environ['MOSAICEARTH_DESKTOP_LOCAL'] = '1'
        os.environ['MOSAICEARTH_STORAGE_MODE'] = 'local_only'
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = MosaicEarthDataStore(str(self.root / 'source'))
        self.dest = MosaicEarthDataStore(str(self.root / 'destination'))
        self.blobs = LocalBlobStore(str(self.root / 'source'))
        self.target = LocalBlobStore(str(self.root / 'destination'))
        pid = self.source.create_project(1, 'Project català', is_project_container=True)
        survey = self.source.create_project(1, 'Survey', parent_project_id=pid, is_survey_container=True)
        self.task = self.source.create_project(1, 'Finished task', kind='external', parent_project_id=pid, survey_id=survey)
        folder = self.source.get_project(self.task)['FOLDER']
        self.blobs.save_bytes('imagenes', folder + '/photo.jpg', b'original photo fixture')
        self.source.add_project_file(self.task, 1, 'photo.jpg', 22, capture_date='2016-06-23T16:31:59')
        source_path = f'external/{self.task}/11/source/map.geojson'
        preview_path = f'external/{self.task}/11/overlay.geojson'
        self.blobs.save_bytes('assets', source_path, b'{"type":"FeatureCollection","features":[]}')
        self.blobs.save_bytes('results', preview_path, b'{"type":"FeatureCollection","features":[]}')
        self.source.tables[ASSETS] = [{'ID': 11, 'PROJECT_ID': self.task, 'USER_ID': 1,
            'TITLE': 'Elevation / vector fixture', 'STATUS': 'private-ready', 'VISIBILITY': 'private',
            'STORAGE_RELATIVE_PATH': source_path, 'PREVIEW_RELATIVE_PATH': preview_path,
            'LATITUDE': 41.3, 'LONGITUDE': 1.8, 'CRS': 'EPSG:4326', 'HEADING': 32.5}]
        self.source.tables[MAPS] = [{'ID': 21, 'CREATE_ID': 1, 'NAME': 'MyMosaic català', 'VISIBILITY': 'public'}]
        self.source.tables['dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS'] = [{'MYMAP_ID': 21, 'ASSET_ID': 11}]
        for table in (ASSETS, MAPS, 'dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS'):
            self.source.persist_table(table)
        self.manifest, self.paths = export_workspace(self.source, self.blobs, 1, 'mymosaic', 21, self.root / 'export')

    def tearDown(self):
        self.temp.cleanup()

    def test_full_scene_roundtrip_and_idempotency(self):
        before = {key: digest(path) for key, path in self.paths.items()}
        result = import_workspace(self.dest, self.target, 9, 'a' * 64, self.paths)
        self.assertEqual(result['projects'], 3)
        self.assertEqual(result['assets'], 1)
        self.assertEqual(result['files'], 3)
        imported = self.dest.get_mymap(str(result['id']))
        self.assertEqual(imported['CREATE_ID'], 9)
        self.assertEqual(imported['VISIBILITY'], 'private')
        asset = next(a for a in self.dest.tables[ASSETS] if a.get('IMPORT_REVISION'))
        self.assertEqual(asset['CRS'], 'EPSG:4326')
        self.assertEqual(asset['HEADING'], 32.5)
        self.assertEqual(digest(self.target.resolve_path('assets', asset['STORAGE_RELATIVE_PATH'])), before['blobs/assets/' + self.source.tables[ASSETS][0]['STORAGE_RELATIVE_PATH']])
        membership = self.dest.tables['dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS'][0]
        self.assertEqual(membership['MYMAP_ID'], imported['ID'])
        self.assertEqual(membership['ASSET_ID'], asset['ID'])
        projects = [p for p in self.dest.tables[PROJECTS] if p.get('IMPORT_REVISION')]
        self.assertTrue(all(p['CREATE_ID'] == 9 for p in projects))
        task = next(p for p in projects if p['NAME'] == 'Finished task')
        self.assertTrue(any(p['ID'] == task['SURVEY_ID'] and p['IS_SURVEY_CONTAINER'] for p in projects))
        self.assertEqual(self.dest.get_project_files(task['ID'])[0]['CAPTURE_DATE'], '2016-06-23T16:31:59')
        self.assertEqual(import_workspace(self.dest, self.target, 9, 'a' * 64, self.paths), result)
        self.assertEqual({key: digest(path) for key, path in self.paths.items()}, before)

    def test_credentials_or_cross_workspace_paths_are_rejected(self):
        document = self.paths['workspace.json']
        original = document.read_text()
        package = json.loads(original)
        package['tables'][PROJECTS][0]['CUSTOM_NODEODM_TOKEN'] = 'must-not-import'
        document.write_text(json.dumps(package))
        with self.assertRaisesRegex(ValueError, 'Unexpected workspace fields'):
            import_workspace(self.dest, self.target, 9, 'b' * 64, self.paths)
        package = json.loads(original)
        package['tables'][ASSETS][0]['STORAGE_RELATIVE_PATH'] = '../another-account/private.tif'
        document.write_text(json.dumps(package))
        with self.assertRaisesRegex(ValueError, 'Unsafe workspace file path'):
            import_workspace(self.dest, self.target, 9, 'c' * 64, self.paths)
        self.assertFalse(self.dest.tables[ASSETS])

    def test_export_cannot_read_another_owners_scene(self):
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            export_workspace(self.source, self.blobs, 2, 'mymosaic', 21, self.root / 'unauthorized')

    def test_legacy_migration_preserves_originals_and_never_restarts_old_tasks(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
        from mosaicearth_migration import prepare_legacy, migrate_legacy, legacy_status
        row = next(p for p in self.source.tables[PROJECTS] if p['ID'] == self.task)
        row.update(STATE_ID=2, NODEODM_UUID='old-task-must-never-run')
        self.source.persist_table(PROJECTS)
        target = self.root / 'migration'
        target.mkdir()
        with patch.dict(os.environ, {'MOSAICEARTH_LEGACY_WORKSPACE': str(self.root / 'source')}):
            source_blobs = prepare_legacy(target)
        self.assertEqual(legacy_status(target)['unfinished'], 1)
        before = {str(path): digest(path) for path in (self.root / 'source/blobs').rglob('*') if path.is_file()}
        result = migrate_legacy(target, source_blobs, self.dest, self.target, lambda *args: None, lambda: False)
        self.assertEqual(result['unfinished_tasks'], 1)
        self.assertTrue(result['original_preserved'])
        migrated = next(p for p in self.dest.tables[PROJECTS] if p.get('NAME') == 'Finished task')
        self.assertEqual(migrated['STATE_ID'], 3)
        self.assertFalse(migrated.get('NODEODM_UUID'))
        self.assertFalse(migrated.get('ZIP_UPLOADED'))
        self.assertEqual(self.source.get_project(self.task)['STATE_ID'], 2)
        self.assertEqual(len(self.dest.tables[MAPS]), 1)
        self.assertEqual(result, migrate_legacy(target, source_blobs, self.dest, self.target, lambda *args: None, lambda: False))
        self.assertEqual(before, {str(path): digest(path) for path in (self.root / 'source/blobs').rglob('*') if path.is_file()})

    def test_display_references_cannot_read_another_accounts_files(self):
        document = self.paths['workspace.json']
        original = document.read_text()
        for field, value in [('SOURCE_BLOB_NAME', 'external/99/88/private.tif'),
                             ('DISPLAY_BLOB_PREFIX', 'external/99/88'),
                             ('DERIVATIVES_MANIFEST', [{'path': 'external/99/88/private.tif'}])]:
            with self.subTest(field=field):
                package = json.loads(original)
                package['tables'][ASSETS][0][field] = value
                document.write_text(json.dumps(package))
                with self.assertRaisesRegex(ValueError, 'missing'):
                    import_workspace(self.dest, self.target, 9, 'd' * 64, self.paths)
                self.assertFalse(self.dest.tables[ASSETS])

    def test_interrupted_file_copy_resumes_without_publishing_partial_data(self):
        import shutil
        real_copy = shutil.copyfile
        calls = []
        def interrupted(source, target):
            calls.append(str(target))
            Path(target).write_bytes(b'partial')
            raise OSError('Simulated disk interruption')
        with patch('homelab.workspace_exchange.shutil.copyfile', side_effect=interrupted):
            with self.assertRaises(OSError):
                import_workspace(self.dest, self.target, 9, 'e' * 64, self.paths)
        self.assertTrue(calls)
        self.assertTrue(all(not p.get('ACTIVO') for p in self.dest.tables[PROJECTS]))
        result = import_workspace(self.dest, self.target, 9, 'e' * 64, self.paths)
        self.assertEqual(result['files'], 3)
        self.assertEqual(len([p for p in self.dest.tables[PROJECTS] if p.get('ACTIVO')]), 3)

    def test_snapshot_transport_imports_and_exports_the_full_scene(self):
        from homelab.connector_sync import SnapshotStore
        import hashlib
        sync = SnapshotStore(self.dest, self.target, 9)
        state = sync.start(self.manifest)
        for index, item in enumerate(state['manifest']['files']):
            content = self.paths[item['path']].read_bytes()
            sync.append(state['id'], index, 0, content, hashlib.sha256(content).hexdigest())
        with patch.object(self.dest.get_job_queue(), 'ensure_workers_started'):
            sync.commit(state['id'])
        finished = sync.finalize(state['id'], lambda *args: None)
        self.assertEqual(finished['status'], 'COMPLETE')
        mosaic_id = finished['workspace_result']['id']
        self.assertIsNone(self.dest.get_project(finished['project_id']))
        with patch.object(self.dest.get_job_queue(), 'ensure_workers_started'):
            export = sync.export_workspace('mymosaic', mosaic_id)
        exported = sync.finalize_export(export['id'], lambda *args: None)
        self.assertEqual(exported['status'], 'COMPLETE')
        original_hashes = sorted(item['sha256'] for item in self.manifest['files'] if item['path'] != 'workspace.json')
        exported_hashes = sorted(item['sha256'] for item in exported['manifest']['files'] if item['path'] != 'workspace.json')
        self.assertEqual(exported_hashes, original_hashes)


if __name__ == '__main__':
    unittest.main()
