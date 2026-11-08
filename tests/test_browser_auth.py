import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mosaicearth_browser_auth import BrowserSignIn
from mosaicearth_desktop import Workspace
from mosaicearth_sync import SyncError


class BrowserAuthTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=Workspace(Path(self.temp.name))
        self.auth=BrowserSignIn(self.workspace,{})
        self.client=MagicMock();self.client.origin='https://app.mosaicearth.app'
        self.start={'request_id':'a'*32,'device_code':'secret-test-only','user_code':'AAAA-AAAA',
                    'verification_uri':'https://app.mosaicearth.app/api/v2/desktop/browser-signin/authorize?request_id='+'a'*32,'expires_in':600}
        self.patch=patch('mosaicearth_browser_auth.Client',return_value=self.client);self.patch.start();self.addCleanup(self.patch.stop)
        self.client.request.return_value=self.start
        identity=patch('mosaicearth_browser_auth.device_identity',return_value='a'*64)
        identity.start();self.addCleanup(identity.stop)

    def test_secret_stays_out_of_browser_and_concurrent_requests_reuse_flow(self):
        public=self.auth.start('cloud')
        self.assertNotIn('device_code',public);self.assertNotIn('secret-test-only',str(public))
        self.assertEqual(public,self.auth.start('cloud'));self.assertEqual(self.client.request.call_count,1)
        with self.assertRaises(SyncError):self.auth.start('activate')

    def test_cloud_identity_adopted_only_after_approval(self):
        self.auth.start('cloud');self.assertIsNone(self.workspace.cloud_session)
        self.client.request.return_value={'status':'pending'};self.assertEqual(self.auth.poll()['status'],'pending')
        self.auth.pending['next_poll']=0
        self.client.request.return_value={'status':'complete','user':{'id':42,'email':'qa@example.invalid'},'token':'fixture-token'}
        self.assertEqual(self.auth.poll()['email'],'qa@example.invalid')
        self.assertEqual(self.workspace.cloud_session['cloud_uid'],42);self.assertIsNone(self.auth.pending)

    def test_invalid_grant_never_claims_activation(self):
        self.auth.start('activate')
        self.client.request.return_value={'status':'complete','user':{'id':42,'email':'qa@example.invalid'},'grant':{'payload':'invalid','signature':'invalid'}}
        with self.assertRaises(SyncError):self.auth.poll()
        self.assertFalse((self.workspace.root/'account-grant.json').exists())

    def test_expired_canceled_and_redirected_flows_cannot_connect(self):
        self.auth.start('cloud');self.auth.pending['expires_at']=0
        self.assertEqual(self.auth.poll()['status'],'expired');self.assertIsNone(self.workspace.cloud_session)
        self.client.request.return_value={**self.start,'verification_uri':'https://evil.invalid/sign-in'}
        with self.assertRaises(SyncError):self.auth.start('cloud')
        self.client.request.return_value=self.start;self.auth.start('cloud');self.auth.cancel()
        self.assertIsNone(self.auth.pending);self.assertIsNone(self.workspace.cloud_session)


if __name__=='__main__':unittest.main()
