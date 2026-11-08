"""Authenticated, resumable snapshots shared by Desktop and GIS connectors.

Originals are immutable; viewer generation uses the existing persistent worker.
Transfers never provision a processing engine and never publish data publicly.
"""
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .processing_architecture import cross_process_file_lock

router = APIRouter(prefix="/api/v2/sync", tags=["Connector synchronization"])


@router.get('/capabilities')
def capabilities():
    return {'ok': True, 'snapshot_schema': 1, 'workspace_schema': 1, 'private_copies': True}
CHUNK = 4 * 1024**2
MAX_FILES = 100000
PRODUCTS = {".tif", ".tiff", ".las", ".laz", ".ply", ".spz", ".splat", ".ksplat",
            ".glb", ".geojson", ".gpkg", ".kml", ".kmz", ".gpx", ".obj"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalize_manifest(value):
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise HTTPException(400, "Unsupported manifest schema")
    origin = value.get("origin")
    name = value.get("name")
    if not isinstance(origin, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", origin):
        raise HTTPException(400, "Invalid project identity")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise HTTPException(400, "Invalid project name")
    files = value.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(400, "Select between 1 and 100000 files")
    clean, seen = [], set()
    for item in files:
        if not isinstance(item, dict):
            raise HTTPException(400, "Invalid file")
        path = item.get("path")
        if (not isinstance(path, str) or not 1 <= len(path) <= 600 or path.startswith("/")
                or any(c in path for c in "\\\x00:<>\"|?*") or any(ord(c) < 32 for c in path)
                or any(p in ("", ".", "..") or p.rstrip(". ") != p for p in path.split("/"))
                or any(p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1,10)], *[f"LPT{i}" for i in range(1,10)]} for p in path.split("/"))):
            raise HTTPException(400, "Invalid relative file path")
        if path.casefold() in seen:
            raise HTTPException(409, "Duplicate file path")
        seen.add(path.casefold())
        size, sha, role = item.get("size"), item.get("sha256"), item.get("role", "attachment")
        if type(size) is not int or not 0 <= size <= 1024**4:
            raise HTTPException(413, "Invalid file size")
        if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha):
            raise HTTPException(400, "Each file needs a SHA-256 checksum")
        if role not in {"source", "product", "attachment"}:
            raise HTTPException(400, "Invalid file role")
        if role == "product" and PurePosixPath(path).suffix.lower() not in PRODUCTS and not path.lower().endswith((".obj.zip", ".shp.zip")):
            raise HTTPException(415, "Unsupported viewer product; preserve this file as an attachment")
        entry = {"path": path, "size": size, "sha256": sha, "role": role}
        if item.get("gaussian") is True:
            if PurePosixPath(path).suffix.lower() not in {".ply", ".spz", ".splat", ".ksplat"}:
                raise HTTPException(400, "Only completed Gaussian models can be synchronized")
            entry["gaussian"] = True
        clean.append(entry)
    if any(parent.as_posix().casefold() in seen for item in clean for parent in PurePosixPath(item["path"]).parents if parent.as_posix() != "."):
        raise HTTPException(409, "A file cannot also be a folder in the same snapshot")
    # Application metadata is data, never an executable job or a cloud credential.
    metadata = value.get("metadata", {})
    try:
        metadata_size = len(json.dumps(metadata, allow_nan=False).encode())
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid project metadata") from None
    if not isinstance(metadata, dict) or metadata_size > 65536:
        raise HTTPException(400, "Invalid project metadata")
    return {"schema": 1, "origin": origin, "name": name.strip(), "metadata": metadata,
            "files": sorted(clean, key=lambda f: f["path"])}


