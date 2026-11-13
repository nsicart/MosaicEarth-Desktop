"""
MosaicEarth Wave 2 (Phase 4) — Native Apple `.USDZ` / `.USDA` / `.USDC` -> `.GLB` PBR Converter.

Supports LiDAR & photogrammetry captures exported from iPhone / iPad apps:
  - Scaniverse, Polycam, 3d Scanner App, Apple Object Capture, RoomPlan, Reality Composer.
  - Handles uncompressed `.usdz` archives, ASCII `.usda` stages, and Binary Crate `.usdc` stages.
  - Preserves world transforms, `metersPerUnit` (cm -> m scaling), `upAxis` (`Z` vs `Y` -> glTF `Y-up`),
    vertex/faceVarying UVs (`primvars:st`), normals, PBR `UsdPreviewSurface` diffuse/baseColor textures,
    metallic/roughness factors, and embedded customLayerData GPS coordinates if present.
"""

from __future__ import annotations

import io
import json
import re
import struct
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image

USD_EXTENSIONS = {"usdz", "usda", "usdc", "usd"}
_MAX_USDZ_UNCOMPRESSED = 768 * 1024 * 1024  # 768 MiB safety cap
_MAX_USDZ_ENTRIES = 512


def is_usd_payload(ext_low: str, head_bytes: bytes = b"") -> bool:
    ext_clean = (ext_low or "").lower().lstrip(".")
    if ext_clean in USD_EXTENSIONS:
        return True
    if head_bytes[:8] == b"PXR-USDC":
        return True
    if head_bytes.lstrip()[:6] == b"#usda ":
        return True
    return False


def validate_usd_archive_or_stage(path: Path, filename: str = "") -> Dict[str, Any]:
    """Validate `.usdz`, `.usda`, or `.usdc` file header and archive safety before compute."""
    ext = (Path(filename or path.name).suffix or "").lower().lstrip(".")
    with path.open("rb") as fh:
        head = fh.read(128)

    if ext == "usdz" or (ext in ("", "usd") and head[:4] == b"PK\x03\x04"):
        if not zipfile.is_zipfile(path):
            raise ValueError("Invalid USDZ package: not a valid ZIP archive.")
        with zipfile.ZipFile(path, "r") as zf:
            infos = [zi for zi in zf.infolist() if not zi.is_dir()]
            if not infos or len(infos) > _MAX_USDZ_ENTRIES:
                raise ValueError("USDZ archive entry count is invalid or exceeds safety limits.")
            total_sz = sum(zi.file_size for zi in infos)
            if total_sz <= 0 or total_sz > _MAX_USDZ_UNCOMPRESSED:
                raise ValueError("USDZ archive uncompressed size exceeds safety limits.")
            has_root_stage = False
            for zi in infos:
                norm_n = zi.filename.replace("\\", "/")
                if (
                    norm_n.startswith("/")
                    or ".." in norm_n.split("/")
                    or ":" in norm_n.split("/")[0]
                    or ((zi.external_attr >> 16) & 0o170000) == 0o120000
                ):
                    raise ValueError("USDZ archive contains unsafe path or symlink.")
                if Path(norm_n).suffix.lower() in (".usd", ".usda", ".usdc"):
                    has_root_stage = True
            if not has_root_stage:
                raise ValueError("USDZ archive contains no root .usd/.usda/.usdc stage.")
        return {"kind": "usdz"}

    if ext == "usdc" or head[:8] == b"PXR-USDC":
        if head[:8] != b"PXR-USDC":
            raise ValueError("Invalid USDC binary crate header.")
        return {"kind": "usdc"}

    if ext in ("usda", "usd") or head.lstrip()[:6] == b"#usda ":
        if not head.lstrip().startswith(b"#usda "):
            raise ValueError("Invalid USDA ASCII stage header.")
        return {"kind": "usda"}

    raise ValueError(f"Unsupported USD file format: {filename or path.name}")


