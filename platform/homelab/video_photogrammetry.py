"""Video-to-Photogrammetry Keyframe & EXIF GPS Injector + Universal Upload Georeference Inspector."""
from __future__ import annotations

import io
import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .geo_video import (
    TELEMETRY_EXTENSIONS,
    VIDEO_EXTENSIONS,
    _is_valid_lat_lon,
    parse_telemetry_file,
    probe_video_metadata_and_embedded_telemetry,
)


def _deg_to_dms_rationals(deg_abs: float) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]:
    d = int(math.floor(deg_abs))
    rem_m = (deg_abs - d) * 60.0
    m = int(math.floor(rem_m))
    s_num = int(round((rem_m - m) * 60.0 * 10000.0))
    if s_num >= 600000:
        s_num -= 600000
        m += 1
    if m >= 60:
        m -= 60
        d += 1
    return ((d, 1), (m, 1), (s_num, 10000))


def build_minimal_exif_gps_bytes(
    lat: float,
    lon: float,
    alt_m: float = 0.0,
    make: str = "MosaicEarth",
    model: str = "VideoPhotogrammetryFrame",
) -> bytes:
    """Builds a standards-compliant big-endian TIFF/EXIF APP1 payload (`Exif\\0\\0` + IFD0 + GPS IFD)
    readable by Pillow, Exiv2, OpenSfM, OpenDroneMap, and COLMAP."""
    lat_ref = b"S\x00" if lat < 0 else b"N\x00"
    lon_ref = b"W\x00" if lon < 0 else b"E\x00"
    alt_ref = 1 if alt_m < 0 else 0
    lat_dms = _deg_to_dms_rationals(abs(lat))
    lon_dms = _deg_to_dms_rationals(abs(lon))
    alt_rat = (int(round(abs(alt_m) * 1000.0)), 1000)

    make_b = (make.encode("ascii", errors="ignore") or b"MosaicEarth") + b"\x00"
    if len(make_b) % 2:
        make_b += b"\x00"
    model_b = (model.encode("ascii", errors="ignore") or b"VideoFrame") + b"\x00"
    if len(model_b) % 2:
        model_b += b"\x00"

    # Big-endian TIFF ('MM', 0x002A, IFD0 at offset 8)
    # IFD0 has 3 entries: Make (0x010F), Model (0x0110), GPSIFDPointer (0x8825)
    ifd0_count = 3
    ifd0_off = 8
    ifd0_end = ifd0_off + 2 + ifd0_count * 12 + 4  # 8 + 2 + 36 + 4 = 50

    make_off = ifd0_end
    model_off = make_off + len(make_b)
    gps_ifd_off = model_off + len(model_b)

    # GPS IFD has 7 entries: VersionID(0), LatRef(1), Lat(2), LonRef(3), Lon(4), AltRef(5), Alt(6)
    gps_count = 7
    gps_end = gps_ifd_off + 2 + gps_count * 12 + 4
    lat_off = gps_end
    lon_off = lat_off + 24
    alt_off = lon_off + 24

    def _entry(tag: int, typ: int, cnt: int, val_or_off: bytes) -> bytes:
        return struct.pack(">HHI", tag, typ, cnt) + val_or_off

    tiff = bytearray()
    tiff.extend(b"MM")
    tiff.extend(struct.pack(">HI", 42, ifd0_off))

    # IFD0
    tiff.extend(struct.pack(">H", ifd0_count))
    tiff.extend(
        _entry(
            0x010F,
            2,
            len(make_b),
            make_b.ljust(4, b"\x00")[:4] if len(make_b) <= 4 else struct.pack(">I", make_off),
        )
    )
    tiff.extend(
        _entry(
            0x0110,
            2,
            len(model_b),
            model_b.ljust(4, b"\x00")[:4] if len(model_b) <= 4 else struct.pack(">I", model_off),
        )
    )
    tiff.extend(_entry(0x8825, 4, 1, struct.pack(">I", gps_ifd_off)))
    tiff.extend(struct.pack(">I", 0))  # next IFD = 0

    tiff.extend(make_b)
    tiff.extend(model_b)

    # GPS IFD
    tiff.extend(struct.pack(">H", gps_count))
    tiff.extend(_entry(0x0000, 1, 4, bytes([2, 3, 0, 0])))
    tiff.extend(_entry(0x0001, 2, 2, lat_ref + b"\x00\x00"))
    tiff.extend(_entry(0x0002, 5, 3, struct.pack(">I", lat_off)))
    tiff.extend(_entry(0x0003, 2, 2, lon_ref + b"\x00\x00"))
    tiff.extend(_entry(0x0004, 5, 3, struct.pack(">I", lon_off)))
    tiff.extend(_entry(0x0005, 1, 1, bytes([alt_ref, 0, 0, 0])))
    tiff.extend(_entry(0x0006, 5, 1, struct.pack(">I", alt_off)))
    tiff.extend(struct.pack(">I", 0))

    for num, den in lat_dms:
        tiff.extend(struct.pack(">II", num, den))
    for num, den in lon_dms:
        tiff.extend(struct.pack(">II", num, den))
    tiff.extend(struct.pack(">II", alt_rat[0], alt_rat[1]))

    return b"Exif\x00\x00" + bytes(tiff)


