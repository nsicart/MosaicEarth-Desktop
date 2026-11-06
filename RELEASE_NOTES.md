# 0.1.3 development release

The local workspace supports SQLite projects, GeoTIFF preview in CesiumJS, NodeODX photogrammetry, and CPU COLMAP and OpenSplat with Gaussian PLY export. The optional MyEarth sync copies the selected project name after sign-in. Imagery and model uploads remain local.

The Windows build workflow and Linux installer are tracked separately. See the MosaicEarth Web release report for the recorded installation and engine test results. macOS is deferred.

Version 0.1.1 links the training benchmark and uses local imagery layers for raster preview.

Version 0.1.2 exports PLY, SPZ, SPLAT and a 3D Tiles archive in the input SfM coordinate frame. The original PLY stays byte-identical. The local tile export carries no geographic anchor. A raster can also be inspected as a local image.

Version 0.1.3 renders the georeferenced raster on a local Cesium surface with no online base layer.