def _extract_gps_from_dict(d: Any) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if not isinstance(d, dict):
        return None, None, None
    lat = lon = alt = None
    lower_map = {str(k).lower(): v for k, v in d.items()}
    for lk in ("latitude", "gpslatitude", "lat"):
        if lk in lower_map:
            try:
                cand = float(lower_map[lk])
                if -90.0 <= cand <= 90.0:
                    lat = cand
                    break
            except (TypeError, ValueError):
                pass
    for lk in ("longitude", "gpslongitude", "lon", "lng"):
        if lk in lower_map:
            try:
                cand = float(lower_map[lk])
                if -180.0 <= cand <= 180.0:
                    lon = cand
                    break
            except (TypeError, ValueError):
                pass
    for lk in ("altitude", "gpsaltitude", "alt", "elevation"):
        if lk in lower_map:
            try:
                cand = float(lower_map[lk])
                if -12000.0 <= cand <= 100000.0:
                    alt = cand
                    break
            except (TypeError, ValueError):
                pass
    if lat is None or lon is None:
        for sub_v in d.values():
            if isinstance(sub_v, dict):
                s_lat, s_lon, s_alt = _extract_gps_from_dict(sub_v)
                if s_lat is not None and s_lon is not None:
                    return s_lat, s_lon, s_alt
    return lat, lon, alt


def _safe_extract_usdz(raw_bytes: bytes, dest_dir: Path) -> Tuple[Path, List[Path]]:
    extracted: List[Path] = []
    root_stage: Optional[Path] = None
    with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
        for zi in zf.infolist():
            if zi.is_dir():
                continue
            rel = zi.filename.replace("\\", "/").lstrip("/")
            parts = [p for p in rel.split("/") if p and p not in (".", "..")]
            if not parts:
                continue
            out_p = dest_dir.joinpath(*parts)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_bytes(zf.read(zi.filename))
            extracted.append(out_p)
            # Per Apple USDZ spec, the FIRST stage file in the ZIP directory is the default root layer!
            if root_stage is None and out_p.suffix.lower() in (".usdc", ".usda", ".usd"):
                root_stage = out_p
    if root_stage is None:
        raise ValueError("USDZ archive contains no USD stage layer.")
    return root_stage, extracted


def _resolve_usd_material_pbr(
    mesh_prim: Any,
    stage_dir: Path,
    extracted_files: Sequence[Path],
) -> Tuple[Optional[Image.Image], Tuple[float, float, float, float], float, float]:
    """Extract `(baseColorImage, baseColorFactorRGBA, metallic, roughness)` from bound `UsdShade.Material`."""
    from pxr import Sdf, UsdShade

    base_img: Optional[Image.Image] = None
    base_rgba: Tuple[float, float, float, float] = (0.85, 0.85, 0.85, 1.0)
    metallic = 0.0
    roughness = 0.65

    try:
        mat, _ = UsdShade.MaterialBindingAPI(mesh_prim).ComputeBoundMaterial()
        if not mat:
            return None, base_rgba, metallic, roughness

        # Walk shaders inside the Material prim looking for UsdPreviewSurface and UsdUVTexture
        tex_asset_paths: List[str] = []
        for child in mat.GetPrim().GetAllChildren():
            shader = UsdShade.Shader(child)
            if not shader:
                continue
            sid = str(shader.GetIdAttr().Get() or "")
            if "PreviewSurface" in sid:
                diff_in = shader.GetInput("diffuseColor")
                if diff_in:
                    val = diff_in.Get()
                    if val is not None and hasattr(val, "__len__") and len(val) >= 3:
                        base_rgba = (float(val[0]), float(val[1]), float(val[2]), 1.0)
                met_in = shader.GetInput("metallic")
                if met_in and met_in.Get() is not None:
                    try:
                        metallic = float(np.clip(float(met_in.Get()), 0.0, 1.0))
                    except Exception:
                        pass
                rough_in = shader.GetInput("roughness")
                if rough_in and rough_in.Get() is not None:
                    try:
                        roughness = float(np.clip(float(rough_in.Get()), 0.0, 1.0))
                    except Exception:
                        pass
            elif "UVTexture" in sid or child.GetName().lower().find("tex") >= 0:
                file_in = shader.GetInput("file")
                if file_in:
                    fval = file_in.Get()
                    if isinstance(fval, Sdf.AssetPath):
                        resolved = str(fval.resolvedPath or fval.path or "").strip()
                        if resolved:
                            tex_asset_paths.append(resolved)
                    elif fval:
                        tex_asset_paths.append(str(fval).strip("@ "))

        for raw_tp in tex_asset_paths:
            clean_tp = raw_tp.strip("@ ")
            # Handle stage[subfile] USDZ syntax if present (e.g. `/tmp/a.usdz[0/tex.png]`)
            if "[" in clean_tp and clean_tp.endswith("]"):
                clean_tp = clean_tp.split("[", 1)[1][:-1]
            cand_paths = [
                Path(clean_tp),
                stage_dir / clean_tp,
                stage_dir / Path(clean_tp).name,
            ]
            for ef in extracted_files:
                if ef.name.lower() == Path(clean_tp).name.lower():
                    cand_paths.append(ef)
            for cp in cand_paths:
                if cp.is_file():
                    with Image.open(cp) as pim:
                        base_img = pim.convert("RGBA") if pim.mode in ("RGBA", "LA", "P") else pim.convert("RGB")
                        base_rgba = (1.0, 1.0, 1.0, 1.0)
                        break
            if base_img is not None:
                break
    except Exception:
        pass

    return base_img, base_rgba, metallic, roughness