def interpolate_telemetry_at_ms(samples: List[Dict[str, Any]], t_ms: float) -> Optional[Tuple[float, float, float]]:
    if not samples:
        return None
    if len(samples) == 1:
        s0 = samples[0]
        return (float(s0["lat"]), float(s0["lon"]), float(s0.get("alt_m") or 0.0))
    if t_ms <= float(samples[0]["t_ms"]):
        s0 = samples[0]
        return (float(s0["lat"]), float(s0["lon"]), float(s0.get("alt_m") or 0.0))
    if t_ms >= float(samples[-1]["t_ms"]):
        sn = samples[-1]
        return (float(sn["lat"]), float(sn["lon"]), float(sn.get("alt_m") or 0.0))

    lo, hi = 0, len(samples) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if float(samples[mid]["t_ms"]) <= t_ms:
            lo = mid
        else:
            hi = mid
    a, b = samples[lo], samples[hi]
    ta, tb = float(a["t_ms"]), float(b["t_ms"])
    alpha = (t_ms - ta) / max(1.0, tb - ta)
    alpha = max(0.0, min(1.0, alpha))
    lat = float(a["lat"]) + alpha * (float(b["lat"]) - float(a["lat"]))
    lon = float(a["lon"]) + alpha * (float(b["lon"]) - float(a["lon"]))
    alt = float(a.get("alt_m") or 0.0) + alpha * (float(b.get("alt_m") or 0.0) - float(a.get("alt_m") or 0.0))
    return (lat, lon, alt)


def _laplacian_var(gray: np.ndarray) -> float:
    lap = -4.0 * gray + np.roll(gray, 1, axis=0) + np.roll(gray, -1, axis=0) + np.roll(gray, 1, axis=1) + np.roll(gray, -1, axis=1)
    inner = lap[1:-1, 1:-1]
    return float(np.var(inner)) if inner.size > 0 else 0.0


