"""MosaicEarth CloudCompare CLI, GUI & `-SILENT` Raster/Cloud Bridge.

AGPL-3.0-or-later. Uses only the Python standard library and mosaicearth_sync.
"""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
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


CC_NATIVE_CONVERT_EXTS = {".bin", ".e57", ".pts", ".pcd", ".xyz", ".asc"}


def find_cloudcompare_binary() -> list[str] | None:
    """Locate CloudCompare executable on Windows, Linux or macOS."""
    env_bin = os.environ.get("CLOUDCOMPARE_BIN", "").strip()
    if env_bin and Path(env_bin).is_file():
        return [env_bin]
    for name in ("CloudCompare", "cloudcompare.CloudCompare", "CloudCompare.exe"):
        found = shutil.which(name)
        if found:
            return [found]
    candidates = [
        Path("C:/Program Files/CloudCompare/CloudCompare.exe"),
        Path("C:/Program Files/CloudCompareStereo/CloudCompare.exe"),
        Path("/Applications/CloudCompare.app/Contents/MacOS/CloudCompare"),
    ]
    for c in candidates:
        if c.is_file():
            return [str(c)]
    return None


def convert_with_cloudcompare_cli(
    src_cloud: Path,
    out_dir: Path,
    *,
    export_laz: bool = True,
    rasterize_dsm_step: float = 0.0,
) -> dict:
    """Invoke `CloudCompare -SILENT` to convert native .bin/.e57 clouds and/or generate a GeoTIFF DSM."""
    cc_cmd = find_cloudcompare_binary()
    if not cc_cmd:
        raise SyncError(
            "CloudCompare executable not found for .bin/.e57 conversion. Set CLOUDCOMPARE_BIN or export .laz / .ply directly."
        )
    src_cloud = Path(src_cloud).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"pointcloud": None, "dsm_tif": None}

    if export_laz:
        out_laz = out_dir / f"{src_cloud.stem}.laz"
        cmd = [
            *cc_cmd,
            "-SILENT",
            "-AUTO_SAVE",
            "OFF",
            "-O",
            "-GLOBAL_SHIFT",
            "AUTO",
            str(src_cloud),
            "-C_EXPORT_FMT",
            "LAS",
            "-SAVE_CLOUDS",
            "FILE",
            str(out_laz),
        ]
        subprocess.run(cmd, check=True)
        if out_laz.is_file():
            result["pointcloud"] = out_laz

    if rasterize_dsm_step > 0:
        work_copy = out_dir / src_cloud.name
        if src_cloud != work_copy:
            shutil.copyfile(src_cloud, work_copy)
        cmd_r = [
            *cc_cmd,
            "-SILENT",
            "-AUTO_SAVE",
            "OFF",
            "-O",
            "-GLOBAL_SHIFT",
            "AUTO",
            str(work_copy),
            "-RASTERIZE",
            "-GRID_STEP",
            str(float(rasterize_dsm_step)),
            "-PROJ",
            "MAX",
            "-EMPTY_FILL",
            "INTERP",
            "-OUTPUT_RASTER_Z",
        ]
        subprocess.run(cmd_r, check=True)
        tifs = sorted(out_dir.glob("*.tif"), key=lambda p: p.stat().st_mtime, reverse=True)
        if tifs:
            dsm_dest = out_dir / f"{src_cloud.stem}_dsm.tif"
            tifs[0].replace(dsm_dest)
            result["dsm_tif"] = dsm_dest
        if work_copy.is_file() and work_copy != src_cloud:
            work_copy.unlink(missing_ok=True)

    return result


def _bundle_obj_with_materials(obj_file: Path, dest_zip: Path) -> Path:
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(obj_file, obj_file.name)
        mtl = obj_file.with_suffix(".mtl")
        if mtl.is_file():
            zf.write(mtl, mtl.name)
        for sib in sorted(obj_file.parent.iterdir()):
            if sib.is_file() and sib.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
                zf.write(sib, sib.name)
    return dest_zip


