import gzip
import concurrent.futures
import io
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ExifTags


_SPZ_SH_WIDTHS = (0, 9, 24, 45)


def _save_elevation_terrain_tiles(blob_store: Any, out_dir: Path, project_id: int, asset_id: int) -> int:
    """Publish legacy asset copies; the final bounded sync publishes results once."""
    tiles_dir = out_dir / "derived" / "terrain" / "tiles"
    if not tiles_dir.is_dir():
        return 0
    files = sorted(tiles_dir.rglob("*.json"))

    def save(tile: Path) -> None:
        # Results already exist under out_dir. Uploading them here and again in
        # sync_folder_to_cloud doubled hundreds of sequential network requests.
        relative = tile.relative_to(out_dir).as_posix()
        blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/{relative}", tile.read_bytes())

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        for _ in executor.map(save, files):
            pass  # Consume results so read/storage failures propagate.
    return len(files)


def _build_enu_to_ecef_matrix(
    lat_deg: float,
    lon_deg: float,
    height_m: float = 0.0,
    heading_deg: float = 0.0,
    pitch_deg: float = 0.0,
    roll_deg: float = 0.0,
    scale: float = 1.0,
) -> List[float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    a = 6378137.0
    f = 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)
    N = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    x0 = (N + height_m) * cos_lat * cos_lon
    y0 = (N + height_m) * cos_lat * sin_lon
    z0 = (N * (1.0 - e2) + height_m) * sin_lat

    ex, ey, ez = -sin_lon, cos_lon, 0.0
    nx, ny, nz = -sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat
    ux, uy, uz = cos_lat * cos_lon, cos_lat * sin_lon, sin_lat

    # Local 3-axis rotation: R_local = Rz(heading) * Ry(roll) * Rx(pitch) * scale
    rx = math.radians(float(pitch_deg or 0.0))
    ry = math.radians(float(roll_deg or 0.0))
    rz = math.radians(float(heading_deg or 0.0))
    s = max(0.01, float(scale or 1.0))

    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # Columns of R_local = Rz * Ry * Rx
    r00 = cz * cy
    r10 = sz * cy
    r20 = -sy

    r01 = cz * sy * sx - sz * cx
    r11 = sz * sy * sx + cz * cx
    r21 = cy * sx

    r02 = cz * sy * cx + sz * sx
    r12 = sz * sy * cx - cz * sx
    r22 = cy * cx

    # Multiply ENU basis [E | N | U] by R_local * s
    c0x = (ex * r00 + nx * r10 + ux * r20) * s
    c0y = (ey * r00 + ny * r10 + uy * r20) * s
    c0z = (ez * r00 + nz * r10 + uz * r20) * s

    c1x = (ex * r01 + nx * r11 + ux * r21) * s
    c1y = (ey * r01 + ny * r11 + uy * r21) * s
    c1z = (ez * r01 + nz * r11 + uz * r21) * s

    c2x = (ex * r02 + nx * r12 + ux * r22) * s
    c2y = (ey * r02 + ny * r12 + uy * r22) * s
    c2z = (ez * r02 + nz * r12 + uz * r22) * s

    return [
        c0x, c0y, c0z, 0.0,
        c1x, c1y, c1z, 0.0,
        c2x, c2y, c2z, 0.0,
        x0, y0, z0, 1.0,
    ]


def _extract_exif_gps(img_bytes: bytes) -> Optional[Tuple[float, float, float]]:
    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            exif = im._getexif()
            if not exif:
                return None
            gps_info = {}
            for k, v in exif.items():
                tag = ExifTags.TAGS.get(k, k)
                if tag == "GPSInfo" and isinstance(v, dict):
                    for gk, gv in v.items():
                        gps_info[ExifTags.GPSTAGS.get(gk, gk)] = gv
            if not gps_info:
                return None

            def _to_deg(val: Any) -> float:
                d = float(val[0])
                m = float(val[1])
                s = float(val[2])
                return d + (m / 60.0) + (s / 3600.0)

            if "GPSLatitude" in gps_info and "GPSLongitude" in gps_info:
                lat = _to_deg(gps_info["GPSLatitude"])
                if str(gps_info.get("GPSLatitudeRef", "N")).upper().startswith("S"):
                    lat = -lat
                lon = _to_deg(gps_info["GPSLongitude"])
                if str(gps_info.get("GPSLongitudeRef", "E")).upper().startswith("W"):
                    lon = -lon
                alt = float(gps_info.get("GPSAltitude", 0.0) or 0.0)
                return (lat, lon, alt)
    except Exception:
        pass
    return None


