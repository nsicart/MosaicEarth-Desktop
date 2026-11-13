"""
MosaicEarth Wave 2 (Phase 5) — 1-Click Multi-Platform `.ZIP` Migration Importer.

Automatically inspects and unpacks multi-deliverable `.ZIP` exports from:
  - DJI Terra (`map/result.tif`, `map/dsm.tif`, `models/pc/.../terra_las`, `models/pc/.../terra_b3dm`, `terra_obj`)
  - PIX4Dmapper / PIX4Dmatic (`*_transparent_mosaic_group1.tif`, `*_dsm.tif`, `*_report.pdf`, point clouds & meshes)
  - Polycam & Luma AI (`.usdz`, `.glb`, `.obj` + `.mtl` + textures, `.ply` / `.splat` / `.spz`)
  - DroneDeploy, Agisoft Metashape & WebODM (`odm_orthophoto.tif`, `dsm.tif`, `.laz`, textured meshes, 360° panos, PDF reports)

Classifies each deliverable into its native MosaicAsset class inside a single Survey container while
keeping multi-file groups (`OBJ+MTL+textures`, `3D Tiles tileset.json+tiles`, `Shapefile .shp+.dbf+.shx`,
`Video + .SRT/.GPX`) intact and preserving Quality Report `.PDF` attachments on the Survey.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

MAX_MIGRATION_ENTRIES = 2000
MAX_MIGRATION_UNCOMPRESSED_BYTES = 6 * 1024**3  # 6 GiB safety cap

JUNK_PREFIXES = ("__macosx/", ".git/", ".svn/", "$recycle.bin/")
JUNK_FILENAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def _norm_zip_rel(raw_name: str) -> Optional[str]:
    clean = raw_name.replace("\\", "/").strip("/")
    if not clean:
        return None
    parts = clean.split("/")
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        return None
    low = clean.lower()
    if any(low.startswith(pref) or f"/{pref}" in low for pref in JUNK_PREFIXES):
        return None
    if parts[-1].lower() in JUNK_FILENAMES or parts[-1].startswith("._"):
        return None
    return clean


def detect_export_platform(rel_paths: List[str]) -> str:
    joined = " | ".join(p.lower() for p in rel_paths)
    if any(tok in joined for tok in ("terra_b3dm", "terra_obj", "terra_las", "terra_pnts", "map/result.tif")):
        return "DJI Terra"
    if any(tok in joined for tok in ("transparent_mosaic", "1_initial", "2_densification", "3_dsm_ortho", "pix4d")):
        return "PIX4D"
    if any(tok in joined for tok in ("odm_orthophoto", "odm_dem", "odm_texturing", "entwine_pointcloud")):
        return "WebODM"
    if "polycam" in joined or ("keyframes/" in joined and any(p.lower().endswith((".usdz", ".glb")) for p in rel_paths)):
        return "Polycam"
    if "luma" in joined:
        return "Luma AI"
    if "dronedeploy" in joined:
        return "DroneDeploy"
    if "metashape" in joined or "agisoft" in joined:
        return "Agisoft Metashape"
    return "Multi-Deliverable Survey Bundle"


def inspect_migration_zip(zip_path: Path) -> Dict[str, Any]:
    """
    Safely inspect a `.zip` archive and group its contents into logical geospatial deliverables
    plus PDF quality reports without extracting untrusted files to disk yet.
    """
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Invalid .ZIP archive.")

    entries_by_rel: Dict[str, zipfile.ZipInfo] = {}
    total_uncompressed = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_MIGRATION_ENTRIES:
            raise ValueError(f"Archive exceeds maximum entry count ({MAX_MIGRATION_ENTRIES}).")
        for info in infos:
            # Reject symlinks
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Archive contains unsafe symbolic links.")
            # Zip-bomb compression ratio guard for large entries
            if info.file_size > 16 * 1024**2 and info.file_size > 250 * max(1, info.compress_size):
                raise ValueError("Archive entry exceeds safe compression ratio.")
            rel = _norm_zip_rel(info.filename)
            if not rel:
                continue
            total_uncompressed += int(info.file_size)
            if total_uncompressed > MAX_MIGRATION_UNCOMPRESSED_BYTES:
                raise ValueError("Archive uncompressed size exceeds 6 GiB limit.")
            entries_by_rel[rel] = info

    all_rels = sorted(entries_by_rel.keys())
    platform = detect_export_platform(all_rels)
    claimed: Set[str] = set()
    deliverables: List[Dict[str, Any]] = []
    reports_pdf: List[str] = []

    # 1. Group 3D Tiles directories (`tileset.json` + sibling/child tile payloads)
    tileset_jsons = [r for r in all_rels if posixpath.basename(r).lower() == "tileset.json"]
    for ts_rel in tileset_jsons:
        if ts_rel in claimed:
            continue
        ts_dir = posixpath.dirname(ts_rel)
        prefix = f"{ts_dir}/" if ts_dir else ""
        members = [r for r in all_rels if (r == ts_rel or (prefix and r.startswith(prefix)) or (not prefix and posixpath.dirname(r) == ""))]
        tile_bins = [m for m in members if Path(m).suffix.lower() in {".b3dm", ".pnts", ".i3dm", ".cmpt", ".glb", ".gltf", ".bin", ".ktx2", ".webp", ".png", ".jpg"}]
        if tile_bins:
            claimed.update(members)
            folder_label = posixpath.basename(ts_dir) if ts_dir else Path(zip_path.name).stem
            deliverables.append({
                "kind": "3dtiles_bundle",
                "category": "3D Tileset",
                "title": f"3D Tiles — {folder_label}",
                "bundle_filename": f"{re.sub(r'[^A-Za-z0-9._-]+', '_', folder_label or 'tileset')}_3dtiles.zip",
                "members": members,
                "strip_prefix": prefix,
            })

    # 2. Group `.obj` + `.mtl` + companion textures in the same directory
    obj_rels = [r for r in all_rels if r not in claimed and r.lower().endswith(".obj")]
    for obj_r in obj_rels:
        obj_dir = posixpath.dirname(obj_r)
        stem = Path(obj_r).stem
        same_dir_files = [
            r for r in all_rels
            if r not in claimed
            and posixpath.dirname(r) == obj_dir
            and Path(r).suffix.lower() in {".obj", ".mtl", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tga", ".prj", ".xyz"}
        ]
        # Keep multiple OBJs separate if unrelated, otherwise bundle OBJ + MTL + textures
        members = [r for r in same_dir_files if Path(r).suffix.lower() != ".obj" or r == obj_r]
        claimed.update(members)
        if len(members) > 1:
            deliverables.append({
                "kind": "obj_bundle",
                "category": "3D Textured Mesh",
                "title": f"3D Mesh — {stem}",
                "bundle_filename": f"{re.sub(r'[^A-Za-z0-9._-]+', '_', stem)}_mesh.zip",
                "members": members,
                "strip_prefix": f"{obj_dir}/" if obj_dir else "",
            })
        else:
            deliverables.append({
                "kind": "single",
                "category": "3D Mesh",
                "title": f"3D Mesh — {stem}",
                "primary_rel": obj_r,
                "sidecar_rels": [],
            })

    # 3. Group ESRI Shapefiles (`.shp` + `.shx` + `.dbf` + `.prj` + `.cpg`)
    shp_rels = [r for r in all_rels if r not in claimed and r.lower().endswith(".shp")]
    for shp_r in shp_rels:
        shp_dir = posixpath.dirname(shp_r)
        shp_stem = Path(shp_r).stem.lower()
        members = [
            r for r in all_rels
            if r not in claimed
            and posixpath.dirname(r) == shp_dir
            and Path(r).stem.lower() == shp_stem
            and Path(r).suffix.lower() in {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qpj"}
        ]
        claimed.update(members)
        deliverables.append({
            "kind": "shp_bundle",
            "category": "GIS Vector Layer",
            "title": f"Vector — {Path(shp_r).stem}",
            "bundle_filename": f"{re.sub(r'[^A-Za-z0-9._-]+', '_', Path(shp_r).stem)}_shapefile.zip",
            "members": members,
            "strip_prefix": f"{shp_dir}/" if shp_dir else "",
        })

    # 4. Group Drone Geo-Videos (`.mp4`/`.mov`/`.webm`/`.m4v` + matching `.srt`/`.gpx`/`.csv`)
    vid_rels = [r for r in all_rels if r not in claimed and Path(r).suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}]
    for vid_r in vid_rels:
        vid_dir = posixpath.dirname(vid_r)
        vid_stem = Path(vid_r).stem.lower()
        sidecars = [
            r for r in all_rels
            if r not in claimed
            and posixpath.dirname(r) == vid_dir
            and Path(r).stem.lower() == vid_stem
            and Path(r).suffix.lower() in {".srt", ".gpx", ".csv"}
        ]
        claimed.add(vid_r)
        claimed.update(sidecars)
        deliverables.append({
            "kind": "single",
            "category": "Geo-Video",
            "title": f"Geo-Video — {Path(vid_r).stem}",
            "primary_rel": vid_r,
            "sidecar_rels": sidecars,
        })

    # 5. Classify remaining standalone deliverables (GeoTIFF Ortho/DSM, USDZ/GLB, Splat/PointCloud, Panos, Vectors, PDF Reports)
    for rel in all_rels:
        if rel in claimed:
            continue
        ext = Path(rel).suffix.lower()
        stem = Path(rel).stem
        low_rel = rel.lower()

        if ext == ".pdf":
            claimed.add(rel)
            reports_pdf.append(rel)
            continue

        if ext in {".tif", ".tiff"}:
            r_dir = posixpath.dirname(rel)
            sidecars = [
                s for s in all_rels
                if s not in claimed
                and posixpath.dirname(s) == r_dir
                and Path(s).stem.lower() == stem.lower()
                and Path(s).suffix.lower() in {".tfw", ".wld", ".prj", ".aux.xml"}
            ]
            claimed.add(rel)
            claimed.update(sidecars)
            is_dsm_name = any(tok in low_rel for tok in ("dsm", "dtm", "dem", "elev", "height"))
            cat = "DSM / Elevation" if is_dsm_name else "Orthomosaic / Raster"
            deliverables.append({
                "kind": "single",
                "category": cat,
                "title": f"{cat.split('/')[0].strip()} — {stem}",
                "primary_rel": rel,
                "sidecar_rels": sidecars,
            })
        elif ext in {".usdz", ".usda", ".usdc", ".glb", ".dae", ".stl", ".fbx", ".ifc"}:
            claimed.add(rel)
            deliverables.append({
                "kind": "single",
                "category": "3D Model",
                "title": f"3D Model — {stem}",
                "primary_rel": rel,
                "sidecar_rels": [],
            })
        elif ext in {".splat", ".spz", ".ksplat"}:
            claimed.add(rel)
            deliverables.append({
                "kind": "single",
                "category": "3D Gaussian Splat",
                "title": f"Gaussian Splat — {stem}",
                "primary_rel": rel,
                "sidecar_rels": [],
            })
        elif ext in {".laz", ".las", ".ply", ".xyz", ".pts", ".e57"}:
            claimed.add(rel)
            cat = "Point Cloud / Splat" if ext == ".ply" else "LiDAR / Point Cloud"
            deliverables.append({
                "kind": "single",
                "category": cat,
                "title": f"{cat.split('/')[0].strip()} — {stem}",
                "primary_rel": rel,
                "sidecar_rels": [],
            })
        elif ext in {".geojson", ".json", ".kml", ".kmz", ".gpx", ".dxf", ".gpkg"}:
            if ext == ".json" and not any(k in low_rel for k in ("geojson", "cityjson", "boundary", "track")):
                continue
            claimed.add(rel)
            deliverables.append({
                "kind": "single",
                "category": "GIS / CAD Vector",
                "title": f"Vector — {stem}",
                "primary_rel": rel,
                "sidecar_rels": [],
            })
        elif ext in {".jpg", ".jpeg", ".webp", ".png"}:
            # Skip tiny thumbnails / icons (< 12 KB) inside platform export zips
            if int(entries_by_rel[rel].file_size) < 12 * 1024:
                continue
            r_dir = posixpath.dirname(rel)
            sidecars = [
                s for s in all_rels
                if s not in claimed
                and posixpath.dirname(s) == r_dir
                and Path(s).stem.lower() == stem.lower()
                and Path(s).suffix.lower() in {".jgw", ".pgw", ".wld", ".prj"}
            ]
            claimed.add(rel)
            claimed.update(sidecars)
            deliverables.append({
                "kind": "single",
                "category": "360° Panorama / Geo-Photo",
                "title": stem,
                "primary_rel": rel,
                "sidecar_rels": sidecars,
            })

    is_multi = (len(deliverables) >= 2) or (len(deliverables) == 1 and len(reports_pdf) >= 1)
    return {
        "ok": True,
        "platform": platform,
        "deliverables_count": len(deliverables),
        "reports_pdf_count": len(reports_pdf),
        "is_multi_deliverable_bundle": is_multi,
        "deliverables": deliverables,
        "reports_pdf": reports_pdf,
        "total_uncompressed_bytes": total_uncompressed,
    }


def ingest_migration_zip_into_project(
    store: Any,
    blob_store: Any,
    *,
    zip_path: Path,
    project_id: int,
    user_id: int,
    base_title: str = "",
    capture_date: str = "",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    run_sync: bool = False,
    staged_upload_id: str = "",
    upload_attempt: str = "",
) -> Dict[str, Any]:
    """
    Unpack a multi-deliverable `.ZIP` export into `project_id`, creating individual
    first-class `MosaicAsset` records for each geospatial deliverable and saving any
    Quality Report `.PDF` directly onto the Survey!
    """
    plan = inspect_migration_zip(zip_path)
    if plan["deliverables_count"] == 0 and plan["reports_pdf_count"] == 0:
        raise ValueError("No supported geospatial deliverables or reports were found inside the .ZIP archive.")

    pid = int(project_id)
    uid = int(user_id)
    proj = store.get_project(pid) or {}
    folder = str(proj.get("FOLDER") or f"{pid}-{proj.get('NAME', 'survey')}")
    platform = plan["platform"]

    created_assets: List[Dict[str, Any]] = []
    saved_reports: List[str] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        # 1. Persist Quality Report PDFs onto the Survey (`{folder}/procesado/download/report.pdf` + `attachments/`)
        for idx_pdf, pdf_rel in enumerate(plan["reports_pdf"]):
            pdf_bytes = zf.read(pdf_rel)
            if not pdf_bytes.startswith(b"%PDF-"):
                continue
            safe_pdf_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(pdf_rel).name) or f"report_{idx_pdf + 1}.pdf"
            att_rel = f"external/{pid}/attachments/{safe_pdf_name}"
            blob_store.save_bytes("assets", att_rel, pdf_bytes)
            if idx_pdf == 0 or "report" in safe_pdf_name.lower():
                blob_store.save_bytes("imagenes", f"{folder}/procesado/download/report.pdf", pdf_bytes)
            saved_reports.append(safe_pdf_name)

        # 2. Materialize each geospatial deliverable into `assets` blob store and register via `create_external_asset_import`
        for idx_d, item in enumerate(plan["deliverables"]):
            kind = item["kind"]
            item_title = item["title"]
            if base_title and plan["deliverables_count"] == 1:
                item_title = base_title

            if kind in ("3dtiles_bundle", "obj_bundle", "shp_bundle"):
                bundle_fname = item["bundle_filename"]
                strip_p = item.get("strip_prefix") or ""
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as sub_zf:
                    for m_rel in item["members"]:
                        arc_name = m_rel[len(strip_p) :] if (strip_p and m_rel.startswith(strip_p)) else m_rel
                        sub_zf.writestr(arc_name, zf.read(m_rel))
                sub_bytes = buf.getvalue()
                stored_rel = f"external/{pid}/migrated/{idx_d:03d}_{bundle_fname}"
                blob_store.save_bytes("assets", stored_rel, sub_bytes)
                aid = store.create_external_asset_import(
                    project_id=pid,
                    user_id=uid,
                    display_name=item_title,
                    source_filename=bundle_fname,
                    stored_rel_path=stored_rel,
                    size_bytes=len(sub_bytes),
                    raw_bytes=sub_bytes if run_sync else None,
                    lat=lat,
                    lon=lon,
                    capture_date=capture_date,
                    capture_method=f"1-Click ZIP Migration ({platform})",
                    source_origin="imported",
                    run_sync=run_sync,
                    staged_upload_id=staged_upload_id,
                    upload_attempt=upload_attempt,
                )
                created_assets.append({
                    "asset_id": int(aid),
                    "title": item_title,
                    "category": item["category"],
                    "filename": bundle_fname,
                })
            else:
                prim_rel = item["primary_rel"]
                prim_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(prim_rel).name)
                prim_bytes = zf.read(prim_rel)
                stored_prim = f"external/{pid}/migrated/{idx_d:03d}/{prim_name}"
                blob_store.save_bytes("assets", stored_prim, prim_bytes)

                extra_pairs: List[Tuple[str, str]] = [(Path(prim_rel).name, stored_prim)]
                for sc_rel in item.get("sidecar_rels") or []:
                    sc_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(sc_rel).name)
                    sc_stored = f"external/{pid}/migrated/{idx_d:03d}/{sc_name}"
                    blob_store.save_bytes("assets", sc_stored, zf.read(sc_rel))
                    extra_pairs.append((Path(sc_rel).name, sc_stored))

                aid = store.create_external_asset_import(
                    project_id=pid,
                    user_id=uid,
                    display_name=item_title,
                    source_filename=Path(prim_rel).name,
                    stored_rel_path=stored_prim,
                    size_bytes=len(prim_bytes),
                    raw_bytes=prim_bytes if run_sync else None,
                    lat=lat,
                    lon=lon,
                    capture_date=capture_date,
                    capture_method=f"1-Click ZIP Migration ({platform})",
                    source_origin="imported",
                    run_sync=run_sync,
                    extra_media_rel_paths=extra_pairs if len(extra_pairs) > 1 else None,
                    staged_upload_id=staged_upload_id,
                    upload_attempt=upload_attempt,
                )
                created_assets.append({
                    "asset_id": int(aid),
                    "title": item_title,
                    "category": item["category"],
                    "filename": Path(prim_rel).name,
                })

    return {
        "ok": True,
        "project_id": pid,
        "platform": platform,
        "created_assets": created_assets,
        "asset_ids": [a["asset_id"] for a in created_assets],
        "saved_reports_pdf": saved_reports,
    }