def _convert_with_pxr(
    stage_path: Path,
    extracted_files: Sequence[Path],
) -> Tuple[bytes, np.ndarray, np.ndarray, Dict[str, Any]]:
    import trimesh
    from trimesh.visual.material import PBRMaterial
    from trimesh.visual.texture import TextureVisuals
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(stage_path))
    if not stage:
        raise ValueError(f"OpenUSD failed to open stage: {stage_path.name}")

    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    if not (1e-5 <= meters_per_unit <= 1000.0):
        meters_per_unit = 1.0
    up_axis = str(UsdGeom.GetStageUpAxis(stage) or "Y").upper()

    lat, lon, alt = _extract_gps_from_dict(stage.GetRootLayer().customLayerData or {})

    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    sub_meshes: List[trimesh.Trimesh] = []
    all_v_list: List[np.ndarray] = []
    all_f_list: List[np.ndarray] = []
    v_global_offset = 0
    has_textures = False
    has_normals = False

    for prim in stage.Traverse():
        if lat is None or lon is None:
            p_lat, p_lon, p_alt = _extract_gps_from_dict(prim.GetCustomData() or {})
            if p_lat is not None and p_lon is not None:
                lat, lon, alt = p_lat, p_lon, p_alt

        if not prim.IsA(UsdGeom.Mesh):
            continue

        usd_mesh = UsdGeom.Mesh(prim)
        pts_attr = usd_mesh.GetPointsAttr().Get(Usd.TimeCode.Default())
        cnts_attr = usd_mesh.GetFaceVertexCountsAttr().Get(Usd.TimeCode.Default())
        idxs_attr = usd_mesh.GetFaceVertexIndicesAttr().Get(Usd.TimeCode.Default())
        if not pts_attr or not cnts_attr or not idxs_attr:
            continue

        pts = np.asarray(pts_attr, dtype=np.float64)
        cnts = np.asarray(cnts_attr, dtype=np.int64)
        idxs = np.asarray(idxs_attr, dtype=np.int64)
        if len(pts) < 3 or len(cnts) < 1 or len(idxs) < 3:
            continue

        # Apply full world transform matrix (USD uses row-vector convention: p' = [p, 1] * M)
        gf_mat = xform_cache.GetLocalToWorldTransform(prim)
        mat4 = np.array([[gf_mat[r][c] for c in range(4)] for r in range(4)], dtype=np.float64)
        ones = np.ones((len(pts), 1), dtype=np.float64)
        pts_world = (np.hstack([pts, ones]) @ mat4)[:, :3] * meters_per_unit

        # Normalize Z-up stages to glTF 2.0 Y-up: (X, Y_north, Z_up) -> (X, Z_up, -Y_north)
        if up_axis == "Z":
            pts_world = np.column_stack([pts_world[:, 0], pts_world[:, 2], -pts_world[:, 1]])

        # Read optional UVs (`primvars:st` or `primvars:st0`)
        pv_api = UsdGeom.PrimvarsAPI(usd_mesh)
        st_pv = pv_api.GetPrimvar("st")
        if not st_pv or not st_pv.HasValue():
            st_pv = pv_api.GetPrimvar("st0")
        uv_raw = None
        uv_indices = None
        uv_interp = ""
        if st_pv and st_pv.HasValue():
            try:
                uv_vals = st_pv.Get(Usd.TimeCode.Default())
                if uv_vals is not None and len(uv_vals) > 0:
                    uv_raw = np.asarray(uv_vals, dtype=np.float32)[:, :2]
                    uv_interp = str(st_pv.GetInterpolation() or "vertex")
                    if st_pv.IsIndexed():
                        pv_idx = st_pv.GetIndices(Usd.TimeCode.Default())
                        if pv_idx is not None and len(pv_idx) > 0:
                            uv_indices = np.asarray(pv_idx, dtype=np.int64)
            except Exception:
                uv_raw = None

        normals_attr = usd_mesh.GetNormalsAttr().Get(Usd.TimeCode.Default())
        if normals_attr is not None and len(normals_attr) > 0:
            has_normals = True

        # Fan-triangulate faces while tracking faceVarying corner offsets
        orientation = str(usd_mesh.GetOrientationAttr().Get() or "rightHanded")
        flip_winding = orientation == "leftHanded"

        tri_v_idx: List[Tuple[int, int, int]] = []
        tri_fv_idx: List[Tuple[int, int, int]] = []
        cursor = 0
        n_pts = len(pts_world)
        for fc in cnts:
            fc_i = int(fc)
            if fc_i >= 3 and cursor + fc_i <= len(idxs):
                f_slice = idxs[cursor : cursor + fc_i]
                if np.all((f_slice >= 0) & (f_slice < n_pts)):
                    for k in range(1, fc_i - 1):
                        if flip_winding:
                            tri_v_idx.append((int(f_slice[0]), int(f_slice[k + 1]), int(f_slice[k])))
                            tri_fv_idx.append((cursor, cursor + k + 1, cursor + k))
                        else:
                            tri_v_idx.append((int(f_slice[0]), int(f_slice[k]), int(f_slice[k + 1])))
                            tri_fv_idx.append((cursor, cursor + k, cursor + k + 1))
            cursor += fc_i

        if not tri_v_idx:
            continue

        faces_np = np.asarray(tri_v_idx, dtype=np.uint32)
        base_img, base_rgba, metallic, roughness = _resolve_usd_material_pbr(
            prim, stage_path.parent, extracted_files
        )
        if base_img is not None:
            has_textures = True

        # Unroll faceVarying UVs cleanly if present so every triangle corner preserves exact UV seams!
        mesh_verts = pts_world.astype(np.float32)
        mesh_faces = faces_np
        mesh_uvs: Optional[np.ndarray] = None

        if uv_raw is not None:
            if uv_interp == "faceVarying":
                fv_arr = np.asarray(tri_fv_idx, dtype=np.int64).ravel()
                if uv_indices is not None and np.max(fv_arr) < len(uv_indices):
                    mapped_uv_idx = uv_indices[fv_arr]
                else:
                    mapped_uv_idx = fv_arr
                if len(mapped_uv_idx) > 0 and np.max(mapped_uv_idx) < len(uv_raw):
                    flat_v = faces_np.ravel()
                    mesh_verts = mesh_verts[flat_v]
                    mesh_uvs = uv_raw[mapped_uv_idx]
                    mesh_faces = np.arange(len(mesh_verts), dtype=np.uint32).reshape((-1, 3))
            elif uv_interp in ("vertex", "varying"):
                if uv_indices is not None and len(uv_indices) == len(pts_world) and np.max(uv_indices) < len(uv_raw):
                    mesh_uvs = uv_raw[uv_indices]
                elif len(uv_raw) == len(pts_world):
                    mesh_uvs = uv_raw

        pbr_mat = PBRMaterial(
            baseColorTexture=base_img,
            baseColorFactor=[int(round(c * 255)) for c in base_rgba],
            metallicFactor=metallic,
            roughnessFactor=roughness,
        )
        if mesh_uvs is not None and len(mesh_uvs) == len(mesh_verts):
            visual = TextureVisuals(uv=mesh_uvs, material=pbr_mat)
        else:
            visual = TextureVisuals(material=pbr_mat)

        tm = trimesh.Trimesh(vertices=mesh_verts, faces=mesh_faces, visual=visual, process=False)
        sub_meshes.append(tm)

        all_v_list.append(mesh_verts)
        all_f_list.append(mesh_faces + v_global_offset)
        v_global_offset += len(mesh_verts)

    if not sub_meshes:
        raise ValueError("USD stage contains no valid triangle mesh geometry.")

    if len(sub_meshes) == 1:
        glb_bytes = sub_meshes[0].export(file_type="glb")
    else:
        scene = trimesh.Scene()
        for idx_m, sm in enumerate(sub_meshes):
            scene.add_geometry(sm, node_name=f"usd_mesh_{idx_m}")
        glb_bytes = scene.export(file_type="glb")

    verts_combined = np.vstack(all_v_list).astype(np.float32)
    faces_combined = np.vstack(all_f_list).astype(np.uint32)
    extents_m = (np.max(verts_combined, axis=0) - np.min(verts_combined, axis=0)).tolist()
    info = {
        "vertex_count": int(len(verts_combined)),
        "triangle_count": int(len(faces_combined)),
        "mesh_prim_count": len(sub_meshes),
        "meters_per_unit": meters_per_unit,
        "up_axis": up_axis,
        "has_textures": has_textures,
        "has_normals": has_normals,
        "bbox_extents_m": [float(x) for x in extents_m],
        "lat": lat,
        "lon": lon,
        "alt": alt,
    }
    return glb_bytes, verts_combined, faces_combined, info


