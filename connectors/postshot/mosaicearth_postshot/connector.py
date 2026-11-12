"""MosaicEarth Jawset Postshot & LichtFeld Studio 3DGS Connector (0.4.0).

Synchronizes completed 3D Gaussian Splatting scenes (`.ply` with spherical
harmonics `f_dc_0`, `.splat`, `.spz`, `.ksplat`) from Jawset Postshot and
LichtFeld Studio to MosaicEarth 3D:
  - Automatic WGS84 georeferencing from companion drone JPEG EXIF/XMP GPS tags,
    `geo.txt` (COLMAP), or `transforms.json` / `gps.json`.
  - Watch-Folder daemon for Postshot / LichtFeld export folders.
  - Instant Share Portal URL, `<iframe>` embed, and `oEmbed` 1.0 discovery URL
    generation with 1-click / automatic clipboard copy.

AGPL-3.0-or-later. Python standard library only.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

try:
    from .mosaicearth_sync import ORIGIN, Client, SyncError, build_manifest
except ImportError:  # pragma: no cover
    from mosaicearth_sync import ORIGIN, Client, SyncError, build_manifest


SPLAT_EXTS = {".ply", ".splat", ".spz", ".ksplat"}


def _parse_jpeg_exif_or_dji_xmp_gps(jpeg_path: Path) -> dict | None:
    """Pure-Python JPEG EXIF GPS IFD + DJI XMP GPS extractor (no Pillow required)."""
    try:
        raw = jpeg_path.read_bytes()
    except OSError:
        return None
    if len(raw) < 4 or raw[:2] != b"\xff\xd8":
        return None

    # 1. Fast check for DJI / standard XMP GPS attributes (`drone-dji:GpsLatitude`, etc.)
    head_str = raw[: min(len(raw), 262144)].decode("latin-1", errors="ignore")
    m_lat = re.search(r'GpsLatitude\s*=\s*"([+-]?\d+(?:\.\d+)?)"', head_str)
    m_lon = re.search(r'GpsLongitude\s*=\s*"([+-]?\d+(?:\.\d+)?)"', head_str)
    if m_lat and m_lon:
        lat = float(m_lat.group(1))
        lon = float(m_lon.group(1))
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and (lat != 0.0 or lon != 0.0):
            out: dict = {"lat": lat, "lon": lon, "source": f"xmp:{jpeg_path.name}"}
            m_alt = re.search(r'AbsoluteAltitude\s*=\s*"([+-]?\d+(?:\.\d+)?)"', head_str)
            m_yaw = re.search(r'FlightYawDegree\s*=\s*"([+-]?\d+(?:\.\d+)?)"', head_str)
            if m_alt:
                out["alt"] = float(m_alt.group(1))
            if m_yaw:
                out["heading"] = float(m_yaw.group(1))
            return out

    # 2. Standard TIFF EXIF APP1 (`Exif\x00\x00`) GPS IFD (`0x8825`)
    pos = 2
    while pos + 4 <= len(raw) and raw[pos] == 0xFF:
        marker = raw[pos + 1]
        if marker in (0xD9, 0xDA):
            break
        seg_len = struct.unpack(">H", raw[pos + 2 : pos + 4])[0]
        seg = raw[pos + 4 : pos + 2 + seg_len]
        if marker == 0xE1 and seg.startswith(b"Exif\x00\x00"):
            tiff = seg[6:]
            if len(tiff) < 8:
                break
            endian = "<" if tiff[:2] == b"II" else (">" if tiff[:2] == b"MM" else "")
            if not endian:
                break
            ifd0_off = struct.unpack(endian + "I", tiff[4:8])[0]
            if ifd0_off + 2 > len(tiff):
                break
            n_entries = struct.unpack(endian + "H", tiff[ifd0_off : ifd0_off + 2])[0]
            gps_off = 0
            for i in range(n_entries):
                ent = tiff[ifd0_off + 2 + 12 * i : ifd0_off + 2 + 12 * (i + 1)]
                if len(ent) < 12:
                    break
                tag, _, _, val_off = struct.unpack(endian + "HHII", ent)
                if tag == 0x8825:
                    gps_off = val_off
                    break
            if gps_off and gps_off + 2 <= len(tiff):
                g_count = struct.unpack(endian + "H", tiff[gps_off : gps_off + 2])[0]
                gps_tags: dict[int, tuple[int, int, bytes]] = {}
                for i in range(g_count):
                    ent = tiff[gps_off + 2 + 12 * i : gps_off + 2 + 12 * (i + 1)]
                    if len(ent) < 12:
                        break
                    gtag, gtype, gcnt, gval = struct.unpack(endian + "HHII", ent)
                    gps_tags[gtag] = (gtype, gcnt, ent[8:12] if gcnt * (8 if gtype == 5 else 1) <= 4 else tiff[gval : gval + gcnt * (8 if gtype == 5 else 1)])

                def _dms(tag_id: int) -> float | None:
                    if tag_id not in gps_tags:
                        return None
                    _, cnt, buf = gps_tags[tag_id]
                    if cnt < 3 or len(buf) < 24:
                        return None
                    vals = []
                    for k in range(3):
                        num, den = struct.unpack(endian + "II", buf[8 * k : 8 * (k + 1)])
                        vals.append(num / den if den else 0.0)
                    return vals[0] + vals[1] / 60.0 + vals[2] / 3600.0

                lat_v = _dms(2)
                lon_v = _dms(4)
                if lat_v is not None and lon_v is not None:
                    lat_ref = gps_tags.get(1, (2, 2, b"N"))[2][:1].upper()
                    lon_ref = gps_tags.get(3, (2, 2, b"E"))[2][:1].upper()
                    if lat_ref == b"S":
                        lat_v = -lat_v
                    if lon_ref == b"W":
                        lon_v = -lon_v
                    if -90.0 <= lat_v <= 90.0 and -180.0 <= lon_v <= 180.0 and (lat_v != 0.0 or lon_v != 0.0):
                        out_e: dict = {"lat": round(lat_v, 8), "lon": round(lon_v, 8), "source": f"exif:{jpeg_path.name}"}
                        if 6 in gps_tags and len(gps_tags[6][2]) >= 8:
                            anum, aden = struct.unpack(endian + "II", gps_tags[6][2][:8])
                            if aden:
                                alt_v = anum / aden
                                if gps_tags.get(5, (1, 1, b"\x00"))[2][:1] == b"\x01":
                                    alt_v = -alt_v
                                out_e["alt"] = round(alt_v, 3)
                        return out_e
        pos += 2 + seg_len
    return None


def extract_georef_from_companion_inputs(source: Path) -> dict | None:
    """Extract WGS84 centroid (`lat`, `lon`, `alt`) from companion COLMAP `geo.txt`, JSON, or drone EXIF photos."""
    p = Path(source).resolve()
    search_dir = p if p.is_dir() else p.parent

    # 1. Explicit gps.json / transforms.json
    for jname in ("gps.json", "georef.json", "transforms.json"):
        cand = search_dir / jname
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                src_d = data.get("georef") if isinstance(data.get("georef"), dict) else data
                if "origin_lla" in src_d and isinstance(src_d["origin_lla"], list) and len(src_d["origin_lla"]) >= 2:
                    lat, lon = float(src_d["origin_lla"][0]), float(src_d["origin_lla"][1])
                    alt = float(src_d["origin_lla"][2]) if len(src_d["origin_lla"]) >= 3 else 0.0
                    return {"lat": lat, "lon": lon, "alt": alt, "source": jname}
                if "lat" in src_d and "lon" in src_d:
                    res = {"lat": float(src_d["lat"]), "lon": float(src_d["lon"]), "source": jname}
                    if "alt" in src_d:
                        res["alt"] = float(src_d["alt"])
                    return res
            except Exception:
                pass

    # 2. COLMAP `geo.txt` (`WGS84` + `image.jpg lon lat alt` or `lat lon alt`)
    for gname in ("geo.txt", "image_gps.txt"):
        cand = search_dir / gname
        if cand.is_file():
            lats, lons, alts = [], [], []
            for line in cand.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or s.upper() in {"WGS84", "EPSG:4326"}:
                    continue
                parts = s.split()
                nums = []
                for tok in parts[1:] if len(parts) >= 3 else parts:
                    try:
                        nums.append(float(tok))
                    except ValueError:
                        pass
                if len(nums) >= 2:
                    # COLMAP geo.txt uses X(lon) Y(lat) Z(alt) when first is >90 or standard COLMAP convention
                    a, b = nums[0], nums[1]
                    if abs(a) > 90.0 and abs(b) <= 90.0:
                        lon_v, lat_v = a, b
                    else:
                        # In COLMAP WGS84 geo.txt, format is `IMAGE_NAME LON LAT ALT` unless explicitly lat/lon
                        lon_v, lat_v = a, b
                        # Heuristic: if user wrote lat first (e.g. 41.9 2.2), check if header mentions lat lon
                    lats.append(lat_v)
                    lons.append(lon_v)
                    if len(nums) >= 3:
                        alts.append(nums[2])
            if lats and lons:
                out_g = {"lat": round(sum(lats) / len(lats), 8), "lon": round(sum(lons) / len(lons), 8), "source": gname}
                if alts:
                    out_g["alt"] = round(sum(alts) / len(alts), 3)
                return out_g

    # 3. Companion JPEG photos (`images/`, `input/`, or current folder)
    photo_dirs = [search_dir]
    for sub_name in ("images", "input", "inputs", "photos", "../images", "../input"):
        sd = (search_dir / sub_name).resolve()
        if sd.is_dir() and sd not in photo_dirs:
            photo_dirs.append(sd)

    sampled_lats, sampled_lons, sampled_alts = [], [], []
    for pd in photo_dirs:
        jpegs = sorted(list(pd.glob("*.jpg")) + list(pd.glob("*.JPG")) + list(pd.glob("*.jpeg")) + list(pd.glob("*.JPEG")))[:24]
        for jpg in jpegs:
            gps = _parse_jpeg_exif_or_dji_xmp_gps(jpg)
            if gps:
                sampled_lats.append(gps["lat"])
                sampled_lons.append(gps["lon"])
                if "alt" in gps:
                    sampled_alts.append(gps["alt"])
        if sampled_lats:
            out_p = {
                "lat": round(sum(sampled_lats) / len(sampled_lats), 8),
                "lon": round(sum(sampled_lons) / len(sampled_lons), 8),
                "source": f"photos:{pd.name} ({len(sampled_lats)} EXIF/XMP samples)",
            }
            if sampled_alts:
                out_p["alt"] = round(sum(sampled_alts) / len(sampled_alts), 3)
            return out_p
    return None


def _validate_completed_splat(splat_file: Path) -> None:
    ext = splat_file.suffix.lower()
    if ext not in SPLAT_EXTS:
        raise SyncError(f"Unsupported 3D Gaussian Splatting file '{splat_file.name}'. Expected .ply, .splat, .spz or .ksplat.")
    if not splat_file.is_file() or splat_file.stat().st_size < 16:
        raise SyncError(f"Gaussian Splat file is empty or incomplete: {splat_file.name}")
    if ext == ".ply":
        with splat_file.open("rb") as fh:
            head = fh.read(65536)
        if b"end_header" not in head or b"f_dc_0" not in head:
            raise SyncError(
                f"'{splat_file.name}' is not a trained 3D Gaussian Splatting PLY (missing spherical harmonics 'f_dc_0'). "
                "Export the trained radiance field from Jawset Postshot or LichtFeld Studio first."
            )


def stage_postshot_splat(
    staging_dir: Path,
    splat_input: Path,
    scene_name: str = "",
    *,
    companion_source: Path | None = None,
    lat: float | None = None,
    lon: float | None = None,
    alt: float | None = None,
    heading: float | None = None,
    pitch: float | None = None,
    roll: float | None = None,
    scale: float | None = None,
    studio_tool: str = "postshot",
    target_project_id: int = 0,
    survey_id: int = 0,
    survey_name: str = "",
    survey_date: str = "",
    origin_key: str = "",
    progress=None,
):
    """Stage one or more Postshot / LichtFeld Studio 3DGS files into a verified MosaicEarth snapshot."""
    staging_dir = Path(staging_dir).resolve()
    src = Path(splat_input).resolve()
    if not src.exists():
        raise SyncError(f"Postshot / LichtFeld input not found: {src}")

    splat_files: list[Path] = []
    attachments: list[Path] = []
    if src.is_file():
        _validate_completed_splat(src)
        splat_files.append(src)
        for cand_att in sorted(src.parent.glob(f"{src.stem}.*")):
            if cand_att != src and cand_att.suffix.lower() in {".json", ".txt", ".yml", ".yaml"}:
                attachments.append(cand_att)
    else:
        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() in SPLAT_EXTS:
                try:
                    _validate_completed_splat(p)
                    splat_files.append(p)
                except SyncError:
                    continue
            elif p.name.lower() in {"cameras.json", "transforms.json", "cfg_args", "gps.json", "geo.txt"}:
                attachments.append(p)

    if not splat_files:
        raise SyncError(f"No trained 3D Gaussian Splatting models (.ply, .splat, .spz, .ksplat) found in {src}")

    products_dir = staging_dir / "products"
    attachments_dir = staging_dir / "attachments"
    products_dir.mkdir(parents=True, exist_ok=True)
    file_classes: dict[str, str] = {}

    for idx, sf in enumerate(splat_files):
        dest = products_dir / sf.name
        if dest.exists():
            dest = products_dir / f"{idx}_{sf.name}"
        shutil.copyfile(sf, dest)
        file_classes[dest.relative_to(staging_dir).as_posix()] = "gaussian"

    for att in attachments:
        attachments_dir.mkdir(parents=True, exist_ok=True)
        dest_a = attachments_dir / att.name
        if not dest_a.exists():
            shutil.copyfile(att, dest_a)

    # Auto-detect WGS84 coordinates if not explicitly supplied
    auto_geo = extract_georef_from_companion_inputs(companion_source or src)
    georef: dict = {}
    eff_lat = lat if lat is not None else (auto_geo.get("lat") if auto_geo else None)
    eff_lon = lon if lon is not None else (auto_geo.get("lon") if auto_geo else None)
    eff_alt = alt if alt is not None else (auto_geo.get("alt") if auto_geo else None)
    if eff_lat is not None and eff_lon is not None:
        georef["lat"] = float(eff_lat)
        georef["lon"] = float(eff_lon)
    if eff_alt is not None:
        georef["alt"] = float(eff_alt)
    if heading is not None:
        georef["heading"] = float(heading)
    elif auto_geo and "heading" in auto_geo:
        georef["heading"] = float(auto_geo["heading"])
    if pitch is not None:
        georef["pitch"] = float(pitch)
    if roll is not None:
        georef["roll"] = float(roll)
    if scale is not None:
        georef["scale"] = float(scale)

    eff_name = (scene_name or (src.stem if src.is_file() else src.name) or "Postshot 3DGS Scene").strip()[:120]
    eff_date = (survey_date or "").strip()[:10]
    tool_tag = "lichtfeld" if "lichtfeld" in studio_tool.lower() else "postshot"
    safe_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", eff_name).strip("-") or "postshot-scene"
    eff_origin = origin_key or f"{tool_tag}:{safe_slug}"
    if eff_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff_date):
        eff_origin = f"{eff_origin}:{eff_date}"

    meta: dict = {
        "connector": tool_tag,
        "postshot": {
            "tool": tool_tag,
            "scene_name": eff_name,
            "georef_source": auto_geo.get("source", "explicit") if (georef and auto_geo) else ("explicit" if georef else "none"),
        },
        "file_classes": file_classes,
    }
    if georef:
        meta["georef"] = georef
    if int(target_project_id or 0) > 0:
        meta["target_project_id"] = int(target_project_id)
    if int(survey_id or 0) > 0:
        meta["survey_id"] = int(survey_id)
    if survey_name and survey_name.strip():
        meta["survey_name"] = survey_name.strip()[:120]
    if eff_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff_date):
        meta["survey_date"] = eff_date

    def role_for(rel: str) -> str:
        if rel.startswith("products/") and Path(rel).suffix.lower() in SPLAT_EXTS:
            return "product"
        return "attachment"

    manifest, paths = build_manifest(staging_dir, eff_origin, eff_name, meta, role_for, progress)
    for item in manifest["files"]:
        if item["role"] == "product":
            item["gaussian"] = True
            item["asset_class"] = "gaussian"
    return manifest, paths


def fetch_share_and_oembed_links(client: Client, project_id: int, asset_id: int, *, visibility: str = "unlisted") -> dict:
    """Request instant Client Delivery Share URL, `<iframe>` embed, and `oEmbed` 1.0 JSON URL."""
    res = client.request(
        "POST",
        "/api/v2/bridge/share-links",
        {"project_id": int(project_id), "asset_id": int(asset_id), "visibility": visibility},
    )
    share_url = res.get("share_url") or res.get("viewer_url") or ""
    oembed_json_url = f"{client.origin}/api/oembed?url={urllib.parse.quote(share_url, safe='')}&format=json" if share_url else ""
    res["oembed_url"] = oembed_json_url
    return res


def copy_text_to_clipboard(text: str) -> bool:
    """Best-effort cross-platform clipboard copy (Windows `clip`, macOS `pbcopy`, Wayland/X11, or `tkinter`)."""
    if not text:
        return False
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["clip"]):
        exe = shutil.which(cmd[0])
        if exe:
            try:
                subprocess.run([exe, *cmd[1:]], input=text.encode("utf-8"), check=True, timeout=4)
                return True
            except Exception:
                continue
    try:
        import tkinter as tk

        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return True
    except Exception:
        return False


def watch_postshot_exports(
    watch_dir: Path,
    client: Client,
    *,
    companion_photos: Path | None = None,
    target_project_id: int = 0,
    survey_name: str = "",
    copy_share_link: bool = True,
    poll_seconds: float = 6.0,
    max_cycles: int = 0,
    on_event=None,
) -> list[dict]:
    """Watch a Jawset Postshot or LichtFeld Studio export directory and upload completed `.ply`/`.spz`/`.splat` scenes."""
    watch_dir = Path(watch_dir).resolve()
    if not watch_dir.is_dir():
        raise SyncError(f"Watch folder not found: {watch_dir}")
    state_file = watch_dir / ".mosaicearth-postshot-watched.json"
    try:
        synced = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    except Exception:
        synced = {}

    pushed: list[dict] = []
    cycles = 0
    while True:
        cycles += 1
        for sf in sorted(p for p in watch_dir.iterdir() if p.is_file() and p.suffix.lower() in SPLAT_EXTS):
            sig = f"{sf.stat().st_size}:{int(sf.stat().st_mtime)}"
            if synced.get(sf.name) == sig:
                continue
            try:
                _validate_completed_splat(sf)
            except SyncError:
                continue
            if on_event:
                on_event(f"Uploading 3DGS '{sf.name}' to MosaicEarth…")
            with tempfile.TemporaryDirectory(prefix="mosaicearth-postshot-watch-") as tmp:
                manifest, paths = stage_postshot_splat(
                    Path(tmp),
                    sf,
                    sf.stem,
                    companion_source=companion_photos,
                    target_project_id=target_project_id,
                    survey_name=survey_name,
                )
                state = client.push(manifest, paths)
            synced[sf.name] = sig
            state_file.write_text(json.dumps(synced, indent=2), encoding="utf-8")
            entry = {"file": sf.name, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}
            products = state.get("products") or []
            if products and state.get("task_id"):
                try:
                    links = fetch_share_and_oembed_links(client, int(state["task_id"]), int(products[0]["asset_id"]))
                    entry.update(share_url=links.get("share_url"), embed_iframe=links.get("embed_iframe"), oembed_url=links.get("oembed_url"))
                    if copy_share_link and links.get("share_url"):
                        copy_text_to_clipboard(links["share_url"])
                except Exception:
                    pass
            pushed.append(entry)
            if on_event:
                on_event(f"Published '{sf.name}' -> {entry.get('share_url') or ('Project #' + str(state['project_id']))}")
        if max_cycles > 0 and cycles >= max_cycles:
            break
        time.sleep(max(1.0, float(poll_seconds)))
    return pushed


def pull_cloud_splats(client: Client, destination: Path, *, snapshot_id: str = "", cloud_project_id: int = 0, progress=None) -> dict:
    """Download 3D Gaussian Splats from MosaicEarth Cloud for editing in Postshot or LichtFeld Studio."""
    sid = snapshot_id
    if not sid:
        if int(cloud_project_id or 0) <= 0:
            raise SyncError("Select a Cloud project or snapshot ID to pull.")
        state = client.request("POST", f"/api/v2/sync/projects/{int(cloud_project_id)}/export", {})
        sid = state["id"]
        while state["status"] in {"QUEUED", "VERIFYING"}:
            if progress:
                progress(0, 0, "Preparing Cloud export…")
            time.sleep(2)
            state = client.request("GET", f"/api/v2/sync/sessions/{sid}")
        if state["status"] != "COMPLETE":
            raise SyncError(state.get("error") or "Cloud export failed")
    target_dir = Path(destination).resolve() / sid[:12]
    state = client.pull(sid, target_dir)
    return {"snapshot_id": sid, "name": state["manifest"]["name"], "directory": target_dir}


def _apply_dark_theme(root, ttk):
    root.configure(bg="#0f1812")
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", background="#15221a", foreground="#e9f1eb", fieldbackground="#0d1610", bordercolor="#35523b")
    style.configure("TLabelframe", background="#15221a", foreground="#b6e893", bordercolor="#35523b")
    style.configure("TLabelframe.Label", background="#15221a", foreground="#b6e893", font=("Segoe UI", 10, "bold"))
    style.configure("TLabel", background="#15221a", foreground="#e9f1eb")
    style.configure("TFrame", background="#0f1812")
    style.configure("TButton", background="#284625", foreground="#eff8e9", bordercolor="#72915c", padding=(10, 6))
    style.map("TButton", background=[("active", "#366032")])
    style.configure("Accent.TButton", background="#99cc00", foreground="#0c150f", font=("Segoe UI", 9, "bold"), padding=(12, 6))
    style.map("Accent.TButton", background=[("active", "#b3e619")])


def launch_gui():
    """Dark MosaicEarth desktop GUI for Jawset Postshot & LichtFeld Studio 3D Gaussian Splatting."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    session = {"token": os.environ.get("MOSAICEARTH_TOKEN", ""), "email": "", "origin": ORIGIN, "share_url": "", "embed_iframe": ""}
    root = tk.Tk()
    root.title("MosaicEarth · Jawset Postshot & LichtFeld Studio 3DGS Connector (v0.4.0)")
    root.geometry("750x640")
    _apply_dark_theme(root, ttk)

    pad = {"padx": 12, "pady": 6}
    frm_auth = ttk.LabelFrame(root, text="1. MosaicEarth Cloud Account")
    frm_auth.pack(fill="x", **pad)

    var_origin = tk.StringVar(value=ORIGIN)
    var_email = tk.StringVar(value=os.environ.get("MOSAICEARTH_EMAIL", ""))
    var_pass = tk.StringVar()
    var_auth = tk.StringVar(value="Token active" if session["token"] else "Sign in with your MosaicEarth account")

    ttk.Label(frm_auth, text="Cloud Origin:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_auth, textvariable=var_origin, width=46).grid(row=0, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_auth, text="Email:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_auth, textvariable=var_email, width=46).grid(row=1, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_auth, text="Password:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_auth, textvariable=var_pass, show="*", width=46).grid(row=2, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_auth, textvariable=var_auth).grid(row=3, column=1, sticky="w", padx=8, pady=4)

    frm_cfg = ttk.LabelFrame(root, text="2. 3D Gaussian Splat (.ply / .spz / .splat / .ksplat) & Auto-GPS")
    frm_cfg.pack(fill="x", **pad)

    cb_proj = ttk.Combobox(frm_cfg, state="readonly", width=48)
    cb_proj["values"] = ["0: Create / Match Cloud Project Automatically"]
    cb_proj.current(0)

    var_splat = tk.StringVar()
    var_photos = tk.StringVar()
    var_name = tk.StringVar(value="Postshot 3DGS Scene")
    var_lat = tk.StringVar()
    var_lon = tk.StringVar()
    var_alt = tk.StringVar()
    var_sdate = tk.StringVar()
    var_share = tk.StringVar(value="Share & oEmbed URL will be copied automatically on completion.")

    def browse_splat():
        chosen = filedialog.askopenfilename(
            title="Select Postshot / LichtFeld 3DGS File",
            filetypes=[("3D Gaussian Splats", "*.ply *.spz *.splat *.ksplat"), ("All files", "*.*")],
        )
        if chosen:
            var_splat.set(chosen)
            p = Path(chosen)
            var_name.set(p.stem)
            geo = extract_georef_from_companion_inputs(p)
            if geo:
                var_lat.set(str(geo["lat"]))
                var_lon.set(str(geo["lon"]))
                if "alt" in geo:
                    var_alt.set(str(geo["alt"]))

    def browse_photos():
        chosen = filedialog.askdirectory(title="Select Companion Drone Photos / COLMAP Folder (for EXIF WGS84 GPS)")
        if chosen:
            var_photos.set(chosen)
            geo = extract_georef_from_companion_inputs(Path(chosen))
            if geo:
                var_lat.set(str(geo["lat"]))
                var_lon.set(str(geo["lon"]))
                if "alt" in geo:
                    var_alt.set(str(geo["alt"]))

    ttk.Label(frm_cfg, text="Cloud Project:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
    cb_proj.grid(row=0, column=1, sticky="we", padx=8, pady=4)
    ttk.Label(frm_cfg, text="3DGS File:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_splat, width=42).grid(row=1, column=1, sticky="we", padx=8, pady=4)
    ttk.Button(frm_cfg, text="Browse…", command=browse_splat).grid(row=1, column=2, padx=6, pady=4)
    ttk.Label(frm_cfg, text="Photos / GPS Dir:").grid(row=2, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_photos, width=42).grid(row=2, column=1, sticky="we", padx=8, pady=4)
    ttk.Button(frm_cfg, text="Auto-GPS…", command=browse_photos).grid(row=2, column=2, padx=6, pady=4)
    ttk.Label(frm_cfg, text="Scene Name:").grid(row=3, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_name, width=46).grid(row=3, column=1, sticky="we", padx=8, pady=4)

    frm_gps = ttk.Frame(frm_cfg)
    frm_gps.grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=4)
    ttk.Label(frm_cfg, text="WGS84 (Lat/Lon/Alt):").grid(row=4, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_gps, textvariable=var_lat, width=13).pack(side="left", padx=2)
    ttk.Entry(frm_gps, textvariable=var_lon, width=13).pack(side="left", padx=2)
    ttk.Entry(frm_gps, textvariable=var_alt, width=10).pack(side="left", padx=2)
    ttk.Label(frm_gps, text="Date:").pack(side="left", padx=(8, 2))
    ttk.Entry(frm_gps, textvariable=var_sdate, width=12).pack(side="left", padx=2)

    ttk.Label(frm_cfg, text="Share / oEmbed:").grid(row=5, column=0, sticky="w", padx=8, pady=4)
    ttk.Entry(frm_cfg, textvariable=var_share, width=46).grid(row=5, column=1, columnspan=2, sticky="we", padx=8, pady=4)

    var_status = tk.StringVar(value="Ready.")
    ttk.Label(root, textvariable=var_status, wraplength=700).pack(fill="x", padx=14, pady=8)

    def refresh_projects():
        client = Client(token=session["token"], origin=session["origin"])
        projs = client.request("GET", "/api/v2/sync/projects").get("projects", [])
        cb_proj["values"] = ["0: Create / Match Cloud Project Automatically"] + [f"{p['id']}: {p['name']}" for p in projs]
        cb_proj.current(0)

    def sign_in():
        try:
            client = Client(origin=var_origin.get().strip())
            user = client.signin(var_email.get().strip(), var_pass.get())
            var_pass.set("")
            session.update(token=client.token, email=user.get("email") or var_email.get().strip(), origin=client.origin)
            var_auth.set(f"Signed in as {session['email']}")
            refresh_projects()
        except Exception as exc:
            messagebox.showerror("Sign-In Error", str(exc))

    ttk.Button(frm_auth, text="Sign In", command=sign_in).grid(row=3, column=0, padx=8, pady=4)

    def publish_splat():
        if not session["token"]:
            messagebox.showwarning("MosaicEarth", "Sign in first.")
            return
        sp = var_splat.get().strip()
        if not sp:
            messagebox.showwarning("MosaicEarth", "Choose a .ply, .spz, .splat, or .ksplat file.")
            return
        pid = int(cb_proj.get().split(":", 1)[0])

        def worker():
            try:
                lat_f = float(var_lat.get()) if var_lat.get().strip() else None
                lon_f = float(var_lon.get()) if var_lon.get().strip() else None
                alt_f = float(var_alt.get()) if var_alt.get().strip() else None
                comp = Path(var_photos.get().strip()) if var_photos.get().strip() else None
                with tempfile.TemporaryDirectory(prefix="mosaicearth-postshot-gui-") as tmp:
                    manifest, paths = stage_postshot_splat(
                        Path(tmp),
                        Path(sp),
                        var_name.get().strip() or "Postshot 3DGS Scene",
                        companion_source=comp,
                        lat=lat_f,
                        lon=lon_f,
                        alt=alt_f,
                        target_project_id=pid,
                        survey_date=var_sdate.get().strip(),
                        progress=lambda d, t, m: var_status.set(m),
                    )
                    client = Client(
                        token=session["token"],
                        origin=session["origin"],
                        progress=lambda d, t, m: var_status.set(f"{m} ({int(100 * d / max(t, 1))}%)"),
                    )
                    state = client.push(manifest, paths)
                    prods = state.get("products") or []
                    if prods and state.get("task_id"):
                        links = fetch_share_and_oembed_links(client, int(state["task_id"]), int(prods[0]["asset_id"]))
                        session["share_url"] = links.get("share_url", "")
                        session["embed_iframe"] = links.get("embed_iframe", "")
                        var_share.set(session["share_url"])
                        copy_text_to_clipboard(session["share_url"])
                        var_status.set("Published! Share & oEmbed URL copied to clipboard.")
                    else:
                        var_status.set(f"Uploaded to MyEarth Project #{state['project_id']}")
            except Exception as exc:
                var_status.set(f"Error: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def copy_iframe():
        if session.get("embed_iframe"):
            copy_text_to_clipboard(session["embed_iframe"])
            var_status.set("Copied responsive <iframe> 3DGS embed snippet to clipboard!")

    act = ttk.Frame(root)
    act.pack(fill="x", padx=12, pady=8)
    ttk.Button(act, text="Publish 3DGS & Copy Share Link", style="Accent.TButton", command=publish_splat).pack(side="left", padx=4)
    ttk.Button(act, text="Copy <iframe> Embed Code", command=copy_iframe).pack(side="left", padx=4)
    root.mainloop()


def main(argv=None):
    if argv is None and len(sys.argv) == 1:
        launch_gui()
        return

    parser = argparse.ArgumentParser(description="MosaicEarth Jawset Postshot & LichtFeld Studio 3DGS Connector (v0.4.0)")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--origin", default=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", ORIGIN))
    parser.add_argument("--email", default=os.environ.get("MOSAICEARTH_EMAIL", ""))
    sub = parser.add_subparsers(dest="cmd")

    p_gps = sub.add_parser("extract-gps", help="Inspect companion photos/COLMAP folder and print WGS84 coordinates")
    p_gps.add_argument("source", type=Path)

    p_push = sub.add_parser("push", help="Publish a trained 3DGS file or folder (.ply/.spz/.splat/.ksplat) to MyEarth")
    p_push.add_argument("splat", type=Path)
    p_push.add_argument("--name", default="")
    p_push.add_argument("--photos", type=Path, default=None, help="Companion photo/COLMAP directory for EXIF WGS84 GPS")
    p_push.add_argument("--lat", type=float, default=None)
    p_push.add_argument("--lon", type=float, default=None)
    p_push.add_argument("--alt", type=float, default=None)
    p_push.add_argument("--heading", type=float, default=None)
    p_push.add_argument("--scale", type=float, default=None)
    p_push.add_argument("--tool", default="postshot", choices=["postshot", "lichtfeld"])
    p_push.add_argument("--project-id", type=int, default=0)
    p_push.add_argument("--survey-name", default="")
    p_push.add_argument("--survey-date", default="")
    p_push.add_argument("--no-clipboard", action="store_true")

    p_watch = sub.add_parser("watch", help="Watch a Postshot / LichtFeld export folder and auto-publish new 3DGS scenes")
    p_watch.add_argument("folder", type=Path)
    p_watch.add_argument("--photos", type=Path, default=None)
    p_watch.add_argument("--project-id", type=int, default=0)
    p_watch.add_argument("--survey-name", default="")
    p_watch.add_argument("--poll-seconds", type=float, default=6.0)
    p_watch.add_argument("--once", action="store_true")

    p_pull = sub.add_parser("pull", help="Download Cloud 3DGS scenes locally")
    p_pull.add_argument("--dest", type=Path, required=True)
    p_pull.add_argument("--snapshot-id", default="")
    p_pull.add_argument("--project-id", type=int, default=0)

    args = parser.parse_args(argv)
    if args.gui or not args.cmd:
        launch_gui()
        return

    if args.cmd == "extract-gps":
        geo = extract_georef_from_companion_inputs(args.source)
        print(json.dumps({"ok": bool(geo), "georef": geo}, indent=2))
        return

    token = os.environ.get("MOSAICEARTH_TOKEN", "")
    client = Client(token=token, origin=args.origin, progress=lambda d, t, m: print(f"[{d}/{t}] {m}", flush=True))
    if not client.token:
        email = args.email or input("MosaicEarth email: ").strip()
        password = os.environ.get("MOSAICEARTH_PASSWORD") or getpass.getpass("MosaicEarth password: ")
        client.signin(email, password)

    if args.cmd == "push":
        eff_name = args.name.strip() or (args.splat.stem if args.splat.is_file() else args.splat.resolve().name)
        with tempfile.TemporaryDirectory(prefix="mosaicearth-postshot-") as tmp:
            manifest, paths = stage_postshot_splat(
                Path(tmp),
                args.splat,
                eff_name,
                companion_source=args.photos,
                lat=args.lat,
                lon=args.lon,
                alt=args.alt,
                heading=args.heading,
                scale=args.scale,
                studio_tool=args.tool,
                target_project_id=args.project_id,
                survey_name=args.survey_name,
                survey_date=args.survey_date,
            )
            state = client.push(manifest, paths)
            out = {"ok": True, "snapshot_id": state["id"], "project_id": state["project_id"], "task_id": state.get("task_id")}
            prods = state.get("products") or []
            if prods and state.get("task_id"):
                try:
                    links = fetch_share_and_oembed_links(client, int(state["task_id"]), int(prods[0]["asset_id"]))
                    out.update(
                        share_url=links.get("share_url"),
                        embed_url=links.get("embed_url"),
                        embed_iframe=links.get("embed_iframe"),
                        oembed_url=links.get("oembed_url"),
                    )
                    if not args.no_clipboard and links.get("share_url"):
                        out["copied_to_clipboard"] = copy_text_to_clipboard(links["share_url"])
                except Exception:
                    pass
            print(json.dumps(out, indent=2))
    elif args.cmd == "watch":
        pushed = watch_postshot_exports(
            args.folder,
            client,
            companion_photos=args.photos,
            target_project_id=args.project_id,
            survey_name=args.survey_name,
            poll_seconds=args.poll_seconds,
            max_cycles=1 if args.once else 0,
            on_event=lambda m: print(m, flush=True),
        )
        print(json.dumps({"ok": True, "pushed": pushed}, indent=2))
    elif args.cmd == "pull":
        res = pull_cloud_splats(client, args.dest, snapshot_id=args.snapshot_id, cloud_project_id=args.project_id, progress=client.progress)
        print(json.dumps({"ok": True, "snapshot_id": res["snapshot_id"], "directory": str(res["directory"])}, indent=2))


if __name__ == "__main__":
    main()
