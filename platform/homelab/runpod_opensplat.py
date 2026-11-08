"""Send preprocessed frames to a dedicated, authenticated CUDA OpenSplat Pod."""

import hashlib
import io
import time

import httpx
from PIL import Image

from .runpod_engines import RunPodEngineError


MAX_RESULT_BYTES = 256 * 1024**2


def train_on_runpod(jobs, job_key, frames, preset, destination, *, progress=None, ready_timeout=900):
    def ensure_active():
        if not jobs.find(job_key):
            raise RunPodEngineError("The OpenSplat Pod is no longer active.")

    def request(method, path, **kwargs):
        # Frame PUTs are SHA-256 idempotent and /start binds one immutable
        # configuration. All operations used here can safely be replayed.
        for attempt in range(4):
            ensure_active()
            remaining = pod.expires_at - time.time()
            if remaining <= 0:
                raise TimeoutError("The OpenSplat Pod reached its configured deadline.")
            try:
                response = client.request(method, pod.endpoint + path, timeout=min(remaining, 30), **kwargs)
                if response.status_code not in (404, 408, 429, 500, 502, 503, 504) or attempt == 3:
                    return response
            except httpx.TransportError:
                if attempt == 3:
                    raise
            time.sleep(min(2 ** attempt, max(0, pod.expires_at-time.time())))

    owns_cleanup = False
    try:
        if progress:
            progress(35, "Provisioning GPU for COLMAP and OpenSplat")
        pod = jobs.start(job_key, "opensplat")
        owns_cleanup = True
        if progress:
            progress(36, "Starting GPU processing")
        with httpx.Client(
            headers={"Authorization": f"Bearer {pod.token}"},
            follow_redirects=False,
            trust_env=False,
        ) as client:
            ready_by = min(pod.expires_at, time.time() + ready_timeout)
            while time.time() < ready_by:
                try:
                    response = request("GET", "/health")
                    if response.is_success and response.json() == {"engine": "opensplat", "protocol": 1}:
                        break
                except (httpx.TransportError, ValueError):
                    pass
                time.sleep(3)
            else:
                raise RunPodEngineError("The CUDA OpenSplat Pod did not become ready.")

            response = request("GET", "/status")
            response.raise_for_status()
            if response.json()["status"] == "waiting":
                if not jobs.claim_upload(job_key):
                    owns_cleanup = False
                    raise RunPodEngineError("The OpenSplat job already claimed its input upload.")
                for index, frame in enumerate(frames):
                    if progress:
                        progress(37 + int(15 * index / len(frames)), f"Transferring verified frame {index+1} of {len(frames)}")
                    jpeg = io.BytesIO()
                    Image.fromarray(frame).save(jpeg, format="JPEG", quality=95)
                    payload = jpeg.getvalue()
                    checksum = hashlib.sha256(payload).hexdigest()
                    response = request("PUT", f"/input/frame_{index:04d}.jpg", content=payload,
                                       headers={"X-Content-SHA256": checksum, "Content-Type": "image/jpeg"})
                    response.raise_for_status()
                    if response.json()["sha256"] != checksum:
                        raise RunPodEngineError("The OpenSplat Pod did not acknowledge a frame checksum.")
                response = request("POST", "/start", json={"preset": preset, "frame_count": len(frames)})
                response.raise_for_status()

            while True:
                response = request("GET", "/status")
                response.raise_for_status()
                result = response.json()
                if result["status"] == "completed":
                    break
                if result["status"] != "running":
                    raise RunPodEngineError("COLMAP or CUDA OpenSplat failed on the Pod.")
                if progress:
                    progress(55 + int(34 * result["progress"] / 100), str(result.get("stage", "OpenSplat")))
                time.sleep(3)

            benchmark = result["benchmark"]
            if progress:
                progress(90, "Downloading and verifying the trained Gaussian model")
            if benchmark["device"] != "CUDA" or benchmark["preset_id"] != preset:
                raise RunPodEngineError("OpenSplat did not report the expected CUDA training run.")
            checksum = hashlib.sha256()
            size = 0
            ensure_active()
            with client.stream("GET", pod.endpoint + "/result", timeout=30) as response:
                response.raise_for_status()
                with destination.open("xb") as output:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        size += len(chunk)
                        if size > MAX_RESULT_BYTES or time.time() >= pod.expires_at:
                            raise RunPodEngineError("The OpenSplat result exceeded its transfer limits.")
                        checksum.update(chunk)
                        output.write(chunk)
            ensure_active()
            if not size or checksum.hexdigest() != benchmark["ply_sha256"]:
                raise RunPodEngineError("The OpenSplat result failed SHA-256 verification.")
            for name, digest in benchmark.get("validation_images_sha256", {}).items():
                if name not in {"reference.png", "rendered.png"}:
                    raise RunPodEngineError("Unexpected validation artifact name")
                response = request("GET", "/validation/" + name)
                response.raise_for_status()
                if len(response.content) > 24 * 1024**2 or hashlib.sha256(response.content).hexdigest() != digest:
                    raise RunPodEngineError("The held-out validation image failed SHA-256 verification.")
                (destination.parent / name).write_bytes(response.content)
            return result["cameras"], benchmark
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise RunPodEngineError(f"OpenSplat Pod protocol failed ({type(exc).__name__}).") from None
    finally:
        if owns_cleanup:
            try:
                jobs.finish(job_key)
            except RunPodEngineError:
                # Keep the durable closing record for the processing worker's reaper.
                pass
