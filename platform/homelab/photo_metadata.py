"""Read photo metadata without inventing capture dates or locations.

Upload time belongs to CREATE_DATE. An image without EXIF GPS/date must remain
unknown; assigning a default location can mislead geospatial QA and map users.
"""
import datetime
import io
import math
import re
from PIL import Image


def source_photo_metadata(header):
    result = {'resolution': '', 'lat': '', 'lon': '', 'alt': '', 'capture_date': ''}
    try:
        with Image.open(io.BytesIO(header)) as image:
            result['resolution'] = f'{image.width}x{image.height}'
            exif = image.getexif()
            details = exif.get_ifd(34665) if exif.get(34665) else {}
            raw = details.get(36867) or exif.get(36867) or details.get(36868) or exif.get(306)
            if raw:
                try:
                    date = datetime.datetime.strptime(str(raw).strip('\x00 '), '%Y:%m:%d %H:%M:%S')
                    offset = str(details.get(36881) or '').strip('\x00 ')
                    if re.fullmatch(r'[+-](?:0\d|1[0-4]):[0-5]\d', offset):
                        date = datetime.datetime.fromisoformat(date.isoformat() + offset)
                    result['capture_date'] = date.isoformat()
                except (ValueError, OverflowError):
                    pass
            gps = exif.get_ifd(34853) if exif.get(34853) else {}

            def coordinate(tag, ref_tag, positive, negative, maximum):
                parts = gps.get(tag)
                reference = gps.get(ref_tag)
                if isinstance(reference, bytes):
                    reference = reference.decode('ascii', errors='ignore')
                if not parts or len(parts) != 3 or reference not in {positive, negative}:
                    return ''
                degrees, minutes, seconds = map(float, parts)
                if not 0 <= degrees <= maximum or not 0 <= minutes < 60 or not 0 <= seconds < 60:
                    return ''
                value = (degrees + minutes / 60 + seconds / 3600) * (-1 if reference == negative else 1)
                return f'{value:.8f}' if math.isfinite(value) and abs(value) <= maximum else ''

            result['lat'] = coordinate(2, 1, 'N', 'S', 90)
            result['lon'] = coordinate(4, 3, 'E', 'W', 180)
            if not result['lat'] or not result['lon']:
                result['lat'] = result['lon'] = ''
            if gps.get(6) is not None and gps.get(5) is not None:
                altitude = float(gps[6])
                reference = gps[5]
                if isinstance(reference, bytes):
                    reference = int.from_bytes(reference, 'big')
                if reference in {0, 1} and math.isfinite(altitude):
                    result['alt'] = f'{altitude * (-1 if reference else 1):.3f}'
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError, Image.DecompressionBombError):
        pass
    return result
