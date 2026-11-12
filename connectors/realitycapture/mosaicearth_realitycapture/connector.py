"""MosaicEarth RealityCapture (CapturingReality) Post-Export, `-delegateTo` & GUI Connector.

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


def find_realitycapture_exe() -> Path | None:
    """Locate RealityCapture.exe on Windows or via REALITYCAPTURE_EXE."""
    env_rc = os.environ.get("REALITYCAPTURE_EXE", "").strip()
    if env_rc and Path(env_rc).is_file():
        return Path(env_rc)
    found = shutil.which("RealityCapture") or shutil.which("RealityCapture.exe")
    if found:
        return Path(found)
    for candidate in (
        Path("C:/Program Files/Capturing Reality/RealityCapture/RealityCapture.exe"),
        Path("C:/Program Files/Epic Games/RealityCapture/RealityCapture.exe"),
        Path("C:/Program Files/Epic Games/RealityScan_2.0/RealityScan.exe"),
    ):
        if candidate.is_file():
            return candidate
    return None


def export_from_running_rc(
    out_dir: Path,
    *,
    export_glb: bool = True,
    export_pointcloud: bool = True,
    delegate_target: str = "*",
) -> Path:
    """Delegate export commands to a running RealityCapture session (`-delegateTo *`)."""
    rc_exe = find_realitycapture_exe()
    if not rc_exe:
        raise SyncError("RealityCapture executable not found. Set REALITYCAPTURE_EXE or export deliverables to a folder first.")
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(rc_exe), "-delegateTo", delegate_target]
    if export_glb:
        cmd.extend(["-exportSelectedModel", str(out_dir / "rc_model.glb")])
    if export_pointcloud:
        cmd.extend(["-exportSparsePointCloud", str(out_dir / "rc_cloud.ply")])
    subprocess.run(cmd, check=True)
    return out_dir


def stage_realitycapture_folder(
    staging_dir: Path,
    export_folder: Path,
    project_name: str,
    *,
    target_project_id: int = 0,
    survey_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
    origin_key: str = "",
    progress=None,
):
    """Scan a RealityCapture export directory, bundle textured OBJs and classify GeoTIFFs / clouds / 3DGS."""
    staging_dir = Path(staging_dir).resolve()
    export_folder = Path(export_folder).resolve()
    if not export_folder.is_dir():
        raise SyncError(f"RealityCapture export directory not found: {export_folder}")

    products_dir = staging_dir / "products"
    images_dir = staging_dir / "images"
    attachments_dir = staging_dir / "attachments"
    products_dir.mkdir(parents=True, exist_ok=True)

    file_classes = {}
    staged = 0

    # Group .obj files by directory so multi-UDIM textures & .mtl/.rcInfo stay together inside .obj.zip
    bundled_obj_dirs = set()
    for obj_file in sorted(export_folder.rglob("*.obj")):
        if not obj_file.is_file() or obj_file.parent in bundled_obj_dirs:
            continue
        bundled_obj_dirs.add(obj_file.parent)
        zip_name = f"{obj_file.stem}.obj.zip"
        dest_zip = products_dir / zip_name
        with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for sib in sorted(obj_file.parent.iterdir()):
                if not sib.is_file():
                    continue
                se = sib.suffix.lower()
                if se in {".obj", ".mtl", ".png", ".jpg", ".jpeg", ".webp", ".rcinfo"}:
                    zf.write(sib, sib.name)
                elif se in {".tfw", ".jgw", ".pgw", ".prj"} and sib.stem.startswith(obj_file.stem):
                    zf.write(sib, sib.name)
        file_classes[dest_zip.relative_to(staging_dir).as_posix()] = "mesh"
        staged += 1

    for item in sorted(export_folder.rglob("*")):
        if not item.is_file():
            continue
        ext = item.suffix.lower()
        rel_in_exp = item.relative_to(export_folder).as_posix()

        # Skip OBJ companion textures/materials already packed into .obj.zip
        if item.parent in bundled_obj_dirs and ext in {".obj", ".mtl", ".png", ".jpg", ".jpeg", ".webp", ".rcinfo"}:
            continue

        if rel_in_exp.startswith(("images/", "inputs/", "photos/")) and ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            images_dir.mkdir(parents=True, exist_ok=True)
            dest_img = images_dir / item.name
            if dest_img.exists():
                dest_img = images_dir / f"{staged}_{item.name}"
            shutil.copyfile(item, dest_img)
            staged += 1
            continue

        if ext in PRODUCTS or item.name.lower().endswith((".obj.zip", ".shp.zip")):
            dest = products_dir / item.name
            if dest.exists():
                dest = products_dir / f"{staged}_{item.name}"
            shutil.copyfile(item, dest)
            rel_key = dest.relative_to(staging_dir).as_posix()
            stem_low = item.stem.lower()
            if ext in {".tif", ".tiff"}:
                is_elev = any(k in stem_low for k in ("dsm", "dtm", "dem", "elev", "height"))
                file_classes[rel_key] = "elevation" if is_elev else "orthophoto"
                # Preserve RealityCapture .tfw / .prj world-file sidecars as attachments
                for side_ext in (".tfw", ".prj", ".rcortho", ".xml"):
                    side = item.with_suffix(side_ext)
                    if side.is_file():
                        attachments_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(side, attachments_dir / side.name)
            elif ext in {".spz", ".splat", ".ksplat"}:
                file_classes[rel_key] = "gaussian"
            elif ext == ".ply":
                with dest.open("rb") as fh:
                    is_gs = b"f_dc_0" in fh.read(65536)
                file_classes[rel_key] = "gaussian" if is_gs else "pointcloud"
            elif ext in {".las", ".laz"}:
                file_classes[rel_key] = "pointcloud"
            elif ext == ".glb":
                file_classes[rel_key] = "mesh"
            staged += 1
        elif ext in {".rcproj", ".rcbox", ".rcinfo", ".xml", ".csv", ".txt", ".pdf", ".json", ".tfw", ".prj"}:
            attachments_dir.mkdir(parents=True, exist_ok=True)
            dest_att = attachments_dir / item.name
            if not dest_att.exists():
                shutil.copyfile(item, dest_att)
                staged += 1

    if staged == 0:
        raise SyncError(f"No RealityCapture deliverables found in {export_folder}")

    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", project_name.strip()).strip("-") or "realitycapture-scene"
    eff_origin = origin_key or f"realitycapture:{safe_slug}"
    if survey_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", survey_date.strip()):
        eff_origin = f"{eff_origin}:{survey_date.strip()}"

    meta = {
        "connector": "realitycapture",
        "realitycapture": {"project_name": project_name.strip(), "source_folder": export_folder.name},
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

    manifest, paths = build_manifest(staging_dir, eff_origin, project_name.strip() or "RealityCapture Survey", meta, role_for, progress)
    for item in manifest["files"]:
        cls = file_classes.get(item["path"])
        if cls:
            item["asset_class"] = cls
    return manifest, paths


def pull_cloud_for_realitycapture(
    client: Client,
    destination: Path,
    *,
    snapshot_id: str = "",
    cloud_project_id: int = 0,
    progress=None,
):
    """Download a MosaicEarth Cloud project or revision into a RealityCapture-ready workspace."""
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

    rc_inputs = target_dir / "rc_inputs"
    for item in state["manifest"]["files"]:
        f = target_dir / item["path"]
        if item["role"] == "source" and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            rc_inputs.mkdir(parents=True, exist_ok=True)
            dest = rc_inputs / f.name
            if not dest.exists():
                shutil.copyfile(f, dest)
        elif f.name.lower().endswith(".obj.zip"):
            extract_products(f, f.with_suffix(""))

    return {
        "snapshot_id": sid,
        "name": state["manifest"]["name"],
        "directory": target_dir,
        "rc_inputs_dir": rc_inputs if rc_inputs.is_dir() else None,
    }


def launch_gui():
    """Cross-platform Tkinter GUI for RealityCapture <-> MosaicEarth synchronization."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    session = {"token": "", "email": "", "origin": ORIGIN}

    root = tk.Tk()
    root.title("MosaicEarth · RealityCapture Connector (0.3.0)")
    root.geometry("680x540")

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

    frm_exp = ttk.LabelFrame(root, text="2. RealityCapture Export Folder & 4D Survey Routing")
    frm_exp.pack(fill="x", **pad)

    cb_proj = ttk.Combobox(frm_exp, state="readonly", width=46)
    cb_proj["values"] = ["0: Create / Match Standalone Cloud Project"]
    cb_proj.current(0)

    var_folder = tk.StringVar()
    var_name = tk.StringVar(value="RealityCapture Reconstruction")
    var_sname = tk.StringVar()
    var_sdate = tk.StringVar()

    def browse_folder():
        chosen = filedialog.askdirectory(title="Select RealityCapture Export Folder")
        if chosen:
            var_folder.set(chosen)
            if var_name.get() == "RealityCapture Reconstruction":
                var_name.set(Path(chosen).name)

    ttk.Label(frm_exp, text="Cloud Project:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
    cb_proj.grid(row=0, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_exp, text="RC Export Folder:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_exp, textvariable=var_folder, width=38).grid(row=1, column=1, sticky="we", padx=6, pady=3)
    ttk.Button(frm_exp, text="Browse…", command=browse_folder).grid(row=1, column=2, padx=4, pady=3)
    ttk.Label(frm_exp, text="Task Name:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_exp, textvariable=var_name, width=42).grid(row=2, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_exp, text="4D Survey Name:").grid(row=3, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_exp, textvariable=var_sname, width=42).grid(row=3, column=1, sticky="we", padx=6, pady=3)
    ttk.Label(frm_exp, text="4D Date (YYYY-MM-DD):").grid(row=4, column=0, sticky="w", padx=6, pady=3)
    ttk.Entry(frm_exp, textvariable=var_sdate, width=20).grid(row=4, column=1, sticky="w", padx=6, pady=3)

    var_status = tk.StringVar(value="Ready")
    ttk.Label(root, textvariable=var_status, wraplength=640).pack(fill="x", padx=12, pady=8)

    def refresh_projects():
        client = Client(token=session["token"], origin=session["origin"])
        projs = client.request("GET", "/api/v2/sync/projects").get("projects", [])
        cb_proj["values"] = ["0: Create / Match Standalone Cloud Project"] + [f"{p['id']}: {p['name']}" for p in projs]
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

    def push_folder():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        folder = var_folder.get().strip()
        if not folder:
            messagebox.showwarning("MosaicEarth", "Choose a RealityCapture export folder.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="mosaicearth-rc-gui-") as tmp:
                    manifest, paths = stage_realitycapture_folder(
                        Path(tmp),
                        Path(folder),
                        var_name.get().strip() or "RealityCapture Survey",
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
                    state = client.push(manifest, paths)
                    var_status.set(f"Complete! Uploaded to MyEarth Project #{state['project_id']}")
            except Exception as exc:
                var_status.set(f"Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def push_live():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="mosaicearth-rc-live-") as tmp:
                    raw = export_from_running_rc(Path(tmp) / "rc_out")
                    manifest, paths = stage_realitycapture_folder(
                        Path(tmp) / "stage",
                        raw,
                        var_name.get().strip() or "RealityCapture Live Scene",
                        target_project_id=pid,
                        survey_name=var_sname.get().strip(),
                        survey_date=var_sdate.get().strip(),
                    )
                    client = Client(
                        token=session["token"],
                        origin=session["origin"],
                        progress=lambda d, t, m: var_status.set(f"{m} ({int(100 * d / max(t, 1))}%)"),
                    )
                    state = client.push(manifest, paths)
                    var_status.set(f"Live RC scene uploaded to MyEarth Project #{state['project_id']}")
            except Exception as exc:
                var_status.set(f"Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def pull_cloud():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])
        if pid <= 0:
            messagebox.showinfo("MosaicEarth", "Select a Cloud Project above to download.")
            return
        dest = filedialog.askdirectory(title="Select Local Destination Folder")
        if not dest:
            return

        def worker():
            try:
                client = Client(
                    token=session["token"],
                    origin=session["origin"],
                    progress=lambda d, t, m: var_status.set(f"{m} ({int(100 * d / max(t, 1))}%)"),
                )
                res = pull_cloud_for_realitycapture(client, Path(dest), cloud_project_id=pid, progress=client.progress)
                var_status.set(f"Restored '{res['name']}' to {res['directory']}")
            except Exception as exc:
                var_status.set(f"Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    act = ttk.Frame(root)
    act.pack(fill="x", padx=10, pady=8)
    ttk.Button(act, text="Push Export Folder to MyEarth", command=push_folder).pack(side="left", padx=4)
    ttk.Button(act, text="Export from Running RC (-delegateTo *)", command=push_live).pack(side="left", padx=4)
    ttk.Button(act, text="Pull Cloud Project", command=pull_cloud).pack(side="left", padx=4)

    root.mainloop()


def main(argv=None):
    if argv is None and len(sys.argv) == 1:
        launch_gui()
        return

    parser = argparse.ArgumentParser(description="MosaicEarth RealityCapture Connector & Post-Export CLI")
    parser.add_argument("--gui", action="store_true", help="Launch graphical desktop window")
    parser.add_argument("--origin", default=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", ORIGIN))
    parser.add_argument("--email", default=os.environ.get("MOSAICEARTH_EMAIL", ""))
    sub = parser.add_subparsers(dest="cmd")

    p_folder = sub.add_parser("push-folder", help="Synchronize a RealityCapture export directory to MyEarth")
    p_folder.add_argument("folder", type=Path)
    p_folder.add_argument("--name", default="")
    p_folder.add_argument("--project-id", type=int, default=0)
    p_folder.add_argument("--survey-name", default="")
    p_folder.add_argument("--survey-date", default="")

    p_live = sub.add_parser("push-live", help="Export directly from running RealityCapture (-delegateTo *) and upload")
    p_live.add_argument("--name", default="RealityCapture Live Export")
    p_live.add_argument("--project-id", type=int, default=0)
    p_live.add_argument("--survey-name", default="")
    p_live.add_argument("--survey-date", default="")

    p_pull = sub.add_parser("pull", help="Download a MyEarth project or snapshot for RealityCapture")
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

    if args.cmd == "push-folder":
        name = args.name.strip() or args.folder.resolve().name
        with tempfile.TemporaryDirectory(prefix="mosaicearth-rc-") as tmp:
            manifest, paths = stage_realitycapture_folder(
                Path(tmp),
                args.folder,
                name,
                target_project_id=args.project_id,
                survey_name=args.survey_name,
                survey_date=args.survey_date,
            )
            state = client.push(manifest, paths)
            print(json.dumps({"ok": True, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}, indent=2))
    elif args.cmd == "push-live":
        with tempfile.TemporaryDirectory(prefix="mosaicearth-rc-live-") as tmp:
            raw = export_from_running_rc(Path(tmp) / "rc_out")
            manifest, paths = stage_realitycapture_folder(
                Path(tmp) / "stage",
                raw,
                args.name,
                target_project_id=args.project_id,
                survey_name=args.survey_name,
                survey_date=args.survey_date,
            )
            state = client.push(manifest, paths)
            print(json.dumps({"ok": True, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}, indent=2))
    elif args.cmd == "pull":
        res = pull_cloud_for_realitycapture(
            client, args.dest, snapshot_id=args.snapshot_id, cloud_project_id=args.project_id, progress=client.progress
        )
        print(json.dumps({"ok": True, "snapshot_id": res["snapshot_id"], "directory": str(res["directory"])}, indent=2))


if __name__ == "__main__":
    main()
