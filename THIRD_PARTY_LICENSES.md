# Third-party licenses

| Component | License and corresponding source |
| --- | --- |
| CesiumJS 1.144.0 | Apache-2.0. The package includes its upstream LICENSE and ThirdParty notices. [Cesium source and build instructions](https://github.com/CesiumGS/cesium/tree/1.144). |
| NodeODX and ODX | AGPL-3.0. [NodeODX](https://github.com/WebODM/NodeODX) and [ODX](https://github.com/WebODM/ODX). The exact NodeODX distribution digest and the Fast cutline overlay are in the engine Dockerfile. |
| OpenSplat | AGPL-3.0. The engine is built at [revision e33f9dd](https://github.com/WebODM/OpenSplat/tree/e33f9dde8ef9c9d59852ffe4d12e0adddcf1fc1b). |
| PyTorch libtorch 2.3.1 CPU | BSD-3-Clause plus component notices. [PyTorch source](https://github.com/pytorch/pytorch/tree/v2.3.1). |
| COLMAP, GDAL, PROJ, PROJ data | Debian packages with upstream copyright files and Debian source packages. Review individual grid licenses before distributing additional grids. [Debian package source](https://sources.debian.org/). |
| NSIS 3.09 | zlib/libpng license for the core installer, with additional bundled component notices. [NSIS license](https://nsis.sourceforge.io/License). |
| CPython 3.12.10 on Windows | PSF and other notices in the embedded distribution. [CPython source](https://github.com/python/cpython/tree/v3.12.10). |

The Desktop installer includes the application and CesiumJS, plus Python on Windows. The separate provisioning process builds local processing images. Before redistributing those images, verify the complete corresponding source for the pinned upstream distributions, include the Docker and patch scripts, and review package-level notices.