def stage_cloudcompare_files(
    staging_dir: Path,
    project_name: str,
    input_files: list[Path],
    *,
    rasterize_dsm_step: float = 0.0,
    georef: dict | None = None,
    target_project_id: int = 0,
    survey_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
    origin_key: str = "",
    progress=None,
):
    """Stage CloudCompare clouds, M3C2 outputs, meshes, 3DGS .ply and GeoTIFF DSMs."""
    staging_dir = Path(staging_dir).resolve()
    products_dir = staging_dir / "products"
    attachments_dir = staging_dir / "attachments"
    products_dir.mkdir(parents=True, exist_ok=True)

    file_classes = {}
    staged = 0

    for raw in input_files:
        src = Path(raw).resolve()
        if not src.is_file():
            raise SyncError(f"Input file not found: {src}")
        ext = src.suffix.lower()

        if ext in CC_NATIVE_CONVERT_EXTS:
            # Preserve original .bin/.e57 as attachment and convert via CloudCompare -SILENT
            attachments_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, attachments_dir / src.name)
            conv = convert_with_cloudcompare_cli(
                src, staging_dir / "_cc_conv", export_laz=True, rasterize_dsm_step=rasterize_dsm_step
            )
            if conv["pointcloud"]:
                dest_pc = products_dir / conv["pointcloud"].name
                shutil.move(str(conv["pointcloud"]), str(dest_pc))
                file_classes[dest_pc.relative_to(staging_dir).as_posix()] = "pointcloud"
                staged += 1
            if conv["dsm_tif"]:
                dest_dsm = products_dir / conv["dsm_tif"].name
                shutil.move(str(conv["dsm_tif"]), str(dest_dsm))
                file_classes[dest_dsm.relative_to(staging_dir).as_posix()] = "elevation"
                staged += 1
            shutil.rmtree(staging_dir / "_cc_conv", ignore_errors=True)
            continue

        if ext == ".obj":
            dest_zip = products_dir / f"{src.stem}.obj.zip"
            _bundle_obj_with_materials(src, dest_zip)
            file_classes[dest_zip.relative_to(staging_dir).as_posix()] = "mesh"
            staged += 1
            continue

        if ext in PRODUCTS or src.name.lower().endswith((".obj.zip", ".shp.zip")):
            dest = products_dir / src.name
            if dest.exists():
                dest = products_dir / f"{staged}_{src.name}"
            shutil.copyfile(src, dest)
            rel_key = dest.relative_to(staging_dir).as_posix()
            stem_low = src.stem.lower()
            if ext in {".tif", ".tiff"}:
                is_elev = any(k in stem_low for k in ("dsm", "dtm", "dem", "raster_z", "height", "elev", "m3c2"))
                file_classes[rel_key] = "elevation" if is_elev else "orthophoto"
            elif ext in {".spz", ".splat", ".ksplat"}:
                file_classes[rel_key] = "gaussian"
            elif ext == ".ply":
                with dest.open("rb") as fh:
                    is_gs = b"f_dc_0" in fh.read(65536)
                file_classes[rel_key] = "gaussian" if is_gs else "pointcloud"
            elif ext in {".las", ".laz"}:
                file_classes[rel_key] = "pointcloud"
                if rasterize_dsm_step > 0 and find_cloudcompare_binary():
                    conv = convert_with_cloudcompare_cli(
                        src, staging_dir / "_cc_raster", export_laz=False, rasterize_dsm_step=rasterize_dsm_step
                    )
                    if conv["dsm_tif"]:
                        dest_dsm = products_dir / conv["dsm_tif"].name
                        shutil.move(str(conv["dsm_tif"]), str(dest_dsm))
                        file_classes[dest_dsm.relative_to(staging_dir).as_posix()] = "elevation"
                    shutil.rmtree(staging_dir / "_cc_raster", ignore_errors=True)
            elif ext == ".glb":
                file_classes[rel_key] = "mesh"
            staged += 1
        else:
            attachments_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, attachments_dir / src.name)
            staged += 1

    if staged == 0:
        raise SyncError("Select at least one point cloud (.laz, .las, .ply, .e57, .bin), mesh or GeoTIFF")

    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", project_name.strip()).strip("-") or "cloudcompare-cloud"
    eff_origin = origin_key or f"cloudcompare:{safe_slug}"
    if survey_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", survey_date.strip()):
        eff_origin = f"{eff_origin}:{survey_date.strip()}"

    meta = {
        "connector": "cloudcompare",
        "cloudcompare": {"project_name": project_name.strip(), "raster_step": rasterize_dsm_step},
        "file_classes": file_classes,
    }
    if isinstance(georef, dict) and georef:
        meta["georef"] = georef
    if int(target_project_id or 0) > 0:
        meta["target_project_id"] = int(target_project_id)
    if int(survey_id or 0) > 0:
        meta["survey_id"] = int(survey_id)
    if survey_name and survey_name.strip():
        meta["survey_name"] = survey_name.strip()[:120]
    if survey_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", survey_date.strip()):
        meta["survey_date"] = survey_date.strip()

    def role_for(rel: str) -> str:
        if rel.startswith("products/") and (Path(rel).suffix.lower() in PRODUCTS or rel.lower().endswith((".obj.zip", ".shp.zip"))):
            return "product"
        return "attachment"

    manifest, paths = build_manifest(staging_dir, eff_origin, project_name.strip() or "CloudCompare Survey", meta, role_for, progress)
    for item in manifest["files"]:
        cls = file_classes.get(item["path"])
        if cls:
            item["asset_class"] = cls
    return manifest, paths