def extract_photogrammetry_frames_from_video(
    store: Any,
    blob_store: Any,
    project_id: int,
    user_id: int,
    video_path: Path,
    video_filename: str,
    telemetry_path: Optional[Path] = None,
    telemetry_filename: str = "",
    max_frames: int = 120,
) -> Dict[str, Any]:
    """Extracts sharp overlapping photogrammetric keyframes from a video, injects EXIF GPS tags
    from companion `.srt`/`.gpx`/`.csv` or embedded telemetry when available, saves them into
    the project's `imagenes/{folder}` container, and registers them in `dbo.PROJECT_FILES`."""
    from .photo_metadata import source_photo_metadata

    proj = store.get_project(project_id)
    if not proj:
        raise ValueError("Task not found.")

    probe = probe_video_metadata_and_embedded_telemetry(video_path)
    duration_s = float(probe.get("duration_s") or 0.0)
    if duration_s <= 0.2 or duration_s > 1800.0:
        raise ValueError("Video must be valid and up to 30 minutes long for photogrammetry.")

    samples: List[Dict[str, Any]] = []
    telemetry_source = "none"
    if telemetry_path and telemetry_path.is_file():
        t_fmt, parsed_s = parse_telemetry_file(telemetry_path, telemetry_filename)
        if parsed_s:
            samples = sorted(parsed_s, key=lambda r: float(r.get("t_ms") or 0))
            telemetry_source = t_fmt
    if not samples and probe.get("samples"):
        samples = sorted(list(probe["samples"]), key=lambda r: float(r.get("t_ms") or 0))
        telemetry_source = str(probe.get("source_format") or "embedded_gps")
    if not samples and _is_valid_lat_lon(probe.get("tag_lat"), probe.get("tag_lon")):
        samples = [
            {
                "t_ms": 0,
                "lat": float(probe["tag_lat"]),
                "lon": float(probe["tag_lon"]),
                "alt_m": float(probe.get("tag_alt") or 0.0),
            }
        ]
        telemetry_source = "video_iso6709_tag"

    # Choose candidate extraction FPS so we oversample 2x for Laplacian sharpness selection, then keep sharp frames
    max_frames = max(8, min(180, int(max_frames)))
    desired_views = min(max_frames, max(12, int(round(duration_s * 1.25))))
    sample_fps = min(4.0, max(0.5, (desired_views * 1.8) / max(1.0, duration_s)))

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg is required on the server to extract photogrammetry keyframes.")

    stem_clean = re.sub(r"[^A-Za-z0-9_-]", "_", Path(video_filename).stem)[:36] or "vid"
    folder = str(proj.get("FOLDER") or f"{project_id}-project")

    extracted_names: List[str] = []
    gps_lats: List[float] = []
    gps_lons: List[float] = []

    with tempfile.TemporaryDirectory(prefix="me_vid_photogram_") as tmpd:
        tmp_dir = Path(tmpd)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vf",
            f"fps={sample_fps:.4f},scale='min(3840,iw)':-2",
            "-qscale:v",
            "2",
            str(tmp_dir / "cand_%05d.jpg"),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        cand_files = sorted(tmp_dir.glob("cand_*.jpg"))
        if proc.returncode != 0 or len(cand_files) < 4:
            raise ValueError("Video is too short or unreadable (at least 4 clear overlapping frames are required).")

        # Evaluate sharpness on downsampled grayscale + filter duplicate stationary frames
        scored: List[Tuple[int, float, np.ndarray, Path]] = []
        for idx, cf in enumerate(cand_files):
            with Image.open(cf) as im:
                small = im.convert("L").resize((320, 180), Image.Resampling.BILINEAR)
                arr_g = np.asarray(small, dtype=np.float32)
                scored.append((idx, _laplacian_var(arr_g), arr_g, cf))

        # Group into windows of 2 when oversampled and pick the sharpest frame per window, skipping near-zero motion
        filtered: List[Tuple[int, Path]] = []
        last_gray: Optional[np.ndarray] = None
        step = 2 if len(scored) >= desired_views * 1.6 else 1
        for i in range(0, len(scored), step):
            window = scored[i : i + step]
            best_idx, _best_s, best_g, best_path = max(window, key=lambda item: item[1])
            if last_gray is not None and len(scored) > 8:
                mad = float(np.mean(np.abs(best_g - last_gray)))
                if mad < 1.6:
                    continue
            filtered.append((best_idx, best_path))
            last_gray = best_g

        if len(filtered) < 4:
            filtered = [(idx, p) for idx, _, _, p in scored]

        if len(filtered) > max_frames:
            filtered = [filtered[int(i * len(filtered) / max_frames)] for i in range(max_frames)]

        for out_idx, (cand_idx, cand_path) in enumerate(filtered, start=1):
            t_ms = ((cand_idx + 0.5) / sample_fps) * 1000.0
            gps_tuple = interpolate_telemetry_at_ms(samples, t_ms)
            frame_name = f"{stem_clean}_frame_{out_idx:04d}.jpg"

            if gps_tuple and _is_valid_lat_lon(gps_tuple[0], gps_tuple[1]):
                lat_v, lon_v, alt_v = gps_tuple
                exif_bytes = build_minimal_exif_gps_bytes(lat_v, lon_v, alt_v)
                with Image.open(cand_path) as im_full:
                    buf = io.BytesIO()
                    im_full.convert("RGB").save(buf, format="JPEG", quality=94, exif=exif_bytes)
                    final_bytes = buf.getvalue()
                gps_lats.append(lat_v)
                gps_lons.append(lon_v)
            else:
                final_bytes = cand_path.read_bytes()

            blob_rel = f"{folder}/{frame_name}"
            blob_store.save_bytes("imagenes", blob_rel, final_bytes)
            photo_meta = source_photo_metadata(final_bytes[:131072])
            store.add_project_file(
                project_id=project_id,
                user_id=user_id,
                filename=frame_name,
                size_bytes=len(final_bytes),
                content_type="image/jpeg",
                **photo_meta,
            )
            extracted_names.append(frame_name)

    avg_lat: Optional[float] = None
    avg_lon: Optional[float] = None
    if gps_lats and gps_lons:
        avg_lat = round(sum(gps_lats) / len(gps_lats), 6)
        avg_lon = round(sum(gps_lons) / len(gps_lons), 6)
        with store.lock:
            for p in store.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id):
                    p["LATITUD"] = avg_lat
                    p["LONGITUD"] = avg_lon
                    store.persist_table("dbo.WEB_PROJECTS")
                    break

    all_files = store.get_project_files(project_id)
    total_images = len(all_files)
    total_mb = sum(int(f.get("SIZE") or f.get("FILE_SIZE") or f.get("SIZE_BYTES") or 0) for f in all_files) / (1024.0 * 1024.0)
    quote = store.calculate_task_processing_quote(
        user_id,
        num_images=total_images,
        task_kind="photogrammetry",
        total_megabytes=total_mb,
    )
    return {
        "ok": True,
        "video_filename": video_filename,
        "duration_s": round(duration_s, 2),
        "extracted_frames": len(extracted_names),
        "gps_frames": len(gps_lats),
        "total_project_images": total_images,
        "has_gps": bool(gps_lats),
        "telemetry_source": telemetry_source,
        "lat": avg_lat,
        "lon": avg_lon,
        "quote": quote,
    }


