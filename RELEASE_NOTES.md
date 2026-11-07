# 0.2.0

This release replaces project-name-only synchronization with explicit, private transfer of originals, finished products, attachments and metadata. Desktop, WebODM, OpenDroneMap WebUI and QGIS share resumable chunks, SHA-256 verification, immutable revisions and revocable cloud sessions. Existing metadata-only endpoints are retired rather than reporting incomplete transfers as successful.

The source edition runs locally without an account. Official Windows/Linux installers use account and device activation, two active devices and a 30-day offline grant. Windows has a native `.exe` wizard. A verified, resumable engine download installs NodeODX and CPU OpenSplat. There is no macOS installer and installers are not code-signed.

NodeODX now offers Complete and Fast output choices. Completed photogrammetry includes a local raster preview. Gaussian splats render in the local Cesium viewer with an explicit local-coordinate label. The workspace adds transfer progress, pause/resume, cloud browsing and all-file downloads.

Validation includes a fresh 18-photo Complete NodeODX reconstruction with orthophoto, DSM, DTM, point cloud and textured mesh; actual WebODM/WebUI push and native-task restore with all 47 files byte-identical; and a native QGIS network roundtrip whose project reopens without access to its source files, preserving CRS, styling, attributes and raster values. Account, device-copy, revocation, quota, chunk retry and cross-account access checks are automated. Windows wizard activation, native Pythonw runtime and uninstallation were exercised under Wine; physical Windows WSL 2/Docker Desktop processing remains unverified. Local Gaussian rendering was checked using an existing real trained result, separately from the new NodeODX reconstruction.

The canonical cloud origin is `https://app.mosaicearth.app`. Viewer derivative preparation is asynchronous and distinct from file-transfer completion. Transfers do not imply public publication, cloud reconstruction or automatic deletion of another device's work.

# Earlier releases

0.1.4 added optional Linux CUDA training. 0.1.3 added local georeferenced raster rendering; 0.1.2 exported PLY, SPZ, SPLAT and 3D Tiles in the input SfM frame. Those versions synchronized only project names and should be upgraded for full cloud file transfer.
