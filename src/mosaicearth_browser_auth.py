"""Native sign-in via the system browser; only one pending request in memory."""
import os
import hashlib
import platform
import threading
import time
from urllib.parse import urlsplit
from mosaicearth_sync import Client, SyncError
from mosaicearth_license import device_identity, save_grant

BASE = '/api/v2/desktop/browser-signin'


class BrowserSignIn:
    def __init__(self, workspace, public_key):
        self.workspace, self.public_key = workspace, public_key
        self.lock = threading.Lock()
        self.pending = None

    def public_state(self):
        return {k:self.pending[k] for k in ('verification_uri', 'user_code', 'purpose', 'expires_at')}

    def start(self, purpose):
        if purpose not in {'activate', 'cloud'}:
            raise SyncError('Choose activation or cloud sign-in')
        with self.lock:
            if self.pending and self.pending['expires_at'] > time.time():
                if self.pending['purpose'] != purpose:
                    raise SyncError('Complete or cancel the current sign-in first')
                return self.public_state()
            client = Client()
            device = (os.environ.get('MOSAICEARTH_HOST_DEVICE') or
                      (device_identity() if purpose == 'activate' else hashlib.sha256(str(self.workspace.root).encode()).hexdigest()))
            result = client.request('POST', BASE + '/start', {
                'purpose':purpose, 'device':device,
                'name':os.environ.get('MOSAICEARTH_HOST_NAME') or platform.node()})
            url = urlsplit(result['verification_uri'])
            if (url.scheme, url.netloc) != (urlsplit(client.origin).scheme, urlsplit(client.origin).netloc) or url.path != BASE+'/authorize':
                raise SyncError('Invalid sign-in destination from the cloud service')
            self.pending = {**result, 'purpose':purpose, 'expires_at':time.time()+min(600,result['expires_in']), 'next_poll':0}
            return self.public_state()

    def poll(self):
        with self.lock:
            if not self.pending:
                return {'status':'idle'}
            state = self.pending
            if time.time() >= state['expires_at']:
                self.pending = None
                return {'status':'expired'}
            if time.time() < state['next_poll']:
                return {'status':'pending'}
            state['next_poll'] = time.time()+5
            result = Client().request('POST', BASE+'/poll', {k:state[k] for k in ('request_id','device_code')})
            if result['status'] == 'complete':
                if state['purpose'] == 'activate':
                    save_grant(result['grant'], self.workspace.root, self.public_key)
                else:
                    self.workspace.adopt_cloud_session(result['token'], result['user'])
                self.pending = None
                return {'status':'complete', 'purpose':state['purpose'], 'email':result['user']['email']}
            if result['status'] != 'pending':
                self.pending = None
            return {'status':result['status']}

    def cancel(self):
        with self.lock:
            state, self.pending = self.pending, None
            if state and state['expires_at'] > time.time():
                try:
                    Client().request('POST', BASE+'/cancel', {k:state[k] for k in ('request_id','device_code')})
                except SyncError:
                    # Forgetting the device secret also prevents redemption;
                    # abandoned server requests expire without issuing a session.
                    pass
        return {'ok':True}
