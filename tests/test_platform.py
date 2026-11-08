"""Data isolation and offline-shell checks for the shared Desktop interface."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, 'Install platform/requirements.txt for shared-platform tests')
class PlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mosaicearth_platform import create_app
        cls.directory = tempfile.TemporaryDirectory()
        cls.app = create_app(Path(cls.directory.name), 19757)
        cls.client = TestClient(cls.app, base_url='http://127.0.0.1:19757')
        import mosaicearth_platform_context
        from homelab.db_store import get_store
        cls.context = mosaicearth_platform_context
        cls.store = get_store()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.directory.cleanup()

    def test_local_owner_remains_independent_of_cloud_login(self):
        self.assertEqual(self.context.local_user()['ID'], 1)
        self.context.workspace.cloud_session = {'cloud_uid': 999, 'user': 'fixture@example.invalid', 'token': 'fixture'}
        try:
            self.assertEqual(self.context.local_user()['ID'], 1)
            self.assertEqual(self.context.cloud_account()['cloud_uid'], 999)
            self.assertNotIn('token', self.context.cloud_account())
        finally:
            self.context.workspace.cloud_session = None
        self.assertEqual(self.context.local_user()['ID'], 1)

    def test_public_catalog_cannot_turn_public_projects_into_local_owned_projects(self):
        pid = self.store.create_project(1, 'Offline project', is_project_container=True)
        self.assertGreater(pid, 1_000_000_000_000)
        payload = {'dbo.WEB_PROJECTS': [{'ID': 123, 'CREATE_ID': 1, 'NAME': 'Public fixture', 'ACTIVO': True}]}
        with patch.object(self.context, 'Client') as client:
            client.return_value.request.return_value = payload
            self.assertEqual(self.context.refresh_public_catalog()['state'], 'ready')
        public = self.store.get_project(123)
        self.assertNotEqual(public['CREATE_ID'], 1)
        self.assertTrue(public['DESKTOP_PUBLIC_CATALOG'])
        self.assertEqual(self.store.get_project(pid)['CREATE_ID'], 1)

    def test_local_requests_reject_external_host_and_browser_origins(self):
        response = self.client.get('/api/desktop/version', headers={'Host': 'attacker.invalid'})
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/desktop/cloud-logout', headers={'Origin': 'https://attacker.invalid'})
        self.assertEqual(response.status_code, 403)

    def test_shared_navigation_is_available_without_a_cloud_account(self):
        for route in ('/en/Projects', '/en/MyMaps', '/en/Map'):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200, route)
            self.assertIn('MyEarth', response.text)
            self.assertIn('MyMosaics', response.text)
            self.assertNotIn('https://cesium.com/downloads', response.text)
            self.assertNotIn('https://cdnjs.cloudflare.com/ajax/libs/font-awesome', response.text)
        origin = self.client.get('/api/desktop/share-origin').json()
        self.assertTrue(origin['local_only'])
        self.assertEqual(origin['shareOrigin'], 'http://127.0.0.1:19757')

    def test_fly_loads_short_external_ids_and_protects_private_metadata(self):
        pid = self.store.create_project(1, 'Private Fly project', kind='external')
        aid = 999001
        row = {'ID': aid, 'PROJECT_ID': pid, 'USER_ID': 1, 'TITLE': 'Private Fly Gaussian',
               'VISIBILITY': 'private', 'IS_PUBLIC': 0, 'STATUS': 'private-ready',
               'VIEWER_KIND': 'gaussian-splat', 'LATITUDE': 46.8, 'LONGITUDE': -91.9}
        self.store.tables['dbo.MOSAICEARTH_EXTERNAL_ASSETS'].append(row)
        self.store.persist_table('dbo.MOSAICEARTH_EXTERNAL_ASSETS')
        for identifier in (f'external-{aid}', f'external-3-{aid}'):
            response = self.client.get('/Fly.aspx', params={'asset_id': identifier})
            self.assertEqual(response.status_code, 200)
            self.assertIn('Private Fly Gaussian', response.text)
            self.assertIn(f'/ExternalAsset3DTile.ashx/{pid}/{aid}/tileset.json', response.text)
            for user, expected in ((None, 401), ({'ID': 999, 'ROL_ID': 2}, 403)):
                with patch('homelab.app.get_current_user', return_value=user):
                    denied = self.client.get('/Fly.aspx', params={'asset_id': identifier})
                    self.assertEqual(denied.status_code, expected)
                    self.assertNotIn('Private Fly Gaussian', denied.text)

    def test_local_gaussian_photos_queue_without_cloud_billing(self):
        import hashlib
        import io
        import uuid
        from PIL import Image
        self.context.local_user()
        pid = self.store.create_project(1, 'Local Gaussian fixture', kind='gaussian', creation_date='2016-06-23')
        buffer = io.BytesIO()
        Image.new('RGB', (64, 48), '#223344').save(buffer, format='JPEG')
        photo = buffer.getvalue()
        files = [{'name': f'view-{index}.jpg', 'size': len(photo)} for index in range(4)]
        route = f'/api/v2/tasks/{pid}/asset-uploads/{uuid.uuid4()}'
        queue = self.store.get_job_queue()
        with patch('shutil.which', return_value='/installed-engine'), \
             patch.object(queue, 'ensure_workers_started'), \
             patch.object(self.store, 'authorize_and_charge_task_upfront', side_effect=AssertionError('Local processing must not call billing')):
            quote = self.client.get(f'/api/v2/tasks/{pid}/asset-uploads/quote').json()
            self.assertTrue(quote['local'])
            self.assertTrue(quote['engine_available'])
            self.assertEqual(quote['price_eur'], 0)
            response = self.client.put(route, json={'origin': 'gaussian', 'files': files, 'details': {'quality_preset': 'FAST'}})
            self.assertEqual(response.status_code, 200, response.text)
            for index in range(4):
                response = self.client.put(f'{route}/files/{index}?offset=0', content=photo,
                    headers={'X-Content-SHA256': hashlib.sha256(photo).hexdigest()})
                self.assertEqual(response.status_code, 200, response.text)
            response = self.client.post(route + '/finish')
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(response.json()['status'], 'QUEUED')
            self.assertFalse(response.json().get('checkout_url'))
        self.assertFalse(self.store.get_project(pid).get('PAYMENT_STATUS'))


if __name__ == '__main__':
    unittest.main()