class SnapshotStore:
    def __init__(self, store, blobs, uid):
        self.store, self.blobs, self.uid = store, blobs, int(uid)
        self.store.refresh_processing_state()
        self.root = Path(store.data_root) / "connector_sync" / str(self.uid)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def directory(self, sid):
        if not re.fullmatch("[0-9a-f]{64}", sid):
            raise HTTPException(404, "Snapshot not found")
        return self.root / sid

    def read(self, sid):
        try:
            data = json.loads((self.directory(sid) / "manifest.json").read_text())
        except FileNotFoundError:
            raise HTTPException(404, "Snapshot not found") from None
        if data["user_id"] != self.uid:
            raise HTTPException(404, "Snapshot not found")
        return data

    def write(self, data):
        directory = self.directory(data["id"])
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "manifest.json"
        tmp = target.with_suffix(".tmp")
        with tmp.open("w") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(target)

    def file(self, data, index):
        if index < 0 or index >= len(data["manifest"]["files"]):
            raise HTTPException(404, "File not found")
        name = PurePosixPath(data["manifest"]["files"][index]["path"]).name
        return self.blobs.resolve_path("assets", f"sync/{self.uid}/{data['id']}/{index}/{name}")

    def list(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*/manifest.json"))]

    def local_file(self, data, index):
        path = self.file(data, index)
        if not path.is_file():
            relative = path.relative_to(self.blobs.blobs_root / "assets").as_posix()
            path = self.blobs.ensure_local("assets", relative, force_download=True)
        if not path or not path.is_file():
            raise HTTPException(503, "Original temporarily unavailable; retry the download")
        return path

    def start(self, manifest):
        manifest = normalize_manifest(manifest)
        sid = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with cross_process_file_lock(self.root / "account.lock"):
            if (self.directory(sid) / "manifest.json").exists():
                existing = self.read(sid)
                if existing["status"] != "CANCELED":
                    return self.status(sid)
            size = sum(f["size"] for f in manifest["files"])
            usage = self.store.get_user_storage_summary(self.uid)
            # All retained revisions reserve their originals, including unfinished transfers.
            if int(usage["private_used_bytes"]) + size > int(usage["quota_gb"] * 1024**3):
                raise HTTPException(413, "Not enough account storage. Remove an unfinished transfer or upgrade storage.")
            if shutil.disk_usage(self.root).free < size + 1024**3:
                raise HTTPException(507, "Not enough server storage")
            data = {"id": sid, "user_id": self.uid, "manifest": manifest, "bytes": size,
                    "status": "UPLOADING", "created_at": time.time(), "project_id": None, "assets": {}}
            self.write(data)
            return self.status(sid)

    def status(self, sid):
        self.store.refresh_processing_state()
        data = self.read(sid)
        offsets = []
        for i, item in enumerate(data["manifest"]["files"]):
            path = self.file(data, i)
            offsets.append(path.stat().st_size if path.is_file() else 0)
        products = []
        for index, aid in data.get("assets", {}).items():
            asset = next((a for a in self.store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []) if int(a["ID"]) == aid), {})
            products.append({"file": int(index), "asset_id": aid, "status": asset.get("CANONICAL_STATUS", "PENDING"),
                             "url": f"/Client/ExternalAssetDetail.aspx?project_id={data['task_id']}&asset_id={aid}"})
        return {"ok": True, **data, "offsets": offsets, "chunk_bytes": CHUNK, "products": products}

    def append(self, sid, index, offset, content, sha):
        with cross_process_file_lock(self.directory(sid) / "transfer.lock"):
            data = self.read(sid)
            if data["status"] != "UPLOADING":
                raise HTTPException(409, "This snapshot is no longer accepting files")
            path = self.file(data, index)
            expected = data["manifest"]["files"][index]["size"]
            if not 0 < len(content) <= CHUNK or offset < 0 or offset % CHUNK or offset + len(content) > expected:
                raise HTTPException(400, "Invalid chunk size or offset")
            if len(content) != min(CHUNK, expected - offset) or hashlib.sha256(content).hexdigest() != sha:
                raise HTTPException(422, "Chunk checksum or size mismatch")
            path.parent.mkdir(parents=True, exist_ok=True)
            current = path.stat().st_size if path.exists() else 0
            if offset < current:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    if stream.read(len(content)) == content:
                        return {"ok": True, "offset": current}
                raise HTTPException(409, "Existing chunk differs; start a new snapshot")
            if offset != current:
                raise HTTPException(409, "Read the current offset and resume")
            with path.open("ab") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            return {"ok": True, "offset": offset + len(content)}

    def commit(self, sid):
        with cross_process_file_lock(self.directory(sid) / "transfer.lock"):
            data = self.read(sid)
            if data["status"] in {"QUEUED", "VERIFYING", "COMPLETE"}:
                return self.status(sid)
            if data["status"] == "CANCELED":
                raise HTTPException(409, "Transfer canceled")
            for i, item in enumerate(data["manifest"]["files"]):
                file = self.file(data, i)
                if item["size"] == 0:
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.touch(exist_ok=True)
                if not file.is_file() or file.stat().st_size != item["size"]:
                    raise HTTPException(409, "Upload every file before completing the transfer")
            queue = self.store.get_job_queue()
            with self.store.lock, cross_process_file_lock(queue.lock_file), self.store.stripe_transaction():
                queue._sync_jobs_from_storage_unlocked()
                key = data["manifest"]["origin"]
                is_workspace = data['manifest'].get('metadata', {}).get('workspace_schema') == 1
                project = None if is_workspace else next((p for p in self.store.tables.get("dbo.WEB_PROJECTS", [])
                                if int(p.get("CREATE_ID") or 0) == self.uid and p.get("SYNC_ORIGIN") == key), None)
                if project is None:
                    pid = self.store.create_project(self.uid, data["manifest"]["name"], is_project_container=True)
                    if is_workspace:
                        task = pid
                    else:
                        survey = self.store.create_project(self.uid, "Synchronized data", parent_project_id=pid, is_survey_container=True)
                        task = self.store.create_project(self.uid, data["manifest"]["name"], kind="external", survey_id=survey, parent_project_id=pid)
                    project = next(p for p in self.store.tables["dbo.WEB_PROJECTS"] if int(p["ID"]) == pid)
                    project.update(SYNC_ORIGIN=key, SYNC_TASK_ID=task)
                    self.store.persist_table("dbo.WEB_PROJECTS")
                data.update(project_id=int(project["ID"]), task_id=int(project["SYNC_TASK_ID"]))
                prior = next((j for j in queue._jobs_table() if j.get("JOB_TYPE") == "connector_sync" and
                              (j.get("PAYLOAD") or {}).get("snapshot_id") == sid and int(j.get("USER_ID") or 0) == self.uid
                              and j.get("STATUS") in {"QUEUED", "RUNNING"}), None)
                jid = int(prior["ID"]) if prior else queue.enqueue_job("connector_sync", data["task_id"], 0, self.uid,
                                       {"snapshot_id": sid}, within_registration=True)
            data.update(status="QUEUED", job_id=jid, error=None)
            self.write(data)
            return self.status(sid)

    def finalize(self, sid, progress):
        with cross_process_file_lock(self.directory(sid) / "transfer.lock", timeout=3600):
            data = self.read(sid)
            if data["status"] == "COMPLETE":
                return data
            data["status"] = "VERIFYING"
            self.write(data)
            try:
                files = data["manifest"]["files"]
                for i, item in enumerate(files):
                    progress(10 + int(65 * i / len(files)), f"Verifying {i+1}/{len(files)} files")
                    path = self.local_file(data, i)
                    if digest(path) != item["sha256"]:
                        raise ValueError(f"Checksum mismatch: {item['path']}")
                    if self.blobs.is_s3_active():
                        rel = path.relative_to(self.blobs.blobs_root / "assets").as_posix()
                        if not self.blobs._upload_file_to_s3("assets", rel, path):
                            raise RuntimeError("Cloud backup failed; originals are preserved. Retry verification.")
                # Only fully verified snapshots can enter the viewer pipeline.
                if data['manifest'].get('metadata', {}).get('workspace_schema') == 1:
                    from .workspace_exchange import import_workspace
                    paths = {item['path']: self.local_file(data, i) for i, item in enumerate(files)}
                    result = import_workspace(self.store, self.blobs, self.uid, sid, paths,
                                              root_project_id=data['project_id'], progress=progress)
                    if result['kind'] == 'mymosaic':
                        with self.store.lock, self.store.stripe_transaction():
                            for p in self.store.tables['dbo.WEB_PROJECTS']:
                                if int(p['ID']) == int(data['project_id']):
                                    p['ACTIVO'] = False
                            self.store.persist_table('dbo.WEB_PROJECTS')
                    data.update(status='COMPLETE', completed_at=time.time(), error=None, workspace_result=result)
                    self.write(data)
                    return data
                self.store.refresh_processing_state()
                project = self.store.get_project(data["task_id"])
                for i, item in enumerate(files):
                    if item["role"] != "source" or PurePosixPath(item["path"]).suffix.lower() not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
                        continue
                    filename = item["sha256"][:16] + "-" + PurePosixPath(item["path"]).name
                    rel = f"{project['FOLDER']}/orig/{filename}"
                    target = self.blobs.resolve_path("imagenes", rel)
                    if not target.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(self.file(data, i), target)
                    if self.blobs.is_s3_active() and not self.blobs._upload_file_to_s3("imagenes", rel, target):
                        raise RuntimeError("Could not back up original photos. Retry this transfer.")
                    with self.store.lock, self.store.stripe_transaction():
                        if not any(f.get("FILENAME") == filename for f in self.store.get_project_files(data["task_id"])):
                            self.store.add_project_file(data["task_id"], self.uid, filename, item["size"])
                for i, item in enumerate(files):
                    if item["role"] != "product":
                        continue
                    queue = self.store.get_job_queue()
                    with self.store.lock, cross_process_file_lock(queue.lock_file), self.store.stripe_transaction():
                        queue._sync_jobs_from_storage_unlocked()
                        key = f"sync:{self.uid}:{sid}:{i}"
                        existing = next((a for a in self.store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []) if a.get("DERIVATIVE_KEY") == key), None)
                        aid = int(existing["ID"]) if existing else self.store.create_external_asset_import(
                            project_id=data["task_id"], user_id=self.uid, display_name=PurePosixPath(item["path"]).name,
                            source_filename=PurePosixPath(item["path"]).name,
                            stored_rel_path=self.file(data, i).relative_to(self.blobs.blobs_root / "assets").as_posix(), size_bytes=item["size"],
                            source_origin="gaussian" if item.get("gaussian") else "imported", run_sync=False,
                            staged_upload_id="connector-" + sid, derivative_key=key)
                        data["assets"][str(i)] = aid
                    self.write(data)
                data.update(status="COMPLETE", completed_at=time.time(), error=None)
                self.write(data)
                return data
            except Exception as exc:
                data.update(status="FAILED", error=str(exc)[:400])
                self.write(data)
                raise

    def cancel(self, sid):
        with cross_process_file_lock(self.root / "account.lock"), cross_process_file_lock(self.directory(sid) / "transfer.lock"):
            data = self.read(sid)
            if data["status"] not in {"UPLOADING", "FAILED", "CANCELED"} or data.get("assets"):
                raise HTTPException(409, "Only an unfinished transfer can be removed")
            for i in range(len(data["manifest"]["files"])):
                self.file(data, i).unlink(missing_ok=True)
            data.update(status="CANCELED", bytes=0)
            self.write(data)
        return {"ok": True}

    def project_rows(self, pid):
        rows = self.store.tables.get("dbo.WEB_PROJECTS", [])
        project = next((p for p in rows if int(p["ID"]) == pid and int(p.get("CREATE_ID") or 0) == self.uid and p.get("ACTIVO", True)), None)
        if project is None:
            raise HTTPException(404, "Project not found")
        ids = {pid}
        for _ in range(3):
            ids.update(int(p["ID"]) for p in rows if int(p.get("CREATE_ID") or 0) == self.uid and p.get("ACTIVO", True)
                       and (int(p.get("PARENT_PROJECT_ID") or 0) in ids or int(p.get("SURVEY_ID") or 0) in ids))
        return [p for p in rows if int(p["ID"]) in ids]

    def export(self, pid):
        with cross_process_file_lock(self.root / "account.lock"):
            self.store.refresh_processing_state()
            rows = self.project_rows(pid)
            ids = {int(p["ID"]) for p in rows}
            snapshots = self.list()
            fingerprint = hashlib.sha256(json.dumps({"projects": rows,
                "assets": [a for a in self.store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []) if int(a.get("PROJECT_ID") or 0) in ids],
                "files": [f for p in ids for f in self.store.get_project_files(p)],
                "revisions": sorted(s["id"] for s in snapshots if not s.get("export") and s.get("project_id") in ids and s["status"] == "COMPLETE")},
                sort_keys=True, default=str).encode()).hexdigest()
            # Concurrent clicks reuse the in-flight export of this project.
            active = next((s for s in snapshots if s.get("export") and s.get("project_id") == pid
                           and (s["status"] in {"QUEUED", "VERIFYING"} or
                                s["status"] == "COMPLETE" and s.get("fingerprint") == fingerprint)), None)
            if active:
                return self.status(active["id"])
            return self._export(pid, fingerprint)

    def _export(self, pid, fingerprint):
        rows = self.project_rows(pid)
        # Exporting is read-only. A new explicit export includes changes made since the last snapshot.
        sid = uuid.uuid4().hex + uuid.uuid4().hex
        data = {"id": sid, "user_id": self.uid, "bytes": 0, "status": "QUEUED", "created_at": time.time(),
                "project_id": pid, "task_id": pid, "assets": {}, "export": True, "fingerprint": fingerprint,
                "manifest": {"schema": 1, "origin": f"cloud:{pid}", "name": rows[0]["NAME"], "files": [], "metadata": {}}}
        with cross_process_file_lock(self.directory(sid) / "transfer.lock"):
            self.write(data)
            queue = self.store.get_job_queue()
            with self.store.lock, cross_process_file_lock(queue.lock_file), self.store.stripe_transaction():
                queue._sync_jobs_from_storage_unlocked()
                jid = queue.enqueue_job("connector_export", pid, 0, self.uid, {"snapshot_id": sid}, within_registration=True)
            data["job_id"] = jid
            self.write(data)
        return self.status(sid)

    def finalize_export(self, sid, progress):
        with cross_process_file_lock(self.directory(sid) / "transfer.lock", timeout=3600):
            return self._finalize_export(sid, progress)

    def export_workspace(self, kind, entity_id):
        from .workspace_exchange import FIELDS
        if kind not in {'project', 'mymosaic'}:
            raise HTTPException(400, 'Choose a project or MyMosaic')
        row = self.store.get_project(entity_id) if kind == 'project' else self.store.get_mymap(str(entity_id))
        if not row or int(row.get('CREATE_ID') or 0) != self.uid or not row.get('ACTIVO', True):
            raise HTTPException(404, 'Workspace item not found')
        owned = {int(p['ID']) for p in self.store.tables.get('dbo.WEB_PROJECTS', []) if int(p.get('CREATE_ID') or 0) == self.uid}
        maps = {int(m['ID']) for m in self.store.tables.get('dbo.WEB_MYMAPS', []) if int(m.get('CREATE_ID') or 0) == self.uid}
        relevant = {table: [{k: v for k, v in r.items() if k in FIELDS[table]} for r in self.store.tables.get(table, [])
                            if int(r.get('CREATE_ID') or r.get('USER_ID') or 0) == self.uid or
                            int(r.get('PROJECT_ID') or r.get('Project_id') or 0) in owned or int(r.get('MYMAP_ID') or 0) in maps]
                    for table in FIELDS}
        fingerprint = hashlib.sha256(json.dumps(relevant, sort_keys=True, default=str).encode()).hexdigest()
        with cross_process_file_lock(self.root / 'account.lock'):
            existing = next((s for s in self.list() if s.get('workspace_export') == [kind, entity_id]
                             and s.get('fingerprint') == fingerprint and s['status'] in {'QUEUED', 'VERIFYING', 'COMPLETE'}), None)
            if existing:
                return self.status(existing['id'])
            sid = uuid.uuid4().hex + uuid.uuid4().hex
            data = {'id': sid, 'user_id': self.uid, 'bytes': 0, 'status': 'QUEUED', 'created_at': time.time(),
                    'project_id': entity_id if kind == 'project' else 0, 'task_id': 0, 'assets': {}, 'export': True,
                    'fingerprint': fingerprint, 'workspace_export': [kind, entity_id],
                    'manifest': {'schema': 1, 'origin': f'cloud:{kind}:{entity_id}', 'name': row['NAME'], 'files': [], 'metadata': {'workspace_schema': 1}}}
            self.write(data)
            queue = self.store.get_job_queue()
            with self.store.lock, cross_process_file_lock(queue.lock_file), self.store.stripe_transaction():
                queue._sync_jobs_from_storage_unlocked()
                data['job_id'] = queue.enqueue_job('connector_export', data['project_id'], 0, self.uid, {'snapshot_id': sid}, within_registration=True)
            self.write(data)
            return self.status(sid)

    def _finalize_export(self, sid, progress):
        data = self.read(sid)
        if data["status"] == "COMPLETE":
            return data
        try:
            if data.get('workspace_export'):
                from .workspace_exchange import export_workspace
                data['status'] = 'VERIFYING'
                self.write(data)
                kind, entity_id = data['workspace_export']
                manifest, paths = export_workspace(self.store, self.blobs, self.uid, kind, entity_id, self.directory(sid) / 'workspace')
                manifest['origin'] = f'cloud:{kind}:{entity_id}'
                data['manifest'] = normalize_manifest(manifest)
                required = sum(item['size'] for item in data['manifest']['files'])
                if shutil.disk_usage(self.root).free < required + 1024**3:
                    raise ValueError('Not enough temporary disk space to export this workspace safely')
                for i, item in enumerate(data['manifest']['files']):
                    source = paths[item['path']]
                    target = self.file(data, i)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    if digest(target) != item['sha256']:
                        raise ValueError('A workspace file changed during export. Wait for processing to finish and retry.')
                    if self.blobs.is_s3_active() and not self.blobs._upload_file_to_s3('assets', target.relative_to(self.blobs.blobs_root / 'assets').as_posix(), target):
                        raise ValueError('Could not preserve the cloud export. Retry this revision.')
                    progress(5 + int(90 * (i + 1) / len(data['manifest']['files'])), 'Exporting complete workspace')
                data.update(status='COMPLETE', bytes=sum(f['size'] for f in manifest['files']), completed_at=time.time())
                self.write(data)
                return data
            rows = self.project_rows(data["project_id"])
            data["status"] = "VERIFYING"
            self.write(data)
            ids = {int(p["ID"]) for p in rows}
            assets = [a for a in self.store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []) if int(a.get("PROJECT_ID") or 0) in ids and a.get("STATUS") not in {"deleted", "discarded"}]
            fields = {"ID", "NAME", "DESCRIPTION", "PARENT_PROJECT_ID", "SURVEY_ID", "IS_PROJECT_CONTAINER", "IS_SURVEY_CONTAINER", "TASK_CREATION_DATE", "CAPTURE_METHOD", "LATITUD", "LONGITUD", "CANONICAL_RESULT_CONTRACT"}
            afields = {"ID", "PROJECT_ID", "TITLE", "DESCRIPTION", "SOURCE_FILE_NAME", "LATITUDE", "LONGITUDE", "ALTITUDE", "HEADING", "PITCH", "ROLL", "SCALE", "ELEVATION_META", "INSPECTION_META", "ASSET_CLASS"}
            data["manifest"]["metadata"] = {"projects": [{k: p[k] for k in fields if k in p} for p in rows],
                                                "assets": [{k: a[k] for k in afields if k in a} for a in assets]}
            sources = {}
            seen_storage = set()

            def add(container, relative, name, role="attachment", required=True):
                if (container, relative) in seen_storage:
                    return
                path = self.blobs.ensure_local(container, relative, force_download=True)
                if not path or not path.is_file():
                    if required:
                        raise ValueError("An original is unavailable: " + name)
                    return
                sources[name] = (path, role)
                seen_storage.add((container, relative))

            for p in rows:
                pid, folder = int(p["ID"]), str(p.get("FOLDER") or "")
                if folder:
                    for f in self.store.get_project_files(pid):
                        filename = str(f["FILENAME"])
                        path = self.blobs.ensure_local("imagenes", f"{folder}/{filename}", force_download=True)
                        relative = f"{folder}/{filename}" if path and path.is_file() else f"{folder}/orig/{filename}"
                        add("imagenes", relative, f"tasks/{pid}/images/{filename}", "source")
                    contract = p.get("CANONICAL_RESULT_CONTRACT") or {}
                    for key, relative in contract.items():
                        if (key.endswith("_master") or key == "quality_report_pdf") and isinstance(relative, str) and relative:
                            add("imagenes", relative, f"tasks/{pid}/products/{key}/{Path(relative).name}", "product" if Path(relative).suffix.lower() in PRODUCTS else "attachment")
                    # The native results archive preserves textures, camera reports, DEMs and auxiliary files.
                    if p.get("ZIP_UPLOADED"):
                        add("imagenes", f"{folder}/procesado/all.zip", f"tasks/{pid}/all.zip")
                    # Include auxiliary files and every prepared native viewer tile,
                    # even when zero-local-disk storage has evicted its cache.
                    for relative in self.blobs.list_stored_files("imagenes", folder):
                        add("imagenes", relative, f"tasks/{pid}/stored/{relative[len(folder) + 1:]}")
            for a in assets:
                prefix = f"tasks/{a['PROJECT_ID']}/assets/{a['ID']}"
                relative = str(a.get("STORAGE_RELATIVE_PATH") or "")
                if relative:
                    filename = Path(str(a.get("SOURCE_FILE_NAME") or relative)).name
                    add("assets", relative, prefix + "/" + filename, "product" if Path(filename).suffix.lower() in PRODUCTS else "attachment")
                stored_prefix = f"external/{a['PROJECT_ID']}/{a['ID']}/"
                for container in ("results", "assets"):
                    for relative in self.blobs.list_stored_files(container, stored_prefix):
                        name = prefix + "/viewer/" + relative[len(stored_prefix):]
                        if name not in sources:
                            add(container, relative, name)
            # Include all original synchronized attachments and sources, not only imported viewer layers.
            for snapshot in self.list():
                if snapshot.get("export") or snapshot["status"] != "COMPLETE" or snapshot.get("project_id") not in ids:
                    continue
                for i, item in enumerate(snapshot["manifest"]["files"]):
                    sources[f"revisions/{snapshot['id']}/{item['path']}"] = (self.local_file(snapshot, i), item["role"])
            if not sources:
                raise ValueError("This project has no stored files to export")
            if len(sources) > MAX_FILES:
                raise ValueError("Export tasks separately: this project exceeds 100000 files")
            size = sum(p.stat().st_size for p, _ in sources.values())
            if shutil.disk_usage(self.root).free < size + 1024**3:
                raise ValueError("Not enough server storage for this export")
            files = []
            for i, (name, (path, role)) in enumerate(sorted(sources.items())):
                progress(5 + int(90 * i / len(sources)), f"Exporting {i+1}/{len(sources)} files")
                files.append({"path": name, "role": role, "size": path.stat().st_size, "sha256": digest(path)})
                data["manifest"]["files"] = files
                target = self.file(data, i)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                if digest(target) != files[-1]["sha256"]:
                    raise ValueError("A file changed during export. Retry when processing has finished.")
            data["manifest"] = normalize_manifest(data["manifest"])
            data.update(status="COMPLETE", bytes=size, completed_at=time.time())
            self.write(data)
            return data
        except Exception as exc:
            data.update(status="FAILED", error=str(exc)[:400])
            self.write(data)
            raise


