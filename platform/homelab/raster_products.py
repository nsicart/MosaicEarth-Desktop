"""Georeferenced raster products built with the GDAL runtime shipped in the image."""
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path


def vertical_metres_factor(info: dict) -> float:
    unit = str(info["bands"][0].get("unit") or "").strip().lower()
    if not unit:
        from pyproj import CRS
        crs = CRS.from_wkt(info["coordinateSystem"]["wkt"])
        vertical = next((axis for axis in crs.axis_info if axis.direction == "up"), None)
        return float(vertical.unit_conversion_factor) if vertical else 1.0
    factors = {"m": 1, "meter": 1, "metre": 1, "meters": 1, "metres": 1,
               "ft": .3048, "foot": .3048, "feet": .3048, "us survey foot": 1200/3937}
    if unit not in factors:
        raise ValueError("The DEM vertical unit is not supported for metric terrain analysis.")
    return factors[unit]


def read_geographic_dem(source: Path, bounds: list, size: list, *, vertex_samples=False):
    """Reproject a bounded window; optionally place pixel centres on grid vertices."""
    import numpy as np
    from PIL import Image
    west, south, east, north = bounds
    if vertex_samples:
        dx, dy = (east-west)/(size[0]-1), (north-south)/(size[1]-1)
        west, south, east, north = west-dx/2, south-dy/2, east+dx/2, north+dy/2
    with tempfile.TemporaryDirectory(prefix="me-geographic-dem-") as temporary:
        target = Path(temporary) / "dem.tif"
        subprocess.run(["gdalwarp", "-novshift", "-t_srs", "EPSG:4326", "-te", *map(str, [west, south, east, north]),
                        "-ts", *map(str, size), "-r", "bilinear", "-ot", "Float32", "-dstnodata", "nan",
                        "-wm", "128", str(source), str(target)],
                       capture_output=True, text=True, check=True, timeout=300)
        factor = vertical_metres_factor(raster_metadata(source))
        with Image.open(target) as image:
            return np.array(image, dtype=np.float32) * np.float32(factor)


def build_geographic_image_pyramid(image: Path, bounds: list, destination: Path) -> dict:
    """The input is already resampled to a north-up EPSG:4326 geographic grid."""
    west, south, east, north = bounds
    with tempfile.TemporaryDirectory(prefix="me-geographic-color-") as temporary:
        source = Path(temporary) / "rgba.tif"
        subprocess.run(["gdal_translate", "-a_srs", "EPSG:4326", "-a_ullr",
                        *map(str, [west, north, east, south]), str(image), str(source)],
                       capture_output=True, text=True, check=True, timeout=120)
        return build_raster_pyramid(source, destination, xyz=True)