def _convert_usda_ascii_fallback(usda_text: str) -> Tuple[bytes, np.ndarray, np.ndarray, Dict[str, Any]]:
    """Lightweight pure-Python `.usda` ASCII fallback if `pxr` is not installed."""
    import trimesh

    mpu_m = re.search(r"metersPerUnit\s*=\s*([0-9.eE+-]+)", usda_text)
    mpu = float(mpu_m.group(1)) if mpu_m else 1.0
    up_m = re.search(r'upAxis\s*=\s*"([YZyz])"', usda_text)
    up_axis = up_m.group(1).upper() if up_m else "Y"

    pts_m = re.search(r"point3f\[\]\s+points\s*=\s*\[(.*?)\]", usda_text, re.S)
    cnts_m = re.search(r"int\[\]\s+faceVertexCounts\s*=\s*\[(.*?)\]", usda_text, re.S)
    idxs_m = re.search(r"int\[\]\s+faceVertexIndices\s*=\s*\[(.*?)\]", usda_text, re.S)
    if not (pts_m and cnts_m and idxs_m):
        raise ValueError("USDA stage contains no Mesh points/faces.")

    pts_tuples = re.findall(r"\(\s*([0-9.eE+-]+)\s*,\s*([0-9.eE+-]+)\s*,\s*([0-9.eE+-]+)\s*\)", pts_m.group(1))
    pts = np.array([[float(a), float(b), float(c)] for a, b, c in pts_tuples], dtype=np.float32) * mpu
    if up_axis == "Z":
        pts = np.column_stack([pts[:, 0], pts[:, 2], -pts[:, 1]]).astype(np.float32)
    cnts = [int(x) for x in re.findall(r"-?\d+", cnts_m.group(1))]
    idxs = [int(x) for x in re.findall(r"-?\d+", idxs_m.group(1))]

    tris: List[List[int]] = []
    cur = 0
    for fc in cnts:
        if fc >= 3 and cur + fc <= len(idxs):
            for k in range(1, fc - 1):
                tris.append([idxs[cur], idxs[cur + k], idxs[cur + k + 1]])
        cur += fc
    faces = np.asarray(tris, dtype=np.uint32)
    tm = trimesh.Trimesh(vertices=pts, faces=faces, process=False)
    glb_bytes = tm.export(file_type="glb")
    return glb_bytes, pts, faces, {
        "vertex_count": int(len(pts)),
        "triangle_count": int(len(faces)),
        "mesh_prim_count": 1,
        "meters_per_unit": mpu,
        "up_axis": up_axis,
        "has_textures": False,
        "has_normals": False,
        "lat": None,
        "lon": None,
        "alt": None,
    }


