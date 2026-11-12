"""Embedded CloudCompare-PythonRuntime (`pycc`) action for MosaicEarth.

Run inside CloudCompare via Plugins -> Python -> Run Script to push selected DB-tree clouds/meshes.
AGPL-3.0-or-later.
"""
import os
import tempfile
from pathlib import Path

from .bridge import stage_cloudcompare_files
from .mosaicearth_sync import ORIGIN, Client, SyncError

try:
    import cccorelib  # type: ignore
    import pycc  # type: ignore
except ImportError:  # pragma: no cover - outside CloudCompare-PythonRuntime
    pycc = None
    cccorelib = None


def push_selected_pycc_entities(
    email: str,
    password: str,
    *,
    origin: str = ORIGIN,
    target_project_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
):
    """Export currently selected point clouds and meshes in CloudCompare's DB tree and upload to MyEarth."""
    if pycc is None:
        raise SyncError("Run this action inside CloudCompare with CloudCompare-PythonRuntime (pycc) enabled")
    cc = pycc.GetInstance()
    entities = cc.getSelectedEntities()
    if not entities:
        raise SyncError("Select one or more point clouds or meshes in the CloudCompare DB tree first")

    with tempfile.TemporaryDirectory(prefix="mosaicearth-pycc-") as tmp:
        raw_dir = Path(tmp) / "exported"
        raw_dir.mkdir(parents=True, exist_ok=True)
        exported_files = []
        params = pycc.FileIOFilter.SaveParameters()
        for idx, ent in enumerate(entities):
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (ent.getName() or f"entity_{idx}"))
            if isinstance(ent, pycc.ccPointCloud):
                out = raw_dir / f"{safe}.laz"
            else:
                out = raw_dir / f"{safe}.ply"
            res = pycc.FileIOFilter.SaveToFile(ent, str(out), params)
            if res == pycc.CC_FERR_NO_ERROR and out.is_file():
                exported_files.append(out)

        if not exported_files:
            raise SyncError("Could not serialize selected CloudCompare entities")

        manifest, paths = stage_cloudcompare_files(
            Path(tmp) / "stage",
            entities[0].getName() or "CloudCompare Selection",
            exported_files,
            target_project_id=target_project_id,
            survey_name=survey_name,
            survey_date=survey_date,
        )
        client = Client(origin=origin)
        client.signin(email, password)
        return client.push(manifest, paths)


if __name__ == "__main__" and pycc is not None:  # pragma: no cover
    push_selected_pycc_entities(
        os.environ.get("MOSAICEARTH_EMAIL", ""),
        os.environ.get("MOSAICEARTH_PASSWORD", ""),
        survey_date=os.environ.get("MOSAICEARTH_SURVEY_DATE", ""),
    )
