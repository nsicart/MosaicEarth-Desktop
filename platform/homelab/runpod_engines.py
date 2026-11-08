"""Job-owned, authenticated RunPod v2 Pods for the new processing engines."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import math
import os
import re
import secrets
import sqlite3
import time

import httpx

API = "https://api.runpod.io/v2"
NODEODX_IMAGE = "webodm/nodeodx@sha256:77e913d50929885678b59cf7c0d82aa04289887a025d0516737e5c0f701bedf3"
PORTS = {"nodeodx": 3000, "opensplat": 8080}
OPENSPLAT_GPU_IDS = {
    "NVIDIA A40", "NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB", "NVIDIA A10",
    "NVIDIA GeForce RTX 3090", "NVIDIA GeForce RTX 4090", "NVIDIA L4", "NVIDIA L40",
    "NVIDIA L40S", "NVIDIA RTX A4000", "NVIDIA RTX A4500", "NVIDIA RTX A5000",
    "NVIDIA RTX A6000", "NVIDIA RTX 4000 Ada Generation", "NVIDIA RTX 4500 Ada Generation",
    "NVIDIA RTX 5000 Ada Generation", "NVIDIA RTX 6000 Ada Generation", "Tesla T4",
}


class RunPodEngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnginePod:
    job_key: str
    engine: str
    pod_id: str
    token: str
    expires_at: float

    @property
    def endpoint(self):
        return f"https://{self.pod_id}-{PORTS[self.engine]}.proxy.runpod.net"


class RunPodEngineJobs:
    def __init__(self, data_root: Path, api_key: str):
        self.path = Path(data_root) / "db" / "runpod-engine-jobs.sqlite3"
        self.api_key = api_key

    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            job_key TEXT PRIMARY KEY, engine TEXT NOT NULL, name TEXT UNIQUE NOT NULL,
            token TEXT NOT NULL, pod_id TEXT, state TEXT NOT NULL, expires_at REAL NOT NULL,
            reserved_usd REAL NOT NULL, created_at REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS node_uploads (
            job_key TEXT PRIMARY KEY, attempt INTEGER NOT NULL, task_id TEXT NOT NULL
        )""")
        return db

    def _request(self, method, path, **kwargs):
        if not self.api_key:
            raise RunPodEngineError("RunPod credentials are unavailable.")
        try:
            with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
                return client.request(method, API + path, headers={"Authorization": f"Bearer {self.api_key}"}, **kwargs)
        except httpx.HTTPError:
            raise RunPodEngineError("RunPod request did not receive a response.") from None

    def _json(self, method, path, **kwargs):
        response = self._request(method, path, **kwargs)
        if not response.is_success:
            raise RunPodEngineError(f"RunPod refused {method} {path.split('?')[0]} (HTTP {response.status_code}).")
        try:
            return response.json()
        except ValueError:
            raise RunPodEngineError("RunPod returned malformed JSON.") from None

    def _gpu_candidates(self, maximum_rate, engine="nodeodx"):
        data = self._json("GET", "/catalog/gpus?include=AVAILABILITY&product=POD")
        required_id = os.environ.get(f"MOSAICEARTH_{engine.upper()}_RUNPOD_GPU_ID")
        candidates = []
        for gpu in data.get("gpus", []):
            rate = (gpu.get("price") or {}).get("secure")
            if (gpu.get("id") and str(gpu.get("manufacturer") or "NVIDIA").upper() == "NVIDIA"
                    and (not required_id or gpu["id"] == required_id)
                    and (engine != "opensplat" or gpu["id"] in OPENSPLAT_GPU_IDS)
                    and int(gpu.get("memory") or 0) >= 16 and gpu.get("availability") in {"LOW", "MEDIUM", "HIGH"}
                    and isinstance(rate, (int, float)) and 0 < rate <= maximum_rate):
                candidates.append((float(rate), gpu["id"]))
        if not candidates:
            raise RunPodEngineError("No available GPU meets the configured hourly rate and VRAM limits.")
        return sorted(candidates)

    def _gpu(self, maximum_rate, engine="nodeodx"):
        return self._gpu_candidates(maximum_rate, engine)[0]

    @staticmethod
    def policy():
        try:
            budget = float(os.environ["MOSAICEARTH_ENGINE_POD_BUDGET_USD"])
            maximum_rate = float(os.environ["MOSAICEARTH_ENGINE_POD_MAX_GPU_USD_HOUR"])
            maximum_seconds = int(os.environ.get("MOSAICEARTH_ENGINE_POD_MAX_SECONDS", "3600"))
        except (KeyError, ValueError):
            raise RunPodEngineError("Configure an approved RunPod budget and hourly rate before starting jobs.") from None
        if not all(math.isfinite(x) and x > 0 for x in (budget, maximum_rate)) or not 60 <= maximum_seconds <= 14400:
            raise RunPodEngineError("RunPod budget, price or duration limits are invalid.")
        return budget, maximum_rate, maximum_seconds

    @staticmethod
    def _pod(row):
        return EnginePod(row["job_key"], row["engine"], row["pod_id"], row["token"], row["expires_at"])

    def _lookup(self, name):
        data = self._json("GET", "/pods")
        pods = data.get("pods", []) if isinstance(data, dict) else data
        return next((pod.get("id") for pod in pods if pod.get("name") == name), None)

    def start(self, job_key, engine):
        if engine not in PORTS:
            raise ValueError("Unsupported RunPod processing engine")
        image = os.environ.get(
            f"MOSAICEARTH_{engine.upper()}_RUNPOD_IMAGE",
            NODEODX_IMAGE if engine == "nodeodx" else "",
        )
        if not re.fullmatch(r"[a-zA-Z0-9./_:-]+@sha256:[a-f0-9]{64}", image):
            raise RunPodEngineError("Configure a registry image pinned to a verified digest.")
        budget, max_rate, seconds = self.policy()
        with closing(self._db()) as db:
            previous = db.execute("SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        if previous:
            if previous["state"] in {"running", "uploading"} and previous["expires_at"] > time.time() and previous["engine"] == engine:
                return self._pod(previous)
            raise RunPodEngineError("This RunPod job already has an intent. The provisioning request cannot be replayed.")

        candidates = self._gpu_candidates(max_rate, engine)[:4]
        rate, gpu_id = candidates[0]
        now = time.time()
        # Reserve the full lifetime, a disk allowance and one minute for provider cleanup.
        reservation = (max(price for price, _ in candidates) + 0.15) * (seconds + 60) / 3600
        name = f"mosaicearth-{engine}-{secrets.token_hex(12)}"
        token = secrets.token_urlsafe(32)
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            reserved = db.execute("SELECT COALESCE(SUM(reserved_usd),0) FROM jobs").fetchone()[0]
            if reserved + reservation > budget:
                raise RunPodEngineError("The RunPod budget cannot reserve this job's maximum cost.")
            try:
                db.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, NULL, 'starting', ?, ?, ?)",
                           (job_key, engine, name, token, now + seconds, reservation, now))
            except sqlite3.IntegrityError:
                raise RunPodEngineError("A concurrent worker already reserved this RunPod job.") from None

        payload = {"name": name, "image": image, "gpu": {"id": gpu_id, "count": 1},
                   "cloud": "SECURE", "disk": 40, "ports": [f"{PORTS[engine]}/http"]}
        if engine == "nodeodx":
            payload["gpu"].update(minRamPerGpu=16, minVcpuCountPerGpu=4)
            payload["cmd"] = ["--token", token, "--max-concurrency", "4"]
            payload["env"] = {"OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4"}
        else:
            payload["env"] = {"MOSAICEARTH_ENGINE_TOKEN": token}
        registry = os.environ.get(f"MOSAICEARTH_{engine.upper()}_RUNPOD_REGISTRY")
        if registry:
            payload["registry"] = registry
        confirmed_by_lookup = None
        for index, (rate, gpu_id) in enumerate(candidates):
            payload["gpu"]["id"] = gpu_id
            try:
                response = self._request("POST", "/pods", json=payload)
            except RunPodEngineError:
                # A lost response is ambiguous. Reconcile the existing intent below.
                response = None
                break
            if response.status_code != 400 or index == len(candidates) - 1:
                break
            # A GPU can disappear between the catalog read and provisioning.
            # Check the unique provider name before requesting another GPU.
            confirmed_by_lookup = self._lookup(name)
            if confirmed_by_lookup:
                break
            with closing(self._db()) as db:
                current = db.execute("SELECT state FROM jobs WHERE job_key=?", (job_key,)).fetchone()
            if not current or current["state"] != "starting":
                raise RunPodEngineError("Provisioning was canceled.")
        if not confirmed_by_lookup and response is not None and response.status_code in (400, 401, 402, 403, 404, 422, 429):
            with closing(self._db()) as db, db:
                db.execute("UPDATE jobs SET state='closed', reserved_usd=0, token='' WHERE job_key=?", (job_key,))
            raise RunPodEngineError(f"RunPod refused provisioning (HTTP {response.status_code}).")
        try:
            created = response.json() if response is not None and response.is_success else {}
            pod_id = confirmed_by_lookup or created.get("id")
        except (ValueError, AttributeError):
            created = {}
            pod_id = None
        if not pod_id:
            pod_id = self._lookup(name)
        if not pod_id:
            raise RunPodEngineError("RunPod job could not be confirmed. Its intent is retained for cleanup.")
        if not isinstance(pod_id, str) or not re.fullmatch(r"[a-z0-9]{8,32}", pod_id):
            raise RunPodEngineError("RunPod returned an unexpected Pod identifier.")
        with closing(self._db()) as db, db:
            db.execute("UPDATE jobs SET pod_id=? WHERE job_key=?", (pod_id, job_key))
        try:
            actual_rate = created.get("cost")
            if actual_rate is None:
                actual_rate = self._json("GET", f"/pods/{pod_id}").get("cost")
            if not isinstance(actual_rate, (int, float)) or not 0 < actual_rate <= rate:
                raise RunPodEngineError("The provisioned Pod rate exceeds its reservation or could not be verified.")
        except RunPodEngineError:
            self.finish(job_key)
            raise
        with closing(self._db()) as db, db:
            changed = db.execute("UPDATE jobs SET state='running' WHERE job_key=? AND state='starting' AND expires_at>?",
                                 (job_key, time.time())).rowcount
        if not changed:
            # The reaper may have closed the intent while the provider was starting the Pod.
            with closing(self._db()) as db, db:
                db.execute("UPDATE jobs SET state='closing', pod_id=? WHERE job_key=?", (pod_id, job_key))
            self.finish(job_key)
            raise RunPodEngineError("The RunPod job expired during provisioning.")
        return EnginePod(job_key, engine, pod_id, token, now + seconds)

    def find(self, job_key):
        if not self.path.is_file():
            return None
        with closing(self._db()) as db:
            row = db.execute("SELECT * FROM jobs WHERE job_key=? AND state IN ('running','uploading')", (job_key,)).fetchone()
        return self._pod(row) if row else None

    def claim_upload(self, job_key):
        with closing(self._db()) as db, db:
            return bool(db.execute(
                "UPDATE jobs SET state='uploading' WHERE job_key=? AND state IN ('running','uploading') AND expires_at>?",
                (job_key, time.time()),
            ).rowcount)

    def node_upload_task(self, job_key):
        with closing(self._db()) as db:
            row = db.execute("SELECT task_id FROM node_uploads WHERE job_key=?", (job_key,)).fetchone()
            return row[0] if row else None

    def begin_node_upload(self, job_key):
        """A fresh uncommitted Node upload avoids duplicate files after response loss.

        The persistent background-job lock must be held by the caller. The
        stored UUID is checked for an existing committed task before restarting.
        """
        import uuid
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state, expires_at FROM jobs WHERE job_key=?", (job_key,)).fetchone()
            if not row or row["state"] not in ("running", "uploading") or row["expires_at"] <= time.time():
                raise RunPodEngineError("The NodeODX upload no longer has an active pod.")
            previous = db.execute("SELECT attempt FROM node_uploads WHERE job_key=?", (job_key,)).fetchone()
            attempt = (previous[0] if previous else 0) + 1
            if attempt > 3:
                raise RunPodEngineError("NodeODX transfer failed after three attempts. Originals are preserved.")
            task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{job_key}/upload/{attempt}"))
            db.execute("INSERT INTO node_uploads VALUES (?, ?, ?) ON CONFLICT(job_key) DO UPDATE SET attempt=excluded.attempt, task_id=excluded.task_id",
                       (job_key, attempt, task_id))
            db.execute("UPDATE jobs SET state='uploading' WHERE job_key=?", (job_key,))
            return task_id

    def cancel(self, job_key, engine):
        if engine not in PORTS:
            raise ValueError("Unsupported RunPod processing engine")
        with closing(self._db()) as db, db:
            now = time.time()
            db.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?, ?, '', NULL, 'closed', ?, 0, ?)",
                       (job_key, engine, "canceled-" + secrets.token_hex(12), now, now))
        self.finish(job_key)

    def finish(self, job_key):
        with closing(self._db()) as db, db:
            row = db.execute("SELECT * FROM jobs WHERE job_key=? AND state!='closed'", (job_key,)).fetchone()
            if not row:
                return
            db.execute("UPDATE jobs SET state='closing' WHERE job_key=?", (job_key,))
        pod_id = row["pod_id"] or self._lookup(row["name"])
        if pod_id:
            if not re.fullmatch(r"[a-z0-9]{8,32}", pod_id):
                raise RunPodEngineError("RunPod returned an unexpected Pod identifier during cleanup.")
            result = self._request("DELETE", f"/pods/{pod_id}")
            if result.status_code == 202:
                result = self._request("GET", f"/pods/{pod_id}")
                if result.status_code != 404:
                    raise RunPodEngineError("RunPod termination is still pending.")
            elif result.status_code not in (200, 204, 404):
                raise RunPodEngineError("RunPod did not confirm Pod termination.")
        elif time.time() < row["expires_at"]:
            return
        with closing(self._db()) as db, db:
            db.execute("UPDATE jobs SET state='closed', token='' WHERE job_key=?", (job_key,))

    def reap(self):
        if not self.path.is_file():
            return
        with closing(self._db()) as db:
            keys = db.execute("""SELECT job_key FROM jobs WHERE state='closing'
                OR (state!='closed' AND expires_at<=?)""", (time.time(),)).fetchall()
        for row in keys:
            try:
                self.finish(row["job_key"])
            except RunPodEngineError:
                # Keep the durable closing record for the next worker iteration.
                continue
