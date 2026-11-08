import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mosaicearth_update import prepare_update


class WindowsUpdateTest(unittest.TestCase):
    root = r'C:\Users\test\AppData\Local\Programs\MosaicEarth Desktop'

    def processes(self):
        return [dict(ProcessId=42, ExecutablePath=self.root + r'\python\pythonw.exe',
                     CommandLine='"' + self.root + r'\src\mosaicearth_desktop.py" --port 19888'),
                dict(ProcessId=99, ExecutablePath=r'C:\Python312\pythonw.exe',
                     CommandLine='unrelated.py')]

    def test_idle_owned_launcher_only(self):
        stopped = []
        def fetch(port, path):
            self.assertEqual(port, 19888)
            return [] if path == '/api/projects' else {}
        self.assertEqual(prepare_update(self.root, self.processes(), fetch, stopped.append), 1)
        self.assertEqual(stopped, [42])

    def test_active_work_and_unknown_state_preserved(self):
        for projects, transfers in [([{'state': 'running'}], {}),
                                    ([], {'engine-installation': {'status': 'running'}}),
                                    ({'unexpected': True}, {})]:
            stopped = []
            with self.assertRaises(ValueError):
                prepare_update(self.root, self.processes(),
                               lambda port, path: projects if path == '/api/projects' else transfers,
                               stopped.append)
            self.assertEqual(stopped, [])

    def test_unreachable_launcher_preserved(self):
        stopped = []
        def failed(*args):
            raise OSError('offline')
        with self.assertRaises(ValueError):
            prepare_update(self.root, self.processes(), failed, stopped.append)
        self.assertEqual(stopped, [])
