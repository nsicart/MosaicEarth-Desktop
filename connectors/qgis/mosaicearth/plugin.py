"""Native QGIS project/data synchronization, AGPL-3.0-or-later."""
import json
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from qgis.PyQt.QtCore import QStandardPaths
from qgis.PyQt.QtWidgets import (QAction, QDialog, QVBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QListWidget, QFileDialog, QMessageBox, QProgressBar, QApplication)
from qgis.core import (QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsVectorFileWriter, QgsRasterFileWriter, QgsRasterPipe, QgsTask, Qgis)
from .mosaicearth_sync import Client, build_manifest, SyncError


def package_project(project, destination, progress=None):
    """Materialize data providers and preserve styles, CRS, layer order and views."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    snapshot = QgsProject()
    snapshot.setFileName(str(destination / "project.qgz"))
    # Serialize first so project-level properties (layouts, scenes, bookmarks) survive.
    original = destination / "source.qgz"
    previous = project.fileName()
    was_dirty = project.isDirty()
    if not project.write(str(original)):
        raise SyncError("Could not save the QGIS project")
    project.setFileName(previous)
    project.setDirty(was_dirty)
    if not snapshot.read(str(original)):
        raise SyncError("Could not read the QGIS project snapshot")
    for index, layer in enumerate(project.mapLayers().values()):
        if progress:
            progress(index, len(project.mapLayers()), "Packaging " + layer.name())
        clone = snapshot.mapLayer(layer.id())
        if clone is None:
            raise SyncError("Layer missing from the project snapshot")
        output = destination / "data" / layer.id()
        output.parent.mkdir(parents=True, exist_ok=True)
        original_source = Path(layer.source().split("|")[0])
        if original_source.is_file():
            originals = destination / "originals" / layer.id()
            originals.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original_source, originals / original_source.name)
            for sibling in original_source.parent.iterdir():
                if sibling.is_file() and sibling != original_source and (
                    sibling.name.startswith(original_source.name + ".") or
                    sibling.stem == original_source.stem and sibling.suffix.lower() in {".dbf", ".shx", ".prj", ".cpg", ".qpj", ".tfw", ".jgw", ".pgw", ".wld", ".mtl"}):
                    shutil.copyfile(sibling, originals / sibling.name)
        if isinstance(layer, QgsVectorLayer):
            output = output.with_suffix(".gpkg")
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = "data"
            result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(output), project.transformContext(), options)
            if result[0] != QgsVectorFileWriter.NoError:
                raise SyncError("Cannot export " + layer.name() + ": " + str(result[1]))
            clone.setDataSource(str(output) + "|layername=data", layer.name(), "ogr")
        elif isinstance(layer, QgsRasterLayer):
            output = output.with_suffix(".tif")
            pipe = QgsRasterPipe()
            if not pipe.set(layer.dataProvider().clone()):
                raise SyncError("Cannot read raster " + layer.name())
            writer = QgsRasterFileWriter(str(output))
            writer.setOutputFormat("GTiff")
            result = writer.writeRaster(pipe, layer.width(), layer.height(), layer.extent(), layer.crs(), project.transformContext())
            if result != QgsRasterFileWriter.NoError:
                raise SyncError("Cannot export raster " + layer.name())
            clone.setDataSource(str(output), layer.name(), "gdal")
        else:
            # Point-cloud and mesh providers retain their exact master and sidecars.
            source = Path(layer.source().split("|")[0])
            if not source.is_file():
                raise SyncError("Export this provider to a local file first: " + layer.name())
            output = output.with_suffix(source.suffix)
            shutil.copyfile(source, output)
            clone.setDataSource(str(output), layer.name(), layer.providerType())
        if not clone.isValid():
            raise SyncError("Packaged layer cannot be reopened: " + layer.name())
        # setDataSource can reset styling; restore it from the original layer.
        from qgis.core import QgsMapLayerStyle
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        style.writeToLayer(clone)
    snapshot.setFileName(str(destination / "project.qgz"))
    if not snapshot.write():
        raise SyncError("Could not write the portable project")
    # QGIS makes file references relative, but external SVGs and layout images
    # may still point outside the package. Preserve those files and rewrite them.
    import hashlib
    import xml.etree.ElementTree as ET
    import zipfile
    qgz = destination / "project.qgz"
    with zipfile.ZipFile(qgz) as archive:
        members = {i.filename: archive.read(i) for i in archive.infolist()}
    for name, content in list(members.items()):
        if not name.endswith(".qgs"):
            continue
        xml = ET.fromstring(content)
        def portable(value):
            if not value or len(value) > 2048 or "\n" in value or "://" in value:
                return value
            try:
                path = Path(value)
                path = path if path.is_absolute() else destination / path
                if not path.is_file() or path.resolve().is_relative_to(destination.resolve()):
                    return value
                folder = destination / "resources" / hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, folder / path.name)
                return "./" + (folder / path.name).relative_to(destination).as_posix()
            except OSError:
                return value
        for element in xml.iter():
            element.text = portable(element.text)
            for key, value in list(element.attrib.items()):
                element.set(key, portable(value))
        members[name] = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(qgz, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    original.unlink(missing_ok=True)
    return destination / "project.qgz"


class MosaicEarthPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.client = Client()
        self.task = None
        self.dialog = None

    def initGui(self):
        self.action = QAction("MosaicEarth Cloud", self.iface.mainWindow())
        self.action.triggered.connect(self.open)
        self.iface.addPluginToMenu("MosaicEarth", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.task:
            self.task.cancel()
        self.iface.removePluginMenu("MosaicEarth", self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dialog:
            self.dialog.close()

    def open(self):
        if self.dialog:
            self.dialog.show()
            return
        self.dialog = QDialog(self.iface.mainWindow())
        self.dialog.setWindowTitle("MosaicEarth · full project synchronization")
        self.dialog.setMinimumWidth(540)
        layout = QVBoxLayout(self.dialog)
        layout.addWidget(QLabel("Photos, layer data, styles and project settings are synchronized privately."))
        form = QFormLayout()
        self.email, self.password = QLineEdit(), QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("MosaicEarth email", self.email)
        form.addRow("Password", self.password)
        layout.addLayout(form)
        self.status = QLabel("Local QGIS use does not require a MosaicEarth account.")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.entries = QListWidget()
        for label, callback in [("Sign in", self.signin), ("Synchronize current project / resume", self.push),
            ("Browse cloud projects", self.catalog), ("Download selected project", self.pull),
            ("Pause transfer", self.pause), ("Sign out", self.signout)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addWidget(self.entries)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        self.dialog.show()

    def signin(self):
        email, password = self.email.text(), self.password.text()
        self.password.clear()
        self.background("Signing in", lambda task: self.client.signin(email, password),
                        lambda result: self.status.setText("Signed in as " + result["email"]))

    def background(self, title, operation, complete=None):
        if self.task and self.task.isActive():
            self.status.setText("Pause or finish the current transfer first.")
            return
        self.status.setText(title)
        self.progress.setValue(0)
        def run(task):
            self.client.canceled = task.isCanceled
            self.client.progress = lambda done, total, message: task.setProgress(100 * done / total if total else 0)
            return operation(task)
        def finished(exception, result=None):
            self.client.canceled = lambda: False
            if exception:
                self.status.setText(str(exception))
            elif result is not None:
                self.status.setText("Completed and verified")
                if complete:
                    complete(result)
            else:
                self.status.setText("Paused. Resume to continue from saved chunks.")
        self.task = QgsTask.fromFunction(title, run, on_finished=finished)
        self.task.progressChanged.connect(lambda value: self.progress.setValue(int(value)))
        QgsApplication.taskManager().addTask(self.task)

    def push(self):
        if not self.client.token:
            self.status.setText("Sign in before synchronizing")
            return
        if self.task and self.task.isActive():
            self.status.setText("A transfer is already running")
            return
        project = QgsProject.instance()
        origin = project.readEntry("MosaicEarth", "projectId", "")[0] or (
            uuid.uuid5(uuid.NAMESPACE_URL, str(Path(project.fileName()).resolve())).hex if project.fileName() else uuid.uuid4().hex)
        if not re.fullmatch("[0-9a-f]{32}", origin):
            origin = uuid.uuid4().hex
        project.writeEntry("MosaicEarth", "projectId", origin)
        base = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)) / "mosaicearth-sync" / origin
        try:
            self.status.setText("Packaging layers, CRS and styles…")
            QApplication.processEvents()
            package_project(project, base, lambda i, total, name: (self.status.setText(name), QApplication.processEvents()))
        except Exception as exc:
            self.status.setText(str(exc))
            return
        name = project.title() or Path(project.fileName()).stem or "QGIS project"
        crs = project.crs().authid()
        def upload(task):
            manifest, files = build_manifest(base, "qgis:" + origin, name,
                {"qgis": {"project_file": "project.qgz", "crs": crs}},
                role_for=lambda path: "product" if path.startswith("data/") else "attachment", progress=self.client.progress)
            return self.client.push(manifest, files)
        self.background("Synchronizing QGIS project", upload,
                        lambda result: self.status.setText("All files verified. MyEarth project #" + str(result["project_id"])))

    def catalog(self):
        def load(task):
            return {"projects": self.client.request("GET", "/api/v2/sync/projects")["projects"], "snapshots": self.client.snapshots()}
        def loaded(data):
            self.entries.clear()
            self.catalog_data = [*({"name": p["name"] + " · latest cloud data", "project_id": p["id"]} for p in data["projects"]),
                                 *({"name": s["name"] + " · " + s["id"][:8], "snapshot_id": s["id"]} for s in data["snapshots"] if s["status"] == "COMPLETE")]
            self.entries.addItems([p["name"] for p in self.catalog_data])
        self.background("Loading cloud projects", load, loaded)

    def pull(self):
        row = self.entries.currentRow()
        if row < 0:
            self.status.setText("Select a cloud project")
            return
        directory = QFileDialog.getExistingDirectory(self.dialog, "Download into this folder")
        if not directory:
            return
        entry = self.catalog_data[row]
        def download(task):
            import time
            sid = entry.get("snapshot_id")
            if not sid:
                state = self.client.request("POST", f"/api/v2/sync/projects/{entry['project_id']}/export", {})
                sid = state["id"]
                while state["status"] in {"QUEUED", "VERIFYING"}:
                    time.sleep(2)
                    state = self.client.request("GET", f"/api/v2/sync/sessions/{sid}")
                if state["status"] != "COMPLETE":
                    raise SyncError(state.get("error") or "Export failed")
            destination = Path(directory) / ("MosaicEarth-" + sid[:16])
            state = self.client.pull(sid, destination)
            return {"path": str(destination), "state": state}
        def complete(result):
            destination = Path(result["path"])
            project = destination / "project.qgz"
            if project.is_file():
                self.iface.addProject(str(project))
            else:
                for file in destination.rglob("*"):
                    if file.suffix.lower() in {".tif", ".tiff"}:
                        self.iface.addRasterLayer(str(file), file.stem)
                    elif file.suffix.lower() in {".geojson", ".gpkg", ".shp", ".kml"}:
                        self.iface.addVectorLayer(str(file), file.stem, "ogr")
                    elif file.suffix.lower() in {".las", ".laz"}:
                        self.iface.addPointCloudLayer(str(file), file.stem, "pdal")
            self.status.setText("Verified download saved to " + str(destination))
        self.background("Downloading cloud files", download, complete)

    def pause(self):
        if self.task:
            self.task.cancel()

    def signout(self):
        self.background("Signing out", lambda task: self.client.signout() or True,
                        lambda result: self.status.setText("Signed out"))
