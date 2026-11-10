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

    def test_indexing_stays_disabled_even_with_operator_configuration(self):
        from homelab import seo_engine
        with patch.dict(os.environ, {'MOSAICEARTH_INDEXNOW_KEY': 'fixture-indexing-key'}), patch.object(
                seo_engine, 'DEFAULT_INDEXNOW_KEY', 'fixture-indexing-key'):
            self.assertEqual(self.client.get('/fixture-indexing-key.txt').status_code, 404)
            result = self.client.post('/api/seo/ping-indexnow', json={'dryRun': False}).json()
            self.assertFalse(result['ok'])
            self.assertEqual(result['submitted'], 0)

    def test_cloud_billing_opens_canonical_host_and_local_payouts_stay_disabled(self):
        response = self.client.get('/en/CreditPacks', follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], 'https://app.mosaicearth.app/en/CreditPacks')
        with patch.dict(os.environ, {'MOSAICEARTH_CONNECT_ENABLED': '1'}):
            response = self.client.post('/api/v2/wallet/request-payout', json={})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['code'], 'connect_unavailable')

    def test_development_origins_are_not_trusted_by_interoperability(self):
        from homelab.interoperability import resolve_cors_origin
        from starlette.requests import Request
        for origin in ['http://192.168.0.143:8090', 'https://fixture.trycloudflare.com']:
            request = Request({'type': 'http', 'headers': [(b'origin', origin.encode())]})
            self.assertNotEqual(resolve_cors_origin(request), origin)

    def test_photogrammetry_finalization_keeps_status_reads_responsive(self):
        import concurrent.futures
        import threading
        from homelab.processing_architecture import PhotogrammetryResult

        for slow_stage in ('products', 'elevation'):
            with self.subTest(stage=slow_stage):
                pid = self.store.create_project(1, 'Concurrent finalization ' + slow_stage)
                with self.store.lock:
                    row = next(p for p in self.store.tables['dbo.WEB_PROJECTS'] if p['ID'] == pid)
                    row.update(STATE_ID=2, NODEODM_UUID='fixture-' + slow_stage)
                    self.store.persist_table('dbo.WEB_PROJECTS')
                entered, release = threading.Event(), threading.Event()
                result = PhotogrammetryResult(project_id=pid, folder=str(pid))

                def products(**kwargs):
                    if slow_stage == 'products':
                        entered.set()
                        if not release.wait(10):
                            raise TimeoutError('test release')
                    return result

                def elevation(*args):
                    if slow_stage == 'elevation':
                        entered.set()
                        if not release.wait(10):
                            raise TimeoutError('test release')
                    return []

                with patch('homelab.processing_architecture.ODMAdapter.ingest', side_effect=products), \
                     patch('homelab.processing_architecture.register_dsm_dtm_assets_from_result', side_effect=elevation), \
                     patch.object(self.store, '_build_quality_report_pdf', return_value=b'%PDF-fixture'), \
                     concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    finalizing = pool.submit(self.store.complete_photogrammetry_task, pid, 1)
                    try:
                        self.assertTrue(entered.wait(3))
                        response = pool.submit(self.client.get, f'/api/v2/tasks/{pid}/live-log').result(timeout=2)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()['state_id'], 2)
                    finally:
                        release.set()
                    self.assertTrue(finalizing.result(timeout=5))
                self.assertEqual(self.store.get_project(pid)['STATE_ID'], 4)

    def test_finalization_does_not_publish_a_deleted_canceled_or_replaced_task(self):
        import concurrent.futures
        import threading
        from homelab.processing_architecture import PhotogrammetryResult

        for change in ({'ACTIVO': False}, {'STATE_ID': 3}, {'NODEODM_UUID': 'replacement'}):
            with self.subTest(change=change):
                pid = self.store.create_project(1, 'Changed finalization')
                with self.store.lock:
                    row = next(p for p in self.store.tables['dbo.WEB_PROJECTS'] if p['ID'] == pid)
                    row.update(STATE_ID=2, NODEODM_UUID='original')
                    self.store.persist_table('dbo.WEB_PROJECTS')
                entered, release = threading.Event(), threading.Event()

                def products(**kwargs):
                    entered.set()
                    if not release.wait(10):
                        raise TimeoutError('test release')
                    return PhotogrammetryResult(project_id=pid, folder=str(pid))

                with patch('homelab.processing_architecture.ODMAdapter.ingest', side_effect=products), \
                     patch('homelab.processing_architecture.register_dsm_dtm_assets_from_result') as register, \
                     concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    finalizing = pool.submit(self.store.complete_photogrammetry_task, pid, 1)
                    try:
                        self.assertTrue(entered.wait(3))
                        with self.store.lock:
                            row = next(p for p in self.store.tables['dbo.WEB_PROJECTS'] if p['ID'] == pid)
                            row.update(change)
                            self.store.persist_table('dbo.WEB_PROJECTS')
                    finally:
                        release.set()
                    with self.assertRaisesRegex(RuntimeError, 'Task changed'):
                        finalizing.result(timeout=5)
                    register.assert_not_called()
                row = next(p for p in self.store.tables['dbo.WEB_PROJECTS'] if p['ID'] == pid)
                for key, value in change.items():
                    self.assertEqual(row[key], value)
                self.assertNotEqual(row['STATE_ID'], 4)

    def test_elevation_import_cannot_revive_a_task_changed_during_derivation(self):
        import concurrent.futures
        import threading

        for change in ({'ACTIVO': False}, {'STATE_ID': 3}, {'NODEODM_UUID': 'replacement'}):
            with self.subTest(change=change):
                pid = self.store.create_project(1, 'Changed elevation import')
                with self.store.lock:
                    row = next(p for p in self.store.tables['dbo.WEB_PROJECTS'] if p['ID'] == pid)
                    row.update(STATE_ID=2, NODEODM_UUID='original')
                    self.store.persist_table('dbo.WEB_PROJECTS')
                entered, release = threading.Event(), threading.Event()

                def prepare(**kwargs):
                    entered.set()
                    if not release.wait(10):
                        raise TimeoutError('test release')
                    return {'source_sha256': 'a' * 64, 'canonical_status': 'READY', 'asset_class': 'RASTER_ELEVATION'}

                with patch('homelab.gis_splat_engine.prepare_external_asset_derivatives', side_effect=prepare), \
                     concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    importing = pool.submit(self.store.create_external_asset_import,
                        project_id=pid, user_id=1, display_name='DSM', source_filename='dsm.tif',
                        stored_rel_path=f'external/{pid}/source/dsm.tif', size_bytes=4,
                        source_origin='processed', raw_bytes=b'test', run_sync=True)
                    try:
                        self.assertTrue(entered.wait(3))
                        with self.store.lock:
                            row = next(p for p in self.store.tables['dbo.WEB_PROJECTS'] if p['ID'] == pid)
                            row.update(change)
                            self.store.persist_table('dbo.WEB_PROJECTS')
                    finally:
                        release.set()
                    with self.assertRaisesRegex(ValueError, 'task changed'):
                        importing.result(timeout=5)
                assets = [a for a in self.store.tables['dbo.MOSAICEARTH_EXTERNAL_ASSETS'] if a['PROJECT_ID'] == pid]
                self.assertEqual(len(assets), 1)
                self.assertEqual(assets[0]['STATUS'], 'deleted')
                self.assertEqual(assets[0]['CANONICAL_STATUS'], 'FAILED')

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

    def test_license_and_cloud_status_are_independent_and_do_not_expose_tokens(self):
        self.context.workspace.cloud_session=None
        state=self.client.get('/api/desktop/account-status').json()
        self.assertEqual(state['license']['edition'],'source')
        self.assertFalse(state['cloud']['logged_in'])
        self.context.workspace.cloud_session={'cloud_uid':998,'user':'status@example.invalid','token':'secret-status-fixture'}
        try:
            state=self.client.get('/api/desktop/account-status').json()
            self.assertEqual(state['cloud']['email'],'status@example.invalid')
            self.assertNotIn('secret-status-fixture',str(state))
        finally:
            self.context.workspace.cloud_session=None

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

    def test_browser_revalidates_scripts_after_an_engine_update(self):
        import hashlib
        script = self.client.get('/desktop-platform-ui.js')
        revision = hashlib.sha256(script.content).hexdigest()[:16]
        page = self.client.get('/en/Projects')
        self.assertIn('/desktop-platform-ui.js?v=' + revision, page.text)
        for path in ('/desktop-platform-ui.js', '/desktop-browser-signin.js'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('must-revalidate', response.headers['cache-control'])

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

    def test_mymosaic_actions_preserve_quoted_titles_without_breaking_html(self):
        from html.parser import HTMLParser
        title = 'Brighton "North" & South \'local\''
        mid = self.store.save_mymap_composition(1, 0, title, [], visibility='private')
        class Buttons(HTMLParser):
            def __init__(self):
                super().__init__()
                self.actions = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'button' and attrs.get('data-name') == title:
                    self.actions.append(attrs.get('onclick'))
        parser = Buttons()
        parser.feed(self.client.get('/en/MyMaps').text)
        self.assertIn(f'syncLocalMyMosaicToCloud({mid}, this.dataset.name)', parser.actions)
        self.assertIn(f'copyMyMosaicShareLink({mid}, this.dataset.name)', parser.actions)

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

    def test_processed_detail_shows_current_metrics_with_units(self):
        pid = self.store.create_project(1, 'Completed metrics fixture')
        project = next(p for p in self.store.tables['dbo.WEB_PROJECTS'] if p['ID'] == pid)
        project['CANONICAL_RESULT_CONTRACT'] = {'area_ha': 1.021423627,
            'gsd_str': '1.60 cm/px', 'gps_str': 'Not independently verified'}
        self.store.persist_table('dbo.WEB_PROJECTS')
        response = self.client.get('/Client/ProductDetail.aspx', params={'project_id': pid, 'type': 3})
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertIn('1.02 ha', response.text)
        self.assertIn('1.60 cm/px', response.text)
        self.assertIn('Not independently verified</dd>', response.text)
        self.assertIn('<span>Created</span>', response.text)


if __name__ == '__main__':
    unittest.main()
