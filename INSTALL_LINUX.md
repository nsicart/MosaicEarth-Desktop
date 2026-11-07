# Install on Linux

Download the official `mosaicearth-desktop_0.2.0_amd64.deb` from [Desktop](https://app.mosaicearth.app/en/Desktop) and verify its SHA-256. Install with `sudo apt install ./mosaicearth-desktop_0.2.0_amd64.deb`, then launch **MosaicEarth Desktop** from the applications menu or run `mosaicearth-desktop`.

The official package requires a purchasing/Studio account activation for local processing. Open **Installation and local engines**, activate your device, and choose **Install local engines / resume**. Two active computers are supported, with a 30-day offline grant. Engine download is about 1.3 GB; allow at least 20 GB of disk space plus datasets. Docker must be installed and accessible to your user. An existing passwordless sudo Docker configuration can be used with `ME_DOCKER='sudo -n docker' mosaicearth-desktop`.

The source edition is independent of cloud accounts: run `./scripts/provision-engines.sh` and `python3 src/mosaicearth_desktop.py`. Build a community Debian package with `ME_DESKTOP_EDITION=community ./scripts/build-linux.sh`. Source redistribution remains governed by the AGPL.

Create a project, import overlapping photos and choose NodeODX photogrammetry or Gaussian training. NodeODX **Complete** produces orthophoto, terrain, point cloud and textured mesh; **Fast** is intended for orthophoto and terrain. Import a georeferenced GeoTIFF for a local map preview. Gaussian models use their local reconstruction frame unless separately georeferenced. See result downloads and all project files for original outputs.

CPU is the default OpenSplat backend. Optional NVIDIA CUDA on Linux uses `./scripts/provision-cuda.sh` and `mosaicearth-desktop --gaussian-device cuda`; it needs compatible drivers and the NVIDIA Container Toolkit. Both CPU and CUDA engine containers disable networking. Provisioning never starts a cloud GPU.

Project data lives in `$XDG_DATA_HOME/mosaicearth-desktop`, or `~/.local/share/mosaicearth-desktop`. Stop the application before package updates. Uninstallation preserves per-user data and Docker images. An interrupted reconstruction must be restarted; interrupted cloud file transfers resume from verified chunks. Cloud synchronization is optional and transfers complete files; see [Connectors](CONNECTORS.md).
