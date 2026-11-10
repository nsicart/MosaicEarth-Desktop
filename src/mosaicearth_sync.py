"""MosaicEarth synchronization protocol v1. Python standard library only.

AGPL-3.0-or-later. Cloud access is explicit; local processing needs no account.
"""
import hashlib
import concurrent.futures
import threading
import http.client
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ORIGIN = os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app")
CHUNK = 4 * 1024**2
PRODUCTS = {".tif", ".tiff", ".las", ".laz", ".glb", ".geojson", ".gpkg", ".kml", ".kmz", ".gpx", ".spz", ".splat", ".ksplat", ".ply"}


def checksum(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            sha.update(block)
    return sha.hexdigest()


class SyncError(ValueError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SyncError("Unexpected redirect from MosaicEarth Cloud")


class Client:
    def __init__(self, token="", origin=ORIGIN, progress=None, canceled=None):
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
            raise SyncError("Cloud connections require HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise SyncError("Use the service origin without credentials or a path")
        self.origin, self.token = origin.rstrip("/"), token
        self.progress = progress or (lambda done, total, message: None)
        self.canceled = canceled or (lambda: False)
        self.http = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, payload=None, headers=None, raw=False):
        if self.canceled():
            raise SyncError("Transfer paused. Resume to continue from the saved offset.")
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        h = {"User-Agent": "MosaicEarth/0.4.4", "Accept": "application/json", "Content-Type": "application/octet-stream" if isinstance(payload, bytes) else "application/json"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        h.update(headers or {})
        for attempt in range(4):
            try:
                response = self.http.open(urllib.request.Request(self.origin + path, data=data, method=method, headers=h), timeout=90)
                if raw:
                    return response
                with response:
                    result = json.load(response)
                if result.get("ok") is False:
                    raise SyncError(result.get("message", "Cloud rejected the request"))
                return result
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 502, 503, 504} and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                try:
                    body = json.loads(exc.read(8192))
                    reason = body.get("detail") or body.get("message") or "Request rejected"
                except (ValueError, OSError):
                    reason = "Request rejected"
                raise SyncError(f"{reason} (HTTP {exc.code})", retryable=exc.code in {429, 502, 503, 504}) from None
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
                if attempt == 3:
                    raise SyncError("Cloud connection interrupted. Resume to continue safely.", retryable=True) from exc
                time.sleep(2 ** attempt)

    def download_range(self, path, start, end, total, retry_progress=None):
        """Retry a bounded chunk; only return complete, correctly located bytes."""
        for attempt in range(6):
            if self.canceled():
                raise SyncError("Transfer paused. Resume to continue from the saved offset.")
            try:
                with self.request('GET', path, headers={'Range': f'bytes={start}-{end}'}, raw=True) as response:
                    expected = f'bytes {start}-{end}/{total}'
                    if response.status != 206 or response.headers.get('Content-Range') != expected:
                        raise SyncError('The server did not return the requested file range. The partial download is preserved.')
                    data = response.read(end - start + 2)
                    if len(data) != end - start + 1:
                        raise SyncError('Download interrupted before the chunk was complete.', retryable=True)
                    return data
            except (SyncError, http.client.HTTPException, TimeoutError, OSError) as exc:
                if isinstance(exc, SyncError) and not exc.retryable:
                    raise
                if attempt == 5:
                    raise SyncError('Connection unavailable. Retry to continue from the saved download; completed chunks are preserved.', retryable=True) from exc
                (retry_progress or self.progress)(start, total, 'Connection interrupted. Retrying from the last saved chunk…')
                for _ in range(min(30, 2 ** (attempt + 1))):
                    if self.canceled():
                        raise SyncError('Transfer paused. Resume to continue from the saved offset.')
                    time.sleep(1)

    def signin(self, email, password):
        data = self.request("POST", "/api/v2/desktop/cloud-auth", {"email": email, "password": password})
        self.token = data["token"]
        return data["user"]

    def signout(self):
        try:
            self.request("POST", "/api/v2/sync/sign-out", {})
        finally:
            self.token = ""

    def snapshots(self):
        return self.request("GET", "/api/v2/sync/sessions")["snapshots"]

    def push(self, manifest, paths, wait=True):
        state = self.request("POST", "/api/v2/sync/sessions", manifest)
        if state["status"] == "FAILED" and not state.get("assets") and str(state.get("error") or "").startswith("Checksum mismatch:"):
            self.request("DELETE", f"/api/v2/sync/sessions/{state['id']}")
            state = self.request("POST", "/api/v2/sync/sessions", manifest)
        sid = state["id"]
        total = state["bytes"]
        done = sum(state["offsets"])
        if state["status"] == "UPLOADING":
            for index, item in enumerate(state["manifest"]["files"]):
                path = Path(paths[item["path"]])
                if path.stat().st_size != item["size"]:
                    raise SyncError("A local file changed. Create a fresh snapshot.")
                offset = state["offsets"][index]
                with path.open("rb") as stream:
                    stream.seek(offset)
                    while offset < item["size"]:
                        block = stream.read(min(CHUNK, item["size"] - offset))
                        reply = self.request("PUT", f"/api/v2/sync/sessions/{sid}/files/{index}?offset={offset}", block,
                                             {"X-Chunk-SHA256": hashlib.sha256(block).hexdigest()})
                        advanced = reply["offset"] - offset
                        offset = reply["offset"]
                        done += advanced
                        stream.seek(offset)
                        self.progress(done, total, "Uploading " + item["path"])
        state = self.request("POST", f"/api/v2/sync/sessions/{sid}/commit", {})
        deadline = time.monotonic() + 24 * 3600
        while wait and state["status"] in {"QUEUED", "VERIFYING"}:
            if time.monotonic() > deadline:
                raise SyncError("Files uploaded; cloud verification is still pending. Check the transfer later.")
            self.progress(total, total, "Verifying files in MosaicEarth Cloud")
            time.sleep(2)
            state = self.request("GET", f"/api/v2/sync/sessions/{sid}")
        if state["status"] == "FAILED":
            raise SyncError(state.get("error") or "Cloud verification failed")
        return state

    def pull(self, sid, destination):
        state = self.request("GET", f"/api/v2/sync/sessions/{sid}")
        if state["status"] != "COMPLETE":
            raise SyncError("This cloud snapshot is not complete")
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        total = state["bytes"]
        completed = {}
        progress_lock = threading.Lock()
        stopped = threading.Event()
        original_canceled = self.canceled
        self.canceled = lambda: stopped.is_set() or original_canceled()

        def report(index, size, message):
            with progress_lock:
                completed[index] = size
                self.progress(sum(completed.values()), total, message)

        def receive(index, item):
            if self.canceled():
                raise SyncError("Transfer paused. Resume to continue from the saved offset.")
            relative = PurePosixPath(item["path"])
            if relative.is_absolute() or ".." in relative.parts or "\\" in item["path"] or ":" in item["path"]:
                raise SyncError("Unsafe path in cloud manifest")
            target = destination.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(destination):
                raise SyncError("Download path escapes the destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if checksum(target) != item["sha256"]:
                    raise SyncError(f"Local changes in {item['path']}. Download this revision to a new folder.")
                report(index, item["size"], "Verified " + item["path"])
                return
            temporary = target.with_name(target.name + ".mosaicearth-part")
            if temporary.is_symlink():
                raise SyncError("Unsafe download destination")
            offset = temporary.stat().st_size if temporary.exists() else 0
            if offset > item["size"]:
                temporary.unlink()
                offset = 0
            if offset < item["size"]:
                with temporary.open("ab" if offset else "wb") as stream:
                    while offset < item["size"]:
                        end = min(item["size"], offset + CHUNK) - 1
                        block = self.download_range(f"/api/v2/sync/sessions/{sid}/files/{index}",
                                                    offset, end, item["size"],
                                                    retry_progress=lambda *_: report(index, offset, "Connection interrupted. Retrying " + item["path"]))
                        stream.write(block)
                        stream.flush()
                        os.fsync(stream.fileno())
                        offset += len(block)
                        report(index, offset, "Downloading " + item["path"])
            elif item["size"] == 0:
                temporary.touch(exist_ok=True)
            if not temporary.is_file() or temporary.stat().st_size != item["size"] or checksum(temporary) != item["sha256"]:
                temporary.unlink(missing_ok=True)
                raise SyncError("Download checksum mismatch. Retry the transfer.")
            temporary.replace(target)
            report(index, item["size"], "Downloaded " + item["path"])

        try:
            # Bound concurrent files for large tiled models; each file still
            # commits only verified chunks and a complete matching SHA-256.
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                pending = [pool.submit(receive, i, item) for i, item in enumerate(state["manifest"]["files"])]
                try:
                    for future in concurrent.futures.as_completed(pending):
                        future.result()
                except BaseException:
                    stopped.set()
                    for future in pending:
                        future.cancel()
                    raise
        finally:
            self.canceled = original_canceled
        (destination / "mosaicearth-manifest.json").write_text(json.dumps(state["manifest"], indent=2), encoding="utf-8")
        return state


def build_manifest(root, origin, name, metadata=None, role_for=None, progress=None):
    root = Path(root).resolve()
    paths, files = {}, []
    for file in sorted(root.rglob("*")):
        if file.is_symlink():
            raise SyncError("Symlinks cannot be synchronized")
        if not file.is_file() or file.name.endswith(".mosaicearth-part") or file.name == "mosaicearth-manifest.json":
            continue
        relative = file.relative_to(root).as_posix()
        if progress:
            progress(0, 0, "Checking " + relative)
        role = role_for(relative) if role_for else ("source" if relative.startswith("images/") else "product" if file.suffix.lower() in PRODUCTS else "attachment")
        item = {"path": relative, "size": file.stat().st_size, "sha256": checksum(file), "role": role}
        if file.suffix.lower() in {".spz", ".splat", ".ksplat"}:
            item["gaussian"] = True
        elif file.suffix.lower() == ".ply":
            with file.open("rb") as stream:
                item["gaussian"] = b"f_dc_0" in stream.read(65536)
        files.append(item)
        paths[relative] = file
    if not files:
        raise SyncError("There are no files to synchronize")
    return {"schema": 1, "origin": origin, "name": name, "metadata": metadata or {}, "files": files}, paths


def extract_products(archive_path, destination):
    """Expand completed ODM/ODX products without trusting ZIP paths or symlinks."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > 100000 or sum(f.file_size for f in infos) > 1024**4:
            raise SyncError("Result archive exceeds the supported size")
        if sum(f.file_size for f in infos) + 1024**3 > shutil.disk_usage(destination).free:
            raise SyncError("Not enough local disk space to extract the completed products")
        for item in infos:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise SyncError("Unsafe result archive")
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination):
                raise SyncError("Unsafe result archive destination")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.stat().st_size == item.file_size:
                with target.open("rb") as stream:
                    import zlib
                    crc = 0
                    for block in iter(lambda: stream.read(CHUNK), b""):
                        crc = zlib.crc32(block, crc)
                if crc == item.CRC:
                    continue
                raise SyncError("A local result differs from its archive: " + item.filename +
                                ". Preserve the edited file in a separate folder before extracting again.")
            if target.exists():
                raise SyncError("An existing local result must not be overwritten: " + item.filename)
            temporary = target.with_name(target.name + ".mosaicearth-part")
            with archive.open(item) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output, CHUNK)
            temporary.replace(target)
    # OBJ needs its materials and textures to produce the same textured model.
    for obj in destination.rglob("*.obj"):
        bundle = obj.with_suffix(".obj.zip")
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as output:
            for file in sorted(obj.parent.rglob("*")):
                if file.is_file() and file != bundle and not file.name.endswith(".obj.zip"):
                    output.write(file, file.relative_to(obj.parent).as_posix())
