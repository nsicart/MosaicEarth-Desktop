"""
MosaicEarth Canonical Geospatial Ingestion, Inspection & Regenerable Derivatives Engine
======================================================================================
Implements the strict architectural separation:
  1. `source/`   -> Immutable original uploaded file (exact bytes, original name/ext, SHA-256 checksum).
  2. `metadata/` -> `asset.json`, `crs.json`, `transform.json`, `derivatives.json` (canonical manifest).
  3. `derived/`  -> Regenerable visualization & streaming outputs (`preview`, `imagery`, `elevation`,
                    `terrain`, `pointcloud`, `3dtiles`, `vectors`, `optimized`).

All legacy paths (`preview.png`, `overlay.geojson`, `model.glb`, `tileset.json`, `points.pnts`, `model.spz`)
are preserved at the root of `results/external/{project_id}/{asset_id}/` for 100% backward compatibility.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import gzip
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageFilter

# Register system PROJ data directory (/usr/share/proj) and bundled homelab/proj_grids so pyproj finds egm96_15.gtx and es_ign_egm08-rednap.tif offline
BUNDLED_PROJ_GRIDS_DIR = str((Path(__file__).resolve().parent / "proj_grids"))
DEFAULT_PROJ_DATA_DIRS = [
    BUNDLED_PROJ_GRIDS_DIR,
    "/app/homelab/proj_grids",
    "/usr/share/proj",
    "/usr/local/share/proj",
]
try:
    import pyproj
    import pyproj.datadir
    import pyproj.network
    pyproj.network.set_network_enabled(False)
    for _proj_cand in DEFAULT_PROJ_DATA_DIRS:
        if os.path.isdir(_proj_cand) and _proj_cand not in pyproj.datadir.get_data_dir():
            pyproj.datadir.append_data_dir(_proj_cand)
except Exception:
    pass

PIPELINE_VERSION = 2

# ============================================================================
# SECTION 24 — CENTRALIZED CONFIGURABLE SIZE & COMPLEXITY THRESHOLDS
# ============================================================================

@dataclasses.dataclass
class GeospatialThresholds:
    """Centralized, configurable thresholds governing direct vs hierarchical/tiled derivatives."""
    POINTCLOUD_DIRECT_LIMIT: int = int(os.environ.get("ME_POINTCLOUD_DIRECT_LIMIT", "50000"))
    POINTCLOUD_MAX_POINTS_PER_TILE: int = int(os.environ.get("ME_POINTCLOUD_MAX_POINTS_PER_TILE", "25000"))
    MESH_DIRECT_SIZE_LIMIT: int = int(os.environ.get("ME_MESH_DIRECT_SIZE_LIMIT", str(15 * 1024 * 1024)))
    MESH_TRIANGLE_LIMIT: int = int(os.environ.get("ME_MESH_TRIANGLE_LIMIT", "80000"))
    VECTOR_GEOJSON_LIMIT: int = int(os.environ.get("ME_VECTOR_GEOJSON_LIMIT", str(8 * 1024 * 1024)))
    VECTOR_FEATURE_LIMIT: int = int(os.environ.get("ME_VECTOR_FEATURE_LIMIT", "15000"))
    RASTER_TILE_LIMIT: int = int(os.environ.get("ME_RASTER_TILE_LIMIT", str(250 * 1024 * 1024)))
    MAX_ZIP_UNCOMPRESSED_BYTES: int = int(os.environ.get("ME_MAX_ZIP_UNCOMPRESSED_BYTES", str(2 * 1024 * 1024 * 1024)))
    MAX_ZIP_FILES: int = int(os.environ.get("ME_MAX_ZIP_FILES", "5000"))
    MAX_ZIP_COMPRESSION_RATIO: float = float(os.environ.get("ME_MAX_ZIP_COMPRESSION_RATIO", "120.0"))

    @classmethod
    def from_env(cls) -> "GeospatialThresholds":
        return cls(
            POINTCLOUD_DIRECT_LIMIT=int(os.environ.get("ME_POINTCLOUD_DIRECT_LIMIT", "50000")),
            POINTCLOUD_MAX_POINTS_PER_TILE=int(os.environ.get("ME_POINTCLOUD_MAX_POINTS_PER_TILE", "25000")),
            MESH_DIRECT_SIZE_LIMIT=int(os.environ.get("ME_MESH_DIRECT_SIZE_LIMIT", str(15 * 1024 * 1024))),
            MESH_TRIANGLE_LIMIT=int(os.environ.get("ME_MESH_TRIANGLE_LIMIT", "80000")),
            VECTOR_GEOJSON_LIMIT=int(os.environ.get("ME_VECTOR_GEOJSON_LIMIT", str(8 * 1024 * 1024))),
            VECTOR_FEATURE_LIMIT=int(os.environ.get("ME_VECTOR_FEATURE_LIMIT", "15000")),
            RASTER_TILE_LIMIT=int(os.environ.get("ME_RASTER_TILE_LIMIT", str(250 * 1024 * 1024))),
            MAX_ZIP_UNCOMPRESSED_BYTES=int(os.environ.get("ME_MAX_ZIP_UNCOMPRESSED_BYTES", str(2 * 1024 * 1024 * 1024))),
            MAX_ZIP_FILES=int(os.environ.get("ME_MAX_ZIP_FILES", "5000")),
            MAX_ZIP_COMPRESSION_RATIO=float(os.environ.get("ME_MAX_ZIP_COMPRESSION_RATIO", "120.0")),
        )


THRESHOLDS = GeospatialThresholds()

# Canonical Asset Classifications (Section 3)
ASSET_CLASS_RASTER_IMAGERY = "RASTER_IMAGERY"
ASSET_CLASS_RASTER_ELEVATION = "RASTER_ELEVATION"
ASSET_CLASS_RASTER_GENERIC = "RASTER_GENERIC"
ASSET_CLASS_POINT_CLOUD = "POINT_CLOUD"
ASSET_CLASS_MESH = "MESH"
ASSET_CLASS_VECTOR = "VECTOR"
ASSET_CLASS_GAUSSIAN_SPLAT = "GAUSSIAN_SPLAT"
ASSET_CLASS_CESIUM_3D_TILES = "CESIUM_3D_TILES"
ASSET_CLASS_CESIUM_TERRAIN = "CESIUM_TERRAIN"
ASSET_CLASS_UNKNOWN = "UNKNOWN"

# Canonical Processing States (Section 18)
STATE_UPLOADED = "UPLOADED"
STATE_INSPECTING = "INSPECTING"
STATE_PROCESSING = "PROCESSING"
STATE_READY = "READY"
STATE_PARTIAL = "PARTIAL"
STATE_FAILED = "FAILED"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sanitize_filename(filename: str) -> str:
    """Sanitizes a user-supplied filename while preserving its extension."""
    base = Path(filename or "asset.bin").name
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return clean or "asset.bin"


# ============================================================================
# SECTION 13 & 23 — SECURE ZIP EXTRACTION & SIDECAR DISCOVERY
# ============================================================================

class ZipSecurityError(ValueError):
    """Raised when a ZIP archive violates path-traversal, zip-bomb, or file-count limits."""


def safe_extract_zip(
    zip_source: Path | bytes,
    dest_dir: Path,
    thresholds: GeospatialThresholds = THRESHOLDS,
) -> List[Path]:
    """
    Safely extracts a ZIP archive into `dest_dir` with strict protections against:
      - Zip-Slip / path traversal (`..`, leading `/`, Windows drive letters, symlinks)
      - Zip-Bombs (total uncompressed size limit + compression ratio limit)
      - Excessive file count
    Returns the list of extracted file paths.
    """
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: List[Path] = []

    zf_ctx = (
        zipfile.ZipFile(io.BytesIO(zip_source), "r")
        if isinstance(zip_source, (bytes, bytearray))
        else zipfile.ZipFile(zip_source, "r")
    )
    with zf_ctx as zf:
        infos = zf.infolist()
        if len(infos) > thresholds.MAX_ZIP_FILES:
            raise ZipSecurityError(
                f"ZIP archive contains {len(infos)} entries, exceeding limit of {thresholds.MAX_ZIP_FILES}."
            )

        total_uncompressed = 0
        total_compressed = 0
        for info in infos:
            raw_name = info.filename.replace("\\", "/")
            if not raw_name or raw_name.endswith("/"):
                continue
            # Check for path traversal or absolute paths
            norm_parts = raw_name.split("/")
            if (
                raw_name.startswith("/")
                or ":" in norm_parts[0]
                or any(p in ("..", "") for p in norm_parts if p == "..")
            ):
                raise ZipSecurityError(f"Malicious ZIP entry path blocked (zip-slip): {info.filename!r}")

            # Check symlink attribute in external_attr (Unix S_IFLNK = 0o120000)
            unix_mode = (info.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise ZipSecurityError(f"Symbolic link entry blocked in ZIP archive: {info.filename!r}")

            total_uncompressed += int(info.file_size or 0)
            total_compressed += max(1, int(info.compress_size or 1))
            if total_uncompressed > thresholds.MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ZipSecurityError(
                    f"ZIP uncompressed size exceeds limit ({thresholds.MAX_ZIP_UNCOMPRESSED_BYTES} bytes)."
                )
            if info.file_size > 10 * 1024 * 1024:
                ratio = float(info.file_size) / float(max(1, info.compress_size))
                if ratio > thresholds.MAX_ZIP_COMPRESSION_RATIO:
                    raise ZipSecurityError(
                        f"Suspicious ZIP compression ratio ({ratio:.1f}x) in entry {info.filename!r}."
                    )

            target_path = (dest_dir / raw_name).resolve()
            if not str(target_path).startswith(str(dest_dir) + os.sep) and target_path != dest_dir:
                raise ZipSecurityError(f"ZIP entry escapes destination directory: {info.filename!r}")

            target_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src_f, open(target_path, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f, length=1024 * 1024)
            extracted.append(target_path)

    return extracted


def parse_world_file_and_prj(
    files_in_dir: List[Path],
    primary_image: Path,
    image_width: int,
    image_height: int,
) -> Optional[Dict[str, Any]]:
    """
    Parses ESRI World Files (`.jgw`, `.pgw`, `.tfw`, `.wld`) and optional `.prj` / `.aux.xml`
    sidecars accompanying an image (`image.jpg` + `image.jgw` + `image.prj`).
    Returns `{crs, bbox_wgs84, lat, lon}` when valid.
    """
    stem_low = primary_image.stem.lower()
    world_exts = (".jgw", ".pgw", ".tfw", ".gfw", ".wld", ".jpgw", ".pngw", ".tifw")
    world_file: Optional[Path] = None
    prj_file: Optional[Path] = None

    for f in files_in_dir:
        if f.stem.lower() == stem_low:
            if f.suffix.lower() in world_exts:
                world_file = f
            elif f.suffix.lower() == ".prj":
                prj_file = f

    if not world_file or not world_file.is_file():
        return None

    try:
        lines = [float(x.strip()) for x in world_file.read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip()]
        if len(lines) < 6:
            return None
        # A: pixel size X, D: rot Y, B: rot X, E: pixel size Y (negative), C: top-left X, F: top-left Y
        px_a, rot_d, rot_b, px_e, ul_x, ul_y = lines[:6]
        lr_x = ul_x + px_a * image_width + rot_b * image_height
        lr_y = ul_y + rot_d * image_width + px_e * image_height
        min_x, max_x = min(ul_x, lr_x), max(ul_x, lr_x)
        min_y, max_y = min(ul_y, lr_y), max(ul_y, lr_y)

        crs_str = "EPSG:4326"
        if prj_file and prj_file.is_file():
            wkt = prj_file.read_text(encoding="utf-8", errors="ignore").strip()
            if wkt:
                crs_str = wkt
                try:
                    import pyproj
                    crs_obj = pyproj.CRS.from_wkt(wkt)
                    epsg_code = crs_obj.to_epsg()
                    if epsg_code:
                        crs_str = f"EPSG:{epsg_code}"
                    transformer = pyproj.Transformer.from_crs(crs_obj, "EPSG:4326", always_xy=True)
                    w_lon, s_lat = transformer.transform(min_x, min_y)
                    e_lon, n_lat = transformer.transform(max_x, max_y)
                    min_x, max_x = min(w_lon, e_lon), max(w_lon, e_lon)
                    min_y, max_y = min(s_lat, n_lat), max(s_lat, n_lat)
                except Exception:
                    pass

        if -180.0 <= min_x <= 180.0 and -90.0 <= min_y <= 90.0:
            return {
                "crs": crs_str,
                "bbox_wgs84": [float(min_x), float(min_y), float(max_x), float(max_y)],
                "lon": float((min_x + max_x) * 0.5),
                "lat": float((min_y + max_y) * 0.5),
                "world_file": world_file.name,
                "prj_file": prj_file.name if prj_file else None,
            }
    except Exception:
        pass
    return None


# ============================================================================
# SECTION 14 & 15 — VALIDATION OF DIRECT 3D TILES & TERRAIN PACKAGES
# ============================================================================

def validate_3dtiles_package(extracted_dir: Path) -> Dict[str, Any]:
    """
    Validates a user-uploaded 3D Tiles 1.0 or 1.1 directory (from `3dtiles.zip`):
      - Locates `tileset.json`
      - Validates JSON structure (`asset.version`, `root`, `boundingVolume`)
      - Recursively checks all referenced content URIs to ensure they exist and do NOT escape `extracted_dir`
    """
    extracted_dir = extracted_dir.resolve()
    tileset_candidates = list(extracted_dir.glob("tileset.json")) + list(extracted_dir.rglob("tileset.json"))
    if not tileset_candidates:
        return {"ok": False, "error": "No tileset.json found in archive."}

    ts_path = tileset_candidates[0].resolve()
    ts_root_dir = ts_path.parent
    try:
        ts_obj = json.loads(ts_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"Invalid tileset.json syntax: {exc}"}

    if not isinstance(ts_obj, dict) or not isinstance(ts_obj.get("asset"), dict) or not isinstance(ts_obj.get("root"), dict):
        return {"ok": False, "error": "tileset.json missing required 'asset' or 'root' object."}

    version = str(ts_obj["asset"].get("version") or "1.0")
    if version not in ("0.0", "1.0", "1.1"):
        return {"ok": False, "error": f"Unsupported 3D Tiles version: {version!r}"}

    root_node = ts_obj["root"]
    if not isinstance(root_node.get("boundingVolume"), dict):
        return {"ok": False, "error": "tileset.json root node is missing 'boundingVolume'."}

    referenced_uris: List[str] = []

    def _walk_node(node: Dict[str, Any]) -> None:
        if not isinstance(node, dict):
            return
        content = node.get("content")
        if isinstance(content, dict) and content.get("uri"):
            referenced_uris.append(str(content["uri"]))
        if isinstance(content, dict) and content.get("url"):
            referenced_uris.append(str(content["url"]))
        for c_item in node.get("contents") or []:
            if isinstance(c_item, dict) and (c_item.get("uri") or c_item.get("url")):
                referenced_uris.append(str(c_item.get("uri") or c_item.get("url")))
        for child in node.get("children") or []:
            _walk_node(child)

    _walk_node(root_node)

    visited_json_paths = {ts_path}
    idx_u = 0
    while idx_u < len(referenced_uris):
        uri = referenced_uris[idx_u]
        idx_u += 1
        clean_uri = uri.split("?")[0].replace("\\", "/")
        if clean_uri.startswith(("http://", "https://", "data:")):
            return {"ok": False, "error": f"External URI not permitted in self-hosted 3D Tiles archive: {uri!r}"}
        target = (ts_root_dir / clean_uri).resolve()
        if not str(target).startswith(str(extracted_dir) + os.sep) and target != extracted_dir:
            return {"ok": False, "error": f"3D Tiles content URI escapes archive directory: {uri!r}"}
        if not target.is_file():
            return {"ok": False, "error": f"Referenced 3D Tile content file missing from archive: {uri!r}"}
        # If a child tile references an external sub-tileset .json, recursively validate its nodes & URIs
        if target.suffix.lower() == ".json" and target not in visited_json_paths:
            visited_json_paths.add(target)
            try:
                sub_ts = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(sub_ts, dict) and isinstance(sub_ts.get("root"), dict):
                    _walk_node(sub_ts["root"])
            except Exception as sub_exc:
                return {"ok": False, "error": f"Invalid nested 3D Tiles sub-tileset {clean_uri!r}: {sub_exc}"}

    # Extract approximate WGS84 center from root transform or region if present
    lat, lon, height = None, None, 0.0
    bv = root_node.get("boundingVolume") or {}
    if isinstance(bv.get("region"), list) and len(bv["region"]) >= 4:
        west, south, east, north = [float(x) for x in bv["region"][:4]]
        lon = math.degrees((west + east) * 0.5)
        lat = math.degrees((south + north) * 0.5)
        if len(bv["region"]) >= 6:
            height = float(bv["region"][4])
    elif isinstance(root_node.get("transform"), list) and len(root_node["transform"]) == 16:
        tx, ty, tz = [float(x) for x in root_node["transform"][12:15]]
        if abs(tx) > 1000.0 or abs(ty) > 1000.0 or abs(tz) > 1000.0:
            try:
                import pyproj
                tr = pyproj.Transformer.from_crs("EPSG:4978", "EPSG:4326", always_xy=True)
                lon, lat, height = tr.transform(tx, ty, tz)
            except Exception:
                pass

    rel_entry = ts_path.relative_to(extracted_dir).as_posix()
    return {
        "ok": True,
        "valid": True,
        "tileset_path": ts_path,
        "tileset_rel_to_extract": rel_entry,
        "entry_rel_path": rel_entry,
        "version": version,
        "referenced_files_count": len(referenced_uris),
        "lat": lat,
        "lon": lon,
        "height": height,
    }


def validate_terrain_package(extracted_dir: Path) -> Dict[str, Any]:
    """Validates a pre-tiled Cesium Terrain dataset (`layer.json` + `.terrain` quantized-mesh or heightmap tiles)."""
    extracted_dir = extracted_dir.resolve()
    layer_candidates = list(extracted_dir.glob("layer.json")) + list(extracted_dir.rglob("layer.json"))
    if not layer_candidates:
        return {"ok": False, "valid": False, "error": "No layer.json found in terrain archive."}
    lj_path = layer_candidates[0].resolve()
    try:
        lj_obj = json.loads(lj_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "valid": False, "error": f"Invalid layer.json in terrain archive: {exc}"}
    if not isinstance(lj_obj, dict):
        return {"ok": False, "valid": False, "error": "layer.json must be a JSON object."}
    fmt = str(lj_obj.get("format") or "quantized-mesh-1.0")
    bounds = lj_obj.get("bounds") or [-180.0, -90.0, 180.0, 90.0]
    rel_layer = lj_path.relative_to(extracted_dir).as_posix()
    return {
        "ok": True,
        "valid": True,
        "layer_json_path": lj_path,
        "layer_rel_to_extract": rel_layer,
        "entry_rel_path": rel_layer,
        "format": fmt,
        "bounds": bounds,
    }


# ============================================================================
# SECTION 3 — DEEP CONTENT INSPECTION & CLASSIFICATION (`inspect_geospatial_asset`)
# ============================================================================

def inspect_geospatial_asset(
    source_path: Path,
    original_filename: str = "",
    source_origin: str = "imported",
    user_class_hint: str = "",
    sidecar_files: Optional[List[Path]] = None,
    user_hint: str = "",
    thresholds: Optional[GeospatialThresholds] = None,
) -> Dict[str, Any]:
    """
    Inspects the actual binary/header content of `source_path` (not just its extension)
    and returns a structured inspection dictionary containing:
      - `asset_class`: RASTER_IMAGERY | RASTER_ELEVATION | RASTER_GENERIC | POINT_CLOUD | MESH |
                       VECTOR | GAUSSIAN_SPLAT | CESIUM_3D_TILES | CESIUM_TERRAIN | UNKNOWN
      - `mime_type`, `crs`, `epsg`, `has_embedded_georef`
      - `bbox_wgs84`, `lat`, `lon`, `min_val`, `max_val`, `nodata`
      - `width`, `height`, `bands`, `dtype`
      - `point_count`, `las_attributes`
      - `vertex_count`, `triangle_count`, `has_textures`, `has_normals`
      - `vector_layers` (for multi-layer GPKG / vector files)
    """
    fname_low = (original_filename or source_path.name).lower()
    ext_low = Path(fname_low).suffix.lower().lstrip(".")
    hint_up = (user_class_hint or user_hint or "").strip().upper()
    file_size = source_path.stat().st_size if source_path.is_file() else 0

    meta: Dict[str, Any] = {
        "asset_class": ASSET_CLASS_UNKNOWN,
        "classification_confidence": "high",
        "mime_type": "application/octet-stream",
        "crs": None,
        "epsg": None,
        "has_embedded_georef": False,
        "bbox_wgs84": None,
        "lat": None,
        "lon": None,
        "width": None,
        "height": None,
        "bands": None,
        "dtype": None,
        "nodata": None,
        "min_val": None,
        "max_val": None,
        "point_count": None,
        "las_attributes": [],
        "vertex_count": None,
        "triangle_count": None,
        "has_textures": False,
        "has_normals": False,
        "vector_layers": [],
        "file_size_bytes": file_size,
    }

    if not source_path.is_file() or file_size == 0:
        return meta

    with open(source_path, "rb") as f:
        head_bytes = f.read(512)

    # 1. Check ZIP packages first (could be 3dtiles.zip, terrain.zip, .shp.zip, .obj.zip, .kmz, or Gaussian zip)
    if head_bytes[:4] == b"PK\x03\x04":
        meta["mime_type"] = "application/zip"
        try:
            with tempfile.TemporaryDirectory(prefix="me_insp_zip_") as tmpd:
                extracted = safe_extract_zip(source_path, Path(tmpd))
                names_low = [p.name.lower() for p in extracted]
                if "tileset.json" in names_low:
                    v_3d = validate_3dtiles_package(Path(tmpd))
                    meta["asset_class"] = ASSET_CLASS_CESIUM_3D_TILES
                    if v_3d.get("ok"):
                        meta["has_embedded_georef"] = v_3d.get("lat") is not None
                        meta["lat"] = v_3d.get("lat")
                        meta["lon"] = v_3d.get("lon")
                        meta["alt"] = v_3d.get("height")
                        meta["tileset_version"] = v_3d.get("version")
                    else:
                        meta["validation_error"] = v_3d.get("error")
                    return meta
                if "layer.json" in names_low and any(n.endswith(".terrain") for n in names_low):
                    v_ter = validate_terrain_package(Path(tmpd))
                    meta["asset_class"] = ASSET_CLASS_CESIUM_TERRAIN
                    if v_ter.get("ok"):
                        meta["has_embedded_georef"] = True
                    else:
                        meta["validation_error"] = v_ter.get("error")
                    return meta
                if any(n.endswith(".shp") for n in names_low):
                    meta["asset_class"] = ASSET_CLASS_VECTOR
                    shp_p = next(p for p in extracted if p.suffix.lower() == ".shp")
                    prj_p = next((p for p in extracted if p.suffix.lower() == ".prj"), None)
                    if prj_p:
                        meta["crs"] = prj_p.read_text(encoding="utf-8", errors="ignore").strip()[:256]
                        meta["has_embedded_georef"] = True
                    meta["vector_layers"] = [{"name": shp_p.stem, "geometry_type": "Geometry"}]
                    return meta
                if any(n.endswith((".obj", ".gltf", ".glb")) for n in names_low):
                    obj_p = next(p for p in extracted if p.suffix.lower() in (".obj", ".gltf", ".glb"))
                    sub_m = inspect_geospatial_asset(obj_p, obj_p.name, source_origin=source_origin, sidecar_files=extracted)
                    sub_m["asset_class"] = ASSET_CLASS_MESH
                    sub_m["has_textures"] = any(p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") for p in extracted)
                    sub_m["has_mtl"] = any(p.suffix.lower() == ".mtl" for p in extracted)
                    return sub_m
                if any(n.endswith(".kml") for n in names_low):
                    meta["asset_class"] = ASSET_CLASS_VECTOR
                    meta["crs"] = "EPSG:4326"
                    meta["has_embedded_georef"] = True
                    return meta
                if any(p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff") for p in extracted):
                    # Check if JPG + JGW + PRJ sidecar bundle
                    img_p = next(p for p in extracted if p.suffix.lower() in (".tif", ".tiff", ".jpg", ".jpeg", ".png"))
                    if any(p.suffix.lower() in (".jgw", ".pgw", ".tfw", ".wld") for p in extracted):
                        sub_m = inspect_geospatial_asset(img_p, img_p.name, sidecar_files=extracted)
                        return sub_m
                    if source_origin == "gaussian":
                        meta["asset_class"] = ASSET_CLASS_GAUSSIAN_SPLAT
                        return meta
        except ZipSecurityError:
            raise
        except Exception:
            pass

    # 2. Check SPZ Gaussian Splatting (gzipped NGSP magic 0x5053474E)
    if head_bytes[:2] == b"\x1f\x8b" or ext_low in ("spz", "splat", "ksplat"):
        try:
            raw_spz = head_bytes
            if head_bytes[:2] == b"\x1f\x8b":
                with gzip.open(source_path, "rb") as compressed:
                    raw_spz = compressed.read(16)
            if len(raw_spz) >= 16 and struct.unpack_from("<I", raw_spz, 0)[0] == 0x5053474E:
                num_pts = struct.unpack_from("<I", raw_spz, 8)[0]
                sh_deg_spz = int(struct.unpack_from("<B", raw_spz, 12)[0])
                meta["asset_class"] = ASSET_CLASS_GAUSSIAN_SPLAT
                meta["mime_type"] = "application/x-spz"
                meta["point_count"] = int(num_pts)
                meta["sh_degree_source"] = sh_deg_spz
                spz_version = struct.unpack_from("<I", raw_spz, 4)[0]
                preserves_bands = spz_version == 2 and sh_deg_spz <= 3
                meta["sh_degree_derived"] = sh_deg_spz if preserves_bands else min(1, sh_deg_spz)
                meta["lossy_sh_reduction"] = meta["sh_degree_derived"] < sh_deg_spz
                return meta
        except Exception:
            pass
        if ext_low in ("spz", "splat", "ksplat"):
            meta["asset_class"] = ASSET_CLASS_GAUSSIAN_SPLAT
            return meta

    # 3. Check PLY (could be Gaussian Splat PLY with f_dc_0 / opacity / scale_0 OR Point Cloud PLY OR Mesh PLY)
    if head_bytes.startswith(b"ply"):
        with source_path.open("rb") as source:
            hdr_bytes = source.read(8192).split(b"end_header", 1)[0]
        hdr_txt = hdr_bytes.decode("ascii", errors="ignore")
        v_match = re.search(r"element\s+vertex\s+(\d+)", hdr_txt)
        f_match = re.search(r"element\s+face\s+(\d+)", hdr_txt)
        v_cnt = int(v_match.group(1)) if v_match else 0
        f_cnt = int(f_match.group(1)) if f_match else 0
        has_gs_props = ("f_dc_0" in hdr_txt and ("scale_0" in hdr_txt or "opacity" in hdr_txt))
        if has_gs_props or source_origin == "gaussian":
            meta["asset_class"] = ASSET_CLASS_GAUSSIAN_SPLAT
            meta["point_count"] = v_cnt
            sh_src = 3 if "f_rest_44" in hdr_txt else (2 if "f_rest_23" in hdr_txt else (1 if "f_rest_8" in hdr_txt else 0))
            preserve_bands = any(marker in hdr_txt.lower() for marker in (
                "generated by opensplat", "mosaicearth preserves source sh bands",
            ))
            meta["sh_degree_source"] = sh_src
            meta["sh_degree_derived"] = sh_src if preserve_bands and sh_src else 1
            meta["lossy_sh_reduction"] = meta["sh_degree_derived"] < sh_src
            if meta["lossy_sh_reduction"]:
                meta["sh_reduction_note"] = (
                    f"Source PLY contains Spherical Harmonics degree {sh_src} (preserved 100% lossless in source/); "
                    "Cesium visualization derivative uses SPZ v2 SH degree 1."
                )
        elif f_cnt > 0:
            meta["asset_class"] = ASSET_CLASS_MESH
            meta["vertex_count"] = v_cnt
            meta["triangle_count"] = f_cnt
        else:
            meta["asset_class"] = ASSET_CLASS_POINT_CLOUD
            meta["point_count"] = v_cnt
            meta["las_attributes"] = ["XYZ"] + (["RGB"] if "red" in hdr_txt else [])
        meta["mime_type"] = "application/x-ply"
        return meta

    # 4. Check LAS / LAZ Point Cloud (`LASF` magic)
    if head_bytes[:4] == b"LASF" or ext_low in ("las", "laz"):
        meta["asset_class"] = ASSET_CLASS_POINT_CLOUD
        meta["mime_type"] = "application/vnd.las"
        try:
            import laspy
            with laspy.open(str(source_path)) as lf:
                hdr = lf.header
                meta["point_count"] = int(hdr.point_count)
                dim_names = list(hdr.point_format.dimension_names)
                attrs = ["XYZ"]
                if {"red", "green", "blue"}.issubset(set(dim_names)):
                    attrs.append("RGB")
                for cand_a in ("intensity", "classification", "return_number", "point_source_id", "gps_time"):
                    if cand_a in dim_names:
                        attrs.append(cand_a)
                meta["las_attributes"] = attrs
                try:
                    crs_obj = hdr.parse_crs()
                    if crs_obj:
                        epsg = crs_obj.to_epsg()
                        if epsg:
                            meta["crs"] = f"EPSG:{epsg}"
                            meta["epsg"] = int(epsg)
                        elif getattr(crs_obj, "is_compound", False) and len(getattr(crs_obj, "sub_crs_list", ())) >= 2:
                            h_crs, v_crs = crs_obj.sub_crs_list[0], crs_obj.sub_crs_list[1]
                            h_epsg = h_crs.to_epsg()
                            v_epsg = v_crs.to_epsg()
                            if h_epsg and v_epsg:
                                meta["crs"] = f"EPSG:{h_epsg}+{v_epsg}"
                                meta["epsg"] = int(h_epsg)
                            else:
                                meta["crs"] = crs_obj.to_wkt()
                            meta["vertical_datum_name"] = f"{v_crs.name} (EPSG:{v_epsg})" if v_epsg else str(v_crs.name)
                        else:
                            meta["crs"] = crs_obj.to_wkt()
                        meta["has_embedded_georef"] = True
                except Exception:
                    pass
                min_x, min_y, min_z = float(hdr.mins[0]), float(hdr.mins[1]), float(hdr.mins[2])
                max_x, max_y, max_z = float(hdr.maxs[0]), float(hdr.maxs[1]), float(hdr.maxs[2])
                meta["min_val"] = min_z
                meta["max_val"] = max_z
                if -180.0 <= min_x <= 180.0 and -90.0 <= min_y <= 90.0:
                    meta["bbox_wgs84"] = [min_x, min_y, max_x, max_y]
                    meta["lon"] = (min_x + max_x) * 0.5
                    meta["lat"] = (min_y + max_y) * 0.5
                    meta["has_embedded_georef"] = True
        except Exception:
            pass
        return meta

    # 5. Check E57 (`ASTM-E57`) and ASCII Point Clouds (.xyz, .pts, .pcd)
    if head_bytes[:8] == b"ASTM-E57" or ext_low == "e57":
        meta["asset_class"] = ASSET_CLASS_POINT_CLOUD
        meta["mime_type"] = "model/e57"
        try:
            from .gis_splat_engine import _parse_pointcloud_or_splat_bytes
            pos_e57, _, det_ll = _parse_pointcloud_or_splat_bytes(source_path.read_bytes(), "e57")
            meta["point_count"] = int(len(pos_e57))
            meta["las_attributes"] = ["XYZ", "RGB"]
            if det_ll is not None:
                meta["lat"], meta["lon"] = float(det_ll[0]), float(det_ll[1])
                meta["has_embedded_georef"] = True
        except Exception:
            pass
        return meta

    if ext_low in ("xyz", "pts", "pcd"):
        meta["asset_class"] = ASSET_CLASS_POINT_CLOUD
        meta["mime_type"] = "text/plain"
        try:
            lines = [ln.strip() for ln in source_path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip() and not ln.startswith("#")]
            meta["point_count"] = len(lines)
            meta["las_attributes"] = ["XYZ", "RGB"]
        except Exception:
            pass
        return meta

    # 6. Check glTF / GLB / OBJ / STL / DAE / FBX / IFC / CityJSON / LandXML 3D Meshes & BIM/CAD
    is_cityjson = False
    is_landxml = False
    if ext_low in ("json", "cityjson") or fname_low.endswith(".city.json"):
        try:
            j_peek_m = json.loads(source_path.read_text(encoding="utf-8", errors="ignore")[:131072])
            if isinstance(j_peek_m, dict) and j_peek_m.get("type") == "CityJSON":
                is_cityjson = True
        except Exception:
            pass
    elif ext_low in ("xml", "landxml") or b"<LandXML" in head_bytes:
        is_landxml = True

    if (
        head_bytes[:4] == b"glTF"
        or head_bytes[:13] == b"ISO-10303-21;"
        or is_cityjson
        or is_landxml
        or ext_low in ("glb", "gltf", "obj", "stl", "fbx", "dae", "ifc", "cityjson", "landxml")
    ):
        meta["asset_class"] = ASSET_CLASS_MESH
        meta["mime_type"] = (
            "model/gltf-binary"
            if (head_bytes[:4] == b"glTF" or ext_low == "glb")
            else "model/gltf+json"
            if ext_low == "gltf"
            else "model/stl"
            if ext_low == "stl"
            else "model/vnd.collada+xml"
            if ext_low == "dae"
            else "application/x-step"
            if (ext_low == "ifc" or head_bytes[:13] == b"ISO-10303-21;")
            else "application/city+json"
            if is_cityjson
            else "application/landxml+xml"
            if is_landxml
            else "model/obj"
        )
        try:
            if ext_low == "obj":
                txt = source_path.read_text(encoding="utf-8", errors="ignore")
                v_cnt = sum(1 for ln in txt.splitlines() if ln.startswith("v "))
                f_cnt = sum(1 for ln in txt.splitlines() if ln.startswith("f "))
                vn_cnt = sum(1 for ln in txt.splitlines() if ln.startswith("vn "))
                vt_cnt = sum(1 for ln in txt.splitlines() if ln.startswith("vt "))
                meta["vertex_count"] = v_cnt
                meta["triangle_count"] = f_cnt
                meta["has_normals"] = vn_cnt > 0
                meta["has_textures"] = vt_cnt > 0
            elif head_bytes[:4] == b"glTF" and len(head_bytes) >= 20:
                json_len = struct.unpack_from("<I", head_bytes, 12)[0]
                with open(source_path, "rb") as f:
                    f.seek(20)
                    gltf_json = json.loads(f.read(json_len).decode("utf-8", errors="ignore"))
                exts_used = gltf_json.get("extensionsUsed") or []
                if "KHR_gaussian_splatting" in exts_used:
                    meta["asset_class"] = ASSET_CLASS_GAUSSIAN_SPLAT
                    return meta
                v_cnt = 0
                t_cnt = 0
                for mesh in gltf_json.get("meshes") or []:
                    for prim in mesh.get("primitives") or []:
                        attrs = prim.get("attributes") or {}
                        if "NORMAL" in attrs:
                            meta["has_normals"] = True
                        if "TEXCOORD_0" in attrs:
                            meta["has_textures"] = True
                        pos_acc_idx = attrs.get("POSITION")
                        if pos_acc_idx is not None and pos_acc_idx < len(gltf_json.get("accessors") or []):
                            v_cnt += int(gltf_json["accessors"][pos_acc_idx].get("count") or 0)
                        idx_acc_idx = prim.get("indices")
                        if idx_acc_idx is not None and idx_acc_idx < len(gltf_json.get("accessors") or []):
                            t_cnt += int(gltf_json["accessors"][idx_acc_idx].get("count") or 0) // 3
                meta["vertex_count"] = v_cnt
                meta["triangle_count"] = t_cnt if t_cnt > 0 else (v_cnt // 3)
            else:
                cad_parsed = _parse_engineering_cad_bim_mesh_bytes(
                    source_path.read_bytes(),
                    "cityjson" if is_cityjson else ("landxml" if is_landxml else ext_low),
                    original_filename=fname_low,
                )
                if cad_parsed is not None:
                    v_arr_c, f_arr_c, c_info = cad_parsed
                    meta["vertex_count"] = int(len(v_arr_c))
                    meta["triangle_count"] = int(len(f_arr_c))
                    meta["has_normals"] = True
                    if c_info.get("crs"):
                        meta["crs"] = c_info["crs"]
                        meta["has_embedded_georef"] = True
                    if c_info.get("lat") is not None and c_info.get("lon") is not None:
                        meta["lat"] = float(c_info["lat"])
                        meta["lon"] = float(c_info["lon"])
                        meta["has_embedded_georef"] = True
        except Exception:
            pass
        return meta

    # 7. Check GeoPackage (.gpkg / SQLite header `SQLite format 3\x00`) and Vector Formats
    if head_bytes[:16] == b"SQLite format 3\x00" or ext_low in ("gpkg", "geojson", "json", "kml", "kmz", "dxf", "shp", "gpx"):
        if ext_low == "json":
            try:
                j_peek = json.loads(source_path.read_text(encoding="utf-8", errors="ignore")[:65536])
                if isinstance(j_peek, dict) and "asset" in j_peek and "root" in j_peek:
                    meta["asset_class"] = ASSET_CLASS_CESIUM_3D_TILES
                    return meta
            except Exception:
                pass
        meta["asset_class"] = ASSET_CLASS_VECTOR
        meta["mime_type"] = "application/geopackage+sqlite3" if ext_low == "gpkg" else "application/geo+json"
        if head_bytes[:16] == b"SQLite format 3\x00" or ext_low == "gpkg":
            layers_info = inspect_gpkg_layers(source_path)
            meta["vector_layers"] = layers_info
            if layers_info:
                meta["crs"] = layers_info[0].get("crs")
                meta["epsg"] = layers_info[0].get("epsg")
                meta["has_embedded_georef"] = True
                if layers_info[0].get("bbox_wgs84"):
                    b = layers_info[0]["bbox_wgs84"]
                    meta["bbox_wgs84"] = b
                    meta["lon"] = (b[0] + b[2]) * 0.5
                    meta["lat"] = (b[1] + b[3]) * 0.5
        elif ext_low in ("geojson", "json", "kml", "gpx"):
            meta["crs"] = "EPSG:4326"
            meta["epsg"] = 4326
            meta["has_embedded_georef"] = True
        return meta

    # 8. Check Rasters (GeoTIFF `II*\x00` / `MM\x00*` / BigTIFF, Esri ASCII Grid .asc, SRTM .hgt, PNG, JPEG, WebP)
    is_tiff = head_bytes[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
    is_asc_grid = ext_low == "asc" or head_bytes[:64].lower().lstrip().startswith(b"ncols")
    if is_tiff or is_asc_grid or ext_low in ("tif", "tiff", "asc", "hgt", "jpg", "jpeg", "png", "webp"):
        meta["mime_type"] = (
            "image/tiff"
            if is_tiff
            else "text/plain"
            if is_asc_grid
            else "application/x-srtm-hgt"
            if ext_low == "hgt"
            else f"image/{ext_low.replace('jpg', 'jpeg')}"
        )
        gdal_info: Optional[Dict[str, Any]] = None
        if shutil.which("gdalinfo"):
            try:
                proc = subprocess.run(
                    ["gdalinfo", "-json", "-mm", str(source_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode == 0:
                    gdal_info = json.loads(proc.stdout)
            except Exception:
                gdal_info = None

        if gdal_info:
            sz = gdal_info.get("size") or [0, 0]
            meta["width"] = int(sz[0]) if len(sz) >= 1 else None
            meta["height"] = int(sz[1]) if len(sz) >= 2 else None
            bands_list = gdal_info.get("bands") or []
            meta["bands"] = len(bands_list)
            if bands_list:
                b0 = bands_list[0]
                meta["dtype"] = str(b0.get("type") or "")
                meta["nodata"] = b0.get("noDataValue")
                c_min = b0.get("computedMin") if b0.get("computedMin") is not None else b0.get("minimum")
                c_max = b0.get("computedMax") if b0.get("computedMax") is not None else b0.get("maximum")
                if c_min is not None:
                    meta["min_val"] = float(c_min)
                if c_max is not None:
                    meta["max_val"] = float(c_max)

            st_crs = gdal_info.get("coordinateSystem", {}).get("wkt") or ""
            if st_crs:
                meta["crs"] = st_crs[:512]
                meta["has_embedded_georef"] = True
                try:
                    import pyproj
                    crs_obj_g = pyproj.CRS.from_wkt(st_crs)
                    epsg_c = crs_obj_g.to_epsg()
                    if epsg_c:
                        meta["epsg"] = int(epsg_c)
                        meta["crs"] = f"EPSG:{epsg_c}"
                    elif getattr(crs_obj_g, "is_compound", False) and len(getattr(crs_obj_g, "sub_crs_list", ())) >= 2:
                        h_crs, v_crs = crs_obj_g.sub_crs_list[0], crs_obj_g.sub_crs_list[1]
                        h_epsg = h_crs.to_epsg()
                        v_epsg = v_crs.to_epsg()
                        if h_epsg and v_epsg:
                            meta["epsg"] = int(h_epsg)
                            meta["crs"] = f"EPSG:{h_epsg}+{v_epsg}"
                        meta["vertical_datum_name"] = f"{v_crs.name} (EPSG:{v_epsg})" if v_epsg else str(v_crs.name)
                except Exception:
                    pass

            wgs_ext = gdal_info.get("wgs84Extent", {}).get("coordinates")
            if wgs_ext and isinstance(wgs_ext, list) and wgs_ext[0]:
                ring = wgs_ext[0]
                xs = [float(pt[0]) for pt in ring if len(pt) >= 2]
                ys = [float(pt[1]) for pt in ring if len(pt) >= 2]
                if xs and ys and -180.0 <= min(xs) <= 180.0 and -90.0 <= min(ys) <= 90.0:
                    west, east = min(xs), max(xs)
                    south, north = min(ys), max(ys)
                    if east - west > 1e-7 and north - south > 1e-7:
                        meta["bbox_wgs84"] = [west, south, east, north]
                        meta["lon"] = (west + east) * 0.5
                        meta["lat"] = (south + north) * 0.5
                        meta["has_embedded_georef"] = True
        else:
            try:
                with Image.open(source_path) as im:
                    meta["width"], meta["height"] = im.size
                    arr = np.asarray(im)
                    if arr.ndim == 2:
                        meta["bands"] = 1
                        meta["dtype"] = str(arr.dtype)
                        meta["min_val"] = float( np.nanmin(arr) )
                        meta["max_val"] = float( np.nanmax(arr) )
                    else:
                        meta["bands"] = int(arr.shape[2])
                        meta["dtype"] = str(arr.dtype)
            except Exception:
                pass

        # Check world file + PRJ sidecars if present
        if sidecar_files and meta["width"] and meta["height"] and not meta["has_embedded_georef"]:
            wf_info = parse_world_file_and_prj(sidecar_files, source_path, int(meta["width"]), int(meta["height"]))
            if wf_info:
                meta["crs"] = wf_info["crs"]
                meta["bbox_wgs84"] = wf_info["bbox_wgs84"]
                meta["lat"] = wf_info["lat"]
                meta["lon"] = wf_info["lon"]
                meta["has_embedded_georef"] = True
        elif sidecar_files:
            for sf in sidecar_files:
                if isinstance(sf, Path) and sf.suffix.lower() == ".prj" and sf.is_file():
                    prj_txt = sf.read_text(encoding="utf-8", errors="ignore").strip()
                    if any(tok in prj_txt.upper() for tok in ("+5782", "+5773", "+3855", "5782", "5773", "COMPD_CS", "COMPOUNDCRS", "VERT_CS", "VERTCRS", "REDNAP", "ALICANTE", "EGM96", "EGM2008", "4979")):
                        meta["crs"] = prj_txt[:512]
                        break

        # Determine RASTER_IMAGERY vs RASTER_ELEVATION vs RASTER_GENERIC (Section 3, 4, 5)
        if is_asc_grid or ext_low in ("asc", "hgt") or hint_up in (ASSET_CLASS_RASTER_ELEVATION, "DEM", "DSM", "DTM", "ELEVATION"):
            meta["asset_class"] = ASSET_CLASS_RASTER_ELEVATION
        elif hint_up in (ASSET_CLASS_RASTER_IMAGERY, "ORTHOPHOTO", "IMAGERY", "RGB"):
            meta["asset_class"] = ASSET_CLASS_RASTER_IMAGERY
        elif hint_up == ASSET_CLASS_RASTER_GENERIC:
            meta["asset_class"] = ASSET_CLASS_RASTER_GENERIC
            meta["classification_confidence"] = "explicit_thematic_raster"
        else:
            bands_n = int(meta.get("bands") or 0)
            dtype_s = str(meta.get("dtype") or "").lower()
            is_elevation_name = any(
                k in fname_low
                for k in ("dsm", "dtm", "dem", "mdt", "mds", "mde", "elev", "height", "srtm", "lidar_dem", "copernicus", "alos", "aster", "bathymetry")
            )
            is_imagery_name = any(k in fname_low for k in ("ortho", "rgb", "aerial", "mosaic", "truecolor"))
            is_thematic_name = any(
                k in fname_low
                for k in ("ndvi", "ndwi", "ndre", "evi", "savi", "reflectance", "sar", "backscatter", "thermal", "temp", "index", "class", "mask")
            )

            if bands_n >= 3 and ("byte" in dtype_s or "uint8" in dtype_s):
                meta["asset_class"] = ASSET_CLASS_RASTER_IMAGERY
            elif bands_n == 1 and is_thematic_name and not is_elevation_name:
                meta["asset_class"] = ASSET_CLASS_RASTER_GENERIC
                meta["classification_confidence"] = "thematic_single_band"
            elif bands_n == 1 and ("float" in dtype_s or is_elevation_name):
                min_v = meta.get("min_val")
                max_v = meta.get("max_val")
                if (
                    not is_elevation_name
                    and min_v is not None
                    and max_v is not None
                    and -1.05 <= float(min_v) <= float(max_v) <= 1.05
                ):
                    meta["asset_class"] = ASSET_CLASS_RASTER_GENERIC
                    meta["classification_confidence"] = "normalized_index_single_band"
                else:
                    meta["asset_class"] = ASSET_CLASS_RASTER_ELEVATION
            elif bands_n == 1 and ("int16" in dtype_s or "int32" in dtype_s):
                if is_elevation_name or meta.get("nodata") is not None:
                    meta["asset_class"] = ASSET_CLASS_RASTER_ELEVATION
                elif is_imagery_name:
                    meta["asset_class"] = ASSET_CLASS_RASTER_IMAGERY
                else:
                    # Ambiguous single-band integer raster: do not invent classification!
                    meta["asset_class"] = ASSET_CLASS_RASTER_GENERIC
                    meta["classification_confidence"] = "ambiguous_single_band"
            elif bands_n >= 3:
                meta["asset_class"] = ASSET_CLASS_RASTER_IMAGERY
            elif not is_tiff:
                meta["asset_class"] = ASSET_CLASS_RASTER_IMAGERY
            else:
                meta["asset_class"] = ASSET_CLASS_RASTER_GENERIC
                meta["classification_confidence"] = "ambiguous"
        return meta

    return meta


# ============================================================================
# SECTION 11 — MULTI-LAYER GEOPACKAGE (`GPKG`) INSPECTION & EXTRACTION
# ============================================================================

def inspect_gpkg_layers(gpkg_path: Path) -> List[Dict[str, Any]]:
    """
    Detects all spatial vector tables/layers inside a `.gpkg` SQLite container
    without flattening them, returning layer name, geometry type, CRS, feature count, and bbox.
    """
    layers: List[Dict[str, Any]] = []
    if not gpkg_path.is_file():
        return layers
    try:
        with sqlite3.connect(f"file:{gpkg_path}?mode=ro", uri=True, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT c.table_name, c.data_type, c.min_x, c.min_y, c.max_x, c.max_y, c.srs_id,
                       g.geometry_type_name
                FROM gpkg_contents c
                LEFT JOIN gpkg_geometry_columns g ON c.table_name = g.table_name
                WHERE c.data_type IN ('features', '2d-gridded-coverage')
                """
            )
            for row in cur.fetchall():
                tname = str(row["table_name"])
                srs_id = int(row["srs_id"] or 4326)
                geom_type = str(row["geometry_type_name"] or "GEOMETRY")
                try:
                    cnt_row = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()
                    feat_count = int(cnt_row[0]) if cnt_row else 0
                except Exception:
                    feat_count = 0

                min_x = float(row["min_x"]) if row["min_x"] is not None else None
                min_y = float(row["min_y"]) if row["min_y"] is not None else None
                max_x = float(row["max_x"]) if row["max_x"] is not None else None
                max_y = float(row["max_y"]) if row["max_y"] is not None else None
                bbox_wgs84 = None
                if min_x is not None and min_y is not None and max_x is not None and max_y is not None:
                    if srs_id == 4326 and -180.0 <= min_x <= 180.0 and -90.0 <= min_y <= 90.0:
                        bbox_wgs84 = [min_x, min_y, max_x, max_y]
                    elif srs_id > 0:
                        try:
                            import pyproj
                            tr = pyproj.Transformer.from_crs(f"EPSG:{srs_id}", "EPSG:4326", always_xy=True)
                            w_lon, s_lat = tr.transform(min_x, min_y)
                            e_lon, n_lat = tr.transform(max_x, max_y)
                            if -180.0 <= w_lon <= 180.0 and -90.0 <= s_lat <= 90.0:
                                bbox_wgs84 = [min(w_lon, e_lon), min(s_lat, n_lat), max(w_lon, e_lon), max(s_lat, n_lat)]
                        except Exception:
                            pass

                layers.append({
                    "name": tname,
                    "geometry_type": geom_type,
                    "crs": f"EPSG:{srs_id}",
                    "epsg": srs_id,
                    "feature_count": feat_count,
                    "bbox_wgs84": bbox_wgs84,
                })
    except Exception:
        pass
    return layers


