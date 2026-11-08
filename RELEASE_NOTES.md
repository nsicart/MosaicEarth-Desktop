# 0.3.0 — full interface validation candidate

Restores the web platform interface in a local Desktop window: public globe, Public Mosaics, MyEarth projects/surveys/tasks/layers, MyMosaics compositions and Fly View, with the MosaicEarth branding. NodeODX/ODX and COLMAP/OpenSplat run locally. The AGPL source edition needs no sign-in; the official Windows/Linux packages retain account and device activation.

Windows 11 has completed fresh NodeODX Complete and OpenSplat Fast reconstructions through the full interface. The orthophoto, mesh, point cloud, elevation layer and Gaussian viewers have been inspected. Fly starts above the selected model, supports camera changes, reset and pause/resume. Processing metrics preserve their units and distinguish an unverified GPS accuracy from a measurement.

Complete workspace exchange includes the project hierarchy, original photos, result masters, viewer files, external assets and MyMosaic layer references. Transfers create private immutable revisions, verify checksums and support pause/resume. Signing out does not change local ownership. Cloud sign-in remains in memory until sign-out or runtime restart; passwords are not saved.

The Windows entry point keeps background Docker CLI checks windowless, preserving their logs and exit codes. The Windows wizard checks for active processing/transfers before closing its own previous idle launcher. A second shortcut invocation reopens the existing host. Docker readiness and stopped runtimes produce a retryable setup page. The host does not open a live Linux SQLite database across the Windows bind mount. Migration copies earlier Desktop data into a separate workspace and leaves the originals intact.

The final source builds from an empty Git archive using an anonymously downloadable, checksum-pinned UI resource archive and pinned CesiumJS. Its 2,193 application files match the official platform image. All 35 Desktop regression tests pass. Local rendering also works with the backend isolated from the Internet, without a cloud account; public basemaps and cloud layers still require a browser Internet connection.

Connector 0.2.1 packages have passed complete public-cloud roundtrips with WebODM 4.0, OpenDroneMap WebUI 3.2.5, and QGIS 3.44.15 LTR / 4.2.3. Native task or project reopening and all-file checksums are verified. QGIS authentication, cloud browsing and sign-out were checked in both supported Qt generations.

This candidate is not yet promoted: the final packaged setup and production UI synchronization checks are still being completed. CPU processing is the supported backend in the full interface. The older standalone CUDA build script is retained for experiments; it is not an advertised GPU mode of the 0.3 full interface. Packages remain unsigned and there is no macOS installer.

# 0.2.1 — validation candidate

Windows renders the UTF-8 interface correctly. The source edition hides commercial activation controls, result downloads have separate clickable targets, and missing Docker/engines produce an actionable focused alert without replacing an existing completed result.

On Windows, Docker Desktop is also detected in its official per-user and system installation directories, so installing Docker does not require restarting an existing desktop session to refresh PATH.

Paused transfers have a direct Resume button and failed transfers have Retry. Reloading the local page restores the current cloud sign-in state. Gaussian models open from an exported reconstruction camera, with a photo viewpoint selector and a working Home reset, instead of an arbitrary view of the bounding box. This changes only the viewer camera; it does not alter the trained model or its quality.

QGIS supports both 3.x (3.34+) and 4.x. The plugin releases completed task wrappers before the next action, uses Qt 6-compatible password/storage enums, and disables actions while packaging or transferring. Signing out clears the cloud catalog. WebODM/WebUI pages use the MosaicEarth page title.

Validation on physical Windows includes source startup without an account, photo import, native WebODM 4.0 cloud roundtrip and viewers, and QGIS 3.44.15 / 4.2.3 project roundtrips preserving external SVG/layout resources. The QGIS task-lifecycle regression passes on both versions; QGIS 4 also completes GUI sign-in, upload, browsing, download and sign-out. A newly trained Linux Gaussian was synchronized to Windows with all 55 files / 33,139,087 bytes verified, including GUI pause/resume, and rendered in native Windows Chrome. OpenDroneMap WebUI 3.2.5 completed its public-cloud roundtrip in Linux. The official Windows wizard and Docker processing remain in progress; this candidate is not yet promoted.

# 0.2.0

This release replaces project-name-only synchronization with explicit, private transfer of originals, finished products, attachments and metadata. Desktop, WebODM, OpenDroneMap WebUI and QGIS share resumable chunks, SHA-256 verification, immutable revisions and revocable cloud sessions. Existing metadata-only endpoints are retired rather than reporting incomplete transfers as successful.

The source edition runs locally without an account. Official Windows/Linux installers use account and device activation, two active devices and a 30-day offline grant. Windows has a native `.exe` wizard. A verified, resumable engine download installs NodeODX and CPU OpenSplat. There is no macOS installer and installers are not code-signed.

NodeODX now offers Complete and Fast output choices. Completed photogrammetry includes a local raster preview. Gaussian splats render in the local Cesium viewer with an explicit local-coordinate label. The workspace adds transfer progress, pause/resume, cloud browsing and all-file downloads.

Validation includes a fresh 18-photo Complete NodeODX reconstruction with orthophoto, DSM, DTM, point cloud and textured mesh; actual WebODM/WebUI push and native-task restore with all 47 files byte-identical; and a native QGIS network roundtrip whose project reopens without access to its source files, preserving CRS, styling, attributes and raster values. Account, device-copy, revocation, quota, chunk retry and cross-account access checks are automated. Windows wizard activation, native Pythonw runtime and uninstallation were exercised under Wine; physical Windows WSL 2/Docker Desktop processing remains unverified. Local Gaussian rendering was checked using an existing real trained result, separately from the new NodeODX reconstruction.

The canonical cloud origin is `https://app.mosaicearth.app`. Viewer derivative preparation is asynchronous and distinct from file-transfer completion. Transfers do not imply public publication, cloud reconstruction or automatic deletion of another device's work.

# Earlier releases

0.1.4 added optional Linux CUDA training. 0.1.3 added local georeferenced raster rendering; 0.1.2 exported PLY, SPZ, SPLAT and 3D Tiles in the input SfM frame. Those versions synchronized only project names and should be upgraded for full cloud file transfer.