def context(request):
    from .db_store import get_store
    from .blob_store import get_blob_store
    store = get_store()
    store.refresh_account_state()
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    uid = store.resolve_login_session(token) if token else None
    user = store.get_user_by_id(uid) if uid else None
    if not user or not user.get("ACTIVO", True) or user.get("SESSION_REVOKED"):
        raise HTTPException(401, "Sign in to MosaicEarth Cloud again")
    return SnapshotStore(store, get_blob_store(), uid)


@router.post("/sessions")
async def start(request: Request):
    sync = context(request)
    body = await request.body()
    if len(body) > 64 * 1024**2:
        raise HTTPException(413, "Manifest too large")
    try:
        manifest = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Invalid manifest JSON") from None
    return await run_in_threadpool(sync.start, manifest)


@router.get("/sessions")
def listing(request: Request):
    sync = context(request)
    return {"ok": True, "snapshots": [{k: d.get(k) for k in ("id", "project_id", "status", "bytes", "created_at")} |
            {"name": d["manifest"]["name"], "origin": d["manifest"]["origin"]} for d in sync.list()]}


@router.get("/sessions/{sid}")
def status(sid: str, request: Request):
    return context(request).status(sid)


@router.put("/sessions/{sid}/files/{index}")
async def chunk(sid: str, index: int, request: Request, offset: int = 0):
    sync = context(request)
    content = bytearray()
    async for block in request.stream():
        content.extend(block)
        if len(content) > CHUNK:
            raise HTTPException(413, "Chunk exceeds 4 MiB")
    return await run_in_threadpool(sync.append, sid, index, offset, bytes(content), request.headers.get("X-Chunk-SHA256", ""))


