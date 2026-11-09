# Install on Linux

Download the official `mosaicearth-desktop_0.4.3_amd64.deb` from [Desktop](https://app.mosaicearth.app/en/Desktop) and verify its [OpenPGP signature and SHA-256](SIGNING.md). Install with `sudo apt install ./mosaicearth-desktop_0.4.3_amd64.deb`, then launch **MosaicEarth Desktop** from the applications menu or run `mosaicearth-desktop`.

The official package requires a purchasing/Studio account activation for local processing. On first launch, activate your device in the browser with Google, GitHub, Microsoft or your MosaicEarth email, then choose **Install / Resume local engines**. The account menu distinguishes device activation from the optional cloud connection. Two active computers are supported, with a 30-day offline grant. The CPU engine download is about 1.4 GB and the optional GPU bundle is about 9.1 GB. Allow at least 20 GB free for CPU setup and another 50 GB for GPU setup, plus datasets. Docker must be installed and accessible to your user. An existing passwordless sudo Docker configuration can be used with `ME_DOCKER='sudo -n docker' mosaicearth-desktop`.

The source edition is independent of cloud accounts. Follow [Install from source](INSTALL_FROM_SOURCE.md) for prerequisites, provisioning and first launch. Build a community Debian package with `ME_DESKTOP_EDITION=community ./scripts/build-linux.sh`. Source redistribution remains governed by the AGPL.

Create a project, import overlapping photos and choose NodeODX photogrammetry or Gaussian training. NodeODX **Complete** produces orthophoto, terrain, point cloud and textured mesh; **Fast** produces an orthophoto and map tiles. Import a georeferenced GeoTIFF for a local map preview. Gaussian models use their local reconstruction frame unless separately georeferenced. See result downloads and all project files for original outputs.

The full interface automatically uses compatible NVIDIA GPU engines when available and otherwise uses CPU. Linux GPU support requires the NVIDIA driver and NVIDIA Container Toolkit configured for Docker. [GPU setup, compatibility and fallback](GPU.md). No cloud GPU is provisioned.

Project data lives in `$XDG_DATA_HOME/mosaicearth-desktop`, or `~/.local/share/mosaicearth-desktop`. Stop the application before package updates. Uninstallation preserves per-user data and Docker images. An interrupted reconstruction must be restarted; interrupted cloud file transfers resume from verified chunks. Cloud synchronization is optional and transfers complete files; see [Connectors](CONNECTORS.md).
