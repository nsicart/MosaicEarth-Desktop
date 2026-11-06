"""Derivatives of the local OpenSplat PLY. The trained master is preserved."""
import gzip
import io
import json
import re
import struct
import zipfile
from pathlib import Path
from typing import Optional

import numpy as np

_SPZ_SH_WIDTHS = (0, 9, 24, 45)


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
) -> None:
    """Wrap the compressed Gaussian buffer in a local-coordinate 3D Tiles asset."""
    n = len(pos)
    v_min = [float(v) for v in pos.min(axis=0)]
    v_max = [float(v) for v in pos.max(axis=0)]
    center = (pos.min(axis=0) + pos.max(axis=0)) * 0.5
    half_ext = np.maximum((pos.max(axis=0) - pos.min(axis=0)) * 0.65, 0.01)
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
        "extras": {"coordinate_frame": "input_sfm"},
        "geometricError": 64.0,
        "root": {
            "boundingVolume": {
                "box": [*map(float, center), hx, 0.0, 0.0, 0.0, hy, 0.0, 0.0, 0.0, hz]
            },
            "geometricError": 0.0,
            "refine": "REPLACE",
            "content": {"uri": "tile_0.glb"},
        },
    }
    ts_path = out_dir / "tileset.json"
    ts_path.write_text(json.dumps(tileset_obj, indent=2), encoding="utf-8")



def export_gaussian(output):
    with (output / "model.ply").open("rb") as source:
        header = []
        for _ in range(128):
            line = source.readline(512).decode("ascii").strip()
            if line == "end_header":
                break
            header.append(line)
        else:
            raise ValueError("OpenSplat PLY header is invalid")
        if header[:2] != ["ply", "format binary_little_endian 1.0"]:
            raise ValueError("OpenSplat output must be a binary little-endian PLY")
        size = next((int(line.split()[2]) for line in header if line.startswith("element vertex ")), 0)
        names = [line.split()[2] for line in header if line.startswith("property float ")]
        if size <= 0 or size > 700000 or len(names) < 14:
            raise ValueError("OpenSplat PLY has an invalid vertex layout")
        arr = np.frombuffer(source.read(), dtype=np.dtype([(name, "<f4") for name in names]))
    if len(arr) != size or not all(np.isfinite(arr[name]).all() for name in names):
        raise ValueError("OpenSplat PLY has non-finite or truncated vertices")

    def columns(names):
        return np.column_stack([arr[name] for name in names])

    pos = columns(("x", "y", "z"))
    if np.abs(pos).max() >= 2048:
        raise ValueError("Local coordinates exceed the SPZ v2 fixed-point range")
    rgb = np.rint(np.clip(0.5 + 0.28209479177387814 * columns(f"f_dc_{i}" for i in range(3)), 0, 1) * 255).astype(np.uint8)
    scales = np.exp(np.clip(columns(f"scale_{i}" for i in range(3)), -20, 10))
    rotations = columns(f"rot_{i}" for i in (1, 2, 3, 0))
    norm = np.linalg.norm(rotations, axis=1, keepdims=True)
    if (norm < 1e-6).any():
        raise ValueError("OpenSplat PLY has zero quaternions")
    rotations /= norm
    alpha = np.rint((255 / (1 + np.exp(-np.clip(arr["opacity"], -30, 30))))).astype(np.uint8)
    sh_names = [name for name in names if re.fullmatch(r"f_rest_[0-9]+", name)]
    if len(sh_names) not in _SPZ_SH_WIDTHS:
        raise ValueError("OpenSplat PLY has an unsupported SH degree")
    sh = None
    if sh_names:
        sh = columns(f"f_rest_{i}" for i in range(len(sh_names))).reshape(size, 3, -1).transpose(0, 2, 1).reshape(size, -1)

    dtype = np.dtype([("xyz", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    splats = np.empty(size, dtype=dtype)
    splats["xyz"], splats["scale"] = pos, scales
    splats["rgba"][:, :3], splats["rgba"][:, 3] = rgb, alpha
    splats["rot"] = np.rint(np.clip(rotations[:, [3, 0, 1, 2]] * 0.5 + 0.5, 0, 1) * 255).astype(np.uint8)
    (output / "model.splat").write_bytes(splats.tobytes())
    spz = _write_spz_v2(output / "model.spz", pos, rgb, scales, rotations, alpha, sh)
    _write_khr_gaussian_splatting_glb_and_tileset(output, spz, pos)
    with zipfile.ZipFile(output / "gaussian-3dtiles.zip", "w") as tiles:
        for name in ("tileset.json", "tile_0.glb"):
            tiles.write(output / name, name)
