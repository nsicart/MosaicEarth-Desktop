"""MosaicEarth Desktop's local workspace and launcher."""
import argparse
import errno
import http.client
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
import webbrowser
import zipfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from mosaicearth_sync import Client, SyncError, build_manifest, extract_products, checksum
from mosaicearth_license import activate, verify_grant


ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "resources"
LOCAL_IMAGE = "mosaicearth-desktop/local-engine:0.1.0"
CUDA_IMAGE = "mosaicearth-desktop/local-engine:0.1.0-cuda"
ODX_IMAGE = "mosaicearth-desktop/nodeodx:0.1.0"
MAX_UPLOAD = 3 * 1024**3
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
PROJECT_ID = re.compile(r"[0-9a-f]{32}")


def docker_command():
    override = os.environ.get("ME_DOCKER")
    if override:
        return shlex.split(override)
    executable = shutil.which("docker")
    if executable:
        return [executable]
    # Docker Desktop can be installed after Explorer started. Its children may
    # retain the old PATH until sign-out, so also check the vendor's locations.
    if sys.platform == "win32":
        for variable, suffix in [
            ("LOCALAPPDATA", "Programs/DockerDesktop/resources/bin/docker.exe"),
            ("ProgramFiles", "Docker/Docker/resources/bin/docker.exe"),
        ]:
            base = os.environ.get(variable)
            if base:
                candidate = Path(base) / suffix
                if candidate.is_file():
                    return [str(candidate)]
    return ["docker"]


