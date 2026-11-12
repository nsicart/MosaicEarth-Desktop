"""MosaicEarth DJI Terra Connector, Watch-Folder Daemon & Dark GUI (0.4.0).

Auto-discovers DJI Terra 2D/3D/LiDAR reconstruction outputs (`map/result.tif`,
`map/dsm.tif`, `models/pc/*/terra_b3dm/tileset.json`, `terra_las/*.las|*.laz`,
`terra_obj/*.obj`, and PDF quality reports) and synchronizes them into dated
MyEarth 4D Surveys via verified 4 MiB SHA-256 chunks.

AGPL-3.0-or-later. Python standard library only.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

try:
    from .mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products
except ImportError:  # pragma: no cover
    from mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products


PRODUCT_BUNDLES = (".obj.zip", ".shp.zip", ".3dtiles.zip", "_3dtiles.zip")
EXTRA_PRODUCTS = {".dxf", ".fbx", ".gltf", ".dae", ".e57", ".usdz"}


def _infer_date_from_terra(folder: Path) -> str:
    m = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", folder.name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def _parse_terra_metadata_xml(folder: Path) -> dict:
    info: dict = {}
    for xml_path in sorted(folder.rglob("metadata.xml")):
        try:
            root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="ignore"))
            srs = (root.findtext("SRS") or "").strip()
            origin = (root.findtext("SRSOrigin") or "").strip()
            if srs:
                info["srs"] = srs
            if origin:
                info["srs_origin"] = origin
                if srs.upper().startswith("EPSG:4326") or "WGS84" in srs.upper():
                    parts = [float(x) for x in origin.split(",")[:3]]
                    if len(parts) >= 2 and -180 <= parts[0] <= 180 and -90 <= parts[1] <= 90:
                        info["lon"], info["lat"] = parts[0], parts[1]
                        if len(parts) >= 3:
                            info["alt"] = parts[2]
            break
        except Exception:
            continue
    return info


def inspect_djiterra_project(project_folder: Path) -> dict:
    """Inspect a DJI Terra task/mission folder and classify all geospatial deliverables."""
    folder = Path(project_folder).resolve()
    if not folder.is_dir():
        raise SyncError(f"DJI Terra folder not found: {folder}")

    orthos: list[Path] = []
    elevations: list[Path] = []
    tilesets: list[Path] = []
    pointclouds: list[Path] = []
    obj_models: list[Path] = []
    glb_models: list[Path] = []
    splats: list[Path] = []
    vectors: list[Path] = []
    reports: list[Path] = []

    tileset_dirs: set[Path] = set()
    for ts in sorted(folder.rglob("tileset.json")):
        if ts.is_file():
            tilesets.append(ts)
            tileset_dirs.add(ts.parent)

    obj_dirs: set[Path] = set()
    for obj in sorted(folder.rglob("*.obj")):
        if obj.is_file() and not any(obj.is_relative_to(td) for td in tileset_dirs):
            if obj.parent not in obj_dirs:
                obj_dirs.add(obj.parent)
                obj_models.append(obj)

    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        if any(p.is_relative_to(td) for td in tileset_dirs):
            continue
        ext = p.suffix.lower()
        rel_low = p.relative_to(folder).as_posix().lower()
        stem_low = p.stem.lower()

        if ext in {".tif", ".tiff"}:
            if "/images/" in f"/{rel_low}" or "/raw/" in f"/{rel_low}":
                continue
            if any(k in stem_low or f"/{k}" in f"/{rel_low}" for k in ("dsm", "dtm", "dem", "elevation", "height")):
                elevations.append(p)
            else:
                orthos.append(p)
        elif ext in {".las", ".laz", ".e57"}:
            pointclouds.append(p)
        elif ext in {".spz", ".splat", ".ksplat"}:
            splats.append(p)
        elif ext == ".ply":
            with p.open("rb") as fh:
                head = fh.read(65536)
            if b"f_dc_0" in head:
                splats.append(p)
            else:
                pointclouds.append(p)
        elif ext in {".glb", ".gltf", ".fbx"}:
            glb_models.append(p)
        elif ext in {".geojson", ".kml", ".kmz", ".gpkg", ".dxf"}:
            vectors.append(p)
        elif ext == ".pdf":
            reports.append(p)

    terra_meta = _parse_terra_metadata_xml(folder)
    total = (
        len(orthos)
        + len(elevations)
        + len(tilesets)
        + len(pointclouds)
        + len(obj_models)
        + len(glb_models)
        + len(splats)
        + len(vectors)
    )
    return {
        "folder": str(folder),
        "inferred_date": _infer_date_from_terra(folder),
        "metadata": terra_meta,
        "deliverables_count": total,
        "orthophotos": orthos,
        "elevations": elevations,
        "tilesets_3dtiles": tilesets,
        "pointclouds": pointclouds,
        "obj_models": obj_models,
        "glb_models": glb_models,
        "splats": splats,
        "vectors": vectors,
        "reports_pdf": reports,
    }


def stage_djiterra_folder(
    staging_dir: Path,
    terra_folder: Path,
    project_name: str = "",
    *,
    target_project_id: int = 0,
    survey_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
    origin_key: str = "",
    progress=None,
):
    """Stage a DJI Terra reconstruction directory into a verified MosaicEarth snapshot."""
    staging_dir = Path(staging_dir).resolve()
    terra_folder = Path(terra_folder).resolve()
    plan = inspect_djiterra_project(terra_folder)
    if plan["deliverables_count"] == 0 and not plan["reports_pdf"]:
        raise SyncError(f"No DJI Terra deliverables found in {terra_folder}")

    products_dir = staging_dir / "products"
    attachments_dir = staging_dir / "attachments"
    products_dir.mkdir(parents=True, exist_ok=True)
    file_classes: dict[str, str] = {}
    staged = 0

    def _unique_dest(directory: Path, name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        cand = directory / name
        if cand.exists():
            cand = directory / f"{staged}_{name}"
        return cand

    # 1. Orthophotos & DSM/DTM GeoTIFFs (+ sidecar .tfw / .prj)
    for tif_list, cls_name in ((plan["orthophotos"], "orthophoto"), (plan["elevations"], "elevation")):
        for tif in tif_list:
            dest = _unique_dest(products_dir, tif.name if cls_name == "orthophoto" or "dsm" in tif.name.lower() or "dtm" in tif.name.lower() else f"dsm_{tif.name}")
            shutil.copyfile(tif, dest)
            file_classes[dest.relative_to(staging_dir).as_posix()] = cls_name
            staged += 1
            for side_ext in (".tfw", ".prj", ".xml", ".json"):
                side = tif.with_suffix(side_ext)
                if side.is_file():
                    shutil.copyfile(side, _unique_dest(attachments_dir, side.name))

    # 2. DJI Terra B3DM / PNTS 3D Tiles (`terra_b3dm/tileset.json` -> `*_3dtiles.zip`)
    for idx_ts, ts_file in enumerate(plan["tilesets_3dtiles"], start=1):
        ts_root = ts_file.parent
        label = re.sub(r"[^A-Za-z0-9._-]+", "_", ts_root.name or f"terra_tiles_{idx_ts}").strip("_")
        zip_dest = _unique_dest(products_dir, f"{label}_3dtiles.zip")
        with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for sub in sorted(ts_root.rglob("*")):
                if sub.is_file():
                    zf.write(sub, sub.relative_to(ts_root).as_posix())
            meta_xml = ts_root.parent / "metadata.xml"
            if meta_xml.is_file() and "metadata.xml" not in zf.namelist():
                zf.write(meta_xml, "metadata.xml")
        file_classes[zip_dest.relative_to(staging_dir).as_posix()] = "mesh"
        staged += 1

    # 3. Textured OBJ meshes (`terra_obj/*.obj` -> `*.obj.zip`)
    for obj_file in plan["obj_models"]:
        obj_root = obj_file.parent
        zip_dest = _unique_dest(products_dir, f"{obj_file.stem}.obj.zip")
        with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for sub in sorted(obj_root.rglob("*")):
                if sub.is_file() and sub.suffix.lower() in {".obj", ".mtl", ".jpg", ".jpeg", ".png", ".webp", ".xml", ".prj"}:
                    zf.write(sub, sub.relative_to(obj_root).as_posix())
            meta_xml = obj_root.parent / "metadata.xml"
            if meta_xml.is_file() and "metadata.xml" not in zf.namelist():
                zf.write(meta_xml, "metadata.xml")
        file_classes[zip_dest.relative_to(staging_dir).as_posix()] = "mesh"
        staged += 1

    # 4. Point clouds, GLBs, 3DGS & Vectors
    for item_list, cls_name in (
        (plan["pointclouds"], "pointcloud"),
        (plan["glb_models"], "mesh"),
        (plan["splats"], "gaussian"),
        (plan["vectors"], "vector"),
    ):
        for p in item_list:
            dest = _unique_dest(products_dir, p.name)
            shutil.copyfile(p, dest)
            file_classes[dest.relative_to(staging_dir).as_posix()] = cls_name
            staged += 1

    # 5. DJI Terra Quality Reports (`.pdf`)
    for pdf in plan["reports_pdf"]:
        shutil.copyfile(pdf, _unique_dest(attachments_dir, pdf.name))
        staged += 1

    eff_name = (project_name or terra_folder.name or "DJI Terra Survey").strip()[:120]
    eff_date = (survey_date or plan["inferred_date"] or "").strip()[:10]
    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", eff_name).strip("-") or "djiterra-survey"
    eff_origin = origin_key or f"djiterra:{safe_slug}"
    if eff_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff_date):
        eff_origin = f"{eff_origin}:{eff_date}"

    meta: dict = {
        "connector": "djiterra",
        "djiterra": {
            "project_name": eff_name,
            "source_folder": terra_folder.name,
            "srs": plan["metadata"].get("srs", ""),
            "srs_origin": plan["metadata"].get("srs_origin", ""),
        },
        "file_classes": file_classes,
    }
    if "lat" in plan["metadata"] and "lon" in plan["metadata"]:
        meta["georef"] = {
            "lat": plan["metadata"]["lat"],
            "lon": plan["metadata"]["lon"],
            **({"alt": plan["metadata"]["alt"]} if "alt" in plan["metadata"] else {}),
        }
    if int(target_project_id or 0) > 0:
        meta["target_project_id"] = int(target_project_id)
    if int(survey_id or 0) > 0:
        meta["survey_id"] = int(survey_id)
    if survey_name and survey_name.strip():
        meta["survey_name"] = survey_name.strip()[:120]
    if eff_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff_date):
        meta["survey_date"] = eff_date

    def role_for(rel: str) -> str:
        if rel.startswith("images/"):
            return "source"
        if rel.startswith("products/") and (Path(rel).suffix.lower() in (PRODUCTS | EXTRA_PRODUCTS) or rel.lower().endswith(PRODUCT_BUNDLES)):
            return "product"
        return "attachment"

    manifest, paths = build_manifest(staging_dir, eff_origin, eff_name, meta, role_for, progress)
    for item in manifest["files"]:
        cls = file_classes.get(item["path"])
        if cls:
            item["asset_class"] = cls
    return manifest, paths


def watch_djiterra_directory(
    watch_root: Path,
    client: Client,
    *,
    target_project_id: int = 0,
    survey_name: str = "",
    poll_seconds: float = 10.0,
    max_cycles: int = 0,
    on_event=None,
) -> list[dict]:
    """Watch a DJI Terra `PCGSPRO` output folder and push newly completed missions."""
    watch_root = Path(watch_root).resolve()
    if not watch_root.is_dir():
        raise SyncError(f"Watch folder does not exist: {watch_root}")
    state_file = watch_root / ".mosaicearth-djiterra-watched.json"
    try:
        synced = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    except Exception:
        synced = {}

    pushed: list[dict] = []
    cycles = 0
    while True:
        cycles += 1
        candidates = [d for d in sorted(watch_root.iterdir()) if d.is_dir() and not d.name.startswith(".")]
        for cand in candidates:
            try:
                plan = inspect_djiterra_project(cand)
            except SyncError:
                continue
            if plan["deliverables_count"] == 0:
                continue
            sig_items = [
                f"{p.relative_to(cand).as_posix()}:{p.stat().st_size}:{int(p.stat().st_mtime)}"
                for grp in ("orthophotos", "elevations", "tilesets_3dtiles", "pointclouds", "obj_models", "glb_models")
                for p in plan[grp]
            ]
            fingerprint = "|".join(sorted(sig_items))
            if synced.get(cand.name) == fingerprint:
                continue
            if on_event:
                on_event(f"Synchronizing DJI Terra mission '{cand.name}'…")
            with tempfile.TemporaryDirectory(prefix="mosaicearth-terra-watch-") as tmp:
                manifest, paths = stage_djiterra_folder(
                    Path(tmp),
                    cand,
                    cand.name,
                    target_project_id=target_project_id,
                    survey_name=survey_name,
                )
                res = client.push(manifest, paths)
            synced[cand.name] = fingerprint
            state_file.write_text(json.dumps(synced, indent=2), encoding="utf-8")
            pushed.append({"folder": cand.name, "snapshot_id": res["id"], "project_id": res["project_id"], "task_id": res.get("task_id")})
            if on_event:
                on_event(f"Uploaded '{cand.name}' -> Project #{res['project_id']}")
        if max_cycles > 0 and cycles >= max_cycles:
            break
        time.sleep(max(1.0, float(poll_seconds)))
    return pushed


def pull_cloud_for_djiterra(client: Client, destination: Path, *, snapshot_id: str = "", cloud_project_id: int = 0, progress=None) -> dict:
    """Download a MosaicEarth Cloud project or snapshot locally."""
    sid = snapshot_id
    if not sid:
        if int(cloud_project_id or 0) <= 0:
            raise SyncError("Select a Cloud project or snapshot ID to pull.")
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
    for item in state["manifest"]["files"]:
        f = target_dir / item["path"]
        if f.name.lower().endswith((".obj.zip", "_3dtiles.zip", ".3dtiles.zip")):
            extract_products(f, f.with_suffix(""))
    return {"snapshot_id": sid, "name": state["manifest"]["name"], "directory": target_dir}


def _apply_dark_mosaic_theme(root, ttk):
    root.configure(bg="#0f1812")
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", background="#15221a", foreground="#e9f1eb", fieldbackground="#0d1610", bordercolor="#35523b")
    style.configure("TLabelframe", background="#15221a", foreground="#b6e893", bordercolor="#35523b")
    style.configure("TLabelframe.Label", background="#15221a", foreground="#b6e893", font=("Segoe UI", 10, "bold"))
    style.configure("TLabel", background="#15221a", foreground="#e9f1eb")
    style.configure("TFrame", background="#0f1812")
    style.configure("TButton", background="#284625", foreground="#eff8e9", bordercolor="#72915c", padding=(10, 6))
    style.map("TButton", background=[("active", "#366032")])
    style.configure("Accent.TButton", background="#99cc00", foreground="#0c150f", font=("Segoe UI", 9, "bold"), padding=(12, 6))
    style.map("Accent.TButton", background=[("active", "#b3e619")])


def launch_gui():
    """Cross-platform dark MosaicEarth GUI for DJI Terra synchronization & Watch Folder."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    session = {"token": os.environ.get("MOSAICEARTH_TOKEN", ""), "email": "", "origin": ORIGIN, "watching": False}
    root = tk.Tk()
    root.title("MosaicEarth · DJI Terra Connector (v0.4.0)")
    root.geometry("720x590")
    _apply_dark_mosaic_theme(root, ttk)

    pad = {"padx": 12, "pady": 6}
    frm_auth = ttk.LabelFrame(root, text="1. MosaicEarth Cloud Account")
    frm_auth.pack(fill="x", **pad)

    var_origin = tk.StringVar(value=ORIGIN)
    var_email = tk.StringVar(value=os.environ.get("MOSAICEARTH_EMAIL", ""))
    var_pass = tk.StringVar()
    var_auth = tk.StringVar(value="Token active" if session["token"] else "Sign in with your MosaicEarth account")

    ttk.Label(frm_auth, text="Cloud Origin:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_auth, textvariable=var_origin, width=46).grid(row=0, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_auth, text="Email:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_auth, textvariable=var_email, width=46).grid(row=1, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_auth, text="Password:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_auth, textvariable=var_pass, show="*", width=46).grid(row=2, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_auth, textvariable=var_auth).grid(row=3, column=1, sticky="w", padx=8, pady=4)

    frm_cfg = ttk.LabelFrame(root, text="2. DJI Terra Mission Folder & 4D Survey Routing")
    frm_cfg.pack(fill="x", **pad)

    cb_proj = ttk.Combobox(frm_cfg, state="readonly", width=48)
    cb_proj["values"] = ["0: Create / Match Cloud Project Automatically"]
    cb_proj.current(0)

    var_folder = tk.StringVar()
    var_name = tk.StringVar(value="DJI Terra Mission")
    var_sname = tk.StringVar()
    var_sdate = tk.StringVar()
    var_summary = tk.StringVar(value="Select a DJI Terra mission directory (map/, models/pc/, report/)…")

    def browse():
        chosen = filedialog.askdirectory(title="Select DJI Terra Mission or PCGSPRO Folder")
        if chosen:
            var_folder.set(chosen)
            p = Path(chosen)
            var_name.set(p.name)
            try:
                plan = inspect_djiterra_project(p)
                if plan["inferred_date"] and not var_sdate.get():
                    var_sdate.set(plan["inferred_date"])
                var_summary.set(
                    f"Detected {plan['deliverables_count']} deliverables: "
                    f"{len(plan['orthophotos'])} ortho, {len(plan['elevations'])} DSM, "
                    f"{len(plan['tilesets_3dtiles'])} 3D Tiles, {len(plan['pointclouds'])} LiDAR/PC, "
                    f"{len(plan['obj_models'])} OBJ, {len(plan['reports_pdf'])} PDF reports."
                )
            except Exception as exc:
                var_summary.set(str(exc))

    ttk.Label(frm_cfg, text="Cloud Project:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
    cb_proj.grid(row=0, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="DJI Terra Folder:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_folder, width=42).grid(row=1, column=1, sticky="we", padx=8, pady=4)
    ttk.Button(frm_cfg, text="Browse…", command=browse).grid(row=1, column=2, padx=6, pady=4)
    ttk.Label(frm_cfg, text="Task Name:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_name, width=46).grid(row=2, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="4D Survey Name:").grid(row=3, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_sname, width=46).grid(row=3, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="4D Date (YYYY-MM-DD):").grid(row=4, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_sdate, width=22).grid(row=4, column=1, sticky="w", padx=8, pady=4)
    ttk.Label(frm_cfg, textvariable=var_summary, wraplength=660).grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=6)

    var_status = tk.StringVar(value="Ready.")
    ttk.Label(root, textvariable=var_status, wraplength=680).pack(fill="x", padx=14, pady=8)

    def refresh_projects():
        client = Client(token=session["token"], origin=session["origin"])
        projs = client.request("GET", "/api/v2/sync/projects").get("projects", [])
        cb_proj["values"] = ["0: Create / Match Cloud Project Automatically"] + [f"{p['id']}: {p['name']}" for p in projs]
        cb_proj.current(0)

    def sign_in():
        try:
            client = Client(origin=var_origin.get().strip())
            user = client.signin(var_email.get().strip(), var_pass.get())
            var_pass.set("")
            session.update(token=client.token, email=user.get("email") or var_email.get().strip(), origin=client.origin)
            var_auth.set(f"Signed in as {session['email']}")
            refresh_projects()
        except Exception as exc:
            messagebox.showerror("Sign-In Error", str(exc))

    ttk.Button(frm_auth, text="Sign In", command=sign_in).grid(row=3, column=0, padx=8, pady=4)

    def push_now():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        folder = var_folder.get().strip()
        if not folder:
            messagebox.showwarning("MosaicEarth", "Choose a DJI Terra mission folder.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="mosaicearth-terra-gui-") as tmp:
                    manifest, paths = stage_djiterra_folder(
                        Path(tmp),
                        Path(folder),
                        var_name.get().strip() or "DJI Terra Mission",
                        target_project_id=pid,
                        survey_name=var_sname.get().strip(),
                        survey_date=var_sdate.get().strip(),
                        progress=lambda d, t, m: var_status.set(m),
                    )
                    client = Client(
                        token=session["token"],
                        origin=session["origin"],
                        progress=lambda d, t, m: var_status.set(f"{m} ({int(100 * d / max(t, 1))}%)"),
                    )
                    res = client.push(manifest, paths)
                    var_status.set(f"Uploaded to MyEarth Project #{res['project_id']} (Task #{res.get('task_id')})")
            except Exception as exc:
                var_status.set(f"Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def toggle_watch():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        folder = var_folder.get().strip()
        if not folder:
            messagebox.showwarning("MosaicEarth", "Select your DJI Terra PCGSPRO directory first.")
            return
        session["watching"] = not session["watching"]
        if not session["watching"]:
            var_status.set("Watch mode stopped.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])
        var_status.set(f"Watching {folder} for completed DJI Terra missions…")

        def loop():
            client = Client(token=session["token"], origin=session["origin"])
            while session["watching"]:
                try:
                    watch_djiterra_directory(
                        Path(folder),
                        client,
                        target_project_id=pid,
                        survey_name=var_sname.get().strip(),
                        max_cycles=1,
                        on_event=var_status.set,
                    )
                except Exception as exc:
                    var_status.set(f"Watch warning: {exc}")
                time.sleep(8)

        threading.Thread(target=loop, daemon=True).start()

    act = ttk.Frame(root)
    act.pack(fill="x", padx=12, pady=8)
    ttk.Button(act, text="Synchronize Mission to MyEarth", style="Accent.TButton", command=push_now).pack(side="left", padx=4)
    ttk.Button(act, text="Toggle Watch Folder (PCGSPRO)", command=toggle_watch).pack(side="left", padx=4)
    root.mainloop()


def main(argv=None):
    if argv is None and len(sys.argv) == 1:
        launch_gui()
        return

    parser = argparse.ArgumentParser(description="MosaicEarth DJI Terra Connector & Watch-Folder CLI (v0.4.0)")
    parser.add_argument("--gui", action="store_true", help="Launch dark desktop GUI")
    parser.add_argument("--origin", default=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", ORIGIN))
    parser.add_argument("--email", default=os.environ.get("MOSAICEARTH_EMAIL", ""))
    sub = parser.add_subparsers(dest="cmd")

    p_ins = sub.add_parser("inspect", help="Inspect a DJI Terra mission folder without uploading")
    p_ins.add_argument("folder", type=Path)

    p_push = sub.add_parser("push-folder", help="Synchronize a DJI Terra mission folder to MyEarth")
    p_push.add_argument("folder", type=Path)
    p_push.add_argument("--name", default="")
    p_push.add_argument("--project-id", type=int, default=0)
    p_push.add_argument("--survey-name", default="")
    p_push.add_argument("--survey-date", default="")

    p_watch = sub.add_parser("watch", help="Watch a DJI Terra PCGSPRO folder and auto-upload finished missions")
    p_watch.add_argument("folder", type=Path)
    p_watch.add_argument("--project-id", type=int, default=0)
    p_watch.add_argument("--survey-name", default="")
    p_watch.add_argument("--poll-seconds", type=float, default=10.0)
    p_watch.add_argument("--once", action="store_true")

    p_pull = sub.add_parser("pull", help="Download a MyEarth project or snapshot locally")
    p_pull.add_argument("--dest", type=Path, required=True)
    p_pull.add_argument("--snapshot-id", default="")
    p_pull.add_argument("--project-id", type=int, default=0)

    args = parser.parse_args(argv)
    if args.gui or not args.cmd:
        launch_gui()
        return

    if args.cmd == "inspect":
        plan = inspect_djiterra_project(args.folder)
        serializable = {
            k: ([str(x) for x in v] if isinstance(v, list) else v)
            for k, v in plan.items()
        }
        print(json.dumps({"ok": True, **serializable}, indent=2))
        return

    token = os.environ.get("MOSAICEARTH_TOKEN", "")
    client = Client(token=token, origin=args.origin, progress=lambda d, t, m: print(f"[{d}/{t}] {m}", flush=True))
    if not client.token:
        email = args.email or input("MosaicEarth email: ").strip()
        password = os.environ.get("MOSAICEARTH_PASSWORD") or getpass.getpass("MosaicEarth password: ")
        client.signin(email, password)

    if args.cmd == "push-folder":
        with tempfile.TemporaryDirectory(prefix="mosaicearth-terra-") as tmp:
            manifest, paths = stage_djiterra_folder(
                Path(tmp),
                args.folder,
                args.name.strip() or args.folder.resolve().name,
                target_project_id=args.project_id,
                survey_name=args.survey_name,
                survey_date=args.survey_date,
            )
            state = client.push(manifest, paths)
            print(json.dumps({"ok": True, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}, indent=2))
    elif args.cmd == "watch":
        pushed = watch_djiterra_directory(
            args.folder,
            client,
            target_project_id=args.project_id,
            survey_name=args.survey_name,
            poll_seconds=args.poll_seconds,
            max_cycles=1 if args.once else 0,
            on_event=lambda msg: print(msg, flush=True),
        )
        print(json.dumps({"ok": True, "pushed": pushed}, indent=2))
    elif args.cmd == "pull":
        res = pull_cloud_for_djiterra(client, args.dest, snapshot_id=args.snapshot_id, cloud_project_id=args.project_id, progress=client.progress)
        print(json.dumps({"ok": True, "snapshot_id": res["snapshot_id"], "directory": str(res["directory"])}, indent=2))


if __name__ == "__main__":
    main()
