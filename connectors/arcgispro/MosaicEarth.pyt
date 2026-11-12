"""MosaicEarth.pyt — Native ArcGIS Pro Python Toolbox (v0.4.0).

Add directly in ArcGIS Pro via:
  Catalog Pane -> Toolboxes -> Right-click -> Add Toolbox -> MosaicEarth.pyt

Provides 3 Geoprocessing tools:
  1. Publish Layer or Scene to MosaicEarth 3D (`PublishLayerToMosaicEarth`)
  2. Add Live MosaicEarth OGC WMTS / KML Stream (`AddLiveMosaicEarthWMTS`)
  3. Pull MosaicEarth Cloud Survey Locally (`PullMosaicEarthSurvey`)

AGPL-3.0-or-later.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

TOOLBOX_DIR = Path(__file__).resolve().parent
if str(TOOLBOX_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLBOX_DIR))

from mosaicearth_arcgispro.connector import (  # noqa: E402
    ORIGIN,
    Client,
    fetch_live_ogc_links,
    pull_cloud_for_arcgis,
    stage_arcgis_deliverables,
)

try:
    import arcpy  # type: ignore
except ImportError:  # pragma: no cover - allows importing/testing outside ArcGIS Pro
    arcpy = None


def _make_param(displayName, name, datatype="GPString", parameterType="Required", direction="Input", default=None):
    if arcpy is not None:
        p = arcpy.Parameter(
            displayName=displayName,
            name=name,
            datatype=datatype,
            parameterType=parameterType,
            direction=direction,
        )
        if default is not None:
            p.value = default
        return p
    return {"displayName": displayName, "name": name, "datatype": datatype, "parameterType": parameterType, "value": default}


class Toolbox:
    def __init__(self):
        self.label = "MosaicEarth 3D & 4D Cloud"
        self.alias = "mosaicearth"
        self.description = "Synchronize ArcGIS Pro rasters, vectors, multipatch 3D models & point clouds with MosaicEarth, and stream live OGC WMTS 1.0.0 / KML layers."
        self.tools = [PublishLayerToMosaicEarth, AddLiveMosaicEarthWMTS, PullMosaicEarthSurvey]


class PublishLayerToMosaicEarth:
    def __init__(self):
        self.label = "1. Publish Layer or Deliverable to MosaicEarth"
        self.description = "Export & upload an ArcGIS Pro Raster, Feature Layer, CAD .DXF, Point Cloud, or 3D Model into a dated MosaicEarth 4D Survey."
        self.canRunInBackground = True

    def getParameterInfo(self):
        return [
            _make_param("Input Layer, Raster, CAD .DXF, LAS/LAZ or Folder", "input_data", "DEFile", "Required"),
            _make_param("Task / Deliverable Name", "task_name", "GPString", "Required", default="ArcGIS Pro Deliverable"),
            _make_param("Target Cloud Project ID (0 = Auto-create / match)", "project_id", "GPLong", "Optional", default=0),
            _make_param("4D Survey Name (Optional)", "survey_name", "GPString", "Optional", default=""),
            _make_param("4D Survey Date YYYY-MM-DD (Optional)", "survey_date", "GPString", "Optional", default=""),
            _make_param("MosaicEarth Cloud Origin", "origin", "GPString", "Required", default=ORIGIN),
            _make_param("MosaicEarth Email (if Bearer Token empty)", "email", "GPString", "Optional", default=""),
            _make_param("MosaicEarth Password or Bearer Token", "secret", "GPStringHidden", "Required", default=""),
        ]

    def execute(self, parameters, messages):
        vals = [p.valueAsText if hasattr(p, "valueAsText") else p.get("value") for p in parameters]
        input_path_raw, task_name, pid_raw, sname, sdate, origin, email, secret = vals
        pid = int(pid_raw or 0)
        origin = (origin or ORIGIN).strip()
        secret = (secret or "").strip()

        def _msg(txt: str):
            if messages is not None and hasattr(messages, "addMessage"):
                messages.addMessage(txt)

        client = Client(token="" if (email and "@" in str(email)) else secret, origin=origin, progress=lambda d, t, m: _msg(f"[{d}/{t}] {m}"))
        if email and "@" in str(email):
            client.signin(str(email).strip(), secret)

        with tempfile.TemporaryDirectory(prefix="arcgis-mosaicearth-") as tmp:
            tmp_root = Path(tmp)
            src_candidate = Path(str(input_path_raw))
            if not src_candidate.exists() and arcpy is not None:
                desc = arcpy.Describe(input_path_raw)
                dtype = getattr(desc, "dataType", "")
                if "Raster" in dtype:
                    out_tif = tmp_root / "exported_raster.tif"
                    arcpy.management.CopyRaster(input_path_raw, str(out_tif))
                    src_candidate = out_tif
                else:
                    out_geojson = tmp_root / "exported_features.geojson"
                    arcpy.conversion.FeaturesToJSON(input_path_raw, str(out_geojson), geoJSON="GEOJSON")
                    src_candidate = out_geojson

            manifest, paths = stage_arcgis_deliverables(
                tmp_root / "stage",
                [src_candidate],
                project_name=str(task_name or src_candidate.stem),
                target_project_id=pid,
                survey_name=str(sname or ""),
                survey_date=str(sdate or ""),
                progress=lambda d, t, m: _msg(m),
            )
            state = client.push(manifest, paths)
            _msg(f"Synchronized to MosaicEarth Project #{state['project_id']} (Snapshot {state['id'][:12]}).")
            return state


class AddLiveMosaicEarthWMTS:
    def __init__(self):
        self.label = "2. Stream Live MosaicEarth OGC WMTS / KML into Map"
        self.description = "Generate a live read-only OGC WMTS 1.0.0 Capabilities URL and NetworkLink KML for any MosaicEarth orthomosaic and add it to the active ArcGIS Pro Map."
        self.canRunInBackground = False

    def getParameterInfo(self):
        return [
            _make_param("MosaicEarth Project / Task ID", "project_id", "GPLong", "Optional", default=0),
            _make_param("MosaicEarth Asset ID (Optional)", "asset_id", "GPLong", "Optional", default=0),
            _make_param("MosaicEarth MyMosaic ID (Optional)", "mymap_id", "GPLong", "Optional", default=0),
            _make_param("Save NetworkLink .KML Copy To (Optional)", "save_kml", "DEFile", "Optional", default=""),
            _make_param("MosaicEarth Cloud Origin", "origin", "GPString", "Required", default=ORIGIN),
            _make_param("MosaicEarth Email (if Bearer Token empty)", "email", "GPString", "Optional", default=""),
            _make_param("MosaicEarth Password or Bearer Token", "secret", "GPStringHidden", "Required", default=""),
        ]

    def execute(self, parameters, messages):
        vals = [p.valueAsText if hasattr(p, "valueAsText") else p.get("value") for p in parameters]
        pid_raw, aid_raw, mm_raw, save_kml_raw, origin, email, secret = vals
        origin = (origin or ORIGIN).strip()
        secret = (secret or "").strip()

        def _msg(txt: str):
            if messages is not None and hasattr(messages, "addMessage"):
                messages.addMessage(txt)

        client = Client(token="" if (email and "@" in str(email)) else secret, origin=origin)
        if email and "@" in str(email):
            client.signin(str(email).strip(), secret)

        kml_dest = Path(save_kml_raw) if save_kml_raw else Path(tempfile.gettempdir()) / f"mosaicearth_live_{pid_raw or aid_raw or mm_raw}.kml"
        links = fetch_live_ogc_links(
            client,
            project_id=int(pid_raw or 0),
            asset_id=int(aid_raw or 0),
            mymap_id=int(mm_raw or 0),
            save_kml_to=kml_dest,
        )
        _msg(f"OGC WMTS 1.0.0 Capabilities URL: {links['wmts_capabilities_url']}")
        _msg(f"XYZ Tile Template URL: {links['xyz_template_url']}")
        _msg(f"Saved NetworkLink KML: {links.get('saved_kml_path')}")

        if arcpy is not None:
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                active_map = aprx.activeMap
                if active_map is not None:
                    try:
                        active_map.addDataFromPath(links["wmts_capabilities_url"])
                        _msg("Added live OGC WMTS 1.0.0 layer directly to active ArcGIS Pro Map.")
                    except Exception:
                        active_map.addDataFromPath(str(kml_dest))
                        _msg("Added NetworkLink KML layer to active ArcGIS Pro Map.")
            except Exception as exc:
                _msg(f"Note: Paste the WMTS URL into Insert -> Connections -> New WMTS Server ({exc}).")
        return links


class PullMosaicEarthSurvey:
    def __init__(self):
        self.label = "3. Pull MosaicEarth Cloud Survey Locally"
        self.description = "Download verified original GeoTIFFs, Shapefiles, Point Clouds & 3D Models from a MosaicEarth Project into a local folder and add them to ArcGIS Pro."
        self.canRunInBackground = True

    def getParameterInfo(self):
        return [
            _make_param("MosaicEarth Project ID", "project_id", "GPLong", "Required", default=0),
            _make_param("Local Output Workspace Folder", "dest_folder", "DEFolder", "Required"),
            _make_param("MosaicEarth Cloud Origin", "origin", "GPString", "Required", default=ORIGIN),
            _make_param("MosaicEarth Email (if Bearer Token empty)", "email", "GPString", "Optional", default=""),
            _make_param("MosaicEarth Password or Bearer Token", "secret", "GPStringHidden", "Required", default=""),
        ]

    def execute(self, parameters, messages):
        vals = [p.valueAsText if hasattr(p, "valueAsText") else p.get("value") for p in parameters]
        pid_raw, dest_raw, origin, email, secret = vals
        origin = (origin or ORIGIN).strip()
        secret = (secret or "").strip()

        def _msg(txt: str):
            if messages is not None and hasattr(messages, "addMessage"):
                messages.addMessage(txt)

        client = Client(token="" if (email and "@" in str(email)) else secret, origin=origin, progress=lambda d, t, m: _msg(f"[{d}/{t}] {m}"))
        if email and "@" in str(email):
            client.signin(str(email).strip(), secret)

        res = pull_cloud_for_arcgis(client, Path(str(dest_raw)), cloud_project_id=int(pid_raw or 0), progress=client.progress)
        _msg(f"Downloaded '{res['name']}' to {res['directory']}")
        if arcpy is not None:
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                active_map = aprx.activeMap
                if active_map is not None:
                    for lyr_p in res.get("layers", []):
                        p = Path(lyr_p)
                        if p.is_file() and p.suffix.lower() in {".tif", ".tiff", ".geojson", ".gpkg", ".kml", ".kmz", ".dxf", ".las", ".laz"}:
                            active_map.addDataFromPath(str(p))
            except Exception:
                pass
        return res
