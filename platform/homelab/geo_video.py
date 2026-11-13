"""Universal Georeferenced Video & Telemetry Engine (DJI SRT, GoPro GPMF, GPX, CSV, YouTube/Vimeo 0 MB & Hosted MP4)."""
from __future__ import annotations

import csv
import datetime
import io
import ipaddress
import json
import math
import re
import socket
import struct
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw


VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
TELEMETRY_EXTENSIONS = {".srt", ".gpx", ".csv"}


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        f = float(val)
        return f if math.isfinite(f) else None
    s = str(val).strip()
    if not s:
        return None
    s = s.replace(",", ".") if s.count(",") == 1 and "." not in s else s
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        f = float(m.group(0))
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def _wrap360(deg: float) -> float:
    x = float(deg) % 360.0
    return x + 360.0 if x < 0.0 else x


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp * 0.5) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl * 0.5) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return _wrap360(math.degrees(math.atan2(y, x)))


def _is_valid_lat_lon(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False
    if abs(lat) < 1e-5 and abs(lon) < 1e-5:
        return False
    return True


def parse_external_video_url(url: str) -> Dict[str, Any]:
    """Validate and classify a 0-MB external video URL (YouTube, Vimeo, or direct HTTPS MP4/WebM)."""
    raw = str(url or "").strip()
    if not raw or len(raw) > 2048:
        raise ValueError("Invalid or empty external video URL.")
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("https", "http"):
        raise ValueError("External video URL must use https:// or http://.")
    host = (parsed.hostname or "").strip().lower()
    if not host or host in ("localhost", "0.0.0.0") or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("Private or local video hosts are not allowed.")
    try:
        ip_obj = ipaddress.ip_address(host)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved or ip_obj.is_multicast:
            raise ValueError("Private IP video URLs are not allowed.")
    except ValueError as exc:
        if "Private IP" in str(exc):
            raise

    # 1. YouTube detection
    yt_id: Optional[str] = None
    if host in ("youtu.be", "www.youtu.be"):
        cand = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", cand):
            yt_id = cand
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            cand = qs["v"][0].strip()
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", cand):
                yt_id = cand
        if not yt_id:
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
                cand = parts[1]
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", cand):
                    yt_id = cand
    if yt_id:
        return {
            "provider": "youtube",
            "youtube_id": yt_id,
            "vimeo_id": "",
            "external_url": f"https://www.youtube.com/watch?v={yt_id}",
            "embed_url": f"https://www.youtube-nocookie.com/embed/{yt_id}?enablejsapi=1&rel=0&modestbranding=1&playsinline=1",
        }

    # 2. Vimeo detection
    if host in ("vimeo.com", "www.vimeo.com", "player.vimeo.com"):
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        vm_id = next((p for p in parts if re.fullmatch(r"\d{6,12}", p)), None)
        if vm_id:
            return {
                "provider": "vimeo",
                "youtube_id": "",
                "vimeo_id": vm_id,
                "external_url": f"https://vimeo.com/{vm_id}",
                "embed_url": f"https://player.vimeo.com/video/{vm_id}?dnt=1",
            }

    # 3. Direct HTTPS/HTTP video stream (.mp4, .webm, .mov, .m4v, .m3u8)
    path_low = parsed.path.lower()
    if any(path_low.endswith(ext) for ext in (".mp4", ".webm", ".mov", ".m4v", ".m3u8")):
        return {
            "provider": "direct_url",
            "youtube_id": "",
            "vimeo_id": "",
            "external_url": raw,
            "embed_url": raw,
        }

    raise ValueError("Unsupported video URL. Use a YouTube link, Vimeo link, or direct .mp4/.webm URL.")


def _parse_srt_timestamp_ms(ts: str) -> Optional[int]:
    m = re.match(r"^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*$", ts)
    if not m:
        return None
    hh, mm, ss, ms_s = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    ms = int(ms_s.ljust(3, "0")[:3])
    return ((hh * 3600 + mm * 60 + ss) * 1000) + ms


def parse_dji_srt(text: str) -> List[Dict[str, Any]]:
    """Parse all DJI SRT dialects (Mini 2/3/4 Pro, Air, Mavic, Phantom, Avata/FPV)."""
    clean = re.sub(r"<[^>]+>", " ", text or "")
    blocks = re.split(r"\r?\n\s*\r?\n", clean.strip())
    samples: List[Dict[str, Any]] = []

    re_time = re.compile(r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})")
    re_lat = re.compile(r"\[?\blatitude\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
    re_lon = re.compile(r"\[?\blongitude\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
    re_rel_alt = re.compile(r"\brel_alt\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
    re_abs_alt = re.compile(r"\babs_alt\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
    re_gb_yaw = re.compile(r"\b(?:gb_yaw|gimbal_yaw|yaw)\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
    re_gb_pitch = re.compile(r"\b(?:gb_pitch|gimbal_pitch|pitch)\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
    re_gb_roll = re.compile(r"\b(?:gb_roll|gimbal_roll|roll)\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
    re_focal = re.compile(r"\bfocal_len\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
    re_gps_legacy = re.compile(
        r"\bGPS\s*\(\s*([-+]?\d+(?:\.\d+)?)([NSEW]?)\s*,\s*([-+]?\d+(?:\.\d+)?)([NSEW]?)(?:\s*,\s*([-+]?\d+(?:\.\d+)?)m?)?\s*\)",
        re.IGNORECASE,
    )
    re_baro = re.compile(r"\b(?:BAROMETER|H|ALT)\s*:\s*([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)

    for blk in blocks:
        tm = re_time.search(blk)
        if not tm:
            continue
        t_ms = _parse_srt_timestamp_ms(tm.group(1))
        if t_ms is None:
            continue

        lat: Optional[float] = None
        lon: Optional[float] = None
        abs_alt: Optional[float] = None
        rel_alt: Optional[float] = None

        mlat = re_lat.search(blk)
        mlon = re_lon.search(blk)
        if mlat and mlon:
            lat = _safe_float(mlat.group(1))
            lon = _safe_float(mlon.group(1))
        else:
            mg = re_gps_legacy.search(blk)
            if mg:
                v1 = _safe_float(mg.group(1))
                h1 = (mg.group(2) or "").upper()
                v2 = _safe_float(mg.group(3))
                h2 = (mg.group(4) or "").upper()
                v3 = _safe_float(mg.group(5))
                if v1 is not None and v2 is not None:
                    if h1 in ("N", "S") or h2 in ("E", "W"):
                        lat = -abs(v1) if h1 == "S" else v1
                        lon = -abs(v2) if h2 == "W" else v2
                    elif h1 in ("E", "W") or h2 in ("N", "S"):
                        lon = -abs(v1) if h1 == "W" else v1
                        lat = -abs(v2) if h2 == "S" else v2
                    elif abs(v1) > 90.0 and abs(v2) <= 90.0:
                        lon, lat = v1, v2
                    elif abs(v2) > 90.0 and abs(v1) <= 90.0:
                        lat, lon = v1, v2
                    else:
                        # Classic DJI Mavic Pro / Phantom 4 writes GPS (lon, lat, alt_or_sats)
                        lon, lat = v1, v2
                if v3 is not None and v3 > 25.0:
                    rel_alt = v3

        m_rel = re_rel_alt.search(blk)
        m_abs = re_abs_alt.search(blk)
        m_baro = re_baro.search(blk)
        if m_rel:
            rel_alt = _safe_float(m_rel.group(1))
        elif m_baro and rel_alt is None:
            rel_alt = _safe_float(m_baro.group(1))
        if m_abs:
            abs_alt = _safe_float(m_abs.group(1))

        if not _is_valid_lat_lon(lat, lon):
            continue

        yaw = _safe_float(re_gb_yaw.search(blk).group(1)) if re_gb_yaw.search(blk) else None
        pitch = _safe_float(re_gb_pitch.search(blk).group(1)) if re_gb_pitch.search(blk) else None
        roll = _safe_float(re_gb_roll.search(blk).group(1)) if re_gb_roll.search(blk) else None
        focal = _safe_float(re_focal.search(blk).group(1)) if re_focal.search(blk) else None
        fov = 73.7
        if focal and 8.0 <= focal <= 300.0:
            fov = round(math.degrees(2.0 * math.atan(18.0 / focal)), 2)

        samples.append({
            "t_ms": t_ms,
            "lat": float(lat),
            "lon": float(lon),
            "alt_m": float(abs_alt) if abs_alt is not None else (float(rel_alt) if rel_alt is not None else 0.0),
            "rel_alt_m": float(rel_alt) if rel_alt is not None else None,
            "has_msl": abs_alt is not None,
            "heading": _wrap360(yaw) if yaw is not None else None,
            "pitch": float(pitch) if pitch is not None else -22.0,
            "roll": float(roll) if roll is not None else 0.0,
            "fov": fov,
            "speed_mps": None,
        })
    return samples


def _decode_gpmf_numbers(type_ch: str, struct_size: int, repeat: int, payload: bytes) -> Optional[List[Tuple[float, ...]]]:
    fmt_map = {
        "l": ("i", 4),
        "L": ("I", 4),
        "s": ("h", 2),
        "S": ("H", 2),
        "f": ("f", 4),
        "d": ("d", 8),
        "b": ("b", 1),
        "B": ("B", 1),
    }
    if type_ch not in fmt_map or struct_size <= 0 or repeat <= 0:
        return None
    code, elem_sz = fmt_map[type_ch]
    if struct_size % elem_sz != 0:
        return None
    elems_per_row = struct_size // elem_sz
    total_bytes = struct_size * repeat
    if len(payload) < total_bytes:
        return None
    out: List[Tuple[float, ...]] = []
    row_fmt = ">" + (code * elems_per_row)
    for i in range(repeat):
        off = i * struct_size
        vals = struct.unpack(row_fmt, payload[off : off + struct_size])
        out.append(tuple(float(v) for v in vals))
    return out


def _walk_gpmf_Buffer(buf: bytes, start: int, end: int, gps_packets: List[List[Dict[str, Any]]]) -> None:
    pos = start
    current_scal: List[float] = [1.0]
    while pos + 8 <= end:
        fourcc = buf[pos : pos + 4]
        if not all(32 <= b <= 126 for b in fourcc):
            break
        key = fourcc.decode("ascii", errors="ignore")
        type_b = buf[pos + 4]
        struct_sz = buf[pos + 5]
        repeat = struct.unpack(">H", buf[pos + 6 : pos + 8])[0]
        data_len = struct_sz * repeat
        padded_len = (data_len + 3) & ~3
        payload_start = pos + 8
        payload_end = payload_start + data_len
        next_pos = payload_start + padded_len
        if next_pos > end:
            break

        if type_b == 0:
            # Nested container (e.g., DEVC, STRM)
            if key == "DEVC":
                devc_rows: List[Dict[str, Any]] = []
                _walk_gpmf_strm_or_devc(buf, payload_start, payload_end, devc_rows)
                if devc_rows:
                    gps_packets.append(devc_rows)
            else:
                _walk_gpmf_Buffer(buf, payload_start, payload_end, gps_packets)
        else:
            t_ch = chr(type_b)
            if key == "SCAL":
                rows = _decode_gpmf_numbers(t_ch, struct_sz, repeat, buf[payload_start:payload_end])
                if rows:
                    flat = [v for r in rows for v in r]
                    if flat:
                        current_scal = [v if abs(v) > 1e-12 else 1.0 for v in flat]
            elif key in ("GPS5", "GPS9"):
                rows = _decode_gpmf_numbers(t_ch, struct_sz, repeat, buf[payload_start:payload_end])
                if rows:
                    pkt: List[Dict[str, Any]] = []
                    for r in rows:
                        if len(r) >= 5:
                            sc = lambda idx: current_scal[idx] if idx < len(current_scal) else current_scal[0]
                            lat = r[0] / sc(0)
                            lon = r[1] / sc(1)
                            alt = r[2] / sc(2)
                            s2d = r[3] / sc(3)
                            if _is_valid_lat_lon(lat, lon):
                                pkt.append({
                                    "lat": lat,
                                    "lon": lon,
                                    "alt_m": alt,
                                    "speed_mps": max(0.0, s2d),
                                })
                    if pkt:
                        gps_packets.append(pkt)
        pos = next_pos


def _walk_gpmf_strm_or_devc(buf: bytes, start: int, end: int, out_rows: List[Dict[str, Any]]) -> None:
    pos = start
    current_scal: List[float] = [1.0]
    while pos + 8 <= end:
        fourcc = buf[pos : pos + 4]
        if not all(32 <= b <= 126 for b in fourcc):
            break
        key = fourcc.decode("ascii", errors="ignore")
        type_b = buf[pos + 4]
        struct_sz = buf[pos + 5]
        repeat = struct.unpack(">H", buf[pos + 6 : pos + 8])[0]
        data_len = struct_sz * repeat
        padded_len = (data_len + 3) & ~3
        payload_start = pos + 8
        payload_end = payload_start + data_len
        next_pos = payload_start + padded_len
        if next_pos > end:
            break
        if type_b == 0:
            _walk_gpmf_strm_or_devc(buf, payload_start, payload_end, out_rows)
        else:
            t_ch = chr(type_b)
            if key == "SCAL":
                rows = _decode_gpmf_numbers(t_ch, struct_sz, repeat, buf[payload_start:payload_end])
                if rows:
                    flat = [v for r in rows for v in r]
                    if flat:
                        current_scal = [v if abs(v) > 1e-12 else 1.0 for v in flat]
            elif key == "GPS5":
                rows = _decode_gpmf_numbers(t_ch, struct_sz, repeat, buf[payload_start:payload_end])
                if rows:
                    for r in rows:
                        if len(r) >= 5:
                            sc = lambda idx: current_scal[idx] if idx < len(current_scal) else current_scal[0]
                            lat = r[0] / sc(0)
                            lon = r[1] / sc(1)
                            alt = r[2] / sc(2)
                            s2d = r[3] / sc(3)
                            s3d = r[4] / sc(4)
                            if _is_valid_lat_lon(lat, lon):
                                out_rows.append({
                                    "lat": lat,
                                    "lon": lon,
                                    "alt_m": alt,
                                    "speed_mps": max(0.0, s2d, s3d),
                                })
        pos = next_pos


def parse_gpmf_bytes(data: bytes, total_duration_s: Optional[float] = None) -> List[Dict[str, Any]]:
    """Parse raw GoPro GPMF (`DEVC` / `STRM` / `SCAL` / `GPS5`) binary stream or MP4 buffer."""
    if not data or b"DEVC" not in data:
        return []
    packets: List[List[Dict[str, Any]]] = []
    idx = 0
    n = len(data)
    while True:
        found = data.find(b"DEVC\x00", idx)
        if found < 0 or found + 8 > n:
            break
        struct_sz = data[found + 5]
        repeat = struct.unpack(">H", data[found + 6 : found + 8])[0]
        blk_len = 8 + ((struct_sz * repeat + 3) & ~3)
        if struct_sz > 0 and repeat > 0 and found + blk_len <= n:
            devc_rows: List[Dict[str, Any]] = []
            _walk_gpmf_strm_or_devc(data, found + 8, found + blk_len, devc_rows)
            if devc_rows:
                packets.append(devc_rows)
            idx = found + blk_len
        else:
            idx = found + 4

    if not packets:
        return []

    num_pkts = len(packets)
    pkt_dur_ms = (float(total_duration_s) * 1000.0 / num_pkts) if (total_duration_s and total_duration_s > 0) else 1000.0
    samples: List[Dict[str, Any]] = []
    for p_idx, rows in enumerate(packets):
        base_ms = p_idx * pkt_dur_ms
        step_ms = pkt_dur_ms / max(1, len(rows))
        for r_idx, row in enumerate(rows):
            samples.append({
                "t_ms": int(round(base_ms + r_idx * step_ms)),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "alt_m": float(row["alt_m"]),
                "rel_alt_m": None,
                "has_msl": True,
                "heading": None,
                "pitch": -12.0,
                "roll": 0.0,
                "fov": 92.0,
                "speed_mps": float(row["speed_mps"]),
            })
    return samples


def _parse_iso_time(val: str) -> Optional[datetime.datetime]:
    s = (val or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.datetime.strptime(s[:19], fmt).replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                continue
    return None


def parse_gpx_telemetry(source: Union[str, bytes, Path]) -> List[Dict[str, Any]]:
    """Parse GPX 1.0/1.1 tracks or routes (<trkpt> / <rtept>) into time-indexed telemetry."""
    if isinstance(source, Path):
        raw = source.read_bytes()
    elif isinstance(source, str):
        raw = source.encode("utf-8", errors="ignore")
    else:
        raw = bytes(source)
    root = ET.fromstring(raw)

    pts: List[ET.Element] = []
    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1].lower()
        if tag_local in ("trkpt", "rtept"):
            pts.append(elem)
    if not pts:
        for elem in root.iter():
            if elem.tag.split("}")[-1].lower() == "wpt":
                pts.append(elem)

    parsed_rows: List[Tuple[Optional[datetime.datetime], float, float, float, Optional[float], Optional[float]]] = []
    for pt in pts:
        lat = _safe_float(pt.attrib.get("lat"))
        lon = _safe_float(pt.attrib.get("lon"))
        if not _is_valid_lat_lon(lat, lon):
            continue
        ele = 0.0
        dt_val: Optional[datetime.datetime] = None
        spd: Optional[float] = None
        crs: Optional[float] = None
        for child in pt.iter():
            ctag = child.tag.split("}")[-1].lower()
            if ctag == "ele" and child.text:
                ev = _safe_float(child.text)
                if ev is not None:
                    ele = ev
            elif ctag == "time" and child.text:
                dt_val = _parse_iso_time(child.text)
            elif ctag == "speed" and child.text:
                spd = _safe_float(child.text)
            elif ctag in ("course", "heading") and child.text:
                crs = _safe_float(child.text)
        parsed_rows.append((dt_val, float(lat), float(lon), ele, spd, crs))

    if not parsed_rows:
        return []

    t0 = next((r[0] for r in parsed_rows if r[0] is not None), None)
    samples: List[Dict[str, Any]] = []
    for idx, (dt_val, lat, lon, ele, spd, crs) in enumerate(parsed_rows):
        if t0 is not None and dt_val is not None:
            t_ms = max(0, int(round((dt_val - t0).total_seconds() * 1000.0)))
        else:
            t_ms = idx * 1000
        samples.append({
            "t_ms": t_ms,
            "lat": lat,
            "lon": lon,
            "alt_m": ele,
            "rel_alt_m": None,
            "has_msl": abs(ele) > 0.01,
            "heading": _wrap360(crs) if crs is not None else None,
            "pitch": -18.0,
            "roll": 0.0,
            "fov": 78.0,
            "speed_mps": spd,
        })
    return samples


def parse_csv_telemetry(text: str) -> List[Dict[str, Any]]:
    """Parse drone/outdoor CSV logs (AirData UAV, Litchi, Garmin, custom CSV)."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) < 2:
        return []
    dialect_delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=dialect_delim)
    if not reader.fieldnames:
        return []

    norm_map = {re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_"): h for h in reader.fieldnames if h}

    def _find_col(*candidates: str) -> Optional[str]:
        for c in candidates:
            if c in norm_map:
                return norm_map[c]
        for k, orig in norm_map.items():
            if any(k.startswith(c) or c in k for c in candidates):
                return orig
        return None

    col_lat = _find_col("latitude", "lat", "gps_lat")
    col_lon = _find_col("longitude", "lon", "lng", "gps_lon")
    if not col_lat or not col_lon:
        return []
    col_tms = _find_col("time_millisecond", "t_ms", "timestamp_ms", "millisecond", "elapsed_ms")
    col_ts = _find_col("time_s", "seconds", "elapsed_time", "time_sec")
    col_dt = _find_col("datetime_utc", "datetime", "timestamp", "time", "date")
    col_alt = _find_col("altitude_above_sealevel", "alt_m", "altitude", "elevation", "ele", "abs_alt")
    col_rel = _find_col("height_above_takeoff", "rel_alt", "height_m", "agl")
    col_yaw = _find_col("compass_heading", "gimbal_yaw", "heading", "yaw", "course")
    col_pitch = _find_col("gimbal_pitch", "pitch")
    col_spd = _find_col("speed_mps", "speed", "velocity")

    alt_is_feet = bool(col_alt and ("feet" in col_alt.lower() or "(ft)" in col_alt.lower()))
    rel_is_feet = bool(col_rel and ("feet" in col_rel.lower() or "(ft)" in col_rel.lower()))
    spd_is_mph = bool(col_spd and "mph" in col_spd.lower())

    samples: List[Dict[str, Any]] = []
    t0_dt: Optional[datetime.datetime] = None
    t0_ms: Optional[float] = None
    for idx, row in enumerate(reader):
        lat = _safe_float(row.get(col_lat))
        lon = _safe_float(row.get(col_lon))
        if not _is_valid_lat_lon(lat, lon):
            continue

        t_ms: Optional[int] = None
        if col_tms and row.get(col_tms) is not None:
            v = _safe_float(row.get(col_tms))
            if v is not None:
                if t0_ms is None:
                    t0_ms = v if v > 60000 else 0.0
                t_ms = max(0, int(round(v - t0_ms)))
        if t_ms is None and col_ts and row.get(col_ts) is not None:
            v = _safe_float(row.get(col_ts))
            if v is not None:
                t_ms = max(0, int(round(v * 1000.0)))
        if t_ms is None and col_dt and row.get(col_dt):
            dt = _parse_iso_time(str(row.get(col_dt)))
            if dt is not None:
                if t0_dt is None:
                    t0_dt = dt
                t_ms = max(0, int(round((dt - t0_dt).total_seconds() * 1000.0)))
        if t_ms is None:
            t_ms = idx * 1000

        alt_v = _safe_float(row.get(col_alt)) if col_alt else None
        if alt_v is not None and alt_is_feet:
            alt_v *= 0.3048
        rel_v = _safe_float(row.get(col_rel)) if col_rel else None
        if rel_v is not None and rel_is_feet:
            rel_v *= 0.3048
        spd_v = _safe_float(row.get(col_spd)) if col_spd else None
        if spd_v is not None and spd_is_mph:
            spd_v *= 0.44704
        yaw_v = _safe_float(row.get(col_yaw)) if col_yaw else None
        pitch_v = _safe_float(row.get(col_pitch)) if col_pitch else None

        samples.append({
            "t_ms": t_ms,
            "lat": float(lat),
            "lon": float(lon),
            "alt_m": float(alt_v) if alt_v is not None else (float(rel_v) if rel_v is not None else 0.0),
            "rel_alt_m": float(rel_v) if rel_v is not None else None,
            "has_msl": alt_v is not None,
            "heading": _wrap360(yaw_v) if yaw_v is not None else None,
            "pitch": float(pitch_v) if pitch_v is not None else -22.0,
            "roll": 0.0,
            "fov": 73.7,
            "speed_mps": spd_v,
        })
    return samples


def parse_telemetry_file(path: Path, filename: str = "") -> Tuple[str, List[Dict[str, Any]]]:
    ext = Path(filename or path.name).suffix.lower()
    raw = path.read_bytes()
    if ext == ".srt":
        return "dji_srt", parse_dji_srt(raw.decode("utf-8", errors="ignore"))
    if ext == ".gpx":
        return "gpx", parse_gpx_telemetry(raw)
    if ext == ".csv":
        return "csv", parse_csv_telemetry(raw.decode("utf-8", errors="ignore"))
    txt = raw.decode("utf-8", errors="ignore")
    if "-->" in txt:
        return "dji_srt", parse_dji_srt(txt)
    if "<gpx" in txt.lower():
        return "gpx", parse_gpx_telemetry(raw)
    return "csv", parse_csv_telemetry(txt)


def probe_video_metadata_and_embedded_telemetry(video_path: Path) -> Dict[str, Any]:
    """Extract duration, dimensions, ISO-6709 GPS tag, embedded subtitle SRT, or GoPro GPMF from an MP4/MOV/WebM."""
    info: Dict[str, Any] = {
        "duration_s": 0.0,
        "width": 0,
        "height": 0,
        "codec": "",
        "tag_lat": None,
        "tag_lon": None,
        "tag_alt": None,
        "source_format": "none",
        "samples": [],
    }
    try:
        pr = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=index,codec_type,codec_name,codec_tag_string,width,height:format=duration:format_tags=location,com.apple.quicktime.location.ISO6709",
                "-of", "json", str(video_path),
            ],
            capture_output=True, text=True, timeout=25,
        )
        if pr.returncode == 0 and pr.stdout:
            pdata = json.loads(pr.stdout)
            dur = _safe_float((pdata.get("format") or {}).get("duration"))
            if dur and dur > 0:
                info["duration_s"] = round(dur, 3)
            ftags = (pdata.get("format") or {}).get("tags") or {}
            loc_iso = str(ftags.get("com.apple.quicktime.location.ISO6709") or ftags.get("location") or "").strip()
            if loc_iso:
                m_iso = re.match(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?", loc_iso)
                if m_iso:
                    tlat, tlon = _safe_float(m_iso.group(1)), _safe_float(m_iso.group(2))
                    if _is_valid_lat_lon(tlat, tlon):
                        info["tag_lat"] = tlat
                        info["tag_lon"] = tlon
                        info["tag_alt"] = _safe_float(m_iso.group(3))

            gpmd_stream_idx: Optional[int] = None
            sub_stream_idx: Optional[int] = None
            for st in pdata.get("streams") or []:
                ctype = str(st.get("codec_type") or "")
                cname = str(st.get("codec_name") or "").lower()
                ctag = str(st.get("codec_tag_string") or "").lower()
                if ctype == "video" and not info["width"]:
                    info["width"] = int(st.get("width") or 0)
                    info["height"] = int(st.get("height") or 0)
                    info["codec"] = cname
                elif ctag == "gpmd" or cname == "gpmd":
                    gpmd_stream_idx = int(st.get("index"))
                elif ctype == "subtitle" and sub_stream_idx is None:
                    sub_stream_idx = int(st.get("index"))

            if gpmd_stream_idx is not None:
                ex = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(video_path), "-map", f"0:{gpmd_stream_idx}", "-codec", "copy", "-f", "rawvideo", "-"],
                    capture_output=True, timeout=25,
                )
                if ex.returncode == 0 and ex.stdout:
                    g_samples = parse_gpmf_bytes(ex.stdout, total_duration_s=info["duration_s"] or None)
                    if g_samples:
                        info["source_format"] = "gopro_gpmf"
                        info["samples"] = g_samples
                        return info

            if sub_stream_idx is not None:
                ex_s = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(video_path), "-map", f"0:{sub_stream_idx}", "-f", "srt", "-"],
                    capture_output=True, text=True, timeout=25,
                )
                if ex_s.returncode == 0 and ex_s.stdout:
                    s_samples = parse_dji_srt(ex_s.stdout)
                    if s_samples:
                        info["source_format"] = "embedded_dji_srt"
                        info["samples"] = s_samples
                        return info
    except Exception:
        pass

    # Fallback direct binary scan for GoPro GPMF DEVC blocks inside <= 64 MiB files
    try:
        if video_path.stat().st_size <= 64 * 1024 * 1024:
            raw_v = video_path.read_bytes()
            if b"DEVC\x00" in raw_v:
                g_samples = parse_gpmf_bytes(raw_v, total_duration_s=info["duration_s"] or None)
                if g_samples:
                    info["source_format"] = "gopro_gpmf"
                    info["samples"] = g_samples
    except Exception:
        pass
    return info


def normalize_and_enrich_samples(
    raw_samples: List[Dict[str, Any]],
    fallback_lat: Optional[float] = None,
    fallback_lon: Optional[float] = None,
    fallback_alt: float = 0.0,
    duration_s: Optional[float] = None,
    source_format: str = "telemetry",
    video_info: Optional[Dict[str, Any]] = None,
    max_points: int = 1800,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Produce canonical `telemetry.json` and 3D `track.geojson` from raw telemetry samples."""
    valid = [s for s in raw_samples if _is_valid_lat_lon(s.get("lat"), s.get("lon"))]
    valid.sort(key=lambda x: int(x.get("t_ms") or 0))

    # Deduplicate identical timestamps
    dedup: List[Dict[str, Any]] = []
    for s in valid:
        t_ms = max(0, int(s.get("t_ms") or 0))
        if dedup and dedup[-1]["t_ms"] == t_ms:
            dedup[-1] = {**s, "t_ms": t_ms}
        else:
            dedup.append({**s, "t_ms": t_ms})

    # If video has no dynamic track, create a stationary 2-point pin at fallback/ISO6709 location
    if not dedup:
        lat0 = fallback_lat if _is_valid_lat_lon(fallback_lat, fallback_lon) else 41.8205
        lon0 = fallback_lon if _is_valid_lat_lon(fallback_lat, fallback_lon) else 1.8676
        dur_ms = int(round((duration_s if duration_s and duration_s > 0 else 30.0) * 1000.0))
        dedup = [
            {"t_ms": 0, "lat": float(lat0), "lon": float(lon0), "alt_m": float(fallback_alt), "rel_alt_m": None, "has_msl": abs(fallback_alt) > 0.5, "heading": 0.0, "pitch": -20.0, "roll": 0.0, "fov": 73.7, "speed_mps": 0.0},
            {"t_ms": dur_ms, "lat": float(lat0), "lon": float(lon0), "alt_m": float(fallback_alt), "rel_alt_m": None, "has_msl": abs(fallback_alt) > 0.5, "heading": 0.0, "pitch": -20.0, "roll": 0.0, "fov": 73.7, "speed_mps": 0.0},
        ]
        source_format = "static_pin"

    # Downsample gracefully if track is huge, always preserving first and last sample
    if len(dedup) > max_points:
        step = (len(dedup) - 1) / float(max_points - 1)
        picked = [dedup[int(round(i * step))] for i in range(max_points - 1)]
        picked.append(dedup[-1])
        dedup = picked

    # Compute distance, derived speed_mps, and geodesic heading where missing
    total_dist_m = 0.0
    last_heading = float(dedup[0].get("heading") if dedup[0].get("heading") is not None else 0.0)
    for i, cur in enumerate(dedup):
        if i > 0:
            prev = dedup[i - 1]
            seg_m = _haversine_m(prev["lat"], prev["lon"], cur["lat"], cur["lon"])
            total_dist_m += seg_m
            dt_s = max(0.001, (cur["t_ms"] - prev["t_ms"]) / 1000.0)
            if cur.get("speed_mps") is None:
                cur["speed_mps"] = round(min(120.0, seg_m / dt_s), 2)
            if cur.get("heading") is None:
                if seg_m >= 0.4:
                    last_heading = _bearing_deg(prev["lat"], prev["lon"], cur["lat"], cur["lon"])
                cur["heading"] = round(last_heading, 2)
            else:
                last_heading = float(cur["heading"])
        else:
            if cur.get("speed_mps") is None:
                cur["speed_mps"] = 0.0
            if cur.get("heading") is None:
                if len(dedup) > 1 and _haversine_m(cur["lat"], cur["lon"], dedup[1]["lat"], dedup[1]["lon"]) >= 0.4:
                    last_heading = _bearing_deg(cur["lat"], cur["lon"], dedup[1]["lat"], dedup[1]["lon"])
                cur["heading"] = round(last_heading, 2)

    canonical_samples: List[Dict[str, Any]] = []
    lats, lons, alts, spds = [], [], [], []
    has_any_msl = any(bool(s.get("has_msl")) for s in dedup)
    for s in dedup:
        lat_v = round(float(s["lat"]), 7)
        lon_v = round(float(s["lon"]), 7)
        alt_v = round(float(s.get("alt_m") or 0.0), 2)
        spd_v = round(float(s.get("speed_mps") or 0.0), 2)
        lats.append(lat_v)
        lons.append(lon_v)
        alts.append(alt_v)
        spds.append(spd_v)
        row_out: Dict[str, Any] = {
            "t_ms": int(s["t_ms"]),
            "lat": lat_v,
            "lon": lon_v,
            "alt_m": alt_v,
            "speed_mps": spd_v,
            "heading": round(_wrap360(float(s.get("heading") or 0.0)), 1),
            "pitch": round(float(s.get("pitch") if s.get("pitch") is not None else -20.0), 1),
            "roll": round(float(s.get("roll") or 0.0), 1),
            "fov": round(float(s.get("fov") or 73.7), 1),
        }
        if s.get("rel_alt_m") is not None:
            row_out["rel_alt_m"] = round(float(s["rel_alt_m"]), 2)
        canonical_samples.append(row_out)

    track_dur_s = canonical_samples[-1]["t_ms"] / 1000.0
    eff_dur_s = round(max(track_dur_s, float(duration_s or 0.0)), 3)
    bbox = [min(lons), min(lats), max(lons), max(lats)]
    v_meta = video_info or {}

    telemetry_doc: Dict[str, Any] = {
        "schema": "mosaicearth.geovideo.v1",
        "source_format": source_format,
        "video_provider": str(v_meta.get("provider") or "hosted"),
        "external_video_url": str(v_meta.get("external_url") or ""),
        "embed_url": str(v_meta.get("embed_url") or ""),
        "youtube_id": str(v_meta.get("youtube_id") or ""),
        "vimeo_id": str(v_meta.get("vimeo_id") or ""),
        "width": int(v_meta.get("width") or 0),
        "height": int(v_meta.get("height") or 0),
        "duration_s": eff_dur_s,
        "sample_count": len(canonical_samples),
        "distance_m": round(total_dist_m, 2),
        "min_alt_m": round(min(alts), 2),
        "max_alt_m": round(max(alts), 2),
        "max_speed_mps": round(max(spds), 2),
        "has_msl_altitude": has_any_msl,
        "start_lat": canonical_samples[0]["lat"],
        "start_lon": canonical_samples[0]["lon"],
        "start_alt_m": canonical_samples[0]["alt_m"],
        "center_lat": round((bbox[1] + bbox[3]) * 0.5, 7),
        "center_lon": round((bbox[0] + bbox[2]) * 0.5, 7),
        "bbox_wgs84": bbox,
        "samples": canonical_samples,
    }

    coords_3d = [[s["lon"], s["lat"], s["alt_m"]] for s in canonical_samples]
    if len(coords_3d) == 1:
        coords_3d = [coords_3d[0], coords_3d[0]]
    track_geojson: Dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "kind": "flight_ribbon",
                    "duration_s": eff_dur_s,
                    "distance_m": telemetry_doc["distance_m"],
                    "sample_count": len(canonical_samples),
                },
                "geometry": {"type": "LineString", "coordinates": coords_3d},
            }
        ],
    }
    return telemetry_doc, track_geojson


def render_track_preview_image(telemetry_doc: Dict[str, Any], video_path: Optional[Path] = None) -> Image.Image:
    """Extract a real video frame when hosted, or draw a crisp StoryMap GPS ribbon preview card."""
    w, h = 960, 540
    bg: Optional[Image.Image] = None
    if video_path and video_path.is_file():
        seek_s = max(0.0, min(5.0, float(telemetry_doc.get("duration_s") or 0.0) * 0.15))
        try:
            pr = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-ss", f"{seek_s:.2f}",
                    "-i", str(video_path), "-frames:v", "1", "-vf", "scale=960:-1", "-f", "image2pipe", "-vcodec", "png", "-",
                ],
                capture_output=True, timeout=15,
            )
            if pr.returncode == 0 and pr.stdout:
                with Image.open(io.BytesIO(pr.stdout)) as vim:
                    bg = vim.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
        except Exception:
            bg = None

    if bg is None:
        bg = Image.new("RGB", (w, h), (11, 18, 14))
        draw = ImageDraw.Draw(bg)
        for gx in range(0, w, 60):
            draw.line([(gx, 0), (gx, h)], fill=(20, 32, 25), width=1)
        for gy in range(0, h, 60):
            draw.line([(0, gy), (w, gy)], fill=(20, 32, 25), width=1)
        samples = telemetry_doc.get("samples") or []
        if len(samples) >= 2:
            lons = [float(s["lon"]) for s in samples]
            lats = [float(s["lat"]) for s in samples]
            min_x, max_x = min(lons), max(lons)
            min_y, max_y = min(lats), max(lats)
            span_x = max(1e-6, max_x - min_x)
            span_y = max(1e-6, max_y - min_y)
            pad = 90
            pts_xy = [
                (
                    pad + ((lon - min_x) / span_x) * (w - 2 * pad),
                    h - pad - ((lat - min_y) / span_y) * (h - 2 * pad),
                )
                for lon, lat in zip(lons, lats)
            ]
            draw.line(pts_xy, fill=(153, 204, 0), width=6)
            sx, sy = pts_xy[0]
            ex, ey = pts_xy[-1]
            draw.ellipse((sx - 8, sy - 8, sx + 8, sy + 8), fill=(56, 189, 248))
            draw.ellipse((ex - 8, ey - 8, ex + 8, ey + 8), fill=(250, 204, 21))

    return bg
