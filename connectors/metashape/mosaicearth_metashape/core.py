"""Pure-Python staging, manifest construction and Cloud pull for Agisoft Metashape.

AGPL-3.0-or-later. Standard library only plus mosaicearth_sync.
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
except ImportError:  # pragma: no cover
    from mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products


def bundle_obj_model(obj_path: Path, output_zip: Path) -> Path:
    """Bundle .obj, .mtl and texture maps from obj_path.parent into output_zip."""
    obj_path = Path(obj_path).resolve()
    if not obj_path.is_file():
        raise SyncError(f"Model file not found: {obj_path}")
    output_zip = Path(output_zip).resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for f in sorted(obj_path.parent.rglob("*")):
            if f.is_file() and f != output_zip and not f.name.lower().endswith(".obj.zip"):
                archive.write(f, f.relative_to(obj_path.parent).as_posix())
    return output_zip


def stage_metashape_deliverables(
    staging_dir: Path,
    chunk_name: str,
    *,
    orthomosaic_tif: Path | None = None,
    dsm_tif: Path | None = None,
    dtm_tif: Path | None = None,
    pointcloud_file: Path | None = None,
    model_file: Path | None = None,
    splat_file: Path | None = None,
    report_pdf: Path | None = None,
    source_photos: list[Path] | None = None,
    chunk_metadata: dict | None = None,
    target_project_id: int = 0,
    survey_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
    origin_key: str = "",
    progress=None,
):
    """Prepare exported Metashape products and build a MosaicEarth v1 sync manifest."""
    staging_dir = Path(staging_dir).resolve()
    products_dir = staging_dir / "products"
    images_dir = staging_dir / "images"
    attachments_dir = staging_dir / "attachments"
    products_dir.mkdir(parents=True, exist_ok=True)

    file_classes = {}
    staged_count = 0

    def _copy_product(src_opt: Path | None, dest_name: str | None, asset_cls: str):
        nonlocal staged_count
        if not src_opt or not Path(src_opt).is_file():
            return
        src = Path(src_opt).resolve()
        if src.suffix.lower() == ".obj":
            dest = products_dir / ((dest_name or src.stem) + ".obj.zip")
            bundle_obj_model(src, dest)
        else:
            dest = products_dir / (dest_name or src.name)
            if src != dest:
                shutil.copyfile(src, dest)
        file_classes[dest.relative_to(staging_dir).as_posix()] = asset_cls
        staged_count += 1

    _copy_product(orthomosaic_tif, "orthomosaic.tif" if orthomosaic_tif else None, "orthophoto")
    _copy_product(dsm_tif, "dsm.tif" if dsm_tif else None, "elevation")
    _copy_product(dtm_tif, "dtm.tif" if dtm_tif else None, "elevation")
    _copy_product(pointcloud_file, None, "pointcloud")
    _copy_product(model_file, None, "mesh")
    _copy_product(splat_file, None, "gaussian")

    if report_pdf and Path(report_pdf).is_file():
        attachments_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(report_pdf).resolve(), attachments_dir / Path(report_pdf).name)
        staged_count += 1

    for photo in source_photos or []:
        p = Path(photo).resolve()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            images_dir.mkdir(parents=True, exist_ok=True)
            dest_img = images_dir / p.name
            if dest_img.exists():
                dest_img = images_dir / f"{ staged_count }_{p.name}"
            shutil.copyfile(p, dest_img)
            staged_count += 1

    if staged_count == 0:
        raise SyncError("No Metashape deliverables or source photos selected to synchronize")

    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", chunk_name.strip()).strip("-") or "metashape-chunk"
    eff_origin = origin_key or f"metashape:{safe_slug}"
    if survey_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", survey_date.strip()):
        eff_origin = f"{eff_origin}:{survey_date.strip()}"

    meta = {
        "connector": "metashape",
        "metashape": chunk_metadata or {"chunk": chunk_name.strip()},
        "file_classes": file_classes,
    }
    if int(target_project_id or 0) > 0:
        meta["target_project_id"] = int(target_project_id)
    if int(survey_id or 0) > 0:
        meta["survey_id"] = int(survey_id)
    if survey_name and survey_name.strip():
        meta["survey_name"] = survey_name.strip()[:120]
    if survey_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", survey_date.strip()):
        meta["survey_date"] = survey_date.strip()

    def role_for(rel: str) -> str:
        if rel.startswith("images/"):
            return "source"
        if rel.startswith("products/") and (Path(rel).suffix.lower() in PRODUCTS or rel.lower().endswith((".obj.zip", ".shp.zip"))):
            return "product"
        return "attachment"

    manifest, paths = build_manifest(staging_dir, eff_origin, chunk_name.strip() or "Metashape Survey", meta, role_for, progress)
    for item in manifest["files"]:
        cls = file_classes.get(item["path"])
        if cls:
            item["asset_class"] = cls
    return manifest, paths


def pull_cloud_for_metashape(client: Client, destination: Path, *, snapshot_id: str = "", cloud_project_id: int = 0, progress=None):
    """Download a Cloud project or snapshot and classify files for Metashape chunk import."""
    sid = snapshot_id
    if not sid:
        if int(cloud_project_id or 0) <= 0:
            raise SyncError("Select a Cloud project or snapshot to download")
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

    photos, rasters, pointclouds, models = [], [], [], []
    for item in state["manifest"]["files"]:
        f = target_dir / item["path"]
        ext = f.suffix.lower()
        if f.name == "all.zip":
            ex = target_dir / "_extracted_all"
            extract_products(f, ex)
            for sub in sorted(ex.rglob("*")):
                if not sub.is_file():
                    continue
                se = sub.suffix.lower()
                if se in {".tif", ".tiff"}:
                    rasters.append(sub)
                elif se in {".laz", ".las", ".ply"}:
                    pointclouds.append(sub)
                elif se in {".glb", ".obj"}:
                    models.append(sub)
        elif item["role"] == "source" and ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            photos.append(f)
        elif f.name.lower().endswith(".obj.zip"):
            ex = f.with_suffix("")
            extract_products(f, ex)
            models.extend(sorted(ex.rglob("*.obj")))
        elif ext in {".tif", ".tiff"}:
            rasters.append(f)
        elif ext in {".laz", ".las", ".ply"}:
            pointclouds.append(f)
        elif ext in {".glb", ".obj"}:
            models.append(f)

    return {
        "snapshot_id": sid,
        "name": state["manifest"]["name"],
        "directory": target_dir,
        "photos": photos,
        "rasters": rasters,
        "pointclouds": pointclouds,
        "models": models,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="MosaicEarth Agisoft Metashape CLI synchronizer")
    parser.add_argument("--origin", default=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", ORIGIN))
    parser.add_argument("--email", default=os.environ.get("MOSAICEARTH_EMAIL", ""))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_push = sub.add_parser("push", help="Synchronize exported Metashape deliverables to MyEarth")
    p_push.add_argument("--name", required=True)
    p_push.add_argument("--ortho", type=Path)
    p_push.add_argument("--dsm", type=Path)
    p_push.add_argument("--dtm", type=Path)
    p_push.add_argument("--pointcloud", type=Path)
    p_push.add_argument("--model", type=Path)
    p_push.add_argument("--splat", type=Path)
    p_push.add_argument("--report", type=Path)
    p_push.add_argument("--project-id", type=int, default=0)
    p_push.add_argument("--survey-name", default="")
    p_push.add_argument("--survey-date", default="")

    p_pull = sub.add_parser("pull", help="Download a MyEarth project or snapshot for Metashape")
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
        with tempfile.TemporaryDirectory(prefix="mosaicearth-metashape-") as tmp:
            manifest, paths = stage_metashape_deliverables(
                Path(tmp),
                args.name,
                orthomosaic_tif=args.ortho,
                dsm_tif=args.dsm,
                dtm_tif=args.dtm,
                pointcloud_file=args.pointcloud,
                model_file=args.model,
                splat_file=args.splat,
                report_pdf=args.report,
                target_project_id=args.project_id,
                survey_name=args.survey_name,
                survey_date=args.survey_date,
            )
            state = client.push(manifest, paths)
            print(json.dumps({"ok": True, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}, indent=2))
    else:
        res = pull_cloud_for_metashape(
            client, args.dest, snapshot_id=args.snapshot_id, cloud_project_id=args.project_id, progress=client.progress
        )
        print(json.dumps({"ok": True, "snapshot_id": res["snapshot_id"], "directory": str(res["directory"])}, indent=2))


if __name__ == "__main__":
    main()