class Workspace:
    def __init__(self, root, steps=2000, gaussian_device="cpu"):
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.steps = steps
        self.gaussian_device = gaussian_device
        self.cloud_session = None
        self.cloud_lock = threading.Lock()
        self.transfers = {}
        self.transfer_lock = threading.Lock()
        self.docker = docker_command()
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS project(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ready',
                mode TEXT, result TEXT, error TEXT)""")
            db.execute("UPDATE project SET state='interrupted', error='Restarted during processing' WHERE state='running'")
            db.execute("""CREATE TABLE IF NOT EXISTS cloud_sync (
                project_id TEXT NOT NULL, user_id INTEGER NOT NULL, cloud_id INTEGER,
                PRIMARY KEY(project_id,user_id))""")

    def db(self):
        db = sqlite3.connect(self.root / "projects.sqlite3", timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def project(self, project_id):
        if not PROJECT_ID.fullmatch(project_id):
            raise ValueError("Unknown project")
        with self.db() as db:
            row = db.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown project")
        return dict(row)

    def create(self, name):
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError("Use a project name from 1 to 120 characters")
        project_id = uuid.uuid4().hex
        (self.root / project_id / "images").mkdir(mode=0o700, parents=True)
        with self.db() as db:
            db.execute("INSERT INTO project(id,name) VALUES(?,?)", (project_id, name))
        return project_id

    def cloud_signin(self, email, password):
        client = Client()
        user = client.signin(email, password)
        uid = int(user["id"])
        with self.cloud_lock:
            self.cloud_session = {"token": client.token, "cloud_uid": uid, "user": user["email"]}
        return {"user": user["email"]}

    def sync_project(self, project_id):
        project = self.project(project_id)
        with self.cloud_lock:
            session = dict(self.cloud_session or {})
            if not session:
                raise ValueError("Sign in to MosaicEarth Cloud before syncing")
        if project["state"] == "running":
            raise ValueError("Wait for local processing to finish before synchronizing")
        return self.start_transfer(project_id, self.push_snapshot, project, session)

    def start_transfer(self, key, operation, *args):
        with self.transfer_lock:
            if self.transfers.get(key, {}).get("status") == "running":
                raise ValueError("A transfer is already running")
            state = {"status": "running", "message": "Preparing transfer", "done": 0, "total": 0, "pause": False}
            self.transfers[key] = state

        def progress(done, total, message):
            with self.transfer_lock:
                state.update(done=done, total=total, message=message)

        def run():
            try:
                result = operation(*args, progress=progress, canceled=lambda: state["pause"])
                with self.transfer_lock:
                    state.update(status="complete", message="All files synchronized and verified", result=result)
            except (ValueError, OSError, zipfile.BadZipFile) as exc:
                with self.transfer_lock:
                    state.update(status="paused" if state["pause"] else "failed", message=str(exc))
        threading.Thread(target=run, daemon=True).start()
        return {"transfer_id": key}

    def push_snapshot(self, project, session, progress, canceled):
        root = self.root / project["id"]
        result = project.get("result")
        if result and project["state"] == "complete" and project["mode"] == "odx":
            progress(0, 0, "Preparing completed NodeODX products")
            extract_products(root / result / "all.zip", root / result / "products")

        def role(path):
            if path.startswith("images/"):
                return "source"
            if path == "raster.tif":
                return "product"
            if result and path.startswith(result + "/") and project["state"] == "complete":
                if path.endswith("/output/model.spz") or path.endswith(".obj.zip"):
                    return "product"
                if "/products/" in path and Path(path).suffix.lower() in {".tif", ".tiff", ".las", ".laz", ".glb", ".geojson"}:
                    return "product"
            return "attachment"

        manifest, paths = build_manifest(root, "desktop:" + project["id"], project["name"],
                                         {"desktop": project}, role, progress)
        data = Client(session["token"], progress=progress, canceled=canceled).push(manifest, paths)
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO cloud_sync VALUES(?,?,?)", (project["id"], session["cloud_uid"], data["project_id"]))
        return {"cloud_id": data["project_id"], "snapshot_id": data["id"], "products": data["products"]}

    def cloud_client(self):
        with self.cloud_lock:
            if not self.cloud_session:
                raise ValueError("Sign in to MosaicEarth Cloud first")
            return Client(self.cloud_session["token"])

    def cloud_catalog(self):
        client = self.cloud_client()
        return {"snapshots": client.snapshots(), "projects": client.request("GET", "/api/v2/sync/projects")["projects"]}

    def pull_project(self, snapshot_id="", cloud_project_id=0):
        client = self.cloud_client()
        key = "download:" + (snapshot_id or str(cloud_project_id))

        def download(progress, canceled):
            client.progress, client.canceled = progress, canceled
            sid = snapshot_id
            if not sid:
                state = client.request("POST", f"/api/v2/sync/projects/{int(cloud_project_id)}/export", {})
                sid = state["id"]
                while state["status"] in {"QUEUED", "VERIFYING"}:
                    progress(0, 0, "Preparing cloud project export")
                    time.sleep(2)
                    state = client.request("GET", f"/api/v2/sync/sessions/{sid}")
                if state["status"] != "COMPLETE":
                    raise ValueError(state.get("error") or "Cloud export failed")
            state = client.request("GET", f"/api/v2/sync/sessions/{sid}")
            # Stable local ID allows resuming a download without overwriting a working project.
            pid = uuid.uuid5(uuid.NAMESPACE_URL, f"{client.origin}:{sid}").hex
            destination = self.root / pid
            client.pull(sid, destination)
            (destination / "images").mkdir(exist_ok=True)
            meta = state["manifest"].get("metadata", {}).get("desktop", {})
            if not meta:
                # Make cloud/connector originals available to local processing too.
                for item in state["manifest"]["files"]:
                    source = destination / item["path"]
                    if item["role"] == "source" and source.suffix.lower() in IMAGE_TYPES:
                        target = destination / "images" / source.name
                        if target.exists() and checksum(target) != item["sha256"]:
                            target = target.with_name(item["sha256"][:16] + "-" + target.name)
                        if not target.exists():
                            shutil.copyfile(source, target)
            result = meta.get("result")
            if result and not PROJECT_ID.fullmatch(result):
                raise ValueError("Invalid local result reference")
            with self.db() as db:
                db.execute("INSERT OR IGNORE INTO project(id,name,state,mode,result) VALUES(?,?,?,?,?)",
                           (pid, state["manifest"]["name"] + " (cloud copy)", "complete", meta.get("mode", "imported"), result))
            return {"project_id": pid, "snapshot_id": sid}
        return self.start_transfer(key, download)

    def cloud_signout(self):
        client = self.cloud_client()
        with self.transfer_lock:
            for state in self.transfers.values():
                state["pause"] = True
        try:
            client.signout()
        finally:
            with self.cloud_lock:
                self.cloud_session = None

    def installation_status(self):
        commercial = (RESOURCES / "edition-commercial").is_file()
        grant, account = None, None
        try:
            grant = json.loads((self.root / "account-grant.json").read_text())
            account = verify_grant(grant, json.loads((RESOURCES / "license-public.json").read_text()))
        except (OSError, ValueError):
            pass
        engines = {}
        try:
            for image in [ODX_IMAGE, LOCAL_IMAGE]:
                result = subprocess.run([*self.docker, "image", "inspect", image, "--format", "{{.Id}}"],
                                        stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
                engines[image] = result.returncode == 0
            available = subprocess.run([*self.docker, "info", "--format", "{{.OSType}}"], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            docker = available.returncode == 0 and available.stdout.strip() == b"linux"
        except (OSError, subprocess.SubprocessError):
            docker = False
        return {"commercial": commercial, "account": account, "docker": docker, "engines": engines,
                "gaussian_device": self.gaussian_device}

    def activate_installation(self, email, password):
        return activate(email, password, self.root, json.loads((RESOURCES / "license-public.json").read_text()))

    def provision_engines(self):
        status = self.installation_status()
        if not status["docker"]:
            raise ValueError("Start Docker with Linux containers, then retry. On Windows, install Docker Desktop and enable WSL 2.")
        if not status["account"]:
            raise ValueError("Activate this official installation first. Source builds can build the engines with scripts/provision-engines.sh.")

        def provision(progress, canceled):
            from mosaicearth_sync import checksum, CHUNK
            expected = json.loads((RESOURCES / "engine-payload.json").read_text())
            grant = json.loads((self.root / "account-grant.json").read_text())
            client = Client(canceled=canceled, progress=progress)
            client.token = client.request("POST", "/api/v2/desktop/provision-session", {"grant": grant})["token"]
            authorized_at = time.monotonic()
            archive = self.root / expected["filename"]
            offset = archive.stat().st_size if archive.exists() else 0
            if offset > expected["bytes"]:
                archive.unlink()
                offset = 0
            with archive.open("ab") as stream:
                while offset < expected["bytes"]:
                    # A slow first install can outlast a provisioning session.
                    # Renew using the signed device grant, never a saved password.
                    if time.monotonic() - authorized_at >= 1800:
                        client.token = client.request('POST', '/api/v2/desktop/provision-session', {'grant': grant})['token']
                        authorized_at = time.monotonic()
                    end = min(offset + CHUNK, expected["bytes"]) - 1
                    block = client.download_range('/api/v2/desktop/engine-bundle', offset, end, expected['bytes'])
                    stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                    offset += len(block)
                    progress(offset, expected["bytes"], "Downloading local NodeODX and OpenSplat engines")
            progress(offset, offset, "Verifying engine checksums")
            if checksum(archive) != expected["sha256"]:
                archive.unlink()
                raise ValueError("Engine checksum mismatch; retry the download")
            progress(offset, offset, "Installing verified Docker images")
            with (self.root / "engine-install.log").open("wb") as log:
                self.docker_run(["load", "--input", str(archive)], log, timeout=1800)
            for name, expected_id in expected["image_ids"].items():
                actual = subprocess.check_output([*self.docker, "image", "inspect", name, "--format", "{{.Id}}"], stdin=subprocess.DEVNULL, timeout=20).decode().strip()
                if actual != expected_id:
                    raise ValueError("Installed engine digest mismatch")
            return {"engines_installed": True}
        return self.start_transfer("engine-installation", provision)

    def docker_run(self, args, log=None, timeout=45, check=True):
        return subprocess.run([*self.docker, *args], stdin=subprocess.DEVNULL, stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT,
                              timeout=timeout, check=check)

    @staticmethod
    def mount(source, dest, readonly=False):
        if "," in str(source):
            raise ValueError("Docker workspace paths cannot contain commas")
        return ["--mount", f"type=bind,src={source},dst={dest}" + (",readonly" if readonly else "")]

    def require_engine(self, mode):
        image = ODX_IMAGE if mode == "odx" else CUDA_IMAGE if mode == "gaussian" and self.gaussian_device == "cuda" else LOCAL_IMAGE
        try:
            info = subprocess.run([*self.docker, "info", "--format", "{{.OSType}}"],
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            if info.returncode or info.stdout.strip() != b"linux":
                raise ValueError("Start Docker with Linux containers, then retry. See Installation and local engines.")
            installed = subprocess.run([*self.docker, "image", "inspect", image, "--format", "{{.Id}}"],
                                       stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            if installed.returncode:
                raise ValueError("The required local engine is not installed. Open Installation and local engines before retrying.")
        except (OSError, subprocess.SubprocessError):
            raise ValueError("Docker is unavailable. Install and start Docker with Linux containers, then retry.") from None

    def run(self, project_id, mode, preset="standard"):
        if (RESOURCES / "edition-commercial").is_file():
            try:
                verify_grant(json.loads((self.root / "account-grant.json").read_text()),
                             json.loads((RESOURCES / "license-public.json").read_text()))
            except (OSError, ValueError):
                raise ValueError("Activate or renew this official installation with your purchasing account") from None
        if mode not in {"gaussian", "odx", "raster"}:
            raise ValueError("Unknown local engine")
        if preset not in {"fast", "standard"}:
            raise ValueError("Unknown photogrammetry preset")
        project = self.project(project_id)
        if project["state"] == "running":
            raise ValueError("This project is already processing")
        if self.transfers.get(project_id, {}).get("status") == "running":
            raise ValueError("Pause the synchronization before changing local results")
        self.require_engine(mode)
        with self.db() as db:
            changed = db.execute(
                "UPDATE project SET state='running',mode=?,result=NULL,error=NULL WHERE id=? AND state!='running'",
                (mode, project_id),
            ).rowcount
        if not changed:
            raise ValueError("This project is already processing")
        threading.Thread(target=self.process, args=(project["id"], mode, preset), daemon=True).start()

    def process(self, project_id, mode, preset="standard"):
        project = self.root / project_id
        work = project / uuid.uuid4().hex
        state, error = "complete", None
        job_name = "me-desktop-" + uuid.uuid4().hex
        user_args = ["--user", f"{os.getuid()}:{os.getgid()}"] if os.name != "nt" else []
        runner = ["/usr/bin/python3", "/desktop/run_engine.py", mode]
        try:
            work.mkdir()
            script_dir = self.mount(ROOT / "src", "/desktop", True)
            work_mount = self.mount(work, "/work")
            with (work / "job.log").open("wb") as log:
                if mode == "raster":
                    src = project / "raster.tif"
                    if not src.exists():
                        raise ValueError("Import a georeferenced GeoTIFF first")
                    shutil.copyfile(src, work / "source.tif")
                else:
                    files = sorted(p for p in (project / "images").iterdir() if p.is_file())
                    if len(files) < (4 if mode == "gaussian" else 5):
                        raise ValueError("Import more overlapping photos first")
                    (work / "images").mkdir()
                    for photo in files:
                        shutil.copyfile(photo, work / "images" / photo.name)
                if mode == "odx":
                    token = secrets.token_hex(20)
                    self.docker_run(["run", "-d", "--rm", "--pull=never", "--name", job_name,
                                     "--network", "none", *script_dir, *work_mount, ODX_IMAGE,
                                     "--max-concurrency", "1", "--token", token], log)
                    try:
                        self.docker_run(["exec", *user_args, job_name, *runner, "--token", token, "--preset", preset], log, timeout=14700)
                    finally:
                        self.docker_run(["stop", job_name], log, check=False)
                    with zipfile.ZipFile(work / "all.zip") as archive:
                        if not any(Path(n).name == "odm_orthophoto.tif" for n in archive.namelist()):
                            raise ValueError("The local reconstruction did not produce an orthophoto")
                else:
                    use_cuda = mode == "gaussian" and self.gaussian_device == "cuda"
                    gpu_args = ["--gpus", "all"] if use_cuda else []
                    self.docker_run(["run", "--rm", "--pull=never", "--network", "none", "--name", job_name,
                                     *gpu_args, *user_args, *script_dir, *work_mount,
                                     CUDA_IMAGE if use_cuda else LOCAL_IMAGE, *runner,
                                     "--steps", str(self.steps), "--device", "cuda" if use_cuda else "cpu"], log, timeout=9400)
        except (OSError, subprocess.SubprocessError, ValueError, zipfile.BadZipFile) as exc:
            state = "failed"
            error = str(exc) if isinstance(exc, ValueError) else f"Local processing failed ({type(exc).__name__}). See job.log."
            try:
                self.docker_run(["stop", job_name], subprocess.DEVNULL, timeout=20, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        finally:
            with self.db() as db:
                db.execute("UPDATE project SET state=?,result=?,error=? WHERE id=?", (state, work.name, error, project_id))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_bytes(self, body, mime, status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if self.headers.get('Origin') == self.server.platform.url:
            self.send_header('Access-Control-Allow-Origin', self.server.platform.url)
            self.send_header('Vary', 'Origin')
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, status=200):
        self.send_bytes(json.dumps(obj).encode(), "application/json", status)

    def authorize(self):
        if self.headers.get("Host") != self.server.host:
            raise ValueError("Invalid host")
        if self.command != "GET" and (self.headers.get("Origin") not in {'http://' + self.server.host, self.server.platform.url}
                                     or self.headers.get("X-Mosaic-Workspace") != self.server.token):
            raise ValueError("Invalid workspace request")
        return self.server.workspace

    def do_OPTIONS(self):
        if self.headers.get('Host') != self.server.host or self.headers.get('Origin') != self.server.platform.url:
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', self.server.platform.url)
        self.send_header('Access-Control-Allow-Methods', 'POST')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Mosaic-Workspace')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        try:
            workspace = self.authorize()
            path = urlsplit(self.path).path
            if path == '/api/platform':
                self.json(self.server.platform.snapshot())
                return
            if path == "/api/projects":
                with workspace.db() as db:
                    self.json([dict(row) for row in db.execute("SELECT * FROM project ORDER BY rowid DESC")])
                return
            if path == "/api/transfers":
                with workspace.transfer_lock:
                    self.json(workspace.transfers)
                return
            if path == "/api/cloud/catalog":
                self.json(workspace.cloud_catalog())
                return
            if path == "/api/cloud/status":
                with workspace.cloud_lock:
                    self.json({"user": (workspace.cloud_session or {}).get("user")})
                return
            if path == "/api/installation":
                self.json(workspace.installation_status())
                return
            if path.startswith("/api/files/"):
                pid = path.rsplit("/", 1)[-1]
                workspace.project(pid)
                root = workspace.root / pid
                self.json([p.relative_to(root).as_posix() for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()])
                return
            if path.startswith("/project-files/"):
                _, _, pid, relative = path.split("/", 3)
                workspace.project(pid)
                root = workspace.root / pid
                file = (root / unquote(relative)).resolve()
                if not file.is_relative_to(root) or not file.is_file():
                    raise ValueError("Unknown file")
            elif path.startswith("/results/"):
                _, _, pid, name = path.split("/", 3)
                result = workspace.project(pid)["result"]
                if not result or name not in {"job.log", "all.zip", "preview.png", "bounds.json", "colmap.log",
                                              "output/model.ply", "output/model.spz", "output/model.splat",
                                              "output/gaussian-3dtiles.zip", "output/benchmark.json", "output/opensplat.log"}:
                    raise ValueError("Unknown file")
                file = workspace.root / pid / result / name
            elif path == "/":
                if self.server.platform.snapshot()['status'] == 'ready':
                    self.send_response(303)
                    self.send_header('Location', self.server.platform.url + '/en/Map')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                page = (RESOURCES / "setup.html").read_text(encoding="utf-8").replace("__WORKSPACE_TOKEN__", self.server.token)
                self.send_bytes(page.encode(), "text/html; charset=utf-8")
                return
            else:
                file = (RESOURCES / unquote(path).lstrip("/")).resolve()
                if not file.is_relative_to(RESOURCES):
                    raise ValueError("Unknown file")
            if not file.is_file():
                self.json({"error": "File unavailable"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(file.stat().st_size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if path.startswith(("/project-files/", "/results/")):
                # Untrusted synchronized HTML/SVG must never execute in the workspace origin.
                if file.suffix.lower() in {".html", ".htm", ".svg", ".xhtml", ".js"}:
                    self.send_header("Content-Disposition", "attachment")
                self.send_header("Content-Security-Policy", "sandbox")
            self.end_headers()
            with file.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile)
        except ValueError as exc:
            self.json({"error": str(exc)}, 400)
        except (ConnectionError, BrokenPipeError):
            return

    def do_POST(self):
        try:
            workspace = self.authorize()
            size = int(self.headers.get("Content-Length") or 0)
            if self.path == '/api/platform/start':
                self.json(self.server.platform.start())
                return
            if self.path == '/api/platform/open-folder':
                folder = workspace.root / 'platform' / 'blobs'
                folder.mkdir(parents=True, exist_ok=True)
                if os.name == 'nt':
                    os.startfile(str(folder))
                else:
                    subprocess.Popen(['xdg-open', str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.json({'ok': True})
                return
            if self.path == "/api/projects" and 0 < size <= 8192:
                self.json({"id": workspace.create(json.loads(self.rfile.read(size))["name"])}, 201)
                return
            if self.path == "/api/cloud/sign-in" and 0 < size <= 8192:
                data = json.loads(self.rfile.read(size))
                self.json(workspace.cloud_signin(data["email"], data["password"]))
                return
            if self.path == "/api/installation/activate" and 0 < size <= 8192:
                data = json.loads(self.rfile.read(size))
                self.json(workspace.activate_installation(data["email"], data["password"]))
                return
            if self.path == "/api/installation/engines":
                self.json(workspace.provision_engines())
                return
            if self.path == "/api/cloud/sign-out":
                workspace.cloud_signout()
                self.json({"ok": True})
                return
            if self.path == "/api/cloud/download" and 0 < size <= 8192:
                data = json.loads(self.rfile.read(size))
                self.json(workspace.pull_project(str(data.get("snapshot_id") or ""), int(data.get("project_id") or 0)))
                return
            if self.path == "/api/cloud/cancel-upload" and 0 < size <= 8192:
                data = json.loads(self.rfile.read(size))
                sid = str(data["snapshot_id"])
                if not re.fullmatch("[0-9a-f]{64}", sid):
                    raise ValueError("Invalid snapshot ID")
                self.json(workspace.cloud_client().request("DELETE", "/api/v2/sync/sessions/" + sid))
                return
            if self.path == "/api/transfers/pause" and 0 < size <= 8192:
                data = json.loads(self.rfile.read(size))
                with workspace.transfer_lock:
                    workspace.transfers[data["id"]]["pause"] = True
                self.json({"ok": True})
                return
            _, api, pid, operation = urlsplit(self.path).path.split("/")
            if api != "api":
                raise ValueError("Unknown operation")
            project = workspace.project(pid)
            if operation == "sync":
                self.json(workspace.sync_project(pid))
                return
            if operation.startswith("run-"):
                data = json.loads(self.rfile.read(size)) if 0 < size <= 8192 else {}
                workspace.run(pid, operation[4:], data.get("preset", "standard"))
            elif operation in {"photo", "raster"} and 0 < size <= MAX_UPLOAD and project["state"] != "running":
                if workspace.transfers.get(pid, {}).get("status") == "running":
                    raise ValueError("Pause the synchronization before importing files")
                suffix = Path(unquote(self.headers.get("X-File-Name", ""))).suffix.lower()
                if suffix not in (IMAGE_TYPES if operation == "photo" else {".tif", ".tiff"}):
                    raise ValueError("Unsupported input file")
                folder = workspace.root / pid
                original_name = Path(unquote(self.headers.get("X-File-Name", ""))).name
                if any(c in original_name for c in "\\/:\0") or original_name in {".", ".."}:
                    raise ValueError("Invalid file name")
                file = folder / ("raster.tif" if operation == "raster" else "images/" + original_name)
                temp = file.with_suffix(f".{uuid.uuid4().hex}.tmp")
                try:
                    with temp.open("xb") as fp:
                        while size:
                            block = self.rfile.read(min(size, 1024 * 1024))
                            if not block:
                                raise ValueError("Upload was interrupted")
                            fp.write(block)
                            size -= len(block)
                    if operation == "photo" and file.exists():
                        if checksum(temp) != checksum(file):
                            raise ValueError("A different photo already has this name. The existing original is preserved; rename the new photo.")
                    else:
                        if operation == "raster":
                            archive = folder / "raster-inputs" / uuid.uuid4().hex / original_name
                            archive.parent.mkdir(parents=True)
                            shutil.copyfile(temp, archive)
                        temp.replace(file)
                finally:
                    temp.unlink(missing_ok=True)
            else:
                raise ValueError("Invalid operation or upload size")
            self.json({"ok": True}, 202)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.json({"error": str(exc)}, 400)


def open_workspace_window(url, data):
    if os.name == 'nt':
        for variable in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA'):
            base = os.environ.get(variable)
            edge = Path(base) / 'Microsoft/Edge/Application/msedge.exe' if base else None
            if edge and edge.is_file():
                subprocess.Popen([str(edge), '--app=' + url, '--no-first-run',
                                  '--user-data-dir=' + str(Path(data).resolve() / 'browser')],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    webbrowser.open(url)


def main():
    args = argparse.ArgumentParser()
    base = Path(os.getenv("LOCALAPPDATA", str(Path.home())) if os.name == "nt"
                else os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    args.add_argument("--data", type=Path, default=base / "mosaicearth-desktop")
    args.add_argument("--port", type=int, default=19754)
    args.add_argument("--no-browser", action="store_true")
    args.add_argument("--gaussian-steps", type=int, default=2000, choices=range(1, 12001), metavar="1..12000")
    args.add_argument("--gaussian-device", choices=("cpu", "cuda"), default="cpu")
    options = args.parse_args()
    from mosaicearth_launcher import PlatformLauncher
    url = f'http://127.0.0.1:{options.port}'
    try:
        host = ThreadingHTTPServer(("127.0.0.1", options.port), Handler)
    except OSError as exc:
        if exc.errno not in (errno.EADDRINUSE, 10048):
            raise
        # Opening the shortcut twice must reopen the existing workspace, not
        # start another worker or silently fail in pythonw.exe.
        import urllib.request
        with urllib.request.urlopen(url + '/api/platform', timeout=4) as response:
            state = json.load(response)
        if state.get('url') != f'http://127.0.0.1:{options.port + 3}':
            raise ValueError('This local port belongs to another application.') from exc
        if not options.no_browser:
            open_workspace_window(url, options.data)
        return
    with host as server:
        server.workspace = Workspace(options.data, options.gaussian_steps, options.gaussian_device)
        server.token = secrets.token_urlsafe(32)
        server.host = f"127.0.0.1:{server.server_port}"
        # Keep the local control capability across launcher restarts so a running
        # platform can still open its own folder. It grants no cloud access.
        capability = server.workspace.root / 'launcher-capability'
        if capability.exists():
            server.token = capability.read_text().strip()
        else:
            capability.write_text(server.token)
            capability.chmod(0o600)
        server.platform = PlatformLauncher(server.workspace, RESOURCES, options.port + 3, options.port, server.token)
        server.platform.start()
        url = "http://" + server.host
        print(url, flush=True)
        if not options.no_browser:
            open_workspace_window(url, options.data)
        server.serve_forever()


if __name__ == "__main__":
    main()
