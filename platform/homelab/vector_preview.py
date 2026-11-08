"""Render dense 2D line overlays without an unbounded ground-primitive workload."""
import json
import subprocess
import tempfile
from pathlib import Path


def build_dense_line_preview(collection: dict, destination: Path) -> dict:
    features = collection.get('features') or []
    lines = []
    for feature in features:
        geometry = feature.get('geometry') or {}
        if geometry.get('type') == 'LineString':
            lines.append(geometry['coordinates'])
        elif geometry.get('type') == 'MultiLineString':
            lines.extend(geometry['coordinates'])
        else:
            return {}
    points = [point for line in lines for point in line]
    if len(points) <= 100000 or any(len(point) != 2 for point in points):
        return {}
    west, south = min(p[0] for p in points), min(p[1] for p in points)
    east, north = max(p[0] for p in points), max(p[1] for p in points)
    if not (-180 <= west < east <= 180 and -85 < south < north < 85) or east-west > 180:
        return {}
    # Include stroke edges; all source vertices/features are rasterized, with
    # their exact vector geometry retained in overlay.geojson and the master.
    padding = max(east-west, north-south) * .002
    bounds = [west-padding, south-padding, east+padding, north+padding]
    width, height = bounds[2]-bounds[0], bounds[3]-bounds[1]
    size = [max(1, round(4096*width/max(width,height))), max(1, round(4096*height/max(width,height)))]
    from PIL import Image, ImageFilter
    from .raster_products import build_geographic_image_pyramid
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='me-dense-lines-') as temporary:
        root = Path(temporary)
        source, mask = root/'source.geojson', root/'lines.tif'
        source.write_text(json.dumps(collection))
        subprocess.run(['gdal_rasterize', '-burn', '255', '-ot', 'Byte', '-of', 'GTiff',
                        '-a_srs', 'EPSG:4326', '-te', *map(str,bounds), '-ts', *map(str,size), str(source), str(mask)],
                       capture_output=True, text=True, check=True, timeout=120)
        with Image.open(mask) as raster:
            alpha = raster.convert('L').filter(ImageFilter.MaxFilter(5))
        image = Image.new('RGBA', tuple(size), (153,204,0,0));image.putalpha(alpha)
        preview = destination/'vector-overlay.png';image.save(preview)
        info = build_geographic_image_pyramid(preview,bounds,destination)
    metadata = {'west':bounds[0],'south':bounds[1],'east':bounds[2],'north':bounds[3],'bounds_wgs84':bounds,'min_zoom':info['min_zoom'],'max_zoom':info['max_zoom'],
                'georeferencing_version':2,'source_vertex_count':len(points),'source_feature_count':len(features),
                'viewer_representation':'rasterized-vector','display_size':size,'all_features_rendered':True,'source_preserved':True}
    (destination/'bounds.json').write_text(json.dumps(metadata))
    return metadata
