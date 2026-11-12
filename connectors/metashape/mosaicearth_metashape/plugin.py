"""Agisoft Metashape Professional GUI & headless `-r` integration for MosaicEarth.

Supports Metashape Pro 1.8 / 2.0 / 2.1 / 2.2+ (PySide2 and PySide6).
AGPL-3.0-or-later.
"""
import argparse
import re
import shutil
import tempfile
import threading
from pathlib import Path

from .core import pull_cloud_for_metashape, stage_metashape_deliverables
from .mosaicearth_sync import ORIGIN, Client, SyncError

try:
    import Metashape
except ImportError:  # pragma: no cover - imported outside Metashape
    Metashape = None


_MS_SESSION = {"token": "", "email": "", "origin": ORIGIN}


def detect_chunk_capture_date(chunk) -> str:
    """Extract YYYY-MM-DD from the first aligned camera's EXIF DateTime if present."""
    if chunk is None:
        return ""
    for cam in getattr(chunk, "cameras", []) or []:
        photo = getattr(cam, "photo", None)
        meta = getattr(photo, "meta", None) if photo else None
        if not meta:
            continue
        for key in ("Exif/DateTimeOriginal", "Exif/DateTime", "File/ModifyDate"):
            raw = meta[key] if key in meta else None
            if raw:
                m = re.search(r"(\d{4})[:/-](\d{2})[:/-](\d{2})", str(raw))
                if m:
                    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def export_metashape_chunk(
    chunk,
    export_dir: Path,
    *,
    include_ortho: bool = True,
    include_dem: bool = True,
    include_pointcloud: bool = True,
    include_model: bool = True,
    include_report: bool = True,
    include_photos: bool = False,
):
    """Export available products from a Metashape chunk into export_dir."""
    if Metashape is None:
        raise SyncError("Metashape Python API is not loaded in this interpreter")
    export_dir = Path(export_dir).resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    exported = {
        "orthomosaic_tif": None,
        "dsm_tif": None,
        "pointcloud_file": None,
        "model_file": None,
        "report_pdf": None,
        "source_photos": [],
    }

    if include_ortho and getattr(chunk, "orthomosaic", None) is not None:
        ortho_out = export_dir / "orthomosaic.tif"
        comp = Metashape.ImageCompression()
        comp.tiff_big = True
        comp.tiff_overviews = True
        comp.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW
        chunk.exportRaster(
            path=str(ortho_out),
            source_data=Metashape.OrthomosaicData,
            image_compression=comp,
            save_alpha=True,
        )
        if ortho_out.is_file():
            exported["orthomosaic_tif"] = ortho_out

    if include_dem and getattr(chunk, "elevation", None) is not None:
        dsm_out = export_dir / "dsm.tif"
        comp = Metashape.ImageCompression()
        comp.tiff_big = True
        comp.tiff_overviews = True
        comp.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW
        chunk.exportRaster(
            path=str(dsm_out),
            source_data=Metashape.ElevationData,
            image_compression=comp,
        )
        if dsm_out.is_file():
            exported["dsm_tif"] = dsm_out

    has_pc = getattr(chunk, "point_cloud", None) is not None or getattr(chunk, "dense_cloud", None) is not None
    if include_pointcloud and has_pc:
        laz_out = export_dir / "dense_cloud.laz"
        if hasattr(chunk, "exportPointCloud"):
            fmt = getattr(Metashape, "PointCloudFormatLAZ", None)
            kwargs = {"path": str(laz_out)}
            if fmt is not None:
                kwargs["format"] = fmt
            if hasattr(Metashape, "PointCloudData"):
                kwargs["source_data"] = Metashape.PointCloudData
            chunk.exportPointCloud(**kwargs)
        elif hasattr(chunk, "exportPoints"):
            chunk.exportPoints(path=str(laz_out))
        if laz_out.is_file():
            exported["pointcloud_file"] = laz_out

    if include_model and getattr(chunk, "model", None) is not None:
        glb_fmt = getattr(Metashape, "ModelFormatGLTF", None)
        if glb_fmt is not None:
            glb_out = export_dir / "model.glb"
            chunk.exportModel(path=str(glb_out), format=glb_fmt, binary=True, save_texture=True)
            if glb_out.is_file():
                exported["model_file"] = glb_out
        if exported["model_file"] is None:
            obj_dir = export_dir / "model_obj"
            obj_dir.mkdir(parents=True, exist_ok=True)
            obj_out = obj_dir / "model.obj"
            chunk.exportModel(path=str(obj_out), save_texture=True)
            if obj_out.is_file():
                exported["model_file"] = obj_out

    if include_report and hasattr(chunk, "exportReport"):
        rep_out = export_dir / "report.pdf"
        try:
            chunk.exportReport(str(rep_out))
            if rep_out.is_file():
                exported["report_pdf"] = rep_out
        except Exception:
            pass

    if include_photos:
        for cam in getattr(chunk, "cameras", []) or []:
            photo = getattr(cam, "photo", None)
            p_path = Path(photo.path) if (photo and getattr(photo, "path", None)) else None
            if p_path and p_path.is_file():
                exported["source_photos"].append(p_path)

    return exported


