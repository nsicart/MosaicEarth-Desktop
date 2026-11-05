# Build and provision

Check out the desired Git revision. On Debian-compatible Linux with Python 3, curl, OpenSSL and dpkg tools, run `./scripts/build-linux.sh`. This produces a Debian package and SHA-256 manifest. The package includes its source revision and the exact Cesium package verified with the npm distribution integrity hash.

Run `./scripts/provision-engines.sh` with a working Docker daemon and Internet access. It builds the NodeODX Fast cutline overlay from the pinned upstream digest and a separate Debian CPU runtime with OpenSplat, COLMAP, GDAL and PROJ. The OpenSplat source revision and libtorch checksum are pinned. Debian build and runtime packages are resolved from Debian repositories during the build, so identical engine image digests across future builds are unverified. Provision once while online. The two processing containers run with Docker networking disabled and never pull on demand.

The Windows workflow invokes `scripts/build-windows.ps1` and publishes an unsigned installer and SHA-256 manifest as CI artifacts. Provision Linux engine images on a Docker Desktop Linux backend before using local processing. See [Windows installation](INSTALL_WINDOWS.md).
