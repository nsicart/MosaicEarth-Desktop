"""Bound the texture memory of native ODX browser tiles; never touch masters.

Only embedded PNG/JPEG images change. Geometry, UVs, transforms, hierarchy and
all non-image buffer views remain byte-identical. Originals stay in download/.
"""
import hashlib
import io
import json
import math
import struct
from pathlib import Path
from PIL import Image


def optimize_tile(data, max_edge=1024, max_pixels=4_194_304):
    prefix = b''
    if data[:4] == b'b3dm':
        if len(data) < 28:
            raise ValueError('Truncated B3DM')
        offset = 28 + sum(struct.unpack_from('<4I', data, 12))
        prefix, glb = data[:offset], data[offset:]
    else:
        glb = data
    if glb[:4] != b'glTF' or struct.unpack_from('<I', glb, 4)[0] != 2:
        raise ValueError('Expected GLB 2.0')
    jsize, jtype = struct.unpack_from('<II', glb, 12)
    if jtype != 0x4e4f534a:
        raise ValueError('Invalid GLB JSON chunk')
    doc = json.loads(glb[20:20+jsize])
    bsize, btype = struct.unpack_from('<II', glb, 20+jsize)
    if btype != 0x004e4942 or len(doc.get('buffers', [])) != 1:
        raise ValueError('Expected one embedded GLB buffer')
    binary = glb[28+jsize:28+jsize+bsize]
    views = doc.get('bufferViews', [])
    images = []
    for im in doc.get('images', []):
        if 'bufferView' not in im or im.get('mimeType') not in ('image/png', 'image/jpeg'):
            continue
        view = views[im['bufferView']]
        off, size = view.get('byteOffset', 0), view['byteLength']
        image = Image.open(io.BytesIO(binary[off:off+size]))
        images.append((im, image, min(1, max_edge/max(image.size))))
    pixels = sum(im.width*im.height*scale*scale for _,im,scale in images)
    budget_scale = min(1, math.sqrt(max_pixels/max(pixels, 1)))
    replaced = {}
    output_pixels = 0
    for metadata, im, scale in images:
        target = (max(1, round(im.width*scale*budget_scale)), max(1, round(im.height*scale*budget_scale)))
        output_pixels += target[0]*target[1]
        if target == im.size:
            continue
        im = im.resize(target, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        # Preserve alpha, including atlas gutters. Do not introduce JPEG seams.
        im.save(buf, format='PNG')
        replaced[metadata['bufferView']] = buf.getvalue()
        metadata['mimeType'] = 'image/png'
    if not replaced:
        return data, {'images':len(images), 'preview_pixels':output_pixels, 'changed':False}
    chunks = bytearray()
    for index, view in enumerate(views):
        if view.get('buffer', 0) != 0:
            raise ValueError('External buffer view')
        start, length = view.get('byteOffset', 0), view['byteLength']
        content = replaced.get(index, binary[start:start+length])
        if len(content) != length and index not in replaced:
            raise ValueError('Truncated GLB buffer view')
        chunks.extend(b'\0' * ((-len(chunks)) % 4))
        view['byteOffset'], view['byteLength'] = len(chunks), len(content)
        chunks.extend(content)
    doc['buffers'][0]['byteLength'] = len(chunks)
    chunks.extend(b'\0' * ((-len(chunks)) % 4))
    packed = json.dumps(doc, separators=(',', ':')).encode()
    packed += b' ' * ((-len(packed)) % 4)
    result = struct.pack('<4sII', b'glTF', 2, 28+len(packed)+len(chunks)) + struct.pack('<II',len(packed),0x4e4f534a) + packed + struct.pack('<II',len(chunks),0x004e4942) + chunks
    if prefix:
        prefix = bytearray(prefix)
        struct.pack_into('<I',prefix,8,len(prefix)+len(result))
        result = bytes(prefix)+result
    return result, {'images':len(images), 'preview_pixels':output_pixels, 'changed':True}


def optimize_mesh_tiles(directory: Path):
    marker = directory/'preview-texture-manifest.json'
    previous = {}
    if marker.is_file():
        previous = {row['file']:row for row in json.loads(marker.read_text())}
    paths = sorted(directory.rglob('*.b3dm'))
    # A large terrain can select many leaf tiles simultaneously. Bound the
    # aggregate atlas budget as well as each individual image.
    pixel_budget = min(4_194_304, max(262_144, 67_108_864 // max(1, len(paths))))
    rows = []
    for path in paths:
        original = path.read_bytes()
        relative = str(path.relative_to(directory))
        digest = hashlib.sha256(original).hexdigest()
        old = previous.get(relative)
        if old and old.get('preview_sha256') == digest and old.get('pixel_budget') == pixel_budget:
            rows.append(old)
            continue
        result, meta = optimize_tile(original, max_edge=512 if 'LOD-2' in path.parts else 1024, max_pixels=pixel_budget)
        if result != original:
            temporary = path.with_suffix('.b3dm.tmp')
            temporary.write_bytes(result)
            temporary.replace(path)
        rows.append(dict(meta, file=relative, pixel_budget=pixel_budget, source_bytes=len(original), preview_bytes=len(result), source_sha256=digest, preview_sha256=hashlib.sha256(result).hexdigest()))
    marker.write_text(json.dumps(rows, indent=2))
    return rows