def pull_cloud_for_cloudcompare(
    client: Client,
    destination: Path,
    *,
    snapshot_id: str = "",
    cloud_project_id: int = 0,
    open_in_cc: bool = False,
    progress=None,
):
    """Download a Cloud project or snapshot and optionally launch CloudCompare with the clouds/meshes."""
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

    cc_openable = []
    for item in state["manifest"]["files"]:
        f = target_dir / item["path"]
        if f.name == "all.zip":
            ex = target_dir / "_extracted_all"
            extract_products(f, ex)
            for sub in sorted(ex.rglob("*")):
                if sub.is_file() and sub.suffix.lower() in {".laz", ".las", ".ply", ".obj", ".e57", ".bin"}:
                    cc_openable.append(sub)
        elif f.name.lower().endswith(".obj.zip"):
            ex = f.with_suffix("")
            extract_products(f, ex)
            cc_openable.extend(sorted(ex.rglob("*.obj")))
        elif f.suffix.lower() in {".laz", ".las", ".ply", ".obj", ".e57", ".bin"}:
            cc_openable.append(f)

    if open_in_cc and cc_openable:
        cc_cmd = find_cloudcompare_binary()
        if cc_cmd:
            subprocess.Popen([*cc_cmd, *[str(p) for p in cc_openable[:12]]])

    return {
        "snapshot_id": sid,
        "name": state["manifest"]["name"],
        "directory": target_dir,
        "clouds_and_meshes": cc_openable,
    }


