"""MosaicEarth ArcGIS Pro Connector & Live OGC WMTS/KML Client (0.4.0).

Provides pure-Python GIS staging (GeoTIFF Ortho/DSM, Shapefile -> `.shp.zip`,
GeoPackage `.gpkg`, GeoJSON, CAD `.dxf`, `.las`/`.laz`, `.glb`/`.obj`, 3D Tiles),
live OGC `WMTS 1.0.0` / `XYZ` / Google Earth `NetworkLink .KML` discovery for
ArcGIS Pro maps & scenes, and verified snapshot downloads.

AGPL-3.0-or-later. Python standard library only (zero `arcpy` dependency outside ArcGIS Pro).
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
import urllib.parse
import zipfile
from pathlib import Path

try:
    from .mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products
except ImportError:  # pragma: no cover
    from mosaicearth_sync import ORIGIN, PRODUCTS, Client, SyncError, build_manifest, extract_products


PRODUCT_BUNDLES = (".obj.zip", ".shp.zip", ".3dtiles.zip", "_3dtiles.zip")
EXTRA_PRODUCTS = {".dxf", ".fbx", ".gltf", ".dae", ".e57", ".usdz"}


def stage_arcgis_deliverables(
    staging_dir: Path,
    inputs: list[Path] | Path,
    project_name: str = "ArcGIS Pro Layer",
    *,
    layer_kind: str = "auto",
    target_project_id: int = 0,
    survey_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
    origin_key: str = "",
    progress=None,
):
    """Stage one or more ArcGIS Pro files/folders into a verified MosaicEarth snapshot."""
    staging_dir = Path(staging_dir).resolve()
    raw_list = [inputs] if isinstance(inputs, (str, Path)) else list(inputs)
    if not raw_list:
        raise SyncError("Provide at least one GIS layer, raster, CAD .DXF, point cloud, or folder.")

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

    def _classify_single_file(file_path: Path):
        nonlocal staged
        ext = file_path.suffix.lower()
        stem_low = file_path.stem.lower()
        if ext == ".shp":
            zip_dest = _unique_dest(products_dir, f"{file_path.stem}.shp.zip")
            with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for s_ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".xml"):
                    sib = file_path.with_suffix(s_ext)
                    if sib.is_file():
                        zf.write(sib, sib.name)
            file_classes[zip_dest.relative_to(staging_dir).as_posix()] = "vector"
            staged += 1
            return
        if ext == ".obj":
            zip_dest = _unique_dest(products_dir, f"{file_path.stem}.obj.zip")
            with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for sib in sorted(file_path.parent.iterdir()):
                    if sib.is_file() and sib.suffix.lower() in {".obj", ".mtl", ".jpg", ".jpeg", ".png", ".webp", ".prj"}:
                        zf.write(sib, sib.name)
            file_classes[zip_dest.relative_to(staging_dir).as_posix()] = "mesh"
            staged += 1
            return
        if file_path.name.lower() == "tileset.json":
            ts_root = file_path.parent
            label = re.sub(r"[^A-Za-z0-9._-]+", "_", ts_root.name or "arcgis_3dtiles").strip("_")
            zip_dest = _unique_dest(products_dir, f"{label}_3dtiles.zip")
            with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for sub in sorted(ts_root.rglob("*")):
                    if sub.is_file():
                        zf.write(sub, sub.relative_to(ts_root).as_posix())
            file_classes[zip_dest.relative_to(staging_dir).as_posix()] = "mesh"
            staged += 1
            return

        if ext in (PRODUCTS | EXTRA_PRODUCTS) or file_path.name.lower().endswith(PRODUCT_BUNDLES):
            dest = _unique_dest(products_dir, file_path.name)
            shutil.copyfile(file_path, dest)
            rel_key = dest.relative_to(staging_dir).as_posix()
            if layer_kind in {"orthophoto", "elevation", "pointcloud", "mesh", "gaussian", "vector"}:
                file_classes[rel_key] = layer_kind
            elif ext in {".tif", ".tiff"}:
                is_elev = any(k in stem_low for k in ("dsm", "dtm", "dem", "elev", "hillshade", "height"))
                file_classes[rel_key] = "elevation" if is_elev else "orthophoto"
                for side_ext in (".tfw", ".prj", ".aux.xml", ".xml"):
                    cand_side = Path(str(file_path) + side_ext) if side_ext.startswith(".aux") else file_path.with_suffix(side_ext)
                    if cand_side.is_file():
                        shutil.copyfile(cand_side, _unique_dest(attachments_dir, cand_side.name))
            elif ext in {".geojson", ".gpkg", ".kml", ".kmz", ".gpx", ".dxf"} or rel_key.endswith(".shp.zip"):
                file_classes[rel_key] = "vector"
            elif ext in {".las", ".laz", ".e57"}:
                file_classes[rel_key] = "pointcloud"
            elif ext in {".spz", ".splat", ".ksplat"}:
                file_classes[rel_key] = "gaussian"
            elif ext == ".ply":
                with dest.open("rb") as fh:
                    is_gs = b"f_dc_0" in fh.read(65536)
                file_classes[rel_key] = "gaussian" if is_gs else "pointcloud"
            elif ext in {".glb", ".gltf", ".fbx", ".dae", ".usdz"} or rel_key.endswith((".obj.zip", "_3dtiles.zip", ".3dtiles.zip")):
                file_classes[rel_key] = "mesh"
            staged += 1
        elif ext in {".pdf", ".lyrx", ".stylx", ".prj", ".xml", ".json", ".csv"}:
            shutil.copyfile(file_path, _unique_dest(attachments_dir, file_path.name))
            staged += 1

    for raw in raw_list:
        p = Path(raw).resolve()
        if not p.exists():
            raise SyncError(f"Input path not found: {p}")
        if p.is_file():
            _classify_single_file(p)
        elif p.is_dir():
            if (p / "tileset.json").is_file():
                _classify_single_file(p / "tileset.json")
                continue
            bundled_shp_stems: set[tuple[Path, str]] = set()
            bundled_obj_dirs: set[Path] = set()
            ts_dirs = {ts.parent for ts in p.rglob("tileset.json")}
            for ts in sorted(p.rglob("tileset.json")):
                _classify_single_file(ts)
            for sub in sorted(p.rglob("*")):
                if not sub.is_file() or any(sub.is_relative_to(td) for td in ts_dirs):
                    continue
                se = sub.suffix.lower()
                if se == ".shp":
                    bundled_shp_stems.add((sub.parent, sub.stem.lower()))
                    _classify_single_file(sub)
                elif (sub.parent, sub.stem.lower()) in bundled_shp_stems and se in {".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx"}:
                    continue
                elif se == ".obj":
                    if sub.parent not in bundled_obj_dirs:
                        bundled_obj_dirs.add(sub.parent)
                        _classify_single_file(sub)
                elif sub.parent in bundled_obj_dirs and se in {".mtl", ".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                else:
                    _classify_single_file(sub)

    if staged == 0:
        raise SyncError("No supported ArcGIS Pro layers or geospatial deliverables found.")

    eff_name = (project_name or "ArcGIS Pro Layer").strip()[:120]
    eff_date = (survey_date or "").strip()[:10]
    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", eff_name).strip("-") or "arcgispro-layer"
    eff_origin = origin_key or f"arcgispro:{safe_slug}"
    if eff_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff_date):
        eff_origin = f"{eff_origin}:{eff_date}"

    meta: dict = {
        "connector": "arcgispro",
        "arcgispro": {"project_name": eff_name, "layer_kind": layer_kind},
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


def fetch_live_ogc_links(
    client: Client,
    *,
    project_id: int = 0,
    asset_id: int = 0,
    mymap_id: int = 0,
    save_kml_to: Path | None = None,
) -> dict:
    """Mint live read-only OGC WMTS 1.0.0, Slippy XYZ, and NetworkLink .KML URLs for ArcGIS Pro."""
    params = {}
    if int(project_id or 0) > 0:
        params["project_id"] = int(project_id)
    if int(asset_id or 0) > 0:
        params["asset_id"] = int(asset_id)
    if int(mymap_id or 0) > 0:
        params["mymap_id"] = int(mymap_id)
    if not params:
        raise SyncError("Provide project_id, asset_id, or mymap_id to generate OGC WMTS/KML streaming links.")

    qs = urllib.parse.urlencode(params)
    res = client.request("GET", f"/api/v2/ogc/links?{qs}")
    if not res.get("available"):
        raise SyncError(res.get("message") or "No georeferenced orthomosaic/raster is available on this item yet.")

    if save_kml_to is not None:
        save_kml_to = Path(save_kml_to).resolve()
        save_kml_to.parent.mkdir(parents=True, exist_ok=True)
        kml_path = urllib.parse.urlsplit(res["kml_networklink_url"]).path
        with client.request("GET", kml_path, raw=True) as raw_resp:
            save_kml_to.write_bytes(raw_resp.read())
        res["saved_kml_path"] = str(save_kml_to)
    return res


def pull_cloud_for_arcgis(client: Client, destination: Path, *, snapshot_id: str = "", cloud_project_id: int = 0, progress=None) -> dict:
    """Download a MosaicEarth Cloud Survey/Project into an ArcGIS Pro workspace folder."""
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
    extracted_layers: list[Path] = []
    for item in state["manifest"]["files"]:
        f = target_dir / item["path"]
        if f.name.lower().endswith((".shp.zip", ".obj.zip", "_3dtiles.zip")):
            out_sub = f.with_suffix("")
            extract_products(f, out_sub)
            extracted_layers.append(out_sub)
        elif item["role"] == "product":
            extracted_layers.append(f)
    return {
        "snapshot_id": sid,
        "name": state["manifest"]["name"],
        "directory": target_dir,
        "layers": [str(p) for p in extracted_layers],
    }


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
    """Standalone dark MosaicEarth GUI for ArcGIS Pro layers & live OGC WMTS streaming."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    session = {"token": os.environ.get("MOSAICEARTH_TOKEN", ""), "email": "", "origin": ORIGIN}
    root = tk.Tk()
    root.title("MosaicEarth · ArcGIS Pro Connector & Live OGC WMTS (v0.4.0)")
    root.geometry("740x610")
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

    frm_cfg = ttk.LabelFrame(root, text="2. Publish GIS Layer / Folder or Copy Live OGC WMTS for ArcGIS Pro")
    frm_cfg.pack(fill="x", **pad)

    cb_proj = ttk.Combobox(frm_cfg, state="readonly", width=48)
    cb_proj["values"] = ["0: Create / Match Cloud Project Automatically"]
    cb_proj.current(0)

    var_path = tk.StringVar()
    var_name = tk.StringVar(value="ArcGIS Pro Deliverable")
    var_sname = tk.StringVar()
    var_sdate = tk.StringVar()
    var_wmts = tk.StringVar(value="Sign in and select a Cloud Project to generate a live OGC WMTS 1.0.0 URL for ArcGIS Pro.")

    def browse_file():
        chosen = filedialog.askopenfilename(title="Select GeoTIFF, Shapefile (.shp), GeoPackage (.gpkg), CAD (.dxf), LAS/LAZ or GLB")
        if chosen:
            var_path.set(chosen)
            var_name.set(Path(chosen).stem)

    ttk.Label(frm_cfg, text="Cloud Project:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
    cb_proj.grid(row=0, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="GIS Layer / File:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_path, width=42).grid(row=1, column=1, sticky="we", padx=8, pady=4)
    ttk.Button(frm_cfg, text="Browse…", command=browse_file).grid(row=1, column=2, padx=6, pady=4)
    ttk.Label(frm_cfg, text="Task Name:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_name, width=46).grid(row=2, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="4D Survey Name:").grid(row=3, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_sname, width=46).grid(row=3, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="4D Date (YYYY-MM-DD):").grid(row=4, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_sdate, width=22).grid(row=4, column=1, sticky="w", padx=8, pady=4)
    ttk.Label(frm_cfg, text="Live WMTS URL:").grid(row=5, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_wmts, width=46).grid(row=5, column=1, columnspan=2, sticky="we", padx=8, pady=4)

    var_status = tk.StringVar(value="Tip: In ArcGIS Pro, open Catalog -> Toolboxes -> Add Toolbox -> MosaicEarth.pyt")
    ttk.Label(root, textvariable=var_status, wraplength=700).pack(fill="x", padx=14, pady=8)

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
        src_p = var_path.get().strip()
        if not src_p:
            messagebox.showwarning("MosaicEarth", "Choose a GIS file or directory.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="mosaicearth-arcgis-gui-") as tmp:
                    manifest, paths = stage_arcgis_deliverables(
                        Path(tmp),
                        [Path(src_p)],
                        var_name.get().strip() or "ArcGIS Pro Layer",
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

    def copy_wmts():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])
        if pid <= 0:
            messagebox.showinfo("MosaicEarth", "Select a Cloud Project above to generate its OGC WMTS URL.")
            return
        try:
            client = Client(token=session["token"], origin=session["origin"])
            links = fetch_live_ogc_links(client, project_id=pid)
            url = links["wmts_capabilities_url"]
            var_wmts.set(url)
            root.clipboard_clear()
            root.clipboard_append(url)
            var_status.set("Copied OGC WMTS 1.0.0 Capabilities URL to clipboard! Paste into ArcGIS Pro -> Insert -> Connections -> New WMTS Server.")
        except Exception as exc:
            messagebox.showerror("OGC WMTS Error", str(exc))

    act = ttk.Frame(root)
    act.pack(fill="x", padx=12, pady=8)
    ttk.Button(act, text="Publish Layer to MyEarth", style="Accent.TButton", command=push_now).pack(side="left", padx=4)
    ttk.Button(act, text="Copy Live OGC WMTS URL", command=copy_wmts).pack(side="left", padx=4)
    root.mainloop()


def main(argv=None):
    if argv is None and len(sys.argv) == 1:
        launch_gui()
        return

    parser = argparse.ArgumentParser(description="MosaicEarth ArcGIS Pro Connector & Live OGC WMTS CLI (v0.4.0)")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--origin", default=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", ORIGIN))
    parser.add_argument("--email", default=os.environ.get("MOSAICEARTH_EMAIL", ""))
    sub = parser.add_subparsers(dest="cmd")

    p_push = sub.add_parser("push", help="Publish one or more GIS layers/folders to MyEarth")
    p_push.add_argument("inputs", nargs="+", type=Path)
    p_push.add_argument("--name", default="")
    p_push.add_argument("--layer-kind", default="auto", choices=["auto", "orthophoto", "elevation", "pointcloud", "mesh", "gaussian", "vector"])
    p_push.add_argument("--project-id", type=int, default=0)
    p_push.add_argument("--survey-name", default="")
    p_push.add_argument("--survey-date", default="")

    p_ogc = sub.add_parser("ogc-links", help="Fetch live authenticated OGC WMTS 1.0.0 / XYZ / NetworkLink KML URLs")
    p_ogc.add_argument("--project-id", type=int, default=0)
    p_ogc.add_argument("--asset-id", type=int, default=0)
    p_ogc.add_argument("--mymap-id", type=int, default=0)
    p_ogc.add_argument("--save-kml", type=Path, default=None)

    p_pull = sub.add_parser("pull", help="Download a MyEarth Survey/Project for ArcGIS Pro")
    p_pull.add_argument("--dest", type=Path, required=True)
    p_pull.add_argument("--snapshot-id", default="")
    p_pull.add_argument("--project-id", type=int, default=0)

    args = parser.parse_args(argv)
    if args.gui or not args.cmd:
        launch_gui()
        return

    token = os.environ.get("MOSAICEARTH_TOKEN", "")
    client = Client(token=token, origin=args.origin, progress=lambda d, t, m: print(f"[{d}/{t}] {m}", flush=True))
    if not client.token:
        email = args.email or input("MosaicEarth email: ").strip()
        password = os.environ.get("MOSAICEARTH_PASSWORD") or getpass.getpass("MosaicEarth password: ")
        client.signin(email, password)

    if args.cmd == "push":
        eff_name = args.name.strip() or args.inputs[0].resolve().stem
        with tempfile.TemporaryDirectory(prefix="mosaicearth-arcgis-") as tmp:
            manifest, paths = stage_arcgis_deliverables(
                Path(tmp),
                args.inputs,
                eff_name,
                layer_kind=args.layer_kind,
                target_project_id=args.project_id,
                survey_name=args.survey_name,
                survey_date=args.survey_date,
            )
            state = client.push(manifest, paths)
            print(json.dumps({"ok": True, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}, indent=2))
    elif args.cmd == "ogc-links":
        links = fetch_live_ogc_links(
            client,
            project_id=args.project_id,
            asset_id=args.asset_id,
            mymap_id=args.mymap_id,
            save_kml_to=args.save_kml,
        )
        print(json.dumps(links, indent=2))
    elif args.cmd == "pull":
        res = pull_cloud_for_arcgis(client, args.dest, snapshot_id=args.snapshot_id, cloud_project_id=args.project_id, progress=client.progress)
        print(json.dumps({"ok": True, **res, "directory": str(res["directory"])}, indent=2))


if __name__ == "__main__":
    main()
