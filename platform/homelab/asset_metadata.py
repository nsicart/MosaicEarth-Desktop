"""Apply the inspected product identity instead of a filename or upload hint."""


def derivative_version(asset):
    """Stable per completed derivative revision; bypass old browser cache entries."""
    import hashlib
    identity = str(asset.get("UPDATED_AT") or asset.get("SOURCE_SHA256") or asset.get("ID") or "")
    return hashlib.sha256(("georeferenced-v2:" + identity).encode()).hexdigest()[:16]


def apply_inspected_identity(asset, prep):
    inspection = prep.get("inspection") or {}
    asset_class = str(prep.get("asset_class") or "")
    identity = {
        "POINT_CLOUD": ("pointcloud", "pointcloud"),
        "MESH": ("model3d", "mesh"),
        "CESIUM_3D_TILES": ("model3d", "3dtiles"),
        "GAUSSIAN_SPLAT": ("model3d", "gaussian_splat"),
        "VECTOR": ("orthophoto", "vector"),
        "RASTER_IMAGERY": ("orthophoto", "orthophoto"),
        "RASTER_GENERIC": ("orthophoto", "raster"),
        "CESIUM_TERRAIN": ("orthophoto", "terrain"),
    }.get(asset_class)
    if asset_class == "RASTER_ELEVATION":
        subtype = asset.get("ASSET_SUBTYPE")
        if subtype not in ("dsm", "dtm"):
            subtype = "dtm" if "dtm" in str(asset.get("SOURCE_FILE_NAME", "")).lower() else "dsm"
        identity = ("orthophoto", subtype)
    if identity:
        asset["ASSET_TYPE"], asset["ASSET_SUBTYPE"] = identity
        asset["RESULT_KIND"] = identity[1]
    if asset_class == "GAUSSIAN_SPLAT":
        asset["OUTPUT_FORMAT"] = "SPZ · PLY · 3D Tiles"
    # The derivative builder uses the embedded CRS when present, and the
    # user's placement only for unreferenced models. Its origin is authoritative.
    for field, key in (("LATITUDE", "lat"), ("LONGITUDE", "lon"), ("ALTITUDE", "alt")):
        if prep.get(key) is not None:
            asset[field] = float(prep[key])
    asset["HAS_EMBEDDED_GEOREF"] = bool(inspection.get("has_embedded_georef"))
