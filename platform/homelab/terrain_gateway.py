"""Bounded public terrain cache. It never proxies project data or arbitrary URLs.

The stable client contract is independent of the upstream operator. CDN cache is
an accelerator; files here survive restarts but are evicted at the configured
limit. Cache misses still depend on the upstream. No paid storage is provisioned.
"""
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter()
PREFIX = "/terrain/v1/global"
DEFAULT_ORIGIN = "https://terrain.reearth.land/cesium-mesh/ellipsoid"
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "X-Terrain-Cache, X-Terrain-Source, ETag"}
EXTENSIONS = {"", "octvertexnormals", "watermask", "octvertexnormals-watermask"}


class TerrainUnavailable(Exception):
    pass


class TerrainCache:
    def __init__(self, directory, origin=DEFAULT_ORIGIN, transport=None, max_bytes=1024**3, max_files=20000):
        self.directory = Path(directory)
        self.origin = origin.rstrip("/")
        self.transport = transport
        self.max_bytes, self.max_files = max_bytes, max_files
        self.pending = {}
        self.semaphore = asyncio.Semaphore(8)
        self.write_lock = threading.Lock()
        self.index = None
        self.size = 0
        self.failures, self.retry_at = 0, 0
        self.last_error = None
        self.client = None

    async def close(self):
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    def path(self, key):
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".cache")

    async def read(self, key, age=None):
        def read():
            path = self.path(key)
            try:
                if age is not None and time.time() - path.stat().st_mtime > age:
                    return None
                return path.read_bytes()
            except OSError:
                return None
        return await asyncio.to_thread(read)

    async def write(self, key, data):
        def write():
            with self.write_lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                if self.index is None:
                    files = sorted((p.stat().st_mtime, p.stat().st_size, p) for p in self.directory.glob("*.cache"))
                    self.index = OrderedDict((p, length) for _, length, p in files)
                    self.size = sum(self.index.values())
                if len(data) > self.max_bytes:
                    return
                path = self.path(key)
                self.size -= self.index.pop(path, 0)
                while self.index and (self.size + len(data) > self.max_bytes or len(self.index) >= self.max_files):
                    item, length = self.index.popitem(last=False)
                    item.unlink(missing_ok=True)
                    self.size -= length
                temporary = path.with_suffix(".partial")
                temporary.write_bytes(data)
                temporary.replace(path)
                self.index[path] = len(data)
                self.size += len(data)
        try:
            await asyncio.to_thread(write)
        except OSError:
            # A full/read-only cache must not break an otherwise valid response.
            pass

    async def fetch(self, path, limit):
        if time.monotonic() < self.retry_at:
            raise TerrainUnavailable("Terrain source temporarily unavailable")
        async with self.semaphore:
            try:
                if self.client is None:
                    self.client = httpx.AsyncClient(timeout=httpx.Timeout(12, connect=4, pool=2),
                        transport=self.transport, follow_redirects=False,
                        limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
                        headers={"User-Agent": "MosaicEarth-Terrain/1.0 (+https://mosaicearth.app)",
                                 "Accept": "application/vnd.quantized-mesh,application/json"})
                async with self.client.stream("GET", self.origin + "/" + path) as r:
                    if r.status_code != 200:
                        raise TerrainUnavailable("Terrain source returned " + str(r.status_code))
                    chunks, length = [], 0
                    async for chunk in r.aiter_bytes():
                        length += len(chunk)
                        if length > limit:
                            raise TerrainUnavailable("Terrain source exceeded response limit")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                self.failures = 0
                self.last_error = None
                return data
            except (httpx.HTTPError, TerrainUnavailable) as exc:
                self.failures += 1
                self.last_error = str(exc)[:160]
                logging.getLogger(__name__).warning("Terrain upstream unavailable: %s", type(exc).__name__)
                if self.failures >= 3:
                    self.retry_at = time.monotonic() + 20
                raise TerrainUnavailable("Terrain source temporarily unavailable") from exc

    async def coalesce(self, key, operation):
        if key in self.pending:
            return await asyncio.shield(self.pending[key])
        if len(self.pending) >= 64:
            raise TerrainUnavailable("Terrain request queue is full")
        async def run():
            try:
                return await asyncio.wait_for(operation(), timeout=10)
            except asyncio.TimeoutError:
                raise TerrainUnavailable("Terrain request timed out") from None
            finally:
                self.pending.pop(key, None)
        task = asyncio.create_task(run())
        self.pending[key] = task
        # Retrieve exceptions even if all HTTP clients disconnect.
        task.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
        return await asyncio.shield(task)

    async def metadata(self):
        cached = await self.read("metadata", age=3600)
        if cached:
            return json.loads(cached)
        async def load():
            try:
                data = json.loads(await self.fetch("layer.json", 65536))
                if (data.get("format") != "quantized-mesh-1.0" or data.get("projection", "EPSG:4326") != "EPSG:4326"
                    or data.get("scheme", "tms") != "tms" or not 0 <= int(data["maxzoom"]) <= 20
                    or not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", str(data["version"]))):
                    raise ValueError("Unsupported terrain contract")
                # Never forward arbitrary URLs from upstream metadata to clients.
                data["tiles"] = [str(data["version"]) + "/{z}/{x}/{y}.terrain"]
                data.pop("parentUrl", None)
                await self.write("metadata", json.dumps(data).encode())
                return data
            except (TerrainUnavailable, ValueError, KeyError, TypeError):
                stale = await self.read("metadata", age=30*86400)
                if stale:
                    return json.loads(stale)
                raise TerrainUnavailable("Terrain metadata unavailable") from None
        return await self.coalesce("metadata", load)

    async def tile(self, version, z, x, y, extension):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", version) or not 0 <= z <= 20 or not 0 <= x < 2**(z+1) or not 0 <= y < 2**z or extension not in EXTENSIONS:
            raise ValueError("Invalid terrain tile")
        key = f"{version}/{z}/{x}/{y}?extensions={extension}"
        cached = await self.read(key, age=30*86400)
        if cached:
            return cached, "HIT"
        async def load():
            metadata = await self.metadata()
            if str(metadata["version"]) != version or z > metadata["maxzoom"]:
                raise ValueError("Terrain revision or detail unavailable")
            path = f"{z}/{x}/{y}.terrain" + ("?extensions="+extension if extension else "")
            data = await self.fetch(path, 4*1024*1024)
            # Reject HTML error pages and truncated mesh headers before caching.
            if len(data) < 92:
                raise TerrainUnavailable("Invalid terrain mesh")
            values = struct.unpack_from("<3d2f7dI", data)
            if not all(math.isfinite(v) for v in values[:-1]) or values[3] > values[4] or not 3 <= values[-1] <= 500000 or len(data) < 92 + values[-1]*6:
                raise TerrainUnavailable("Invalid terrain mesh")
            await self.write(key, data)
            return data, "MISS"
        try:
            return await self.coalesce(key, load)
        except (TerrainUnavailable, ValueError):
            stale = await self.read(key)
            if stale:
                return stale, "STALE"
            raise


_caches = {}


@router.on_event("shutdown")
async def close_connections():
    for instance in _caches.values():
        await instance.close()


def cache():
    directory = Path(os.environ.get("MOSAICEARTH_DATA_DIR", "/data")) / "terrain-cache-v1"
    origin = os.environ.get("MOSAICEARTH_TERRAIN_UPSTREAM", DEFAULT_ORIGIN)
    key = (str(directory), origin)
    if key not in _caches:
        _caches[key] = TerrainCache(directory, origin)
    return _caches[key]


def enabled(request):
    if os.environ.get("MOSAICEARTH_TERRAIN_GATEWAY") != "1":
        raise HTTPException(404, "Terrain gateway is not enabled on this instance")
    if any(k not in {"extensions", "v"} for k in request.query_params):
        raise HTTPException(400, "Unsupported terrain query")


@router.get(PREFIX + "/layer.json")
async def terrain_metadata(request: Request):
    enabled(request)
    try:
        data = await cache().metadata()
    except TerrainUnavailable:
        return Response(status_code=503, headers={**CORS, "Cache-Control": "no-store", "Retry-After": "20"})
    return Response(json.dumps(data), media_type="application/json", headers={**CORS, "Cache-Control": "public, max-age=300"})


@router.get(PREFIX + "/{version}/{z}/{x}/{y}.terrain")
async def terrain_tile(request: Request, version: str, z: int, x: int, y: int):
    enabled(request)
    try:
        data, state = await cache().tile(version, z, x, y, request.query_params.get("extensions", ""))
    except ValueError:
        return Response(status_code=404, headers={**CORS, "Cache-Control": "no-store"})
    except TerrainUnavailable:
        return Response(status_code=503, headers={**CORS, "Cache-Control": "no-store", "Retry-After": "20"})
    etag = '"' + hashlib.sha256(data).hexdigest() + '"'
    headers = {**CORS, "Cache-Control": "public, max-age=300" if state == "STALE" else "public, max-age=86400, s-maxage=604800",
               "ETag": etag, "X-Terrain-Cache": state, "X-Terrain-Source": "ReEarth-Mapterhorn"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="application/vnd.quantized-mesh", headers=headers)
