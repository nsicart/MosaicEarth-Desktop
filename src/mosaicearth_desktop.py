"""MosaicEarth Desktop's local workspace and launcher."""
import argparse
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
import threading
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "resources"
LOCAL_IMAGE = "mosaicearth-desktop/local-engine:0.1.0"
ODX_IMAGE = "mosaicearth-desktop/nodeodx:0.1.0"
MAX_UPLOAD = 3 * 1024**3
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
PROJECT_ID = re.compile(r"[0-9a-f]{32}")


class Workspace:
    def __init__(self, root, steps=2000):
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.steps = steps
        self.cloud_session = None
        self.cloud_lock = threading.Lock()
        self.docker = shlex.split(os.environ.get("ME_DOCKER", "docker"))
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

    @staticmethod
    def cloud(path, payload):
        connection = http.client.HTTPSConnection("app.mosaicearth.app", timeout=15)
        try:
            connection.request("POST", "/api/v2/desktop/" + path, json.dumps(payload),
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"MosaicEarth Cloud returned HTTP {response.status}")
            data = json.loads(response.read(2 * 1024**2))
            if data.get("ok") is not True:
                raise ValueError("MosaicEarth Cloud rejected the request")
            return data
        except (OSError, http.client.HTTPException) as exc:
            raise ValueError("MosaicEarth Cloud is unreachable") from exc
        finally:
            connection.close()

    def cloud_signin(self, email, password):
        data = self.cloud("cloud-auth", {"email": email, "password": password})
        uid = int(data["user"]["id"])
        with self.cloud_lock:
            self.cloud_session = {"token": data["token"], "cloud_uid": uid}
        return {"user": data["user"]["email"]}

    def sync_project(self, project_id):
        project = self.project(project_id)
        with self.cloud_lock:
            session = self.cloud_session
            if not session:
                raise ValueError("Sign in to MosaicEarth Cloud before syncing")
            uid = session["cloud_uid"]
            with self.db() as db:
                existing = db.execute("SELECT cloud_id FROM cloud_sync WHERE project_id=? AND user_id=?", (project_id, uid)).fetchone()
                if existing:
                    if existing["cloud_id"]:
                        return {"cloud_id": existing["cloud_id"]}
                    raise ValueError("Sync outcome is uncertain. Check MyEarth before retrying this project.")
                db.execute("INSERT INTO cloud_sync VALUES (?,?,NULL)", (project_id,uid))
            data = self.cloud("receive-sync", {**session, "type": "project", "project": {"name": project["name"]}})
            cloud_id = int(data["cloudProjectId"])
            with self.db() as db:
                db.execute("UPDATE cloud_sync SET cloud_id=? WHERE project_id=? AND user_id=?", (cloud_id,project_id,uid))
            return {"cloud_id": cloud_id}

    def docker_run(self, args, log=None, timeout=45, check=True):
        return subprocess.run([*self.docker, *args], stdout=log, stderr=subprocess.STDOUT,
                              timeout=timeout, check=check)

    @staticmethod
    def mount(source, dest, readonly=False):
        if "," in str(source):
            raise ValueError("Docker workspace paths cannot contain commas")
        return ["--mount", f"type=bind,src={source},dst={dest}" + (",readonly" if readonly else "")]

    def run(self, project_id, mode):
        if mode not in {"gaussian", "odx", "raster"}:
            raise ValueError("Unknown local engine")
        project = self.project(project_id)
        with self.db() as db:
            changed = db.execute(
                "UPDATE project SET state='running',mode=?,result=NULL,error=NULL WHERE id=? AND state!='running'",
                (mode, project_id),
            ).rowcount
        if not changed:
            raise ValueError("This project is already processing")
        threading.Thread(target=self.process, args=(project["id"], mode), daemon=True).start()

    def process(self, project_id, mode):
        project = self.root / project_id
        work = project / uuid.uuid4().hex
        state, error = "complete", None
        job_name = "me-desktop-" + uuid.uuid4().hex
        user_args = ["--user", f"{os.getuid()}:{os.getgid()}"] if os.name != "nt" else []
        runner = ["python3", "/desktop/run_engine.py", mode]
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
                        self.docker_run(["exec", *user_args, job_name, *runner, "--token", token], log, timeout=14700)
                    finally:
                        self.docker_run(["stop", job_name], log, check=False)
                    with zipfile.ZipFile(work / "all.zip") as archive:
                        if not any(Path(n).name == "odm_orthophoto.tif" for n in archive.namelist()):
                            raise ValueError("The local reconstruction did not produce an orthophoto")
                else:
                    self.docker_run(["run", "--rm", "--pull=never", "--network", "none", "--name", job_name,
                                     *user_args, *script_dir, *work_mount, LOCAL_IMAGE, *runner,
                                     "--steps", str(self.steps)], log, timeout=9400)
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
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, status=200):
        self.send_bytes(json.dumps(obj).encode(), "application/json", status)

    def authorize(self):
        if self.headers.get("Host") != self.server.host:
            raise ValueError("Invalid host")
        if self.command != "GET" and (self.headers.get("Origin") != "http://" + self.server.host
                                     or self.headers.get("X-Mosaic-Workspace") != self.server.token):
            raise ValueError("Invalid workspace request")
        return self.server.workspace

    def do_GET(self):
        try:
            workspace = self.authorize()
            path = urlsplit(self.path).path
            if path == "/api/projects":
                with workspace.db() as db:
                    self.json([dict(row) for row in db.execute("SELECT * FROM project ORDER BY rowid DESC")])
                return
            if path.startswith("/results/"):
                _, _, pid, name = path.split("/", 3)
                result = workspace.project(pid)["result"]
                if not result or name not in {"job.log", "all.zip", "preview.png", "bounds.json", "colmap.log",
                                              "output/model.ply", "output/metadata.json", "output/opensplat.log"}:
                    raise ValueError("Unknown file")
                file = workspace.root / pid / result / name
            elif path == "/":
                page = (RESOURCES / "index.html").read_text().replace("__WORKSPACE_TOKEN__", self.server.token)
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
            if self.path == "/api/projects" and 0 < size <= 8192:
                self.json({"id": workspace.create(json.loads(self.rfile.read(size))["name"])}, 201)
                return
            if self.path == "/api/cloud/sign-in" and 0 < size <= 8192:
                data = json.loads(self.rfile.read(size))
                self.json(workspace.cloud_signin(data["email"], data["password"]))
                return
            if self.path == "/api/cloud/sign-out":
                with workspace.cloud_lock:
                    workspace.cloud_session = None
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
                workspace.run(pid, operation[4:])
            elif operation in {"photo", "raster"} and 0 < size <= MAX_UPLOAD and project["state"] != "running":
                suffix = Path(unquote(self.headers.get("X-File-Name", ""))).suffix.lower()
                if suffix not in (IMAGE_TYPES if operation == "photo" else {".tif", ".tiff"}):
                    raise ValueError("Unsupported input file")
                folder = workspace.root / pid
                file = folder / ("raster.tif" if operation == "raster" else f"images/{uuid.uuid4().hex}{suffix}")
                temp = file.with_suffix(f".{uuid.uuid4().hex}.tmp")
                try:
                    with temp.open("xb") as fp:
                        while size:
                            block = self.rfile.read(min(size, 1024 * 1024))
                            if not block:
                                raise ValueError("Upload was interrupted")
                            fp.write(block)
                            size -= len(block)
                    temp.replace(file)
                finally:
                    temp.unlink(missing_ok=True)
            else:
                raise ValueError("Invalid operation or upload size")
            self.json({"ok": True}, 202)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.json({"error": str(exc)}, 400)


def main():
    args = argparse.ArgumentParser()
    base = Path(os.getenv("LOCALAPPDATA", str(Path.home())) if os.name == "nt"
                else os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    args.add_argument("--data", type=Path, default=base / "mosaicearth-desktop")
    args.add_argument("--port", type=int, default=19754)
    args.add_argument("--no-browser", action="store_true")
    args.add_argument("--gaussian-steps", type=int, default=2000, choices=range(1, 12001), metavar="1..12000")
    options = args.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", options.port), Handler) as server:
        server.workspace = Workspace(options.data, options.gaussian_steps)
        server.token = secrets.token_urlsafe(32)
        server.host = f"127.0.0.1:{server.server_port}"
        url = "http://" + server.host
        print(url, flush=True)
        if not options.no_browser:
            webbrowser.open(url)
        server.serve_forever()


if __name__ == "__main__":
    main()
