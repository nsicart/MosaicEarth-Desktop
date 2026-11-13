# MosaicEarth Open-Source Connectors & GCP Studio

All MosaicEarth connectors and **MosaicEarth GCP Studio** are maintained in **dedicated standalone open-source repositories** on GitHub under [`github.com/nsicart`](https://github.com/nsicart) and are also downloadable directly from [Desktop & Connectors (`app.mosaicearth.app/en/Desktop`)](https://app.mosaicearth.app/en/Desktop).

Every connector speaks the open **MosaicEarth Synchronization Protocol v1** (`src/mosaicearth_sync.py` — resumable 4 MiB chunked transfers + end-to-end SHA-256 verification) and works with both:

- **MosaicEarth Cloud**: `https://app.mosaicearth.app`
- **MosaicEarth Desktop (Local Workstation)**: `http://127.0.0.1:19771` (`MOSAICEARTH_URL=http://127.0.0.1:19771`)

## Standalone Open-Source Repositories

| Tool / Ecosystem | Repository | License | Highlights |
| --- | --- | --- | --- |
| **MosaicEarth GCP Studio** | [nsicart/MosaicEarth-GCP-Studio](https://github.com/nsicart/MosaicEarth-GCP-Studio) | MIT | Browser Ground Control Point & Check Point editor for Photogrammetry (`gcp_list.txt`) and 3D Gaussian Splatting (multi-view ray triangulation + Umeyama `Sim(3)` metric alignment). Integrated at `/GCPStudio.aspx`. |
| **MosaicEarth Capture (Android)** | [nsicart/MosaicEarth-Capture-Android](https://github.com/nsicart/MosaicEarth-Capture-Android) | AGPL-3.0-or-later | On-device Android Photogrammetry & 3D Gaussian Splatting (Brush Vulkan), microSD drone import, and €0 model upload to MyEarth. |
| **MosaicEarth Bridge for iOS** | [nsicart/MosaicEarth-Bridge-iOS](https://github.com/nsicart/MosaicEarth-Bridge-iOS) | AGPL-3.0-or-later | Apple Shortcut + SwiftUI & ShareExtension for iPhone/iPad Pro LiDAR `.USDZ` scans, 360° panoramas & drone Geo-Videos (`/bridge`). |
| **DJI Terra** | [nsicart/MosaicEarth-Connector-DJITerra](https://github.com/nsicart/MosaicEarth-Connector-DJITerra) | AGPL-3.0-or-later | Watch Folder daemon, GUI & CLI for DJI Terra orthomosaics, DSMs, `terra_b3dm` 3D Tiles, LiDAR/dense point clouds, meshes & PDF reports. |
| **PIX4Dmapper & PIX4Dmatic** | [nsicart/MosaicEarth-Connector-PIX4D](https://github.com/nsicart/MosaicEarth-Connector-PIX4D) | AGPL-3.0-or-later | Dual-edition auto-discovery for PIX4Dmapper & PIX4Dmatic (Ortho, DSM, COPC/`.laz`, meshes, `.spz`/`.ply` 3DGS & PDF quality reports). |
| **ArcGIS Pro** | [nsicart/MosaicEarth-Connector-ArcGISPro](https://github.com/nsicart/MosaicEarth-Connector-ArcGISPro) | AGPL-3.0-or-later | Native `MosaicEarth.pyt` Python Toolbox, live authenticated OGC `WMTS 1.0.0` / `KML` map streaming & standalone CLI/GUI. |
| **QGIS** | [nsicart/MosaicEarth-Connector-QGIS](https://github.com/nsicart/MosaicEarth-Connector-QGIS) | AGPL-3.0-or-later | Native QGIS 3.34+ / 4.x plugin for full `.qgz` project, layer, style, GeoPackage, GeoTIFF & OGC WMTS synchronization. |
| **Jawset Postshot & LichtFeld Studio** | [nsicart/MosaicEarth-Connector-Postshot](https://github.com/nsicart/MosaicEarth-Connector-Postshot) | AGPL-3.0-or-later | 3D Gaussian Splatting publisher (`.ply`, `.splat`, `.spz`, `.ksplat`) with DJI XMP / EXIF / COLMAP WGS84 georeferencing & instant `oEmbed` Share copy. |
| **Blender** | [nsicart/MosaicEarth-Connector-Blender](https://github.com/nsicart/MosaicEarth-Connector-Blender) | AGPL-3.0-or-later | Blender 3.6–4.2+ add-on & extension for WGS84 `.glb` scenes and 3D Gaussian Splats, plus pull into Blender Collections. |
| **Agisoft Metashape** | [nsicart/MosaicEarth-Connector-Metashape](https://github.com/nsicart/MosaicEarth-Connector-Metashape) | AGPL-3.0-or-later | Metashape Pro 2.x menu & headless script exporting Ortho, DEM/DSM, `.laz`, `.glb`/`.obj.zip` & PDF reports into 4D Surveys. |
| **CloudCompare** | [nsicart/MosaicEarth-Connector-CloudCompare](https://github.com/nsicart/MosaicEarth-Connector-CloudCompare) | AGPL-3.0-or-later | GUI, `-SILENT` CLI & `pycc` bridge for `.laz`/`.las`/`.e57`/`.bin` point clouds, M3C2 outputs, DSM rasterization & 3DGS. |
| **RealityCapture** | [nsicart/MosaicEarth-Connector-RealityCapture](https://github.com/nsicart/MosaicEarth-Connector-RealityCapture) | AGPL-3.0-or-later | Interactive GUI, `-delegateTo` live bridge & post-export automation script for RealityCapture deliverables. |
| **WebODM & OpenDroneMap WebUI** | [nsicart/MosaicEarth-Connector-WebODM](https://github.com/nsicart/MosaicEarth-Connector-WebODM) | AGPL-3.0-or-later | Native WebODM (2.9.4+) and OpenDroneMap WebUI (3.2.5+) plugins for bidirectional task & source-photo synchronization. |

## Desktop Workspace Synchronization

Open the account menu in **MosaicEarth Desktop** and choose **Connect cloud account**, or use **Sign In to Cloud Account** in MyEarth. Local projects and scenes appear in the local section; cloud entries appear separately. **Pass to Cloud** transfers the entire selected project or MyMosaic and its referenced local data with verified 4 MiB chunks. **Download complete local copy** restores a complete cloud revision locally.
