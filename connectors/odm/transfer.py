"""Runs transfers in the host application's existing worker."""
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from django.conf import settings
from .state import cache
from app.models import Task
from app import pending_actions
from nodeodm import status_codes
from worker import tasks as worker_tasks
from .mosaicearth_sync import Client, build_manifest, extract_products, SyncError, PRODUCTS


def transfer(action, target, token, key, snapshot_id="", cloud_project_id=0):
    def progress(done, total, message):
        cache.set(key, {"status": "running", "done": done, "total": total, "message": message}, 86400)
    def canceled():
        return bool(cache.get(key + ":pause"))
    client = Client(token, progress=progress, canceled=canceled)
    try:
        if action == "push":
            task = Task.objects.get(pk=target)
            if task.status != status_codes.COMPLETED:
                raise SyncError("Wait for task processing to finish")
            root = Path(task.task_path()).resolve()
            # The native directory includes all original photos, products, textures and reports.
            def role(path):
                if "/" not in path and Path(path).suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
                    return "source"
                if path.startswith("assets/") and (Path(path).suffix.lower() in PRODUCTS or path.endswith(".obj.zip")):
                    return "product"
                return "attachment"
            # The texture bundle is generated without modifying the original OBJ/MTL/textures.
            for obj in (root / "assets").rglob("*.obj"):
                import zipfile
                bundle = obj.with_suffix(".obj.zip")
                with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in sorted(obj.parent.rglob("*")):
                        if file.is_file() and file != bundle and not file.name.endswith(".obj.zip"):
                            archive.write(file, file.relative_to(obj.parent))
            manifest, paths = build_manifest(root, "odm:" + str(task.pk), task.name or str(task.pk),
                {"odm": {"task_id": str(task.pk), "name": task.name, "project": task.project.name,
                         "options": task.options}}, role, progress)
            state = client.push(manifest, paths)
            result = {"snapshot_id": state["id"], "cloud_project_id": state["project_id"]}
        else:
            sid = snapshot_id
            if not sid:
                import time
                state = client.request("POST", f"/api/v2/sync/projects/{cloud_project_id}/export", {})
                sid = state["id"]
                while state["status"] in {"QUEUED", "VERIFYING"}:
                    progress(0, 0, "Preparing cloud export")
                    time.sleep(2)
                    state = client.request("GET", f"/api/v2/sync/sessions/{sid}")
                if state["status"] != "COMPLETE":
                    raise SyncError(state.get("error") or "Cloud export failed")
            # Stable staging folder retains partial downloads for a retry.
            destination = Path(settings.MEDIA_ROOT) / "mosaicearth-sync" / str(target) / sid
            state = client.pull(sid, destination)
            native = state["manifest"].get("metadata", {}).get("odm")
            task = Task.objects.create(project_id=int(target), auto_processing_node=False,
                name=state["manifest"]["name"], import_url="file://external", status=status_codes.RUNNING,
                pending_action=pending_actions.IMPORT, public=False,
                options=native.get("options", []) if native else [])
            task.create_task_directories()
            if native:
                shutil.copytree(destination, task.task_path(), dirs_exist_ok=True)
                # Validate ZIP paths before passing archives to a host import implementation.
                archive = Path(task.assets_path("all.zip"))
                if archive.is_file():
                    original_archive = Path(task.assets_path("mosaicearth-original", "all.zip"))
                    original_archive.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(archive, original_archive)
                    extract_products(archive, Path(task.assets_path()))
                    archive.unlink()
            else:
                # Preserve every file. Map compatible products into native ODM asset names.
                shutil.copytree(destination, task.assets_path("mosaicearth"), dirs_exist_ok=True)
                for item in state["manifest"]["files"]:
                    source = destination / item["path"]
                    if source.name == "all.zip":
                        extract_products(source, Path(task.assets_path()))
                    elif item["role"] == "source" and source.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
                        dest = Path(task.task_path(source.name))
                        if dest.exists():
                            dest = dest.with_name(uuid.uuid4().hex + "-" + dest.name)
                        shutil.copyfile(source, dest)
                    elif source.name in {"odm_orthophoto.tif", "orthophoto.tif", "dsm.tif", "dtm.tif", "odm_georeferenced_model.laz", "odm_georeferenced_model.las"}:
                        folder = "odm_dem" if source.name in {"dsm.tif", "dtm.tif"} else "odm_orthophoto" if "orthophoto" in source.name else "odm_georeferencing"
                        dest = Path(task.assets_path(folder, "odm_orthophoto.tif" if source.name == "orthophoto.tif" else source.name))
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, dest)
            worker_tasks.process_task.delay(task.id)
            result = {"task_id": str(task.id), "message": "All files restored; native viewer import queued"}
        cache.set(key, {"status": "complete", "message": "All files transferred and verified", "result": result}, 86400)
    except Exception as exc:
        cache.set(key, {"status": "paused" if canceled() else "failed", "message": str(exc)[:500]}, 86400)
        raise
