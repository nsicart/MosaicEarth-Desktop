"""Durable, owner-scoped input uploads for the existing asset worker."""
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from .processing_architecture import cross_process_file_lock

CHUNK_BYTES = 4 * 1024**2
MAX_BYTES = 1024**3
MAX_FILES = 200
LIFETIME_SECONDS = 24 * 3600
GAUSSIAN_MODELS = {".ply", ".splat", ".spz", ".ksplat"}
GAUSSIAN_MEDIA = {".mp4", ".mov", ".webm", ".m4v", ".zip", ".jpg", ".jpeg", ".png", ".webp"}
GAUSSIAN_TELEMETRY = {".srt", ".gpx", ".csv"}
SIDECARS = {".jgw", ".tfw", ".pgw", ".wld", ".prj", ".mtl", ".dbf", ".shx", ".cpg", ".qpj", ".sbn", ".sbx", ".xml", ".srt"}
IMPORT_FILES = GAUSSIAN_MODELS | SIDECARS | {
    ".tif", ".tiff", ".laz", ".las", ".e57", ".obj", ".fbx", ".glb", ".gltf",
    ".dae", ".usdz", ".usda", ".usdc", ".zip", ".geojson", ".json", ".kml", ".kmz", ".gpkg", ".dxf",
    ".shp", ".gpx", ".csv", ".jpg", ".jpeg", ".png", ".webp",
    ".mp4", ".mov", ".webm", ".m4v",
}


def is_pretrained_gaussian_archive(zip_path: Path) -> bool:
    """Returns True if a .zip archive already contains a pre-trained 3D Gaussian Splatting model
    (.splat, .spz, .ksplat, or a 3DGS .ply with f_dc_0 + opacity + scale_0)."""
    import zipfile

    if not zip_path or not zip_path.is_file() or not zipfile.is_zipfile(zip_path):
        return False
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for item in zf.infolist():
                if item.is_dir():
                    continue
                ext = Path(item.filename).suffix.lower()
                if ext in {".splat", ".spz", ".ksplat"}:
                    return True
                if ext == ".ply":
                    with zf.open(item, "r") as fp:
                        hdr = fp.read(65536)
                    if hdr.startswith((b"ply\n", b"ply\r\n")):
                        hdr_part = hdr.split(b"end_header", 1)[0]
                        props = {ln.split()[-1] for ln in hdr_part.splitlines() if ln.startswith(b"property ")}
                        if {b"opacity", b"scale_0", b"f_dc_0"} <= props:
                            return True
    except Exception:
        return False
    return False


class UploadError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def normalize_files(files, origin):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise UploadError("Select between 1 and 200 files.")
    normalized = []
    for item in files:
        if not isinstance(item, dict):
            raise UploadError("Invalid file description.")
        name, size = item.get("name"), item.get("size")
        if (not isinstance(name, str) or not 1 <= len(name) <= 240 or name in {".", ".."}
                or any(c in "/\\\0" or ord(c) < 32 for c in name)):
            raise UploadError("Invalid file name.")
        allowed = (GAUSSIAN_MODELS | GAUSSIAN_MEDIA | GAUSSIAN_TELEMETRY) if origin == "gaussian" else IMPORT_FILES
        if Path(name).suffix.lower() not in allowed:
            raise UploadError("File format is not supported for this task.", 415)
        if type(size) is not int or size <= 0 or size > MAX_BYTES:
            raise UploadError("File is empty or exceeds the upload limit.", 413)
        normalized.append({"name": name, "size": size})
    if len({item["name"].casefold() for item in normalized}) != len(normalized):
        raise UploadError("Each file in the same upload needs a distinct name.", 409)
    if sum(item["size"] for item in normalized) > MAX_BYTES:
        raise UploadError("The upload exceeds 1 GiB.", 413)
    if origin == "gaussian":
        non_telem = [it for it in normalized if Path(it["name"]).suffix.lower() not in GAUSSIAN_TELEMETRY]
        telem = [it for it in normalized if Path(it["name"]).suffix.lower() in GAUSSIAN_TELEMETRY]
        if not non_telem:
            raise UploadError("Select photos, a video, an archive, or a 3D Gaussian model.")
        if len(telem) > 1:
            raise UploadError("Attach at most one GPS telemetry file (.srt, .gpx, or .csv).")
        extensions = {Path(item["name"]).suffix.lower() for item in non_telem}
        if extensions & ({".mp4", ".mov", ".webm", ".m4v", ".zip"} | GAUSSIAN_MODELS):
            if len(non_telem) != 1:
                raise UploadError("Select one video, archive, or Gaussian model at a time.")
            if telem and not (extensions & {".mp4", ".mov", ".webm", ".m4v"}):
                raise UploadError("Telemetry files (.srt/.gpx/.csv) can only accompany video or photo captures.")
        elif len(non_telem) < 4:
            raise UploadError("Select at least four overlapping photos.")
    return normalized