@router.post("/sessions/{sid}/commit")
def commit(sid: str, request: Request):
    return context(request).commit(sid)


@router.delete("/sessions/{sid}")
def cancel(sid: str, request: Request):
    return context(request).cancel(sid)


@router.get("/projects")
def projects(request: Request):
    sync = context(request)
    return {"ok": True, "projects": [{"id": int(p["ID"]), "name": p["NAME"]} for p in sync.store.tables.get("dbo.WEB_PROJECTS", [])
            if int(p.get("CREATE_ID") or 0) == sync.uid and p.get("ACTIVO", True) and not p.get("PARENT_PROJECT_ID") and not p.get("SURVEY_ID")]}


@router.post("/projects/{pid}/export")
def export(pid: int, request: Request):
    return context(request).export(pid)


@router.post('/workspaces/{kind}/{entity_id}/export')
def export_full_workspace(kind: str, entity_id: int, request: Request):
    return context(request).export_workspace(kind, entity_id)


@router.get("/sessions/{sid}/files/{index}")
def download(sid: str, index: int, request: Request):
    sync = context(request)
    data = sync.read(sid)
    if data["status"] != "COMPLETE":
        raise HTTPException(409, "Snapshot verification is not complete")
    path = sync.local_file(data, index)
    item = data["manifest"]["files"][index]
    return stream_download(request, path, PurePosixPath(item["path"]).name, item["sha256"])


def stream_download(request, path, filename, sha256):
    headers = {"Cache-Control": "private, no-store", "X-Content-SHA256": sha256, "Accept-Ranges": "bytes"}
    range_header = request.headers.get("Range")
    if range_header:
        size = path.stat().st_size
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if not match or not any(match.groups()) or not size:
            raise HTTPException(416, "Invalid byte range", headers={"Content-Range": f"bytes */{size}"})
        first, last = match.groups()
        start = int(first) if first else max(0, size - int(last))
        end = min(size - 1, int(last)) if first and last else size - 1
        if start >= size or end < start:
            raise HTTPException(416, "Range outside the file", headers={"Content-Range": f"bytes */{size}"})
        headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})

        def blocks():
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = stream.read(min(CHUNK, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    yield block
        return StreamingResponse(blocks(), status_code=206, media_type="application/octet-stream", headers=headers)
    return FileResponse(path, filename=filename, media_type="application/octet-stream", headers=headers)


@router.post("/sign-out")
def signout(request: Request):
    sync = context(request)
    sync.store.revoke_login_session(request.headers["Authorization"][7:])
    return {"ok": True}
