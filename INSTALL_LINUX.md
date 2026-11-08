# Install on Linux

Download the official `mosaicearth-desktop_0.3.0_amd64.deb` from [Desktop](https://app.mosaicearth.app/en/Desktop) and verify its SHA-256. Install with `sudo apt install ./mosaicearth-desktop_0.3.0_amd64.deb`, then launch **MosaicEarth Desktop** from the applications menu or run `mosaicearth-desktop`.

The official package requires a purchasing/Studio account activation for local processing. On first launch, activate your device in the browser with Google, GitHub, Microsoft or your MosaicEarth email, then choose **Install / Resume local engines**. The account menu distinguishes device activation from the optional cloud connection. Two active computers are supported, with a 30-day offline grant. Engine download is about 1.3 GB; allow at least 20 GB of disk space plus datasets. Docker must be installed and accessible to your user. An existing passwordless sudo Docker configuration can be used with `ME_DOCKER='sudo -n docker' mosaicearth-desktop`.

The source edition is independent of cloud accounts: run `./scripts/fetch-cesium.sh`, `./scripts/provision-engines.sh` and `python3 src/mosaicearth_desktop.py`. Build a community Debian package with `ME_DESKTOP_EDITION=community ./scripts/build-linux.sh`. Source redistribution remains governed by the AGPL.

Create a project, import overlapping photos and choose NodeODX photogrammetry or Gaussian training. NodeODX **Complete** produces orthophoto, terrain, point cloud and textured mesh; **Fast** is intended for orthophoto and terrain. Import a georeferenced GeoTIFF for a local map preview. Gaussian models use their local reconstruction frame unless separately georeferenced. See result downloads and all project files for original outputs.

The full interface uses CPU OpenSplat in the local platform container. It does not provision a cloud GPU. The separate `scripts/provision-cuda.sh` recipe is retained for standalone engine experiments and is not a supported GPU mode of this full-interface release.

Project data lives in `$XDG_DATA_HOME/mosaicearth-desktop`, or `~/.local/share/mosaicearth-desktop`. Stop the application before package updates. Uninstallation preserves per-user data and Docker images. An interrupted reconstruction must be restarted; interrupted cloud file transfers resume from verified chunks. Cloud synchronization is optional and transfers complete files; see [Connectors](CONNECTORS.md).