def extract_gpkg_multi_layers(
    gpkg_path: Path,
    out_dir: Path,
    fallback_lat: float,
    fallback_lon: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], float, float]:
    """
    Extracts each layer of a `.gpkg` into its own `derived/vectors/layers/{layer_slug}.geojson`
    (reprojected to EPSG:4326 via `ogr2ogr`) AND builds a combined `overlay.geojson` preview
    with `_layer` property so both per-layer toggling and legacy single-file viewers work.
    """
    layers_meta = inspect_gpkg_layers(gpkg_path)
    layers_dir = out_dir / "derived" / "vectors" / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    extracted_layers: List[Dict[str, Any]] = []
    combined_features: List[Dict[str, Any]] = []
    all_lats: List[float] = []
    all_lons: List[float] = []

    ogr_bin = shutil.which("ogr2ogr")
    for idx, linfo in enumerate(layers_meta):
        lname = str(linfo["name"])
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", lname).strip("_") or f"layer_{idx}"
        out_gj_path = layers_dir / f"{slug}.geojson"
        layer_gj: Optional[Dict[str, Any]] = None

        if ogr_bin:
            cmd = [ogr_bin, "-f", "GeoJSON", "-t_srs", "EPSG:4326", str(out_gj_path), str(gpkg_path), lname]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode == 0 and out_gj_path.is_file():
                try:
                    layer_gj = json.loads(out_gj_path.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    layer_gj = None

        if not layer_gj or not isinstance(layer_gj.get("features"), list) or len(layer_gj.get("features") or []) == 0:
            # Pure-Python SQLite GeoPackage fallback (supports JSON/GeoJSON geometry or WKB/envelope fallback)
            try:
                import sqlite3
                fb_feats: List[Dict[str, Any]] = []
                with sqlite3.connect(f"file:{gpkg_path}?mode=ro", uri=True) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(f'SELECT * FROM "{lname}" LIMIT 10000').fetchall()
                    for r in rows:
                        rdict = dict(r)
                        geom_obj = None
                        props_out = {}
                        for k, v in rdict.items():
                            if isinstance(v, str) and v.strip().startswith("{") and '"type"' in v and '"coordinates"' in v:
                                try:
                                    geom_obj = json.loads(v)
                                    continue
                                except Exception:
                                    pass
                            if not isinstance(v, (bytes, bytearray)):
                                props_out[k] = v
                        if geom_obj is None:
                            bw = linfo.get("bbox_wgs84") or [fallback_lon, fallback_lat, fallback_lon, fallback_lat]
                            geom_obj = {"type": "Point", "coordinates": [(bw[0] + bw[2]) * 0.5, (bw[1] + bw[3]) * 0.5]}
                        fb_feats.append({"type": "Feature", "properties": props_out, "geometry": geom_obj})
                layer_gj = {"type": "FeatureCollection", "name": lname, "features": fb_feats}
            except Exception:
                pass

        if layer_gj and isinstance(layer_gj.get("features"), list):
            feats = layer_gj["features"]
            for f in feats:
                props = f.setdefault("properties", {})
                if isinstance(props, dict):
                    props.setdefault("_gpkg_layer", lname)
                geom = f.get("geometry") or {}
                coords = geom.get("coordinates")
                _collect_geojson_coords(coords, all_lons, all_lats)
            combined_features.extend(feats[:5000])
            out_gj_path.write_text(json.dumps(layer_gj, ensure_ascii=False), encoding="utf-8")
            extracted_layers.append({
                **linfo,
                "slug": slug,
                "relative_path": f"derived/vectors/layers/{slug}.geojson",
                "feature_count": len(feats),
                "size_bytes": out_gj_path.stat().st_size,
            })

    c_lat = float(sum(all_lats) / len(all_lats)) if all_lats else fallback_lat
    c_lon = float(sum(all_lons) / len(all_lons)) if all_lons else fallback_lon
    combined_gj = {"type": "FeatureCollection", "features": combined_features}
    comb_bytes = json.dumps(combined_gj, ensure_ascii=False).encode("utf-8")
    (out_dir / "overlay.geojson").write_bytes(comb_bytes)
    vec_dir = out_dir / "derived" / "vectors"
    vec_dir.mkdir(parents=True, exist_ok=True)
    (vec_dir / "overlay.geojson").write_bytes(comb_bytes)
    return extracted_layers, combined_gj, c_lat, c_lon


def _collect_geojson_coords(c: Any, lons: List[float], lats: List[float]) -> None:
    if isinstance(c, (list, tuple)) and len(c) >= 2 and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float)):
        lon_v, lat_v = float(c[0]), float(c[1])
        if -180.0 <= lon_v <= 180.0 and -90.0 <= lat_v <= 90.0:
            lons.append(lon_v)
            lats.append(lat_v)
    elif isinstance(c, list):
        for item in c:
            _collect_geojson_coords(item, lons, lats)


