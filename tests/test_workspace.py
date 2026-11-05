import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mosaicearth_desktop import Workspace


class WorkspaceTest(unittest.TestCase):
    def test_local_state_and_authenticated_cloud_sync(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = Workspace(root)
            project = first.create("Synthetic raster")
            self.assertEqual(Workspace(root).project(project)["name"], "Synthetic raster")
            with self.assertRaises(ValueError):
                first.sync_project(project)
            first.cloud = Mock(side_effect=[
                {"user": {"id": 4, "email": "fixture@example.com"}, "token": "sandbox-fixture"},
                {"cloudProjectId": 55},
            ])
            first.cloud_signin("fixture@example.com", "fixture-only")
            self.assertEqual(first.sync_project(project), {"cloud_id": 55})
            self.assertEqual(first.sync_project(project), {"cloud_id": 55})
            self.assertEqual(first.cloud.call_count, 2)
            self.assertEqual(first.cloud.call_args.args[1]["project"], {"name": "Synthetic raster"})
            with self.assertRaises(ValueError):
                first.project("../private")


if __name__ == "__main__":
    unittest.main()
