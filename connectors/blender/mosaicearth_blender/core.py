"""Pure-Python staging and synchronization core for the MosaicEarth Blender add-on.

AGPL-3.0-or-later. Uses only the Python standard library and mosaicearth_sync.
"""
import argparse
import getpass
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

try:
    from .mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products
except ImportError:  # pragma: no cover - direct script execution
    from mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products


SPLAT_EXTS = {".ply", ".spz", ".splat", ".ksplat"}
MESH_IMPORT_EXTS = {".glb", ".gltf", ".obj", ".ply"}


def is_gaussian_ply(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext in {".spz", ".splat", ".ksplat"}:
        return True
    if ext == ".ply" and path.is_file():
        with path.open("rb") as stream:
            return b"f_dc_0" in stream.read(65536)
    return False


def bundle_obj_directory(folder: Path) -> Path:
    """Create a portable .obj.zip preserving .mtl and texture maps."""
    objs = sorted(folder.glob("*.obj"))
    if not objs:
        raise SyncError("No .obj file found in export directory")
    primary = objs[0]
    bundle = primary.with_suffix(".obj.zip")
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(folder.rglob("*")):
            if item.is_file() and item != bundle and not item.name.endswith(".obj.zip"):
                archive.write(item, item.relative_to(folder).as_posix())
    return bundle


def stage_blender_package(
    staging_dir: Path,
    project_name: str,
    *,
    glb_path: Path | None = None,
    splat_path: Path | None = None,
    extra_files: list[Path] | None = None,
    blend_backup: Path | None = None,
    georef: dict | None = None,
    target_project_id: int = 0,
    survey_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
    origin_key: str = "",
    progress=None,
):
    """Assemble exported Blender deliverables and return (manifest, paths)."""
    staging_dir = Path(staging_dir).resolve()
    products_dir = staging_dir / "products"
    attachments_dir = staging_dir / "attachments"
    products_dir.mkdir(parents=True, exist_ok=True)

    file_classes = {}
    copied_any = False

    if glb_path and Path(glb_path).is_file():
        src = Path(glb_path).resolve()
        dest = products_dir / src.name
        if src != dest:
            shutil.copyfile(src, dest)
        file_classes[dest.relative_to(staging_dir).as_posix()] = "mesh"
        copied_any = True

    if splat_path and Path(splat_path).is_file():
        src = Path(splat_path).resolve()
        if src.suffix.lower() not in SPLAT_EXTS:
            raise SyncError("Gaussian Splat file must be .ply, .spz, .splat or .ksplat")
        dest = products_dir / src.name
        if src != dest:
            shutil.copyfile(src, dest)
        file_classes[dest.relative_to(staging_dir).as_posix()] = (
            "gaussian" if is_gaussian_ply(dest) else "pointcloud"
        )
        copied_any = True

    for extra in extra_files or []:
        src = Path(extra).resolve()
        if not src.is_file():
            continue
        if src.suffix.lower() == ".obj":
            obj_tmp = staging_dir / "_obj_stage"
            if obj_tmp.exists():
                shutil.rmtree(obj_tmp)
            obj_tmp.mkdir(parents=True)
            shutil.copyfile(src, obj_tmp / src.name)
            mtl = src.with_suffix(".mtl")
            if mtl.is_file():
                shutil.copyfile(mtl, obj_tmp / mtl.name)
            for tex in src.parent.iterdir():
                if tex.is_file() and tex.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
                    shutil.copyfile(tex, obj_tmp / tex.name)
            bundle = bundle_obj_directory(obj_tmp)
            dest = products_dir / bundle.name
            shutil.move(str(bundle), str(dest))
            shutil.rmtree(obj_tmp, ignore_errors=True)
            file_classes[dest.relative_to(staging_dir).as_posix()] = "mesh"
            copied_any = True
        elif src.suffix.lower() in PRODUCTS or src.name.lower().endswith((".obj.zip", ".shp.zip")):
            dest = products_dir / src.name
            if src != dest:
                shutil.copyfile(src, dest)
            stem_low = src.stem.lower()
            if src.suffix.lower() in {".tif", ".tiff"} and any(k in stem_low for k in ("dsm", "dtm", "dem", "elev", "height")):
                file_classes[dest.relative_to(staging_dir).as_posix()] = "elevation"
            copied_any = True
        else:
            attachments_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, attachments_dir / src.name)
            copied_any = True

    if blend_backup and Path(blend_backup).is_file():
        attachments_dir.mkdir(parents=True, exist_ok=True)
        src = Path(blend_backup).resolve()
        shutil.copyfile(src, attachments_dir / src.name)
        copied_any = True

    if not copied_any:
        raise SyncError("Export a .glb mesh or choose a 3D Gaussian Splat / model file to synchronize")

    clean_georef = {}
    if isinstance(georef, dict) and georef.get("enabled", True):
        for key in ("lat", "lon", "alt", "heading", "pitch", "roll", "scale"):
            if key in georef and georef[key] is not None:
                try:
                    clean_georef[key] = float(georef[key])
                except (TypeError, ValueError):
                    pass

    if clean_georef:
        attachments_dir.mkdir(parents=True, exist_ok=True)
        (attachments_dir / "georeference.json").write_text(
            json.dumps({"crs": "EPSG:4326", **clean_georef}, indent=2), encoding="utf-8"
        )

    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", project_name.strip()).strip("-") or "blender-scene"
    eff_origin = origin_key or f"blender:{safe_slug}"
    if survey_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", survey_date.strip()):
        eff_origin = f"{eff_origin}:{survey_date.strip()}"

    meta = {
        "connector": "blender",
        "blender": {
            "project_name": project_name.strip(),
            "georef": clean_georef,
        },
        "file_classes": file_classes,
    }
    if clean_georef:
        meta["georef"] = clean_georef
    if int(target_project_id or 0) > 0:
        meta["target_project_id"] = int(target_project_id)
    if int(survey_id or 0) > 0:
        meta["survey_id"] = int(survey_id)
    if survey_name and survey_name.strip():
        meta["survey_name"] = survey_name.strip()[:120]
    if survey_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", survey_date.strip()):
        meta["survey_date"] = survey_date.strip()

    def role_for(rel: str) -> str:
        p = Path(rel)
        if rel.startswith("products/") and (p.suffix.lower() in PRODUCTS or rel.lower().endswith((".obj.zip", ".shp.zip"))):
            return "product"
        return "attachment"

    manifest, paths = build_manifest(staging_dir, eff_origin, project_name.strip() or "Blender Scene", meta, role_for, progress)
    for item in manifest["files"]:
        cls = file_classes.get(item["path"])
        if cls:
            item["asset_class"] = cls
    return manifest, paths


