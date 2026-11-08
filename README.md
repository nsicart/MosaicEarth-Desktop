# MosaicEarth Desktop

## Full platform interface — integration in progress

The intended Desktop product is the MosaicEarth web platform running locally: the same branding, public globe, Public Mosaics, MyEarth project/survey/task hierarchy, MyMosaics scene composer and Fly View. MyEarth and MyMosaics must distinguish local storage from cloud storage. Local processing, local editing and local viewing remain available without cloud sign-in in the source edition. The official installer retains account/device activation.

The 0.2 processing workspace is a regression in interface coverage, despite its working reconstruction and file-transfer components. It is not an accepted replacement for the full Desktop 3.6.3 interface. The `fix/desktop-web-parity` branch integrates the shared platform interface with the verified processing, activation and transfer components. Do not promote it until local/cloud projects and scenes, offline startup, public browsing, Fly View, migration and packaged Windows/Linux runs have been exercised. A globe screenshot alone is not proof of functional parity.

A local workspace for photogrammetry, Gaussian splatting and georeferenced imagery, with an offline CesiumJS viewer. NodeODX runs ODX photogrammetry; COLMAP and OpenSplat reconstruct and train Gaussian models. Projects, originals and results remain on your computer until you explicitly synchronize them.

**The AGPL source edition works locally without signing in.** With Python 3 and Docker available, run `./scripts/provision-engines.sh` to build the processing engines and complete interface, then run `python3 src/mosaicearth_desktop.py`. No cloud processing credits are consumed by local reconstruction.

**The official Windows and Linux installers require a purchasing account or an eligible Studio account.** Activation binds the official installation to a device, supports two active computers and permits 30 days of local offline operation between activations. Download/install authorization does not restrict rights granted by the AGPL. These controls protect the official distribution and provisioning service; they cannot make open-source software impossible to copy.

Cloud synchronization transfers the actual originals, finished models, rasters, attachments and project metadata in both directions. Transfers resume in verified chunks and validate SHA-256 checksums. Each saved revision is immutable, private and charged against account storage; it does not silently overwrite another computer's changes. Completed files and cloud viewer preparation have separate states. Local projects continue to work when the cloud is unavailable.

Native plugins use the same protocol for **WebODM**, **OpenDroneMap WebUI** and **QGIS**. See [Connectors](CONNECTORS.md), [Build](BUILD.md), [Linux installation](INSTALL_LINUX.md), [Windows installation](INSTALL_WINDOWS.md), [release notes](RELEASE_NOTES.md) and [third-party licenses](THIRD_PARTY_LICENSES.md).

The cloud service is [app.mosaicearth.app](https://app.mosaicearth.app). Downloads are at [Desktop and connectors](https://app.mosaicearth.app/en/Desktop). macOS has no supported installer in this release.
