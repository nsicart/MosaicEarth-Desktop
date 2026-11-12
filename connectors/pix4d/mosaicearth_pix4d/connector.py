"""MosaicEarth PIX4Dmapper & PIX4Dmatic Connector, Watch-Folder & Dark GUI (0.4.0).

Auto-detects both classic PIX4Dmapper hierarchies (`1_initial/report/*.pdf`,
`2_densification/point_cloud/*.laz`, `2_densification/3d_mesh/*.obj|*.fbx`,
`3_dsm_ortho/1_dsm/*.tif`, `3_dsm_ortho/2_mosaic/*.tif`, contours `.shp`/`.dxf`)
and modern PIX4Dmatic workspaces (`exports/` COPC point clouds, `.spz`/`.ply`
3D Gaussian Splats, ortho/DSM GeoTIFFs, 3D Tiles, and PDF quality reports).

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
import zipfile
from pathlib import Path

try:
    from .mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products
except ImportError:  # pragma: no cover
    from mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products


PRODUCT_BUNDLES = (".obj.zip", ".shp.zip", ".3dtiles.zip", "_3dtiles.zip")
EXTRA_PRODUCTS = {".dxf", ".fbx", ".gltf", ".dae", ".e57", ".usdz"}


def inspect_pix4d_project(project_folder: Path) -> dict:
    """Inspect a PIX4Dmapper or PIX4Dmatic project/export folder and classify outputs."""
    folder = Path(project_folder).resolve()
    if not folder.is_dir():
        raise SyncError(f"PIX4D folder not found: {folder}")

    has_p4m = any(folder.glob("*.p4m")) or any((folder / "exports").glob("*")) if (folder / "exports").is_dir() else any(folder.glob("*.p4m"))
    has_p4d = any(folder.glob("*.p4d")) or any((folder / d).is_dir() for d in ("1_initial", "2_densification", "3_dsm_ortho"))
    edition = "pix4dmatic" if has_p4m and not has_p4d else ("pix4dmapper" if has_p4d else "pix4d")

    orthos: list[Path] = []
    elevations: list[Path] = []
    pointclouds: list[Path] = []
    obj_models: list[Path] = []
    meshes: list[Path] = []
    tilesets: list[Path] = []
    splats: list[Path] = []
    shapefiles: list[Path] = []
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
        rel_low = p.relative_to(folder).as_posix().lower()
        # Skip PIX4Dmapper internal tiles (`3_dsm_ortho/2_mosaic/tiles/`, `1_dsm/tiles/`, `google_tiles/`)
        if "/tiles/" in f"/{rel_low}" or "/google_tiles/" in f"/{rel_low}" or "/temp/" in f"/{rel_low}":
            continue
        ext = p.suffix.lower()
        stem_low = p.stem.lower()

        if ext in {".tif", ".tiff"}:
            if any(k in stem_low or f"/{k}" in f"/{rel_low}" for k in ("1_dsm", "dsm", "dtm", "dem", "elevation")):
                elevations.append(p)
            elif "reflectance" not in stem_low:
                orthos.append(p)
        elif ext in {".las", ".laz", ".e57"}:
            pointclouds.append(p)
        elif ext in {".spz", ".splat", ".ksplat"}:
            splats.append(p)
        elif ext == ".ply":
            with p.open("rb") as fh:
                is_gs = b"f_dc_0" in fh.read(65536)
            (splats if is_gs else pointclouds).append(p)
        elif ext in {".glb", ".gltf", ".fbx"}:
            meshes.append(p)
        elif ext == ".shp":
            shapefiles.append(p)
        elif ext in {".geojson", ".gpkg", ".kml", ".kmz", ".dxf"}:
            vectors.append(p)
        elif ext == ".pdf":
            reports.append(p)

    m_date = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", folder.name)
    inferred_date = f"{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}" if m_date else ""

    total = (
        len(orthos)
        + len(elevations)
        + len(pointclouds)
        + len(obj_models)
        + len(meshes)
        + len(tilesets)
        + len(splats)
        + len(shapefiles)
        + len(vectors)
    )
    return {
        "folder": str(folder),
        "edition": edition,
        "inferred_date": inferred_date,
        "deliverables_count": total,
        "orthophotos": orthos,
        "elevations": elevations,
        "pointclouds": pointclouds,
        "obj_models": obj_models,
        "meshes": meshes,
        "tilesets_3dtiles": tilesets,
        "splats": splats,
        "shapefiles": shapefiles,
        "vectors": vectors,
        "reports_pdf": reports,
    }


def stage_pix4d_folder(
    staging_dir: Path,
    pix4d_folder: Path,
    project_name: str = "",
    *,
    target_project_id: int = 0,
    survey_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
    origin_key: str = "",
    progress=None,
):
    """Stage a PIX4Dmapper or PIX4Dmatic workspace into a verified MosaicEarth snapshot."""
    staging_dir = Path(staging_dir).resolve()
    pix4d_folder = Path(pix4d_folder).resolve()
    plan = inspect_pix4d_project(pix4d_folder)
    if plan["deliverables_count"] == 0 and not plan["reports_pdf"]:
        raise SyncError(f"No PIX4Dmapper or PIX4Dmatic deliverables found in {pix4d_folder}")

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

    for tif_list, cls_name in ((plan["orthophotos"], "orthophoto"), (plan["elevations"], "elevation")):
        for tif in tif_list:
            dest = _unique_dest(products_dir, tif.name)
            shutil.copyfile(tif, dest)
            file_classes[dest.relative_to(staging_dir).as_posix()] = cls_name
            staged += 1
            for side_ext in (".tfw", ".prj", ".xml"):
                side = tif.with_suffix(side_ext)
                if side.is_file():
                    shutil.copyfile(side, _unique_dest(attachments_dir, side.name))

    for obj_file in plan["obj_models"]:
        zip_dest = _unique_dest(products_dir, f"{obj_file.stem}.obj.zip")
        with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for sib in sorted(obj_file.parent.iterdir()):
                if sib.is_file() and sib.suffix.lower() in {".obj", ".mtl", ".jpg", ".jpeg", ".png", ".webp", ".prj", ".xyz"}:
                    zf.write(sib, sib.name)
        file_classes[zip_dest.relative_to(staging_dir).as_posix()] = "mesh"
        staged += 1

    for idx_ts, ts_file in enumerate(plan["tilesets_3dtiles"], start=1):
        ts_root = ts_file.parent
        label = re.sub(r"[^A-Za-z0-9._-]+", "_", ts_root.name or f"pix4d_tiles_{idx_ts}").strip("_")
        zip_dest = _unique_dest(products_dir, f"{label}_3dtiles.zip")
        with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for sub in sorted(ts_root.rglob("*")):
                if sub.is_file():
                    zf.write(sub, sub.relative_to(ts_root).as_posix())
        file_classes[zip_dest.relative_to(staging_dir).as_posix()] = "mesh"
        staged += 1

    for shp_file in plan["shapefiles"]:
        zip_dest = _unique_dest(products_dir, f"{shp_file.stem}.shp.zip")
        with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qpj"):
                cand = shp_file.with_suffix(ext)
                if cand.is_file():
                    zf.write(cand, cand.name)
        file_classes[zip_dest.relative_to(staging_dir).as_posix()] = "vector"
        staged += 1

    for item_list, cls_name in (
        (plan["pointclouds"], "pointcloud"),
        (plan["meshes"], "mesh"),
        (plan["splats"], "gaussian"),
        (plan["vectors"], "vector"),
    ):
        for p in item_list:
            dest = _unique_dest(products_dir, p.name)
            shutil.copyfile(p, dest)
            file_classes[dest.relative_to(staging_dir).as_posix()] = cls_name
            staged += 1

    for pdf in plan["reports_pdf"]:
        shutil.copyfile(pdf, _unique_dest(attachments_dir, pdf.name))
        staged += 1

    for proj_meta in sorted(pix4d_folder.glob("*.p4d")) + sorted(pix4d_folder.glob("*.p4m")):
        if proj_meta.is_file() and proj_meta.stat().st_size < 32 * 1024 * 1024:
            shutil.copyfile(proj_meta, _unique_dest(attachments_dir, proj_meta.name))

    eff_name = (project_name or pix4d_folder.name or "PIX4D Survey").strip()[:120]
    eff_date = (survey_date or plan["inferred_date"] or "").strip()[:10]
    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", eff_name).strip("-") or "pix4d-survey"
    eff_origin = origin_key or f"{plan['edition']}:{safe_slug}"
    if eff_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff_date):
        eff_origin = f"{eff_origin}:{eff_date}"

    meta: dict = {
        "connector": plan["edition"],
        "pix4d": {
            "edition": plan["edition"],
            "project_name": eff_name,
            "source_folder": pix4d_folder.name,
        },
        "file_classes": file_classes,
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


def watch_pix4d_directory(
    watch_root: Path,
    client: Client,
    *,
    target_project_id: int = 0,
    survey_name: str = "",
    poll_seconds: float = 10.0,
    max_cycles: int = 0,
    on_event=None,
) -> list[dict]:
    """Watch a PIX4D projects directory and upload newly finished reconstructions."""
    watch_root = Path(watch_root).resolve()
    if not watch_root.is_dir():
        raise SyncError(f"Watch folder does not exist: {watch_root}")
    state_file = watch_root / ".mosaicearth-pix4d-watched.json"
    try:
        synced = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    except Exception:
        synced = {}

    pushed: list[dict] = []
    cycles = 0
    while True:
        cycles += 1
        for cand in sorted(d for d in watch_root.iterdir() if d.is_dir() and not d.name.startswith(".")):
            try:
                plan = inspect_pix4d_project(cand)
            except SyncError:
                continue
            if plan["deliverables_count"] == 0:
                continue
            sig = "|".join(
                sorted(
                    f"{p.relative_to(cand).as_posix()}:{p.stat().st_size}"
                    for grp in ("orthophotos", "elevations", "pointclouds", "obj_models", "meshes", "splats", "tilesets_3dtiles")
                    for p in plan[grp]
                )
            )
            if synced.get(cand.name) == sig:
                continue
            if on_event:
                on_event(f"Synchronizing {plan['edition']} project '{cand.name}'…")
            with tempfile.TemporaryDirectory(prefix="mosaicearth-pix4d-watch-") as tmp:
                manifest, paths = stage_pix4d_folder(
                    Path(tmp),
                    cand,
                    cand.name,
                    target_project_id=target_project_id,
                    survey_name=survey_name,
                )
                res = client.push(manifest, paths)
            synced[cand.name] = sig
            state_file.write_text(json.dumps(synced, indent=2), encoding="utf-8")
            pushed.append({"folder": cand.name, "edition": plan["edition"], "snapshot_id": res["id"], "project_id": res["project_id"], "task_id": res.get("task_id")})
            if on_event:
                on_event(f"Uploaded '{cand.name}' -> Project #{res['project_id']}")
        if max_cycles > 0 and cycles >= max_cycles:
            break
        time.sleep(max(1.0, float(poll_seconds)))
    return pushed


def pull_cloud_for_pix4d(client: Client, destination: Path, *, snapshot_id: str = "", cloud_project_id: int = 0, progress=None) -> dict:
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
        if f.name.lower().endswith((".obj.zip", ".shp.zip", "_3dtiles.zip")):
            extract_products(f, f.with_suffix(""))
    return {"snapshot_id": sid, "name": state["manifest"]["name"], "directory": target_dir}


def _apply_dark_theme(root, ttk):
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
    """Dark MosaicEarth desktop GUI for PIX4Dmapper & PIX4Dmatic."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    session = {"token": os.environ.get("MOSAICEARTH_TOKEN", ""), "email": "", "origin": ORIGIN, "watching": False}
    root = tk.Tk()
    root.title("MosaicEarth · PIX4Dmapper & PIX4Dmatic Connector (v0.4.0)")
    root.geometry("730x590")
    _apply_dark_theme(root, ttk)

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

    frm_cfg = ttk.LabelFrame(root, text="2. PIX4Dmapper / PIX4Dmatic Project & 4D Survey Routing")
    frm_cfg.pack(fill="x", **pad)

    cb_proj = ttk.Combobox(frm_cfg, state="readonly", width=48)
    cb_proj["values"] = ["0: Create / Match Cloud Project Automatically"]
    cb_proj.current(0)

    var_folder = tk.StringVar()
    var_name = tk.StringVar(value="PIX4D Survey")
    var_sname = tk.StringVar()
    var_sdate = tk.StringVar()
    var_summary = tk.StringVar(value="Select a PIX4Dmapper (1_initial/2_densification/3_dsm_ortho) or PIX4Dmatic (exports/) folder…")

    def browse():
        chosen = filedialog.askdirectory(title="Select PIX4D Project or Exports Folder")
        if chosen:
            var_folder.set(chosen)
            p = Path(chosen)
            var_name.set(p.name)
            try:
                plan = inspect_pix4d_project(p)
                if plan["inferred_date"] and not var_sdate.get():
                    var_sdate.set(plan["inferred_date"])
                var_summary.set(
                    f"[{plan['edition'].upper()}] {plan['deliverables_count']} deliverables: "
                    f"{len(plan['orthophotos'])} ortho, {len(plan['elevations'])} DSM, "
                    f"{len(plan['pointclouds'])} PC, {len(plan['splats'])} 3DGS, "
                    f"{len(plan['obj_models']) + len(plan['meshes'])} meshes, {len(plan['reports_pdf'])} PDF reports."
                )
            except Exception as exc:
                var_summary.set(str(exc))

    ttk.Label(frm_cfg, text="Cloud Project:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
    cb_proj.grid(row=0, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="PIX4D Folder:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_folder, width=42).grid(row=1, column=1, sticky="we", padx=8, pady=4)
    ttk.Button(frm_cfg, text="Browse…", command=browse).grid(row=1, column=2, padx=6, pady=4)
    ttk.Label(frm_cfg, text="Task Name:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_name, width=46).grid(row=2, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="4D Survey Name:").grid(row=3, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_sname, width=46).grid(row=3, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="4D Date (YYYY-MM-DD):").grid(row=4, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_sdate, width=22).grid(row=4, column=1, sticky="w", padx=8, pady=4)
    ttk.Label(frm_cfg, textvariable=var_summary, wraplength=670).grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=6)

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
            messagebox.showwarning("MosaicEarth", "Choose a PIX4D project folder.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="mosaicearth-pix4d-gui-") as tmp:
                    manifest, paths = stage_pix4d_folder(
                        Path(tmp),
                        Path(folder),
                        var_name.get().strip() or "PIX4D Survey",
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

    act = ttk.Frame(root)
    act.pack(fill="x", padx=12, pady=8)
    ttk.Button(act, text="Synchronize PIX4D Project to MyEarth", style="Accent.TButton", command=push_now).pack(side="left", padx=4)
    root.mainloop()


def main(argv=None):
    if argv is None and len(sys.argv) == 1:
        launch_gui()
        return

    parser = argparse.ArgumentParser(description="MosaicEarth PIX4Dmapper & PIX4Dmatic Connector CLI (v0.4.0)")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--origin", default=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", ORIGIN))
    parser.add_argument("--email", default=os.environ.get("MOSAICEARTH_EMAIL", ""))
    sub = parser.add_subparsers(dest="cmd")

    p_ins = sub.add_parser("inspect")
    p_ins.add_argument("folder", type=Path)

    p_push = sub.add_parser("push-folder")
    p_push.add_argument("folder", type=Path)
    p_push.add_argument("--name", default="")
    p_push.add_argument("--project-id", type=int, default=0)
    p_push.add_argument("--survey-name", default="")
    p_push.add_argument("--survey-date", default="")

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("folder", type=Path)
    p_watch.add_argument("--project-id", type=int, default=0)
    p_watch.add_argument("--survey-name", default="")
    p_watch.add_argument("--poll-seconds", type=float, default=10.0)
    p_watch.add_argument("--once", action="store_true")

    p_pull = sub.add_parser("pull")
    p_pull.add_argument("--dest", type=Path, required=True)
    p_pull.add_argument("--snapshot-id", default="")
    p_pull.add_argument("--project-id", type=int, default=0)

    args = parser.parse_args(argv)
    if args.gui or not args.cmd:
        launch_gui()
        return

    if args.cmd == "inspect":
        plan = inspect_pix4d_project(args.folder)
        serializable = {k: ([str(x) for x in v] if isinstance(v, list) else v) for k, v in plan.items()}
        print(json.dumps({"ok": True, **serializable}, indent=2))
        return

    token = os.environ.get("MOSAICEARTH_TOKEN", "")
    client = Client(token=token, origin=args.origin, progress=lambda d, t, m: print(f"[{d}/{t}] {m}", flush=True))
    if not client.token:
        email = args.email or input("MosaicEarth email: ").strip()
        password = os.environ.get("MOSAICEARTH_PASSWORD") or getpass.getpass("MosaicEarth password: ")
        client.signin(email, password)

    if args.cmd == "push-folder":
        with tempfile.TemporaryDirectory(prefix="mosaicearth-pix4d-") as tmp:
            manifest, paths = stage_pix4d_folder(
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
        pushed = watch_pix4d_directory(
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
        res = pull_cloud_for_pix4d(client, args.dest, snapshot_id=args.snapshot_id, cloud_project_id=args.project_id, progress=client.progress)
        print(json.dumps({"ok": True, "snapshot_id": res["snapshot_id"], "directory": str(res["directory"])}, indent=2))


if __name__ == "__main__":
    main()
