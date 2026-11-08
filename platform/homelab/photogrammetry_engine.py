"""NodeODM/NodeODX v2 transport. Keep billing and result ingestion in MosaicEarth."""

import json
import math
import os
import re
import time
from pathlib import Path

import httpx


MAX_NODE_ARCHIVE_BYTES = 8 * 1024**3


def node_startup_deadline(expires_at: float, *, now: float | None = None) -> float:
    """Allow a cold OCI pull while staying inside the existing paid Pod lease."""
    now = time.time() if now is None else now
    seconds = int(os.environ.get("MOSAICEARTH_NODEODX_STARTUP_SECONDS", "1800"))
    if not 60 <= seconds <= 1800:
        raise ValueError("NodeODX startup allowance must be between 60 and 1800 seconds.")
    return min(expires_at, now + seconds)


class NodeEngineError(RuntimeError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class NodeODMAdapter:
    def __init__(self, client: httpx.Client, endpoint: str, token: str = ""):
        self.client = client
        self.endpoint = endpoint.rstrip("/")
        self.auth = {"token": token} if token else {}

    def _json(self, method, path, *, params=None, **kwargs):
        # RunPod's proxy can briefly return 404 while routing a newly started
        # pod. Retry reads only; uploads have no server-side deduplication.
        attempts = 5 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                return self._request_json(method, path, params=params, **kwargs)
            except NodeEngineError as exc:
                if not exc.retryable or attempt == attempts - 1:
                    raise
                time.sleep(min(2 ** attempt, 8))

    def _request_json(self, method, path, *, params=None, **kwargs):
        try:
            response = self.client.request(
                method, self.endpoint + path, params={**self.auth, **(params or {})}, **kwargs
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            proxy_starting = code == 404 and self.endpoint.endswith(".proxy.runpod.net")
            raise NodeEngineError(f"Node API {method} {path}: HTTP {code}",
                                  retryable=proxy_starting or code in (408, 429, 500, 502, 503, 504)) from None
        except (httpx.HTTPError, ValueError) as exc:
            # HTTP exception messages can include the token or the raw request URL.
            raise NodeEngineError(f"Node API {method} {path}: {type(exc).__name__}",
                                  retryable=isinstance(exc, httpx.TransportError)) from None
        if isinstance(data, dict) and ("error" in data or data.get("success") is False):
            raise NodeEngineError(f"Node API {method} {path}: request rejected")
        return data

    def info(self):
        data = self._json("GET", "/info", timeout=8)
        if not isinstance(data, dict) or not data.get("engine") or not data.get("engineVersion"):
            raise NodeEngineError("Node API /info did not identify the reconstruction engine")
        return data

    def options(self):
        data = self._json("GET", "/options", timeout=15)
        if not isinstance(data, list) or not all(isinstance(item, dict) and item.get("name") for item in data):
            raise NodeEngineError("Node API /options returned an invalid catalog")
        return data

    def effective_options(self, options):
        # Preserve the legacy NodeODM behavior, including server-side option validation.
        return list(options)

    @staticmethod
    def _task_id(task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-zA-Z_-]{1,128}", task_id):
            raise NodeEngineError("Invalid Node task identifier")
        return task_id

    def initialize(self, name, options, requested_id=None):
        headers = {"set-uuid": self._task_id(requested_id)} if requested_id else {}
        # /init only writes metadata. The fixed UUID makes response-loss retries
        # safe before any images are sent; never retry an unkeyed creation.
        for attempt in range(3 if requested_id else 1):
            try:
                data = self._json("POST", "/task/new/init", headers=headers, files={
                    "name": (None, name), "options": (None, json.dumps(options)),
                })
                return self._task_id(data.get("uuid") if isinstance(data, dict) else None)
            except NodeEngineError as exc:
                if not requested_id or not exc.retryable or attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    def upload(self, task_id, path: Path):
        with path.open("rb") as fp:
            data = self._json("POST", f"/task/new/upload/{self._task_id(task_id)}",
                              files={"images": (path.name, fp, "application/octet-stream")})
        if not isinstance(data, dict) or data.get("success") is not True:
            raise NodeEngineError("Node API did not acknowledge the upload")

    def commit(self, task_id, name, options):
        data = self._json("POST", f"/task/new/commit/{self._task_id(task_id)}",
                          data={"name": name, "options": json.dumps(options)})
        if not isinstance(data, dict) or data.get("uuid") != task_id:
            raise NodeEngineError("Node API did not acknowledge the task commit")

    def contains_task(self, task_id):
        tasks = self._json("GET", "/task/list", timeout=15)
        if not isinstance(tasks, list):
            raise NodeEngineError("Node API returned an invalid task list")
        return any(isinstance(task, dict) and task.get("uuid") == task_id for task in tasks)

    def status(self, task_id):
        data = self._json("GET", f"/task/{self._task_id(task_id)}/info", timeout=15)
        try:
            code = int(data["status"]["code"])
            progress = float(data.get("progress") or 0)
            if code not in (10, 20, 30, 40, 50) or not math.isfinite(progress):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise NodeEngineError("Node API returned an invalid task status") from None
        return data

    def logs(self, task_id, line=0):
        return self._json("GET", f"/task/{self._task_id(task_id)}/output", params={"line": line}, timeout=15)

    def cancel(self, task_id):
        result = self._json("POST", "/task/cancel", data={"uuid": self._task_id(task_id)})
        if not isinstance(result, dict) or result.get("success") is not True:
            raise NodeEngineError("Node API did not acknowledge cancellation")

    def download(self, task_id, destination: Path, *, parallel=False, progress=None):
        path = f"/task/{self._task_id(task_id)}/download/all.zip"
        if parallel:
            head = self.client.head(self.endpoint + path, params=self.auth, timeout=30)
            if head.is_success and head.headers.get('accept-ranges') == 'bytes':
                size = int(head.headers.get('content-length') or 0)
                if size > MAX_NODE_ARCHIVE_BYTES:
                    raise NodeEngineError("Node result archive exceeds the download limit")
                if size >= 64 * 1024**2:
                    return self._download_ranges(path, destination, size, progress, head.headers.get('etag'))
        for attempt in range(5):
            try:
                with self.client.stream("GET", self.endpoint + path, params=self.auth, timeout=600) as response:
                    response.raise_for_status()
                    received = 0
                    expected = int(response.headers.get('content-length') or 0)
                    with destination.open("wb") as target:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            received += len(chunk)
                            if received > MAX_NODE_ARCHIVE_BYTES:
                                raise NodeEngineError("Node result archive exceeds the download limit")
                            target.write(chunk)
                            if progress:
                                progress(received, expected)
                    if expected and received != expected:
                        raise NodeEngineError("Node result archive is incomplete", retryable=True)
                return
            except httpx.HTTPError as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                transient = isinstance(exc, httpx.TransportError) or status in (408, 429, 500, 502, 503, 504) or (status == 404 and self.endpoint.endswith(".proxy.runpod.net"))
                if not transient or attempt == 4:
                    detail = f"HTTP {status}" if status else type(exc).__name__
                    raise NodeEngineError(f"Node API archive download failed: {detail}", retryable=transient) from None
                time.sleep(min(2 ** attempt, 8))

    def _download_ranges(self, path, destination, size, progress, etag=None):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Lock
        import hashlib
        import os
        completed = 0
        lock = Lock()
        step = 8*1024*1024
        receipt = destination.with_suffix(destination.suffix + '.ranges.json')
        identity = {'path': path, 'size': size, 'etag': etag}
        saved = {}
        try:
            state = json.loads(receipt.read_text())
            if state.get('identity') == identity and destination.stat().st_size == size:
                with destination.open('rb') as existing:
                    for start, digest in state.get('parts', {}).items():
                        offset = int(start)
                        if offset < 0 or offset >= size or offset % step:
                            continue
                        existing.seek(offset)
                        data = existing.read(min(step, size-offset))
                        if hashlib.sha256(data).hexdigest() == digest:
                            saved[start] = digest
                            completed += len(data)
        except (OSError, ValueError, TypeError):
            saved = {}
            completed = 0
        if not saved:
            with destination.open('wb') as output:
                output.truncate(size)
        if progress:
            progress(completed, size)
        def part(bounds):
            nonlocal completed
            start, end = bounds
            if str(start) in saved:
                return
            for attempt in range(5):
                try:
                    with self.client.stream('GET', self.endpoint + path, params=self.auth,
                            headers={'Range': f'bytes={start}-{end}'}, timeout=60) as response:
                        response.raise_for_status()
                        if response.status_code != 206 or response.headers.get('content-range') != f'bytes {start}-{end}/{size}':
                            raise NodeEngineError('Node did not acknowledge the requested result range')
                        received = 0
                        digest = hashlib.sha256()
                        with destination.open('r+b') as output:
                            output.seek(start)
                            for chunk in response.iter_bytes(chunk_size=1024*1024):
                                received += len(chunk)
                                if received > end-start+1:
                                    raise NodeEngineError('Node result range exceeded its boundary')
                                output.write(chunk)
                                digest.update(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                        if received != end-start+1:
                            raise NodeEngineError('Node result range is incomplete', retryable=True)
                    with lock:
                        completed += received
                        saved[str(start)] = digest.hexdigest()
                        temporary = receipt.with_suffix('.tmp')
                        temporary.write_text(json.dumps({'identity': identity, 'parts': saved}))
                        temporary.replace(receipt)
                        if progress:
                            progress(completed, size)
                    return
                except (httpx.HTTPError, NodeEngineError) as exc:
                    if isinstance(exc, NodeEngineError) and not exc.retryable:
                        raise
                    if attempt == 4:
                        detail = str(exc) if isinstance(exc, NodeEngineError) else f'HTTP {exc.response.status_code}' if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
                        raise NodeEngineError(f'Node result range {start}-{end} failed after retries ({detail})', retryable=True) from None
                    time.sleep(min(2**attempt, 8))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(part, ((start,min(start+step,size)-1) for start in range(0,size,step))))


class NodeODXAdapter(NodeODMAdapter):
    def effective_options(self, options):
        advertised = {item["name"]: item for item in self.options()}
        effective = []
        for option in options:
            name, value = option["name"], option["value"]
            capability = advertised.get(name)
            if not capability:
                raise NodeEngineError(f"ODX does not advertise the required option {name}")
            domain = capability.get("domain")
            if isinstance(domain, list) and value not in domain:
                raise NodeEngineError(f"ODX does not support the requested value for {name}")
            effective.append(option)
        return effective


def configured_node_token(variable, endpoint):
    """Bind operator-managed credentials to their configured destination."""
    import os

    if variable not in ("MOSAICEARTH_LOCAL_NODEODX", "MOSAICEARTH_MANAGED_NODEODX"):
        raise NodeEngineError("Unknown operator-managed node configuration")
    if os.environ.get(variable, "").strip().rstrip("/") != endpoint:
        raise NodeEngineError("The configured node endpoint changed while the task was active")
    token = os.environ.get(f"{variable}_TOKEN", "").strip()
    if variable == "MOSAICEARTH_MANAGED_NODEODX" and (not endpoint.startswith("https://") or not token):
        raise NodeEngineError("Managed NodeODX requires HTTPS and a token")
    return token