def convert_usd_bytes_to_glb(
    raw_bytes: bytes,
    ext_low: str = "usdz",
    filename: str = "scan.usdz",
) -> Tuple[bytes, np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Convert raw `.usdz`, `.usda`, or `.usdc` bytes into binary `glTF 2.0` `.glb`,
    returning `(glb_bytes, vertices_float32, faces_uint32, metadata_dict)`.
    """
    ext_clean = (ext_low or Path(filename).suffix or "usdz").lower().lstrip(".")
    with tempfile.TemporaryDirectory(prefix="me_usd_stage_") as tmpd:
        tmp_root = Path(tmpd)
        extracted: List[Path] = []
        if ext_clean == "usdz" or raw_bytes[:4] == b"PK\x03\x04":
            stage_path, extracted = _safe_extract_usdz(raw_bytes, tmp_root)
        else:
            suffix = ".usdc" if (ext_clean == "usdc" or raw_bytes[:8] == b"PXR-USDC") else ".usda"
            stage_path = tmp_root / f"stage{suffix}"
            stage_path.write_bytes(raw_bytes)
            extracted = [stage_path]

        try:
            import pxr  # noqa: F401
            has_pxr = True
        except ImportError:
            has_pxr = False

        if has_pxr:
            return _convert_with_pxr(stage_path, extracted)
        if stage_path.suffix.lower() == ".usda" or stage_path.read_bytes().lstrip()[:6] == b"#usda ":
            return _convert_usda_ascii_fallback(stage_path.read_text(encoding="utf-8", errors="ignore"))
        raise RuntimeError("OpenUSD (`usd-core`) is required to convert binary .usdc stages.")


def inspect_usd_stage_metadata(source_path: Union[Path, bytes], filename: str = "") -> Dict[str, Any]:
    """Fast inspection of `.usdz` / `.usda` / `.usdc` stage metadata for `inspect_geospatial_asset`."""
    if isinstance(source_path, (bytes, bytearray)):
        raw = bytes(source_path)
        ext_s = (filename or "scan.usdz").strip()
        if not Path(ext_s).suffix:
            ext_s = f"scan.{ext_s.lstrip('.')}"
        with tempfile.TemporaryDirectory(prefix="me_usd_insp_") as tmpd:
            tmp_p = Path(tmpd) / Path(ext_s).name
            tmp_p.write_bytes(raw)
            vinfo = validate_usd_archive_or_stage(tmp_p, tmp_p.name)
        _, _, _, meta = convert_usd_bytes_to_glb(raw, ext_low=vinfo["kind"], filename=tmp_p.name)
        meta["usd_kind"] = vinfo["kind"]
        return meta
    vinfo = validate_usd_archive_or_stage(source_path, filename)
    raw = source_path.read_bytes()
    _, _, _, meta = convert_usd_bytes_to_glb(raw, ext_low=vinfo["kind"], filename=filename or source_path.name)
    meta["usd_kind"] = vinfo["kind"]
    return meta