# ============================================================================
# SECTION 5 & 6 — DEM / DSM / DTM DUAL REPRESENTATION (COLOR RELIEF + TERRAIN)
# ============================================================================

PALETTE_STOPS: Dict[str, List[Tuple[float, Tuple[int, int, int]]]] = {
    "hypsometric": [
        (0.00, (36, 110, 68)),
        (0.25, (116, 178, 87)),
        (0.50, (224, 212, 118)),
        (0.75, (186, 122, 68)),
        (1.00, (245, 245, 248)),
    ],
    "viridis": [
        (0.00, (68, 1, 84)),
        (0.33, (49, 104, 142)),
        (0.66, (53, 183, 121)),
        (1.00, (253, 231, 37)),
    ],
    "terrain": [
        (0.00, (0, 96, 160)),
        (0.20, (52, 168, 83)),
        (0.55, (230, 200, 95)),
        (0.85, (150, 90, 50)),
        (1.00, (250, 250, 250)),
    ],
    "grayscale": [
        (0.00, (20, 20, 20)),
        (1.00, (245, 245, 245)),
    ],
}


def render_elevation_color_relief_and_hillshade(
    dem_arr: np.ndarray,
    nodata_val: Optional[float] = None,
    palette_name: str = "hypsometric",
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray, float, float]:
    """
    Computes:
      1. `color_relief_rgba` (H x W x 4 uint8) using `palette_name` and `[vmin, vmax]`
      2. `hillshade_rgba` (H x W x 4 uint8) shaded relief (315° azimuth, 45° altitude)
      3. `(eff_min, eff_max)` elevation in meters
    """
    arr = np.asarray(dem_arr, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[:, :, 0]

    valid_mask = np.isfinite(arr)
    if nodata_val is not None and math.isfinite(float(nodata_val)):
        valid_mask &= np.abs(arr - float(nodata_val)) > 1e-4
    valid_mask &= arr > -10000.0

    if np.any(valid_mask):
        eff_min = float(vmin) if vmin is not None else float(np.percentile(arr[valid_mask], 2.0))
        eff_max = float(vmax) if vmax is not None else float(np.percentile(arr[valid_mask], 98.0))
        if eff_max - eff_min < 1e-3:
            eff_min = float(np.min(arr[valid_mask]))
            eff_max = float(np.max(arr[valid_mask])) + 1.0
    else:
        eff_min, eff_max = 0.0, 100.0

    norm = np.clip((arr - eff_min) / max(eff_max - eff_min, 1e-3), 0.0, 1.0)
    stops = PALETTE_STOPS.get(palette_name, PALETTE_STOPS["hypsometric"])

    r_ch = np.zeros_like(norm, dtype=np.float32)
    g_ch = np.zeros_like(norm, dtype=np.float32)
    b_ch = np.zeros_like(norm, dtype=np.float32)

    for idx in range(len(stops) - 1):
        t0, c0 = stops[idx]
        t1, c1 = stops[idx + 1]
        seg = (norm >= t0) & (norm <= t1)
        w = np.clip((norm - t0) / max(t1 - t0, 1e-5), 0.0, 1.0)
        r_ch = np.where(seg, c0[0] + w * (c1[0] - c0[0]), r_ch)
        g_ch = np.where(seg, c0[1] + w * (c1[1] - c0[1]), g_ch)
        b_ch = np.where(seg, c0[2] + w * (c1[2] - c0[2]), b_ch)

    alpha_u8 = np.where(valid_mask, 255, 0).astype(np.uint8)
    color_rgba = np.dstack([
        np.clip(r_ch, 0, 255).astype(np.uint8),
        np.clip(g_ch, 0, 255).astype(np.uint8),
        np.clip(b_ch, 0, 255).astype(np.uint8),
        alpha_u8,
    ])

    # Compute analytical Hillshade (azimuth 315°, altitude 45°)
    clean_arr = np.where(valid_mask, arr, eff_min)
    gy, gx = np.gradient(clean_arr)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az = math.radians(315.0)
    alt = math.radians(45.0)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    hs_u8 = np.clip(hs * 255.0, 0, 255).astype(np.uint8)
    hillshade_rgba = np.dstack([hs_u8, hs_u8, hs_u8, alpha_u8])

    return color_rgba, hillshade_rgba, eff_min, eff_max


def build_elevation_derivatives(
    src_tif: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    bbox_wgs84: Optional[List[float]] = None,
    lat_c: Optional[float] = None,
    lon_c: Optional[float] = None,
    nodata_val: Optional[float] = None,
    src_file: Optional[Path] = None,
    fallback_lat: float = 41.8205,
    fallback_lon: float = 1.8676,
    palette: str = "hypsometric",
    crs_str: Optional[str] = None,
    vertical_datum_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates the two independent representations for a `RASTER_ELEVATION` (DEM/DSM/DTM) asset:
      A. Imagery Visualization (Default ON):
         - `derived/elevation/color-relief/color_relief.png` + `derived/elevation/color_relief.png` + XYZ tiles
         - `derived/elevation/hillshade/hillshade.png` + `derived/elevation/hillshade.png` + XYZ tiles
         - `derived/preview/preview.webp` + `preview.png` (blended color-relief + hillshade)
      B. Local High-Resolution Terrain Derivative (Default OFF):
         - `derived/terrain/layer.json`
         - `derived/terrain/elevation_grid.json` (65x65 Float32 height grid bounded to `bbox_wgs84`
           for `MosaicCesiumRuntime.applyLocalDemTerrain` composited over global terrain)
    """
    eff_src = src_tif or src_file
    if eff_src is None or out_dir is None:
        raise ValueError("src_tif/src_file and out_dir are required")

    eff_lat = float(lat_c if lat_c is not None else fallback_lat)
    eff_lon = float(lon_c if lon_c is not None else fallback_lon)

    insp = inspect_geospatial_asset(eff_src, eff_src.name) if (not bbox_wgs84 or len(bbox_wgs84) < 4 or not crs_str) else {}
    if not bbox_wgs84 or len(bbox_wgs84) < 4:
        if insp.get("bbox_wgs84") and len(insp["bbox_wgs84"]) == 4:
            bbox_wgs84 = [float(x) for x in insp["bbox_wgs84"]]
            eff_lon = (bbox_wgs84[0] + bbox_wgs84[2]) * 0.5
            eff_lat = (bbox_wgs84[1] + bbox_wgs84[3]) * 0.5
        else:
            bbox_wgs84 = [eff_lon - 0.002, eff_lat - 0.002, eff_lon + 0.002, eff_lat + 0.002]
    if nodata_val is None and insp.get("nodata") is not None:
        nodata_val = float(insp["nodata"])
    eff_crs_str = crs_str or str(insp.get("crs") or "")
    eff_v_hint = vertical_datum_hint or str(insp.get("vertical_datum_name") or insp.get("vertical_datum_type") or "")

    # Browser color/shade arrays multiply memory use. Reduce through GDAL before
    # decoding; the source DEM and native downloads remain unchanged.
    from .raster_products import raster_metadata, read_geographic_dem, build_geographic_image_pyramid
    dem_info = raster_metadata(eff_src)
    bbox_wgs84 = dem_info["bounds_wgs84"]
    eff_lon, eff_lat = (bbox_wgs84[0]+bbox_wgs84[2])/2, (bbox_wgs84[1]+bbox_wgs84[3])/2
    source_width, source_height = map(int, dem_info["size"])
    display_ratio = min(1.0, 4096 / max(source_width, source_height))
    display_size = [max(2, round(source_width * display_ratio)), max(2, round(source_height * display_ratio))]
    # Reprojection must precede geographic overlays and local terrain sampling.
    # Projected/rotated pixel rows cannot be stretched over a WGS84 bounding box.
    dem_arr = read_geographic_dem(eff_src, bbox_wgs84, display_size)

    valid_dem_mask_raw = np.isfinite(dem_arr) & (dem_arr > -10000.0)
    if nodata_val is not None and math.isfinite(float(nodata_val)):
        valid_dem_mask_raw &= np.abs(dem_arr - float(nodata_val)) > 1e-4
    raw_valid_vals = dem_arr[valid_dem_mask_raw]
    ortho_min = float(np.percentile(raw_valid_vals, 2.0)) if raw_valid_vals.size > 0 else 0.0
    ortho_max = float(np.percentile(raw_valid_vals, 98.0)) if raw_valid_vals.size > 0 else 0.0
    if ortho_max - ortho_min < 1e-3 and raw_valid_vals.size > 0:
        ortho_min = float(np.min(raw_valid_vals))
        ortho_max = float(np.max(raw_valid_vals)) + 1.0
    ortho_mean = float(np.mean(raw_valid_vals)) if raw_valid_vals.size > 0 else 0.0

    # Evaluate vertical datum and apply geoid undulation N (H -> h = H + N) once to valid DEM samples when applicable
    v_eval_dem = evaluate_vertical_datum_and_geoid_transform(
        crs_str=eff_crs_str,
        lat=eff_lat,
        lon=eff_lon,
        alt=ortho_mean,
        vertical_datum_hint=eff_v_hint,
        already_transformed=False,
    )
    geoid_shift_m = float(v_eval_dem.get("geoid_undulation_m") or 0.0) if v_eval_dem.get("vertical_transform_applied") else 0.0
    if abs(geoid_shift_m) > 1e-6:
        dem_arr = np.where(valid_dem_mask_raw, dem_arr + np.float32(geoid_shift_m), dem_arr).astype(np.float32)

    color_rgba, hs_rgba, eff_min, eff_max = render_elevation_color_relief_and_hillshade(
        dem_arr, nodata_val=nodata_val, palette_name=palette or "hypsometric"
    )
    valid_dem_mask_stats = np.isfinite(dem_arr) & (dem_arr > -10000.0)
    if nodata_val is not None and math.isfinite(float(nodata_val)):
        valid_dem_mask_stats &= np.abs(dem_arr - float(nodata_val)) > 1e-4
    valid_vals = dem_arr[valid_dem_mask_stats]
    eff_mean = float(np.mean(valid_vals)) if valid_vals.size > 0 else (eff_min + eff_max) * 0.5

    # Blend 75% color relief + 25% hillshade for crisp default preview
    blended_rgb = np.clip(
        color_rgba[:, :, :3].astype(np.float32) * (0.65 + 0.35 * (hs_rgba[:, :, :1].astype(np.float32) / 255.0)),
        0,
        255,
    ).astype(np.uint8)
    blended_rgba = np.dstack([blended_rgb, color_rgba[:, :, 3]])

    elev_dir = out_dir / "derived" / "elevation"
    cr_dir = elev_dir / "color-relief"
    hs_dir = elev_dir / "hillshade"
    ter_dir = out_dir / "derived" / "terrain"
    prev_dir = out_dir / "derived" / "preview"
    for d in (elev_dir, cr_dir, hs_dir, ter_dir, prev_dir):
        d.mkdir(parents=True, exist_ok=True)

    cr_im = Image.fromarray(blended_rgba, mode="RGBA")
    hs_im = Image.fromarray(hs_rgba, mode="RGBA")
    cr_png = cr_dir / "color_relief.png"
    hs_png = hs_dir / "hillshade.png"
    cr_im.save(cr_png, format="PNG")
    hs_im.save(hs_png, format="PNG")
    cr_im.save(elev_dir / "color_relief.png", format="PNG")
    hs_im.save(elev_dir / "hillshade.png", format="PNG")

    stats_path = elev_dir / "elevation_stats.json"
    stats_path.write_text(
        json.dumps({
            "min_elevation_m": round(eff_min, 2),
            "max_elevation_m": round(eff_max, 2),
            "mean_elevation_m": round(eff_mean, 2),
            "min_orthometric_m": round(ortho_min, 2),
            "max_orthometric_m": round(ortho_max, 2),
            "geoid_undulation_applied_m": round(geoid_shift_m, 4),
            "vertical_datum": v_eval_dem.get("vertical_datum"),
            "vertical_transform_status": v_eval_dem.get("vertical_transform_status"),
            "vertical_grid_used": v_eval_dem.get("vertical_grid_used"),
            "palette": palette or "hypsometric",
            "bbox_wgs84": bbox_wgs84,
            "source_dimensions": [source_width, source_height],
            "browser_dimensions": display_size,
        }, indent=2),
        encoding="utf-8",
    )

    # Save preview.webp + legacy preview.png
    prev_im = cr_im.copy()
    if max(prev_im.size) > 1024:
        prev_im.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    prev_webp = prev_dir / "preview.webp"
    prev_png_root = out_dir / "preview.png"
    prev_im.save(prev_webp, format="WEBP", quality=88)
    prev_im.save(prev_png_root, format="PNG")

    # Each pixel follows its real footprint across XYZ tile boundaries/zooms.
    tile_info = build_geographic_image_pyramid(cr_png, bbox_wgs84, cr_dir)
    for zoom_dir in cr_dir.iterdir():
        if zoom_dir.is_dir() and zoom_dir.name.isdigit():
            shutil.copytree(zoom_dir, out_dir / zoom_dir.name, dirs_exist_ok=True)
    build_geographic_image_pyramid(hs_png, bbox_wgs84, hs_dir)

    # Representation B: Multi-Resolution Quadtree Local Terrain Pyramid (65x65 samples PER TILE across levels z=0..max_z)
    # Uses normalized convolution (`valid_sum / weight_sum`) so NoData pixels NEVER bleed into adjacent valid pixels,
    # and preserves explicit NoData validity mask (`valid_mask`) so NoData pixels are never invented as `eff_min`.
    grid_h, grid_w = 65, 65
    valid_dem_mask = np.isfinite(dem_arr) & (dem_arr > -10000.0)
    if nodata_val is not None and math.isfinite(float(nodata_val)):
        valid_dem_mask &= np.abs(dem_arr - float(nodata_val)) > 1e-4
    weighted_dem = np.where(valid_dem_mask, dem_arr, 0.0).astype(np.float32)
    mask_f32 = valid_dem_mask.astype(np.float32)
    src_h, src_w = int(weighted_dem.shape[0]), int(weighted_dem.shape[1])
    max_dim = max(src_h, src_w)
    # Adaptive pyramid depth up to z=7 (8193x8193 effective samples); static pre-generation up to z=4 (341 tiles),
    # with z=5..7 served on-demand via `render_dynamic_terrain_tile_for_external_asset` and cached to disk!
    max_pyramid_z = max(2, min(7, int(math.ceil(math.log2(max(1.0, float(max_dim) / 64.0))))))
    pregen_max_z = min(max_pyramid_z, 4)

    west, south, east, north = [float(x) for x in bbox_wgs84]
    lon_span = max(1e-9, east - west)
    lat_span = max(1e-9, north - south)

    pyramid_levels_list: List[Dict[str, Any]] = []
    terrain_tiles_count = 0
    root_grid_f32: Optional[np.ndarray] = None
    root_valid_u8: Optional[np.ndarray] = None

    for z_lvl in range(pregen_max_z + 1):
        n_axis = 1 << z_lvl
        lvl_tiles: Dict[str, Dict[str, Any]] = {}
        for ty in range(n_axis):
            # Row 0 in raster is North, Row src_h is South
            t_north = north - (ty / n_axis) * lat_span
            t_south = north - ((ty + 1) / n_axis) * lat_span
            for tx in range(n_axis):
                t_west = west + (tx / n_axis) * lon_span
                t_east = west + ((tx + 1) / n_axis) * lon_span

                # Shared vertices use identical geographic coordinates at every
                # tile boundary, with normalized weights around NoData.
                tile_f32, tile_valid_u8 = _sample_dem_grid(
                    weighted_dem, mask_f32, tx/n_axis, (tx+1)/n_axis,
                    ty/n_axis, (ty+1)/n_axis, grid_w, grid_h, eff_min)
                if z_lvl == 0 and tx == 0 and ty == 0:
                    root_grid_f32 = tile_f32
                    root_valid_u8 = tile_valid_u8

                valid_tile_vals = tile_f32[tile_valid_u8 > 0]
                t_min = float(np.min(valid_tile_vals)) if valid_tile_vals.size > 0 else float(eff_min)
                t_max = float(np.max(valid_tile_vals)) if valid_tile_vals.size > 0 else float(eff_min)
                heights_list = [round(float(v), 2) for v in tile_f32.ravel()]
                valid_list = [int(v) for v in tile_valid_u8.ravel()]
                has_nodata = bool(np.any(tile_valid_u8 == 0))
                tile_doc = {
                    "z": z_lvl,
                    "x": tx,
                    "y": ty,
                    "bounds": [round(t_west, 8), round(t_south, 8), round(t_east, 8), round(t_north, 8)],
                    "width": grid_w,
                    "height": grid_h,
                    "min_elevation_m": round(t_min, 3),
                    "max_elevation_m": round(t_max, 3),
                    "has_nodata": has_nodata,
                    "valid_mask": valid_list if has_nodata else None,
                    "heights_row_major": heights_list,
                    "heights": heights_list,
                }
                if z_lvl <= 2:
                    lvl_tiles[f"{tx}_{ty}"] = {
                        "width": grid_w,
                        "height": grid_h,
                        "bounds": tile_doc["bounds"],
                        "has_nodata": has_nodata,
                        "valid_mask": valid_list if has_nodata else None,
                        "heights": heights_list,
                    }
                terrain_tiles_count += 1

                # Write both nested `{z}/{tx}/{ty}.json` and flat `{z}_{tx}_{ty}.json` for on-demand streaming
                tile_file = ter_dir / "tiles" / str(z_lvl) / str(tx) / f"{ty}.json"
                tile_file.parent.mkdir(parents=True, exist_ok=True)
                tile_json_str = json.dumps(tile_doc)
                tile_file.write_text(tile_json_str, encoding="utf-8")
                flat_tile_file = ter_dir / "tiles" / f"{z_lvl}_{tx}_{ty}.json"
                flat_tile_file.write_text(tile_json_str, encoding="utf-8")

        pyramid_levels_list.append({
            "z": z_lvl,
            "tiles_per_axis": n_axis,
            "tile_width": grid_w,
            "tile_height": grid_h,
            "effective_samples_per_axis": (grid_w - 1) * n_axis + 1,
            "inline_loaded": z_lvl <= 2,
            "pregenerated": True,
            "on_demand_tile_template": "tiles/{z}_{x}_{y}.json",
            "tiles": lvl_tiles,
        })

    for z_dyn in range(pregen_max_z + 1, max_pyramid_z + 1):
        n_axis_dyn = 1 << z_dyn
        pyramid_levels_list.append({
            "z": z_dyn,
            "tiles_per_axis": n_axis_dyn,
            "tile_width": grid_w,
            "tile_height": grid_h,
            "effective_samples_per_axis": (grid_w - 1) * n_axis_dyn + 1,
            "inline_loaded": False,
            "pregenerated": False,
            "on_demand_tile_template": "tiles/{z}_{x}_{y}.json",
            "tiles": {},
        })

    if root_grid_f32 is None:
        root_grid_f32 = np.full((grid_h, grid_w), eff_min, dtype=np.float32)
    if root_valid_u8 is None:
        root_valid_u8 = np.ones((grid_h, grid_w), dtype=np.uint8)

    eff_samples_max = (grid_w - 1) * (1 << max_pyramid_z) + 1
    root_has_nodata = bool(np.any(root_valid_u8 == 0))
    elev_grid_obj = {
        "format": "mosaicearth-local-dem-pyramid-v2",
        "version": 2,
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "bounds": [west, south, east, north],
        "source_dimensions": [src_w, src_h],
        "width": grid_w,
        "height": grid_h,
        "max_pyramid_level": max_pyramid_z,
        "inline_max_pyramid_level": min(2, max_pyramid_z),
        "on_demand_tile_template": "tiles/{z}_{x}_{y}.json",
        "total_pyramid_tiles": terrain_tiles_count,
        "effective_resolution": [eff_samples_max, eff_samples_max],
        "min_elevation_m": round(eff_min, 3),
        "max_elevation_m": round(eff_max, 3),
        "min_orthometric_m": round(ortho_min, 3),
        "max_orthometric_m": round(ortho_max, 3),
        "geoid_undulation_applied_m": round(geoid_shift_m, 4),
        "vertical_datum": v_eval_dem.get("vertical_datum"),
        "vertical_transform_status": v_eval_dem.get("vertical_transform_status"),
        "vertical_grid_used": v_eval_dem.get("vertical_grid_used"),
        "nodata": nodata_val,
        "has_nodata": root_has_nodata,
        "valid_mask": [int(v) for v in root_valid_u8.ravel()] if root_has_nodata else None,
        "heights_row_major": [round(float(v), 2) for v in root_grid_f32.ravel()],
        "heights": [round(float(v), 2) for v in root_grid_f32.ravel()],
        "pyramid": pyramid_levels_list,
    }
    grid_json_path = ter_dir / "elevation_grid.json"
    grid_json_path.write_text(json.dumps(elev_grid_obj), encoding="utf-8")

    layer_json_obj = {
        "tilejson": "2.1.0",
        "name": "MosaicEarth Multi-Resolution Local DEM Terrain Pyramid",
        "version": "1.2.0",
        "format": "heightmap-1.0",
        "crs": "EPSG:4326",
        "bounds": [west, south, east, north],
        "projection": "EPSG:4326",
        "minzoom": 0,
        "maxzoom": 20,
        "maxPyramidLevel": max_pyramid_z,
        "tileWidth": grid_w,
        "tileHeight": grid_h,
        "totalPyramidTiles": terrain_tiles_count,
        "effectiveResolution": [eff_samples_max, eff_samples_max],
        "tiles": ["tiles/{z}/{x}/{y}.json"],
        "attribution": "MosaicEarth Multi-Resolution Local Terrain Derivative",
        "elevationGridUrl": "elevation_grid.json",
        "defaultActive": False,
    }
    layer_json_path = ter_dir / "layer.json"
    layer_json_path.write_text(json.dumps(layer_json_obj, indent=2), encoding="utf-8")

    (out_dir / "bounds.json").write_text(
        json.dumps({
            "west": west,
            "south": south,
            "east": east,
            "north": north,
            "lat": eff_lat,
            "lon": eff_lon,
            "is_elevation": True,
            "min_elevation_m": round(eff_min, 2),
            "max_elevation_m": round(eff_max, 2),
            "max_pyramid_level": max_pyramid_z,
            "total_pyramid_tiles": terrain_tiles_count,
            "default_terrain_active": False,
            "min_zoom": tile_info["min_zoom"],
            "max_zoom": tile_info["max_zoom"],
            "georeferencing_version": 2,
        }),
        encoding="utf-8",
    )

    return {
        "lat": eff_lat,
        "lon": eff_lon,
        "bbox_wgs84": [west, south, east, north],
        "min_elevation_m": round(eff_min, 2),
        "max_elevation_m": round(eff_max, 2),
        "mean_elevation_m": round(eff_mean, 2),
        "min_orthometric_m": round(ortho_min, 2),
        "max_orthometric_m": round(ortho_max, 2),
        "geoid_undulation_applied_m": round(geoid_shift_m, 4),
        "vertical_eval": v_eval_dem,
        "max_pyramid_level": max_pyramid_z,
        "pregenerated_max_pyramid_level": pregen_max_z,
        "total_pyramid_tiles": terrain_tiles_count,
        "palette": palette or "hypsometric",
        "color_relief_png": str(cr_png),
        "hillshade_png": str(hs_png),
        "terrain_layer_json": str(layer_json_path),
        "terrain_grid_json": str(grid_json_path),
        "preview_webp": str(prev_webp),
    }


def render_dynamic_terrain_tile_for_external_asset(
    asset_dir: Path,
    z_lvl: int,
    tx: int,
    ty: int,
    source_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    On-demand generator for high-zoom local terrain pyramid tiles (`z >= 3` up to `z = 7`).
    If the tile already exists on disk, returns it immediately; otherwise crops `(z, tx, ty)`
    from the source DEM using normalized-convolution NoData-safe resampling, caches it to
    `derived/terrain/tiles/{z}/{tx}/{ty}.json` and `derived/terrain/tiles/{z}_{tx}_{ty}.json`,
    and returns the tile JSON dictionary.
    """
    ter_dir = asset_dir / "derived" / "terrain"
    flat_tile = ter_dir / "tiles" / f"{z_lvl}_{tx}_{ty}.json"
    nested_tile = ter_dir / "tiles" / str(z_lvl) / str(tx) / f"{ty}.json"
    if flat_tile.is_file():
        try:
            return json.loads(flat_tile.read_text(encoding="utf-8"))
        except Exception:
            pass
    if nested_tile.is_file():
        try:
            return json.loads(nested_tile.read_text(encoding="utf-8"))
        except Exception:
            pass

    if z_lvl < 0 or z_lvl > 7:
        return None
    n_axis = 1 << z_lvl
    if tx < 0 or tx >= n_axis or ty < 0 or ty >= n_axis:
        return None

    grid_json_path = ter_dir / "elevation_grid.json"
    if not grid_json_path.is_file():
        return None
    try:
        grid_meta = json.loads(grid_json_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    eff_src = source_path
    if eff_src is None or not eff_src.is_file():
        src_dir = asset_dir / "source"
        if src_dir.is_dir():
            cands = [p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in (".tif", ".tiff", ".png")]
            if cands:
                eff_src = cands[0]
    if eff_src is None or not eff_src.is_file():
        return None

    nodata_val = grid_meta.get("nodata")
    eff_min = float(grid_meta.get("min_elevation_m") or 0.0)
    geoid_shift_m = float(grid_meta.get("geoid_undulation_applied_m") or 0.0)
    west, south, east, north = map(float, grid_meta["bounds"])
    lon_span, lat_span = east-west, north-south
    t_north, t_south = north-ty/n_axis*lat_span, north-(ty+1)/n_axis*lat_span
    t_west, t_east = west+tx/n_axis*lon_span, west+(tx+1)/n_axis*lon_span
    grid_w = grid_h = 65
    from .raster_products import read_geographic_dem
    try:
        tile_f32 = read_geographic_dem(eff_src, [t_west,t_south,t_east,t_north], [grid_w,grid_h], vertex_samples=True)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    tile_valid_u8 = (np.isfinite(tile_f32) & (tile_f32 > -10000)).astype(np.uint8)
    tile_f32 = np.where(tile_valid_u8, tile_f32+geoid_shift_m, eff_min).astype(np.float32)

    valid_tile_vals = tile_f32[tile_valid_u8 > 0]
    t_min = float(np.min(valid_tile_vals)) if valid_tile_vals.size > 0 else float(eff_min)
    t_max = float(np.max(valid_tile_vals)) if valid_tile_vals.size > 0 else float(eff_min)
    heights_list = [round(float(v), 2) for v in tile_f32.ravel()]
    valid_list = [int(v) for v in tile_valid_u8.ravel()]
    has_nodata = bool(np.any(tile_valid_u8 == 0))
    tile_doc = {
        "z": z_lvl,
        "x": tx,
        "y": ty,
        "bounds": [round(t_west, 8), round(t_south, 8), round(t_east, 8), round(t_north, 8)],
        "width": grid_w,
        "height": grid_h,
        "min_elevation_m": round(t_min, 3),
        "max_elevation_m": round(t_max, 3),
        "has_nodata": has_nodata,
        "valid_mask": valid_list if has_nodata else None,
        "heights_row_major": heights_list,
        "heights": heights_list,
    }
    try:
        nested_tile.parent.mkdir(parents=True, exist_ok=True)
        tile_json_str = json.dumps(tile_doc)
        nested_tile.write_text(tile_json_str, encoding="utf-8")
        flat_tile.write_text(tile_json_str, encoding="utf-8")
    except Exception:
        pass
    return tile_doc


def _sample_dem_grid(weighted, mask, left, right, top, bottom, width, height, fallback):
    """Bilinear geographic vertex samples, preserving holes and shared edges."""
    rows, columns = weighted.shape
    xx = np.clip(np.linspace(left*columns-.5, right*columns-.5, width), 0, columns-1)
    yy = np.clip(np.linspace(top*rows-.5, bottom*rows-.5, height), 0, rows-1)
    x0, y0 = np.floor(xx).astype(int), np.floor(yy).astype(int)
    x1, y1 = np.minimum(x0+1, columns-1), np.minimum(y0+1, rows-1)
    wx, wy = (xx-x0)[None,:], (yy-y0)[:,None]
    def interpolate(array):
        return ((1-wy)*((1-wx)*array[y0[:,None],x0]+wx*array[y0[:,None],x1])
                + wy*((1-wx)*array[y1[:,None],x0]+wx*array[y1[:,None],x1]))
    weights = interpolate(mask)
    nearest = mask[np.rint(yy).astype(int)[:,None], np.rint(xx).astype(int)]
    valid = ((weights >= .25) & (nearest >= .5)).astype(np.uint8)
    values = np.where(valid, interpolate(weighted)/np.maximum(weights,1e-6), fallback)
    return values.astype(np.float32), valid


# ============================================================================
# SECTION 8 — HIERARCHICAL POINT CLOUD 3D TILES 1.1 (`glTF POINTS` + OCTREE LOD)
# ============================================================================

def _build_gltf_points_glb(
    positions: np.ndarray,
    colors_u8: np.ndarray,
    extra_attrs: Optional[Dict[str, np.ndarray]] = None,
) -> bytes:
    """
    Encodes a point cloud node as a standard glTF 2.0 Binary (`.glb`) primitive with `mode: 0` (`POINTS`),
    supported natively by 3D Tiles 1.1 in CesiumJS 1.144+, including custom vertex attributes
    (`_INTENSITY`, `_CLASSIFICATION`, `_RETURN_NUMBER`).
    """
    pos_f = np.ascontiguousarray(positions, dtype="<f4")
    rgb_f = np.ascontiguousarray(colors_u8[:, :3].astype(np.float32) / 255.0, dtype="<f4")
    n = int(pos_f.shape[0])
    v_min = [float(x) for x in pos_f.min(axis=0)] if n > 0 else [0.0, 0.0, 0.0]
    v_max = [float(x) for x in pos_f.max(axis=0)] if n > 0 else [0.0, 0.0, 0.0]

    pos_bytes = pos_f.tobytes()
    col_bytes = rgb_f.tobytes()
    chunks = [pos_bytes, col_bytes]
    buffer_views = [
        {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": 34962},
        {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": len(col_bytes), "target": 34962},
    ]
    accessors = [
        {
            "bufferView": 0,
            "byteOffset": 0,
            "componentType": 5126,
            "count": n,
            "type": "VEC3",
            "min": v_min,
            "max": v_max,
        },
        {
            "bufferView": 1,
            "byteOffset": 0,
            "componentType": 5126,
            "count": n,
            "type": "VEC3",
        },
    ]
    prim_attributes: Dict[str, int] = {"POSITION": 0, "COLOR_0": 1}
    curr_offset = len(pos_bytes) + len(col_bytes)

    if extra_attrs and n > 0:
        for attr_name, attr_arr in extra_attrs.items():
            if attr_arr is None or len(attr_arr) != n:
                continue
            gltf_attr_key = f"_{attr_name.upper().lstrip('_')}"
            arr_f = np.ascontiguousarray(attr_arr, dtype="<f4").reshape(n)
            a_bytes = arr_f.tobytes()
            bv_idx = len(buffer_views)
            buffer_views.append({"buffer": 0, "byteOffset": curr_offset, "byteLength": len(a_bytes), "target": 34962})
            accessors.append({
                "bufferView": bv_idx,
                "byteOffset": 0,
                "componentType": 5126,
                "count": n,
                "type": "SCALAR",
                "min": [float(arr_f.min())],
                "max": [float(arr_f.max())],
            })
            prim_attributes[gltf_attr_key] = bv_idx
            chunks.append(a_bytes)
            curr_offset += len(a_bytes)

    bin_chunk = b"".join(chunks)
    while len(bin_chunk) % 4 != 0:
        bin_chunk += b"\x00"

    gltf_dict = {
        "asset": {"version": "2.0", "generator": "MosaicEarth 3D Tiles 1.1 PointCloud Tiler"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "mesh": 0,
                "matrix": [
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, -1.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            }
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": prim_attributes,
                        "mode": 0,
                    }
                ]
            }
        ],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    json_bytes = json.dumps(gltf_dict, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "

    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
    return (
        struct.pack("<4sII", b"glTF", 2, total_len)
        + struct.pack("<II", len(json_bytes), 0x4E4F534A)
        + json_bytes
        + struct.pack("<II", len(bin_chunk), 0x004E4942)
        + bin_chunk
    )


def build_hierarchical_pointcloud_3dtiles(
    positions_enu: Optional[np.ndarray] = None,
    colors_rgb: Optional[np.ndarray] = None,
    out_dir: Optional[Path] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    alt: float = 0.0,
    enu_matrix_fn: Optional[Callable[..., List[float]]] = None,
    thresholds: GeospatialThresholds = THRESHOLDS,
    source_path: Optional[Path] = None,
    raw_bytes: Optional[bytes] = None,
    ext_low: str = "las",
    fallback_lat: float = 41.8205,
    fallback_lon: float = 1.8676,
    extra_point_attrs: Optional[Dict[str, np.ndarray]] = None,
    vertical_offset_m: float = 0.0,
) -> Dict[str, Any]:
    """
    Builds a scalable 3D Tiles 1.1 hierarchical point cloud tileset (`derived/3dtiles/` + `derived/pointcloud/3dtiles/` + root `tileset.json`):
      - Small datasets (`<= POINTCLOUD_DIRECT_LIMIT`): single root LOD (`lod0_root.glb` + legacy `points.pnts`).
      - Large datasets (`> POINTCLOUD_DIRECT_LIMIT`):
          * Root coarse LOD subsampled to `POINTCLOUD_MAX_POINTS_PER_TILE`
          * Spatial octree subdivision (`children` tiles `oct_{0..7}.glb`) loaded dynamically by SSE & camera distance!
    """
    if out_dir is None:
        raise ValueError("out_dir is required")

    from .gis_splat_engine import _build_enu_to_ecef_matrix, _parse_pointcloud_or_splat_bytes, _write_pnts_and_tileset

    eff_lat = float(lat if lat is not None else fallback_lat)
    eff_lon = float(lon if lon is not None else fallback_lon)
    attrs_preserved = ["XYZ", "RGB"]
    if extra_point_attrs is None:
        extra_point_attrs = {}
    else:
        for k_ex in extra_point_attrs.keys():
            if k_ex not in attrs_preserved:
                attrs_preserved.append(k_ex)

    if positions_enu is None or colors_rgb is None:
        if source_path is not None or raw_bytes is not None:
            rb = raw_bytes if raw_bytes is not None else (source_path.read_bytes() if source_path else b"")
            eff_ext = (ext_low or (source_path.suffix.lower().lstrip(".") if source_path else "las")).lower()
            positions_enu, colors_rgb, det_ll = _parse_pointcloud_or_splat_bytes(rb, eff_ext, max_points=None)
            if det_ll is not None:
                eff_lat, eff_lon = float(det_ll[0]), float(det_ll[1])
                if len(det_ll) > 2:
                    alt = float(det_ll[2])
            if eff_ext in ("las", "laz") and not extra_point_attrs:
                try:
                    import laspy
                    las = laspy.open(io.BytesIO(rb)).read()
                    dim_names = {d.lower(): d for d in las.point_format.dimension_names}
                    for cand in ("intensity", "classification", "return_number", "point_source_id", "gps_time"):
                        if cand in dim_names and cand not in attrs_preserved:
                            attrs_preserved.append(cand)
                            try:
                                raw_dim = np.asarray(getattr(las, dim_names[cand]), dtype=np.float32)
                                if len(raw_dim) == len(positions_enu):
                                    extra_point_attrs[cand] = raw_dim
                            except Exception:
                                pass
                except Exception:
                    pass
        else:
            raise ValueError("A point cloud source is required")

    # Only an explicitly verified datum conversion or user offset may shift height.
    alt += float(vertical_offset_m)
    pc_dir = out_dir / "derived" / "pointcloud" / "3dtiles"
    tiles_dir = out_dir / "derived" / "3dtiles"
    pc_dir.mkdir(parents=True, exist_ok=True)
    tiles_dir.mkdir(parents=True, exist_ok=True)

    n_total = int(positions_enu.shape[0])
    if n_total == 0:
        raise ValueError("Point cloud contains no points")

    pos_f = np.ascontiguousarray(positions_enu, dtype=np.float32)
    rgb_u8 = np.ascontiguousarray(colors_rgb, dtype=np.uint8)

    min_xyz = pos_f.min(axis=0)
    max_xyz = pos_f.max(axis=0)
    center = (min_xyz + max_xyz) * 0.5
    pos_c = pos_f  # Keep the ENU origin; centering without translating the root displaces geometry.
    half_ext = np.maximum((max_xyz - min_xyz) * 0.5, np.array([0.25, 0.25, 0.1], dtype=np.float32))
    hx, hy, hz = float(half_ext[0]), float(half_ext[1]), float(half_ext[2])

    max_per_tile = max(50, thresholds.POINTCLOUD_MAX_POINTS_PER_TILE)
    is_hierarchical = n_total > thresholds.POINTCLOUD_DIRECT_LIMIT

    all_indices = np.arange(n_total, dtype=np.int64)
    tile_count = 1
    max_octree_depth_reached = 0

    # Root coarse LOD + disjoint remainder for recursive multi-level octree
    if is_hierarchical:
        step = max(2, int(math.ceil(n_total / float(max_per_tile))))
        root_mask = np.zeros(n_total, dtype=bool)
        root_mask[::step] = True
        root_idx = all_indices[root_mask]
        rem_idx = all_indices[~root_mask]
        root_pos = pos_c[root_idx]
        root_rgb = rgb_u8[root_idx]
        root_extra = {k: v[root_idx] for k, v in extra_point_attrs.items() if len(v) == n_total}
    else:
        root_pos = pos_c
        root_rgb = rgb_u8
        rem_idx = np.empty(0, dtype=np.int64)
        root_extra = {k: v for k, v in extra_point_attrs.items() if len(v) == n_total}

    root_glb = _build_gltf_points_glb(root_pos, root_rgb, extra_attrs=root_extra)
    for d_out in (pc_dir, tiles_dir, out_dir):
        (d_out / "lod0_root.glb").write_bytes(root_glb)

    diag_root = float(np.linalg.norm(max_xyz - min_xyz)) if n_total > 1 else 50.0
    base_geom_error = max(16.0, round(diag_root * 0.35, 2))

    def _subdivide_octree_recursive(
        idxs: np.ndarray,
        n_min: np.ndarray,
        n_max: np.ndarray,
        depth: int,
        prefix: str,
        parent_geom_err: float,
        max_depth: int = 8,
    ) -> List[Dict[str, Any]]:
        nonlocal tile_count, max_octree_depth_reached
        if len(idxs) == 0:
            return []
        max_octree_depth_reached = max(max_octree_depth_reached, depth)
        mid = (n_min + n_max) * 0.5
        nodes_out: List[Dict[str, Any]] = []

        pts_sub = pos_c[idxs]
        oct_codes = (
            (pts_sub[:, 0] >= mid[0]).astype(np.uint8)
            | ((pts_sub[:, 1] >= mid[1]).astype(np.uint8) << 1)
            | ((pts_sub[:, 2] >= mid[2]).astype(np.uint8) << 2)
        )
        for oct_idx in range(8):
            sx = 1 if (oct_idx & 1) else -1
            sy = 1 if (oct_idx & 2) else -1
            sz = 1 if (oct_idx & 4) else -1
            oct_idxs = idxs[oct_codes == oct_idx]
            if len(oct_idxs) == 0:
                continue

            c_min = np.array([
                mid[0] if sx > 0 else n_min[0],
                mid[1] if sy > 0 else n_min[1],
                mid[2] if sz > 0 else n_min[2],
            ], dtype=np.float32)
            c_max = np.array([
                n_max[0] if sx > 0 else mid[0],
                n_max[1] if sy > 0 else mid[1],
                n_max[2] if sz > 0 else mid[2],
            ], dtype=np.float32)

            node_id = f"{prefix}_{oct_idx}"
            oct_fname = f"{node_id}.glb"

            # Recursive subdivision: if this octant still exceeds the point budget and depth < max_depth,
            # retain `max_per_tile` representative points at this LOD level and push remaining points to deeper octants!
            # At leaf octants (`not need_split`), retain 100% of `oct_idxs` so ZERO points are ever dropped across the additive octree!
            need_split = (len(oct_idxs) > max_per_tile) and (depth < max_depth)
            if need_split:
                o_step = max(2, int(math.ceil(len(oct_idxs) / float(max_per_tile))))
                keep_mask = np.zeros(len(oct_idxs), dtype=bool)
                keep_mask[::o_step] = True
                my_idxs = oct_idxs[keep_mask]
                child_rem_idxs = oct_idxs[~keep_mask]
            else:
                my_idxs = oct_idxs
                child_rem_idxs = np.empty(0, dtype=np.int64)

            oct_pos = pos_c[my_idxs]
            oct_rgb = rgb_u8[my_idxs]
            oct_extra = {k: v[my_idxs] for k, v in extra_point_attrs.items() if len(v) == n_total}
            oct_glb = _build_gltf_points_glb(oct_pos, oct_rgb, extra_attrs=oct_extra)
            for d_out in (pc_dir, tiles_dir, out_dir):
                (d_out / oct_fname).write_bytes(oct_glb)
            tile_count += 1

            all_oct_pos = pos_c[oct_idxs]
            o_min = all_oct_pos.min(axis=0)
            o_max = all_oct_pos.max(axis=0)
            o_c = (o_min + o_max) * 0.5
            o_h = np.maximum((o_max - o_min) * 0.5, np.array([0.5, 0.5, 0.5], dtype=np.float32))

            sub_children: List[Dict[str, Any]] = []
            node_geom_err = round(parent_geom_err * 0.5, 3) if need_split else 0.0
            if need_split and len(child_rem_idxs) > 0:
                sub_children = _subdivide_octree_recursive(
                    child_rem_idxs,
                    c_min,
                    c_max,
                    depth + 1,
                    node_id,
                    max(1.0, parent_geom_err * 0.5),
                    max_depth=max_depth,
                )
                if not sub_children:
                    node_geom_err = 0.0

            node_dict: Dict[str, Any] = {
                "boundingVolume": {
                    "box": [
                        float(o_c[0]), float(o_c[1]), float(o_c[2]),
                        float(o_h[0]), 0.0, 0.0,
                        0.0, float(o_h[1]), 0.0,
                        0.0, 0.0, float(o_h[2]),
                    ]
                },
                "geometricError": node_geom_err,
                "refine": "ADD",
                "content": {"uri": oct_fname},
            }
            if sub_children:
                node_dict["children"] = sub_children
            nodes_out.append(node_dict)
        return nodes_out

    children_nodes: List[Dict[str, Any]] = []
    if is_hierarchical and len(rem_idx) > 0:
        children_nodes = _subdivide_octree_recursive(
            rem_idx,
            pos_c.min(axis=0),
            pos_c.max(axis=0),
            depth=1,
            prefix="oct",
            parent_geom_err=base_geom_error,
            max_depth=8,
        )

    # Write legacy points.pnts first, then overwrite tileset.json with 3D Tiles 1.1 recursive octree structure
    _write_pnts_and_tileset(out_dir, root_pos, root_rgb, eff_lat, eff_lon, alt)

    enu_mat = (enu_matrix_fn or _build_enu_to_ecef_matrix)(float(eff_lat), float(eff_lon), float(alt))
    root_node: Dict[str, Any] = {
        "transform": enu_mat,
        "boundingVolume": {
            "box": [float(center[0]), float(center[1]), float(center[2]), hx, 0.0, 0.0, 0.0, hy, 0.0, 0.0, 0.0, hz]
        },
        "geometricError": base_geom_error if is_hierarchical else 0.0,
        "refine": "ADD",
        "content": {"uri": "lod0_root.glb"},
    }
    if children_nodes:
        root_node["children"] = children_nodes

    tileset_11 = {
        "asset": {
            "version": "1.1",
            "generator": "MosaicEarth Recursive Multi-Level Octree PointCloud 3D Tiles 1.1",
            "octreeDepth": max_octree_depth_reached,
            "maxPointsPerTile": max_per_tile,
        },
        "geometricError": round(base_geom_error * 2.5, 2) if is_hierarchical else 32.0,
        "root": root_node,
    }
    ts_json = json.dumps(tileset_11, indent=2)
    for d_out in (pc_dir, tiles_dir, out_dir):
        (d_out / "tileset.json").write_text(ts_json, encoding="utf-8")

    return {
        "lat": eff_lat,
        "lon": eff_lon,
        "alt": alt,
        "total_points": n_total,
        "hierarchical": is_hierarchical,
        "is_hierarchical": is_hierarchical,
        "octree_depth": max_octree_depth_reached,
        "tile_count": tile_count,
        "attributes_preserved": attrs_preserved,
        "tileset_rel": "derived/3dtiles/tileset.json",
    }


# ============================================================================
# SECTION 9 — SMALL VS LARGE 3D MESH PIPELINE (`GLB` vs `3D Tiles 1.1` LOD)
# ============================================================================

def _simplify_mesh_vertex_clustering(
    verts: np.ndarray,
    faces: np.ndarray,
    grid_divs: int = 16,
    uvs: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """
    Fast, dependency-free geometric mesh simplification via 3D uniform grid vertex clustering.
    Clusters vertices into `grid_divs^3` spatial cells, computes centroid vertices and UVs per cell,
    remaps triangle indices, and removes degenerate/collapsed triangles.
    """
    has_uvs = uvs is not None and len(uvs) == len(verts)
    if len(verts) == 0 or len(faces) == 0:
        return verts, faces, (uvs if has_uvs else None)
    sub_u, inv = np.unique(faces.reshape(-1), return_inverse=True)
    sub_v = verts[sub_u]
    sub_uv = uvs[sub_u].astype(np.float32) if has_uvs else None
    sub_f = inv.reshape(-1, 3).astype(np.uint32)
    if len(sub_f) <= 12:
        return sub_v, sub_f, sub_uv

    vmin = sub_v.min(axis=0)
    vmax = sub_v.max(axis=0)
    span = np.maximum(vmax - vmin, 1e-5)
    coords = np.floor(((sub_v - vmin) / span) * float(grid_divs - 1)).astype(np.int64)
    coords = np.clip(coords, 0, grid_divs - 1)
    spatial_keys = coords[:, 0] * (grid_divs * grid_divs) + coords[:, 1] * grid_divs + coords[:, 2]
    if sub_uv is not None:
        # Include quantized UV bin in cluster key so vertices across UV seams are never merged into distorted UV centroids!
        uv_bins = 8
        uv_wrapped = np.mod(sub_uv, 1.0)
        uv_coords = np.clip(np.floor(uv_wrapped * float(uv_bins)).astype(np.int64), 0, uv_bins - 1)
        uv_keys = uv_coords[:, 0] * uv_bins + uv_coords[:, 1]
        cell_keys = spatial_keys * (uv_bins * uv_bins) + uv_keys
    else:
        cell_keys = spatial_keys

    unique_keys, key_inv = np.unique(cell_keys, return_inverse=True)
    n_clusters = len(unique_keys)
    cluster_verts = np.zeros((n_clusters, 3), dtype=np.float32)
    counts = np.bincount(key_inv, minlength=n_clusters).astype(np.float32)
    counts = np.maximum(counts, 1.0)
    for axis in range(3):
        cluster_verts[:, axis] = np.bincount(key_inv, weights=sub_v[:, axis], minlength=n_clusters) / counts

    cluster_uvs: Optional[np.ndarray] = None
    if sub_uv is not None:
        cluster_uvs = np.zeros((n_clusters, 2), dtype=np.float32)
        for u_axis in range(2):
            cluster_uvs[:, u_axis] = np.bincount(key_inv, weights=sub_uv[:, u_axis], minlength=n_clusters) / counts

    remapped_f = key_inv[sub_f].astype(np.uint32)
    non_deg = (
        (remapped_f[:, 0] != remapped_f[:, 1])
        & (remapped_f[:, 1] != remapped_f[:, 2])
        & (remapped_f[:, 2] != remapped_f[:, 0])
    )
    clean_f = remapped_f[non_deg]
    if len(clean_f) < 4:
        # Fallback to stride decimation if clustering collapsed nearly everything (e.g. coplanar quad)
        step = max(1, len(sub_f) // max(4, grid_divs * 2))
        return sub_v, sub_f[::step], sub_uv
    return cluster_verts, clean_f, cluster_uvs


def _compute_vertex_normals_numpy(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """
    Computes smooth area-weighted vertex normals in pure vectorized NumPy (no SciPy dependency).
    """
    vn = np.zeros_like(verts, dtype=np.float32)
    if len(verts) == 0 or len(faces) == 0:
        vn[:, 1] = 1.0
        return vn
    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0).astype(np.float32)
    np.add.at(vn, faces[:, 0], fn)
    np.add.at(vn, faces[:, 1], fn)
    np.add.at(vn, faces[:, 2], fn)
    lengths = np.linalg.norm(vn, axis=1, keepdims=True)
    valid = lengths[:, 0] > 1e-8
    vn[valid] = vn[valid] / lengths[valid]
    vn[~valid] = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    return vn


def _compact_submesh_to_glb(
    verts: np.ndarray,
    faces: np.ndarray,
    uvs: Optional[np.ndarray] = None,
    material: Any = None,
) -> Tuple[bytes, np.ndarray, np.ndarray]:
    """
    Compacts a face subset so only referenced vertices (and their UVs, vertex normals, & PBR material/texture)
    are exported to GLB, and returns `(glb_bytes, bbox_center, bbox_half_extents)`.
    """
    import trimesh
    u_idx, inv = np.unique(faces.reshape(-1), return_inverse=True)
    c_verts = verts[u_idx].astype(np.float32)
    c_faces = inv.reshape(-1, 3).astype(np.uint32)
    vmin = c_verts.min(axis=0)
    vmax = c_verts.max(axis=0)
    b_center = (vmin + vmax) * 0.5
    b_half = np.maximum((vmax - vmin) * 0.5, np.array([1.0, 1.0, 0.5], dtype=np.float32))
    # Convert ENU Z-up (East, North, Up) -> glTF 2.0 Y-up (X = East, Y = Up, Z = -North)
    # so CesiumGltfLoader's Axis.Y_UP_TO_Z_UP transform places the mesh in exact ENU alignment!
    c_verts_yup = np.column_stack([c_verts[:, 0], c_verts[:, 2], -c_verts[:, 1]]).astype(np.float32)
    c_normals_yup = _compute_vertex_normals_numpy(c_verts_yup, c_faces)
    visual_obj = None
    if uvs is not None and len(uvs) == len(verts):
        try:
            c_uvs = uvs[u_idx].astype(np.float32)
            visual_obj = trimesh.visual.TextureVisuals(uv=c_uvs, material=material)
        except Exception:
            visual_obj = None
    sub_m = trimesh.Trimesh(
        vertices=c_verts_yup,
        faces=c_faces,
        vertex_normals=c_normals_yup,
        visual=visual_obj,
        process=False,
    )
    return sub_m.export(file_type="glb"), b_center, b_half


def _parse_engineering_cad_bim_mesh_bytes(
    raw_bytes: bytes,
    ext_low: str,
    original_filename: str = "",
) -> Optional[Tuple[np.ndarray, np.ndarray, Dict[str, Any]]]:
    """
    Parses engineering, CAD, BIM, and 3D mesh formats (`STL`, `DAE`/COLLADA, `FBX`, `IFC`,
    `CityJSON`, `LandXML`, `GLTF`) into `(vertices_f32, faces_u32, info_dict)`.
    """
    info: Dict[str, Any] = {}
    fname_l = (original_filename or "").lower()
    eff_ext = ext_low.lower().lstrip(".")
    if fname_l.endswith(".city.json"):
        eff_ext = "cityjson"
    elif eff_ext == "json" and b'"CityJSON"' in raw_bytes[:16384]:
        eff_ext = "cityjson"
    elif eff_ext == "xml" and b"<LandXML" in raw_bytes[:16384]:
        eff_ext = "landxml"

    # 1. STL (ASCII `solid ...` or Binary 80-byte header + uint32 count + 50-byte triangles)
    if eff_ext == "stl":
        try:
            import trimesh
            m_stl = trimesh.load(io.BytesIO(raw_bytes), file_type="stl", force="mesh")
            if hasattr(m_stl, "vertices") and hasattr(m_stl, "faces") and len(m_stl.faces) > 0:
                return np.asarray(m_stl.vertices, dtype=np.float32), np.asarray(m_stl.faces, dtype=np.uint32), info
        except Exception:
            pass
        if len(raw_bytes) >= 84 and not raw_bytes[:80].lstrip().startswith(b"solid "):
            n_tri = struct.unpack_from("<I", raw_bytes, 80)[0]
            if 0 < n_tri <= (len(raw_bytes) - 84) // 50:
                dt_stl = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")])
                recs = np.frombuffer(raw_bytes, dtype=dt_stl, offset=84, count=n_tri)
                verts = recs["v"].reshape(-1, 3).astype(np.float32)
                faces = np.arange(len(verts), dtype=np.uint32).reshape(-1, 3)
                return verts, faces, info

    # 2. COLLADA (.dae XML `<COLLADA`)
    if eff_ext == "dae" or b"<COLLADA" in raw_bytes[:4096]:
        try:
            import xml.etree.ElementTree as ET
            root_el = ET.fromstring(raw_bytes.decode("utf-8", errors="ignore"))
            verts_list: List[np.ndarray] = []
            faces_list: List[np.ndarray] = []
            v_offset = 0
            for mesh_el in root_el.iter():
                if not mesh_el.tag.endswith("mesh"):
                    continue
                float_arrays: Dict[str, np.ndarray] = {}
                for fa in mesh_el.iter():
                    if fa.tag.endswith("float_array") and fa.attrib.get("id") and fa.text:
                        arr_f = np.fromstring(fa.text, sep=" ", dtype=np.float32)
                        if len(arr_f) >= 3:
                            float_arrays[fa.attrib["id"]] = arr_f.reshape(-1, 3)
                if not float_arrays:
                    continue
                v_arr_m = next(iter(float_arrays.values()))
                for tri_el in mesh_el.iter():
                    if tri_el.tag.endswith(("triangles", "polylist", "polygons")):
                        inputs = [ch for ch in tri_el if ch.tag.endswith("input")]
                        stride = max(1, len(inputs))
                        for p_el in tri_el:
                            if p_el.tag.endswith("p") and p_el.text:
                                idxs = np.fromstring(p_el.text, sep=" ", dtype=np.uint32)
                                v_idxs = idxs[0::stride]
                                n_t = len(v_idxs) // 3
                                if n_t > 0:
                                    faces_list.append(v_idxs[: n_t * 3].reshape(-1, 3) + v_offset)
                verts_list.append(v_arr_m)
                v_offset += len(v_arr_m)
            if verts_list and faces_list:
                return np.vstack(verts_list).astype(np.float32), np.vstack(faces_list).astype(np.uint32), info
        except Exception:
            pass

    # 3. CityJSON (`.city.json` / `"type": "CityJSON"`)
    if eff_ext == "cityjson":
        try:
            cj = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
            raw_verts = np.asarray(cj.get("vertices") or [], dtype=np.float64)
            tr_cj = cj.get("transform") or {}
            if len(raw_verts) > 0 and tr_cj.get("scale") and tr_cj.get("translate"):
                sc = np.asarray(tr_cj["scale"], dtype=np.float64)
                tl = np.asarray(tr_cj["translate"], dtype=np.float64)
                raw_verts = raw_verts * sc + tl
            ref_sys = (cj.get("metadata") or {}).get("referenceSystem") or ""
            if ref_sys:
                m_epsg = re.search(r"(\d{4,5})$", str(ref_sys).strip())
                info["crs"] = f"EPSG:{m_epsg.group(1)}" if m_epsg else str(ref_sys)
                if len(raw_verts) > 0 and m_epsg:
                    try:
                        from pyproj import Transformer
                        tr_wgs = Transformer.from_crs(info["crs"], "EPSG:4326", always_xy=True)
                        cx_m, cy_m = float(raw_verts[:, 0].mean()), float(raw_verts[:, 1].mean())
                        lon_c, lat_c = tr_wgs.transform(cx_m, cy_m)
                        if -90.0 <= lat_c <= 90.0 and -180.0 <= lon_c <= 180.0:
                            info["lat"], info["lon"] = float(lat_c), float(lon_c)
                    except Exception:
                        pass
            if len(raw_verts) > 0:
                verts_f32 = (raw_verts - raw_verts.mean(axis=0)).astype(np.float32)
                tris: List[List[int]] = []

                def _extract_cj_boundaries(b_node: Any) -> None:
                    if not isinstance(b_node, list) or not b_node:
                        return
                    if isinstance(b_node[0], int):
                        ring = [int(x) for x in b_node if 0 <= int(x) < len(verts_f32)]
                        for i in range(1, len(ring) - 1):
                            tris.append([ring[0], ring[i], ring[i + 1]])
                    else:
                        for sub in b_node:
                            _extract_cj_boundaries(sub)

                for c_obj in (cj.get("CityObjects") or {}).values():
                    for geom in c_obj.get("geometry") or []:
                        _extract_cj_boundaries(geom.get("boundaries"))
                if tris:
                    return verts_f32, np.asarray(tris, dtype=np.uint32), info
        except Exception:
            pass

    # 4. LandXML (`.xml` / `.landxml` TIN `<Surface><Definition><Pnts>...<Faces>...`)
    if eff_ext == "landxml":
        try:
            import xml.etree.ElementTree as ET
            root_lx = ET.fromstring(raw_bytes.decode("utf-8", errors="ignore"))
            for cs_el in root_lx.iter():
                if cs_el.tag.endswith("CoordinateSystem"):
                    epsg_code = cs_el.attrib.get("epsgCode") or cs_el.attrib.get("ogcWktCode") or cs_el.attrib.get("name")
                    if epsg_code:
                        info["crs"] = f"EPSG:{epsg_code}" if str(epsg_code).isdigit() else str(epsg_code)
            p_map: Dict[str, int] = {}
            pts_list: List[List[float]] = []
            faces_lx: List[List[int]] = []
            for el in root_lx.iter():
                if el.tag.endswith("P") and el.attrib.get("id") and el.text:
                    coords = [float(x) for x in el.text.strip().split()]
                    if len(coords) >= 3:
                        p_map[str(el.attrib["id"]).strip()] = len(pts_list)
                        # LandXML Pnt order is Northing (Y), Easting (X), Elevation (Z)
                        pts_list.append([coords[1], coords[0], coords[2]])
                elif el.tag.endswith("F") and el.text:
                    refs = el.text.strip().split()
                    if len(refs) >= 3 and all(r in p_map for r in refs[:3]):
                        faces_lx.append([p_map[refs[0]], p_map[refs[1]], p_map[refs[2]]])
            if pts_list and faces_lx:
                raw_pts = np.asarray(pts_list, dtype=np.float64)
                if info.get("crs") and str(info["crs"]).startswith("EPSG:"):
                    try:
                        from pyproj import Transformer
                        tr_wgs = Transformer.from_crs(info["crs"], "EPSG:4326", always_xy=True)
                        lon_c, lat_c = tr_wgs.transform(float(raw_pts[:, 0].mean()), float(raw_pts[:, 1].mean()))
                        if -90.0 <= lat_c <= 90.0 and -180.0 <= lon_c <= 180.0:
                            info["lat"], info["lon"] = float(lat_c), float(lon_c)
                    except Exception:
                        pass
                verts_f32 = (raw_pts - raw_pts.mean(axis=0)).astype(np.float32)
                return verts_f32, np.asarray(faces_lx, dtype=np.uint32), info
        except Exception:
            pass

    # 5. IFC (`ISO-10303-21;` STEP BIM: `IFCCARTESIANPOINTList3D` + `IFCTRIANGULATEDFACESET` or `IFCCARTESIANPOINT` + `IFCPOLYLOOP`)
    if eff_ext == "ifc" or raw_bytes[:13] == b"ISO-10303-21;":
        try:
            txt_ifc = raw_bytes.decode("utf-8", errors="ignore")
            # Check IFC4 IfcTriangulatedFaceSet first
            pt_list_m = re.search(r"IFCCARTESIANPOINTLIST3D\s*\(\s*\((.+?)\)\s*\)\s*;", txt_ifc, re.DOTALL | re.IGNORECASE)
            face_set_m = re.search(r"IFCTRIANGULATEDFACESET\s*\([^,]+,[^,]+,[^,]+,\s*\((.+?)\)\s*[,)]", txt_ifc, re.DOTALL | re.IGNORECASE)
            if pt_list_m and face_set_m:
                pts_ifc = [
                    [float(c) for c in m.group(1).split(",")]
                    for m in re.finditer(r"\(\s*([-\d.eE+]+,\s*[-\d.eE+]+,\s*[-\d.eE+]+)\s*\)", pt_list_m.group(1))
                ]
                tris_ifc = [
                    [int(c.strip()) - 1 for c in m.group(1).split(",")]
                    for m in re.finditer(r"\(\s*(\d+\s*,\s*\d+\s*,\s*\d+)\s*\)", face_set_m.group(1))
                ]
                if pts_ifc and tris_ifc:
                    v_arr = np.asarray(pts_ifc, dtype=np.float32)
                    v_arr -= v_arr.mean(axis=0)
                    return v_arr, np.asarray(tris_ifc, dtype=np.uint32), info
            # Fallback to IFC2x3 IfcCartesianPoint + IfcPolyLoop
            cp_map: Dict[int, int] = {}
            cp_list: List[List[float]] = []
            for m in re.finditer(r"#(\d+)\s*=\s*IFCCARTESIANPOINT\s*\(\s*\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\)\s*\)", txt_ifc, re.IGNORECASE):
                cp_map[int(m.group(1))] = len(cp_list)
                cp_list.append([float(m.group(2)), float(m.group(3)), float(m.group(4))])
            tris_loop: List[List[int]] = []
            for m in re.finditer(r"IFCPOLYLOOP\s*\(\s*\(([^)]+)\)\s*\)", txt_ifc, re.IGNORECASE):
                ids = [int(x) for x in re.findall(r"#(\d+)", m.group(1)) if int(x) in cp_map]
                ring = [cp_map[i] for i in ids]
                for idx_i in range(1, len(ring) - 1):
                    tris_loop.append([ring[0], ring[idx_i], ring[idx_i + 1]])
            if cp_list and tris_loop:
                v_arr = np.asarray(cp_list, dtype=np.float32)
                v_arr -= v_arr.mean(axis=0)
                return v_arr, np.asarray(tris_loop, dtype=np.uint32), info
        except Exception:
            pass

    # 6. FBX (ASCII FBX `Vertices: *N { a: ... }` + `PolygonVertexIndex: *M { a: ... }`)
    if eff_ext == "fbx":
        try:
            txt_fbx = raw_bytes.decode("utf-8", errors="ignore")
            v_m = re.search(r"Vertices:\s*\*\d+\s*\{\s*a:\s*([-\d.,eE+\s]+)\}", txt_fbx)
            p_m = re.search(r"PolygonVertexIndex:\s*\*\d+\s*\{\s*a:\s*([-\d,.\s]+)\}", txt_fbx)
            if v_m and p_m:
                v_vals = np.fromstring(v_m.group(1).replace("\n", " "), sep=",", dtype=np.float32)
                p_vals = [int(x.strip()) for x in p_m.group(1).replace("\n", "").split(",") if x.strip()]
                if len(v_vals) >= 9 and len(p_vals) >= 3:
                    verts_fbx = v_vals[: (len(v_vals) // 3) * 3].reshape(-1, 3)
                    tris_fbx: List[List[int]] = []
                    poly: List[int] = []
                    for idx_v in p_vals:
                        if idx_v < 0:
                            poly.append((~idx_v))
                            for k in range(1, len(poly) - 1):
                                tris_fbx.append([poly[0], poly[k], poly[k + 1]])
                            poly = []
                        else:
                            poly.append(idx_v)
                    if tris_fbx:
                        return verts_fbx, np.asarray(tris_fbx, dtype=np.uint32), info
        except Exception:
            pass

    # 7. Standard glTF JSON (`.gltf` with embedded base64 buffer)
    if eff_ext == "gltf":
        try:
            import trimesh
            m_gltf = trimesh.load(io.BytesIO(raw_bytes), file_type="gltf", force="mesh")
            if hasattr(m_gltf, "vertices") and hasattr(m_gltf, "faces") and len(m_gltf.faces) > 0:
                return np.asarray(m_gltf.vertices, dtype=np.float32), np.asarray(m_gltf.faces, dtype=np.uint32), info
        except Exception:
            pass

    return None


def validate_mesh_glb(data: bytes) -> None:
    """Reject corrupt/empty models before registering browser derivatives."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("The model is not a valid binary glTF file.")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2 or length != len(data):
        raise ValueError("The GLB version or declared file size is invalid.")
    offset = 12
    document = None
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("The GLB chunk header is truncated.")
        size, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if size % 4 or offset + size > len(data):
            raise ValueError("The GLB chunk is truncated or misaligned.")
        if kind == 0x4E4F534A:
            if document is not None or offset != 20:
                raise ValueError("The GLB JSON chunk is invalid.")
            try:
                document = json.loads(data[offset:offset+size])
            except (ValueError, UnicodeError):
                raise ValueError("The GLB scene description is invalid.") from None
        offset += size
    if not isinstance(document, dict):
        raise ValueError("The GLB scene description is missing.")
    accessors = document.get("accessors") or []
    has_geometry = False
    for mesh in document.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            position = (primitive.get("attributes") or {}).get("POSITION")
            if isinstance(position, int) and 0 <= position < len(accessors):
                accessor = accessors[position]
                has_geometry |= accessor.get("type") == "VEC3" and int(accessor.get("count") or 0) > 0
    if not has_geometry:
        raise ValueError("The GLB contains no renderable mesh geometry.")


def build_mesh_derivatives(
    source_path: Union[Path, bytes, None] = None,
    raw_bytes: Optional[bytes] = None,
    ext_low: str = "glb",
    out_dir: Optional[Path] = None,
    lat: float = 41.8205,
    lon: float = 1.8676,
    alt: float = 0.0,
    thresholds: GeospatialThresholds = THRESHOLDS,
) -> Dict[str, Any]:
    """
    Routes 3D Meshes (`OBJ+MTL+textures`, `GLB`, `glTF`, `STL`, `DAE`, `FBX`, `IFC`, `CityJSON`, `LandXML`) into:
      - Route A (Small Mesh): `derived/optimized/model.glb` + root `model.glb` & `tileset.json`
      - Route B (Large Mesh > `MESH_DIRECT_SIZE_LIMIT` or `MESH_TRIANGLE_LIMIT`):
        True Multi-Level Hierarchical 3D Tiles 1.1 LOD with Geometric Simplification:
          * L0 Root (`model.glb` in `derived/3dtiles/`): Decimated coarse overview mesh (`geometricError = 32.0`)
          * L1 Children (`mesh_quad_{0..3}.glb`): 4 medium-resolution simplified spatial quadrants (`geometricError = 8.0`)
          * L2 Grandchildren (`mesh_quad_{0..3}_{0..3}.glb`): Up to 16 full-resolution leaf sub-quadrants (`geometricError = 0.0`)
    """
    if isinstance(source_path, (bytes, bytearray)) and raw_bytes is None:
        raw_bytes = bytes(source_path)
        source_path = None
    elif raw_bytes is None and isinstance(source_path, Path) and source_path.is_file():
        raw_bytes = source_path.read_bytes()
    if raw_bytes is None:
        raw_bytes = b""
    if out_dir is None:
        raise ValueError("out_dir is required")
    from .gis_splat_engine import _build_enu_to_ecef_matrix, _obj_bytes_to_glb, _wrap_glb_in_3dtiles

    opt_dir = out_dir / "derived" / "optimized"
    tiles_dir = out_dir / "derived" / "3dtiles"
    opt_dir.mkdir(parents=True, exist_ok=True)
    tiles_dir.mkdir(parents=True, exist_ok=True)

    verts_arr: Optional[np.ndarray] = None
    faces_arr: Optional[np.ndarray] = None
    uvs_arr: Optional[np.ndarray] = None
    orig_material: Any = None
    glb_bytes: bytes = b""

    # Parse CAD/BIM/Engineering formats (`stl`, `dae`, `fbx`, `ifc`, `cityjson`, `landxml`, `json`, `xml`) first if applicable
    orig_fname = source_path.name if isinstance(source_path, Path) else f"asset.{ext_low}"
    if ext_low in ("stl", "dae", "fbx", "ifc", "cityjson", "landxml", "json", "xml") or raw_bytes[:13] == b"ISO-10303-21;" or b"<LandXML" in raw_bytes[:4096]:
        cad_res = _parse_engineering_cad_bim_mesh_bytes(raw_bytes, ext_low, original_filename=orig_fname)
        if cad_res is not None:
            verts_arr, faces_arr, cad_info = cad_res
            if cad_info.get("lat") is not None and cad_info.get("lon") is not None:
                lat, lon = float(cad_info["lat"]), float(cad_info["lon"])
            if len(verts_arr) > 0 and len(faces_arr) > 0:
                glb_bytes, _, _ = _compact_submesh_to_glb(verts_arr, faces_arr)

    # Parse via trimesh if available for rich material/normal/UV preservation
    try:
        import trimesh
        loaded_m = None
        if verts_arr is not None:
            pass
        elif ext_low == "glb" or raw_bytes[:4] == b"glTF":
            glb_bytes = raw_bytes
            loaded_m = trimesh.load(io.BytesIO(raw_bytes), file_type="glb", force="mesh")
        else:
            obj_bytes = raw_bytes
            if raw_bytes[:2] == b"PK":
                with tempfile.TemporaryDirectory(prefix="me_obj_zip_") as tmpd:
                    extracted = safe_extract_zip(raw_bytes, Path(tmpd), thresholds=thresholds)
                    obj_p = next((p for p in extracted if p.suffix.lower() in (".obj", ".gltf", ".glb")), None)
                    if obj_p:
                        loaded_m = trimesh.load(str(obj_p), force="mesh")
                        vis_tmp = getattr(loaded_m, "visual", None)
                        if vis_tmp is not None and hasattr(vis_tmp, "material") and vis_tmp.material is not None:
                            mat_tmp = vis_tmp.material
                            if hasattr(mat_tmp, "to_pbr"):
                                try:
                                    mat_tmp = mat_tmp.to_pbr()
                                except Exception:
                                    pass
                            if hasattr(mat_tmp, "baseColorTexture") and mat_tmp.baseColorTexture is not None:
                                try:
                                    mat_tmp.baseColorTexture.load()
                                    mat_tmp.baseColorTexture = mat_tmp.baseColorTexture.copy()
                                except Exception:
                                    pass
                            if hasattr(mat_tmp, "image") and mat_tmp.image is not None:
                                try:
                                    mat_tmp.image.load()
                                    mat_tmp.image = mat_tmp.image.copy()
                                except Exception:
                                    pass
                            vis_tmp.material = mat_tmp
                            orig_material = mat_tmp
            else:
                loaded_m = trimesh.load(io.BytesIO(obj_bytes), file_type=ext_low if ext_low in ("obj", "gltf") else "obj", force="mesh")
        if loaded_m is not None and hasattr(loaded_m, "vertices") and hasattr(loaded_m, "faces"):
            verts_arr = np.asarray(loaded_m.vertices, dtype=np.float32)
            faces_arr = np.asarray(loaded_m.faces, dtype=np.uint32)
            vis = getattr(loaded_m, "visual", None)
            if vis is not None:
                if hasattr(vis, "uv") and vis.uv is not None and len(vis.uv) == len(verts_arr):
                    uvs_arr = np.asarray(vis.uv, dtype=np.float32)
                if orig_material is None and hasattr(vis, "material") and vis.material is not None:
                    mat_cand = vis.material
                    if hasattr(mat_cand, "to_pbr"):
                        try:
                            mat_cand = mat_cand.to_pbr()
                        except Exception:
                            pass
                    orig_material = mat_cand
            if not glb_bytes and len(verts_arr) > 0 and len(faces_arr) > 0:
                glb_bytes, _, _ = _compact_submesh_to_glb(verts_arr, faces_arr, uvs=uvs_arr, material=orig_material)
    except Exception:
        pass

    if not glb_bytes:
        if ext_low == "glb" or raw_bytes[:4] == b"glTF":
            glb_bytes = raw_bytes
        else:
            glb_bytes = _obj_bytes_to_glb(raw_bytes)

    validate_mesh_glb(glb_bytes)
    tri_count = int(len(faces_arr)) if faces_arr is not None else 0
    is_large_mesh = (len(raw_bytes) > thresholds.MESH_DIRECT_SIZE_LIMIT) or (tri_count > thresholds.MESH_TRIANGLE_LIMIT)

    # Full-detail converted GLB always stored in `derived/optimized/model.glb`
    (opt_dir / "model.glb").write_bytes(glb_bytes)
    (tiles_dir / "model.glb").write_bytes(glb_bytes)
    (out_dir / "model.glb").write_bytes(glb_bytes)
    _wrap_glb_in_3dtiles(out_dir, glb_bytes, lat, lon, alt)
    if verts_arr is not None and len(verts_arr) > 0 and (out_dir / "tileset.json").is_file():
        try:
            v_min_sm = verts_arr.min(axis=0)
            v_max_sm = verts_arr.max(axis=0)
            c_sm = (v_min_sm + v_max_sm) * 0.5
            h_sm = np.maximum((v_max_sm - v_min_sm) * 0.5, np.array([0.1, 0.1, 0.1], dtype=np.float32))
            ts_sm = json.loads((out_dir / "tileset.json").read_text(encoding="utf-8"))
            ts_sm["asset"] = {"version": "1.1", "generator": "MosaicEarth Mesh 3D Tiles 1.1 Tiler"}
            ts_sm["root"]["content"] = {"uri": "model.glb"}
            ts_sm["root"]["boundingVolume"] = {
                "box": [
                    float(c_sm[0]), float(c_sm[1]), float(c_sm[2]),
                    float(h_sm[0]), 0.0, 0.0,
                    0.0, float(h_sm[1]), 0.0,
                    0.0, 0.0, float(h_sm[2]),
                ]
            }
            (out_dir / "tileset.json").write_text(json.dumps(ts_sm, indent=2), encoding="utf-8")
        except Exception:
            pass
    if (out_dir / "tileset.json").is_file():
        (tiles_dir / "tileset.json").write_bytes((out_dir / "tileset.json").read_bytes())

    tile_count = 1
    lod_levels = 1
    if is_large_mesh and verts_arr is not None and faces_arr is not None and len(faces_arr) >= 4:
        try:
            # 1. Build L0 Root Decimated Overview Mesh (`model.glb` in 3dtiles) preserving UVs & material
            l0_verts, l0_faces, l0_uvs = _simplify_mesh_vertex_clustering(verts_arr, faces_arr, grid_divs=14, uvs=uvs_arr)
            l0_glb, l0_c, l0_h = _compact_submesh_to_glb(l0_verts, l0_faces, uvs=l0_uvs, material=orig_material)
            (tiles_dir / "model.glb").write_bytes(l0_glb)
            (out_dir / "model.glb").write_bytes(l0_glb)

            tri_centers = verts_arr[faces_arr].mean(axis=1)
            mid_xy = tri_centers[:, :2].mean(axis=0)
            children_l1: List[Dict[str, Any]] = []

            for q_idx in range(4):
                qx = 1 if (q_idx & 1) else -1
                qy = 1 if (q_idx & 2) else -1
                q_mask = (
                    ((tri_centers[:, 0] >= mid_xy[0]) if qx > 0 else (tri_centers[:, 0] < mid_xy[0]))
                    & ((tri_centers[:, 1] >= mid_xy[1]) if qy > 0 else (tri_centers[:, 1] < mid_xy[1]))
                )
                q_faces = faces_arr[q_mask]
                if len(q_faces) == 0:
                    continue

                # 2. L1 Medium-Resolution Quadrant (simplified with finer grid_divs=26, preserving UVs & material)
                l1_verts, l1_faces, l1_uvs = _simplify_mesh_vertex_clustering(verts_arr, q_faces, grid_divs=26, uvs=uvs_arr)
                q_glb, q_c, q_h = _compact_submesh_to_glb(l1_verts, l1_faces, uvs=l1_uvs, material=orig_material)
                q_fname = f"mesh_quad_{q_idx}.glb"
                (tiles_dir / q_fname).write_bytes(q_glb)
                (out_dir / q_fname).write_bytes(q_glb)
                tile_count += 1

                # 3. L2 Full-Detail Leaf Sub-Quadrants (4 sub-quadrants per L1 quadrant, preserving UVs & material)
                q_tri_centers = verts_arr[q_faces].mean(axis=1)
                q_mid_xy = q_tri_centers[:, :2].mean(axis=0)
                grandchildren_l2: List[Dict[str, Any]] = []
                if len(q_faces) >= 8:
                    for sq_idx in range(4):
                        sqx = 1 if (sq_idx & 1) else -1
                        sqy = 1 if (sq_idx & 2) else -1
                        sq_mask = (
                            ((q_tri_centers[:, 0] >= q_mid_xy[0]) if sqx > 0 else (q_tri_centers[:, 0] < q_mid_xy[0]))
                            & ((q_tri_centers[:, 1] >= q_mid_xy[1]) if sqy > 0 else (q_tri_centers[:, 1] < q_mid_xy[1]))
                        )
                        sq_faces = q_faces[sq_mask]
                        if len(sq_faces) == 0:
                            continue
                        sq_glb, sq_c, sq_h = _compact_submesh_to_glb(verts_arr, sq_faces, uvs=uvs_arr, material=orig_material)
                        sq_fname = f"mesh_quad_{q_idx}_{sq_idx}.glb"
                        (tiles_dir / sq_fname).write_bytes(sq_glb)
                        (out_dir / sq_fname).write_bytes(sq_glb)
                        tile_count += 1
                        grandchildren_l2.append({
                            "boundingVolume": {
                                "box": [
                                    float(sq_c[0]), float(sq_c[1]), float(sq_c[2]),
                                    float(sq_h[0]), 0.0, 0.0,
                                    0.0, float(sq_h[1]), 0.0,
                                    0.0, 0.0, float(sq_h[2]),
                                ]
                            },
                            "geometricError": 0.0,
                            "refine": "REPLACE",
                            "content": {"uri": sq_fname},
                        })

                l1_node: Dict[str, Any] = {
                    "boundingVolume": {
                        "box": [
                            float(q_c[0]), float(q_c[1]), float(q_c[2]),
                            float(q_h[0]), 0.0, 0.0,
                            0.0, float(q_h[1]), 0.0,
                            0.0, 0.0, float(q_h[2]),
                        ]
                    },
                    "geometricError": 8.0 if grandchildren_l2 else 0.0,
                    "refine": "REPLACE",
                    "content": {"uri": q_fname},
                }
                if grandchildren_l2:
                    l1_node["children"] = grandchildren_l2
                    lod_levels = max(lod_levels, 3)
                else:
                    lod_levels = max(lod_levels, 2)
                children_l1.append(l1_node)

            if children_l1:
                enu_mat = _build_enu_to_ecef_matrix(lat, lon, alt)
                ts_large = {
                    "asset": {
                        "version": "1.1",
                        "generator": "MosaicEarth Hierarchical Multi-LOD Mesh 3D Tiles 1.1 Tiler",
                        "lodLevels": lod_levels,
                        "simplification": "vertex_clustering_3level",
                    },
                    "geometricError": 128.0,
                    "root": {
                        "transform": enu_mat,
                        "boundingVolume": {
                            "box": [
                                float(l0_c[0]), float(l0_c[1]), float(l0_c[2]),
                                float(l0_h[0]), 0.0, 0.0,
                                0.0, float(l0_h[1]), 0.0,
                                0.0, 0.0, float(l0_h[2]),
                            ]
                        },
                        "geometricError": 32.0,
                        "refine": "REPLACE",
                        "content": {"uri": "model.glb"},
                        "children": children_l1,
                    },
                }
                ts_txt = json.dumps(ts_large, indent=2)
                (tiles_dir / "tileset.json").write_text(ts_txt, encoding="utf-8")
                (out_dir / "tileset.json").write_text(ts_txt, encoding="utf-8")
        except Exception:
            pass

    return {
        "is_large_mesh": is_large_mesh,
        "triangle_count": tri_count,
        "lod_levels": lod_levels,
        "tile_count": tile_count,
        "viewer_kind": "3dtiles" if is_large_mesh else "glb",
        "optimized_glb_rel": "derived/optimized/model.glb",
    }


# ============================================================================
# SECTION 6, 12, 20, 21 — VERTICAL GEODESY (PROJ GRIDS) & CANONICAL MANIFEST
# ============================================================================

def evaluate_vertical_datum_and_geoid_transform(
    crs_str: Optional[str],
    lat: float,
    lon: float,
    alt: float = 0.0,
    vertical_datum_hint: Optional[str] = None,
    already_transformed: bool = False,
    proj_data_dirs: Optional[List[str]] = None,
    manual_offset_m: float = 0.0,
    vertical_accuracy_m: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evaluates the vertical datum of an asset and computes the orthometric-to-ellipsoidal
    height transformation (`H -> h = H + N`) using real PROJ vertical grids
    (`es_ign_egm08-rednap.tif` for `EPSG:5782` Alicante height and `egm96_15.gtx` for `EPSG:5773` EGM96)
    when available locally, with strict metadata disambiguation (Section 5.3):
      - `source_height_m`: Raw input elevation before vertical datum transformation.
      - `source_vertical_crs`: Explicit vertical CRS (`EPSG:5782`, `EPSG:5773`, `EPSG:3855`, `EPSG:4979`) or `None` when unknown.
      - `target_vertical_crs`: `"EPSG:4979"` (WGS84 3D Ellipsoidal height).
      - `ellipsoidal_height_m`: Transformed ellipsoidal height ONLY when `APPLIED_PROJ_GRID`, `ALREADY_ELLIPSOIDAL`,
        or `ALREADY_APPLIED_GUARD`. Strictly `None` (`null`) when `UNKNOWN_DATUM_NO_OP` or `GRID_UNAVAILABLE`.
      - `geoid_undulation_m`: Applied geoid undulation `N` (`0.0` for already-ellipsoidal, `round(undul, 4)` for `APPLIED_PROJ_GRID`,
        and strictly `None` (`null`) when `UNKNOWN_DATUM_NO_OP` or `GRID_UNAVAILABLE`).
      - `manual_offset_m`: User manual height offset (`Height`), isolated from automatic geodetic transformation.
      - `vertical_accuracy_m`: Measured vertical RMSE / accuracy against independent check points when available, else `None`.
    """
    raw_up = f"{crs_str or ''} {vertical_datum_hint or ''}".upper()
    search_dirs = list(proj_data_dirs) if proj_data_dirs is not None else list(DEFAULT_PROJ_DATA_DIRS)
    src_h = round(float(alt), 4)
    man_off = round(float(manual_offset_m or 0.0), 4)
    v_acc = round(float(vertical_accuracy_m), 4) if vertical_accuracy_m is not None else None

    if already_transformed:
        return {
            "source_height_m": src_h,
            "source_vertical_crs": "EPSG:4979",
            "target_vertical_crs": "EPSG:4979",
            "vertical_datum_type": "ellipsoidal",
            "vertical_datum": "WGS84 Ellipsoid (already transformed)",
            "vertical_transform_status": "ALREADY_APPLIED_GUARD",
            "vertical_transform_applied": True,
            "vertical_grid_used": None,
            "geoid_undulation_m": 0.0,
            "ellipsoidal_height_m": src_h,
            "manual_offset_m": man_off,
            "vertical_accuracy_m": v_acc,
            "vertical_transform_warning": None,
        }

    explicit_ellipsoid = "ELLIPSOID" in str(vertical_datum_hint or "").upper()
    if explicit_ellipsoid or re.search(r"(?:EPSG[:\s]*|ID\[\"EPSG\",)(4979|4978|4937)\b", raw_up):
        return {
            "source_height_m": src_h,
            "source_vertical_crs": "EPSG:4979",
            "target_vertical_crs": "EPSG:4979",
            "vertical_datum_type": "ellipsoidal",
            "vertical_datum": "WGS84 Ellipsoid",
            "vertical_transform_status": "ALREADY_ELLIPSOIDAL",
            "vertical_transform_applied": False,
            "vertical_grid_used": None,
            "geoid_undulation_m": 0.0,
            "ellipsoidal_height_m": src_h,
            "manual_offset_m": man_off,
            "vertical_accuracy_m": v_acc,
            "vertical_transform_warning": None,
        }

    is_alicante = any(k in raw_up for k in ("ALICANTE", "5782", "REDNAP"))
    is_egm96 = any(k in raw_up for k in ("EGM96", "5773"))
    is_egm2008 = (not is_alicante) and any(k in raw_up for k in ("EGM2008", "EGM08", "3855"))
    is_other_orthometric = any(k in raw_up for k in ("EVRF", "NAVD88", "5703", "VERT_CS", "VERTCRS", "ORTHOMETRIC"))

    if is_alicante:
        grid_file: Optional[str] = None
        for d in search_dirs:
            cand = os.path.join(d, "es_ign_egm08-rednap.tif")
            if os.path.isfile(cand):
                grid_file = cand
                break
        if not grid_file:
            return {
                "source_height_m": src_h,
                "source_vertical_crs": "EPSG:5782",
                "target_vertical_crs": "EPSG:4979",
                "vertical_datum_type": "orthometric",
                "vertical_datum": "Alicante (EPSG:5782)",
                "vertical_transform_status": "GRID_UNAVAILABLE",
                "vertical_transform_applied": False,
                "vertical_grid_used": None,
                "geoid_undulation_m": None,
                "ellipsoidal_height_m": None,
                "manual_offset_m": man_off,
                "vertical_accuracy_m": v_acc,
                "vertical_transform_warning": (
                    "Vertical datum Alicante (EPSG:5782) detected, but PROJ grid es_ign_egm08-rednap.tif "
                    "is not installed in the active PROJ data directories; no synthetic offset was applied."
                ),
            }
        # Verify coverage bbox of es_ign_egm08-rednap.tif (Mainland Spain and Balearic Islands: [-9.5083333, 34.9916667, 4.5083277, 44.0083297])
        f_lon, f_lat, f_alt = float(lon), float(lat), float(alt)
        if not (-9.5083333 <= f_lon <= 4.5083277 and 34.9916667 <= f_lat <= 44.0083297):
            return {
                "source_height_m": src_h,
                "source_vertical_crs": "EPSG:5782",
                "target_vertical_crs": "EPSG:4979",
                "vertical_datum_type": "orthometric",
                "vertical_datum": "Alicante (EPSG:5782)",
                "vertical_transform_status": "GRID_UNAVAILABLE",
                "vertical_transform_applied": False,
                "vertical_grid_used": None,
                "geoid_undulation_m": None,
                "ellipsoidal_height_m": None,
                "manual_offset_m": man_off,
                "vertical_accuracy_m": v_acc,
                "vertical_transform_warning": (
                    f"Coordinates ({f_lat:.5f}, {f_lon:.5f}) are outside the geographic coverage of "
                    "es_ign_egm08-rednap.tif (Spain Mainland & Balearic Islands); no vertical offset was applied."
                ),
            }
        try:
            import pyproj
            import pyproj.datadir
            import pyproj.network
            pyproj.network.set_network_enabled(False)
            grid_dir = os.path.dirname(grid_file)
            if grid_dir and grid_dir not in pyproj.datadir.get_data_dir():
                pyproj.datadir.append_data_dir(grid_dir)
            # EPSG:4258+5782 (ETRS89 + Alicante height) -> EPSG:4937 (ETRS89 3D ellipsoidal), harmonized with EPSG:4979
            tr = pyproj.Transformer.from_crs("EPSG:4258+5782", "EPSG:4937", always_xy=True)
            _, _, z_out = tr.transform(f_lon, f_lat, f_alt)
            defn = str(getattr(tr, "definition", "") or "")
            if not math.isfinite(float(z_out)):
                raise ValueError("PROJ vertical transformation returned non-finite height")
            undul = float(z_out) - f_alt
            if ("vgridshift" in defn or "es_ign_egm08-rednap.tif" in defn or abs(undul) > 1e-4) and abs(undul) > 1e-4:
                return {
                    "source_height_m": src_h,
                    "source_vertical_crs": "EPSG:5782",
                    "target_vertical_crs": "EPSG:4979",
                    "vertical_datum_type": "orthometric",
                    "vertical_datum": "Alicante (EPSG:5782 / EGM08-REDNAP)",
                    "vertical_transform_status": "APPLIED_PROJ_GRID",
                    "vertical_transform_applied": True,
                    "vertical_grid_used": "es_ign_egm08-rednap.tif",
                    "geoid_undulation_m": round(undul, 4),
                    "ellipsoidal_height_m": round(float(z_out), 4),
                    "manual_offset_m": man_off,
                    "vertical_accuracy_m": v_acc,
                    "datum_ensemble_note": "ETRS89 (EPSG:4937) harmonized with WGS84 (EPSG:4979) for vertical geoid shift es_ign_egm08-rednap.tif",
                    "grid_attribution": "Instituto Geográfico Nacional (IGN Spain), CC-BY 4.0",
                    "vertical_transform_warning": None,
                }
            return {
                "source_height_m": src_h,
                "source_vertical_crs": "EPSG:5782",
                "target_vertical_crs": "EPSG:4979",
                "vertical_datum_type": "orthometric",
                "vertical_datum": "Alicante (EPSG:5782)",
                "vertical_transform_status": "GRID_UNAVAILABLE",
                "vertical_transform_applied": False,
                "vertical_grid_used": None,
                "geoid_undulation_m": None,
                "ellipsoidal_height_m": None,
                "manual_offset_m": man_off,
                "vertical_accuracy_m": v_acc,
                "vertical_transform_warning": f"Alicante EGM08-REDNAP PROJ grid shift produced 0m offset (definition: {defn}).",
            }
        except Exception as exc:
            return {
                "source_height_m": src_h,
                "source_vertical_crs": "EPSG:5782",
                "target_vertical_crs": "EPSG:4979",
                "vertical_datum_type": "orthometric",
                "vertical_datum": "Alicante (EPSG:5782)",
                "vertical_transform_status": "GRID_UNAVAILABLE",
                "vertical_transform_applied": False,
                "vertical_grid_used": None,
                "geoid_undulation_m": None,
                "ellipsoidal_height_m": None,
                "manual_offset_m": man_off,
                "vertical_accuracy_m": v_acc,
                "vertical_transform_warning": f"Alicante EGM08-REDNAP PROJ grid evaluation failed: {exc}",
            }

    if is_egm96:
        egm96_file: Optional[str] = None
        for d in search_dirs:
            cand = os.path.join(d, "egm96_15.gtx")
            if os.path.isfile(cand):
                egm96_file = cand
                break
        if not egm96_file:
            return {
                "source_height_m": src_h,
                "source_vertical_crs": "EPSG:5773",
                "target_vertical_crs": "EPSG:4979",
                "vertical_datum_type": "orthometric",
                "vertical_datum": "EGM96 (EPSG:5773)",
                "vertical_transform_status": "GRID_UNAVAILABLE",
                "vertical_transform_applied": False,
                "vertical_grid_used": None,
                "geoid_undulation_m": None,
                "ellipsoidal_height_m": None,
                "manual_offset_m": man_off,
                "vertical_accuracy_m": v_acc,
                "vertical_transform_warning": "EGM96 grid egm96_15.gtx is not installed in the active PROJ data directories.",
            }
        try:
            import pyproj
            import pyproj.datadir
            import pyproj.network
            pyproj.network.set_network_enabled(False)
            egm96_dir = os.path.dirname(egm96_file)
            if egm96_dir and egm96_dir not in pyproj.datadir.get_data_dir():
                pyproj.datadir.append_data_dir(egm96_dir)
            tr = pyproj.Transformer.from_crs("EPSG:4326+5773", "EPSG:4979", always_xy=True)
            defn = str(getattr(tr, "definition", "") or "")
            _, _, z_out = tr.transform(float(lon), float(lat), float(alt))
            undul = float(z_out) - float(alt)
            if "vgridshift" in defn or abs(undul) > 1e-4:
                return {
                    "source_height_m": src_h,
                    "source_vertical_crs": "EPSG:5773",
                    "target_vertical_crs": "EPSG:4979",
                    "vertical_datum_type": "orthometric",
                    "vertical_datum": "EGM96 (EPSG:5773)",
                    "vertical_transform_status": "APPLIED_PROJ_GRID",
                    "vertical_transform_applied": True,
                    "vertical_grid_used": "egm96_15.gtx",
                    "geoid_undulation_m": round(undul, 4),
                    "ellipsoidal_height_m": round(float(z_out), 4),
                    "manual_offset_m": man_off,
                    "vertical_accuracy_m": v_acc,
                    "vertical_transform_warning": None,
                }
        except Exception as exc:
            return {
                "source_height_m": src_h,
                "source_vertical_crs": "EPSG:5773",
                "target_vertical_crs": "EPSG:4979",
                "vertical_datum_type": "orthometric",
                "vertical_datum": "EGM96 (EPSG:5773)",
                "vertical_transform_status": "GRID_UNAVAILABLE",
                "vertical_transform_applied": False,
                "vertical_grid_used": None,
                "geoid_undulation_m": None,
                "ellipsoidal_height_m": None,
                "manual_offset_m": man_off,
                "vertical_accuracy_m": v_acc,
                "vertical_transform_warning": f"EGM96 PROJ grid evaluation failed: {exc}",
            }

    if is_egm2008 or is_other_orthometric:
        datum_label = "EGM2008 (EPSG:3855)" if is_egm2008 else "Orthometric Vertical CRS"
        src_vcrs = "EPSG:3855" if is_egm2008 else ("EPSG:5703" if "5703" in raw_up or "NAVD88" in raw_up else "ORTHOMETRIC")
        epsg_comp = "EPSG:4326+3855" if is_egm2008 else None
        if epsg_comp:
            try:
                import pyproj
                tg = pyproj.transformer.TransformerGroup(epsg_comp, "EPSG:4979")
                if tg.best_available and tg.transformers:
                    tr0 = tg.transformers[0]
                    defn0 = str(getattr(tr0, "definition", "") or "")
                    if "vgridshift" in defn0:
                        _, _, z_out = tr0.transform(float(lon), float(lat), float(alt))
                        undul = float(z_out) - float(alt)
                        return {
                            "source_height_m": src_h,
                            "source_vertical_crs": src_vcrs,
                            "target_vertical_crs": "EPSG:4979",
                            "vertical_datum_type": "orthometric",
                            "vertical_datum": datum_label,
                            "vertical_transform_status": "APPLIED_PROJ_GRID",
                            "vertical_transform_applied": True,
                            "vertical_grid_used": defn0,
                            "geoid_undulation_m": round(undul, 4),
                            "ellipsoidal_height_m": round(float(z_out), 4),
                            "manual_offset_m": man_off,
                            "vertical_accuracy_m": v_acc,
                            "vertical_transform_warning": None,
                        }
            except Exception:
                pass
        return {
            "source_height_m": src_h,
            "source_vertical_crs": src_vcrs,
            "target_vertical_crs": "EPSG:4979",
            "vertical_datum_type": "orthometric",
            "vertical_datum": datum_label,
            "vertical_transform_status": "GRID_UNAVAILABLE",
            "vertical_transform_applied": False,
            "vertical_grid_used": None,
            "geoid_undulation_m": None,
            "ellipsoidal_height_m": None,
            "manual_offset_m": man_off,
            "vertical_accuracy_m": v_acc,
            "vertical_transform_warning": (
                f"Vertical datum {datum_label} detected, but its PROJ vertical grid is not installed locally; "
                "no synthetic offset was applied."
            ),
        }

    return {
        "source_height_m": src_h,
        "source_vertical_crs": None,
        "target_vertical_crs": "EPSG:4979",
        "vertical_datum_type": "unknown",
        "vertical_datum": "unknown",
        "vertical_transform_status": "UNKNOWN_DATUM_NO_OP",
        "vertical_transform_applied": False,
        "vertical_grid_used": None,
        "geoid_undulation_m": None,
        "ellipsoidal_height_m": None,
        "manual_offset_m": man_off,
        "vertical_accuracy_m": v_acc,
        "vertical_transform_warning": None,
    }


def write_canonical_metadata_and_manifest(
    out_dir: Path,
    project_id: int,
    asset_id: int,
    source_filename: str,
    source_rel_path: str,
    source_sha256: str,
    inspection: Dict[str, Any],
    lat: float,
    lon: float,
    alt: float,
    heading: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    scale: float = 1.0,
    derivatives_list: Optional[List[Dict[str, Any]]] = None,
    status: str = STATE_READY,
) -> Dict[str, Any]:
    """
    Writes the 4 canonical metadata files inside `metadata/`:
      1. `metadata/asset.json`
      2. `metadata/crs.json`
      3. `metadata/transform.json`
      4. `metadata/derivatives.json`
    """
    from .gis_splat_engine import _build_enu_to_ecef_matrix

    meta_dir = out_dir / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    now = _now_iso()

    # 1. asset.json
    asset_doc = {
        "asset_id": int(asset_id),
        "project_id": int(project_id),
        "pipeline_version": PIPELINE_VERSION,
        "generator": "MosaicEarth Geospatial Pipeline",
        "status": status,
        "source": {
            "filename": source_filename,
            "extension": Path(source_filename).suffix.lower(),
            "relative_path": source_rel_path,
            "sha256": source_sha256,
            "size_bytes": int(inspection.get("file_size_bytes") or 0),
            "immutable": True,
        },
        "classification": {
            "asset_class": inspection.get("asset_class", ASSET_CLASS_UNKNOWN),
            "confidence": inspection.get("classification_confidence", "high"),
            "mime_type": inspection.get("mime_type", "application/octet-stream"),
        },
        "inspection": inspection,
        "updated_at": now,
    }
    (meta_dir / "asset.json").write_text(json.dumps(asset_doc, indent=2), encoding="utf-8")

    # 2. crs.json — Explicit horizontal & vertical datum separation with real PROJ vertical grid evaluation
    has_georef = bool(inspection.get("has_embedded_georef"))
    raw_crs_str = str(inspection.get("crs") or "")
    v_hint = str(inspection.get("vertical_datum_name") or inspection.get("vertical_datum_type") or "")
    already_v_transformed = bool(inspection.get("vertical_transform_applied", False))
    if isinstance(inspection.get("vertical_eval_precomputed"), dict):
        v_eval = dict(inspection["vertical_eval_precomputed"])
    else:
        v_eval = evaluate_vertical_datum_and_geoid_transform(
            crs_str=raw_crs_str,
            lat=float(lat),
            lon=float(lon),
            alt=float(alt),
            vertical_datum_hint=v_hint,
            already_transformed=already_v_transformed,
        )
    if inspection.get("geoid_undulation_m") is not None and not v_eval["vertical_transform_applied"]:
        # Respect explicit user/caller override if provided
        custom_undul = float(inspection["geoid_undulation_m"])
        if abs(custom_undul) > 1e-6:
            v_eval["geoid_undulation_m"] = round(custom_undul, 4)
            v_eval["ellipsoidal_height_m"] = round(float(alt) + custom_undul, 4)
            v_eval["target_vertical_crs"] = "EPSG:4979"
            v_eval["vertical_transform_status"] = "EXPLICIT_USER_UNDULATION"
            v_eval["vertical_transform_applied"] = True

    v_datum_type = v_eval["vertical_datum_type"]
    v_datum_name = v_eval["vertical_datum"]
    raw_undul = v_eval.get("geoid_undulation_m")
    raw_ellip = v_eval.get("ellipsoidal_height_m")
    geoid_undulation_m = round(float(raw_undul), 4) if raw_undul is not None else None
    ellipsoidal_height_m = round(float(raw_ellip), 4) if raw_ellip is not None else None
    eff_alt_ellipsoid = float(ellipsoidal_height_m) if ellipsoidal_height_m is not None else float(alt)

    crs_doc = {
        "source_crs": inspection.get("crs") if has_georef else None,
        "epsg": inspection.get("epsg") if has_georef else None,
        "georeferenced": has_georef,
        "crs_confidence": "explicit" if has_georef else "none",
        "has_embedded_georef": has_georef,
        "bbox_wgs84": inspection.get("bbox_wgs84") if has_georef else None,
        "source_height_m": v_eval.get("source_height_m", round(float(alt), 4)),
        "source_vertical_crs": v_eval.get("source_vertical_crs"),
        "target_vertical_crs": v_eval.get("target_vertical_crs"),
        "vertical_datum_type": v_datum_type,
        "vertical_datum": v_datum_name,
        "vertical_transform_status": v_eval["vertical_transform_status"],
        "vertical_transform_applied": v_eval["vertical_transform_applied"],
        "vertical_grid_used": v_eval["vertical_grid_used"],
        "vertical_transform_warning": v_eval["vertical_transform_warning"],
        "geoid_undulation_m": geoid_undulation_m,
        "ellipsoidal_height_m": ellipsoidal_height_m,
        "manual_offset_m": v_eval.get("manual_offset_m", 0.0),
        "vertical_accuracy_m": v_eval.get("vertical_accuracy_m"),
        "updated_at": now,
    }
    (meta_dir / "crs.json").write_text(json.dumps(crs_doc, indent=2), encoding="utf-8")

    # 3. transform.json (Explicit Georeferencing separate from immutable source — Section 12 & Section 5.3)
    ecef_mat = _build_enu_to_ecef_matrix(
        float(lat),
        float(lon),
        eff_alt_ellipsoid,
        heading_deg=float(heading),
        pitch_deg=float(pitch),
        roll_deg=float(roll),
        scale=float(scale),
    )
    transform_doc = {
        "source_crs": crs_doc["source_crs"] or "LOCAL_ENU",
        "target_crs": "EPSG:4978",
        "display_crs": "EPSG:4326",
        "georeferenced": has_georef,
        "crs_confidence": crs_doc["crs_confidence"],
        "longitude": round(float(lon), 7),
        "latitude": round(float(lat), 7),
        "height": round(float(alt), 3),
        "source_height_m": v_eval.get("source_height_m", round(float(alt), 4)),
        "source_vertical_crs": v_eval.get("source_vertical_crs"),
        "target_vertical_crs": v_eval.get("target_vertical_crs"),
        "geoid_undulation_m": geoid_undulation_m,
        "ellipsoidal_height_m": ellipsoidal_height_m,
        "manual_offset_m": v_eval.get("manual_offset_m", 0.0),
        "vertical_accuracy_m": v_eval.get("vertical_accuracy_m"),
        "vertical_datum_type": v_datum_type,
        "vertical_datum": v_datum_name,
        "vertical_transform_status": v_eval["vertical_transform_status"],
        "vertical_transform_applied": v_eval["vertical_transform_applied"],
        "vertical_grid_used": v_eval["vertical_grid_used"],
        "vertical_transform_warning": v_eval["vertical_transform_warning"],
        "vertical_reference": "WGS84 Ellipsoid" if ellipsoidal_height_m is not None else "Unverified Source Height (No-Op)",
        "orientation": {
            "heading": round(float(heading), 3),
            "pitch": round(float(pitch), 3),
            "roll": round(float(roll), 3),
        },
        "scale": round(float(scale), 5),
        "transform_matrix_ecef_column_major": ecef_mat,
        "updated_at": now,
    }
    (meta_dir / "transform.json").write_text(json.dumps(transform_doc, indent=2), encoding="utf-8")
    # Also write root transform.json if not already a Gaussian Splat transform
    root_tf = out_dir / "transform.json"
    if not root_tf.is_file():
        root_tf.write_text(json.dumps(transform_doc, indent=2), encoding="utf-8")

    # 4. derivatives.json (Canonical Manifest — Section 1 & 21)
    derivs = derivatives_list or []
    for d in derivs:
        d.setdefault("pipeline_version", PIPELINE_VERSION)
        d.setdefault("generator", "MosaicEarth")
        d.setdefault("generated_at", now)
        d.setdefault("source_sha256", source_sha256)
        d.setdefault("status", "READY")

    derivatives_doc = {
        "asset_id": int(asset_id),
        "project_id": int(project_id),
        "pipeline_version": PIPELINE_VERSION,
        "source_sha256": source_sha256,
        "generated_at": now,
        "derivatives": derivs,
    }
    (meta_dir / "derivatives.json").write_text(json.dumps(derivatives_doc, indent=2), encoding="utf-8")

    return {
        "asset": asset_doc,
        "crs": crs_doc,
        "transform": transform_doc,
        "derivatives": derivatives_doc,
    }