def pull_cloud_to_folder(client: Client, destination: Path, *, snapshot_id: str = "", cloud_project_id: int = 0, progress=None):
    """Download a MosaicEarth snapshot or project export and return importable 3D asset paths."""
    sid = snapshot_id
    if not sid:
        if int(cloud_project_id or 0) <= 0:
            raise SyncError("Select a Cloud snapshot or project to download")
        state = client.request("POST", f"/api/v2/sync/projects/{int(cloud_project_id)}/export", {})
        sid = state["id"]
        while state["status"] in {"QUEUED", "VERIFYING"}:
            if progress:
                progress(0, 0, "Preparing Cloud export…")
            time.sleep(2)
            state = client.request("GET", f"/api/v2/sync/sessions/{sid}")
        if state["status"] != "COMPLETE":
            raise SyncError(state.get("error") or "Cloud export failed")

    target_dir = Path(destination).resolve() / sid[:12]
    state = client.pull(sid, target_dir)

    importable = []
    for item in state["manifest"]["files"]:
        local_file = target_dir / item["path"]
        if local_file.name == "all.zip":
            extracted = target_dir / "_extracted_all"
            extract_products(local_file, extracted)
            for sub in sorted(extracted.rglob("*")):
                if sub.is_file() and sub.suffix.lower() in MESH_IMPORT_EXTS:
                    importable.append(sub)
        elif local_file.name.lower().endswith(".obj.zip"):
            extracted = local_file.with_suffix("")
            extract_products(local_file, extracted)
            for sub in sorted(extracted.rglob("*.obj")):
                importable.append(sub)
        elif local_file.suffix.lower() in MESH_IMPORT_EXTS or local_file.suffix.lower() in SPLAT_EXTS:
            importable.append(local_file)

    manifest_meta = state["manifest"].get("metadata") or {}
    georef = manifest_meta.get("georef") or (manifest_meta.get("blender") or {}).get("georef") or {}
    if not georef and isinstance(manifest_meta.get("assets"), list) and manifest_meta["assets"]:
        a0 = manifest_meta["assets"][0]
        if a0.get("LATITUDE") or a0.get("LONGITUDE"):
            georef = {
                "lat": a0.get("LATITUDE", 0.0),
                "lon": a0.get("LONGITUDE", 0.0),
                "alt": a0.get("ALTITUDE", 0.0),
                "heading": a0.get("HEADING", 0.0),
                "pitch": a0.get("PITCH", 0.0),
                "roll": a0.get("ROLL", 0.0),
                "scale": a0.get("SCALE", 1.0),
            }

    return {
        "snapshot_id": sid,
        "name": state["manifest"]["name"],
        "directory": target_dir,
        "importable_files": importable,
        "georef": georef,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="MosaicEarth Blender & 3DGS CLI synchronizer")
    parser.add_argument("--origin", default=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", ORIGIN))
    parser.add_argument("--email", default=os.environ.get("MOSAICEARTH_EMAIL", ""))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_push = sub.add_parser("push", help="Upload .glb meshes and/or 3D Gaussian Splats to MyEarth")
    p_push.add_argument("--name", required=True, help="Scene or survey task name")
    p_push.add_argument("--glb", type=Path, help="Exported .glb mesh file")
    p_push.add_argument("--splat", type=Path, help="3D Gaussian Splat (.ply, .spz, .splat, .ksplat)")
    p_push.add_argument("--file", type=Path, action="append", default=[], help="Additional product or attachment file")
    p_push.add_argument("--lat", type=float, default=None)
    p_push.add_argument("--lon", type=float, default=None)
    p_push.add_argument("--alt", type=float, default=None)
    p_push.add_argument("--heading", type=float, default=None)
    p_push.add_argument("--scale", type=float, default=None)
    p_push.add_argument("--project-id", type=int, default=0, help="Existing MyEarth Cloud Project ID")
    p_push.add_argument("--survey-name", default="", help="Survey container name (4D tracking)")
    p_push.add_argument("--survey-date", default="", help="Survey capture date YYYY-MM-DD (4D tracking)")

    p_pull = sub.add_parser("pull", help="Download a MyEarth project or snapshot locally")
    p_pull.add_argument("--dest", type=Path, required=True)
    p_pull.add_argument("--snapshot-id", default="")
    p_pull.add_argument("--project-id", type=int, default=0)

    args = parser.parse_args(argv)
    token = os.environ.get("MOSAICEARTH_TOKEN", "")
    client = Client(token=token, origin=args.origin, progress=lambda d, t, m: print(f"[{d}/{t}] {m}", flush=True))
    if not client.token:
        email = args.email or input("MosaicEarth email: ").strip()
        password = os.environ.get("MOSAICEARTH_PASSWORD") or getpass.getpass("MosaicEarth password: ")
        client.signin(email, password)

    if args.cmd == "push":
        georef = {}
        for k, v in (("lat", args.lat), ("lon", args.lon), ("alt", args.alt), ("heading", args.heading), ("scale", args.scale)):
            if v is not None:
                georef[k] = v
        with tempfile.TemporaryDirectory(prefix="mosaicearth-blender-") as tmp:
            manifest, paths = stage_blender_package(
                Path(tmp),
                args.name,
                glb_path=args.glb,
                splat_path=args.splat,
                extra_files=args.file,
                georef=georef or None,
                target_project_id=args.project_id,
                survey_name=args.survey_name,
                survey_date=args.survey_date,
            )
            state = client.push(manifest, paths)
            print(json.dumps({"ok": True, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}, indent=2))
    else:
        res = pull_cloud_to_folder(
            client,
            args.dest,
            snapshot_id=args.snapshot_id,
            cloud_project_id=args.project_id,
            progress=client.progress,
        )
        print(json.dumps({"ok": True, "snapshot_id": res["snapshot_id"], "directory": str(res["directory"]), "files": [str(f) for f in res["importable_files"]]}, indent=2))


if __name__ == "__main__":
    main()