def inspect_uploaded_georef_preview(filename: str, raw_bytes: bytes) -> Dict[str, Any]:
    """Quickly inspects an uploaded file header/sample to detect real WGS84 coordinates
    for instant placement on the upload map picker."""
    ext = Path(filename or "").suffix.lower()
    if not raw_bytes:
        return {"georeferenced": False}

    # 1. JPEG / WebP / PNG EXIF GPS
    if ext in (".jpg", ".jpeg", ".png", ".webp"):
        from .photo_metadata import source_photo_metadata

        meta = source_photo_metadata(raw_bytes[:262144])
        lat = meta.get("lat")
        lon = meta.get("lon")
        if _is_valid_lat_lon(lat, lon):
            return {"georeferenced": True, "lat": round(float(lat), 6), "lon": round(float(lon), 6), "source": "EXIF GPS"}
        return {"georeferenced": False}

    # 2. Telemetry (.srt, .gpx, .csv)
    if ext in TELEMETRY_EXTENSIONS:
        with tempfile.NamedTemporaryFile(suffix=ext) as tf:
            tf.write(raw_bytes[: 4 * 1024 * 1024])
            tf.flush()
            fmt, samples = parse_telemetry_file(Path(tf.name), filename)
            if samples:
                lats = [float(s["lat"]) for s in samples]
                lons = [float(s["lon"]) for s in samples]
                return {
                    "georeferenced": True,
                    "lat": round(sum(lats) / len(lats), 6),
                    "lon": round(sum(lons) / len(lons), 6),
                    "source": fmt.upper(),
                }

    # 3. Video (.mp4, .mov, .webm, .m4v)
    if ext in VIDEO_EXTENSIONS:
        with tempfile.NamedTemporaryFile(suffix=ext) as tf:
            tf.write(raw_bytes)
            tf.flush()
            info = probe_video_metadata_and_embedded_telemetry(Path(tf.name))
            if info.get("samples"):
                s = info["samples"]
                return {
                    "georeferenced": True,
                    "lat": round(sum(float(x["lat"]) for x in s) / len(s), 6),
                    "lon": round(sum(float(x["lon"]) for x in s) / len(s), 6),
                    "source": str(info.get("source_format") or "Embedded GPS"),
                }
            if _is_valid_lat_lon(info.get("tag_lat"), info.get("tag_lon")):
                return {
                    "georeferenced": True,
                    "lat": round(float(info["tag_lat"]), 6),
                    "lon": round(float(info["tag_lon"]), 6),
                    "source": "ISO-6709 Video Tag",
                }
        return {"georeferenced": False}

    # 4. GeoJSON / JSON
    if ext in (".geojson", ".json"):
        try:
            doc = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
            coords: List[Tuple[float, float]] = []

            def _walk_coords(c: Any) -> None:
                if isinstance(c, list):
                    if len(c) >= 2 and isinstance(c[0], (int, float)) and isinstance(c[1], (int, float)):
                        lon_c, lat_c = float(c[0]), float(c[1])
                        if _is_valid_lat_lon(lat_c, lon_c):
                            coords.append((lat_c, lon_c))
                    else:
                        for sub in c[:200]:
                            _walk_coords(sub)

            if isinstance(doc, dict):
                for feat in (doc.get("features") or [doc])[:200]:
                    geom = feat.get("geometry") if isinstance(feat, dict) and "geometry" in feat else feat
                    if isinstance(geom, dict):
                        _walk_coords(geom.get("coordinates"))
            if coords:
                return {
                    "georeferenced": True,
                    "lat": round(sum(p[0] for p in coords) / len(coords), 6),
                    "lon": round(sum(p[1] for p in coords) / len(coords), 6),
                    "source": "GeoJSON",
                }
        except Exception:
            pass

    # 5. KML / KMZ
    if ext in (".kml", ".kmz"):
        try:
            kml_txt = ""
            if ext == ".kmz" and zipfile.is_zipfile(io.BytesIO(raw_bytes)):
                with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                    k_name = next((n for n in zf.namelist() if n.lower().endswith(".kml")), None)
                    if k_name:
                        kml_txt = zf.read(k_name).decode("utf-8", errors="ignore")
            else:
                kml_txt = raw_bytes.decode("utf-8", errors="ignore")
            m = re.search(r"<coordinates>\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)", kml_txt, re.IGNORECASE)
            if m:
                lon_k, lat_k = float(m.group(1)), float(m.group(2))
                if _is_valid_lat_lon(lat_k, lon_k):
                    return {"georeferenced": True, "lat": round(lat_k, 6), "lon": round(lon_k, 6), "source": "KML/KMZ"}
        except Exception:
            pass

    # 6. GeoTIFF / LAS / LAZ / GPKG via rasterio / laspy / gis_splat_engine inspector
    if ext in (".tif", ".tiff"):
        try:
            import rasterio
            from rasterio.warp import transform_bounds

            with rasterio.open(io.BytesIO(raw_bytes)) as ds:
                if ds.crs and ds.bounds:
                    w, s, e, n = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=5)
                    lat_t, lon_t = (s + n) * 0.5, (w + e) * 0.5
                    if _is_valid_lat_lon(lat_t, lon_t):
                        return {
                            "georeferenced": True,
                            "lat": round(lat_t, 6),
                            "lon": round(lon_t, 6),
                            "bbox": [round(w, 6), round(s, 6), round(e, 6), round(n, 6)],
                            "source": f"GeoTIFF ({ds.crs})",
                        }
        except Exception:
            pass

    if ext in (".las", ".laz"):
        try:
            import laspy
            from pyproj import Transformer

            with laspy.open(io.BytesIO(raw_bytes)) as lf:
                hdr = lf.header
                crs = hdr.parse_crs()
                cx = (float(hdr.mins[0]) + float(hdr.maxs[0])) * 0.5
                cy = (float(hdr.mins[1]) + float(hdr.maxs[1])) * 0.5
                if crs:
                    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                    lon_l, lat_l = tr.transform(cx, cy)
                    if _is_valid_lat_lon(lat_l, lon_l):
                        return {
                            "georeferenced": True,
                            "lat": round(float(lat_l), 6),
                            "lon": round(float(lon_l), 6),
                            "source": f"LAS/LAZ ({crs.to_authority() or 'CRS'})",
                        }
                elif _is_valid_lat_lon(cy, cx):
                    return {"georeferenced": True, "lat": round(cy, 6), "lon": round(cx, 6), "source": "LAS/LAZ WGS84"}
        except Exception:
            pass

    if ext == ".zip" and zipfile.is_zipfile(io.BytesIO(raw_bytes)):
        try:
            from .photo_metadata import source_photo_metadata

            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                for zn in zf.namelist()[:60]:
                    zlow = zn.lower()
                    if zlow.endswith((".jpg", ".jpeg")):
                        meta = source_photo_metadata(zf.read(zn)[:131072])
                        if _is_valid_lat_lon(meta.get("lat"), meta.get("lon")):
                            return {
                                "georeferenced": True,
                                "lat": round(float(meta["lat"]), 6),
                                "lon": round(float(meta["lon"]), 6),
                                "source": "ZIP EXIF GPS",
                            }
        except Exception:
            pass

    return {"georeferenced": False}
