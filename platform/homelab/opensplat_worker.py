"""Single-task COLMAP/OpenSplat service for an ephemeral, token-bound Pod."""

import asyncio
import hashlib
import hmac
import io
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

from .opensplat_engine import OpenSplatAdapter, prepare_colmap


MAX_FRAME_BYTES = 8 * 1024**2
MAX_FRAME_DIMENSION = 2048
MAX_FRAMES = 240
FRAME_NAME = re.compile(r"frame_[0-9]{4}\.jpg")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class StartJob(BaseModel):
    preset: Literal["FAST", "STANDARD", "HIGH_QUALITY"]
    frame_count: int = Field(ge=4, le=MAX_FRAMES)


class Worker:
    def __init__(self):
        self.token = os.environ.get("MOSAICEARTH_ENGINE_TOKEN", "")
        self.colmap = shutil.which("colmap")
        self.opensplat = shutil.which(os.environ.get("MOSAICEARTH_OPENSPLAT_BIN", "opensplat"))
        self.directory = tempfile.TemporaryDirectory(prefix="me-opensplat-")
        self.project = Path(self.directory.name)
        self.images = self.project / "images"
        self.images.mkdir()
        self.configuration = None
        self.state = {"status": "waiting", "progress": 0}
        self.task = None

    def train(self, preset):
        try:
            self.state = {"status": "running", "progress": 5, "stage": "COLMAP"}
            registered = prepare_colmap(self.project, self.colmap, self.project / "colmap.log")

            def progress(step, total):
                self.state = {"status": "running", "progress": 45 + int(54 * step / total), "stage": "OpenSplat"}

            _, cameras, benchmark = OpenSplatAdapter(self.opensplat).train(
                self.project, self.project / "training", preset, registered, progress=progress,
            )
            if benchmark["device"] != "CUDA":
                raise RuntimeError("The OpenSplat Pod did not train on CUDA.")
            self.state = {"status": "completed", "progress": 100, "benchmark": benchmark, "cameras": cameras}
        except Exception:
            logging.getLogger(__name__).exception("OpenSplat Pod processing failed")
            self.state = {"status": "failed", "progress": 0, "error": "COLMAP or CUDA OpenSplat failed."}


worker = Worker()


async def authorize(authorization: str = Header(default="")):
    if len(worker.token) < 32 or not hmac.compare_digest(
        authorization.encode("utf-8"), f"Bearer {worker.token}".encode("utf-8")
    ):
        raise HTTPException(401, "Worker token required")


@app.get("/health", dependencies=[Depends(authorize)])
async def health():
    if not worker.colmap or not worker.opensplat or not Path("/dev/nvidiactl").exists():
        raise HTTPException(503, "COLMAP, OpenSplat and a CUDA device are required")
    return {"engine": "opensplat", "protocol": 1}


@app.put("/input/{name}", dependencies=[Depends(authorize)])
async def upload(name: str, request: Request, x_content_sha256: str = Header(default="")):
    if not FRAME_NAME.fullmatch(name) or int(name[6:10]) >= MAX_FRAMES:
        raise HTTPException(400, "Invalid frame name")
    if not re.fullmatch(r"[a-f0-9]{64}", x_content_sha256):
        raise HTTPException(400, "A SHA-256 checksum is required")
    if worker.configuration is not None:
        raise HTTPException(409, "Inputs have been finalized")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_FRAME_BYTES:
            raise HTTPException(413, "Frame exceeds the upload limit")
        content.extend(chunk)
    if hashlib.sha256(content).hexdigest() != x_content_sha256:
        raise HTTPException(400, "Frame checksum mismatch")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "JPEG" or not 0 < min(image.size) <= max(image.size) <= MAX_FRAME_DIMENSION:
                raise ValueError
            image.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, "Invalid JPEG frame") from None
    # All writes and transitions are serialized by one event loop, without an await below.
    if worker.configuration is not None:
        raise HTTPException(409, "Inputs have been finalized")
    path = worker.images / name
    if path.exists():
        if path.read_bytes() != content:
            raise HTTPException(409, "A different frame already uses this name")
    else:
        path.write_bytes(content)
    return {"sha256": x_content_sha256}


@app.post("/start", status_code=202, dependencies=[Depends(authorize)])
async def start(configuration: StartJob):
    if worker.configuration is not None:
        if worker.configuration != configuration:
            raise HTTPException(409, "A different job already uses this worker")
        return {"status": worker.state["status"]}
    await health()
    expected = {f"frame_{i:04d}.jpg" for i in range(configuration.frame_count)}
    if {path.name for path in worker.images.iterdir()} != expected:
        raise HTTPException(400, "The frame set is incomplete")
    worker.configuration = configuration
    worker.state = {"status": "running", "progress": 0}
    worker.task = asyncio.create_task(asyncio.to_thread(worker.train, configuration.preset))
    return {"status": "running"}


@app.get("/status", dependencies=[Depends(authorize)])
async def status():
    return worker.state


@app.get("/logs/{phase}", dependencies=[Depends(authorize)])
async def logs(phase: Literal["colmap", "opensplat"]):
    path = worker.project / ("colmap.log" if phase == "colmap" else "training/opensplat.log")
    if not path.is_file():
        return {"log": ""}
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 8192))
        return {"log": stream.read(8192).decode("utf-8", errors="replace")}


@app.get("/result", dependencies=[Depends(authorize)])
async def result():
    if worker.state["status"] != "completed":
        raise HTTPException(409, "The result is not ready")
    return FileResponse(worker.project / "training" / "model.ply", media_type="application/octet-stream")


@app.get("/validation/{name}", dependencies=[Depends(authorize)])
async def validation_image(name: Literal["reference.png", "rendered.png"]):
    if worker.state["status"] != "completed":
        raise HTTPException(409, "The validation result is not ready")
    path = worker.project / "training" / "validation" / name
    if not path.is_file():
        raise HTTPException(404, "Validation images were not exported by this engine version")
    return FileResponse(path, media_type="image/png")
