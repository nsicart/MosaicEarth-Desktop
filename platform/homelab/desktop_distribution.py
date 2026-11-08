"""Serve verified release artifacts, with account checks for official installers."""
import json
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from .db_store import get_store
from .connector_sync import stream_download


def release_manifest():
    root = get_store().data_root / "desktop_distribution"
    try:
        return root, json.loads((root / "release.json").read_text())
    except (OSError, ValueError):
        return root, {"version": None, "artifacts": {}}


def connector_download(request, target, user):
    root, release = release_manifest()
    aliases = {"webodm-plugin": "webodm", "webui-plugin": "webui", "qgis-plugin": "qgis"}
    target = aliases.get(target, target)
    if target in {"desktop-installer-win", "desktop-installer-linux"}:
        if not user:
            return RedirectResponse("/Login.aspx?ReturnUrl=%2Fen%2FDesktop", status_code=303)
        if not get_store().get_desktop_license_info(int(user["ID"])).get("licensed"):
            raise HTTPException(403, "Purchase Desktop or use an active Studio account to download the official installer")
    item = release.get("artifacts", {}).get(target)
    if not item:
        raise HTTPException(404, "This installer or connector is not available")
    path = root / Path(item["filename"]).name
    if not path.is_file() or path.stat().st_size != item["bytes"]:
        raise HTTPException(503, "The release artifact is temporarily unavailable")
    response = stream_download(request, path, path.name, item["sha256"])
    response.headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    response.headers["X-MosaicEarth-Version"] = release["version"]
    return response