def metric_analysis_dem(source: Path, temporary: Path) -> Path:
    """GDAL slope/contours require horizontal and vertical values in metres."""
    from pyproj import CRS
    info = raster_metadata(source)
    crs = CRS.from_wkt(info["coordinateSystem"]["wkt"])
    result = source
    if not crs.is_projected or abs(crs.axis_info[0].unit_conversion_factor - 1) > 1e-8:
        west, south, east, north = info["bounds_wgs84"]
        lon, lat = (west+east)/2, (south+north)/2
        epsg = (32661 if lat >= 0 else 32761) if abs(lat) > 80 else (32600 if lat >= 0 else 32700)+min(60,max(1,int((lon+180)//6)+1))
        result = temporary / "metric-horizontal.tif"
        subprocess.run(["gdalwarp", "-novshift", "-t_srs", f"EPSG:{epsg}", "-ot", "Float32", "-r", "bilinear",
                        str(source), str(result)], capture_output=True, text=True, check=True, timeout=300)
    factor = vertical_metres_factor(info)
    if factor != 1:
        scaled = temporary / "metric-height.tif"
        subprocess.run(["gdal_translate", "-ot", "Float32", "-scale", "0", "1", "0", str(factor),
                        str(result), str(scaled)], capture_output=True, text=True, check=True, timeout=300)
        subprocess.run(["/usr/bin/python3", "/usr/bin/gdal_edit.py", "-units", "m", str(scaled)],
                       capture_output=True, text=True, check=True, timeout=30)
        result = scaled
    return result


def raster_metadata(source: Path) -> dict:
    command = ["gdalinfo", "-json", str(source)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=True)
    info = json.loads(result.stdout)
    ring = (info.get("wgs84Extent") or {}).get("coordinates", [[]])[0]
    if not info.get("coordinateSystem") or len(ring) < 4:
        raise ValueError("The raster needs a readable coordinate system and geographic bounds.")
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    if not all(math.isfinite(v) for v in xs + ys) or not (-180 <= min(xs) < max(xs) <= 180 and -85.0511 <= min(ys) < max(ys) <= 85.0511):
        raise ValueError("The raster bounds cannot be displayed in Web Mercator.")
    info["bounds_wgs84"] = [min(xs), min(ys), max(xs), max(ys)]
    return info


def valid_raster_coverage(source: Path) -> dict:
    # Debian's GDAL Python bindings belong to /usr/bin/python3, separate from
    # the application's Python. Read the mask in strips to bound memory.
    script = """
import json, sys
from osgeo import gdal, osr
import numpy as np
gdal.UseExceptions()
ds = gdal.Open(sys.argv[1])
srs = osr.SpatialReference(wkt=ds.GetProjection())
if not srs.IsProjected():
    print('{}'); sys.exit(0)
t = ds.GetGeoTransform()
mask = ds.GetRasterBand(1).GetMaskBand()
count = sum(int(np.count_nonzero(mask.ReadAsArray(0, y, ds.RasterXSize, min(128, ds.RasterYSize-y)))) for y in range(0, ds.RasterYSize, 128))
area = count * abs(t[1]*t[5]-t[2]*t[4]) * srs.GetLinearUnits()**2
print(json.dumps({'area_ha':area/10000, 'valid_pixels':count, 'crs':srs.GetAuthorityCode(None), 'method':'valid raster mask in projected CRS'}))
"""
    result = subprocess.run(["/usr/bin/python3", "-c", script, str(source)],
                            capture_output=True, text=True, check=True, timeout=180)
    return json.loads(result.stdout)


def build_raster_preview(source: Path, destination: Path, *, info: dict | None = None) -> None:
    """Decode only a bounded GDAL thumbnail, leaving Pillow's safety limit intact."""
    from PIL import Image
    info = info or raster_metadata(source)
    width, height = map(int, info["size"])
    if min(width, height) <= 0:
        raise ValueError("Invalid raster dimensions.")
    ratio = min(1.0, 1600 / max(width, height))
    size = [max(1, round(width * ratio)), max(1, round(height * ratio))]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".preview-", dir=destination.parent) as temporary:
        scaled = Path(temporary) / "preview.png"
        subprocess.run(["gdal_translate", "-of", "PNG", "-ot", "Byte", "-outsize",
                        *map(str, size), "-r", "average", str(source), str(scaled)],
                       capture_output=True, text=True, check=True, timeout=300)
        with Image.open(scaled) as image:
            if image.size != tuple(size):
                raise ValueError("Unexpected raster preview dimensions.")
            image.convert("RGBA").save(destination, "WEBP")


def build_raster_pyramid(source: Path, destination: Path, *, xyz: bool = False) -> dict:
    """Warp using the embedded CRS; never stretch a preview over a guessed box."""
    info = raster_metadata(source)
    west, south, east, north = info["bounds_wgs84"]
    # Select native display resolution and bound work for very large extents.
    resolution = max((east-west) / info["size"][0], (north-south) / info["size"][1])
    maximum = min(22, max(0, math.ceil(math.log2(360 / (256 * resolution)))))
    while maximum > 0:
        n = 2 ** maximum
        columns = math.ceil((east-west) / 360 * n) + 1
        rows = math.ceil((math.asinh(math.tan(math.radians(north))) - math.asinh(math.tan(math.radians(south)))) / (2*math.pi) * n) + 1
        if columns * rows <= 12000:
            break
        maximum -= 1
    minimum = max(0, maximum - 7)
    info.update(min_zoom=minimum, max_zoom=maximum)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".raster-", dir=destination.parent) as temporary:
        temp = Path(temporary)
        display_source = source
        if any(band.get("type") != "Byte" for band in info.get("bands", [])):
            display_source = temp / "display.tif"
            subprocess.run(["gdal_translate", "-ot", "Byte", "-scale", str(source), str(display_source)],
                           capture_output=True, text=True, check=True, timeout=300)
        command = ["/usr/bin/python3", "/usr/bin/gdal2tiles.py", "--processes=2", "--webviewer=none",
                   "--exclude", "--resampling=average", f"--zoom={minimum}-{maximum}"]
        if xyz:
            command.append("--xyz")
        command += [str(display_source), str(temp / "tiles")]
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=1800)
        if not next((temp / "tiles").glob("*/*/*.png"), None):
            raise ValueError("No valid map tiles were produced from this raster.")
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(temp / "tiles", destination, dirs_exist_ok=True)
    return info