class AssetUpload:
    def __init__(self, store, blobs, project_id, user_id, upload_id):
        try:
            if str(uuid.UUID(upload_id, version=4)) != upload_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise UploadError("Invalid upload ID.") from None
        self.store = store
        self.blobs = blobs
        self.project_id = project_id
        self.user_id = user_id
        self.upload_id = upload_id
        self.directory = store.data_root / "asset_uploads" / str(project_id) / upload_id
        self.metadata = self.directory / "session.json"
        self.lock_file = self.directory / "session.lock"

    def require_owner(self):
        project = self.store.get_project(self.project_id)
        if not project or int(project.get("CREATE_ID") or 0) != self.user_id:
            raise UploadError("Task not found.", 404)
        return project

    def read(self):
        self.require_owner()
        try:
            data = json.loads(self.metadata.read_text())
        except FileNotFoundError:
            raise UploadError("Upload not found.", 404) from None
        if data["user_id"] != self.user_id:
            raise UploadError("Upload not found.", 404)
        if time.time() > data["expires_at"] and data["status"] == "UPLOADING":
            raise UploadError("This upload expired. Start a new upload.", 410)
        return data

    def write(self, data):
        tmp = self.metadata.with_suffix(".tmp")
        with tmp.open("w") as file:
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        tmp.replace(self.metadata)

    def check_storage_quota(self, size):
        if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
            if size > shutil.disk_usage(self.store.data_root).free:
                raise UploadError("There is not enough free space on the local drive for this upload.", 413)
            return
        usage = self.store.get_user_storage_summary(self.user_id)
        if usage["private_used_bytes"] + size > int(usage["quota_gb"] * 1024**3):
            raise UploadError("This upload exceeds your private storage quota.", 413)

    def start(self, files, origin, details):
        self.require_owner()
        with cross_process_file_lock(self.lock_file):
            if self.metadata.exists():
                data = self.read()
                if data["files"] != files or data["origin"] != origin:
                    raise UploadError("That upload ID belongs to different files.", 409)
            else:
                size = sum(item["size"] for item in files)
                self.check_storage_quota(size)
                if shutil.disk_usage(self.store.data_root).free < size:
                    raise UploadError("Upload storage is temporarily full.", 507)
                data = {"user_id": self.user_id, "files": files, "origin": origin,
                        "details": details, "status": "UPLOADING", "expires_at": time.time() + LIFETIME_SECONDS}
                self.write(data)
            return self.public_status(data)

    def relpath(self, index, name):
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        return f"external/{self.project_id}/uploads/{self.upload_id}/{index}/source/{safe_name}"

    def file_path(self, index, name):
        return self.blobs.resolve_path("assets", self.relpath(index, name))

    def public_status(self, data):
        offsets = []
        for index, item in enumerate(data["files"]):
            path = self.file_path(index, item["name"])
            offsets.append(path.stat().st_size if path.is_file() else 0)
        res_st = {"upload_id": self.upload_id, "status": data["status"], "files": data["files"],
                  "offsets": offsets, "job_id": data.get("job_id"), "asset_id": data.get("asset_id"),
                  "asset_ids": data.get("asset_ids") or ([data["asset_id"]] if data.get("asset_id") else []),
                  "job_ids": data.get("job_ids") or ([data["job_id"]] if data.get("job_id") else []),
                  "checkout_url": data.get("checkout_url") if data["status"] == "AWAITING_PAYMENT" else None}
        if data.get("migrated_platform"):
            res_st["migrated_platform"] = data["migrated_platform"]
        if data.get("saved_reports_pdf"):
            res_st["saved_reports_pdf"] = data["saved_reports_pdf"]
        return res_st

    def append(self, index, offset, contents, digest):
        if not contents or len(contents) > CHUNK_BYTES or hashlib.sha256(contents).hexdigest() != digest:
            raise UploadError("The uploaded block failed its integrity check.", 422)
        with cross_process_file_lock(self.lock_file):
            data = self.read()
            if data["status"] != "UPLOADING" or not 0 <= index < len(data["files"]):
                raise UploadError("This upload does not accept more blocks.", 409)
            item = data["files"][index]
            if offset < 0 or offset % CHUNK_BYTES or len(contents) != min(CHUNK_BYTES, item["size"] - offset):
                raise UploadError("Block size or offset does not match the upload.", 422)
            path = self.file_path(index, item["name"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a+b") as file:
                file.seek(0, os.SEEK_END)
                current = file.tell()
                if offset != current:
                    if offset + len(contents) <= current:
                        file.seek(offset)
                        if file.read(len(contents)) == contents:
                            return current
                    raise UploadError("Upload offset changed. Resume from the server offset.", 409)
                file.write(contents)
                file.flush()
                os.fsync(file.fileno())
                return current + len(contents)

    def cancel(self, *, checkout_canceled=False):
        with cross_process_file_lock(self.lock_file):
            data = self.read()
            if data["status"] == "AWAITING_PAYMENT" and not checkout_canceled:
                raise UploadError("Close the secure Checkout before canceling this upload.", 409)
            if data["status"] not in {"UPLOADING", "CANCELED", "AWAITING_PAYMENT"}:
                raise UploadError("Cancel the processing job from the task page.", 409)
            with self.store.stripe_transaction():
                project = next(p for p in self.store.tables["dbo.WEB_PROJECTS"] if int(p.get("ID") or 0) == self.project_id)
                if (project.get("AUTHORIZED_UPLOAD_ID") == self.upload_id
                        and project.get("PAYMENT_STATUS") not in {"REFUNDED_ON_FAILURE", "RELEASED_ON_FAILURE"}):
                    raise UploadError("Payment was confirmed. Cancel the processing job from the task page.", 409)
                if project.get("PENDING_GAUSSIAN_UPLOAD_ID") == self.upload_id:
                    project.pop("PENDING_GAUSSIAN_UPLOAD_ID", None)
                    self.store.persist_table("dbo.WEB_PROJECTS")
            data["status"] = "CANCELED"
            self.write(data)
            # A canceled retry still owns the originals of the failed attempt.
            # Preserve them so that cancellation cannot destroy a recoverable capture.
            if not data.get("attempt"):
                for index, item in enumerate(data["files"]):
                    self.file_path(index, item["name"]).unlink(missing_ok=True)
            return self.public_status(data)

    def finish(self):
        queue = self.store.get_job_queue()
        queue.ensure_workers_started()
        with cross_process_file_lock(self.lock_file):
            data = self.read()
            if data["status"] == "QUEUED":
                return self.public_status(data)
            status = self.public_status(data)
            if data["status"] not in {"UPLOADING", "AWAITING_PAYMENT"} or status["offsets"] != [f["size"] for f in data["files"]]:
                raise UploadError("All files must finish uploading before processing.", 409)
            indexed_files = [(i, f["name"], self.relpath(i, f["name"])) for i, f in enumerate(data["files"])]
            if data["origin"] in {"imported", "gaussian"}:
                has_video_or_photo = any(
                    Path(f[1]).suffix.lower() in {".mp4", ".mov", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}
                    for f in indexed_files
                )
                indexed_files.sort(
                    key=lambda item: (
                        Path(item[1]).suffix.lower() in SIDECARS,
                        (Path(item[1]).suffix.lower() in GAUSSIAN_TELEMETRY if has_video_or_photo else False),
                    )
                )
            files = [(name, relp) for _, name, relp in indexed_files]
            first_orig_idx = indexed_files[0][0]
            details = data["details"]
            with self.store.lock, cross_process_file_lock(queue.lock_file), self.store.stripe_transaction():
                self.require_owner()
                project = next(p for p in self.store.tables["dbo.WEB_PROJECTS"] if int(p.get("ID") or 0) == self.project_id)
                previous = next((j for j in queue._jobs_table()
                                 if (j.get("PAYLOAD") or {}).get("upload_id") == self.upload_id
                                 and (j.get("PAYLOAD") or {}).get("upload_attempt", "") == data.get("attempt", "")
                                 and j.get("PROJECT_ID") == self.project_id and j.get("USER_ID") == self.user_id), None)
                if previous:
                    aid, jid = previous["ASSET_ID"], previous["ID"]
                else:
                    self.check_storage_quota(sum(status["offsets"]))
                    first, rel = files[0]
                    first_ext = Path(first).suffix.lower()
                    pretrained_zip = (
                        data["origin"] == "gaussian"
                        and first_ext == ".zip"
                        and is_pretrained_gaussian_archive(self.file_path(first_orig_idx, first))
                    )
                    training = (
                        data["origin"] == "gaussian"
                        and first_ext not in GAUSSIAN_MODELS
                        and not pretrained_zip
                    )
                    if training:
                        if project.get("PENDING_GAUSSIAN_UPLOAD_ID") not in (None, "", self.upload_id):
                            raise UploadError("This task already has an upload awaiting payment. Resume it before starting another reconstruction.", 409)
                        paid_for_upload = (project.get("PAYMENT_STATUS") == "AUTHORIZED_PAID_UPFRONT"
                                           and project.get("AUTHORIZED_UPLOAD_ID") == self.upload_id)
                        if not paid_for_upload and project.get("PAYMENT_STATUS") in {"COVERED_BY_PLAN", "AUTHORIZED_PAID_UPFRONT", "REFUND_PENDING_REVIEW"}:
                            raise UploadError("This task already has an authorized reconstruction. Open the existing job or create a new task.", 409)
                        if any(j.get("PROJECT_ID") == self.project_id and j.get("JOB_TYPE") == "gaussian_splat"
                               and j.get("STATUS") in ("QUEUED", "RUNNING") for j in queue._jobs_table()):
                            raise UploadError("This task already has an active reconstruction.", 409)
                        local_training = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
                        if local_training:
                            if not (shutil.which("colmap") and shutil.which(os.environ.get("MOSAICEARTH_OPENSPLAT_BIN", "opensplat"))):
                                raise UploadError("Local COLMAP and OpenSplat need installation before processing.", 503)
                        elif os.environ.get("MOSAICEARTH_GAUSSIAN_ENGINE") != "runpod-opensplat":
                            raise UploadError("Cloud Gaussian training is unavailable on this server.", 503)
                        if data["status"] == "AWAITING_PAYMENT" and not paid_for_upload:
                            return self.public_status(data)
                        photo_or_media_cnt = sum(1 for fn, _ in files if Path(fn).suffix.lower() not in GAUSSIAN_TELEMETRY)
                        auth = {"ok": True} if (local_training or paid_for_upload) else self.store.authorize_and_charge_task_upfront(
                            self.user_id, self.project_id, num_images=max(1, photo_or_media_cnt), task_kind="gaussian",
                            total_megabytes=sum(status["offsets"]) / (1024**2), target_mode="cloud",
                        )
                        if not auth.get("ok"):
                            raise UploadError("The reconstruction could not be authorized.", 403)
                        if auth.get("requires_stripe"):
                            project["PENDING_GAUSSIAN_UPLOAD_ID"] = self.upload_id
                            self.store.persist_table("dbo.WEB_PROJECTS")
                            data.update(status="AWAITING_PAYMENT", checkout_quote=auth,
                                        checkout_attempt=str(uuid.uuid4()))
                            self.write(data)
                            return self.public_status(data)
                    aid_list = []
                    jid_list = []
                    photo_exts = {".jpg", ".jpeg", ".png", ".webp"}
                    is_multi_photo_batch = (
                        data["origin"] == "imported"
                        and len(files) >= 2
                        and all(Path(fname).suffix.lower() in photo_exts for fname, _ in files)
                    )
                    migrated_zip_info = None
                    if data["origin"] == "imported" and len(files) == 1 and Path(files[0][0]).suffix.lower() == ".zip":
                        try:
                            from .zip_migrator import ingest_migration_zip_into_project, inspect_migration_zip
                            local_zip = self.file_path(0, files[0][0])
                            z_plan = inspect_migration_zip(local_zip)
                            if z_plan.get("deliverables_count", 0) >= 1 and (
                                bool(details.get("migrate_bundle")) or bool(z_plan.get("is_multi_deliverable_bundle"))
                            ):
                                migrated_zip_info = ingest_migration_zip_into_project(
                                    self.store,
                                    self.blobs,
                                    zip_path=local_zip,
                                    project_id=self.project_id,
                                    user_id=self.user_id,
                                    base_title=str(details.get("title") or ""),
                                    capture_date=str(details.get("capture_date") or ""),
                                    lat=details.get("lat"),
                                    lon=details.get("lon"),
                                    run_sync=False,
                                    staged_upload_id=self.upload_id,
                                    upload_attempt=data.get("attempt", ""),
                                )
                        except Exception:
                            migrated_zip_info = None

                    if migrated_zip_info and migrated_zip_info.get("asset_ids"):
                        aid_list = list(migrated_zip_info["asset_ids"])
                        jid_list = [
                            next(j["ID"] for j in queue._jobs_table() if j["ASSET_ID"] == item_aid)
                            for item_aid in aid_list
                        ]
                        aid, jid = aid_list[0], jid_list[0]
                        data["migrated_platform"] = migrated_zip_info.get("platform")
                        data["saved_reports_pdf"] = migrated_zip_info.get("saved_reports_pdf") or []
                    elif is_multi_photo_batch:
                        base_title = (details.get("title") or "").strip()
                        for orig_i, fname, frel in indexed_files:
                            fsize = int(status["offsets"][orig_i])
                            item_title = f"{base_title} ({len(aid_list) + 1}/{len(files)})" if base_title else fname
                            item_aid = self.store.create_external_asset_import(
                                project_id=self.project_id, user_id=self.user_id,
                                display_name=item_title, source_filename=fname,
                                stored_rel_path=frel, size_bytes=fsize,
                                lat=details.get("lat"), lon=details.get("lon"),
                                description=details.get("description"), capture_date=details.get("capture_date"),
                                capture_method=details.get("capture_method"), source_origin=data["origin"],
                                run_sync=False, extra_media_rel_paths=[(fname, frel)],
                                staged_upload_id=self.upload_id,
                                quality_preset="HIGH_QUALITY",
                                upload_attempt=data.get("attempt", ""),
                            )
                            item_jid = next(j["ID"] for j in queue._jobs_table() if j["ASSET_ID"] == item_aid)
                            aid_list.append(item_aid)
                            jid_list.append(item_jid)
                        aid, jid = aid_list[0], jid_list[0]
                    else:
                        aid = self.store.create_external_asset_import(
                            project_id=self.project_id, user_id=self.user_id,
                            display_name=details.get("title") or first, source_filename=first,
                            stored_rel_path=rel, size_bytes=sum(status["offsets"]),
                            lat=details.get("lat"), lon=details.get("lon"),
                            description=details.get("description"), capture_date=details.get("capture_date"),
                            capture_method=details.get("capture_method"), source_origin=data["origin"],
                            run_sync=False, extra_media_rel_paths=files,
                            staged_upload_id=self.upload_id,
                            quality_preset="HIGH_QUALITY",
                            upload_attempt=data.get("attempt", ""),
                        )
                        jid = next(j["ID"] for j in queue._jobs_table() if j["ASSET_ID"] == aid)
                        aid_list = [aid]
                        jid_list = [jid]
            queue._wake_event.set()
            data.update(
                status="QUEUED",
                asset_id=aid,
                job_id=jid,
                asset_ids=aid_list if previous is None else ([aid]),
                job_ids=jid_list if previous is None else ([jid]),
            )
            self.write(data)
            return self.public_status(data)

    def prepare_retry(self):
        """Start an explicit new attempt with immutable originals, after compensation."""
        with cross_process_file_lock(self.lock_file):
            data = self.read()
            if data["status"] != "QUEUED":
                return self.public_status(data)
            queue = self.store.get_job_queue()
            with self.store.lock, cross_process_file_lock(queue.lock_file), self.store.stripe_transaction():
                queue._sync_jobs_from_storage_unlocked()
                job = next((j for j in queue._jobs_table() if j["ID"] == data.get("job_id")), None)
                project = self.store.get_project(self.project_id)
                if not job or job.get("STATUS") not in {"FAILED", "CANCELED"}:
                    raise UploadError("Only a failed or canceled job can be retried.", 409)
                if project.get("PAYMENT_STATUS") in {"REFUND_PENDING_REVIEW", "AUTHORIZED_PAID_UPFRONT", "COVERED_BY_PLAN"}:
                    raise UploadError("The previous payment is being reconciled. Your originals are preserved; retry once the refund is confirmed.", 409)
                if self.public_status(data)["offsets"] != [f["size"] for f in data["files"]]:
                    raise UploadError("The saved originals are unavailable. Contact support before starting another upload.", 409)
                for field in ("asset_id", "job_id", "asset_ids", "job_ids", "checkout_url", "checkout_session", "checkout_quote", "checkout_attempt"):
                    data.pop(field, None)
                data.update(status="UPLOADING", attempt=str(uuid.uuid4()), expires_at=time.time()+LIFETIME_SECONDS)
                self.write(data)
                return self.public_status(data)


def validate_source(path, name, gaussian=False):
    """Check declared types using the payload before any compute is provisioned."""
    import subprocess
    import zipfile

    ext = Path(name).suffix.lower()
    with path.open("rb") as file:
        header = file.read(65536) if ext == ".ply" else file.read(128)
    if ext in {".mp4", ".mov", ".webm", ".m4v"}:
        max_dur = 900 if gaussian else 7200
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name,width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        try:
            video = json.loads(result.stdout)
            stream = video["streams"][0]
            if (result.returncode or not stream["codec_name"] or not stream["width"] or not stream["height"]
                    or not 0 < float(video["format"]["duration"]) <= max_dur):
                raise ValueError
        except (ValueError, KeyError, IndexError):
            raise ValueError("The video is invalid or longer than 15 minutes." if gaussian else "The video file is invalid or exceeds the 2-hour limit.") from None
    elif ext in {".zip", ".kmz"}:
        if not zipfile.is_zipfile(path):
            raise ValueError("The archive is invalid.")
        if gaussian:
            with zipfile.ZipFile(path) as archive:
                entries = [item for item in archive.infolist() if not item.is_dir()]
                if len(entries) > MAX_FILES or sum(item.file_size for item in entries) > MAX_BYTES:
                    raise ValueError("The Gaussian archive exceeds extraction limits.")
                for item in entries:
                    filename = item.filename.replace(chr(92), "/")
                    if (filename.startswith("/") or ".." in filename.split("/") or ":" in filename.split("/")[0]
                            or ((item.external_attr >> 16) & 0o170000) == 0o120000
                            or (item.file_size > 10 * 1024**2 and item.file_size > 200 * max(item.compress_size, 1))):
                        raise ValueError("The Gaussian archive contains an unsafe entry.")
    elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
        from PIL import Image
        with Image.open(path) as picture:
            # DJI JPEGs may include the CIPA multi-picture (MPF) metadata and
            # are identified as MPO by Pillow. Their primary image is JPEG.
            formats = {"PNG"} if ext == ".png" else ({"WEBP"} if ext == ".webp" else {"JPEG", "MPO"})
            valid_magic = (
                True
                if ext == ".png"
                else (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
                if ext == ".webp"
                else header.startswith(b"\xff\xd8\xff")
            )
            if picture.format not in formats or not valid_magic:
                raise ValueError("The image format does not match the file name.")
            picture.verify()
    elif ext in {".usdz", ".usda", ".usdc"}:
        from .usdz_converter import validate_usd_archive_or_stage
        validate_usd_archive_or_stage(path, name)
    elif ext == ".ply":
        if not header.startswith((b"ply\n", b"ply\r\n")):
            raise ValueError("The PLY file has an invalid header.")
        header = header.split(b"end_header", 1)[0]
        properties = {line.split()[-1] for line in header.splitlines() if line.startswith(b"property ")}
        if gaussian and not {b"opacity", b"rot_0", b"scale_0", b"f_dc_0"} <= properties:
            raise ValueError("This PLY is not a Gaussian model. Import it as a point cloud.")


def gaussian_training_input(asset):
    rel = str(asset.get("STORAGE_RELATIVE_PATH") or "")
    source_ext = Path(rel).suffix.lower()
    if asset.get("SOURCE_ORIGIN") != "gaussian" or source_ext not in GAUSSIAN_MEDIA:
        return False
    if source_ext == ".zip":
        summary = str(asset.get("PIPELINE_SUMMARY") or "")
        if "Imported 3D Gaussian Splat archive" in summary:
            return False
        try:
            from .blob_store import get_blob_store
            zp = get_blob_store().resolve_blob_path("assets", rel, allow_remote_fallback=False)
            if zp and is_pretrained_gaussian_archive(zp):
                return False
        except Exception:
            pass
    return True


def asset_output_available(asset):
    status = str(asset.get("STATUS") or "").lower()
    subtype = asset.get("ASSET_SUBTYPE")
    if status in {"pending", "failed", "canceled", "discarded"} or subtype == "source_media":
        return False
    source_ext = Path(str(asset.get("STORAGE_RELATIVE_PATH") or "")).suffix.lower()
    if subtype == "gaussian_splat" and source_ext in GAUSSIAN_MEDIA:
        if asset.get("CANONICAL_STATUS"):
            return asset["CANONICAL_STATUS"] == "READY" and bool(asset.get("DERIVATIVES_MANIFEST"))
        # Older imports predate the canonical manifest. Preserve access only
        # when their actual outputs are present in the established namespace.
        from .blob_store import get_blob_store

        blobs = get_blob_store()
        prefix = f"external/{int(asset['PROJECT_ID'])}/{int(asset['ID'])}"
        return any(
            all((path := blobs.resolve_path(container, f"{prefix}/{name}")) is not None
                and path.is_file() and path.stat().st_size > 0
                for name in ("tileset.json", "tile_0.glb", "model.ply", "model.spz"))
            for container in ("results", "assets")
        )
    return True
