# Third-party licenses

| Component | License and corresponding source |
| --- | --- |
| CesiumJS 1.144.0 | Apache-2.0. The package includes its upstream LICENSE and ThirdParty notices. [Cesium source and build instructions](https://github.com/CesiumGS/cesium/tree/1.144). |
| Shared MosaicEarth interface | The application's Python/JavaScript is under this repository's AGPL license. The vendored baseline and checksums are recorded in `platform/source-manifest.json`; local adaptations are tracked in Git. |
| Font Awesome Free 5.14.0 | Icons CC BY 4.0, fonts SIL OFL 1.1, code MIT. Exact upstream notice is included in `resources/notices/FontAwesome-LICENSE.txt`. |
| Raleway | SIL Open Font License 1.1. Notice included in `resources/notices/Raleway-OFL.txt`; the bundled font bytes are pinned in the static manifest. |
| Potree and bundled libraries | Their upstream license files remain beside the bundled files under `Potree/`. Potree's BSD license and bundled MIT/BSD/Apache notices are preserved. |
| Bootstrap, jQuery and UI plugins | Upstream copyright/license headers remain in the pinned payload. The old INSPINIA theme payloads are excluded. |
| NodeODX and ODX | AGPL-3.0. [NodeODX v2.4.0, revision 0229646](https://github.com/WebODM/NodeODX/tree/02296467c0ca2142c2c7771876788bacbed102f6) and [ODX v3.8.5, revision d1e15c7](https://github.com/WebODM/ODX/tree/d1e15c7a1daf1be77713cc68cd063a05d79edbb5). The distribution digest and the Fast cutline overlay are in the engine Dockerfile. |
| OpenSplat | AGPL-3.0. The engine is built at [revision e33f9dd](https://github.com/WebODM/OpenSplat/tree/e33f9dde8ef9c9d59852ffe4d12e0adddcf1fc1b). |
| PyTorch libtorch 2.3.1 CPU | BSD-3-Clause plus component notices. [PyTorch source](https://github.com/pytorch/pytorch/tree/v2.3.1). |
| PyTorch libtorch 2.3.1 CUDA and NVIDIA CUDA 11.8 | Optional verified GPU engine bundle. [PyTorch source](https://github.com/pytorch/pytorch/tree/v2.3.1) and [NVIDIA CUDA 11.8 EULA](https://docs.nvidia.com/cuda/archive/11.8.0/eula/index.html). CUDA development tools remain in build stages. The optional OpenSplat GPU bundle includes CUDA runtime libraries, libtorch, and notices under resources/notices; it is downloaded separately from the small installer. The pinned NodeODX GPU bundle includes its upstream CUDA 12.9 runtime and notices. |
| COLMAP, GDAL, PROJ, PROJ data | Debian packages with upstream copyright files and Debian source packages. Review individual grid licenses before distributing additional grids. [Debian package source](https://sources.debian.org/). |
| NSIS 3.09 | zlib/libpng license for the core installer, with additional bundled component notices. [NSIS license](https://nsis.sourceforge.io/License). |
| CPython 3.12.10 on Windows | PSF and other notices in the embedded distribution. [CPython source](https://github.com/python/cpython/tree/v3.12.10). |

The Desktop installer includes the application and CesiumJS, plus Python on Windows. Source builds provision processing images from the versioned recipes. Official installations can download the verified CPU image archive listed in `resources/engine-payload.json`. Complete engine sources, upstream submodules, patches, build instructions and notices are available without sign-in at [MosaicEarth engine source](https://app.mosaicearth.app/open-source). This repository supplies the Desktop entry points, exports and image build recipes. Image containers retain their package copyright files. CUDA is not included in the official CPU archive.

## Default map data and services

The shared map uses CesiumJS (Apache 2.0), Mapzen terrain tiles from AWS Open Data, OpenStreetMap (ODbL), EOX cloudless 2016 (CC BY 4.0), and IGN PNOA (CC BY 4.0). The Natural Earth Spain boundary (public domain) selects the national imagery provider. Full data-source credits, service links, capture-year limitations and resampling notes are distributed in `platform/homelab/map_sources.html`, served locally at `/map-sources`. Photon supplies explicitly submitted place searches through the canonical shared cache. Public endpoints have no uptime guarantee; no bulk/offline basemap download is offered. Map licences do not change the ownership of user assets.

## Worldwide background terrain (0.4.2)

Re:Earth Terrain serves Mapterhorn-derived quantized meshes. Service implementation: MIT; terrain data: CC BY 4.0 and the upstream regional notices at https://download.mapterhorn.com/attribution.json. Source and service information: https://github.com/reearth/reearth-terrain and https://terrain.reearth.land/. These remote datasets are not bundled in the installer.

The two bundled polar grids in `platform/homelab/terrain-data` are derived from NOAA NCEI ETOPO 2022 60 arc-second **surface** elevation and its EGM2008 geoid. NOAA publishes these data under CC0 1.0; credit: NOAA National Centers for Environmental Information (2022), ETOPO 2022 15 Arc-Second Global Relief Model, https://doi.org/10.25921/fd45-gt74. The build script resamples longitude, clamps ocean bathymetry to sea surface, adds geoid undulation and stores quarter-metre samples. Reproduce them with `scripts/build_polar_terrain.py` in a Python environment with GDAL and NumPy. Metadata and hashes accompany the grids. These background data are not suitable for navigation or survey certification.

The 0.4.2 global fallback grid is derived from NOAA ETOPO 2022 (CC0-1.0), at 0.25° resolution with ellipsoidal heights. Its provenance and hashes are in `platform/homelab/terrain-data/global-etopo2022-v1.json`. It is contextual display terrain, not survey or navigation data.

## Online imagery service (0.4.3)

Esri World Imagery is accessed through ArcGIS Location Platform under its service and data terms. It is not bundled or licensed under the AGPL. Temporary per-client session tokens are issued by a private broker; no permanent Esri credential is included in the source or installer. The viewer preserves Powered by Esri and upstream data attribution, with EOX/PNOA open fallbacks. See [map service terms and configuration](MAP_SERVICES.md).
