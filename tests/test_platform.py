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


if __name__ == '__main__':
    unittest.main()
