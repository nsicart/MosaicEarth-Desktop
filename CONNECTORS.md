# Full project synchronization

All connectors are AGPL-3.0-or-later and work with an existing MosaicEarth Cloud account for cloud transfers. Local use of their host applications does not require MosaicEarth sign-in. Passwords are used only for sign-in; revocable session tokens remain in the host session or process. Sign out to revoke them.

The connectors sign in with a MosaicEarth email and password. If you normally use Google, GitHub or Microsoft on the website, open [MosaicEarth sign-in](https://app.mosaicearth.app/Default.aspx), choose **Forgot your password?**, and set a MosaicEarth password for the same account email. Use that password in the connector; do not enter your Google, GitHub or Microsoft password. Desktop also offers browser sign-in with those providers.

Transfers include actual original bytes, finished products, textures and metadata. A checksum manifest and 4 MiB chunks support safe retries. Downloads verify every file. Changed local files are preserved: download a different revision into a new folder to resolve conflicts. This is explicit revision synchronization, not simultaneous editing or automatic deletion propagation. Very large transfers need adequate local disk space and cloud account storage. A revision supports up to 100,000 files; split larger projects into tasks.

Connectors can also target an existing **MyEarth Cloud Project** and specify a **4D Survey Name & Capture Date (`YYYY-MM-DD`)**, routing multi-epoch flights directly into chronological Surveys ready for 1-click **4D Split-Screen (`A | B`) Comparison**, **Cut/Fill Volume ($\Delta V$)** and **Client Delivery Portals**.

## MosaicEarth Capture (Android)

Download the signed 64-bit ARM64 APK (`MosaicEarth-Capture-0.4.1-arm64.apk`) from [Desktop & Connectors](https://app.mosaicearth.app/en/Desktop) or from [GitHub Releases](https://github.com/nsicart/MosaicEarth-Capture-Android/releases), or build from source at [nsicart/MosaicEarth-Capture-Android](https://github.com/nsicart/MosaicEarth-Capture-Android).

Capture overlapping photographs with the live overlap assistant, record continuous video for Gaussian splatting, pick media from your gallery, or import a drone microSD (`DCIM`) folder with EXIF GPS. Reconstruct Photogrammetry (colored point cloud, textured `.glb` mesh, DSM and GeoTIFF orthophoto) and 3D Gaussian Splats (Brush Vulkan compute) locally on your phone for €0, inspect results offline in the built-in 3D & GeoTIFF viewer, and upload finished models to MyEarth within your storage quota — or send raw captures to MosaicEarth Cloud.

## Blender

Download `MosaicEarth-blender-0.3.0.zip` from [Desktop & Connectors](https://app.mosaicearth.app/en/Desktop).

- **Blender 4.2 and later**: open **Edit → Preferences → Get Extensions → ⌄ (top-right) → Install from Disk…**, choose `MosaicEarth-blender-0.3.0.zip`, and enable **MosaicEarth Cloud & 3DGS Connector**.
- **Blender 3.6 – 4.1**: open **Edit → Preferences → Add-ons → Install…**, choose `MosaicEarth-blender-0.3.0.zip`, and enable the add-on.

Open the 3D Viewport sidebar (**N** → **MosaicEarth**):

1. Sign in with your MosaicEarth email and password (session tokens remain in memory only and are never saved inside `.blend` files).
2. Choose whether to export the visible scene or selected objects as binary glTF 2.0 (`.glb`), and/or attach an external **3D Gaussian Splat** (`.ply` with spherical harmonics, `.spz`, `.splat` or `.ksplat`).
3. Optionally enable **Attach WGS84 Georeference Anchor** (`Latitude`, `Longitude`, `Altitude`, `Heading`, `Pitch`, `Roll`, `Scale`) and select a target **MyEarth Project** + **4D Capture Date (`YYYY-MM-DD`)**.
4. Use **Pull & Import into Blender** to download any verified Cloud project or snapshot into a local directory and automatically import `.glb`, `.obj` or `.ply` models into a dedicated `MosaicEarth_<Name>` Collection.

Headless CLI usage is also included:

```bash
python3 -m mosaicearth_blender.core push --name "Quarry Mesh" --glb scene.glb --splat scene_3dgs.ply --lat 41.3874 --lon 2.1686 --survey-date 2026-11-12
```

## Agisoft Metashape

Download `MosaicEarth-metashape-0.3.0.zip` and extract its contents (`mosaicearth_metashape_startup.py` and the `mosaicearth_metashape/` folder) into your Agisoft Metashape Professional `scripts/` directory:

- **Windows**: `%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts`
- **Linux**: `~/.local/share/Agisoft/Metashape Pro/scripts`
- **macOS**: `~/Library/Application Support/Agisoft/Metashape Pro/scripts`

Restart Metashape Professional (2.0 / 2.1 / 2.2+) and open **MosaicEarth → Synchronize Chunk or 4D Survey…**:

- Exports **Orthomosaic** (BigTIFF/LZW GeoTIFF), **DEM / DSM** (GeoTIFF tagged as `elevation` so MosaicEarth's Cut/Fill Stockpile Volume and 4D $\Delta V$ engine work immediately), **Dense Point Cloud** (`.laz`), **3D Textured Model** (`.glb` or bundled `.obj.zip`), **Processing Report** (`.pdf`) and optional source photographs.
- Pre-fills the **4D Capture Date (`YYYY-MM-DD`)** from aligned camera EXIF timestamps and lets you route the chunk directly into an existing MyEarth Project or create a new one.
- **Pull Selected Project into New Chunk…** downloads verified Cloud assets and creates a new Metashape chunk with restored photographs, point clouds and meshes.
- Supports batch headless execution via `metashape -r`:

```bash
metashape -r -c "from mosaicearth_metashape.plugin import run_headless_metashape; run_headless_metashape(['--psx', 'site.psx', '--email', 'you@example.com', '--password', '***', '--survey-date', '2026-11-12'])"
```

## CloudCompare

Download and extract `MosaicEarth-cloudcompare-0.3.0.zip`.

- **Desktop GUI**: run `MosaicEarth-CloudCompare.bat` (Windows) or `./MosaicEarth-CloudCompare.sh` (Linux). Sign in, add `.laz`, `.las`, `.ply` (both Lidar/photogrammetry point clouds, M3C2 distance scalar clouds and 3D Gaussian Splats), `.spz`, `.e57`, `.bin`, `.glb`, `.obj` or `.tif` files, choose a MyEarth Project and optional **4D Capture Date (`YYYY-MM-DD`)**, and click **Push Selected Clouds to MyEarth**.
- **Automatic `-SILENT` Conversion & DSM Rasterization**: for native `.bin` / `.e57` clouds or when a grid step (`--rasterize-dsm-step 0.25`) is specified, the bridge invokes `CloudCompare -SILENT` to export `.laz` and/or rasterize a GeoTIFF DSM (`-RASTERIZE -PROJ MAX -OUTPUT_RASTER_Z`) while preserving the original file as a private attachment.
- **Pull & Open in CloudCompare**: downloads verified Cloud revisions locally and launches CloudCompare with the restored point clouds and meshes.
- **CloudCompare-PythonRuntime (`pycc`)**: users with the embedded Python plugin can also invoke `mosaicearth_cloudcompare.pycc_plugin.push_selected_pycc_entities(...)` to synchronize entities selected in CloudCompare's DB tree.

## RealityCapture

Download and extract `MosaicEarth-realitycapture-0.3.0.zip`.

- **Interactive Desktop Window**: launch `rc_post_export_mosaicearth.bat` with no arguments (or `python -m mosaicearth_realitycapture.connector --gui`) to sign in, pick a RealityCapture export directory or delegate directly to a running RealityCapture instance (`RealityCapture.exe -delegateTo *`), and synchronize deliverables into a MyEarth Project & dated **4D Survey**.
- **Post-Export Automation Script**: wire `rc_post_export_mosaicearth.bat` (Windows) or `rc_post_export_mosaicearth.sh` (Linux/WSL) into RealityCapture's post-export / CLI workflow:

```bat
set MOSAICEARTH_EMAIL=you@example.com
set MOSAICEARTH_PASSWORD=your-mosaicearth-password
rc_post_export_mosaicearth.bat "D:\RC_Exports\Quarry_Nov" "Quarry Survey" "2026-11-12" 42
```

Textured `.obj` exports (`.obj` + `.mtl` + multi-UDIM textures + `.rcInfo`) are automatically bundled into `.obj.zip`, GeoTIFF DSMs (`*dsm*.tif`, `*dem*.tif`) are tagged as `elevation` layers alongside GeoTIFF orthomosaics, and `.laz` / `.ply` / 3DGS `.ply` models are uploaded with verified 4 MiB chunks.

## WebODM and OpenDroneMap WebUI

Download the matching `MosaicEarth-webodm-0.2.2.zip` or `MosaicEarth-webui-0.2.2.zip`. Install it through the host's Administration → Plugins screen, enable it and restart the host web and worker services if requested. WebODM requires 2.9.4 or later; OpenDroneMap WebUI requires 3.2.5 or later. The two ZIPs have different native manifests and must not be interchanged.

Open **MosaicEarth** in the main menu and sign in. Select a local completed task and synchronize. The existing host worker transfers original photos, finished assets and task options without launching a new reconstruction. Cloud viewer layers are prepared separately. Browse the cloud, select a saved revision or latest cloud project, choose a destination host project and download. The plugin restores all files into a new private task and asks the host to prepare its native viewers. Original downloads remain in the resumable staging directory; native viewers may produce additional derivatives.

Host project permissions apply to both upload and restore. Pause stops transfers between chunks; repeat the same action to resume. A native WebODM/WebUI snapshot restores the task structure. A project from another application retains every file and maps compatible orthophotos, elevation models and point clouds into host viewers; unsupported formats remain downloadable attachments. Gaussian splats remain intact but need MosaicEarth's Gaussian viewer.

## QGIS

In QGIS 3.34–3.x or QGIS 4.x, choose Plugins → Manage and Install Plugins → Install from ZIP and select `MosaicEarth-qgis-0.2.2.zip`. Enable MosaicEarth, then open its toolbar/menu action.

Sign in and synchronize the current project. The connector saves project settings, CRS, layer order, styles, layouts and local image/SVG resources. It materializes vector providers into GeoPackage and raster providers into GeoTIFF, and also preserves original local files and sidecars. Local point-cloud/mesh masters are retained. Export unsupported or remote mesh/point-cloud providers to local files before synchronization; an unavailable provider produces an explicit error.

Browse and download into a chosen directory. A QGIS snapshot opens its portable `.qgz` project. Other cloud exports add compatible raster, vector and point-cloud files; all remaining models and attachments are still saved. Authentication and file transfers use QGIS background tasks. Packaging reads the current project on the main thread so native providers and styles remain consistent.

## Desktop

Open the account menu and choose **Connect cloud account**, or use **Sign In to Cloud Account** in MyEarth. Choose **Continue in browser** to use Google, GitHub, Microsoft or your MosaicEarth email. Confirm the account and the code shown in Desktop. The menu identifies the activation account and cloud account separately. Local projects and scenes appear in the local section; cloud entries appear separately. **Pass to Cloud** transfers the entire selected project or MyMosaic and its referenced local data. **Download complete local copy** restores a complete cloud revision. Originals, masters, viewer files, scene references and the project/survey/task hierarchy are preserved. **Open Local Disk Folder** opens the local files.

Cloud synchronization is optional and private. A transferred MyMosaic can be edited or viewed in the destination workspace after restoration. It is a copied revision, not a shared live editing session. Signing out leaves the local copies available. A runtime restart requires signing in again to browse or synchronize cloud data.

No connector publishes data publicly, spends processing credits or starts a GPU pod during file synchronization. Existing cloud viewer conversion runs after validated uploads. Cloud-created projects export available originals, canonical masters, native result archives, supported metadata and prepared viewer files. External links that are not stored files are metadata, not a copy of the remote service.

Windows validation covers native WebODM 4.0 and QGIS 3.44.15 LTR / 4.2.3. OpenDroneMap WebUI 3.2.5 was tested in its Linux container. QGIS tests include isolated reopening after a public-cloud roundtrip, Unicode attributes, SVG symbols and layout images, plus consecutive background operations in both Qt versions. Native host imports finish asynchronously; wait for their image counts and viewers to finish preparing.
