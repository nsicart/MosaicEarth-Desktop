"""KSPLAT 0.1 reader matching GaussianSplats3D SplatBuffer's section format.

Format reference: github.com/mkkellogg/GaussianSplats3D/src/loaders/SplatBuffer.js.
Keep centres in their source coordinate frame and preserve SH through degree 2.
"""
import struct
import numpy as np


def read_ksplat(data: bytes):
    def uint(offset, fmt='<I'):
        return struct.unpack_from(fmt, data, offset)[0]
    if len(data) < 5120 or data[:2] != b'\x00\x01':
        raise ValueError('Unsupported or truncated KSPLAT; expected complete version 0.1.')
    sections, active, capacity, total = struct.unpack_from('<4I', data, 4)
    compression = uint(20, '<H')
    if compression not in (0, 1, 2) or not 0 < sections == active <= 4096 or capacity != total or total < 1:
        raise ValueError('Invalid or incomplete KSPLAT header.')
    base = 4096 + sections * 1024
    if base > len(data):
        raise ValueError('Truncated KSPLAT section headers.')
    low, high = struct.unpack_from('<2f', data, 36)
    low, high = low or -1.5, high or 1.5
    if not np.isfinite([low, high]).all() or high <= low:
        raise ValueError('Invalid KSPLAT SH compression range.')
    output = []
    for section in range(sections):
        h = 4096 + section * 1024
        count, maximum, bucket_size, buckets = struct.unpack_from('<4I', data, h)
        block = uint(h + 16, '<f')
        bucket_bytes = uint(h + 20, '<H')
        scale_range, storage, full, partial = struct.unpack_from('<4I', data, h + 24)
        degree = uint(h + 40, '<H')
        if not 0 < count == maximum <= total or degree > 2:
            raise ValueError('Invalid KSPLAT section count or unsupported SH degree.')
        sh_width = (0, 9, 24)[degree]
        scalar_bytes = 4 if compression == 0 else 2
        sh_bytes = (4, 2, 1)[compression]
        stride = (44 if compression == 0 else 24) + sh_width * sh_bytes
        bucket_storage = partial * 4 + buckets * bucket_bytes
        size = bucket_storage + stride * maximum
        if base + size > len(data) or (storage and storage != size):
            raise ValueError('Truncated or inconsistent KSPLAT section payload.')
        if compression and (bucket_bytes != 12 or not bucket_size or full + partial != buckets or not np.isfinite(block) or block <= 0):
            raise ValueError('Invalid KSPLAT bucket metadata.')
        payload = base + bucket_storage
        def array(dtype, width, offset):
            return np.ndarray((count, width), dtype=dtype, buffer=data, offset=payload+offset, strides=(stride, np.dtype(dtype).itemsize)).copy()
        numeric = '<f4' if compression == 0 else '<f2'
        positions = array('<f4' if compression == 0 else '<u2', 3, 0).astype(np.float32)
        scales = array(numeric, 3, 3 * scalar_bytes).astype(np.float32)
        rotations = array(numeric, 4, 6 * scalar_bytes).astype(np.float32)[:, [1, 2, 3, 0]]
        rgba = array('u1', 4, 10 * scalar_bytes)
        if compression:
            lengths = np.frombuffer(data, dtype='<u4', count=partial, offset=base)
            if full * bucket_size + int(lengths.astype(np.uint64).sum()) != count or np.any(lengths > bucket_size):
                raise ValueError('KSPLAT bucket lengths do not match the splat count.')
            counts = np.concatenate([np.full(full, bucket_size, dtype=np.int64), lengths])
            centres = np.frombuffer(data, dtype='<f4', count=buckets*3, offset=base+partial*4).reshape(-1, 3)
            scale_range = scale_range or 32767
            positions = (positions - scale_range) * (block / (2 * scale_range)) + np.repeat(centres, counts.astype(np.int64), axis=0)
        sh = None
        if sh_width:
            sh = array(('<f4', '<f2', 'u1')[compression], sh_width, 10 * scalar_bytes + 4).astype(np.float32)
            if compression == 2:
                sh = sh / 255 * (high-low) + low
            if degree == 2:
                sh = np.concatenate([sh[:, :9].reshape(-1, 3, 3), sh[:, 9:].reshape(-1, 3, 5)], axis=2).reshape(-1, 24)
        norms = np.linalg.norm(rotations, axis=1, keepdims=True)
        if not all(np.isfinite(a).all() for a in (positions, scales, rotations)) or np.any(scales < 0) or np.any(norms < 1e-8) or (sh is not None and not np.isfinite(sh).all()):
            raise ValueError('KSPLAT contains invalid Gaussian attributes.')
        output.append((positions, rgba[:, :3], np.maximum(scales, 1e-8), rotations / norms, rgba[:, 3], sh))
        base += size
    if sum(len(item[0]) for item in output) != total or base != len(data):
        raise ValueError('KSPLAT size does not match its declared sections.')
    width = max((item[5].shape[1] if item[5] is not None else 0) for item in output)
    sh_parts = []
    if width:
        for item in output:
            sh = item[5]
            if sh is None:
                sh = np.zeros((len(item[0]), width), dtype=np.float32)
            elif sh.shape[1] < width:
                sh = np.pad(sh.reshape(-1, 3, 3), ((0,0),(0,0),(0,5))).reshape(-1, 24)
            sh_parts.append(sh)
    return tuple(np.concatenate([item[i] for item in output]) for i in range(5)) + (np.concatenate(sh_parts) if width else None, None)
