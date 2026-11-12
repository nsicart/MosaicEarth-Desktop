"""MosaicEarth Cloud & 3D Gaussian Splatting Add-on for Blender 3.6–4.x.

AGPL-3.0-or-later. Session tokens remain in memory only and are never saved in .blend files.
"""
bl_info = {
    "name": "MosaicEarth Cloud & 3DGS Connector",
    "author": "MosaicEarth",
    "version": (0, 3, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > MosaicEarth",
    "description": "Synchronize georeferenced .glb meshes and 3D Gaussian Splats (.ply/.spz/.splat) with MyEarth Projects & 4D Surveys",
    "doc_url": "https://github.com/nsicart/MosaicEarth-Desktop/blob/main/CONNECTORS.md#blender",
    "category": "Import-Export",
}

import queue
import shutil
import tempfile
import threading
from pathlib import Path

from .core import pull_cloud_to_folder, stage_blender_package
from .mosaicearth_sync import ORIGIN, Client, SyncError

try:
    import bpy
    from bpy.props import (
        BoolProperty,
        EnumProperty,
        FloatProperty,
        PointerProperty,
        StringProperty,
    )
except ImportError:  # pragma: no cover - imported outside Blender for CLI/testing
    bpy = None


_SESSION = {
    "token": "",
    "email": "",
    "origin": ORIGIN,
    "projects": [],
    "snapshots": [],
    "busy": False,
    "cancel": False,
    "status": "Not signed in",
    "progress": 0.0,
    "events": queue.Queue(),
}


def _project_items(self, context):
    items = [("0", "Create / Match Standalone Cloud Project", "Create or update a dedicated Cloud project")]
    for p in _SESSION["projects"]:
        surveys = p.get("surveys") or []
        hint = f"{len(surveys)} survey(s)" if surveys else "MyEarth Project"
        items.append((str(p["id"]), f"{p['name']} (#{p['id']})", hint))
    return items


def _cloud_pull_items(self, context):
    items = []
    for p in _SESSION["projects"]:
        items.append((f"proj:{p['id']}", f"[Project] {p['name']} (#{p['id']})", "Export and import latest Cloud project"))
    for s in _SESSION["snapshots"]:
        if s.get("status") == "COMPLETE":
            items.append((f"snap:{s['id']}", f"[Revision] {s['name']} ({s['id'][:8]})", "Download verified snapshot"))
    if not items:
        items.append(("none", "No Cloud projects loaded (Sign in & Refresh)", ""))
    return items


if bpy is not None:

    class MosaicEarthSceneProps(bpy.types.PropertyGroup):
        origin_url: StringProperty(
            name="Cloud Origin",
            default=ORIGIN,
            description="MosaicEarth Cloud HTTPS origin",
        )
        email: StringProperty(
            name="Account Email",
            default="",
            description="MosaicEarth account email",
        )
        password: StringProperty(
            name="Password",
            default="",
            subtype="PASSWORD",
            options={"SKIP_SAVE"},
            description="MosaicEarth password (used only for sign-in; never stored in .blend)",
        )
        project_name: StringProperty(
            name="Task / Asset Name",
            default="Blender Scene",
            description="Name of the synchronized model or survey task in MyEarth",
        )
        export_scope: EnumProperty(
            name="Mesh Export",
            items=[
                ("VISIBLE", "Visible Scene (.glb)", "Export all visible mesh objects as binary glTF 2.0 (.glb)"),
                ("SELECTED", "Selected Objects (.glb)", "Export only currently selected objects as .glb"),
                ("NONE", "Skip Scene Mesh (Splat Only)", "Synchronize only the external 3DGS file below"),
            ],
            default="VISIBLE",
        )
        splat_filepath: StringProperty(
            name="3DGS / Point Cloud File",
            default="",
            subtype="FILE_PATH",
            description="Optional .ply, .spz, .splat or .ksplat 3D Gaussian Splat / point cloud to upload",
        )
        include_blend_backup: BoolProperty(
            name="Include saved .blend as private attachment",
            default=False,
            description="Preserve the current saved .blend file alongside viewer models",
        )
        target_project: EnumProperty(
            name="Target MyEarth Project",
            items=_project_items,
        )
        survey_name: StringProperty(
            name="4D Survey Name",
            default="",
            description="Optional Survey container name inside the MyEarth Project (e.g. 'Flight Nov 2026')",
        )
        survey_date: StringProperty(
            name="4D Capture Date (YYYY-MM-DD)",
            default="",
            description="Optional ISO capture date YYYY-MM-DD for 4D Split-Screen Timeline comparison",
        )
        use_georef: BoolProperty(
            name="Attach WGS84 Georeference Anchor",
            default=False,
            description="Place the exported 3D model or Gaussian Splat at exact WGS84 coordinates on the MosaicEarth globe",
        )
        latitude: FloatProperty(name="Latitude (°)", default=41.3874, min=-90.0, max=90.0, precision=6)
        longitude: FloatProperty(name="Longitude (°)", default=2.1686, min=-180.0, max=180.0, precision=6)
        altitude: FloatProperty(name="Altitude (m)", default=0.0, min=-1000.0, max=50000.0, precision=2)
        heading: FloatProperty(name="Heading (°)", default=0.0, min=-360.0, max=360.0, precision=2)
        pitch: FloatProperty(name="Pitch (°)", default=0.0, min=-180.0, max=180.0, precision=2)
        roll: FloatProperty(name="Roll (°)", default=0.0, min=-180.0, max=180.0, precision=2)
        scale_factor: FloatProperty(name="Scale", default=1.0, min=0.001, max=10000.0, precision=3)
        cloud_pull_target: EnumProperty(
            name="Cloud Source",
            items=_cloud_pull_items,
        )
        pull_directory: StringProperty(
            name="Download Folder",
            default="",
            subtype="DIR_PATH",
            description="Local directory where verified Cloud revisions will be stored before importing",
        )

    def _refresh_cloud_lists(client: Client):
        _SESSION["projects"] = client.request("GET", "/api/v2/sync/projects").get("projects", [])
        _SESSION["snapshots"] = client.snapshots()

    def _import_downloaded_files(scene, result: dict):
        col_name = f"MosaicEarth_{result['name']}"[:60]
        collection = bpy.data.collections.new(col_name)
        scene.collection.children.link(collection)
        prev_active = bpy.context.view_layer.active_layer_collection
        for lc in bpy.context.view_layer.layer_collection.children:
            if lc.collection == collection:
                bpy.context.view_layer.active_layer_collection = lc
                break
        imported = 0
        try:
            for fpath in result.get("importable_files", []):
                ext = Path(fpath).suffix.lower()
                try:
                    if ext in {".glb", ".gltf"}:
                        bpy.ops.import_scene.gltf(filepath=str(fpath))
                        imported += 1
                    elif ext == ".obj":
                        if hasattr(bpy.ops.wm, "obj_import"):
                            bpy.ops.wm.obj_import(filepath=str(fpath))
                        else:
                            bpy.ops.import_scene.obj(filepath=str(fpath))
                        imported += 1
                    elif ext == ".ply":
                        if hasattr(bpy.ops.wm, "ply_import"):
                            bpy.ops.wm.ply_import(filepath=str(fpath))
                        else:
                            bpy.ops.import_mesh.ply(filepath=str(fpath))
                        imported += 1
                except Exception:
                    continue
        finally:
            bpy.context.view_layer.active_layer_collection = prev_active

        georef = result.get("georef") or {}
        props = scene.mosaicearth
        if georef and ("lat" in georef or "lon" in georef):
            props.use_georef = True
            props.latitude = float(georef.get("lat") or 0.0)
            props.longitude = float(georef.get("lon") or 0.0)
            props.altitude = float(georef.get("alt") or 0.0)
            props.heading = float(georef.get("heading") or 0.0)
            props.pitch = float(georef.get("pitch") or 0.0)
            props.roll = float(georef.get("roll") or 0.0)
            props.scale_factor = float(georef.get("scale") or 1.0)
        return imported

    def _poll_events():
        updated = False
        while not _SESSION["events"].empty():
            kind, payload = _SESSION["events"].get_nowait()
            updated = True
            if kind == "status":
                _SESSION["status"] = payload
            elif kind == "done_push":
                _SESSION["busy"] = False
                _SESSION["status"] = payload
            elif kind == "done_pull":
                _SESSION["busy"] = False
                try:
                    count = _import_downloaded_files(bpy.context.scene, payload)
                    _SESSION["status"] = f"Downloaded & imported {count} 3D file(s) into '{payload['name']}'"
                except Exception as exc:
                    _SESSION["status"] = f"Downloaded to {payload['directory']} (import note: {exc})"
            elif kind == "error":
                _SESSION["busy"] = False
                _SESSION["status"] = f"Error: {payload}"
        if updated and bpy.context and bpy.context.screen:
            for area in bpy.context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        return 0.25 if _SESSION["busy"] else None

    class MOSAICEARTH_OT_signin(bpy.types.Operator):
        bl_idname = "mosaicearth.signin"
        bl_label = "Sign In to MyEarth"
        bl_description = "Sign in with your MosaicEarth email and password"

        def execute(self, context):
            props = context.scene.mosaicearth
            if not props.email.strip() or not props.password:
                self.report({"ERROR"}, "Enter your MosaicEarth email and password")
                return {"CANCELLED"}
            try:
                client = Client(origin=props.origin_url.strip())
                user = client.signin(props.email.strip(), props.password)
                props.password = ""
                _SESSION["token"] = client.token
                _SESSION["email"] = user.get("email") or props.email.strip()
                _SESSION["origin"] = client.origin
                _refresh_cloud_lists(client)
                _SESSION["status"] = f"Signed in as {_SESSION['email']}"
                self.report({"INFO"}, _SESSION["status"])
                return {"FINISHED"}
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                _SESSION["status"] = str(exc)
                return {"CANCELLED"}

    class MOSAICEARTH_OT_signout(bpy.types.Operator):
        bl_idname = "mosaicearth.signout"
        bl_label = "Sign Out"
        bl_description = "Revoke this Blender session token"

        def execute(self, context):
            if _SESSION["token"]:
                try:
                    Client(token=_SESSION["token"], origin=_SESSION["origin"]).signout()
                except Exception:
                    pass
            _SESSION.update(token="", email="", projects=[], snapshots=[], status="Signed out")
            return {"FINISHED"}

    class MOSAICEARTH_OT_refresh(bpy.types.Operator):
        bl_idname = "mosaicearth.refresh"
        bl_label = "Refresh Cloud Projects"

        def execute(self, context):
            if not _SESSION["token"]:
                self.report({"ERROR"}, "Sign in first")
                return {"CANCELLED"}
            try:
                client = Client(token=_SESSION["token"], origin=_SESSION["origin"])
                _refresh_cloud_lists(client)
                _SESSION["status"] = f"Loaded {len(_SESSION['projects'])} Cloud project(s)"
                return {"FINISHED"}
            except Exception as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}

    class MOSAICEARTH_OT_cancel(bpy.types.Operator):
        bl_idname = "mosaicearth.cancel"
        bl_label = "Pause Transfer"

        def execute(self, context):
            _SESSION["cancel"] = True
            _SESSION["status"] = "Pausing after current 4 MiB chunk…"
            return {"FINISHED"}

    class MOSAICEARTH_OT_push(bpy.types.Operator):
        bl_idname = "mosaicearth.push"
        bl_label = "Push to MyEarth Cloud"
        bl_description = "Export .glb mesh and/or 3D Gaussian Splat and upload verified chunks to MyEarth"

        def execute(self, context):
            if _SESSION["busy"]:
                self.report({"WARNING"}, "A transfer is already running")
                return {"CANCELLED"}
            if not _SESSION["token"]:
                self.report({"ERROR"}, "Sign in to MosaicEarth Cloud first")
                return {"CANCELLED"}

            props = context.scene.mosaicearth
            staging_root = Path(tempfile.mkdtemp(prefix="mosaicearth-blender-export-"))
            glb_out = None
            if props.export_scope != "NONE":
                glb_out = staging_root / "scene.glb"
                try:
                    bpy.ops.export_scene.gltf(
                        filepath=str(glb_out),
                        export_format="GLB",
                        use_selection=(props.export_scope == "SELECTED"),
                        use_visible=(props.export_scope == "VISIBLE"),
                        export_apply=True,
                    )
                except Exception as exc:
                    shutil.rmtree(staging_root, ignore_errors=True)
                    self.report({"ERROR"}, f"glTF export failed: {exc}")
                    return {"CANCELLED"}

            splat_file = Path(bpy.path.abspath(props.splat_filepath)).resolve() if props.splat_filepath.strip() else None
            blend_file = Path(bpy.data.filepath).resolve() if (props.include_blend_backup and bpy.data.filepath) else None
            georef = None
            if props.use_georef:
                georef = {
                    "enabled": True,
                    "lat": props.latitude,
                    "lon": props.longitude,
                    "alt": props.altitude,
                    "heading": props.heading,
                    "pitch": props.pitch,
                    "roll": props.roll,
                    "scale": props.scale_factor,
                }

            target_pid = int(props.target_project) if str(props.target_project).isdigit() else 0
            p_name = props.project_name.strip() or (Path(bpy.data.filepath).stem if bpy.data.filepath else "Blender Scene")
            s_name = props.survey_name.strip()
            s_date = props.survey_date.strip()

            _SESSION["busy"] = True
            _SESSION["cancel"] = False
            _SESSION["status"] = "Packaging files and computing SHA-256…"

            def worker():
                try:
                    pkg_dir = staging_root / "pkg"
                    manifest, paths = stage_blender_package(
                        pkg_dir,
                        p_name,
                        glb_path=glb_out,
                        splat_path=splat_file,
                        blend_backup=blend_file,
                        georef=georef,
                        target_project_id=target_pid,
                        survey_name=s_name,
                        survey_date=s_date,
                        progress=lambda d, t, m: _SESSION["events"].put(("status", m)),
                    )
                    client = Client(
                        token=_SESSION["token"],
                        origin=_SESSION["origin"],
                        progress=lambda d, t, m: _SESSION["events"].put(
                            ("status", f"{m} ({int(100 * d / max(t, 1))}%)")
                        ),
                        canceled=lambda: _SESSION["cancel"],
                    )
                    state = client.push(manifest, paths)
                    _refresh_cloud_lists(client)
                    _SESSION["events"].put(
                        ("done_push", f"Synchronized '{p_name}' → MyEarth Project #{state['project_id']}")
                    )
                except Exception as exc:
                    _SESSION["events"].put(("error", str(exc)))
                finally:
                    shutil.rmtree(staging_root, ignore_errors=True)

            threading.Thread(target=worker, daemon=True).start()
            bpy.app.timers.register(_poll_events, first_interval=0.2)
            return {"FINISHED"}

    class MOSAICEARTH_OT_pull(bpy.types.Operator):
        bl_idname = "mosaicearth.pull"
        bl_label = "Pull & Import into Blender"
        bl_description = "Download a verified MyEarth project or snapshot and import 3D assets into a new Collection"

        def execute(self, context):
            if _SESSION["busy"]:
                self.report({"WARNING"}, "A transfer is already running")
                return {"CANCELLED"}
            if not _SESSION["token"]:
                self.report({"ERROR"}, "Sign in to MosaicEarth Cloud first")
                return {"CANCELLED"}
            props = context.scene.mosaicearth
            sel = props.cloud_pull_target
            if not sel or sel == "none":
                self.report({"ERROR"}, "Refresh and select a Cloud project or revision")
                return {"CANCELLED"}
            dest_raw = bpy.path.abspath(props.pull_directory).strip() if props.pull_directory.strip() else ""
            dest = Path(dest_raw).resolve() if dest_raw else (Path.home() / "MosaicEarth-Downloads" / "Blender")

            _SESSION["busy"] = True
            _SESSION["cancel"] = False
            _SESSION["status"] = "Starting verified Cloud download…"

            def worker():
                try:
                    client = Client(
                        token=_SESSION["token"],
                        origin=_SESSION["origin"],
                        progress=lambda d, t, m: _SESSION["events"].put(
                            ("status", f"{m} ({int(100 * d / max(t, 1))}%)")
                        ),
                        canceled=lambda: _SESSION["cancel"],
                    )
                    if sel.startswith("proj:"):
                        res = pull_cloud_to_folder(client, dest, cloud_project_id=int(sel.split(":", 1)[1]), progress=client.progress)
                    else:
                        res = pull_cloud_to_folder(client, dest, snapshot_id=sel.split(":", 1)[1], progress=client.progress)
                    _SESSION["events"].put(("done_pull", res))
                except Exception as exc:
                    _SESSION["events"].put(("error", str(exc)))

            threading.Thread(target=worker, daemon=True).start()
            bpy.app.timers.register(_poll_events, first_interval=0.2)
            return {"FINISHED"}

    class VIEW3D_PT_mosaicearth(bpy.types.Panel):
        bl_label = "MosaicEarth Cloud & 3DGS"
        bl_idname = "VIEW3D_PT_mosaicearth"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "MosaicEarth"

        def draw(self, context):
            layout = self.layout
            props = context.scene.mosaicearth

            box_acc = layout.box()
            box_acc.label(text="MosaicEarth Account", icon="WORLD")
            if not _SESSION["token"]:
                box_acc.prop(props, "origin_url")
                box_acc.prop(props, "email")
                box_acc.prop(props, "password")
                box_acc.operator("mosaicearth.signin", icon="UNLOCKED")
            else:
                row = box_acc.row()
                row.label(text=f"Signed in: {_SESSION['email']}", icon="CHECKMARK")
                row = box_acc.row(align=True)
                row.operator("mosaicearth.refresh", text="Refresh", icon="FILE_REFRESH")
                row.operator("mosaicearth.signout", text="Sign Out", icon="LOCKED")

            box_push = layout.box()
            box_push.label(text="Push Scene / 3DGS to MyEarth", icon="EXPORT")
            box_push.prop(props, "project_name")
            box_push.prop(props, "export_scope")
            box_push.prop(props, "splat_filepath")
            box_push.prop(props, "include_blend_backup")
            box_push.separator()
            box_push.prop(props, "target_project")
            box_push.prop(props, "survey_name")
            box_push.prop(props, "survey_date")
            box_push.prop(props, "use_georef")
            if props.use_georef:
                col = box_push.column(align=True)
                col.prop(props, "latitude")
                col.prop(props, "longitude")
                col.prop(props, "altitude")
                row_rot = col.row(align=True)
                row_rot.prop(props, "heading")
                row_rot.prop(props, "pitch")
                row_rot.prop(props, "roll")
                col.prop(props, "scale_factor")
            row_act = box_push.row(align=True)
            row_act.enabled = bool(_SESSION["token"]) and not _SESSION["busy"]
            row_act.operator("mosaicearth.push", icon="URL")

            box_pull = layout.box()
            box_pull.label(text="Pull from MyEarth to Collection", icon="IMPORT")
            box_pull.prop(props, "cloud_pull_target")
            box_pull.prop(props, "pull_directory")
            row_pull = box_pull.row(align=True)
            row_pull.enabled = bool(_SESSION["token"]) and not _SESSION["busy"]
            row_pull.operator("mosaicearth.pull", icon="IMPORT")

            box_st = layout.box()
            box_st.label(text=_SESSION["status"], icon="INFO")
            if _SESSION["busy"]:
                box_st.operator("mosaicearth.cancel", icon="PAUSE")

    _CLASSES = (
        MosaicEarthSceneProps,
        MOSAICEARTH_OT_signin,
        MOSAICEARTH_OT_signout,
        MOSAICEARTH_OT_refresh,
        MOSAICEARTH_OT_cancel,
        MOSAICEARTH_OT_push,
        MOSAICEARTH_OT_pull,
        VIEW3D_PT_mosaicearth,
    )

    def register():
        for cls in _CLASSES:
            bpy.utils.register_class(cls)
        bpy.types.Scene.mosaicearth = PointerProperty(type=MosaicEarthSceneProps)

    def unregister():
        if hasattr(bpy.types.Scene, "mosaicearth"):
            del bpy.types.Scene.mosaicearth
        for cls in reversed(_CLASSES):
            bpy.utils.unregister_class(cls)
else:
    def register():
        pass

    def unregister():
        pass
