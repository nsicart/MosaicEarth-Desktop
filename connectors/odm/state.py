"""Transfer progress in the host plugin datastore, including development mode."""
import time
from app.plugins.data_store import GlobalDataStore


class TransferState:
    def get(self, key):
        data = GlobalDataStore('mosaicearth-transfer').get_json(key, None)
        if not data or data['expires'] < time.time():
            return None
        return data['value']

    def set(self, key, value, seconds):
        GlobalDataStore('mosaicearth-transfer').set_json(key, {'expires': time.time() + seconds, 'value': value})


cache = TransferState()
