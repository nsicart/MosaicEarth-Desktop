# MosaicEarth Desktop

A local workspace for photogrammetry, Gaussian splatting and georeferenced imagery, with an offline CesiumJS viewer. NodeODX runs ODX photogrammetry; COLMAP and OpenSplat reconstruct and train Gaussian models. Projects, originals and results remain on your computer until you explicitly synchronize them.

**The AGPL source edition works locally without signing in.** Run `python3 src/mosaicearth_desktop.py` after provisioning the engines. No cloud processing credits are consumed by local reconstruction.

**The official Windows and Linux installers require a purchasing account or an eligible Studio account.** Activation binds the official installation to a device, supports two active computers and permits 30 days of local offline operation between activations. Download/install authorization does not restrict rights granted by the AGPL. These controls protect the official distribution and provisioning service; they cannot make open-source software impossible to copy.

Cloud synchronization transfers the actual originals, finished models, rasters, attachments and project metadata in both directions. Transfers resume in verified chunks and validate SHA-256 checksums. Each saved revision is immutable, private and charged against account storage; it does not silently overwrite another computer's changes. Completed files and cloud viewer preparation have separate states. Local projects continue to work when the cloud is unavailable.

Native plugins use the same protocol for **WebODM**, **OpenDroneMap WebUI** and **QGIS**. See [Connectors](CONNECTORS.md), [Build](BUILD.md), [Linux installation](INSTALL_LINUX.md), [Windows installation](INSTALL_WINDOWS.md), [release notes](RELEASE_NOTES.md) and [third-party licenses](THIRD_PARTY_LICENSES.md).

The cloud service is [app.mosaicearth.app](https://app.mosaicearth.app). Downloads are at [Desktop and connectors](https://app.mosaicearth.app/en/Desktop). macOS has no supported installer in this release.