def _load_qt():
    try:
        from PySide6 import QtCore, QtWidgets  # type: ignore
        return QtCore, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtWidgets  # type: ignore
        return QtCore, QtWidgets


def open_metashape_sync_dialog():
    """Launch the interactive Synchronization & 4D Survey dialog inside Metashape Pro."""
    if Metashape is None:
        raise SyncError("Open this dialog from Agisoft Metashape Professional")
    QtCore, QtWidgets = _load_qt()
    doc = Metashape.app.document
    chunk = doc.chunk

    parent = QtWidgets.QApplication.activeWindow()
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle("MosaicEarth Cloud & 4D Survey Connector")
    dlg.setMinimumWidth(520)

    layout = QtWidgets.QVBoxLayout(dlg)
    form = QtWidgets.QFormLayout()

    ed_origin = QtWidgets.QLineEdit(_MS_SESSION["origin"])
    ed_email = QtWidgets.QLineEdit(_MS_SESSION["email"])
    ed_pass = QtWidgets.QLineEdit()
    ed_pass.setEchoMode(QtWidgets.QLineEdit.Password)
    btn_auth = QtWidgets.QPushButton("Sign In" if not _MS_SESSION["token"] else f"Signed in ({_MS_SESSION['email']}) · Re-authenticate")

    cb_project = QtWidgets.QComboBox()
    cb_project.addItem("Create / Match Standalone Cloud Project", 0)

    ed_name = QtWidgets.QLineEdit((chunk.label if chunk and chunk.label else "Metashape Survey"))
    ed_survey_name = QtWidgets.QLineEdit()
    ed_survey_name.setPlaceholderText("Optional Survey name (e.g. Quarry Flight Nov)")
    ed_survey_date = QtWidgets.QLineEdit(detect_chunk_capture_date(chunk))
    ed_survey_date.setPlaceholderText("YYYY-MM-DD (for 4D Split-Screen Compare)")

    chk_ortho = QtWidgets.QCheckBox("Orthomosaic (GeoTIFF)")
    chk_ortho.setChecked(bool(chunk and getattr(chunk, "orthomosaic", None)))
    chk_dem = QtWidgets.QCheckBox("DEM / DSM (GeoTIFF — enables Cut/Fill & 4D ΔVolume)")
    chk_dem.setChecked(bool(chunk and getattr(chunk, "elevation", None)))
    chk_pc = QtWidgets.QCheckBox("Dense Point Cloud (.laz)")
    chk_pc.setChecked(bool(chunk and (getattr(chunk, "point_cloud", None) or getattr(chunk, "dense_cloud", None))))
    chk_model = QtWidgets.QCheckBox("3D Textured Model (.glb / .obj.zip)")
    chk_model.setChecked(bool(chunk and getattr(chunk, "model", None)))
    chk_report = QtWidgets.QCheckBox("Processing Report (.pdf)")
    chk_report.setChecked(True)
    chk_photos = QtWidgets.QCheckBox("Original Source Photographs")
    chk_photos.setChecked(False)

    form.addRow("Cloud Origin:", ed_origin)
    form.addRow("MosaicEarth Email:", ed_email)
    form.addRow("Password:", ed_pass)
    form.addRow("", btn_auth)
    form.addRow("Target Cloud Project:", cb_project)
    form.addRow("Task Name:", ed_name)
    form.addRow("4D Survey Name:", ed_survey_name)
    form.addRow("4D Capture Date:", ed_survey_date)
    layout.addLayout(form)

    grp = QtWidgets.QGroupBox("Chunk Deliverables to Export & Upload")
    gl = QtWidgets.QVBoxLayout(grp)
    for w in (chk_ortho, chk_dem, chk_pc, chk_model, chk_report, chk_photos):
        gl.addWidget(w)
    layout.addWidget(grp)

    lbl_status = QtWidgets.QLabel("Ready" if _MS_SESSION["token"] else "Sign in to load your MyEarth Projects")
    bar = QtWidgets.QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    layout.addWidget(lbl_status)
    layout.addWidget(bar)

    row_btns = QtWidgets.QHBoxLayout()
    btn_push = QtWidgets.QPushButton("Export & Push to MyEarth")
    btn_pull = QtWidgets.QPushButton("Pull Selected Project into New Chunk…")
    btn_close = QtWidgets.QPushButton("Close")
    row_btns.addWidget(btn_push)
    row_btns.addWidget(btn_pull)
    row_btns.addWidget(btn_close)
    layout.addLayout(row_btns)
    btn_close.clicked.connect(dlg.reject)

    def refresh_projects():
        if not _MS_SESSION["token"]:
            return
        client = Client(token=_MS_SESSION["token"], origin=_MS_SESSION["origin"])
        projs = client.request("GET", "/api/v2/sync/projects").get("projects", [])
        cb_project.clear()
        cb_project.addItem("Create / Match Standalone Cloud Project", 0)
        for p in projs:
            cb_project.addItem(f"{p['name']} (#{p['id']})", int(p["id"]))

    def do_signin():
        try:
            client = Client(origin=ed_origin.text().strip())
            user = client.signin(ed_email.text().strip(), ed_pass.text())
            ed_pass.clear()
            _MS_SESSION.update(token=client.token, email=user.get("email") or ed_email.text().strip(), origin=client.origin)
            btn_auth.setText(f"Signed in ({_MS_SESSION['email']})")
            refresh_projects()
            lbl_status.setText("Signed in and Cloud projects loaded.")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(dlg, "MosaicEarth Sign-In", str(exc))

    def do_push():
        if not _MS_SESSION["token"]:
            QtWidgets.QMessageBox.warning(dlg, "MosaicEarth", "Sign in to MosaicEarth Cloud first.")
            return
        if chunk is None:
            QtWidgets.QMessageBox.warning(dlg, "MosaicEarth", "Open a Metashape chunk first.")
            return
        btn_push.setEnabled(False)
        tmp_root = Path(tempfile.mkdtemp(prefix="mosaicearth-ms-export-"))
        try:
            lbl_status.setText("Exporting deliverables from Metashape chunk…")
            QtWidgets.QApplication.processEvents()
            exported = export_metashape_chunk(
                chunk,
                tmp_root / "raw",
                include_ortho=chk_ortho.isChecked(),
                include_dem=chk_dem.isChecked(),
                include_pointcloud=chk_pc.isChecked(),
                include_model=chk_model.isChecked(),
                include_report=chk_report.isChecked(),
                include_photos=chk_photos.isChecked(),
            )
            crs_wkt = str(chunk.crs.wkt) if getattr(chunk, "crs", None) else ""
            manifest, paths = stage_metashape_deliverables(
                tmp_root / "stage",
                ed_name.text().strip() or "Metashape Survey",
                orthomosaic_tif=exported["orthomosaic_tif"],
                dsm_tif=exported["dsm_tif"],
                pointcloud_file=exported["pointcloud_file"],
                model_file=exported["model_file"],
                report_pdf=exported["report_pdf"],
                source_photos=exported["source_photos"],
                chunk_metadata={"chunk": chunk.label, "crs_wkt": crs_wkt[:2000]},
                target_project_id=int(cb_project.currentData() or 0),
                survey_name=ed_survey_name.text().strip(),
                survey_date=ed_survey_date.text().strip(),
            )

            def on_prog(done, total, msg):
                lbl_status.setText(msg)
                if total > 0:
                    bar.setValue(int(100 * done / total))
                QtWidgets.QApplication.processEvents()

            client = Client(token=_MS_SESSION["token"], origin=_MS_SESSION["origin"], progress=on_prog)
            state = client.push(manifest, paths)
            bar.setValue(100)
            lbl_status.setText(f"Complete! Uploaded to MyEarth Project #{state['project_id']}")
            refresh_projects()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(dlg, "MosaicEarth Sync Error", str(exc))
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
            btn_push.setEnabled(True)

    def do_pull():
        if not _MS_SESSION["token"]:
            QtWidgets.QMessageBox.warning(dlg, "MosaicEarth", "Sign in first.")
            return
        pid = int(cb_project.currentData() or 0)
        if pid <= 0:
            QtWidgets.QMessageBox.information(dlg, "MosaicEarth", "Select a Cloud Project in 'Target Cloud Project' to pull.")
            return
        dest_str = QtWidgets.QFileDialog.getExistingDirectory(dlg, "Select Local Destination Folder")
        if not dest_str:
            return
        btn_pull.setEnabled(False)
        try:
            def on_prog(done, total, msg):
                lbl_status.setText(msg)
                if total > 0:
                    bar.setValue(int(100 * done / total))
                QtWidgets.QApplication.processEvents()

            client = Client(token=_MS_SESSION["token"], origin=_MS_SESSION["origin"], progress=on_prog)
            res = pull_cloud_for_metashape(client, Path(dest_str), cloud_project_id=pid, progress=on_prog)
            new_chunk = doc.addChunk()
            new_chunk.label = f"MosaicEarth · {res['name']}"
            if res["photos"]:
                new_chunk.addPhotos([str(p) for p in res["photos"]])
            for pc in res["pointclouds"]:
                try:
                    if hasattr(new_chunk, "importPointCloud"):
                        new_chunk.importPointCloud(str(pc))
                except Exception:
                    pass
            for mdl in res["models"]:
                try:
                    new_chunk.importModel(str(mdl))
                except Exception:
                    pass
            bar.setValue(100)
            lbl_status.setText(f"Restored into new chunk '{new_chunk.label}' ({res['directory']})")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(dlg, "MosaicEarth Pull Error", str(exc))
        finally:
            btn_pull.setEnabled(True)

    btn_auth.clicked.connect(do_signin)
    btn_push.clicked.connect(do_push)
    btn_pull.clicked.connect(do_pull)
    if _MS_SESSION["token"]:
        refresh_projects()
    dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()


