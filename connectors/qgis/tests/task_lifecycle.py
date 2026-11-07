"""Build connectors, then run with python-qgis; no network or GUI actions."""
import os
import sys
import time
import unittest
from pathlib import Path

from qgis.core import QgsApplication

sys.path.insert(0, os.environ.get('ME_QGIS_TEST_PACKAGE',
                                str(Path(__file__).resolve().parents[3] / 'build/connector-qgis')))
from mosaicearth.plugin import MosaicEarthPlugin


class Feedback:
    def setText(self, value):
        self.text = value

    def setValue(self, value):
        self.value = value


class TaskLifecycle(unittest.TestCase):
    def test_consecutive_operations_release_finished_tasks(self):
        plugin = MosaicEarthPlugin(None)
        plugin.status = Feedback()
        plugin.progress = Feedback()
        completed = []
        for number in range(2):
            plugin.background('Local test', lambda task: True, lambda result: completed.append(result))
            deadline = time.monotonic() + 10
            while plugin.task is not None and time.monotonic() < deadline:
                QgsApplication.processEvents()
                time.sleep(0.01)
            self.assertIsNone(plugin.task)
            self.assertEqual(len(completed), number + 1)
        plugin.pause()  # Must not call into a deleted C++ wrapper.


if __name__ == '__main__':
    app = QgsApplication([], False)
    app.initQgis()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TaskLifecycle))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result.wasSuccessful() else 1)