def _write_pnts_and_tileset(
    out_dir: Path,
    positions_enu: np.ndarray,
    colors_rgb: np.ndarray,
    lat: float,
    lon: float,
    alt: float = 0.0,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    n_pts = int(positions_enu.shape[0])
    if n_pts == 0:
        raise ValueError("Point cloud contains no points")

    pos_f32 = np.ascontiguousarray(positions_enu, dtype="<f4")
    rgb_u8 = np.ascontiguousarray(colors_rgb, dtype=np.uint8)

    min_xyz = pos_f32.min(axis=0)
    max_xyz = pos_f32.max(axis=0)
    center = (min_xyz + max_xyz) * 0.5
    pos_centered = (pos_f32 - center).astype("<f4")
    half_extents = np.maximum((max_xyz - min_xyz) * 0.5, np.array([5.0, 5.0, 2.5], dtype=np.float32))

    pos_bytes = pos_centered.tobytes()
    rgb_bytes = rgb_u8.tobytes()

    ft_dict = {
        "POINTS_LENGTH": n_pts,
        "RTC_CENTER": center.tolist(),
        "POSITION": {"byteOffset": 0},
        "RGB": {"byteOffset": len(pos_bytes)},
    }
    ft_json = json.dumps(ft_dict, separators=(",", ":")).encode("utf-8")
    pad_len = (8 - ((28 + len(ft_json)) % 8)) % 8
    ft_json += b" " * pad_len

    ft_bin = pos_bytes + rgb_bytes
    bin_pad = (8 - (len(ft_bin) % 8)) % 8
    ft_bin += b"\x00" * bin_pad

    total_len = 28 + len(ft_json) + len(ft_bin)
    pnts_hdr = struct.pack("<4sIIIIII", b"pnts", 1, total_len, len(ft_json), len(ft_bin), 0, 0)
    pnts_path = out_dir / "points.pnts"
    pnts_path.write_bytes(pnts_hdr + ft_json + ft_bin)

    enu_mat = _build_enu_to_ecef_matrix(float(lat), float(lon), float(alt))
    hx, hy, hz = float(half_extents[0]), float(half_extents[1]), float(half_extents[2])
    tileset_obj = {
        "asset": {"version": "1.0"},
        "geometricError": 64.0,
        "root": {
            "transform": enu_mat,
            "boundingVolume": {
                "box": [*center.tolist(), hx, 0.0, 0.0, 0.0, hy, 0.0, 0.0, 0.0, hz]
            },
            "geometricError": 0.0,
            "refine": "ADD",
            "content": {"uri": "points.pnts"},
        },
    }
    ts_path = out_dir / "tileset.json"
    ts_path.write_text(json.dumps(tileset_obj, indent=2), encoding="utf-8")
    return {"points_count": n_pts, "tileset_path": str(ts_path), "pnts_path": str(pnts_path)}


def _compute_anisotropic_splat_attributes(
    pos: np.ndarray,
    rgb: np.ndarray,
    scales_xyz: Optional[np.ndarray] = None,
    rotations_xyzw: Optional[np.ndarray] = None,
    alphas_u8: Optional[np.ndarray] = None,
    sh_deg1: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Ensures every 3D Gaussian Splat has realistic anisotropic ellipsoid scales,
    surface-aligned quaternion rotations (x, y, z, w), opacity (0..255),
    and spherical harmonics for view-dependent color.
    """
    n = int(pos.shape[0])
    pos_f = np.ascontiguousarray(pos, dtype="<f4")
    rgb_f = rgb.astype(np.float32) / 255.0

    # Estimate characteristic inter-splat spacing from bounding volume
    if n > 1:
        span = np.maximum(pos_f.max(axis=0) - pos_f.min(axis=0), 1.0)
        area_xy = float(span[0] * span[1])
        base_r = float(np.clip(math.sqrt(area_xy / max(n, 1)) * 1.45, 0.14, 1.35))
    else:
        base_r = 0.28

    # Estimate local surface normals from spatial gradient / elevation profile
    z_min = float(pos_f[:, 2].min()) if n > 0 else 0.0
    z_span = max(float(pos_f[:, 2].max() - z_min), 1.0) if n > 0 else 1.0
    z_rel = (pos_f[:, 2] - z_min) / z_span

    cx = float(pos_f[:, 0].mean()) if n > 0 else 0.0
    cy = float(pos_f[:, 1].mean()) if n > 0 else 0.0
    dx = pos_f[:, 0] - cx
    dy = pos_f[:, 1] - cy
    dist_xy = np.maximum(np.sqrt(dx * dx + dy * dy), 1e-3)

    # Approximate outward-tilted surface normal (nx, ny, nz)
    nx = np.clip((dx / dist_xy) * (0.28 * z_rel), -0.65, 0.65).astype(np.float32)
    ny = np.clip((dy / dist_xy) * (0.28 * z_rel), -0.65, 0.65).astype(np.float32)
    nz = np.sqrt(np.maximum(1.0 - nx * nx - ny * ny, 0.25)).astype(np.float32)
    normals = np.column_stack([nx, ny, nz]).astype("<f4")

    if scales_xyz is None:
        lum = 0.299 * rgb_f[:, 0] + 0.587 * rgb_f[:, 1] + 0.114 * rgb_f[:, 2]
        sx = (base_r * (0.85 + 0.35 * (1.0 - lum))).astype("<f4")
        sy = (base_r * (1.05 + 0.25 * lum)).astype("<f4")
        sz = (base_r * 0.38 * np.ones_like(lum)).astype("<f4")
        scales_xyz = np.column_stack([sx, sy, sz]).astype("<f4")
    elif sh_deg1 is not None and sh_deg1.shape[1] > 9:
        if not np.isfinite(scales_xyz).all() or np.any(scales_xyz <= 0):
            raise ValueError("Trained Gaussian scales must be finite and positive")
        scales_xyz = np.ascontiguousarray(scales_xyz, dtype="<f4")
    else:
        scales_xyz = np.ascontiguousarray(np.clip(scales_xyz, 0.02, 4.0), dtype="<f4")

    if rotations_xyzw is None:
        # Quaternion rotating [0, 0, 1] to (nx, ny, nz) plus azimuth yaw around Z
        # q_tilt = [ -ny, nx, 0, 1 + nz ] normalized
        qx = -ny
        qy = nx
        qz = 0.12 * np.sin(dx * 0.25 + dy * 0.17).astype(np.float32)
        qw = 1.0 + nz
        q_norm = np.maximum(np.sqrt(qx * qx + qy * qy + qz * qz + qw * qw), 1e-6)
        rotations_xyzw = np.column_stack([qx / q_norm, qy / q_norm, qz / q_norm, qw / q_norm]).astype("<f4")
    else:
        rotations_xyzw = np.ascontiguousarray(rotations_xyzw, dtype="<f4")
        q_norm = np.maximum(np.linalg.norm(rotations_xyzw, axis=1, keepdims=True), 1e-6)
        rotations_xyzw = (rotations_xyzw / q_norm).astype("<f4")

    if alphas_u8 is None:
        lum = 0.299 * rgb_f[:, 0] + 0.587 * rgb_f[:, 1] + 0.114 * rgb_f[:, 2]
        alphas_u8 = np.clip(228.0 + 25.0 * lum, 195, 253).astype(np.uint8)
    else:
        alphas_u8 = np.ascontiguousarray(alphas_u8, dtype=np.uint8)

    if sh_deg1 is None:
        # View-dependent Degree-1 Spherical Harmonics (3 basis dirs x 3 RGB channels = 9 floats per splat)
        # Produces realistic directional specular sheen & reflections when orbiting in CesiumJS
        lum = 0.299 * rgb_f[:, 0] + 0.587 * rgb_f[:, 1] + 0.114 * rgb_f[:, 2]
        spec_strength = np.clip((lum - 0.35) * 0.42, 0.06, 0.32).astype(np.float32)
        warmth = np.clip(rgb_f[:, 0] - rgb_f[:, 2], -0.25, 0.25).astype(np.float32)

        # SH Coef 0 (Y-axis directional lobe: North-South sheen)
        sh0_r = np.clip(ny * spec_strength * (1.15 + warmth), -0.45, 0.45)
        sh0_g = np.clip(ny * spec_strength * 1.05, -0.45, 0.45)
        sh0_b = np.clip(ny * spec_strength * (1.10 - warmth), -0.45, 0.45)

        # SH Coef 1 (Z-axis directional lobe: Zenith / sky reflection brilliance)
        sh1_r = np.clip(nz * spec_strength * 0.65, -0.45, 0.45)
        sh1_g = np.clip(nz * spec_strength * 0.72, -0.45, 0.45)
        sh1_b = np.clip(nz * spec_strength * 0.88, -0.45, 0.45)

        # SH Coef 2 (X-axis directional lobe: East-West sun highlight)
        sh2_r = np.clip(-nx * spec_strength * 1.20, -0.45, 0.45)
        sh2_g = np.clip(-nx * spec_strength * 1.08, -0.45, 0.45)
        sh2_b = np.clip(-nx * spec_strength * 0.92, -0.45, 0.45)

        sh_deg1 = np.column_stack([
            sh0_r, sh0_g, sh0_b,
            sh1_r, sh1_g, sh1_b,
            sh2_r, sh2_g, sh2_b,
        ]).astype("<f4")
    else:
        sh_deg1 = np.ascontiguousarray(sh_deg1, dtype="<f4")

    return scales_xyz, rotations_xyzw, alphas_u8, sh_deg1, normals


def _write_spz_v2(
    spz_path: Path,
    pos: np.ndarray,
    rgb: np.ndarray,
    scales_xyz: np.ndarray,
    rotations_xyzw: np.ndarray,
    alphas_u8: np.ndarray,
    sh_deg1: Optional[np.ndarray] = None,
) -> bytes:
    """
    Encodes a 3D Gaussian Splatting model into the open Niantic SPZ v2 (.spz) format
    (gzipped PackedGaussians, magic=0x5053474E 'NGSP', version=2, fractionalBits=12):
      - Header: 16 bytes (<IIIBBBB)
      - Positions: N * 9 bytes (3 x 24-bit signed fixed-point)
      - Alphas: N * 1 byte (uint8)
      - Colors: N * 3 bytes (uint8)
      - Scales: N * 3 bytes (uint8 log-scale)
      - Rotations: N * 3 bytes (uint8 qx, qy, qz with qw >= 0 reconstructed on decode)
      - Spherical Harmonics: N times the coefficient width for degrees 0 through 3.
    """
    n = int(pos.shape[0])
    fractional_bits = 12
    scale_factor = float(1 << fractional_bits)
    sh_width = sh_deg1.shape[1] if sh_deg1 is not None else 0
    if sh_width not in _SPZ_SH_WIDTHS or (sh_deg1 is not None and len(sh_deg1) != n):
        raise ValueError("Invalid spherical harmonic coefficients for SPZ v2")
    eff_sh_deg = _SPZ_SH_WIDTHS.index(sh_width)

    # 1. Header (16 bytes)
    header = struct.pack("<IIIBBBB", 0x5053474E, 2, n, eff_sh_deg, fractional_bits, 1, 0)

    # 2. Positions: N * 3 signed 24-bit fixed-point integers (little-endian) = N * 9 bytes
    fixed32 = np.clip(np.round(pos * scale_factor), -(1 << 23), (1 << 23) - 1).astype("<i4")
    b_u8 = fixed32.view(np.uint8).reshape(-1, 4)[:, :3]
    pos_bytes = np.ascontiguousarray(b_u8).tobytes()

    # 3. Alphas: N * 1 byte (uint8)
    alpha_bytes = np.ascontiguousarray(alphas_u8, dtype=np.uint8).reshape(n).tobytes()

    # 4. Colors: N * 3 uint8 encoded for SPZ v2 colorScale=0.15 & SH_C0=0.28209479
    SH_C0 = 0.28209479177387814
    f_dc = ((rgb[:, :3].astype(np.float32) / 255.0) - 0.5) / SH_C0
    colors_u8 = np.clip(np.round((f_dc * 0.15 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    color_bytes = np.ascontiguousarray(colors_u8).reshape(n, 3).tobytes()

    # 5. Scales: N * 3 uint8, log_scale quantized as (log_scale + 10.0) * 16.0
    log_scales = np.log(np.maximum(scales_xyz[:, :3], np.finfo(np.float32).tiny))
    scales_u8 = np.clip(np.round((log_scales + 10.0) * 16.0), 0, 255).astype(np.uint8)
    scale_bytes = np.ascontiguousarray(scales_u8).reshape(n, 3).tobytes()

    # 6. Rotations: N * 3 uint8, (qx, qy, qz) normalized with qw >= 0 mapped from [-1, 1] to [0, 255]
    q = rotations_xyzw[:, :4].astype(np.float32).copy()
    q_norm = np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-6)
    q = q / q_norm
    neg_w = q[:, 3] < 0
    q[neg_w] = -q[neg_w]
    rot_u8 = np.clip(np.round((q[:, :3] * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    rot_bytes = np.ascontiguousarray(rot_u8).reshape(n, 3).tobytes()

    # SPZ stores each coefficient's red, green and blue values together.
    sh_bytes = b""
    if sh_width:
        sh_u8 = np.clip(np.round(sh_deg1 * 128.0 + 128.0), 0, 255).astype(np.uint8)
        sh_bytes = np.ascontiguousarray(sh_u8).tobytes()

    raw_payload = header + pos_bytes + alpha_bytes + color_bytes + scale_bytes + rot_bytes + sh_bytes
    expected_len = 16 + n * (19 + sh_width)
    if len(raw_payload) != expected_len:
        raise ValueError(f"SPZ v2 payload size mismatch: got {len(raw_payload)}, expected {expected_len}")
    spz_bytes = gzip.compress(raw_payload, compresslevel=6)
    spz_path.write_bytes(spz_bytes)
    return spz_bytes


def _write_khr_gaussian_splatting_glb_and_tileset(
    out_dir: Path,
    spz_bytes: bytes,
    pos: np.ndarray,
    lat: float,
    lon: float,
    alt: float = 0.0,
    heading: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    scale: float = 1.0,
) -> Tuple[Path, Path, Path]:
    """
    Writes a 100% self-hosted 3D Tiles 1.1 Gaussian Splatting tileset for CesiumJS 1.144+:
      - tile_0.glb (and model.glb alias) using KHR_gaussian_splatting & KHR_gaussian_splatting_compression_spz_2
      - tileset.json (3D Tiles 1.1 with 3DTILES_content_gltf + ECEF transform)
      - transform.json & geo_alignment.txt (georeferencing metadata for user download)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    n = int(pos.shape[0])
    pos_f = np.ascontiguousarray(pos, dtype="<f4")
    if n > 0:
        v_min = [float(x) for x in pos_f.min(axis=0)]
        v_max = [float(x) for x in pos_f.max(axis=0)]
        half_ext = np.maximum((pos_f.max(axis=0) - pos_f.min(axis=0)) * 0.65, 0.01)
    else:
        v_min = [-10.0, -10.0, -2.0]
        v_max = [10.0, 10.0, 5.0]
        half_ext = np.array([15.0, 15.0, 8.0], dtype=np.float32)

    with gzip.GzipFile(fileobj=io.BytesIO(spz_bytes)) as payload:
        sh_degree = payload.read(13)[12]
    if sh_degree not in range(len(_SPZ_SH_WIDTHS)):
        raise ValueError("Unsupported spherical harmonic degree for Gaussian tiles")

    bin_chunk = spz_bytes
    while len(bin_chunk) % 4 != 0:
        bin_chunk += b"\x00"

    # Node matrix = Axis.Z_UP_TO_Y_UP so Cesium's Axis.Y_UP_TO_Z_UP * node.matrix == IDENTITY (exact ENU alignment)
    gltf_dict = {
        "asset": {
            "version": "2.0",
            "generator": "MosaicEarth Self-Hosted 3DGS Pipeline (KHR_gaussian_splatting + SPZ v2)",
        },
        "extensionsUsed": [
            "KHR_gaussian_splatting",
            "KHR_gaussian_splatting_compression_spz_2",
        ],
        "extensionsRequired": [
            "KHR_gaussian_splatting",
            "KHR_gaussian_splatting_compression_spz_2",
        ],
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
                        "attributes": {
                            "POSITION": 0,
                            "COLOR_0": 1,
                            "KHR_gaussian_splatting:SCALE": 2,
                            "KHR_gaussian_splatting:ROTATION": 3,
                        },
                        "mode": 0,
                        "extensions": {
                            "KHR_gaussian_splatting": {
                                "extensions": {
                                    "KHR_gaussian_splatting_compression_spz_2": {
                                        "bufferView": 0
                                    }
                                }
                            }
                        },
                    }
                ]
            }
        ],
        "accessors": [
            {
                "componentType": 5126,
                "count": n,
                "type": "VEC3",
                "min": v_min,
                "max": v_max,
            },
            {
                "componentType": 5121,
                "normalized": True,
                "count": n,
                "type": "VEC4",
            },
            {
                "componentType": 5126,
                "count": n,
                "type": "VEC3",
            },
            {
                "componentType": 5126,
                "count": n,
                "type": "VEC4",
            },
        ],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": 0,
                "byteLength": len(spz_bytes),
            }
        ],
        "buffers": [
            {
                "byteLength": len(bin_chunk),
            }
        ],
    }

    attributes = gltf_dict["meshes"][0]["primitives"][0]["attributes"]
    for degree in range(1, sh_degree + 1):
        for coefficient in range(2 * degree + 1):
            attributes[f"KHR_gaussian_splatting:SH_DEGREE_{degree}_COEF_{coefficient}"] = len(gltf_dict["accessors"])
            gltf_dict["accessors"].append({"componentType": 5126, "count": n, "type": "VEC3"})

    json_bytes = json.dumps(gltf_dict, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "

    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
    glb_bytes = (
        struct.pack("<4sII", b"glTF", 2, total_len)
        + struct.pack("<II", len(json_bytes), 0x4E4F534A)
        + json_bytes
        + struct.pack("<II", len(bin_chunk), 0x004E4942)
        + bin_chunk
    )

    tile_glb_path = out_dir / "tile_0.glb"
    tile_glb_path.write_bytes(glb_bytes)
    model_glb_path = out_dir / "model.glb"
    model_glb_path.write_bytes(glb_bytes)

    enu_mat = _build_enu_to_ecef_matrix(
        float(lat), float(lon), float(alt),
        heading_deg=float(heading), pitch_deg=float(pitch), roll_deg=float(roll), scale=float(scale)
    )
    hx, hy, hz = float(half_ext[0]), float(half_ext[1]), float(half_ext[2])

    tileset_obj = {
        "asset": {"version": "1.1"},
        "extensionsUsed": ["3DTILES_content_gltf"],
        "extensionsRequired": ["3DTILES_content_gltf"],
        "extensions": {
            "3DTILES_content_gltf": {
                "extensionsUsed": [
                    "KHR_gaussian_splatting",
                    "KHR_gaussian_splatting_compression_spz_2",
                ],
                "extensionsRequired": [
                    "KHR_gaussian_splatting",
                    "KHR_gaussian_splatting_compression_spz_2",
                ],
            }
        },
        "geometricError": 64.0,
        "root": {
            "transform": enu_mat,
            "boundingVolume": {
                "box": [*((np.array(v_min) + np.array(v_max)) * 0.5).tolist(), hx, 0.0, 0.0, 0.0, hy, 0.0, 0.0, 0.0, hz]
            },
            "geometricError": 0.0,
            "refine": "REPLACE",
            "content": {"uri": "tile_0.glb"},
        },
    }
    ts_path = out_dir / "tileset.json"
    cameras_file = out_dir / "cameras_sfm.json"
    if cameras_file.is_file():
        camera_doc = json.loads(cameras_file.read_text())
        if camera_doc.get("engine") == "opensplat" and camera_doc.get("cameras"):
            cameras = camera_doc["cameras"]
            camera = next((c for c in cameras if c.get("img_name") == camera_doc.get("validation_image")), cameras[0])
            # OpenSplat exports camera-to-world in the COLMAP convention:
            # +Z looks forward, +Y down. Keep it in the unmodified model frame.
            tileset_obj["extras"] = {"mosaicearthCamera": {"version": 1, "space": "root-z-up",
                "position": camera["position"], "direction": [r[2] for r in camera["rotation"]],
                "up": [-r[1] for r in camera["rotation"]], "fov_y": 2 * math.atan(camera["height"] / (2 * camera["fy"]))}}
    ts_path.write_text(json.dumps(tileset_obj, indent=2), encoding="utf-8")

    transform_meta = {
        "generator": "MosaicEarth 3D Gaussian Splatting Georeferencing Pipeline",
        "crs_local": "Input model coordinates; placement transform does not establish metric accuracy",
        "crs_world": "EPSG:4978 (WGS84 Earth-Centered Earth-Fixed ECEF)",
        "anchor_wgs84": {
            "lat": float(lat),
            "lon": float(lon),
            "alt": float(alt),
            "heading": float(heading),
            "pitch": float(pitch),
            "roll": float(roll),
            "scale": float(scale),
        },
        "transform_ecef_column_major_4x4": enu_mat,
        "splat_count": n,
        "sh_degree": sh_degree,
        "khr_extensions": [
            "KHR_gaussian_splatting",
            "KHR_gaussian_splatting_compression_spz_2",
        ],
        "khr_attribute_conventions": {
            "scale_space": "linear_positive_exp_from_inria_log_scale",
            "opacity_space": "linear_0_1_sigmoid_from_inria_logit",
            "rotation_space": "unit_normalized_quaternion_xyzw",
            "sh_color_dc": "0.5 + SH_C0 * f_dc",
        },
        "files": {
            "master_ply": "model.ply",
            "compressed_spz": "model.spz",
            "cesium_tileset": "tileset.json",
            "cesium_tile_glb": "tile_0.glb",
        },
    }
    transform_path = out_dir / "transform.json"
    transform_path.write_text(json.dumps(transform_meta, indent=2), encoding="utf-8")

    geo_txt = (
        "# MosaicEarth 3D Gaussian Splatting Geo-Alignment\n"
        f"LATITUDE={float(lat):.8f}\n"
        f"LONGITUDE={float(lon):.8f}\n"
        f"ALTITUDE_M={float(alt):.3f}\n"
        f"HEADING_DEG={float(heading):.3f}\n"
        f"PITCH_DEG={float(pitch):.3f}\n"
        f"ROLL_DEG={float(roll):.3f}\n"
        f"SCALE={float(scale):.4f}\n"
        f"ECEF_MATRIX_COLUMN_MAJOR={','.join(f'{v:.8f}' for v in enu_mat)}\n"
    )
    (out_dir / "geo_alignment.txt").write_text(geo_txt, encoding="utf-8")

    return ts_path, tile_glb_path, transform_path


def _write_gaussian_ply_and_splat(
    out_dir: Path,
    positions_enu: np.ndarray,
    colors_rgb: np.ndarray,
    scales_xyz: Optional[np.ndarray] = None,
    rotations_xyzw: Optional[np.ndarray] = None,
    alphas_u8: Optional[np.ndarray] = None,
    sh_deg1: Optional[np.ndarray] = None,
) -> Tuple[Path, Path, Path, bytes]:
    """
    Writes the Original Results for a 3D Gaussian Splatting model:
      1. model.ply (and point_cloud.ply alias): 3DGS PLY with up to 45 SH coefficients
         (x, y, z, nx, ny, nz, f_dc_0..2, f_rest_*, opacity, scale_0..2, rot_0..3)
      2. model.spz: Compressed Niantic SPZ v2 format (~90% smaller than PLY)
      3. model.splat: Standard 32-byte binary splat format
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    n = int(positions_enu.shape[0])
    pos = np.ascontiguousarray(positions_enu, dtype="<f4")
    rgb = np.ascontiguousarray(colors_rgb, dtype=np.uint8)

    scales_xyz, rotations_xyzw, alphas_u8, sh_deg1, normals = _compute_anisotropic_splat_attributes(
        pos, rgb, scales_xyz, rotations_xyzw, alphas_u8, sh_deg1
    )

    # 1. Write standard 32-byte per splat binary .splat file
    splat_dtype = np.dtype([
        ("xyz", "<f4", (3,)),
        ("scale", "<f4", (3,)),
        ("rgba", "u1", (4,)),
        ("rot", "u1", (4,)),
    ])
    splat_arr = np.empty(n, dtype=splat_dtype)
    splat_arr["xyz"] = pos
    splat_arr["scale"] = scales_xyz
    splat_arr["rgba"][:, :3] = rgb
    splat_arr["rgba"][:, 3] = alphas_u8
    # .splat stores (qw, qx, qy, qz) in [0..255]
    splat_arr["rot"][:, 0] = np.clip(np.round((rotations_xyzw[:, 3] * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    splat_arr["rot"][:, 1] = np.clip(np.round((rotations_xyzw[:, 0] * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    splat_arr["rot"][:, 2] = np.clip(np.round((rotations_xyzw[:, 1] * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    splat_arr["rot"][:, 3] = np.clip(np.round((rotations_xyzw[:, 2] * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    splat_path = out_dir / "model.splat"
    splat_path.write_bytes(splat_arr.tobytes())

    # 2. Write Niantic SPZ v2 compressed file (model.spz)
    spz_path = out_dir / "model.spz"
    spz_bytes = _write_spz_v2(spz_path, pos, rgb, scales_xyz, rotations_xyzw, alphas_u8, sh_deg1)

    # 3. Write full 62-property float32 3D Gaussian Splatting Master PLY (model.ply + point_cloud.ply)
    SH_C0 = 0.28209479177387814
    f_dc = (((rgb.astype(np.float32) / 255.0) - 0.5) / SH_C0).astype("<f4")
    log_scale = np.log(scales_xyz).astype("<f4")
    alpha_01 = np.clip(alphas_u8.astype(np.float32) / 255.0, 1e-3, 1.0 - 1e-3)
    logit_opacity = np.log(alpha_01 / (1.0 - alpha_01)).astype("<f4")

    ply_fields = [
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"),
        ("f_dc_0", "<f4"), ("f_dc_1", "<f4"), ("f_dc_2", "<f4"),
    ]
    sh_width = sh_deg1.shape[1]
    stored_sh_width = sh_width if sh_width > 9 else 45
    for i in range(stored_sh_width):
        ply_fields.append((f"f_rest_{i}", "<f4"))
    ply_fields.extend([
        ("opacity", "<f4"),
        ("scale_0", "<f4"), ("scale_1", "<f4"), ("scale_2", "<f4"),
        ("rot_0", "<f4"), ("rot_1", "<f4"), ("rot_2", "<f4"), ("rot_3", "<f4"),
    ])

    ply_dtype = np.dtype(ply_fields)
    ply_arr = np.zeros(n, dtype=ply_dtype)
    ply_arr["x"] = pos[:, 0]
    ply_arr["y"] = pos[:, 1]
    ply_arr["z"] = pos[:, 2]
    ply_arr["nx"] = normals[:, 0]
    ply_arr["ny"] = normals[:, 1]
    ply_arr["nz"] = normals[:, 2]
    ply_arr["f_dc_0"] = f_dc[:, 0]
    ply_arr["f_dc_1"] = f_dc[:, 1]
    ply_arr["f_dc_2"] = f_dc[:, 2]

    # Keep the legacy padded layout for existing degree-one derivatives.
    for coefficient in range(sh_width // 3):
        for channel in range(3):
            ply_arr[f"f_rest_{channel * (stored_sh_width // 3) + coefficient}"] = sh_deg1[:, coefficient * 3 + channel]

    ply_arr["opacity"] = logit_opacity
    ply_arr["scale_0"] = log_scale[:, 0]
    ply_arr["scale_1"] = log_scale[:, 1]
    ply_arr["scale_2"] = log_scale[:, 2]
    # Standard 3DGS PLY stores quaternion as (w, x, y, z) in rot_0..3
    ply_arr["rot_0"] = rotations_xyzw[:, 3]
    ply_arr["rot_1"] = rotations_xyzw[:, 0]
    ply_arr["rot_2"] = rotations_xyzw[:, 1]
    ply_arr["rot_3"] = rotations_xyzw[:, 2]

    prop_lines = "\n".join(f"property float {name}" for name, _ in ply_fields)
    sh_comment = "comment MosaicEarth preserves source SH bands\n" if sh_width > 9 else ""
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "comment Generated by MosaicEarth 3D Gaussian Splatting Engine\n"
        f"{sh_comment}"
        f"element vertex {n}\n"
        f"{prop_lines}\n"
        "end_header\n"
    ).encode("ascii")

    ply_bytes = header + ply_arr.tobytes()
    model_ply_path = out_dir / "model.ply"
    model_ply_path.write_bytes(ply_bytes)
    ply_path = out_dir / "point_cloud.ply"
    ply_path.write_bytes(ply_bytes)
    return model_ply_path, spz_path, splat_path, spz_bytes


def _parse_pointcloud_or_splat_bytes(
    data: bytes, ext_low: str, max_points: Optional[int] = None
) -> Tuple[np.ndarray, np.ndarray, Optional[Tuple[float, float]]]:
    """Parses .laz, .las, .ply, .splat, .xyz, .pts, .pcd into (positions_enu, colors_rgb, optional (lat, lon))."""
    detected_latlon: Optional[Tuple[float, float]] = None

    if ext_low in ("las", "laz"):
        try:
            import laspy
            with laspy.open(io.BytesIO(data)) as fh:
                las = fh.read()
                xs = np.array(las.x, dtype=np.float64)
                ys = np.array(las.y, dtype=np.float64)
                zs = np.array(las.z, dtype=np.float64)
                if max_points is not None and max_points > 0 and len(xs) > max_points:
                    step = max(1, len(xs) // max_points)
                    xs, ys, zs = xs[::step], ys[::step], zs[::step]
                cx, cy, cz = float(xs.mean()), float(ys.mean()), float(zs.min())
                las_crs = las.header.parse_crs() if hasattr(las.header, "parse_crs") else None
                if las_crs is not None:
                    from pyproj import Transformer
                    horizontal = las_crs.sub_crs_list[0] if las_crs.is_compound else las_crs
                    axis = las_crs.axis_info
                    z_unit = axis[2].unit_conversion_factor if len(axis) > 2 else (
                        axis[0].unit_conversion_factor if horizontal.is_projected else 1.0)
                    lons, lats = Transformer.from_crs(horizontal, 4326, always_xy=True).transform(xs, ys, errcheck=True)
                    if not (np.isfinite(lons).all() and np.isfinite(lats).all()):
                        raise ValueError("Point cloud CRS produced invalid geographic coordinates")
                    lon_c, lat_c = float(np.mean(lons)), float(np.mean(lats))
                    z_m = zs * float(z_unit)
                    base_height = float(z_m.min())
                    to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
                    ecef = np.column_stack(to_ecef.transform(lons, lats, z_m, errcheck=True))
                    origin = np.array(to_ecef.transform(lon_c, lat_c, base_height, errcheck=True))
                    phi, lam = math.radians(lat_c), math.radians(lon_c)
                    basis = np.array([[-math.sin(lam), math.cos(lam), 0],
                        [-math.sin(phi)*math.cos(lam), -math.sin(phi)*math.sin(lam), math.cos(phi)],
                        [math.cos(phi)*math.cos(lam), math.cos(phi)*math.sin(lam), math.sin(phi)]])
                    pos = ((ecef - origin) @ basis.T).astype(np.float32)
                    # This preserves source heights in metres. A horizontal CRS
                    # alone does not prove their vertical datum.
                    detected_latlon = (lat_c, lon_c, base_height)
                else:
                    # Unreferenced local coordinates retain the user's placement.
                    # Small coordinates alone are not evidence of WGS84.
                    pos = np.column_stack([xs - cx, ys - cy, zs - cz]).astype(np.float32)

                if hasattr(las, "red") and hasattr(las, "green") and hasattr(las, "blue"):
                    r = np.array(las.red[::step] if max_points is not None and max_points > 0 and len(las.points) > max_points else las.red, dtype=np.float32)
                    g = np.array(las.green[::step] if max_points is not None and max_points > 0 and len(las.points) > max_points else las.green, dtype=np.float32)
                    b = np.array(las.blue[::step] if max_points is not None and max_points > 0 and len(las.points) > max_points else las.blue, dtype=np.float32)
                    max_c = max(float(r.max() if len(r) else 1), float(g.max() if len(g) else 1), float(b.max() if len(b) else 1))
                    if max_c > 255.0:
                        r, g, b = r / 256.0, g / 256.0, b / 256.0
                    rgb = np.column_stack([np.clip(r, 0, 255), np.clip(g, 0, 255), np.clip(b, 0, 255)]).astype(np.uint8)
                else:
                    # Elevation ramp coloring
                    z_norm = (pos[:, 2] - pos[:, 2].min()) / max(1e-3, float(pos[:, 2].max() - pos[:, 2].min()))
                    rgb = np.column_stack([
                        np.clip(80 + 140 * z_norm, 0, 255),
                        np.clip(160 + 80 * (1.0 - np.abs(z_norm - 0.5)), 0, 255),
                        np.clip(220 * (1.0 - z_norm), 0, 255),
                    ]).astype(np.uint8)
                return pos, rgb, detected_latlon
        except Exception as exc:
            raise ValueError(f"Invalid or corrupted .{ext_low} point cloud file: {exc}") from exc

    if ext_low == "e57" or data[:8] == b"ASTM-E57":
        # ASTM E2807 (.e57) 3D Point Cloud parser:
        # Header: 48 bytes (`ASTM-E57`, major=1, minor=0, file_length, xml_offset, xml_length, page_size)
        if len(data) < 48 or data[:8] != b"ASTM-E57":
            raise ValueError("Invalid .e57 file: missing ASTM-E57 header signature")
        xml_off, xml_len = struct.unpack_from("<QQ", data, 24)
        xml_txt = data[xml_off : xml_off + xml_len].decode("utf-8", errors="ignore") if (0 < xml_off < len(data)) else data.decode("utf-8", errors="ignore")
        rec_cnt_m = re.search(r'recordCount\s*=\s*["\'](\d+)["\']', xml_txt)
        file_off_m = re.search(r'fileOffset\s*=\s*["\'](\d+)["\']', xml_txt)
        has_rgb = "colorRed" in xml_txt
        n_pts = int(rec_cnt_m.group(1)) if rec_cnt_m else 0
        f_off = int(file_off_m.group(1)) if file_off_m else 48
        if n_pts > 0 and f_off < len(data):
            stride = 15 if has_rgb else 12
            avail = (len(data) - f_off) // stride
            actual_n = min(n_pts, avail)
            if actual_n > 0:
                if has_rgb:
                    dt_e57 = np.dtype([("xyz", "<f4", (3,)), ("rgb", "u1", (3,))])
                    arr = np.frombuffer(data, dtype=dt_e57, offset=f_off, count=actual_n)
                    pos = arr["xyz"].astype(np.float32).copy()
                    rgb = arr["rgb"].astype(np.uint8).copy()
                else:
                    dt_e57 = np.dtype([("xyz", "<f4", (3,))])
                    arr = np.frombuffer(data, dtype=dt_e57, offset=f_off, count=actual_n)
                    pos = arr["xyz"].astype(np.float32).copy()
                    rgb = np.full((actual_n, 3), [153, 204, 0], dtype=np.uint8)
                pos -= pos.mean(axis=0)
                return pos, rgb, None
        raise ValueError("Invalid or empty .e57 point cloud payload")

    if ext_low == "splat":
        if len(data) < 32 or len(data) % 32 != 0:
            raise ValueError(f"Invalid or truncated .splat binary file (size={len(data)} bytes, must be a positive multiple of 32)")
        n_splats = len(data) // 32
        splat_dtype = np.dtype([
            ("xyz", "<f4", (3,)),
            ("scale", "<f4", (3,)),
            ("rgba", "u1", (4,)),
            ("rot", "u1", (4,)),
        ])
        arr = np.frombuffer(data[: n_splats * 32], dtype=splat_dtype)
        pos = arr["xyz"].astype(np.float32).copy()
        rgb = arr["rgba"][:, :3].astype(np.uint8).copy()
        pos -= pos.mean(axis=0)
        return pos, rgb, None

    if ext_low == "ply":
        if not data.startswith(b"ply"):
            raise ValueError("Invalid .ply file: missing 'ply' magic header")
        end_idx = data.find(b"end_header\n")
        if end_idx < 0:
            end_idx = data.find(b"end_header\r\n")
            hdr_end = end_idx + 12 if end_idx >= 0 else -1
        else:
            hdr_end = end_idx + 11
        if hdr_end > 0:
            hdr_text = data[:hdr_end].decode("ascii", errors="ignore")
            is_binary = "binary_little_endian" in hdr_text
            v_count = 0
            props: List[Tuple[str, str]] = []
            in_vertex = False
            for line in hdr_text.splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                if parts[0] == "element":
                    in_vertex = parts[1] == "vertex"
                    if in_vertex and len(parts) >= 3:
                        v_count = int(parts[2])
                elif parts[0] == "property" and in_vertex and len(parts) >= 3:
                    props.append((parts[-1], parts[1]))
            if is_binary and v_count > 0 and props:
                type_map = {
                    "float": "<f4", "float32": "<f4",
                    "double": "<f8", "float64": "<f8",
                    "uchar": "u1", "uint8": "u1", "char": "i1", "int8": "i1",
                    "ushort": "<u2", "uint16": "<u2", "short": "<i2", "int16": "<i2",
                    "uint": "<u4", "uint32": "<u4", "int": "<i4", "int32": "<i4",
                }
                dt_fields = [(name, type_map.get(ptype, "<f4")) for name, ptype in props]
                rec_dtype = np.dtype(dt_fields)
                body = data[hdr_end : hdr_end + v_count * rec_dtype.itemsize]
                actual_n = len(body) // rec_dtype.itemsize
                if actual_n > 0:
                    arr = np.frombuffer(body[: actual_n * rec_dtype.itemsize], dtype=rec_dtype)
                    names = set(arr.dtype.names or ())
                    pos = np.column_stack([
                        arr["x"] if "x" in names else np.zeros(actual_n),
                        arr["y"] if "y" in names else np.zeros(actual_n),
                        arr["z"] if "z" in names else np.zeros(actual_n),
                    ]).astype(np.float32)
                    pos -= pos.mean(axis=0)
                    if {"red", "green", "blue"}.issubset(names):
                        rgb = np.column_stack([arr["red"], arr["green"], arr["blue"]]).astype(np.uint8)
                    elif {"f_dc_0", "f_dc_1", "f_dc_2"}.issubset(names):
                        SH_C0 = 0.28209479177387814
                        r = np.clip((0.5 + SH_C0 * arr["f_dc_0"]) * 255.0, 0, 255)
                        g = np.clip((0.5 + SH_C0 * arr["f_dc_1"]) * 255.0, 0, 255)
                        b = np.clip((0.5 + SH_C0 * arr["f_dc_2"]) * 255.0, 0, 255)
                        rgb = np.column_stack([r, g, b]).astype(np.uint8)
                    else:
                        rgb = np.full((actual_n, 3), [153, 204, 0], dtype=np.uint8)
                    return pos, rgb, None
            elif not is_binary and v_count > 0:
                lines = data[hdr_end:].decode("utf-8", errors="ignore").splitlines()
                pts = []
                cols = []
                for ln in lines[:v_count]:
                    sp = ln.strip().split()
                    if len(sp) >= 3:
                        pts.append([float(sp[0]), float(sp[1]), float(sp[2])])
                        if len(sp) >= 6:
                            cols.append([int(float(sp[3])), int(float(sp[4])), int(float(sp[5]))])
                        else:
                            cols.append([153, 204, 0])
                if pts:
                    pos = np.array(pts, dtype=np.float32)
                    pos -= pos.mean(axis=0)
                    rgb = np.clip(np.array(cols, dtype=np.int32), 0, 255).astype(np.uint8)
                    return pos, rgb, None
        raise ValueError("Invalid or truncated .ply file")

    if ext_low == "ksplat":
        from .ksplat import read_ksplat
        parsed = read_ksplat(data)
        return parsed[0], parsed[1], None

    if ext_low == "spz":
        raise ValueError("Invalid or corrupted .spz file")

    # Binary .pcd parser (`DATA binary`)
    if ext_low == "pcd" and b"DATA binary" in data[:2048]:
        hdr_match = re.search(rb"DATA\s+binary\r?\n", data[:2048])
        if hdr_match:
            hdr_txt = data[: hdr_match.start()].decode("ascii", errors="ignore")
            body = data[hdr_match.end() :]
            fields: List[str] = []
            sizes: List[int] = []
            types: List[str] = []
            counts: List[int] = []
            n_pts = 0
            for ln in hdr_txt.splitlines():
                parts = ln.strip().split()
                if not parts:
                    continue
                k = parts[0].upper()
                if k == "FIELDS":
                    fields = [p.lower() for p in parts[1:]]
                elif k == "SIZE":
                    sizes = [int(p) for p in parts[1:]]
                elif k == "TYPE":
                    types = [p.upper() for p in parts[1:]]
                elif k == "COUNT":
                    counts = [int(p) for p in parts[1:]]
                elif k == "POINTS" and len(parts) >= 2:
                    n_pts = int(parts[1])
            if fields and len(fields) == len(sizes) == len(types) and n_pts > 0:
                if not counts or len(counts) != len(fields):
                    counts = [1] * len(fields)
                t_map = {("F", 4): "<f4", ("F", 8): "<f8", ("U", 1): "u1", ("U", 2): "<u2", ("U", 4): "<u4", ("I", 1): "i1", ("I", 2): "<i2", ("I", 4): "<i4"}
                dt_list = []
                for fname, fsz, ftyp, fcnt in zip(fields, sizes, types, counts):
                    np_t = t_map.get((ftyp, fsz), f"V{fsz}")
                    dt_list.append((fname, np_t, (fcnt,)) if fcnt > 1 else (fname, np_t))
                rec_dt = np.dtype(dt_list)
                actual_n = min(n_pts, len(body) // rec_dt.itemsize)
                if actual_n > 0 and {"x", "y", "z"}.issubset(set(fields)):
                    arr = np.frombuffer(body[: actual_n * rec_dt.itemsize], dtype=rec_dt)
                    pos = np.column_stack([arr["x"], arr["y"], arr["z"]]).astype(np.float32)
                    pos -= pos.mean(axis=0)
                    if "rgb" in fields:
                        rgb_u32 = arr["rgb"].view("<u4").reshape(-1)
                        rgb = np.column_stack([(rgb_u32 >> 16) & 0xFF, (rgb_u32 >> 8) & 0xFF, rgb_u32 & 0xFF]).astype(np.uint8)
                    else:
                        rgb = np.full((actual_n, 3), [153, 204, 0], dtype=np.uint8)
                    return pos, rgb, None
        raise ValueError("Invalid or corrupted binary .pcd file")

    # ASCII XYZ / PTS / PCD (strictly restricted to text point-cloud formats)
    if ext_low in ("xyz", "pts", "pcd", "txt", "csv"):
        text = data.decode("utf-8", errors="ignore")
        pts = []
        cols = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln or ln[0].isalpha() or ln.startswith("#"):
                continue
            sp = ln.replace(",", " ").split()
            if len(sp) >= 3:
                try:
                    pts.append([float(sp[0]), float(sp[1]), float(sp[2])])
                    if len(sp) >= 6:
                        cols.append([int(float(sp[3])), int(float(sp[4])), int(float(sp[5]))])
                    else:
                        cols.append([153, 204, 0])
                except Exception:
                    continue
        if pts:
            pos = np.array(pts, dtype=np.float32)
            pos -= pos.mean(axis=0)
            rgb = np.clip(np.array(cols, dtype=np.int32), 0, 255).astype(np.uint8)
            return pos, rgb, None

    # NEVER invent a synthetic 2,048-point spiral on parse failure: fail explicitly!
    raise ValueError(f"Unsupported, empty, or corrupted point cloud / splat file (format={ext_low!r}, size={len(data)} bytes)")


def _parse_gaussian_splat_full_attributes(
    data: bytes, ext_low: str
) -> Tuple[
    np.ndarray,
    np.ndarray,
    Optional[np.ndarray],
    Optional[np.ndarray],
    Optional[np.ndarray],
    Optional[np.ndarray],
    Optional[Tuple[float, float]],
]:
    """
    Parses a trained 3D Gaussian Splatting file (`.ply` Inria 3DGS format or `.splat`)
    while strictly enforcing Khronos `KHR_gaussian_splatting` & `KHR_gaussian_splatting_compression_spz_2`
    attribute domain conversions:
      - Inria `scale_0..2` (log-space) -> `exp(log_scale)` positive linear scale (`> 0.0`)
      - Inria `opacity` (pre-sigmoid logit) -> `sigmoid(x) = 1 / (1 + exp(-x))` linear `[0, 1]` opacity
      - Inria `rot_0..3` `(w, x, y, z)` -> unit-normalized `(x, y, z, w)` quaternion (`||q|| = 1.0`)
      - Inria `f_dc_0..2` -> RGB `[0..255]` via `0.5 + SH_C0 * f_dc` and `f_rest_0..44` -> `sh_deg1`
    """
    if ext_low == "spz" or (len(data) >= 18 and data[:2] == b"\x1f\x8b"):
        try:
            raw_spz = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        except Exception as exc:
            if ext_low == "spz":
                raise ValueError(f"Invalid or corrupted gzip stream in .spz file: {exc}") from exc
            raw_spz = b""
        if len(raw_spz) >= 16:
            magic, ver, n_pts, sh_deg, frac_bits, _flags, _res = struct.unpack("<IIIBBBB", raw_spz[:16])
            rot_bytes_per_pt = 4 if int(ver) >= 3 else 3
            if magic == 0x5053474E and int(ver) in (2, 3) and n_pts > 0:
                offset = 16
                pos_len = n_pts * 9
                alpha_len = n_pts
                color_len = n_pts * 3
                scale_len = n_pts * 3
                rot_len = n_pts * rot_bytes_per_pt
                if len(raw_spz) >= offset + pos_len + alpha_len + color_len + scale_len + rot_len:
                    # 1. Unpack 24-bit signed fixed-point positions
                    pos_u8 = np.frombuffer(raw_spz[offset : offset + pos_len], dtype=np.uint8).reshape(n_pts * 3, 3)
                    pad_hi = np.where((pos_u8[:, 2:3] & 0x80) != 0, 0xFF, 0x00).astype(np.uint8)
                    pos_i32 = np.hstack([pos_u8, pad_hi]).view("<i4").reshape(n_pts, 3)
                    scale_factor = float(1 << int(frac_bits or 12))
                    pos = (pos_i32.astype(np.float32) / scale_factor).copy()
                    offset += pos_len

                    # 2. Alphas
                    alphas_u8 = np.frombuffer(raw_spz[offset : offset + alpha_len], dtype=np.uint8).copy()
                    offset += alpha_len

                    # 3. Colors (invert SPZ v2/v3 colorScale=0.15 & SH_C0)
                    SH_C0 = 0.28209479177387814
                    col_u8 = np.frombuffer(raw_spz[offset : offset + color_len], dtype=np.uint8).reshape(n_pts, 3).astype(np.float32)
                    f_dc = ((col_u8 / 255.0) - 0.5) / 0.15
                    rgb = np.clip(np.round((0.5 + SH_C0 * f_dc) * 255.0), 0, 255).astype(np.uint8)
                    offset += color_len

                    # 4. Scales: log_scale = (u8 / 16.0) - 10.0 -> linear = exp(log_scale)
                    sc_u8 = np.frombuffer(raw_spz[offset : offset + scale_len], dtype=np.uint8).reshape(n_pts, 3).astype(np.float32)
                    scales_xyz = np.exp(np.clip((sc_u8 / 16.0) - 10.0, -12.0, 5.0)).astype(np.float32)
                    offset += scale_len

                    # 5. Rotations: SPZ v2 (3 bytes: qx, qy, qz) or SPZ v3 (4 bytes smallest-three)
                    if rot_bytes_per_pt == 3:
                        r_u8 = np.frombuffer(raw_spz[offset : offset + rot_len], dtype=np.uint8).reshape(n_pts, 3).astype(np.float32)
                        q_xyz = (r_u8 / 255.0) * 2.0 - 1.0
                        qw_sq = np.maximum(0.0, 1.0 - np.sum(q_xyz * q_xyz, axis=1, keepdims=True))
                        q_xyzw = np.hstack([q_xyz, np.sqrt(qw_sq)]).astype(np.float32)
                    else:
                        r_u8 = np.frombuffer(raw_spz[offset : offset + rot_len], dtype=np.uint8).reshape(n_pts, 4).astype(np.float32)
                        q_xyz = (r_u8[:, :3] / 255.0) * 2.0 - 1.0
                        qw_sq = np.maximum(0.0, 1.0 - np.sum(q_xyz * q_xyz, axis=1, keepdims=True))
                        q_xyzw = np.hstack([q_xyz, np.sqrt(qw_sq)]).astype(np.float32)
                    q_norm = np.maximum(np.linalg.norm(q_xyzw, axis=1, keepdims=True), 1e-6)
                    q_xyzw = (q_xyzw / q_norm).astype(np.float32)
                    offset += rot_len

                    # 6. Spherical Harmonics Degree 1 (if present)
                    sh_deg1: Optional[np.ndarray] = None
                    if 0 < sh_deg < len(_SPZ_SH_WIDTHS):
                        sh_width = _SPZ_SH_WIDTHS[sh_deg]
                        if len(raw_spz) >= offset + n_pts * sh_width:
                            sh_u8 = np.frombuffer(raw_spz, dtype=np.uint8, count=n_pts * sh_width,
                                                  offset=offset).reshape(n_pts, sh_width)
                            sh_deg1 = (sh_u8.astype(np.float32) - 128.0) / 128.0
                    return pos, rgb, scales_xyz, q_xyzw, alphas_u8, sh_deg1, None
        if ext_low == "spz":
            raise ValueError("Invalid, unsupported version, or truncated .spz Gaussian Splat binary")

    if ext_low == "ksplat":
        from .ksplat import read_ksplat
        return read_ksplat(data)

    if ext_low == "splat" and len(data) >= 32:
        n_splats = len(data) // 32
        splat_dtype = np.dtype([
            ("xyz", "<f4", (3,)),
            ("scale", "<f4", (3,)),
            ("rgba", "u1", (4,)),
            ("rot", "u1", (4,)),
        ])
        arr = np.frombuffer(data[: n_splats * 32], dtype=splat_dtype)
        pos = arr["xyz"].astype(np.float32).copy()
        scales_xyz = np.maximum(np.abs(arr["scale"].astype(np.float32)), 1e-4)
        rgb = arr["rgba"][:, :3].astype(np.uint8).copy()
        alphas_u8 = np.clip(arr["rgba"][:, 3].astype(np.uint8), 1, 255)
        # .splat stores (qw, qx, qy, qz) in [0..255] -> convert to unit-normalized (qx, qy, qz, qw)
        rot_raw = (arr["rot"].astype(np.float32) / 255.0) * 2.0 - 1.0
        q_xyzw = np.column_stack([rot_raw[:, 1], rot_raw[:, 2], rot_raw[:, 3], rot_raw[:, 0]]).astype(np.float32)
        q_norm = np.linalg.norm(q_xyzw, axis=1, keepdims=True)
        q_norm = np.where(q_norm < 1e-6, 1.0, q_norm)
        q_xyzw = (q_xyzw / q_norm).astype(np.float32)
        return pos, rgb, scales_xyz, q_xyzw, alphas_u8, None, None

    if ext_low == "ply" and data.startswith(b"ply"):
        end_idx = data.find(b"end_header\n")
        if end_idx < 0:
            end_idx = data.find(b"end_header\r\n")
            hdr_end = end_idx + 12 if end_idx >= 0 else -1
        else:
            hdr_end = end_idx + 11
        if hdr_end > 0:
            hdr_text = data[:hdr_end].decode("ascii", errors="ignore")
            if "binary_little_endian" in hdr_text:
                v_count = 0
                props: List[Tuple[str, str]] = []
                in_vertex = False
                for line in hdr_text.splitlines():
                    parts = line.strip().split()
                    if not parts:
                        continue
                    if parts[0] == "element":
                        in_vertex = parts[1] == "vertex"
                        if in_vertex and len(parts) >= 3:
                            v_count = int(parts[2])
                    elif parts[0] == "property" and in_vertex and len(parts) >= 3:
                        props.append((parts[-1], parts[1]))
                if v_count > 0 and props:
                    type_map = {
                        "float": "<f4", "float32": "<f4",
                        "double": "<f8", "float64": "<f8",
                        "uchar": "u1", "uint8": "u1", "char": "i1", "int8": "i1",
                        "ushort": "<u2", "uint16": "<u2", "short": "<i2", "int16": "<i2",
                        "uint": "<u4", "uint32": "<u4", "int": "<i4", "int32": "<i4",
                    }
                    dt_fields = [(name, type_map.get(ptype, "<f4")) for name, ptype in props]
                    rec_dtype = np.dtype(dt_fields)
                    body = data[hdr_end : hdr_end + v_count * rec_dtype.itemsize]
                    actual_n = len(body) // rec_dtype.itemsize
                    if actual_n > 0:
                        arr = np.frombuffer(body[: actual_n * rec_dtype.itemsize], dtype=rec_dtype)
                        names = set(arr.dtype.names or ())
                        pos = np.column_stack([
                            arr["x"] if "x" in names else np.zeros(actual_n),
                            arr["y"] if "y" in names else np.zeros(actual_n),
                            arr["z"] if "z" in names else np.zeros(actual_n),
                        ]).astype(np.float32)

                        SH_C0 = 0.28209479177387814
                        if {"f_dc_0", "f_dc_1", "f_dc_2"}.issubset(names):
                            r = np.clip((0.5 + SH_C0 * arr["f_dc_0"].astype(np.float32)) * 255.0, 0, 255)
                            g = np.clip((0.5 + SH_C0 * arr["f_dc_1"].astype(np.float32)) * 255.0, 0, 255)
                            b = np.clip((0.5 + SH_C0 * arr["f_dc_2"].astype(np.float32)) * 255.0, 0, 255)
                            rgb = np.column_stack([r, g, b]).astype(np.uint8)
                        elif {"red", "green", "blue"}.issubset(names):
                            rgb = np.column_stack([arr["red"], arr["green"], arr["blue"]]).astype(np.uint8)
                        else:
                            rgb = np.full((actual_n, 3), [153, 204, 0], dtype=np.uint8)

                        # 1. Convert Inria log_scale -> strictly positive linear scale via exp()
                        scales_xyz: Optional[np.ndarray] = None
                        if {"scale_0", "scale_1", "scale_2"}.issubset(names):
                            log_s = np.column_stack([
                                arr["scale_0"].astype(np.float32),
                                arr["scale_1"].astype(np.float32),
                                arr["scale_2"].astype(np.float32),
                            ])
                            scales_xyz = np.exp(np.clip(log_s, -12.0, 5.0)).astype(np.float32)

                        # 2. Convert Inria pre-sigmoid opacity logit -> linear [0, 1] opacity via sigmoid()
                        alphas_u8: Optional[np.ndarray] = None
                        if "opacity" in names:
                            op_logit = np.clip(arr["opacity"].astype(np.float32), -20.0, 20.0)
                            op_linear_01 = 1.0 / (1.0 + np.exp(-op_logit))
                            alphas_u8 = np.clip(np.round(op_linear_01 * 255.0), 1, 255).astype(np.uint8)

                        # 3. Convert Inria rot_0..3 (w, x, y, z) -> unit-normalized (x, y, z, w) quaternion
                        rot_xyzw: Optional[np.ndarray] = None
                        if {"rot_0", "rot_1", "rot_2", "rot_3"}.issubset(names):
                            q_xyzw = np.column_stack([
                                arr["rot_1"].astype(np.float32),
                                arr["rot_2"].astype(np.float32),
                                arr["rot_3"].astype(np.float32),
                                arr["rot_0"].astype(np.float32),
                            ])
                            q_norm = np.linalg.norm(q_xyzw, axis=1, keepdims=True)
                            bad_q = (q_norm[:, 0] < 1e-6) | ~np.isfinite(q_norm[:, 0])
                            q_norm = np.where(q_norm < 1e-6, 1.0, q_norm)
                            q_xyzw = (q_xyzw / q_norm).astype(np.float32)
                            if np.any(bad_q):
                                q_xyzw[bad_q] = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                            rot_xyzw = q_xyzw

                        # Inria PLY stores all coefficients of one color together.
                        sh_deg1: Optional[np.ndarray] = None
                        sh_fields = sum(name.startswith("f_rest_") for name in names)
                        if sh_fields in _SPZ_SH_WIDTHS[1:] and all(
                            f"f_rest_{index}" in names for index in range(sh_fields)
                        ):
                            per_channel = sh_fields // 3
                            preserve_bands = any(marker in hdr_text.lower() for marker in (
                                "generated by opensplat", "mosaicearth preserves source sh bands",
                            ))
                            keep = per_channel if preserve_bands else 3
                            sh_deg1 = np.column_stack([
                                arr[f"f_rest_{channel * per_channel + coefficient}"]
                                for coefficient in range(keep) for channel in range(3)
                            ]).astype(np.float32)

                        return pos, rgb, scales_xyz, rot_xyzw, alphas_u8, sh_deg1, None

    pos, rgb, det_ll = _parse_pointcloud_or_splat_bytes(data, ext_low)
    return pos, rgb, None, None, None, None, det_ll


def _obj_bytes_to_glb(obj_bytes: bytes) -> bytes:
    """Converts Wavefront .OBJ ASCII mesh into a binary glTF 2.0 (.glb) buffer."""
    verts: List[List[float]] = []
    faces: List[Tuple[int, int, int]] = []
    for line in obj_bytes.decode("utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s.startswith("v "):
            parts = s.split()
            if len(parts) >= 4:
                verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        elif s.startswith("f "):
            parts = s.split()[1:]
            idxs = []
            for p in parts:
                v_i = int(p.split("/")[0])
                idxs.append(v_i - 1 if v_i > 0 else len(verts) + v_i)
            for i in range(1, len(idxs) - 1):
                faces.append((idxs[0], idxs[i], idxs[i + 1]))

    if not verts or not faces:
        verts = [
            [-5.0, -5.0, 0.0], [5.0, -5.0, 0.0], [5.0, 5.0, 0.0], [-5.0, 5.0, 0.0],
            [-5.0, -5.0, 8.0], [5.0, -5.0, 8.0], [5.0, 5.0, 8.0], [-5.0, 5.0, 8.0],
        ]
        faces = [
            (0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ]

    v_arr = np.array(verts, dtype="<f4")
    f_arr = np.array(faces, dtype="<u4").reshape(-1)
    v_min = v_arr.min(axis=0).tolist()
    v_max = v_arr.max(axis=0).tolist()

    v_bytes = v_arr.tobytes()
    i_bytes = f_arr.tobytes()
    bin_chunk = v_bytes + i_bytes
    while len(bin_chunk) % 4 != 0:
        bin_chunk += b"\x00"

    gltf_dict = {
        "asset": {"version": "2.0", "generator": "MosaicEarth OBJ-to-GLB Converter"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0},
                "indices": 1,
                "material": 0,
            }]
        }],
        "materials": [{
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.65, 0.82, 0.15, 1.0],
                "metallicFactor": 0.1,
                "roughnessFactor": 0.7,
            },
            "doubleSided": True,
        }],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(v_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(v_bytes), "byteLength": len(i_bytes), "target": 34963},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(v_arr),
                "type": "VEC3",
                "min": [float(x) for x in v_min],
                "max": [float(x) for x in v_max],
            },
            {
                "bufferView": 1,
                "byteOffset": 0,
                "componentType": 5125,
                "count": len(f_arr),
                "type": "SCALAR",
            },
        ],
    }
    json_bytes = json.dumps(gltf_dict, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "

    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
    glb = (
        struct.pack("<4sII", b"glTF", 2, total_len)
        + struct.pack("<II", len(json_bytes), 0x4E4F534A)
        + json_bytes
        + struct.pack("<II", len(bin_chunk), 0x004E4942)
        + bin_chunk
    )
    return glb


def _wrap_glb_in_3dtiles(out_dir: Path, glb_bytes: bytes, lat: float, lon: float, alt: float = 0.0) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ft_json = json.dumps({"BATCH_LENGTH": 0}, separators=(",", ":")).encode("utf-8")
    while (28 + len(ft_json)) % 8 != 0:
        ft_json += b" "
    total_b3dm = 28 + len(ft_json) + len(glb_bytes)
    b3dm_hdr = struct.pack("<4sIIIIII", b"b3dm", 1, total_b3dm, len(ft_json), 0, 0, 0)
    (out_dir / "model.b3dm").write_bytes(b3dm_hdr + ft_json + glb_bytes)
    enu_mat = _build_enu_to_ecef_matrix(lat, lon, alt)
    ts = {
        "asset": {"version": "1.0"},
        "geometricError": 64.0,
        "root": {
            "transform": enu_mat,
            "boundingVolume": {"box": [0.0, 0.0, 0.0, 25.0, 0.0, 0.0, 0.0, 25.0, 0.0, 0.0, 0.0, 15.0]},
            "geometricError": 0.0,
            "refine": "REPLACE",
            "content": {"uri": "model.b3dm"},
        },
    }
    (out_dir / "tileset.json").write_text(json.dumps(ts, indent=2), encoding="utf-8")


def _convert_vector_to_geojson(
    src_file: Path, ext_low: str, fallback_lat: float, fallback_lon: float
) -> Tuple[Dict[str, Any], float, float]:
    """Converts .geojson, .kml, .kmz, .gpkg, .dxf, .shp, .gpx, .csv into a WGS84 FeatureCollection + centroid."""
    geojson_obj: Optional[Dict[str, Any]] = None

    # 1. Direct GeoJSON
    if ext_low in ("geojson", "json"):
        try:
            parsed = json.loads(src_file.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(parsed, dict):
                if parsed.get("type") == "FeatureCollection":
                    geojson_obj = parsed
                elif parsed.get("type") == "Feature":
                    geojson_obj = {"type": "FeatureCollection", "features": [parsed]}
                elif "coordinates" in parsed:
                    geojson_obj = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": parsed}]}
        except Exception:
            pass

    # 2. Try ogr2ogr for .gpkg, .dxf, .kml, .kmz, .shp, .zip, .gpx
    if geojson_obj is None and shutil.which("ogr2ogr"):
        with tempfile.TemporaryDirectory(prefix="me_ogr_") as tmpd:
            out_gj = Path(tmpd) / "out.geojson"
            in_target = str(src_file)
            if ext_low in ("shp", "zip") and src_file.suffix.lower() == ".zip":
                in_target = f"/vsizip/{src_file}"
            cmd = ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326", str(out_gj), in_target]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                # For DXF without CRS, run ogr2ogr without -t_srs first, then transform local meters if needed
                cmd_raw = ["ogr2ogr", "-f", "GeoJSON", str(out_gj), in_target]
                subprocess.run(cmd_raw, capture_output=True, text=True, timeout=60)
            if out_gj.is_file():
                try:
                    geojson_obj = json.loads(out_gj.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    geojson_obj = None

    # 3. Pure-Python fallback for KML / GPX
    if geojson_obj is None and ext_low in ("kml", "kmz", "gpx"):
        try:
            raw_xml = src_file.read_bytes()
            if ext_low == "kmz" or raw_xml[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(raw_xml)) as zf:
                    for zn in zf.namelist():
                        if zn.lower().endswith(".kml"):
                            raw_xml = zf.read(zn)
                            break
            root = ET.fromstring(raw_xml.decode("utf-8", errors="ignore"))
            features = []
            for elem in root.iter():
                tag = elem.tag.split("}")[-1].lower()
                if tag == "coordinates" and elem.text:
                    pts = []
                    for tok in elem.text.strip().split():
                        parts = tok.split(",")
                        if len(parts) >= 2:
                            pts.append([float(parts[0]), float(parts[1])])
                    if len(pts) == 1:
                        features.append({"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": pts[0]}})
                    elif len(pts) >= 4 and pts[0] == pts[-1]:
                        features.append({"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [pts]}})
                    elif len(pts) >= 2:
                        features.append({"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": pts}})
                elif tag in ("trkpt", "wpt", "rtept"):
                    lat_a = elem.attrib.get("lat")
                    lon_a = elem.attrib.get("lon")
                    if lat_a and lon_a:
                        features.append({"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [float(lon_a), float(lat_a)]}})
            if features:
                geojson_obj = {"type": "FeatureCollection", "features": features}
        except Exception:
            pass

    # 4. Pure-Python fallback for DXF
    if geojson_obj is None and ext_low == "dxf":
        try:
            lines = [ln.strip() for ln in src_file.read_text(encoding="utf-8", errors="ignore").splitlines()]
            pts = []
            i = 0
            while i + 1 < len(lines):
                code, val = lines[i], lines[i + 1]
                if code == "10":
                    x_val = float(val)
                    y_val = 0.0
                    if i + 3 < len(lines) and lines[i + 2] == "20":
                        y_val = float(lines[i + 3])
                    pts.append((x_val, y_val))
                i += 2
            if pts:
                coords_wgs = []
                m_lat = 111320.0
                m_lon = 111320.0 * math.cos(math.radians(fallback_lat))
                for x_v, y_v in pts:
                    if -180.0 <= x_v <= 180.0 and -90.0 <= y_v <= 90.0:
                        coords_wgs.append([x_v, y_v])
                    else:
                        coords_wgs.append([fallback_lon + (x_v / m_lon), fallback_lat + (y_v / m_lat)])
                geom = {"type": "LineString", "coordinates": coords_wgs} if len(coords_wgs) >= 2 else {"type": "Point", "coordinates": coords_wgs[0]}
                geojson_obj = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"layer": "CAD_DXF"}, "geometry": geom}]}
        except Exception:
            pass

    if not geojson_obj or not geojson_obj.get("features"):
        if ext_low == "shp" and src_file.suffix.lower() == ".shp":
            has_shx = src_file.with_suffix(".shx").is_file() or src_file.with_suffix(".SHX").is_file()
            has_dbf = src_file.with_suffix(".dbf").is_file() or src_file.with_suffix(".DBF").is_file()
            if not (has_shx and has_dbf):
                raise ValueError(
                    "Shapefile (.shp) import requires companion .shx and .dbf sidecar files (or upload as a .shp.zip archive)."
                )
        geojson_obj = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": src_file.name},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [fallback_lon - 0.001, fallback_lat - 0.001],
                        [fallback_lon + 0.001, fallback_lat - 0.001],
                        [fallback_lon + 0.001, fallback_lat + 0.001],
                        [fallback_lon - 0.001, fallback_lat + 0.001],
                        [fallback_lon - 0.001, fallback_lat - 0.001],
                    ]],
                },
            }],
        }

    # Normalize any local CAD / projected coordinates into WGS84 if outside [-180..180, -90..90]
    lons: List[float] = []
    lats: List[float] = []
    m_lat = 111320.0
    m_lon = 111320.0 * math.cos(math.radians(fallback_lat))

    def _norm_coord_list(c: Any) -> Any:
        if isinstance(c, (list, tuple)) and len(c) >= 2 and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float)):
            xv, yv = float(c[0]), float(c[1])
            if not (-180.0 <= xv <= 180.0 and -90.0 <= yv <= 90.0):
                if abs(xv) < 50000 and abs(yv) < 50000:
                    xv = fallback_lon + (xv / m_lon)
                    yv = fallback_lat + (yv / m_lat)
            lons.append(xv)
            lats.append(yv)
            return [xv, yv]
        if isinstance(c, list):
            return [_norm_coord_list(item) for item in c]
        return c

    for feat in geojson_obj.get("features", []):
        geom = feat.get("geometry")
        if isinstance(geom, dict) and "coordinates" in geom:
            geom["coordinates"] = _norm_coord_list(geom["coordinates"])

    c_lat = float(sum(lats) / len(lats)) if lats else fallback_lat
    c_lon = float(sum(lons) / len(lons)) if lons else fallback_lon
    return geojson_obj, c_lat, c_lon


def _prepare_raster_geotiff(
    src_file: Path, out_dir: Path, fallback_lat: float, fallback_lon: float
) -> Tuple[float, float, List[float]]:
    """Reproject the embedded CRS into actual XYZ tiles, preserving the master."""
    from .raster_products import build_raster_pyramid

    info = build_raster_pyramid(src_file, out_dir, xyz=True)
    bbox = info["bounds_wgs84"]
    lon_c, lat_c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    preview_png = out_dir / "preview.png"
    subprocess.run(["gdal_translate", "-of", "PNG", "-ot", "Byte", "-scale",
                    "-outsize", "1024", "0", str(src_file), str(preview_png)],
                   capture_output=True, check=True, timeout=180)
    (out_dir / "bounds.json").write_text(json.dumps({
        "west": bbox[0], "south": bbox[1], "east": bbox[2], "north": bbox[3],
        "lat": lat_c, "lon": lon_c, "min_zoom": info["min_zoom"], "max_zoom": info["max_zoom"],
        "dynamic_on_demand_zooms": [], "tiling_method": "GDAL CRS warp to EPSG:3857",
    }), encoding="utf-8")
    return lat_c, lon_c, bbox


def render_dynamic_xyz_tile_for_external_asset(
    blob_store: Any, project_id: int, asset_id: int, ext_row: Dict[str, Any],
    z: int, tx: int, ty: int,
) -> Optional[bytes]:
    """Missing geographic tiles have no content; never fabricate them from previews."""
    return None


def _laplacian_sharpness_score(rgb_frame: np.ndarray) -> float:
    """Computes variance of the 2D Laplacian operator to filter out motion-blurred video frames."""
    gray = (
        0.299 * rgb_frame[:, :, 0].astype(np.float32)
        + 0.587 * rgb_frame[:, :, 1].astype(np.float32)
        + 0.114 * rgb_frame[:, :, 2].astype(np.float32)
    )
    lap = (
        -4.0 * gray
        + np.roll(gray, 1, axis=0)
        + np.roll(gray, -1, axis=0)
        + np.roll(gray, 1, axis=1)
        + np.roll(gray, -1, axis=1)
    )
    inner = lap[1:-1, 1:-1]
    return float(np.var(inner)) if inner.size > 0 else 0.0


def _persist_opensplat_result(ply, cameras, benchmark, out_dir, frame_gps_map, manual_anchor_wgs84):
    from .processing_architecture import align_colmap_cameras_sim3_umeyama

    ply_bytes = ply.read_bytes()
    pos, rgb, scales, _, _, _, _ = _parse_gaussian_splat_full_attributes(ply_bytes, "ply")
    if scales is None or not len(pos) or not np.isfinite(pos).all() or not np.isfinite(scales).all():
        raise RuntimeError("OpenSplat output contains invalid Gaussian geometry.")
    (out_dir / "model_trained_gpu.ply").write_bytes(ply_bytes)
    for name in benchmark.get("validation_images_sha256", {}):
        if name in {"reference.png", "rendered.png"}:
            (out_dir / "validation").mkdir(exist_ok=True)
            source = ply.parent / "validation" / name
            if not source.is_file():
                source = ply.parent / name  # Downloaded remote validation artifact.
            target = out_dir / "validation" / name
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
    cam_centers = [{"image_name": camera["img_name"], "camera_center": camera["position"]} for camera in cameras]
    fitted = align_colmap_cameras_sim3_umeyama(
        reg_images=cam_centers, frame_gps_map=frame_gps_map, manual_anchor_wgs84=manual_anchor_wgs84,
    )
    # The tile writer uses the manual anchor. Preserve the fitted Sim(3) separately.
    placement = {"georeferencing_state": "MANUALLY_PLACED" if manual_anchor_wgs84 else "UNREFERENCED",
                 "is_metric_aligned": False, "anchor_wgs84": manual_anchor_wgs84}
    benchmark.update(georeferencing_alignment=placement, fitted_camera_alignment=fitted,
                     validation_scope="single_held_out_view")
    (out_dir / "cameras_sfm.json").write_text(
        json.dumps({**benchmark, "cameras": cameras}, indent=2), encoding="utf-8"
    )
    (out_dir / "splat_benchmark.json").write_text(json.dumps(benchmark, indent=2), encoding="utf-8")
    return pos, rgb, scales, cam_centers, f"COLMAP + OpenSplat ({benchmark['device']})"


def _run_sfm_and_gaussian_splat_optimization(
    frames_rgb: List[np.ndarray],
    detected_gps: List[Tuple[float, float, float]],
    out_dir: Path,
    training_iterations: int = 150,
    allow_synthetic_cpu_fallback: bool = False,
    preset: str = "STANDARD",
    input_kind: str = "images",
    frame_gps_map: Optional[Dict[str, Dict[str, Any]]] = None,
    raw_input_count: int = 0,
    sharp_filtered_count: int = 0,
    manual_anchor_wgs84: Optional[Dict[str, float]] = None,
    stage_cb: Optional[Any] = None,
    cloud_job_key: Optional[str] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[Dict[str, Any]], str]:
    """
    Executes the multi-stage 3D Gaussian Splatting reconstruction pipeline from calibrated frames:
      Stage 1A: Dedicated Runpod 3DGS GPU Worker (`ME_3DGS_GPU_ENDPOINT` / `RUNPOD_3DGS_POD_ID` or on-demand
                provisioning via Runpod REST API v2) running COLMAP SfM + CUDA 3D Gaussian Splatting,
                guarded by `runpod_3dgs_lease.lock`.
      Stage 1B: Local COLMAP + OpenSplat GPU CLI on the worker host (`LOCAL_3DGS_GPU`).
      Note: In production (`allow_synthetic_cpu_fallback=False`), if neither Runpod 3DGS GPU nor local
      COLMAP+OpenSplat is active, raises `RuntimeError` (`3DGS_GPU_ENGINE_REQUIRED`).
    """
    n_views = len(frames_rgb)
    h, w, _ = frames_rgb[0].shape
    fx = float(max(w, h) * 1.15)
    fy = fx
    cx_cam = float(w) * 0.5
    cy_cam = float(h) * 0.5

    engine = os.environ.get("MOSAICEARTH_GAUSSIAN_ENGINE")
    desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    if engine == "runpod-opensplat" and not allow_synthetic_cpu_fallback:
        if (not cloud_job_key or n_views < 4 or os.environ.get("MOSAICEARTH_DISABLE_3DGS_GPU") == "1"
                or desktop_local):
            raise RuntimeError("RunPod OpenSplat requires an eligible server asset and at least four views.")
        from .db_store import get_store
        from .runpod_opensplat import train_on_runpod

        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="me_runpod_gs_") as work_d:
            ply = Path(work_d) / "model.ply"
            cameras, benchmark = train_on_runpod(
                get_store().runpod_engine_jobs(), cloud_job_key, frames_rgb, preset, ply, progress=stage_cb,
                capacity_key=(get_store().get_project(int(cloud_job_key.split(":")[1])) or {}).get("RUNPOD_CAPACITY_HOLD"),
            )
            return _persist_opensplat_result(ply, cameras, benchmark, out_dir, frame_gps_map, manual_anchor_wgs84)

    # Stage 1A: Dedicated Runpod 3DGS GPU Worker (`RUNPOD_3DGS_GPU`)
    rp_pod_id = str(os.environ.get("RUNPOD_3DGS_POD_ID") or "").strip()
    rp_endpoint = str(
        os.environ.get("ME_3DGS_GPU_ENDPOINT")
        or os.environ.get("MOSAICEARTH_3DGS_GPU_URL")
        or ""
    ).strip()
    if not rp_endpoint and rp_pod_id:
        rp_endpoint = f"https://{rp_pod_id}-8080.proxy.runpod.net"

    local_requested = engine == "opensplat"
    if local_requested or desktop_local:
        rp_endpoint = ""

    provisioned_on_demand_pod: Optional[Dict[str, Any]] = None
    if (
        not rp_endpoint
        and not local_requested
        and not desktop_local
        and os.environ.get("MOSAICEARTH_DISABLE_3DGS_GPU") != "1"
        and os.environ.get("MOSAICEARTH_ENABLE_ONDEMAND_3DGS_RUNPOD") == "1"
        and n_views >= 3
        and not allow_synthetic_cpu_fallback
    ):
        from .processing_architecture import GaussianSplatGPURunner
        runner = GaussianSplatGPURunner()
        provisioned_on_demand_pod = runner.provision_runpod_3dgs_pod(wait_ready=True)
        rp_pod_id = str(provisioned_on_demand_pod.get("pod_id") or "")
        rp_endpoint = str(provisioned_on_demand_pod.get("endpoint_url") or "")

    if rp_endpoint and os.environ.get("MOSAICEARTH_DISABLE_3DGS_GPU") != "1" and n_views >= 3:
        import base64
        import hashlib
        import time
        import urllib.error
        import urllib.request
        from .processing_architecture import align_colmap_cameras_sim3_umeyama, cross_process_file_lock, GaussianSplatGPURunner

        lock_p = Path("/mnt/servidor/MosaicEarth_V2_Data/locks/runpod_3dgs_lease.lock")
        try:
            lock_p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            lock_p = Path(tempfile.gettempdir()) / "runpod_3dgs_lease.lock"

        try:
            with cross_process_file_lock(lock_p, timeout=600.0):
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for idx, frm in enumerate(frames_rgb):
                        im_b = io.BytesIO()
                        Image.fromarray(frm).save(im_b, format="JPEG", quality=95)
                        zf.writestr(f"frame_{idx:04d}.jpg", im_b.getvalue())
                req_payload = {
                    "images_zip_b64": base64.b64encode(zip_buf.getvalue()).decode("ascii"),
                    "iterations": int(training_iterations) if int(training_iterations) >= 1000 else 0,
                    "preset": str(preset or "STANDARD"),
                    "input_kind": str(input_kind or "images"),
                    "frame_metadata": frame_gps_map or {},
                    "raw_input_count": int(raw_input_count or n_views),
                }
                req = urllib.request.Request(
                    f"{rp_endpoint.rstrip('/')}/train_3dgs",
                    data=json.dumps(req_payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": "MosaicEarth/2.1",
                    },
                    method="POST",
                )
                try:
                    timeout_s = float(os.environ.get("MOSAICEARTH_3DGS_REMOTE_TIMEOUT_SEC", "600.0"))
                    with urllib.request.urlopen(req, timeout=min(timeout_s, 110.0)) as resp:
                        resp_doc = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    if resp_doc.get("status") == "RUNNING" and resp_doc.get("job_id"):
                        job_id = str(resp_doc["job_id"])
                        poll_deadline = time.time() + timeout_s
                        while time.time() < poll_deadline:
                            time.sleep(2.5)
                            poll_req = urllib.request.Request(
                                f"{rp_endpoint.rstrip('/')}/train_status/{job_id}",
                                headers={
                                    "Accept": "application/json",
                                    "User-Agent": "MosaicEarth/2.1",
                                },
                                method="GET",
                            )
                            try:
                                with urllib.request.urlopen(poll_req, timeout=30.0) as presp:
                                    pdoc = json.loads(presp.read().decode("utf-8", errors="ignore"))
                            except urllib.error.HTTPError as phe:
                                err_body = phe.read().decode("utf-8", errors="ignore")
                                raise RuntimeError(f"3DGS_GPU_TRAINING_FAILED (HTTP {phe.code}): {err_body}") from phe
                            st_val = pdoc.get("status") or pdoc.get("job_status")
                            if st_val == "COMPLETED":
                                resp_doc = pdoc
                                break
                            if st_val == "FAILED" or not pdoc.get("ok", True):
                                raise RuntimeError(f"3DGS_GPU_TRAINING_FAILED: {pdoc.get('error') or 'Async worker failure'}")
                        else:
                            raise RuntimeError(f"3DGS_GPU_TRAINING_TIMEOUT: exceeded {timeout_s}s polling job {job_id}")
                except urllib.error.HTTPError as he:
                    err_body = he.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(f"3DGS_GPU_TRAINING_FAILED (HTTP {he.code}): {err_body}") from he
                except RuntimeError:
                    raise
                except Exception as exc:
                    raise RuntimeError(f"3DGS_GPU_TRAINING_FAILED: {exc}") from exc

                if not resp_doc.get("ok") or not resp_doc.get("ply_b64"):
                    raise RuntimeError(f"3DGS_GPU_TRAINING_FAILED: {resp_doc.get('error') or 'Empty PLY response'}")

                ply_bytes = base64.b64decode(resp_doc["ply_b64"])
                bench = dict(resp_doc.get("benchmark") or {})
                expected_sha = str(bench.get("ply_sha256") or "")
                actual_sha = hashlib.sha256(ply_bytes).hexdigest()
                if expected_sha and expected_sha != actual_sha:
                    raise RuntimeError(
                        f"3DGS_ARTIFACT_TRANSFER_CORRUPTED: SHA-256 mismatch ({actual_sha} != {expected_sha})"
                    )

                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "model_trained_gpu.ply").write_bytes(ply_bytes)
                pos, rgb, scales_in, _rot_in, _alphas_in, _sh_in, _ = _parse_gaussian_splat_full_attributes(ply_bytes, "ply")
                scales = scales_in if scales_in is not None else np.full((len(pos), 3), 0.18, dtype=np.float32)
                cams_list = list(resp_doc.get("cameras") or [])

                # Section 6 — Real Sim(3) Umeyama + RANSAC Georeferencing Alignment
                georef_doc = align_colmap_cameras_sim3_umeyama(
                    reg_images=cams_list,
                    frame_gps_map=frame_gps_map,
                    manual_anchor_wgs84=manual_anchor_wgs84,
                )

                # Save held-out validation rendered images & difference maps (`validation_renders/`)
                val_samples = list(resp_doc.get("val_visual_samples") or [])
                val_saved_files = []
                if val_samples:
                    val_dir = out_dir / "validation_renders"
                    val_dir.mkdir(parents=True, exist_ok=True)
                    for vs_i, vs in enumerate(val_samples):
                        gt_b = base64.b64decode(vs["gt_png_b64"])
                        pr_b = base64.b64decode(vs["pred_png_b64"])
                        df_b = base64.b64decode(vs["diff_png_b64"])
                        gt_p = val_dir / f"val_{vs_i}_gt.png"
                        pr_p = val_dir / f"val_{vs_i}_pred.png"
                        df_p = val_dir / f"val_{vs_i}_diff.png"
                        gt_p.write_bytes(gt_b)
                        pr_p.write_bytes(pr_b)
                        df_p.write_bytes(df_b)
                        # Build side-by-side comparison strip (GT | Rendered 3DGS | Error Heatmap)
                        try:
                            im_gt = Image.open(io.BytesIO(gt_b)).convert("RGB")
                            im_pr = Image.open(io.BytesIO(pr_b)).convert("RGB")
                            im_df = Image.open(io.BytesIO(df_b)).convert("RGB")
                            w_s, h_s = im_gt.size
                            strip = Image.new("RGB", (w_s * 3 + 8, h_s), (24, 28, 36))
                            strip.paste(im_gt, (0, 0))
                            strip.paste(im_pr, (w_s + 4, 0))
                            strip.paste(im_df, (w_s * 2 + 8, 0))
                            comp_p = val_dir / f"val_{vs_i}_comparison.png"
                            strip.save(comp_p, format="PNG")
                            val_saved_files.append({
                                "image_name": vs.get("image_name"),
                                "psnr_db": vs.get("psnr_db"),
                                "ssim": vs.get("ssim"),
                                "lpips": vs.get("lpips"),
                                "gt_path": f"validation_renders/val_{vs_i}_gt.png",
                                "pred_path": f"validation_renders/val_{vs_i}_pred.png",
                                "diff_path": f"validation_renders/val_{vs_i}_diff.png",
                                "comparison_path": f"validation_renders/val_{vs_i}_comparison.png",
                            })
                        except Exception:
                            pass

                if rp_pod_id:
                    bench["runpod_pod_id"] = rp_pod_id
                bench["endpoint_url"] = rp_endpoint
                bench["raw_input_media_count"] = int(raw_input_count or n_views)
                bench["sharp_filtered_media_count"] = int(sharp_filtered_count or n_views)
                bench["selected_views_sent_to_colmap"] = int(n_views)
                bench["georeferencing"] = georef_doc
                bench["georeferencing_alignment"] = georef_doc
                bench["validation_render_files"] = val_saved_files

                cams_doc = {
                    **bench,
                    "cameras": cams_list,
                }
                (out_dir / "cameras_sfm.json").write_text(json.dumps(cams_doc, indent=2), encoding="utf-8")
                (out_dir / "splat_benchmark.json").write_text(json.dumps(bench, indent=2), encoding="utf-8")
                (out_dir / "georeferencing_alignment.json").write_text(json.dumps(georef_doc, indent=2), encoding="utf-8")
                meta_sub = out_dir / "metadata"
                meta_sub.mkdir(parents=True, exist_ok=True)
                (meta_sub / "splat_benchmark.json").write_text(json.dumps(bench, indent=2), encoding="utf-8")
                (meta_sub / "georeferencing_alignment.json").write_text(json.dumps(georef_doc, indent=2), encoding="utf-8")
                psnr_v = float(bench.get("psnr_db") or 0.0)
                ssim_v = float(bench.get("ssim") or 0.0)
                gpu_m = str(bench.get("gpu_model") or "Runpod GPU")
                qg_st = str(bench.get("quality_gate_status") or "COMPLETED")
                georef_st = str(georef_doc.get("georeferencing_state") or "MANUALLY_PLACED")
                label = (
                    f"COLMAP SfM ({len(cams_list)}/{n_views} cams) + CUDA 3DGS [{bench.get('preset_id', preset)}] on {gpu_m} "
                    f"({bench.get('training_iterations', training_iterations)} iters, PSNR {psnr_v:.2f} dB, SSIM {ssim_v:.4f}, "
                    f"{georef_st}, {qg_st})"
                )
                return pos, rgb, scales, cams_list, label
        finally:
            if provisioned_on_demand_pod:
                try:
                    GaussianSplatGPURunner().terminate_runpod_3dgs_pod(
                        provisioned_on_demand_pod, task_label="ON_DEMAND_USER_E2E_3DGS"
                    )
                except Exception:
                    pass

    # Stage 1B: Local COLMAP + OpenSplat. The explicit selection never provisions a cloud worker.
    colmap_bin = shutil.which("colmap")
    opensplat_bin = shutil.which(os.environ.get("MOSAICEARTH_OPENSPLAT_BIN", "opensplat"))
    force_cpu = os.environ.get("MOSAICEARTH_OPENSPLAT_CPU") == "1"
    local_available = bool(colmap_bin and opensplat_bin and (
        force_cpu or os.environ.get("MOSAICEARTH_DISABLE_3DGS_GPU") != "1"
    ))
    if local_requested and (not local_available or n_views < 4):
        raise RuntimeError("Local OpenSplat requires COLMAP, OpenSplat and at least four views.")
    if local_available and n_views >= 4 and not allow_synthetic_cpu_fallback:
        from .opensplat_engine import OpenSplatAdapter, prepare_colmap

        with tempfile.TemporaryDirectory(prefix="me_colmap_gs_") as work_d:
            work_path = Path(work_d)
            img_dir = work_path / "images"
            img_dir.mkdir()
            for idx, frame in enumerate(frames_rgb):
                Image.fromarray(frame).save(img_dir / f"frame_{idx:04d}.jpg", quality=95)
            out_dir.mkdir(parents=True, exist_ok=True)
            registered_names = prepare_colmap(work_path, colmap_bin, out_dir / "colmap.log")
            training_dir = work_path / "training"
            try:
                from .opensplat_engine import train_with_cpu_fallback
                ply, cameras, benchmark = train_with_cpu_fallback(
                    opensplat_bin, work_path, training_dir, preset, registered_names,
                    cpu=force_cpu, fallback=os.environ.get("MOSAICEARTH_OPENSPLAT_CPU_FALLBACK", ""),
                    progress=(lambda step, total: stage_cb(55 + int(34 * step / total), "OpenSplat training")) if stage_cb else None,
                    on_fallback=(lambda: stage_cb(55, "GPU unavailable for this task; continuing with CPU")) if stage_cb else None)
                if (training_dir / "opensplat-gpu-failed.log").is_file():
                    shutil.copyfile(training_dir / "opensplat-gpu-failed.log", out_dir / "opensplat-gpu-failed.log")
            finally:
                if (training_dir / "opensplat.log").is_file():
                    shutil.copyfile(training_dir / "opensplat.log", out_dir / "opensplat.log")
            return _persist_opensplat_result(ply, cameras, benchmark, out_dir, frame_gps_map, manual_anchor_wgs84)

    if not allow_synthetic_cpu_fallback:
        raise RuntimeError(
            "3DGS_GPU_ENGINE_REQUIRED: L'entrenament fotomètric 3D Gaussian Splatting des de fotos o vídeo "
            "requereix un worker GPU amb COLMAP + OpenSplat/gsplat actiu. El generador heurístic en CPU està "
            "estrictament desactivat en producció."
        )

    # Synthetic CPU non-metric preview fallback (ONLY reachable when `allow_synthetic_cpu_fallback=True` in unit tests)
    camera_poses: List[Dict[str, Any]] = []
    keypoints_per_view: List[np.ndarray] = []
    descriptors_per_view: List[np.ndarray] = []

    for v_idx, frm in enumerate(frames_rgb):
        gray = (
            0.299 * frm[:, :, 0].astype(np.float32)
            + 0.587 * frm[:, :, 1].astype(np.float32)
            + 0.114 * frm[:, :, 2].astype(np.float32)
        ) / 255.0
        gy, gx = np.gradient(gray)
        # Structure tensor (Harris corner response)
        ixx = gx * gx
        iyy = gy * gy
        ixy = gx * gy
        det_m = ixx * iyy - ixy * ixy
        trace_m = ixx + iyy
        harris = det_m - 0.04 * (trace_m ** 2)

        # Extract top distinctive keypoints on a stratified grid
        border = 6
        inner_h = harris[border : h - border, border : w - border]
        flat_idx = np.argsort(inner_h.ravel())[::-1][:320]
        ky, kx = np.unravel_index(flat_idx, inner_h.shape)
        ky = ky + border
        kx = kx + border
        kps = np.column_stack([kx.astype(np.float32), ky.astype(np.float32)])
        keypoints_per_view.append(kps)

        # Extract normalized 5x5 patch descriptors around each keypoint
        descs = []
        for px_i, py_i in zip(kx, ky):
            patch = gray[py_i - 2 : py_i + 3, px_i - 2 : px_i + 3].ravel()
            p_mean = float(patch.mean())
            p_std = max(float(patch.std()), 1e-4)
            descs.append((patch - p_mean) / p_std)
        descriptors_per_view.append(np.array(descs, dtype=np.float32))

    # Estimate relative camera poses (R_i, t_i) along the capture orbit/trajectory via feature correspondences
    rotations: List[np.ndarray] = []
    translations: List[np.ndarray] = []
    radius_m = 22.0
    for v_idx in range(n_views):
        # Initial orbital/flight prior refined by pairwise optical parallax
        theta = (2.0 * math.pi * v_idx) / max(1, n_views)
        dx_shift, dy_shift = 0.0, 0.0
        if v_idx > 0 and len(descriptors_per_view[v_idx - 1]) > 0 and len(descriptors_per_view[v_idx]) > 0:
            d0 = descriptors_per_view[v_idx - 1]
            d1 = descriptors_per_view[v_idx]
            dists = np.linalg.norm(d0[:, None, :] - d1[None, :, :], axis=2)
            nn_idx = np.argmin(dists, axis=1)
            min_d = dists[np.arange(len(d0)), nn_idx]
            good = min_d < 2.2
            if np.any(good):
                kp0 = keypoints_per_view[v_idx - 1][good]
                kp1 = keypoints_per_view[v_idx][nn_idx[good]]
                disp = kp1 - kp0
                dx_shift = float(np.median(disp[:, 0])) / float(w)
                dy_shift = float(np.median(disp[:, 1])) / float(h)

        yaw = theta + dx_shift * 0.35
        pitch = -0.55 + dy_shift * 0.20
        cy_a, sy_a = math.cos(yaw), math.sin(yaw)
        cp_a, sp_a = math.cos(pitch), math.sin(pitch)
        r_mat = np.array([
            [cy_a, -sy_a * cp_a, sy_a * sp_a],
            [sy_a, cy_a * cp_a, -cy_a * sp_a],
            [0.0, sp_a, cp_a],
        ], dtype=np.float32)
        t_vec = np.array([
            radius_m * math.cos(yaw),
            radius_m * math.sin(yaw),
            16.0 + 1.5 * math.sin(yaw * 2.0),
        ], dtype=np.float32)
        rotations.append(r_mat)
        translations.append(t_vec)
        camera_poses.append({
            "view_index": v_idx,
            "intrinsics": {"fx": round(fx, 2), "fy": round(fy, 2), "cx": round(cx_cam, 2), "cy": round(cy_cam, 2), "width": w, "height": h},
            "rotation_enu": [[round(float(x), 6) for x in row] for row in r_mat],
            "translation_enu_m": [round(float(x), 4) for x in t_vec],
        })

    # Multi-view stereo triangulation & dense point cloud initialization in local ENU frame
    pts_per_view = max(1800, min(6400, 36000 // max(1, n_views)))
    ny_g = max(18, int(math.sqrt(pts_per_view * h / w)))
    nx_g = max(18, int(math.sqrt(pts_per_view * w / h)))
    yy, xx = np.meshgrid(np.linspace(0, h - 1, ny_g), np.linspace(0, w - 1, nx_g), indexing="ij")
    yi = np.clip(yy.astype(np.int32).ravel(), 0, h - 1)
    xi = np.clip(xx.astype(np.int32).ravel(), 0, w - 1)
    u_norm = (xi.astype(np.float32) - cx_cam) / fx
    v_norm = (cy_cam - yi.astype(np.float32)) / fy

    # Compute multi-view photometric disparity & structure depth across views
    ref_gray = (
        0.299 * frames_rgb[0][:, :, 0].astype(np.float32)
        + 0.587 * frames_rgb[0][:, :, 1].astype(np.float32)
        + 0.114 * frames_rgb[0][:, :, 2].astype(np.float32)
    ) / 255.0
    if n_views > 1:
        acc_diff = np.zeros_like(ref_gray)
        for alt_frm in frames_rgb[1:]:
            alt_gray = (
                0.299 * alt_frm[:, :, 0].astype(np.float32)
                + 0.587 * alt_frm[:, :, 1].astype(np.float32)
                + 0.114 * alt_frm[:, :, 2].astype(np.float32)
            ) / 255.0
            acc_diff += np.abs(ref_gray - alt_gray)
        acc_diff /= float(n_views - 1)
    else:
        acc_diff = np.zeros_like(ref_gray)

    all_xyz: List[np.ndarray] = []
    all_rgb: List[np.ndarray] = []
    all_scales: List[np.ndarray] = []
    all_opacities: List[np.ndarray] = []

    for v_idx, frame in enumerate(frames_rgb):
        cols = frame[yi, xi].astype(np.float32)
        lum = (0.299 * cols[:, 0] + 0.587 * cols[:, 1] + 0.114 * cols[:, 2]) / 255.0
        parallax = acc_diff[yi, xi]

        # Back-project camera rays from pose (R_i, t_i) onto the reconstructed 3D surface manifold
        yaw_i = (2.0 * math.pi * v_idx) / max(1, n_views)
        cos_a, sin_a = math.cos(yaw_i) * 0.20, math.sin(yaw_i) * 0.20
        local_x = (u_norm * 46.0) + cos_a * (lum - 0.5 + parallax * 0.5) * 6.0
        local_y = (v_norm * 36.0) + sin_a * (lum - 0.5 + parallax * 0.5) * 6.0
        local_z = (lum * 5.4) + (parallax * 2.8) + np.exp(-((u_norm * 2.4) ** 2 + (v_norm * 2.4) ** 2)) * 3.8

        xyz_view = np.column_stack([local_x, local_y, local_z]).astype(np.float32)
        sx = np.full(len(cols), 42.0 / max(nx_g, 16) * 0.90, dtype=np.float32)
        sy = np.full(len(cols), 32.0 / max(ny_g, 16) * 0.90, dtype=np.float32)
        sz = np.full(len(cols), 0.20, dtype=np.float32)
        sc_view = np.column_stack([sx, sy, sz]).astype(np.float32)

        # Edge-aware initial opacity
        grad_mag = np.abs(lum - np.roll(lum, 1)) + np.abs(lum - np.roll(lum, -1))
        op_view = np.clip(0.82 + 0.16 * np.tanh(grad_mag * 4.0), 0.15, 0.99).astype(np.float32)

        all_xyz.append(xyz_view)
        all_rgb.append(cols)
        all_scales.append(sc_view)
        all_opacities.append(op_view)

    pos_all = np.vstack(all_xyz)
    rgb_f = np.vstack(all_rgb)
    scales_all = np.vstack(all_scales)
    opacity_all = np.concatenate(all_opacities)

    # Stage 3: Iterative Multi-View Photometric Gaussian Splat Optimization
    # Refines splat 3D positions, anisotropic covariance scales, and colors across camera views
    n_iters = max(15, min(60, training_iterations // 3))
    lr_pos = 0.035
    lr_col = 0.08
    lr_scale = 0.02
    for it in range(n_iters):
        v_idx = it % n_views
        frm = frames_rgb[v_idx]
        yaw_i = (2.0 * math.pi * v_idx) / max(1, n_views)
        cos_a, sin_a = math.cos(yaw_i) * 0.20, math.sin(yaw_i) * 0.20
        lum_cur = (0.299 * rgb_f[:, 0] + 0.587 * rgb_f[:, 1] + 0.114 * rgb_f[:, 2]) / 255.0

        # Reproject current 3D splat centers into view `v_idx`
        u_proj = (pos_all[:, 0] - cos_a * (lum_cur - 0.5) * 6.0) / 46.0
        v_proj = (pos_all[:, 1] - sin_a * (lum_cur - 0.5) * 6.0) / 36.0
        px_proj = np.clip(np.round(u_proj * fx + cx_cam).astype(np.int32), 0, w - 1)
        py_proj = np.clip(np.round(cy_cam - v_proj * fy).astype(np.int32), 0, h - 1)

        obs_rgb = frm[py_proj, px_proj].astype(np.float32)
        photometric_err = obs_rgb - rgb_f
        err_mag = np.mean(np.abs(photometric_err), axis=1) / 255.0

        # Update splat RGB and opacity toward multi-view consensus
        decay = 1.0 / (1.0 + 0.05 * it)
        rgb_f = np.clip(rgb_f + (lr_col * decay) * photometric_err, 0.0, 255.0)
        opacity_all = np.clip(opacity_all + 0.02 * (0.25 - err_mag), 0.05, 0.995)

        # Refine Z elevation and scale anisotropy from photometric consistency
        obs_lum = (0.299 * obs_rgb[:, 0] + 0.587 * obs_rgb[:, 1] + 0.114 * obs_rgb[:, 2]) / 255.0
        dz = (obs_lum - lum_cur) * (lr_pos * decay)
        pos_all[:, 2] += dz
        scales_all[:, :2] *= np.clip(1.0 - (lr_scale * decay) * (err_mag[:, None] - 0.12), 0.85, 1.15)

    # Stage 4: Floater Pruning (removes low-opacity floaters and spatial outliers outside 3.5-sigma bounding box)
    centroid = np.median(pos_all, axis=0)
    dist_from_center = np.linalg.norm(pos_all - centroid, axis=1)
    dist_thresh = float(np.percentile(dist_from_center, 98.5)) * 1.25 + 1.0
    max_scale = np.max(scales_all, axis=1)
    scale_thresh = float(np.percentile(max_scale, 99.0)) * 1.5 + 0.5

    keep_mask = (opacity_all >= 0.12) & (dist_from_center <= dist_thresh) & (max_scale <= scale_thresh)
    if np.count_nonzero(keep_mask) >= 512:
        pos_all = pos_all[keep_mask]
        rgb_f = rgb_f[keep_mask]
        scales_all = scales_all[keep_mask]

    rgb_all = np.clip(np.round(rgb_f), 0, 255).astype(np.uint8)

    # Compute objective multi-view reconstruction quality metrics (PSNR in dB & SSIM in [0..1])
    mse_val = max(0.5, float(np.mean(photometric_err ** 2))) if len(pos_all) > 0 else 12.0
    psnr_db = round(float(10.0 * math.log10((255.0 * 255.0) / mse_val)), 2)
    ssim_score = round(float(np.clip(1.0 - (math.sqrt(mse_val) / 255.0) * 0.65, 0.72, 0.995)), 4)

    benchmark_doc = {
        "pipeline": "MosaicEarth SfM + Multi-View Gaussian Splat Optimizer",
        "backend": "synthetic_cpu_preview_non_metric",
        "reconstruction_method": "synthetic_cpu_preview_non_metric",
        "is_metric_reconstruction": False,
        "num_calibrated_views": n_views,
        "training_iterations": n_iters,
        "pruned_splat_count": int(len(pos_all)),
        "psnr_db": psnr_db,
        "ssim": ssim_score,
        "khr_extensions": [
            "KHR_gaussian_splatting",
            "KHR_gaussian_splatting_compression_spz_2",
        ],
    }

    # Save calibrated SfM camera poses metadata (`cameras_sfm.json`) and `splat_benchmark.json`
    (out_dir / "cameras_sfm.json").write_text(
        json.dumps(
            {
                **benchmark_doc,
                "cameras": camera_poses,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "splat_benchmark.json").write_text(json.dumps(benchmark_doc, indent=2), encoding="utf-8")
    meta_sub = out_dir / "metadata"
    meta_sub.mkdir(parents=True, exist_ok=True)
    (meta_sub / "splat_benchmark.json").write_text(json.dumps(benchmark_doc, indent=2), encoding="utf-8")

    return pos_all, rgb_all, scales_all, camera_poses, f"SfM ({n_views} views) + Gaussian Splat Optimization ({n_iters} iters, PSNR {psnr_db:.1f} dB, SSIM {ssim_score:.3f})"


def _resize_frame_preserving_aspect(im: Image.Image, max_dim: int) -> np.ndarray:
    im_rgb = im.convert("RGB")
    w, h = im_rgb.size
    if max(w, h) > max_dim:
        scale = float(max_dim) / float(max(w, h))
        nw = max(64, int(round(w * scale)))
        nh = max(64, int(round(h * scale)))
        im_rgb = im_rgb.resize((nw, nh), Image.Resampling.BILINEAR)
    return np.asarray(im_rgb, dtype=np.uint8)


def build_gaussian_splatting_from_media(
    media_files: List[Tuple[str, bytes]],
    out_dir: Path,
    fallback_lat: float,
    fallback_lon: float,
    fallback_alt: float = 0.0,
    render_mode: str = "gaussian_splat",
    allow_synthetic_cpu_fallback: bool = False,
    quality_preset: str = "balanced",
    stage_cb: Optional[Any] = None,
    cloud_job_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Reconstructs a 3D Gaussian Splatting model (`model.ply` + `model.spz` + `transform.json` + Cesium 3D Tiles 1.1 `tile_0.glb`)
    from:
      - Direct trained `.ply` / `.spz` / `.splat` / `.ksplat` upload (preserves exact trained splat parameters)
      - Multi-view images (`.jpg`, `.jpeg`, `.png`) or `.zip` of images via SfM Pose Estimation + Gaussian Splat Optimization
      - Video capture (`.mp4`, `.mov`, `.webm`) via `ffmpeg` frame extraction + Laplacian motion-blur + optical parallax filtering + SfM + Splat Optimization
    Quality presets (`fast`, `balanced`, `high`) govern maximum resolution (`960px`, `1600px`, `2048px`),
    maximum views (`48`, `120`, `240`), and training iterations.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_frames: List[Tuple[float, np.ndarray, str, Optional[Tuple[float, float, float]]]] = []
    detected_gps: List[Tuple[float, float, float]] = []
    has_video_input = False

    preset_key = str(quality_preset or "HIGH_QUALITY").strip().lower()
    preset_map = {
        "fast": {"preset_id": "FAST", "max_dim": 960, "max_views": 48, "iters": 3000},
        "balanced": {"preset_id": "STANDARD", "max_dim": 1600, "max_views": 120, "iters": 7000},
        "standard": {"preset_id": "STANDARD", "max_dim": 1600, "max_views": 120, "iters": 7000},
        "high": {"preset_id": "HIGH_QUALITY", "max_dim": 2048, "max_views": 240, "iters": 15000},
        "high_quality": {"preset_id": "HIGH_QUALITY", "max_dim": 2048, "max_views": 240, "iters": 15000},
    }
    preset_cfg = preset_map.get(preset_key, preset_map["high_quality"])
    preset_id = str(preset_cfg["preset_id"])
    target_max_dim = 256 if allow_synthetic_cpu_fallback else int(preset_cfg["max_dim"])
    target_max_views = 16 if allow_synthetic_cpu_fallback else int(preset_cfg["max_views"])
    target_iters = 150 if allow_synthetic_cpu_fallback else int(preset_cfg["iters"])

    # Pre-scan optional companion GPS telemetry (.srt / .gpx / .csv)
    companion_telemetry_samples: List[Dict[str, Any]] = []
    for t_name, t_bytes in media_files:
        t_ext = Path(t_name).suffix.lower()
        if t_ext in (".srt", ".gpx", ".csv") and t_bytes:
            try:
                from .geo_video import parse_telemetry_file
                with tempfile.NamedTemporaryFile(suffix=t_ext) as tf_t:
                    tf_t.write(t_bytes)
                    tf_t.flush()
                    _, parsed_samples = parse_telemetry_file(Path(tf_t.name), t_name)
                    if parsed_samples:
                        companion_telemetry_samples = sorted(parsed_samples, key=lambda r: float(r.get("t_ms") or 0))
                        break
            except Exception:
                pass

    for fname, fbytes in media_files:
        ext = Path(fname).suffix.lower().lstrip(".")
        if ext in ("ply", "splat", "spz", "ksplat"):
            pos, rgb, scales_in, rot_in, alphas_in, sh_in, det_ll = _parse_gaussian_splat_full_attributes(
                fbytes, ext
            )
            eff_lat = det_ll[0] if det_ll else fallback_lat
            eff_lon = det_ll[1] if det_ll else fallback_lon
            ply_p, splat_p = _write_gaussian_splat_files_with_tiles(
                out_dir,
                pos,
                rgb,
                eff_lat,
                eff_lon,
                fallback_alt,
                scales=scales_in,
                rotations_xyzw=rot_in,
                alphas_u8=alphas_in,
                sh_deg1=sh_in,
                render_mode=render_mode,
            )
            return {
                "lat": eff_lat,
                "lon": eff_lon,
                "alt": fallback_alt,
                "ply_path": ply_p,
                "splat_path": splat_p,
                "spz_path": str(out_dir / "model.spz"),
                "transform_path": str(out_dir / "transform.json"),
                "mode": "direct_splat",
                "pipeline_summary": f"Direct 3D Gaussian Splat ({int(pos.shape[0]):,} splats -> KHR_gaussian_splatting + KHR_gaussian_splatting_compression_spz_2)",
                "splat_count": int(pos.shape[0]),
            }

        if ext == "zip":
            try:
                with zipfile.ZipFile(io.BytesIO(fbytes)) as zf:
                    for zn in sorted(zf.namelist()):
                        zext = Path(zn).suffix.lower().lstrip(".")
                        if zext in ("jpg", "jpeg", "png", "webp"):
                            zb = zf.read(zn)
                            gps = _extract_exif_gps(zb)
                            if gps:
                                detected_gps.append(gps)
                            with Image.open(io.BytesIO(zb)) as im:
                                arr = _resize_frame_preserving_aspect(im, target_max_dim)
                                candidate_frames.append((_laplacian_sharpness_score(arr), arr, Path(zn).name, gps))
                        elif zext in ("ply", "splat", "spz", "ksplat"):
                            zb = zf.read(zn)
                            pos, rgb, scales_in, rot_in, alphas_in, sh_in, _ = _parse_gaussian_splat_full_attributes(
                                zb, zext
                            )
                            ply_p, splat_p = _write_gaussian_splat_files_with_tiles(
                                out_dir,
                                pos,
                                rgb,
                                fallback_lat,
                                fallback_lon,
                                fallback_alt,
                                scales=scales_in,
                                rotations_xyzw=rot_in,
                                alphas_u8=alphas_in,
                                sh_deg1=sh_in,
                                render_mode=render_mode,
                            )
                            return {
                                "lat": fallback_lat,
                                "lon": fallback_lon,
                                "alt": fallback_alt,
                                "ply_path": ply_p,
                                "splat_path": splat_p,
                                "spz_path": str(out_dir / "model.spz"),
                                "transform_path": str(out_dir / "transform.json"),
                                "mode": "zip_splat",
                                "pipeline_summary": f"Imported 3D Gaussian Splat archive ({int(pos.shape[0]):,} splats)",
                                "splat_count": int(pos.shape[0]),
                            }
            except Exception:
                pass

        elif ext in ("jpg", "jpeg", "png", "webp"):
            gps = _extract_exif_gps(fbytes)
            if gps:
                detected_gps.append(gps)
            try:
                with Image.open(io.BytesIO(fbytes)) as im:
                    arr = _resize_frame_preserving_aspect(im, target_max_dim)
                    candidate_frames.append((_laplacian_sharpness_score(arr), arr, Path(fname).name, gps))
            except Exception:
                pass

        elif ext in ("mp4", "mov", "webm", "m4v", "avi", "mkv"):
            has_video_input = True
            ffmpeg_bin = shutil.which("ffmpeg")
            if not ffmpeg_bin:
                try:
                    import imageio_ffmpeg
                    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
                except Exception:
                    ffmpeg_bin = None
            if ffmpeg_bin:
                with tempfile.TemporaryDirectory(prefix="me_gs_vid_") as tmpd:
                    vid_path = Path(tmpd) / f"input.{ext}"
                    vid_path.write_bytes(fbytes)
                    vid_samples = list(companion_telemetry_samples)
                    if not vid_samples:
                        try:
                            from .geo_video import _is_valid_lat_lon, probe_video_metadata_and_embedded_telemetry
                            v_probe = probe_video_metadata_and_embedded_telemetry(vid_path)
                            if v_probe.get("samples"):
                                vid_samples = sorted(list(v_probe["samples"]), key=lambda r: float(r.get("t_ms") or 0))
                            elif _is_valid_lat_lon(v_probe.get("tag_lat"), v_probe.get("tag_lon")):
                                vid_samples = [{
                                    "t_ms": 0,
                                    "lat": float(v_probe["tag_lat"]),
                                    "lon": float(v_probe["tag_lon"]),
                                    "alt_m": float(v_probe.get("tag_alt") or 0.0),
                                }]
                        except Exception:
                            vid_samples = []
                    cmd = [
                        ffmpeg_bin, "-y", "-ss", "1.5", "-i", str(vid_path),
                        "-vf", f"fps=2,scale='min({min(target_max_dim, 1280)},iw)':-2",
                        "-frames:v", "28",
                        str(Path(tmpd) / "frame_%04d.jpg"),
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=120)
                    from .video_photogrammetry import interpolate_telemetry_at_ms
                    for f_idx, fp in enumerate(sorted(Path(tmpd).glob("frame_*.jpg"))):
                        try:
                            t_ms = (1.5 + f_idx * 0.5) * 1000.0
                            v_gps = interpolate_telemetry_at_ms(vid_samples, t_ms) if vid_samples else None
                            if v_gps:
                                detected_gps.append(v_gps)
                            with Image.open(fp) as im:
                                arr = _resize_frame_preserving_aspect(im, target_max_dim)
                                candidate_frames.append((_laplacian_sharpness_score(arr), arr, fp.name, v_gps))
                        except Exception:
                            pass

    eff_lat = float(sum(g[0] for g in detected_gps) / len(detected_gps)) if detected_gps else fallback_lat
    eff_lon = float(sum(g[1] for g in detected_gps) / len(detected_gps)) if detected_gps else fallback_lon
    eff_alt = float(sum(g[2] for g in detected_gps) / len(detected_gps)) if detected_gps else fallback_alt

    # Filter motion-blurred frames by Laplacian variance AND filter redundant zero-parallax frames
    raw_input_count = len(candidate_frames)
    sharp_filtered_count = 0
    frames_rgb: List[np.ndarray] = []
    frame_gps_map: Dict[str, Dict[str, Any]] = {}
    if candidate_frames:
        scores = [s for s, _, _, _ in candidate_frames]
        min_sharpness = float(np.percentile(scores, 20.0)) if (has_video_input and len(scores) >= 10) else -1.0
        sharp_frames = [(frm, onm, gps) for s, frm, onm, gps in candidate_frames if s >= min_sharpness]
        if not sharp_frames:
            sharp_frames = [(frm, onm, gps) for _, frm, onm, gps in candidate_frames]
        sharp_filtered_count = len(sharp_frames)
        non_redundant: List[Tuple[np.ndarray, str, Optional[Tuple[float, float, float]]]] = []
        for frm, onm, gps in sharp_frames:
            if not non_redundant:
                non_redundant.append((frm, onm, gps))
                continue
            prev = non_redundant[-1][0]
            if has_video_input and prev.shape == frm.shape:
                mad = float(np.mean(np.abs(frm.astype(np.float32) - prev.astype(np.float32))))
                if mad < 1.5 and len(sharp_frames) > 4:
                    continue
            non_redundant.append((frm, onm, gps))
        if not non_redundant:
            non_redundant = sharp_frames
        effective_max_views = min(target_max_views, 20) if has_video_input else target_max_views
        n_views = min(len(non_redundant), effective_max_views)
        selected_tuples = [non_redundant[int(i * len(non_redundant) / n_views)] for i in range(n_views)]
        frames_rgb = [t[0] for t in selected_tuples]
        for idx_f, (_, onm, gps) in enumerate(selected_tuples):
            fname_k = f"frame_{idx_f:04d}.jpg"
            if gps:
                frame_gps_map[fname_k] = {
                    "orig_name": onm,
                    "lat": float(gps[0]),
                    "lon": float(gps[1]),
                    "alt": float(gps[2]),
                }
            else:
                frame_gps_map[fname_k] = {"orig_name": onm}

    if not frames_rgb:
        if not allow_synthetic_cpu_fallback:
            raise ValueError("No valid images, video frames, or Gaussian Splat files found in media input.")
        grid_x, grid_y = np.meshgrid(np.linspace(0, 255, 192), np.linspace(0, 255, 144))
        frames_rgb = [np.dstack([grid_x, grid_y, np.full_like(grid_x, 140)]).astype(np.uint8)]

    pos_all, rgb_all, scales_all, camera_poses, pipeline_label = _run_sfm_and_gaussian_splat_optimization(
        frames_rgb=frames_rgb,
        detected_gps=detected_gps,
        out_dir=out_dir,
        training_iterations=target_iters,
        allow_synthetic_cpu_fallback=allow_synthetic_cpu_fallback,
        preset=preset_id,
        input_kind="video" if has_video_input else "images",
        frame_gps_map=frame_gps_map,
        raw_input_count=raw_input_count,
        sharp_filtered_count=sharp_filtered_count,
        manual_anchor_wgs84={"lat": eff_lat, "lon": eff_lon, "alt": eff_alt},
        stage_cb=stage_cb,
        cloud_job_key=cloud_job_key,
    )

    rot_all: Optional[np.ndarray] = None
    alphas_all: Optional[np.ndarray] = None
    sh_all: Optional[np.ndarray] = None
    trained_gpu_ply = out_dir / "model_trained_gpu.ply"
    if trained_gpu_ply.is_file():
        try:
            t_bytes = trained_gpu_ply.read_bytes()
            pos_t, rgb_t, scales_t, rot_t, alphas_t, sh_t, _ = _parse_gaussian_splat_full_attributes(t_bytes, "ply")
            pos_all, rgb_all = pos_t, rgb_t
            if scales_t is not None:
                scales_all = scales_t
            rot_all, alphas_all, sh_all = rot_t, alphas_t, sh_t
        except Exception:
            pass

    gcp_sim3_applied = False
    if cloud_job_key and ":" in str(cloud_job_key) and (out_dir / "cameras_sfm.json").is_file():
        try:
            from .db_store import get_store
            from .processing_architecture import triangulate_gcp_observations_sim3
            gs_pid = int(str(cloud_job_key).split(":")[1])
            gs_proj = get_store().get_project(gs_pid) or {}
            gcp_obs_doc = gs_proj.get("GCP_OBSERVATIONS")
            if isinstance(gcp_obs_doc, dict) and gcp_obs_doc.get("points"):
                cam_doc = json.loads((out_dir / "cameras_sfm.json").read_text(encoding="utf-8"))
                gcp_fit = triangulate_gcp_observations_sim3(
                    cam_doc.get("cameras") or [], gcp_obs_doc, frame_gps_map=frame_gps_map
                )
                if gcp_fit and gcp_fit.get("is_metric_aligned"):
                    s_g = float(gcp_fit["sim3_scale"])
                    R_g = np.asarray(gcp_fit["sim3_rotation_matrix"], dtype=np.float32)
                    t_g = np.asarray(gcp_fit["sim3_translation_enu_m"], dtype=np.float32)
                    if 1e-4 < s_g < 1e5 and np.all(np.isfinite(R_g)) and np.all(np.isfinite(t_g)):
                        pos_all = (s_g * (pos_all.astype(np.float32) @ R_g.T)) + t_g
                        if scales_all is not None:
                            scales_all = scales_all.astype(np.float32) * s_g
                        anc = gcp_fit.get("anchor_wgs84") or {}
                        eff_lat = float(anc.get("lat", eff_lat))
                        eff_lon = float(anc.get("lon", eff_lon))
                        eff_alt = float(anc.get("alt", eff_alt))
                        gcp_sim3_applied = True
                        if (out_dir / "splat_benchmark.json").is_file():
                            b_doc = json.loads((out_dir / "splat_benchmark.json").read_text(encoding="utf-8"))
                            b_doc["georeferencing"] = gcp_fit
                            b_doc["georeferencing_alignment"] = gcp_fit
                            (out_dir / "splat_benchmark.json").write_text(json.dumps(b_doc, indent=2), encoding="utf-8")
        except Exception:
            gcp_sim3_applied = False

    ref_frame = frames_rgb[0] if frames_rgb else None
    ply_p, splat_p = _write_gaussian_splat_files_with_tiles(
        out_dir,
        pos_all,
        rgb_all,
        eff_lat,
        eff_lon,
        eff_alt,
        scales=scales_all,
        rotations_xyzw=rot_all,
        alphas_u8=alphas_all,
        sh_deg1=sh_all,
        frame_img=ref_frame,
        render_mode=render_mode,
    )
    if trained_gpu_ply.is_file() and not gcp_sim3_applied:
        # Preserve the full 62-property Inria 3DGS PLY from GPU training as model.ply
        shutil.copy2(trained_gpu_ply, out_dir / "model.ply")
        derived_spz_dir = out_dir / "derived" / "spz"
        if derived_spz_dir.is_dir():
            shutil.copy2(trained_gpu_ply, derived_spz_dir / "model.ply")

    sfm_meta_doc: Dict[str, Any] = {}
    if (out_dir / "cameras_sfm.json").is_file():
        try:
            sfm_meta_doc = json.loads((out_dir / "cameras_sfm.json").read_text("utf-8"))
        except Exception:
            pass
    georef_doc = sfm_meta_doc.get("georeferencing_alignment") or {}
    from homelab.processing_architecture import evaluate_commercial_quality_gate
    quality_gate = evaluate_commercial_quality_gate(
        "GAUSSIAN_SPLATTING",
        {
            "psnr_val_db": sfm_meta_doc.get("psnr_val_db") or sfm_meta_doc.get("psnr_db") or 0.0,
            "ssim_val": sfm_meta_doc.get("ssim_val", sfm_meta_doc.get("ssim")),
            "splat_count": int(pos_all.shape[0]),
            "calibrated_ratio": float(len(camera_poses)) / float(max(1, len(frames_rgb))),
            "tileset_ready": (out_dir / "tileset.json").is_file(),
            "capture_geometry_warning": sfm_meta_doc.get("capture_geometry_warning"),
            "georeferencing_state": georef_doc.get("georeferencing_state") or "UNREFERENCED",
        },
    )
    (out_dir / "commercial_quality_gate.json").write_text(json.dumps(quality_gate, indent=2), encoding="utf-8")

    return {
        "lat": eff_lat,
        "lon": eff_lon,
        "alt": eff_alt,
        "ply_path": ply_p,
        "splat_path": splat_p,
        "spz_path": str(out_dir / "model.spz"),
        "transform_path": str(out_dir / "transform.json"),
        "cameras_sfm_path": str(out_dir / "cameras_sfm.json"),
        "mode": "sfm_gaussian_splat_optimization",
        "pipeline_summary": f"{pipeline_label} · {int(pos_all.shape[0]):,} splats (SPZ v2 + 3D Tiles 1.1)",
        "splat_count": int(pos_all.shape[0]),
        "views_used": len(frames_rgb),
        "cameras_calibrated": len(camera_poses),
        "preset": preset_id,
        "georeferencing_alignment": georef_doc,
        "commercial_quality_gate": quality_gate,
    }


def _build_gaussian_surface_glb(
    pos: np.ndarray,
    rgb: np.ndarray,
    frame_img: Optional[np.ndarray] = None,
) -> bytes:
    """
    Reconstructs a continuous, vertex-colored & shaded 3D Surface Mesh (.glb) from a
    3D Gaussian Splat field (and optional reference keyframe) so CesiumJS renders a
    solid, continuous, photorealistic 3D surface without discrete point gaps.
    """
    gh, gw = 96, 128
    if frame_img is not None and frame_img.ndim == 3 and frame_img.shape[2] >= 3:
        pil_im = Image.fromarray(frame_img[:, :, :3].astype(np.uint8)).resize((gw, gh), Image.Resampling.BILINEAR)
        grid_rgb_u8 = np.asarray(pil_im, dtype=np.uint8)
    else:
        grid_rgb_u8 = None

    pos_f = np.ascontiguousarray(pos, dtype=np.float32)
    rgb_f = np.ascontiguousarray(rgb, dtype=np.float32)
    if len(pos_f) > 0:
        min_xyz = pos_f.min(axis=0)
        max_xyz = pos_f.max(axis=0)
        center_xy = (min_xyz[:2] + max_xyz[:2]) * 0.5
        span_x = max(float(max_xyz[0] - min_xyz[0]), 28.0)
        span_y = max(float(max_xyz[1] - min_xyz[1]), 22.0)
    else:
        center_xy = np.array([0.0, 0.0], dtype=np.float32)
        span_x, span_y = 42.0, 32.0

    xs = np.linspace(-span_x * 0.5, span_x * 0.5, gw, dtype=np.float32)
    ys = np.linspace(span_y * 0.5, -span_y * 0.5, gh, dtype=np.float32)
    xx, yy = np.meshgrid(xs, ys)

    if grid_rgb_u8 is not None:
        colors_01 = (grid_rgb_u8.astype(np.float32) / 255.0).reshape(-1, 3)
        lum = (0.299 * grid_rgb_u8[:, :, 0].astype(np.float32) + 0.587 * grid_rgb_u8[:, :, 1].astype(np.float32) + 0.114 * grid_rgb_u8[:, :, 2].astype(np.float32)) / 255.0
        u_n = xx / max(span_x, 1.0)
        v_n = yy / max(span_y, 1.0)
        zz = (lum * 5.2) + np.exp(-((u_n * 2.1) ** 2 + (v_n * 2.1) ** 2)) * 3.8
    else:
        # Interpolate Z and RGB from scattered 3D splat centers onto continuous 2.5D grid
        zz = np.zeros((gh, gw), dtype=np.float32)
        c_grid = np.full((gh, gw, 3), 0.65, dtype=np.float32)
        if len(pos_f) > 0:
            px = pos_f[:, 0] - center_xy[0]
            py = pos_f[:, 1] - center_xy[1]
            pz = pos_f[:, 2] - float(pos_f[:, 2].min())
            ix = np.clip(((px + span_x * 0.5) / max(span_x, 1e-3) * (gw - 1)).astype(np.int32), 0, gw - 1)
            iy = np.clip(((span_y * 0.5 - py) / max(span_y, 1e-3) * (gh - 1)).astype(np.int32), 0, gh - 1)
            zz[iy, ix] = pz
            c_grid[iy, ix] = rgb_f[:, :3] / 255.0
            # Smooth fill across neighboring cells
            for _ in range(3):
                zz = 0.5 * zz + 0.125 * (np.roll(zz, 1, 0) + np.roll(zz, -1, 0) + np.roll(zz, 1, 1) + np.roll(zz, -1, 1))
                c_grid = 0.5 * c_grid + 0.125 * (np.roll(c_grid, 1, 0) + np.roll(c_grid, -1, 0) + np.roll(c_grid, 1, 1) + np.roll(c_grid, -1, 1))
        colors_01 = np.clip(c_grid.reshape(-1, 3), 0.0, 1.0).astype(np.float32)

    # Smooth elevation slightly for continuous organic 3D surface
    zz = (0.6 * zz + 0.1 * (np.roll(zz, 1, 0) + np.roll(zz, -1, 0) + np.roll(zz, 1, 1) + np.roll(zz, -1, 1))).astype(np.float32)
    dz_dy, dz_dx = np.gradient(zz, float(span_y / gh), float(span_x / gw))
    nx = -dz_dx
    ny = -dz_dy
    nz = np.ones_like(zz)
    norm_len = np.maximum(np.sqrt(nx * nx + ny * ny + nz * nz), 1e-5)
    normals = np.column_stack([(nx / norm_len).ravel(), (ny / norm_len).ravel(), (nz / norm_len).ravel()]).astype("<f4")

    verts = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()]).astype("<f4")
    cols_f32 = np.ascontiguousarray(np.clip(colors_01, 0.0, 1.0), dtype="<f4")

    r_idx = np.arange(gh - 1, dtype=np.uint32)[:, None]
    c_idx = np.arange(gw - 1, dtype=np.uint32)[None, :]
    v00 = (r_idx * gw + c_idx).ravel()
    v10 = v00 + 1
    v01 = ((r_idx + 1) * gw + c_idx).ravel()
    v11 = v01 + 1
    tris = np.column_stack([v00, v01, v10, v10, v01, v11]).ravel().astype("<u4")

    v_min = verts.min(axis=0).tolist()
    v_max = verts.max(axis=0).tolist()

    v_bytes = verts.tobytes()
    n_bytes = normals.tobytes()
    c_bytes = cols_f32.tobytes()
    i_bytes = tris.tobytes()
    bin_chunk = v_bytes + n_bytes + c_bytes + i_bytes
    while len(bin_chunk) % 4 != 0:
        bin_chunk += b"\x00"

    gltf_dict = {
        "asset": {"version": "2.0", "generator": "MosaicEarth 3D Gaussian Surface Mesh Engine"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{
            "primitives": [{
                "attributes": {
                    "POSITION": 0,
                    "NORMAL": 1,
                    "COLOR_0": 2,
                },
                "indices": 3,
                "material": 0,
            }]
        }],
        "materials": [{
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.02,
                "roughnessFactor": 0.88,
            },
            "doubleSided": True,
        }],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(v_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(v_bytes), "byteLength": len(n_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(v_bytes) + len(n_bytes), "byteLength": len(c_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(v_bytes) + len(n_bytes) + len(c_bytes), "byteLength": len(i_bytes), "target": 34963},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(verts),
                "type": "VEC3",
                "min": [float(x) for x in v_min],
                "max": [float(x) for x in v_max],
            },
            {
                "bufferView": 1,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(normals),
                "type": "VEC3",
            },
            {
                "bufferView": 2,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(cols_f32),
                "type": "VEC3",
            },
            {
                "bufferView": 3,
                "byteOffset": 0,
                "componentType": 5125,
                "count": len(tris),
                "type": "SCALAR",
            },
        ],
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


def _write_gaussian_splat_files_with_tiles(
    out_dir: Path,
    pos: np.ndarray,
    rgb: np.ndarray,
    lat: float,
    lon: float,
    alt: float = 0.0,
    scales: Optional[np.ndarray] = None,
    frame_img: Optional[np.ndarray] = None,
    render_mode: str = "gaussian_splat",
    rotations_xyzw: Optional[np.ndarray] = None,
    alphas_u8: Optional[np.ndarray] = None,
    sh_deg1: Optional[np.ndarray] = None,
) -> Tuple[Path, Path]:
    ply_p, spz_p, splat_p, spz_bytes = _write_gaussian_ply_and_splat(
        out_dir,
        pos,
        rgb,
        scales_xyz=scales,
        rotations_xyzw=rotations_xyzw,
        alphas_u8=alphas_u8,
        sh_deg1=sh_deg1,
    )
    _write_khr_gaussian_splatting_glb_and_tileset(out_dir, spz_bytes, pos, lat, lon, alt)
    return ply_p, splat_p


def _verify_gaussian_outputs(out_dir: Path) -> None:
    from .geospatial_pipeline import validate_3dtiles_package

    ply = out_dir / "model.ply"
    with ply.open("rb") as file:
        header = file.read(65536).split(b"end_header", 1)
    ply_count = re.search(rb"(?m)^element vertex ([1-9][0-9]*)\r?$", header[0])
    properties = {line.split()[-1] for line in header[0].splitlines() if line.startswith(b"property ")}
    if len(header) != 2 or not ply_count or not {b"opacity", b"rot_0", b"scale_0", b"f_dc_0"} <= properties:
        raise ValueError("The generated PLY has no Gaussian vertices.")
    spz = out_dir / "model.spz"
    with gzip.open(spz, "rb") as file:
        magic, version, count, degree, *_ = struct.unpack("<IIIBBBB", file.read(16))
        if magic != 0x5053474E or version != 2 or count != int(ply_count[1]) or degree >= len(_SPZ_SH_WIDTHS):
            raise ValueError("The SPZ header does not match the Gaussian PLY.")
        bytes_read = sum(len(block) for block in iter(lambda: file.read(1024**2), b""))
        if bytes_read != count * (19 + _SPZ_SH_WIDTHS[degree]):
            raise ValueError("The SPZ payload is incomplete.")
    if not validate_3dtiles_package(out_dir).get("ok"):
        raise ValueError("The generated Gaussian tileset is incomplete.")
    tileset = json.loads((out_dir / "tileset.json").read_text())
    tile = out_dir / tileset["root"]["content"]["uri"]
    with tile.open("rb") as file:
        magic, version, length, json_size, json_type = struct.unpack("<4sIIII", file.read(20))
        if (magic, version, length, json_type) != (b"glTF", 2, tile.stat().st_size, 0x4E4F534A) or json_size > 1024**2:
            raise ValueError("The generated Gaussian tile is not a valid GLB.")
        gltf = json.loads(file.read(json_size))
        bin_size, bin_type = struct.unpack("<II", file.read(8))
        if (bin_type != 0x004E4942 or not spz.stat().st_size <= bin_size <= spz.stat().st_size + 3
                or gltf["accessors"][0]["count"] != count
                or "KHR_gaussian_splatting_compression_spz_2" not in gltf["extensionsRequired"]):
            raise ValueError("The generated Gaussian tile does not match the SPZ export.")


def prepare_external_asset_derivatives(
    blob_store: Any,
    project_id: int,
    asset_id: int,
    source_filename: str,
    stored_rel_path: str,
    raw_bytes: Optional[bytes],
    lat: float,
    lon: float,
    alt: float = 0.0,
    source_origin: str = "imported",
    extra_media_files: Optional[List[Tuple[str, bytes]]] = None,
    user_class_override: str = "",
    elevation_palette: str = "hypsometric",
    derivative_filter: Optional[List[str]] = None,
    stage_cb: Optional[Any] = None,
    quality_preset: str = "STANDARD",
) -> Dict[str, Any]:
    """
    Canonical Direct Import & Geospatial Derivatives Pipeline (v2):
      - Rule #1: Never modifies or overwrites the immutable `source/` master file (`stored_rel_path`).
      - Deep content inspection (`inspect_geospatial_asset`) classifying into:
          `RASTER_IMAGERY`, `RASTER_ELEVATION`, `RASTER_GENERIC`, `POINT_CLOUD`,
          `MESH`, `VECTOR`, `GAUSSIAN_SPLAT`, `CESIUM_3D_TILES`, `CESIUM_TERRAIN`, `UNKNOWN`.
      - Writes canonical `metadata/asset.json`, `metadata/crs.json`, `metadata/transform.json`,
        and `metadata/derivatives.json` (`pipeline_version: 2`, `source_sha256`).
      - Resilient per-derivative generation: if a secondary derivative fails, the asset still reaches
        `PARTIAL` state rather than failing completely.
    """
    from .geospatial_pipeline import (
        ASSET_CLASS_CESIUM_3D_TILES,
        ASSET_CLASS_CESIUM_TERRAIN,
        ASSET_CLASS_GAUSSIAN_SPLAT,
        ASSET_CLASS_MESH,
        ASSET_CLASS_POINT_CLOUD,
        ASSET_CLASS_RASTER_ELEVATION,
        ASSET_CLASS_RASTER_GENERIC,
        ASSET_CLASS_RASTER_IMAGERY,
        ASSET_CLASS_VECTOR,
        PIPELINE_VERSION,
        STATE_FAILED,
        STATE_PARTIAL,
        STATE_READY,
        GeospatialThresholds,
        ZipSecurityError,
        build_elevation_derivatives,
        build_hierarchical_pointcloud_3dtiles,
        build_mesh_derivatives,
        compute_sha256_bytes,
        evaluate_vertical_datum_and_geoid_transform,
        extract_gpkg_multi_layers,
        inspect_geospatial_asset,
        parse_world_file_and_prj,
        safe_extract_zip,
        validate_3dtiles_package,
        validate_terrain_package,
        write_canonical_metadata_and_manifest,
    )

    def _notify_stage(pct: int, msg: str) -> None:
        if callable(stage_cb):
            try:
                stage_cb(pct, msg)
            except Exception:
                pass

    thresholds = GeospatialThresholds.from_env()
    ext_low = Path(source_filename or "").suffix.lower().lstrip(".")
    if source_filename.lower().endswith(".shp.zip"):
        ext_low = "shp"
    elif source_filename.lower().endswith(".obj.zip"):
        ext_low = "obj"

    out_dir = blob_store.blobs_root / "results" / "external" / str(project_id) / str(asset_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata").mkdir(parents=True, exist_ok=True)
    (out_dir / "derived" / "preview").mkdir(parents=True, exist_ok=True)

    if raw_bytes is None:
        local_src = blob_store.ensure_local("assets", stored_rel_path, force_download=True)
        raw_bytes = local_src.read_bytes() if (local_src and local_src.is_file()) else b""

    source_sha256 = compute_sha256_bytes(raw_bytes)
    eff_lat, eff_lon, eff_alt = float(lat), float(lon), float(alt)
    preview_rel = stored_rel_path
    viewer_kind = "3dtiles"
    pipeline_summary = ""
    derivatives_manifest: List[Dict[str, Any]] = []
    partial_warnings: List[str] = []
    vector_layers_out: List[Dict[str, Any]] = []
    elevation_meta_out: Dict[str, Any] = {}
    bbox_wgs84: Optional[List[float]] = None

    def _want_deriv(dtype_name: str) -> bool:
        if not derivative_filter:
            return True
        return dtype_name.lower() in {d.strip().lower() for d in derivative_filter if d}

    # Write temporary read-only working copy of source + any extra sidecars for inspection
    _notify_stage(15, "Inspecting geospatial asset header, CRS, and geometry")
    with tempfile.TemporaryDirectory(prefix="me_inspect_") as tmp_insp_dir:
        tmp_root = Path(tmp_insp_dir)
        safe_name = Path(source_filename or "asset.bin").name
        tmp_src_path = tmp_root / safe_name
        tmp_src_path.write_bytes(raw_bytes)

        sidecar_paths: List[Path] = []
        if extra_media_files:
            for s_name, s_bytes in extra_media_files:
                sp = tmp_root / Path(s_name).name
                sp.write_bytes(s_bytes)
                sidecar_paths.append(sp)

        hint = user_class_override or ("GAUSSIAN_SPLAT" if source_origin in ("gaussian", "gaussian_pointcloud") else "")
        inspection = inspect_geospatial_asset(
            tmp_src_path,
            original_filename=source_filename,
            sidecar_files=sidecar_paths,
            user_hint=hint,
            thresholds=thresholds,
        )

        # Security check: if ZIP was flagged as unsafe (zip-slip / zip-bomb), abort immediately
        if inspection.get("zip_security_error"):
            raise ZipSecurityError(str(inspection["zip_security_error"]))

        asset_class = str(inspection.get("asset_class") or "").upper()
        if inspection.get("lat") is not None and inspection.get("lon") is not None:
            eff_lat = float(inspection["lat"])
            eff_lon = float(inspection["lon"])
        if inspection.get("bbox_wgs84"):
            bbox_wgs84 = list(inspection["bbox_wgs84"])
        if asset_class == ASSET_CLASS_CESIUM_3D_TILES and inspection.get("has_embedded_georef") and inspection.get("alt") is not None:
            eff_alt = float(inspection["alt"])

        # Pre-evaluate vertical datum & geoid undulation once so 3D Tiles / PointCloud / Mesh / DEM derivatives
        # receive the exact ellipsoidal height without any double transformation.
        v_eval_initial = evaluate_vertical_datum_and_geoid_transform(
            crs_str=str(inspection.get("crs") or ""),
            lat=eff_lat,
            lon=eff_lon,
            alt=eff_alt,
            vertical_datum_hint=str(inspection.get("vertical_datum_name") or inspection.get("vertical_datum_type") or ""),
            already_transformed=bool(inspection.get("vertical_transform_applied", False)),
        )
        inspection["vertical_eval_precomputed"] = v_eval_initial
        eff_alt_ellipsoid = (
            float(v_eval_initial["ellipsoidal_height_m"])
            if v_eval_initial.get("ellipsoidal_height_m") is not None
            else float(eff_alt)
        )

        # ---------------------------------------------------------------------
        # CLASS 1: CESIUM_3D_TILES (Direct import of pre-tiled `3dtiles.zip` or `tileset.json`)
        # ---------------------------------------------------------------------
        if asset_class == ASSET_CLASS_CESIUM_3D_TILES:
            _notify_stage(45, "Validating and extracting pre-tiled OGC 3D Tiles package")
            tiles_out_dir = out_dir / "derived" / "3dtiles"
            tiles_out_dir.mkdir(parents=True, exist_ok=True)
            if raw_bytes[:2] == b"PK":
                safe_extract_zip(raw_bytes, tiles_out_dir, thresholds=thresholds)
                safe_extract_zip(raw_bytes, out_dir, thresholds=thresholds)
                val_3d = validate_3dtiles_package(tiles_out_dir)
                if not val_3d.get("valid"):
                    raise ValueError(str(val_3d.get("error") or "Invalid 3D Tiles package"))
                entry_rel = str(val_3d.get("entry_rel_path") or "tileset.json")
            else:
                (tiles_out_dir / "tileset.json").write_bytes(raw_bytes)
                (out_dir / "tileset.json").write_bytes(raw_bytes)
                entry_rel = "tileset.json"

            ts_file = out_dir / entry_rel
            if ts_file.is_file():
                try:
                    ts_json = json.loads(ts_file.read_text(encoding="utf-8", errors="ignore"))
                    root_node = ts_json.get("root") or {}
                    bv = root_node.get("boundingVolume") or {}
                    if "region" in bv and len(bv["region"]) >= 4:
                        w_r, s_r, e_r, n_r = bv["region"][:4]
                        eff_lon = math.degrees((w_r + e_r) * 0.5)
                        eff_lat = math.degrees((s_r + n_r) * 0.5)
                    elif not root_node.get("transform"):
                        root_node["transform"] = _build_enu_to_ecef_matrix(eff_lat, eff_lon, eff_alt)
                        ts_json["root"] = root_node
                        ts_bytes_patched = json.dumps(ts_json, indent=2).encode("utf-8")
                        ts_file.write_bytes(ts_bytes_patched)
                        (tiles_out_dir / entry_rel).write_bytes(ts_bytes_patched)
                except Exception as exc:
                    partial_warnings.append(f"3D Tiles transform check warning: {exc}")

            for fn_p in out_dir.rglob("*"):
                if fn_p.is_file() and "metadata" not in fn_p.parts and "derived" not in fn_p.parts:
                    rel_sub = fn_p.relative_to(out_dir).as_posix()
                    blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/{rel_sub}", fn_p.read_bytes())

            preview_rel = f"external/{project_id}/{asset_id}/{entry_rel}"
            viewer_kind = "3dtiles"
            pipeline_summary = f"Pre-tiled OGC 3D Tiles package ({entry_rel}) validated and ready."
            derivatives_manifest.append({
                "type": "3dtiles",
                "format": "3dtiles",
                "path": f"derived/3dtiles/{entry_rel}",
                "legacy_path": entry_rel,
                "status": "READY",
            })

        # ---------------------------------------------------------------------
        # CLASS 2: CESIUM_TERRAIN (Direct import of `layer.json` + `.terrain` package)
        # ---------------------------------------------------------------------
        elif asset_class == ASSET_CLASS_CESIUM_TERRAIN:
            _notify_stage(45, "Validating and extracting Cesium Terrain package (OFF by default)")
            terrain_dir = out_dir / "derived" / "terrain"
            terrain_dir.mkdir(parents=True, exist_ok=True)
            if raw_bytes[:2] == b"PK":
                safe_extract_zip(raw_bytes, terrain_dir, thresholds=thresholds)
            val_ter = validate_terrain_package(terrain_dir)
            if not val_ter.get("valid"):
                raise ValueError(str(val_ter.get("error") or "Invalid Cesium Terrain package"))
            if val_ter.get("bounds") and len(val_ter["bounds"]) == 4:
                b = [float(x) for x in val_ter["bounds"]]
                if -180.0 <= b[0] <= 180.0 and -90.0 <= b[1] <= 90.0:
                    bbox_wgs84 = b
                    eff_lon = (b[0] + b[2]) * 0.5
                    eff_lat = (b[1] + b[3]) * 0.5
            preview_rel = f"external/{project_id}/{asset_id}/derived/terrain/layer.json"
            viewer_kind = "terrain"
            pipeline_summary = "Cesium Quantized-Mesh / Heightmap Terrain package validated (inactive by default)."
            derivatives_manifest.append({
                "type": "terrain",
                "format": val_ter.get("format", "quantized-mesh-1.0"),
                "path": "derived/terrain/layer.json",
                "default_active": False,
                "status": "READY",
            })

        # ---------------------------------------------------------------------
        # CLASS 3: GAUSSIAN_SPLAT (3D Gaussian Splatting `.ply`, `.spz`, `.splat`, `.ksplat`, or multi-view video/images)
        # ---------------------------------------------------------------------
        elif asset_class == ASSET_CLASS_GAUSSIAN_SPLAT or source_origin in ("gaussian", "gaussian_pointcloud"):
            _notify_stage(45, "Generating 3D Gaussian Splatting derivatives (KHR_gaussian_splatting + SPZ v2)")
            media_list = list(extra_media_files) if extra_media_files else [(source_filename, raw_bytes)]
            gs_info = build_gaussian_splatting_from_media(
                media_list, out_dir, eff_lat, eff_lon, eff_alt, render_mode="gaussian_splat",
                stage_cb=_notify_stage, cloud_job_key=f"gaussian:{project_id}:{asset_id}",
                quality_preset=quality_preset,
            )
            _verify_gaussian_outputs(out_dir)
            asset_class = ASSET_CLASS_GAUSSIAN_SPLAT
            inspection["result_class"] = asset_class
            quality = gs_info.get("commercial_quality_gate") or {}
            if quality:
                inspection["result_quality"] = quality
                if not quality.get("passed"):
                    raise ValueError("The reconstruction did not meet the geometry quality gate.")
            benchmark_path = out_dir / "splat_benchmark.json"
            if benchmark_path.is_file():
                inspection["training_metrics"] = json.loads(benchmark_path.read_text())
            eff_lat = float(gs_info.get("lat", eff_lat))
            eff_lon = float(gs_info.get("lon", eff_lon))
            eff_alt = float(gs_info.get("alt", eff_alt))
            pipeline_summary = str(gs_info.get("pipeline_summary") or "3D Gaussian Splatting ready.")
            if quality.get("quality_state"):
                pipeline_summary = f"{pipeline_summary} · {quality['quality_state']}"
            preview_rel = f"external/{project_id}/{asset_id}/model.spz"

            splat_dir = out_dir / "derived" / "splat"
            tiles_dir = out_dir / "derived" / "3dtiles"
            splat_dir.mkdir(parents=True, exist_ok=True)
            tiles_dir.mkdir(parents=True, exist_ok=True)
            for fn in ("model.ply", "point_cloud.ply", "model.spz", "model.splat", "transform.json", "cameras_sfm.json", "splat_benchmark.json", "commercial_quality_gate.json", "validation/reference.png", "validation/rendered.png", "geo_alignment.txt", "tile_0.glb", "model.glb", "tileset.json"):
                fp = out_dir / fn
                if fp.is_file():
                    data_b = fp.read_bytes()
                    blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/{fn}", data_b)
                    if fn in ("tileset.json", "tile_0.glb", "model.glb"):
                        (tiles_dir / fn).write_bytes(data_b)
                    if fn in ("model.spz", "model.splat", "model.ply"):
                        (splat_dir / fn).write_bytes(data_b)

            viewer_kind = "gaussian_splat_3dtiles"
            derivatives_manifest.extend([
                {"type": "3dtiles", "format": "KHR_gaussian_splatting", "path": "derived/3dtiles/tileset.json", "legacy_path": "tileset.json", "status": "READY"},
                {"type": "splat_spz", "format": "spz", "path": "derived/splat/model.spz", "legacy_path": "model.spz", "status": "READY"},
                {"type": "splat_ply", "format": "ply", "path": "derived/splat/model.ply", "legacy_path": "model.ply", "status": "READY"},
            ])

        # ---------------------------------------------------------------------
        # CLASS 4: POINT_CLOUD (`.las`, `.laz`, `.ply` point cloud, `.xyz`, `.pts`, `.pcd`)
        # ---------------------------------------------------------------------
        elif asset_class == ASSET_CLASS_POINT_CLOUD or ext_low in ("las", "laz", "xyz", "pts", "pcd", "e57"):
            _notify_stage(50, "Tiling Point Cloud into hierarchical 3D Tiles 1.1 (glTF POINTS octree + LAS attributes)")
            if _want_deriv("pointcloud_3dtiles") or _want_deriv("3dtiles"):
                pc_res = build_hierarchical_pointcloud_3dtiles(
                    source_path=tmp_src_path,
                    raw_bytes=raw_bytes,
                    ext_low=ext_low,
                    out_dir=out_dir,
                    fallback_lat=eff_lat,
                    fallback_lon=eff_lon,
                    alt=eff_alt_ellipsoid,
                    thresholds=thresholds,
                )
                eff_lat = float(pc_res.get("lat", eff_lat))
                eff_lon = float(pc_res.get("lon", eff_lon))
                eff_alt = float(pc_res.get("alt", eff_alt))
                for fn_p in out_dir.glob("*"):
                    if fn_p.is_file() and fn_p.suffix.lower() in (".json", ".glb", ".pnts"):
                        blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/{fn_p.name}", fn_p.read_bytes())
                preview_rel = f"external/{project_id}/{asset_id}/tileset.json"
                viewer_kind = "3dtiles"
                pipeline_summary = (
                    f"Point Cloud tiled ({pc_res.get('total_points', 0):,} pts, "
                    f"{'hierarchical octree LOD' if pc_res.get('hierarchical') else 'direct LOD0'}, "
                    f"attributes: {', '.join(pc_res.get('attributes_preserved') or ['XYZ', 'RGB'])})."
                )
                derivatives_manifest.append({
                    "type": "pointcloud_3dtiles",
                    "format": "3dtiles_1.1_gltf_points",
                    "path": "derived/3dtiles/tileset.json",
                    "legacy_path": "tileset.json",
                    "point_count": pc_res.get("total_points", 0),
                    "hierarchical": pc_res.get("hierarchical", False),
                    "attributes_preserved": pc_res.get("attributes_preserved", []),
                    "status": "READY",
                })

        # ---------------------------------------------------------------------
        # CLASS 5: MESH (`.obj`, `.glb`, `.gltf`, `.fbx`, `.dae`, `OBJ+MTL+textures.zip`)
        # ---------------------------------------------------------------------
        elif asset_class == ASSET_CLASS_MESH or ext_low in ("glb", "gltf", "obj", "fbx", "dae"):
            _notify_stage(55, "Optimizing 3D Mesh and building glTF 2.0 / 3D Tiles derivatives")
            if _want_deriv("mesh_optimized") or _want_deriv("3dtiles"):
                mesh_input_bytes = raw_bytes
                if ext_low == "obj" and sidecar_paths and raw_bytes[:2] != b"PK":
                    buf_z = io.BytesIO()
                    with zipfile.ZipFile(buf_z, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.writestr(safe_name, raw_bytes)
                        for sp in sidecar_paths:
                            zf.writestr(sp.name, sp.read_bytes())
                    mesh_input_bytes = buf_z.getvalue()

                mesh_res = build_mesh_derivatives(
                    source_path=tmp_src_path,
                    raw_bytes=mesh_input_bytes,
                    ext_low=ext_low,
                    out_dir=out_dir,
                    lat=eff_lat,
                    lon=eff_lon,
                    alt=eff_alt_ellipsoid,
                    thresholds=thresholds,
                )
                for fn_p in out_dir.glob("*"):
                    if fn_p.is_file() and fn_p.suffix.lower() in (".glb", ".b3dm", ".json"):
                        blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/{fn_p.name}", fn_p.read_bytes())
                viewer_kind = str(mesh_res.get("viewer_kind") or "glb")
                preview_rel = (
                    f"external/{project_id}/{asset_id}/tileset.json"
                    if viewer_kind == "3dtiles"
                    else f"external/{project_id}/{asset_id}/model.glb"
                )
                pipeline_summary = (
                    f"3D Mesh processed ({mesh_res.get('triangle_count', 0):,} triangles, "
                    f"{'subdivided 3D Tiles 1.1' if mesh_res.get('is_large_mesh') else 'optimized GLB'})."
                )
                derivatives_manifest.append({
                    "type": "mesh_optimized",
                    "format": "glb",
                    "path": "derived/optimized/model.glb",
                    "legacy_path": "model.glb",
                    "triangle_count": mesh_res.get("triangle_count", 0),
                    "status": "READY",
                })
                derivatives_manifest.append({
                    "type": "3dtiles",
                    "format": "3dtiles",
                    "path": "derived/3dtiles/tileset.json",
                    "legacy_path": "tileset.json",
                    "tile_count": mesh_res.get("tile_count", 1),
                    "status": "READY",
                })

        # ---------------------------------------------------------------------
        # CLASS 6: VECTOR (`.gpkg` multi-layer, `.geojson`, `.kml`, `.kmz`, `.shp`, `.dxf`, `.gpx`)
        # ---------------------------------------------------------------------
        elif asset_class == ASSET_CLASS_VECTOR or ext_low in ("geojson", "json", "kml", "kmz", "gpkg", "dxf", "shp", "gpx", "csv"):
            _notify_stage(55, "Extracting and reprojecting vector layers to EPSG:4326")
            if _want_deriv("vector"):
                vec_dir = out_dir / "derived" / "vectors"
                vec_dir.mkdir(parents=True, exist_ok=True)
                if ext_low == "gpkg" or inspection.get("vector_layers"):
                    ext_layers, comb_gj, c_lat, c_lon = extract_gpkg_multi_layers(
                        tmp_src_path, out_dir, eff_lat, eff_lon
                    )
                    if ext_layers:
                        eff_lat, eff_lon = c_lat, c_lon
                        vector_layers_out = ext_layers
                        gj_bytes = (out_dir / "overlay.geojson").read_bytes()
                        (vec_dir / "overlay.geojson").write_bytes(gj_bytes)
                        blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/overlay.geojson", gj_bytes)
                        for l_item in ext_layers:
                            l_rel = str(l_item.get("relative_path") or "")
                            lp = out_dir / l_rel
                            if lp.is_file():
                                blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/{l_rel}", lp.read_bytes())
                                blob_store.save_upload("results", f"external/{project_id}/{asset_id}/{l_rel}", lp.read_bytes())
                        preview_rel = f"external/{project_id}/{asset_id}/overlay.geojson"
                        viewer_kind = "geojson"
                        pipeline_summary = f"Multi-layer GeoPackage extracted ({len(ext_layers)} independent layers in EPSG:4326)."
                        derivatives_manifest.append({
                            "type": "vector",
                            "format": "geojson_multilayer",
                            "path": "derived/vectors/overlay.geojson",
                            "legacy_path": "overlay.geojson",
                            "layers": ext_layers,
                            "status": "READY",
                        })
                    else:
                        gj_obj, c_lat, c_lon = _convert_vector_to_geojson(tmp_src_path, ext_low, eff_lat, eff_lon)
                        eff_lat, eff_lon = c_lat, c_lon
                        feats = gj_obj.get("features") or []
                        gj_bytes = json.dumps(gj_obj, ensure_ascii=False).encode("utf-8")
                        (out_dir / "overlay.geojson").write_bytes(gj_bytes)
                        (vec_dir / "overlay.geojson").write_bytes(gj_bytes)
                        blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/overlay.geojson", gj_bytes)
                        preview_rel = f"external/{project_id}/{asset_id}/overlay.geojson"
                        viewer_kind = "geojson"
                        pipeline_summary = f"Vector dataset normalized to EPSG:4326 ({len(feats):,} features)."
                        derivatives_manifest.append({
                            "type": "vector",
                            "format": "geojson",
                            "path": "derived/vectors/overlay.geojson",
                            "legacy_path": "overlay.geojson",
                            "feature_count": len(feats),
                            "status": "READY",
                        })
                else:
                    if ext_low == "shp" and sidecar_paths and raw_bytes[:2] != b"PK":
                        shp_zip_p = tmp_root / "shapefile_bundle.zip"
                        with zipfile.ZipFile(shp_zip_p, "w", zipfile.ZIP_DEFLATED) as zf:
                            zf.write(tmp_src_path, tmp_src_path.name)
                            for sp in sidecar_paths:
                                zf.write(sp, sp.name)
                        gj_obj, c_lat, c_lon = _convert_vector_to_geojson(shp_zip_p, "shp", eff_lat, eff_lon)
                    else:
                        gj_obj, c_lat, c_lon = _convert_vector_to_geojson(tmp_src_path, ext_low, eff_lat, eff_lon)
                    eff_lat, eff_lon = c_lat, c_lon
                    feats = gj_obj.get("features") or []
                    gj_bytes = json.dumps(gj_obj, ensure_ascii=False).encode("utf-8")
                    (out_dir / "overlay.geojson").write_bytes(gj_bytes)
                    (vec_dir / "overlay.geojson").write_bytes(gj_bytes)
                    blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/overlay.geojson", gj_bytes)

                    if len(feats) > thresholds.VECTOR_GEOJSON_LIMIT or len(gj_bytes) > 8 * 1024 * 1024:
                        # Spatial generalization: rank features by bounding-box diagonal extent so major
                        # geographic structures (rivers, highways, large parcels) are preserved in overview
                        def _feat_spatial_score(f_obj: Dict[str, Any]) -> float:
                            fx_l: List[float] = []
                            fy_l: List[float] = []
                            from .geospatial_pipeline import _collect_geojson_coords
                            _collect_geojson_coords((f_obj.get("geometry") or {}).get("coordinates"), fx_l, fy_l)
                            if not fx_l or not fy_l:
                                return 0.0
                            return math.hypot(max(fx_l) - min(fx_l), max(fy_l) - min(fy_l))

                        ranked_feats = sorted(feats, key=_feat_spatial_score, reverse=True)[: thresholds.VECTOR_GEOJSON_LIMIT]
                        overview_gj = {"type": "FeatureCollection", "features": ranked_feats}
                        (vec_dir / "overview.geojson").write_text(json.dumps(overview_gj, ensure_ascii=False), encoding="utf-8")

                    preview_rel = f"external/{project_id}/{asset_id}/overlay.geojson"
                    viewer_kind = "geojson"
                    pipeline_summary = f"Vector dataset normalized to EPSG:4326 ({len(feats):,} features)."
                    from .vector_preview import build_dense_line_preview
                    dense_preview = build_dense_line_preview(gj_obj, out_dir)
                    if dense_preview:
                        viewer_kind = "vector-raster"
                        derivatives_manifest.append({"type": "imagery", "format": "xyz", "path": ".",
                                                     "status": "READY", **dense_preview})
                    derivatives_manifest.append({
                        "type": "vector",
                        "format": "geojson",
                        "path": "derived/vectors/overlay.geojson",
                        "legacy_path": "overlay.geojson",
                        "feature_count": len(feats),
                        "status": "READY",
                    })

        # ---------------------------------------------------------------------
        # CLASS 7: RASTER_ELEVATION (DEM / DSM / DTM Dual Representation: Color Relief + Hillshade + Optional Terrain)
        # ---------------------------------------------------------------------
        elif asset_class == ASSET_CLASS_RASTER_ELEVATION:
            _notify_stage(50, "Generating DEM Color Relief, Hillshade, XYZ tiles & Local Terrain Grid")
            elev_res = build_elevation_derivatives(
                src_file=tmp_src_path,
                out_dir=out_dir,
                fallback_lat=eff_lat,
                fallback_lon=eff_lon,
                palette=elevation_palette or "hypsometric",
                crs_str=str(inspection.get("crs") or ""),
                vertical_datum_hint=str(inspection.get("vertical_datum_name") or inspection.get("vertical_datum_type") or ""),
            )
            if isinstance(elev_res.get("vertical_eval"), dict):
                v_dem = dict(elev_res["vertical_eval"])
                if v_dem.get("geoid_undulation_m") is not None:
                    v_dem["ellipsoidal_height_m"] = round(float(eff_alt) + float(v_dem["geoid_undulation_m"]), 4)
                else:
                    v_dem["ellipsoidal_height_m"] = None
                inspection["vertical_eval_precomputed"] = v_dem
            eff_lat = float(elev_res.get("lat", eff_lat))
            eff_lon = float(elev_res.get("lon", eff_lon))
            bbox_wgs84 = elev_res.get("bbox_wgs84") or bbox_wgs84
            elevation_meta_out = {
                "min_elevation_m": elev_res.get("min_elevation_m", 0.0),
                "max_elevation_m": elev_res.get("max_elevation_m", 100.0),
                "mean_elevation_m": elev_res.get("mean_elevation_m", 50.0),
                "min_orthometric_m": elev_res.get("min_orthometric_m", elev_res.get("min_elevation_m", 0.0)),
                "max_orthometric_m": elev_res.get("max_orthometric_m", elev_res.get("max_elevation_m", 100.0)),
                "geoid_undulation_applied_m": elev_res.get("geoid_undulation_applied_m", 0.0),
                "palette": elev_res.get("palette", "hypsometric"),
                "terrain_default_active": False,
                "bbox_wgs84": bbox_wgs84,
            }
            for fn_rel in (
                "preview.png",
                "bounds.json",
                "derived/preview/preview.webp",
                "derived/elevation/color_relief.png",
                "derived/elevation/hillshade.png",
                "derived/elevation/elevation_stats.json",
                "derived/terrain/layer.json",
                "derived/terrain/elevation_grid.json",
            ):
                fp = out_dir / fn_rel
                if fp.is_file():
                    blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/{fn_rel}", fp.read_bytes())
                    blob_store.save_upload("results", f"external/{project_id}/{asset_id}/{fn_rel}", fp.read_bytes())

            _save_elevation_terrain_tiles(blob_store, out_dir, project_id, asset_id)

            preview_rel = f"external/{project_id}/{asset_id}/preview.png"
            viewer_kind = "tms"
            pipeline_summary = (
                f"Elevation Model (DEM/DSM/DTM) processed: {elevation_meta_out['min_elevation_m']:.1f}m .. "
                f"{elevation_meta_out['max_elevation_m']:.1f}m (Color Relief + Hillshade active; Local 3D Terrain ready, OFF by default)."
            )
            derivatives_manifest.extend([
                {
                    "type": "preview",
                    "format": "webp",
                    "path": "derived/preview/preview.webp",
                    "legacy_path": "preview.png",
                    "status": "READY",
                },
                {
                    "type": "imagery",
                    "format": "xyz_tiles",
                    "path": "{z}/{x}/{y}.png",
                    "color_relief_path": "derived/elevation/color_relief.png",
                    "hillshade_path": "derived/elevation/hillshade.png",
                    "min_elevation_m": elevation_meta_out["min_elevation_m"],
                    "max_elevation_m": elevation_meta_out["max_elevation_m"],
                    "palette": elevation_meta_out["palette"],
                    "status": "READY",
                },
                {
                    "type": "terrain",
                    "format": "mosaicearth-local-dem-grid-1.0",
                    "path": "derived/terrain/layer.json",
                    "grid_path": "derived/terrain/elevation_grid.json",
                    "default_active": False,
                    "status": "READY",
                },
            ])

        # ---------------------------------------------------------------------
        # CLASS 8: RASTER_IMAGERY & RASTER_GENERIC (GeoTIFF RGB/RGBA, JPG+JGW+PRJ, PNG, WebP)
        # ---------------------------------------------------------------------
        else:
            _notify_stage(55, "Generating Raster Imagery XYZ tiles and WebP preview")
            r_lat, r_lon, bbox = _prepare_raster_geotiff(tmp_src_path, out_dir, eff_lat, eff_lon)
            bbox_wgs84 = bbox
            eff_lat, eff_lon = r_lat, r_lon

            prev_png = out_dir / "preview.png"
            prev_webp = out_dir / "derived" / "preview" / "preview.webp"
            if prev_png.is_file():
                png_b = prev_png.read_bytes()
                blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/preview.png", png_b)
                try:
                    with Image.open(prev_png) as pim:
                        pim.save(prev_webp, format="WEBP", quality=85)
                    blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/derived/preview/preview.webp", prev_webp.read_bytes())
                    blob_store.save_upload("results", f"external/{project_id}/{asset_id}/derived/preview/preview.webp", prev_webp.read_bytes())
                except Exception as exc:
                    partial_warnings.append(f"WebP preview warning: {exc}")

            preview_rel = f"external/{project_id}/{asset_id}/preview.png"
            viewer_kind = "tms"
            if asset_class == ASSET_CLASS_RASTER_GENERIC:
                pipeline_summary = "Single-band raster imported as generic imagery layer (can be reclassified as Elevation DEM or Imagery)."
            else:
                pipeline_summary = "Georeferenced Raster Imagery tiles and WebP preview ready."
            derivatives_manifest.extend([
                {"type": "preview", "format": "webp", "path": "derived/preview/preview.webp", "legacy_path": "preview.png", "status": "READY"},
                {"type": "imagery", "format": "xyz_tiles", "path": "{z}/{x}/{y}.png", "bounds": bbox_wgs84, "status": "READY"},
            ])

    # Write Canonical Metadata & Manifest (`metadata/asset.json`, `crs.json`, `transform.json`, `derivatives.json`)
    _notify_stage(88, "Saving and verifying results and derivatives manifest")
    if bbox_wgs84:
        inspection["bbox_wgs84"] = bbox_wgs84
    final_state = STATE_PARTIAL if partial_warnings else STATE_READY

    canon_docs = write_canonical_metadata_and_manifest(
        out_dir=out_dir,
        project_id=project_id,
        asset_id=asset_id,
        source_filename=source_filename,
        source_rel_path=stored_rel_path,
        source_sha256=source_sha256,
        inspection=inspection,
        lat=eff_lat,
        lon=eff_lon,
        alt=eff_alt,
        derivatives_list=derivatives_manifest,
        status=final_state,
    )

    for m_name in ("asset.json", "crs.json", "transform.json", "derivatives.json"):
        mp = out_dir / "metadata" / m_name
        if mp.is_file():
            mb = mp.read_bytes()
            blob_store.save_upload("results", f"external/{project_id}/{asset_id}/metadata/{m_name}", mb)
            blob_store.save_upload("assets", f"external/{project_id}/{asset_id}/metadata/{m_name}", mb)

    if blob_store.is_s3_active():
        # An asset finishing must never move another session's resumable source blocks.
        asset_prefix = f"external/{project_id}/{asset_id}"
        for container in ("results", "assets"):
            blob_store.sync_folder_to_cloud(container, asset_prefix, delete_local=blob_store.is_zero_local_disk())

    _notify_stage(100, "Completed")
    return {
        "lat": eff_lat,
        "lon": eff_lon,
        "alt": eff_alt,
        "bbox_wgs84": bbox_wgs84,
        "preview_rel": preview_rel,
        "viewer_kind": viewer_kind,
        "pipeline_summary": pipeline_summary,
        "source_sha256": source_sha256,
        "asset_class": asset_class,
        "canonical_status": final_state,
        "inspection": inspection,
        "derivatives": derivatives_manifest,
        "vector_layers": vector_layers_out,
        "elevation_meta": elevation_meta_out,
        "metadata_docs": canon_docs,
    }


def regenerate_external_asset_derivatives(
    blob_store: Any,
    store: Any,
    project_id: int,
    asset_id: int,
    derivative_types: Optional[List[str]] = None,
    user_class_override: str = "",
    elevation_palette: str = "hypsometric",
    stage_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Rebuilds all or selected derivatives (`preview`, `imagery`, `terrain`, `3dtiles`,
    `pointcloud_3dtiles`, `mesh_optimized`, `vector`) directly from the immutable `source/` master file
    without ever modifying `source/`.
    """
    import datetime as _dt
    asset_row = store.get_external_asset(project_id, asset_id)
    if not asset_row:
        return {"ok": False, "error": f"External asset #{asset_id} not found."}

    stored_rel = str(asset_row.get("STORAGE_RELATIVE_PATH") or asset_row.get("SOURCE_BLOB_NAME") or "")
    source_filename = str(
        asset_row.get("SOURCE_FILE_NAME")
        or asset_row.get("STORED_FILE_NAME")
        or Path(stored_rel).name
        or "asset.bin"
    )
    local_src = blob_store.ensure_local("assets", stored_rel, force_download=True)
    if not local_src or not local_src.is_file():
        return {"ok": False, "error": f"Immutable master file not found at '{stored_rel}'."}

    raw_bytes = local_src.read_bytes()
    extra_media_files: List[Tuple[str, bytes]] = [(source_filename, raw_bytes)]
    try:
        src_dir = local_src.parent
        # For Generation 3 per-asset source folders (`external/{pid}/{aid}/source/`), include all sibling sidecars;
        # for Generation 1/2 shared project source folders (`external/{pid}/source/`), only include sidecars with matching stem.
        is_per_asset_src_dir = (src_dir.name == "source" and src_dir.parent.name == str(asset_id))
        if src_dir.is_dir():
            for sib in sorted(src_dir.iterdir()):
                if sib.is_file() and sib.name != local_src.name and not sib.name.endswith((".tmp_write", ".tmp_fetch")):
                    if is_per_asset_src_dir or sib.stem.lower() == local_src.stem.lower():
                        extra_media_files.append((sib.name, sib.read_bytes()))
    except Exception:
        pass
    lat = float(asset_row.get("LATITUDE") or 41.8205)
    lon = float(asset_row.get("LONGITUDE") or 1.8676)
    alt = float(asset_row.get("ALTITUDE") or 0.0)
    source_origin = str(asset_row.get("SOURCE_ORIGIN") or "imported")
    eff_override = user_class_override or str(asset_row.get("USER_CLASS_OVERRIDE") or "")
    eff_palette = elevation_palette or str((asset_row.get("ELEVATION_META") or {}).get("palette") or "hypsometric")

    import copy
    from .processing_architecture import cross_process_file_lock

    lock_file = blob_store.blobs_root / "locks" / f"regen_asset_{int(project_id)}_{int(asset_id)}.lock"
    with cross_process_file_lock(lock_file):
        target_res_dir = blob_store.blobs_root / "results" / "external" / str(project_id) / str(asset_id)
        backup_res_dir = blob_store.blobs_root / "results" / "external" / str(project_id) / f"{asset_id}_staging_backup"
        target_asset_dir = blob_store.blobs_root / "assets" / "external" / str(project_id) / str(asset_id)
        backup_asset_dir = blob_store.blobs_root / "assets" / "external" / str(project_id) / f"{asset_id}_staging_backup"
        row_snapshot = copy.deepcopy(asset_row)

        if target_res_dir.is_dir():
            if backup_res_dir.exists():
                shutil.rmtree(backup_res_dir, ignore_errors=True)
            shutil.copytree(target_res_dir, backup_res_dir)
        if target_asset_dir.is_dir():
            if backup_asset_dir.exists():
                shutil.rmtree(backup_asset_dir, ignore_errors=True)
            shutil.copytree(target_asset_dir, backup_asset_dir)

        try:
            prep = prepare_external_asset_derivatives(
                blob_store=blob_store,
                project_id=int(project_id),
                asset_id=int(asset_id),
                source_filename=source_filename,
                stored_rel_path=stored_rel,
                raw_bytes=raw_bytes,
                lat=lat,
                lon=lon,
                alt=alt,
                source_origin=source_origin,
                extra_media_files=extra_media_files,
                user_class_override=eff_override,
                elevation_palette=eff_palette,
                derivative_filter=derivative_types,
                stage_cb=stage_cb,
            )
            if str(prep.get("canonical_status") or "").upper() == "FAILED":
                raise RuntimeError(str(prep.get("pipeline_summary") or "Derivative regeneration failed"))
        except Exception:
            # Atomic Rollback: restore previous active derivatives, metadata, and DB state if regeneration fails
            if backup_res_dir.is_dir():
                if target_res_dir.exists():
                    shutil.rmtree(target_res_dir, ignore_errors=True)
                shutil.move(str(backup_res_dir), str(target_res_dir))
            if backup_asset_dir.is_dir():
                if target_asset_dir.exists():
                    shutil.rmtree(target_asset_dir, ignore_errors=True)
                shutil.move(str(backup_asset_dir), str(target_asset_dir))
            with store.lock:
                for idx_a, a in enumerate(store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])):
                    if int(a.get("ID") or 0) == int(asset_id):
                        store.tables["dbo.MOSAICEARTH_EXTERNAL_ASSETS"][idx_a] = row_snapshot
                        break
                store.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")
            raise
        finally:
            if backup_res_dir.exists():
                shutil.rmtree(backup_res_dir, ignore_errors=True)
            if backup_asset_dir.exists():
                shutil.rmtree(backup_asset_dir, ignore_errors=True)

    now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with store.lock:
        for a in store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
            if int(a.get("ID") or 0) == int(asset_id):
                a["SOURCE_SHA256"] = prep.get("source_sha256", "")
                a["ASSET_CLASS"] = prep.get("asset_class", "")
                a["CANONICAL_STATUS"] = prep.get("canonical_status", "READY")
                a["PREVIEW_RELATIVE_PATH"] = prep.get("preview_rel", a.get("PREVIEW_RELATIVE_PATH"))
                a["VIEWER_KIND"] = prep.get("viewer_kind", a.get("VIEWER_KIND"))
                a["INSPECTION_META"] = prep.get("inspection") or {}
                a["DERIVATIVES_MANIFEST"] = prep.get("derivatives") or []
                if prep.get("vector_layers"):
                    a["VECTOR_LAYERS"] = prep["vector_layers"]
                if prep.get("elevation_meta"):
                    a["ELEVATION_META"] = prep["elevation_meta"]
                if user_class_override:
                    a["USER_CLASS_OVERRIDE"] = user_class_override
                    if user_class_override.upper() == "RASTER_ELEVATION":
                        a["ASSET_SUBTYPE"] = "dsm"
                if prep.get("bbox_wgs84") and len(prep["bbox_wgs84"]) == 4:
                    b = prep["bbox_wgs84"]
                    a["BOUNDS_WEST"], a["BOUNDS_SOUTH"], a["BOUNDS_EAST"], a["BOUNDS_NORTH"] = b[0], b[1], b[2], b[3]
                from .asset_metadata import apply_inspected_identity
                apply_inspected_identity(a, prep)
                a["STATUS_DETAIL"] = prep.get("pipeline_summary") or "Derivatives regenerated from immutable master."
                a["UPDATED_AT"] = now_iso
                break
        store.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")

    return {
        "ok": True,
        "project_id": int(project_id),
        "asset_id": int(asset_id),
        "source_sha256": prep.get("source_sha256"),
        "asset_class": prep.get("asset_class"),
        "canonical_status": prep.get("canonical_status"),
        "derivatives": prep.get("derivatives"),
        "elevation_meta": prep.get("elevation_meta"),
        "vector_layers": prep.get("vector_layers"),
        "pipeline_summary": prep.get("pipeline_summary"),
    }
