"""Protect running work and unrelated Docker resources during runtime updates."""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mosaicearth_launcher import PlatformLauncher


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.launcher = PlatformLauncher(SimpleNamespace(root=self.root), self.root, 19757)
        self.database = self.root / 'platform/db/mosaicearth_state.sqlite3'
        self.database.parent.mkdir(parents=True)
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE table_overrides (table_name TEXT, rows_json TEXT)')

    def tearDown(self):
        self.temp.cleanup()

    def row(self, table, rows):
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT INTO table_overrides VALUES (?, ?)', (table, json.dumps(rows)))

    def test_reconstruction_and_background_jobs_block_updates(self):
        for table, row in [('dbo.WEB_PROJECTS', {'STATE_ID': 2}),
                           ('dbo.MOSAICEARTH_BACKGROUND_JOBS', {'STATUS': 'RUNNING'})]:
            with self.subTest(table=table):
                self.row(table, [row])
                self.assertFalse(self.launcher._workspace_idle())
                with sqlite3.connect(self.database) as db:
                    db.execute('DELETE FROM table_overrides')

    def test_transfer_blocks_update_even_when_models_have_finished(self):
        self.row('dbo.WEB_PROJECTS', [{'STATE_ID': 4}])
        response = io.BytesIO(json.dumps({'transfers': {'a': {'status': 'running'}}}).encode())
        with patch('urllib.request.urlopen', return_value=response):
            self.assertFalse(self.launcher._workspace_idle())

    def test_idle_update_ignores_public_catalog_jobs(self):
        self.row('dbo.WEB_PROJECTS', [{'STATE_ID': 2, 'DESKTOP_PUBLIC_CATALOG': True}, {'STATE_ID': 4}])
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"transfers":{}}')):
            self.assertTrue(self.launcher._workspace_idle())

    def test_unreachable_runtime_is_not_assumed_idle(self):
        with patch('urllib.request.urlopen', side_effect=OSError('offline')):
            self.assertFalse(self.launcher._workspace_idle())

    def test_foreign_container_is_never_stopped_or_removed(self):
        existing = SimpleNamespace(returncode=0, stdout=json.dumps([{
            'Config': {'Labels': {'mosaicearth.workspace': 'someone-else'}},
            'Image': 'sha256:old', 'State': {'Running': True}}]).encode())
        current = SimpleNamespace(returncode=0, stdout=b'sha256:new')
        with patch.object(self.launcher, 'command', side_effect=[existing, current]) as command:
            with self.assertRaisesRegex(ValueError, 'Another application owns'):
                self.launcher._container('name', 'image', [], [])
            self.assertFalse(any(call.args[0] in {'stop', 'rm', 'run'} for call in command.call_args_list))


if __name__ == '__main__':
    unittest.main()