def register_metashape_menu():
    """Register the MosaicEarth menu action when loaded by Agisoft Metashape."""
    if Metashape is not None and hasattr(Metashape, "app") and Metashape.app is not None:
        Metashape.app.addMenuItem("MosaicEarth/Synchronize Chunk or 4D Survey…", open_metashape_sync_dialog)


def run_headless_metashape(argv=None):
    """Run inside `metashape -r` to export a .psx project and synchronize with MyEarth."""
    parser = argparse.ArgumentParser(description="Headless Metashape -r exporter for MosaicEarth")
    parser.add_argument("--psx", type=Path, required=True, help="Path to Metashape .psx project")
    parser.add_argument("--origin", default=ORIGIN)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--project-id", type=int, default=0)
    parser.add_argument("--survey-name", default="")
    parser.add_argument("--survey-date", default="")
    args = parser.parse_args(argv)

    if Metashape is None:
        raise SyncError("Run this entrypoint inside `metashape -r`")
    doc = Metashape.Document()
    doc.open(str(args.psx))
    chunk = doc.chunk or (doc.chunks[0] if doc.chunks else None)
    if chunk is None:
        raise SyncError("No chunks found in Metashape document")

    client = Client(origin=args.origin, progress=lambda d, t, m: print(f"[{d}/{t}] {m}", flush=True))
    client.signin(args.email, args.password)
    with tempfile.TemporaryDirectory(prefix="mosaicearth-ms-headless-") as tmp:
        exported = export_metashape_chunk(chunk, Path(tmp) / "raw")
        sdate = args.survey_date or detect_chunk_capture_date(chunk)
        manifest, paths = stage_metashape_deliverables(
            Path(tmp) / "stage",
            chunk.label or args.psx.stem,
            orthomosaic_tif=exported["orthomosaic_tif"],
            dsm_tif=exported["dsm_tif"],
            pointcloud_file=exported["pointcloud_file"],
            model_file=exported["model_file"],
            report_pdf=exported["report_pdf"],
            target_project_id=args.project_id,
            survey_name=args.survey_name,
            survey_date=sdate,
        )
        client.push(manifest, paths)