def launch_gui():
    """Cross-platform Tkinter GUI for CloudCompare <-> MosaicEarth synchronization."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    session = {"token": "", "email": "", "origin": ORIGIN, "projects": []}
    selected_files: list[Path] = []

    root = tk.Tk()
    root.title("MosaicEarth · CloudCompare Connector (0.3.0)")
    root.geometry("680x600")

    pad = {"padx": 10, "pady": 4}
    frm_auth = ttk.LabelFrame(root, text="1. MosaicEarth Cloud Account")
    frm_auth.pack(fill="x", **pad)

    var_origin = tk.StringVar(value=ORIGIN)
    var_email = tk.StringVar()
    var_pass = tk.StringVar()
    var_auth_lbl = tk.StringVar(value="Not signed in")

    ttk.Label(frm_auth, text="Origin:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_auth, textvariable=var_origin, width=42).grid(row=0, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_auth, text="Email:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_auth, textvariable=var_email, width=42).grid(row=1, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_auth, text="Password:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_auth, textvariable=var_pass, show="*", width=42).grid(row=2, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_auth, textvariable=var_auth_lbl).grid(row=3, column=1, sticky="w", padx=6, pady=3)

    frm_target = ttk.LabelFrame(root, text="2. Target MyEarth Project & 4D Survey")
    frm_target.pack(fill="x", **pad)

    cb_proj = ttk.Combobox(frm_target, state="readonly", width=48)
    cb_proj["values"] = ["0: Create / Match Standalone Cloud Project"]
    cb_proj.current(0)
    var_task_name = tk.StringVar(value="CloudCompare Point Cloud")
    var_survey_name = tk.StringVar()
    var_survey_date = tk.StringVar()
    var_dsm_step = tk.StringVar(value="0.0")

    ttk.Label(frm_target, text="Cloud Project:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
    cb_proj.grid(row=0, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_target, text="Task Name:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_target, textvariable=var_task_name, width=42).grid(row=1, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_target, text="4D Survey Name:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_target, textvariable=var_survey_name, width=42).grid(row=2, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_target, text="4D Date (YYYY-MM-DD):").grid(row=3, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_target, textvariable=var_survey_date, width=20).grid(row=3, column=1, sticky="w", padx=6, pady=3)
    ttk.Label(frm_target, text="Auto-Rasterize DSM Grid Step (m, 0=off):").grid(row=4, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_target, textvariable=var_dsm_step, width=12).grid(row=4, column=1, sticky="w", padx=6, pady=3)

    frm_files = ttk.LabelFrame(root, text="3. Point Clouds (.laz/.las/.ply/.e57/.bin), 3DGS, M3C2 & Rasters")
    frm_files.pack(fill="both", expand=True, **pad)
    lst = tk.Listbox(frm_files, height=6)
    lst.pack(fill="both", expand=True, padx=6, pady=4)

    def add_files():
        paths = filedialog.askopenfilenames(
            title="Select CloudCompare Files",
            filetypes=[("Point Clouds, 3DGS & Meshes", "*.laz *.las *.ply *.spz *.splat *.e57 *.bin *.tif *.tiff *.glb *.obj"), ("All files", "*.*")],
        )
        for p in paths:
            fp = Path(p)
            if fp not in selected_files:
                selected_files.append(fp)
                lst.insert("end", str(fp))

    def clear_files():
        selected_files.clear()
        lst.delete(0, "end")

    btn_bar = ttk.Frame(frm_files)
    btn_bar.pack(fill="x", padx=6, pady=3)
    ttk.Button(btn_bar, text="Add Clouds / Rasters…", command=add_files).pack(side="left", padx=4)
    ttk.Button(btn_bar, text="Clear List", command=clear_files).pack(side="left", padx=4)

    var_status = tk.StringVar(value="Ready")
    ttk.Label(root, textvariable=var_status).pack(fill="x", padx=12, pady=4)

    def refresh_projects():
        client = Client(token=session["token"], origin=session["origin"])
        projs = client.request("GET", "/api/v2/sync/projects").get("projects", [])
        session["projects"] = projs
        vals = ["0: Create / Match Standalone Cloud Project"] + [f"{p['id']}: {p['name']}" for p in projs]
        cb_proj["values"] = vals
        cb_proj.current(0)

    def sign_in():
        try:
            client = Client(origin=var_origin.get().strip())
            user = client.signin(var_email.get().strip(), var_pass.get())
            var_pass.set("")
            session.update(token=client.token, email=user.get("email") or var_email.get().strip(), origin=client.origin)
            var_auth_lbl.set(f"Signed in as {session['email']}")
            refresh_projects()
        except Exception as exc:
            messagebox.showerror("Sign-In Error", str(exc))

    ttk.Button(frm_auth, text="Sign In", command=sign_in).grid(row=3, column=0, padx=6, pady=4)

    def push_now():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        if not selected_files:
            messagebox.showwarning("MosaicEarth", "Add at least one file to synchronize.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])
        step = float(var_dsm_step.get() or 0.0)

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="mosaicearth-cc-gui-") as tmp:
                    manifest, paths = stage_cloudcompare_files(
                        Path(tmp),
                        var_task_name.get().strip() or "CloudCompare Survey",
                        list(selected_files),
                        rasterize_dsm_step=step,
                        target_project_id=pid,
                        survey_name=var_survey_name.get().strip(),
                        survey_date=var_survey_date.get().strip(),
                        progress=lambda d, t, m: var_status.set(m),
                    )
                    client = Client(
                        token=session["token"],
                        origin=session["origin"],
                        progress=lambda d, t, m: var_status.set(f"{m} ({int(100 * d / max(t, 1))}%)"),
                    )
                    state = client.push(manifest, paths)
                    var_status.set(f"Complete! Uploaded to MyEarth Project #{state['project_id']}")
            except Exception as exc:
                var_status.set(f"Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def pull_now():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])
        if pid <= 0:
            messagebox.showinfo("MosaicEarth", "Select a Cloud Project above to pull locally.")
            return
        dest = filedialog.askdirectory(title="Select Local Download Folder")
        if not dest:
            return

        def worker():
            try:
                client = Client(
                    token=session["token"],
                    origin=session["origin"],
                    progress=lambda d, t, m: var_status.set(f"{m} ({int(100 * d / max(t, 1))}%)"),
                )
                res = pull_cloud_for_cloudcompare(
                    client, Path(dest), cloud_project_id=pid, open_in_cc=True, progress=client.progress
                )
                var_status.set(f"Downloaded {len(res['clouds_and_meshes'])} cloud/mesh file(s) to {res['directory']}")
            except Exception as exc:
                var_status.set(f"Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    act = ttk.Frame(root)
    act.pack(fill="x", padx=10, pady=8)
    ttk.Button(act, text="Push Selected Clouds to MyEarth", command=push_now).pack(side="left", padx=4)
    ttk.Button(act, text="Pull Project & Open in CloudCompare", command=pull_now).pack(side="left", padx=4)

    root.mainloop()


def main(argv=None):
    if argv is None and len(sys.argv) == 1:
        launch_gui()
        return

    parser = argparse.ArgumentParser(description="MosaicEarth CloudCompare Bridge & CLI")
    parser.add_argument("--gui", action="store_true", help="Launch graphical desktop window")
    parser.add_argument("--origin", default=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", ORIGIN))
    parser.add_argument("--email", default=os.environ.get("MOSAICEARTH_EMAIL", ""))
    sub = parser.add_subparsers(dest="cmd")

    p_push = sub.add_parser("push", help="Synchronize point clouds, M3C2 outputs, 3DGS or DSMs to MyEarth")
    p_push.add_argument("--name", required=True)
    p_push.add_argument("files", nargs="+", type=Path)
    p_push.add_argument("--rasterize-dsm-step", type=float, default=0.0, help="Optional CloudCompare -RASTERIZE grid step (m)")
    p_push.add_argument("--project-id", type=int, default=0)
    p_push.add_argument("--survey-name", default="")
    p_push.add_argument("--survey-date", default="")

    p_pull = sub.add_parser("pull", help="Download a MyEarth project or snapshot and optionally open in CloudCompare")
    p_pull.add_argument("--dest", type=Path, required=True)
    p_pull.add_argument("--snapshot-id", default="")
    p_pull.add_argument("--project-id", type=int, default=0)
    p_pull.add_argument("--open-cc", action="store_true")

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
        with tempfile.TemporaryDirectory(prefix="mosaicearth-cc-") as tmp:
            manifest, paths = stage_cloudcompare_files(
                Path(tmp),
                args.name,
                args.files,
                rasterize_dsm_step=args.rasterize_dsm_step,
                target_project_id=args.project_id,
                survey_name=args.survey_name,
                survey_date=args.survey_date,
            )
            state = client.push(manifest, paths)
            print(json.dumps({"ok": True, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}, indent=2))
    elif args.cmd == "pull":
        res = pull_cloud_for_cloudcompare(
            client, args.dest, snapshot_id=args.snapshot_id, cloud_project_id=args.project_id, open_in_cc=args.open_cc, progress=client.progress
        )
        print(json.dumps({"ok": True, "snapshot_id": res["snapshot_id"], "directory": str(res["directory"])}, indent=2))


if __name__ == "__main__":
    main()
