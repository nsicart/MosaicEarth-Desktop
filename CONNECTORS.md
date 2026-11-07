# Full project synchronization

All connectors are AGPL-3.0-or-later and work with an existing MosaicEarth Cloud account for cloud transfers. Local use of their host applications does not require MosaicEarth sign-in. Passwords are used only for sign-in; revocable session tokens remain in the host session or process. Sign out to revoke them.

Transfers include actual original bytes, finished products, textures and metadata. A checksum manifest and 4 MiB chunks support safe retries. Downloads verify every file. Changed local files are preserved: download a different revision into a new folder to resolve conflicts. This is explicit revision synchronization, not simultaneous editing or automatic deletion propagation. Very large transfers need adequate local disk space and cloud account storage. A revision supports up to 100,000 files; split larger projects into tasks.

## WebODM and OpenDroneMap WebUI

Download the matching `MosaicEarth-webodm-0.2.1.zip` or `MosaicEarth-webui-0.2.1.zip`. Install it through the host's Administration → Plugins screen, enable it and restart the host web and worker services if requested. WebODM requires 2.9.4 or later; OpenDroneMap WebUI requires 3.2.5 or later. The two ZIPs have different native manifests and must not be interchanged.

Open **MosaicEarth** in the main menu and sign in. Select a local completed task and synchronize. The existing host worker transfers original photos, finished assets and task options without launching a new reconstruction. Cloud viewer layers are prepared separately. Browse the cloud, select a saved revision or latest cloud project, choose a destination host project and download. The plugin restores all files into a new private task and asks the host to prepare its native viewers. Original downloads remain in the resumable staging directory; native viewers may produce additional derivatives.

Host project permissions apply to both upload and restore. Pause stops transfers between chunks; repeat the same action to resume. A native WebODM/WebUI snapshot restores the task structure. A project from another application retains every file and maps compatible orthophotos, elevation models and point clouds into host viewers; unsupported formats remain downloadable attachments. Gaussian splats remain intact but need MosaicEarth's Gaussian viewer.

## QGIS

In QGIS 3.34–3.x or QGIS 4.x, choose Plugins → Manage and Install Plugins → Install from ZIP and select `MosaicEarth-qgis-0.2.1.zip`. Enable MosaicEarth, then open its toolbar/menu action.

Sign in and synchronize the current project. The connector saves project settings, CRS, layer order, styles, layouts and local image/SVG resources. It materializes vector providers into GeoPackage and raster providers into GeoTIFF, and also preserves original local files and sidecars. Local point-cloud/mesh masters are retained. Export unsupported or remote mesh/point-cloud providers to local files before synchronization; an unavailable provider produces an explicit error.

Browse and download into a chosen directory. A QGIS snapshot opens its portable `.qgz` project. Other cloud exports add compatible raster, vector and point-cloud files; all remaining models and attachments are still saved. Authentication and file transfers use QGIS background tasks. Packaging reads the current project on the main thread so native providers and styles remain consistent.

## Desktop

Open MosaicEarth Cloud in the local workspace, sign in and choose **Synchronize all files to MyEarth**. **Browse cloud projects** downloads a revision or prepares a fresh export of native cloud files. A Desktop revision restores its project, processing mode, original inputs, result downloads and local preview. Other connector/cloud exports preserve all files; supported original images are also available for local processing. **All project files** exposes every retained attachment.

No connector publishes data publicly, spends processing credits or starts a GPU pod during file synchronization. Existing cloud viewer conversion runs after validated uploads. Cloud-created projects export available originals, canonical masters, native result archives, supported metadata and prepared viewer files. External links that are not stored files are metadata, not a copy of the remote service.

Windows validation covers native WebODM 4.0 and QGIS 3.44.15 LTR / 4.2.3. OpenDroneMap WebUI 3.2.5 was tested in its Linux container. QGIS tests include isolated reopening after a public-cloud roundtrip, Unicode attributes, SVG symbols and layout images, plus consecutive background operations in both Qt versions. Native host imports finish asynchronously; wait for their image counts and viewers to finish preparing.
