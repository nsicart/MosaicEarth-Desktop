import io
import json
import os
import struct
import zlib
import zipfile
from pathlib import Path
from typing import Dict, Any, List


def _make_me_png_icon(size: int) -> bytes:
    """Generates an RGBA PNG of the official MosaicEarth 'ME' monogram (White 'M' + Lime '#92d400' 'E' on black)."""
    if size == 256:
        for candidate in (
            Path("/mnt/servidor/Projectes/MosaicEarth/4dtwinmaps-live/Resources/logo.png"),
            Path(__file__).resolve().parent.parent / "4dtwinmaps-live" / "Resources" / "logo.png",
        ):
            if candidate.is_file():
                return candidate.read_bytes()

    width = height = size
    raw_rows = bytearray()

    for y in range(height):
        raw_rows.append(0)
        ny = y / float(size)
        for x in range(width):
            nx = x / float(size)
            if nx < 0.04 or nx > 0.96 or ny < 0.04 or ny > 0.96:
                raw_rows.extend((48, 56, 49, 255))
                continue

            in_y = 0.29 <= ny <= 0.71
            is_m = False
            is_e = False

            if in_y:
                rel_y = (ny - 0.29) / 0.42
                if 0.115 <= nx <= 0.213:
                    is_m = True
                elif 0.462 <= nx <= 0.560:
                    is_m = True
                elif 0.213 < nx < 0.462:
                    dist_center = abs(nx - 0.3375) / 0.1245
                    diag_y = 0.68 * (1.0 - dist_center)
                    if diag_y - 0.16 <= rel_y <= diag_y + 0.20:
                        is_m = True

                if 0.596 <= nx <= 0.700:
                    is_e = True
                elif 0.700 < nx <= 0.885 and rel_y <= 0.21:
                    is_e = True
                elif 0.700 < nx <= 0.864 and 0.39 <= rel_y <= 0.59:
                    is_e = True
                elif 0.700 < nx <= 0.893 and rel_y >= 0.79:
                    is_e = True

            if is_m:
                raw_rows.extend((255, 255, 255, 255))
            elif is_e:
                raw_rows.extend((146, 212, 0, 255))
            else:
                raw_rows.extend((2, 3, 2, 255))

    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND", b"")
    )


def build_mosaicearth_ico_bytes() -> bytes:
    """Builds a multi-resolution Windows .ico file (256x256, 64x64, 48x48, 32x32, 16x16) with the unified 'ME' mark."""
    sizes = [256, 64, 48, 32, 16]
    pngs = [_make_me_png_icon(s) for s in sizes]
    count = len(sizes)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + count * 16
    entries = bytearray()
    for s, p in zip(sizes, pngs):
        w_byte = 0 if s >= 256 else s
        h_byte = 0 if s >= 256 else s
        entries.extend(struct.pack("<BBBBHHII", w_byte, h_byte, 0, 0, 1, 32, len(p), offset))
        offset += len(p)
    return header + bytes(entries) + b"".join(pngs)


def build_desktop_seed_json() -> str:
    """Builds a seed JSON containing 3D Globe public assets, categories, and a Local Workstation User."""
    from .db_store import get_store

    store = get_store()
    pubs = [
        dict(r)
        for r in store.tables.get("dbo.WEB_PUBLICATIONS", [])
        if int(r.get("STATUS_ID") or 0) == 2
    ]
    pub_pids = {int(r.get("PROJECT_ID") or r.get("ID_PROJECT") or 0) for r in pubs}

    ext_assets = [
        dict(r)
        for r in store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])
        if bool(r.get("IS_PUBLIC")) or int(r.get("PUBLICATION_STATUS_ID") or 0) == 2
    ]
    for r in ext_assets:
        pub_pids.add(int(r.get("PROJECT_ID") or 0))

    projects = [
        dict(r)
        for r in store.tables.get("dbo.WEB_PROJECTS", [])
        if int(r.get("ID") or 0) in pub_pids
    ]
    locations = [
        dict(r)
        for r in store.tables.get("dbo.WEB_PROJECTS_LOCATION", [])
        if int(r.get("PROJECT_ID") or r.get("ID_PROJECT") or 0) in pub_pids
    ]
    values = [
        dict(r)
        for r in store.tables.get("dbo.WEB_PROJECTS_VALUES", [])
        if int(r.get("PROJECT_ID") or r.get("ID_PROJECT") or 0) in pub_pids
    ]
    cesium_models = [
        dict(r)
        for r in store.tables.get("dbo.WEB_CESIUM_MODELS", [])
        if int(r.get("Project_id") or r.get("PROJECT_ID") or r.get("ID_PROJECT") or 0) in pub_pids
    ]

    public_mymaps = [
        dict(r)
        for r in store.tables.get("dbo.WEB_MYMAPS", [])
        if str(r.get("VISIBILITY") or "").lower() == "public" or bool(r.get("IS_PUBLIC"))
    ]
    pub_map_ids = {int(r.get("ID") or 0) for r in public_mymaps}
    mymap_items = [
        dict(r)
        for r in store.tables.get("dbo.WEB_MYMAPS_ITEMS", [])
        if int(r.get("MYMAP_ID") or 0) in pub_map_ids
    ]

    # Collect public creator names (sanitized, without passwords/emails) + User ID=1 as Local Workstation User
    creator_ids = {int(r.get("CREATE_ID") or r.get("USER_ID") or 0) for r in projects} | {
        int(r.get("USER_ID") or r.get("CREATE_ID") or 0) for r in ext_assets
    }
    seeded_users: List[Dict[str, Any]] = [
        {
            "ID": 1,
            "NOMBRE": "Local",
            "APELLIDO1": "Workstation",
            "APELLIDO2": "(PC)",
            "NAME": "Local",
            "SURNAME": "Workstation",
            "COMPANY": "MosaicEarth Desktop (Local PC)",
            "EMAIL": "local@mosaicearth.desktop",
            "ACTIVO": True,
            "ACTIVE": True,
            "ROL_ID": 1,
            "IS_ADMIN": True,
            "CREDITS": 999999,
            "PLAN_KEY": "studio",
            "STORAGE_QUOTA_GB": 10000,
        }
    ]
    for u in store.tables.get("dbo.USERS", []):
        uid = int(u.get("ID") or 0)
        if uid > 1 and uid in creator_ids:
            seeded_users.append(
                {
                    "ID": uid,
                    "NOMBRE": str(u.get("NOMBRE") or u.get("NAME") or "MosaicEarth"),
                    "APELLIDO1": str(u.get("APELLIDO1") or u.get("SURNAME") or ""),
                    "APELLIDO2": str(u.get("APELLIDO2") or ""),
                    "NAME": str(u.get("NOMBRE") or u.get("NAME") or "MosaicEarth"),
                    "SURNAME": str(u.get("APELLIDO1") or u.get("SURNAME") or ""),
                    "EMAIL": f"creator{uid}@mosaicearth.app",
                    "ACTIVO": True,
                    "ACTIVE": True,
                    "ROL_ID": 2,
                    "PLAN_KEY": "pro",
                }
            )

    # Create a starter Local Project + Survey in MyEarth for User ID=1 so the hierarchy is ready out of the box
    max_pid = max([int(r.get("ID") or 0) for r in projects] + [9000])
    starter_proj_id = max_pid + 1
    starter_survey_id = max_pid + 2
    projects.append(
        {
            "ID": starter_proj_id,
            "NAME": "01 My Local PC Project",
            "DESCRIPTION": "Stored locally on your PC hard drive (MosaicEarth_Local_Data). Unlimited local storage & 0.00 EUR processing.",
            "CREATE_ID": 1,
            "USER_ID": 1,
            "STATE_ID": 4,
            "KIND": "container",
            "CONTAINER_LEVEL": "project",
            "FOLDER": f"{starter_proj_id}-local-project",
            "CREATE_DATE": "2026-05-01T10:00:00",
        }
    )
    projects.append(
        {
            "ID": starter_survey_id,
            "NAME": "01 Initial Local Field Survey",
            "DESCRIPTION": "Click '+ Add Data (Photogrammetry / Import / Splats)' to process drone images or import GeoTIFF, LAZ, OBJ, GLB, PLY locally.",
            "CREATE_ID": 1,
            "USER_ID": 1,
            "STATE_ID": 4,
            "KIND": "container",
            "CONTAINER_LEVEL": "survey",
            "PARENT_CONTAINER_ID": starter_proj_id,
            "FOLDER": f"{starter_survey_id}-local-survey",
            "CREATE_DATE": "2026-05-01T10:01:00",
        }
    )
    hierarchy_links = [
        {
            "ID": 1,
            "PARENT_ID": starter_proj_id,
            "CHILD_ID": starter_survey_id,
            "LINK_TYPE": "project_survey",
            "USER_ID": 1,
        }
    ]

    seed_payload = {
        "web_config_settings": {
            "CesiumIonToken": "",
            "BingMapsKey": store.get_setting("BingMapsKey", ""),
            "PublicSiteUrl": "http://127.0.0.1:8095",
        },
        "tables": {
            "dbo.USERS": seeded_users,
            "dbo.WEB_USERS": seeded_users,
            "dbo.WEB_CATEGORY": list(store.tables.get("dbo.WEB_CATEGORY", [])),
            "dbo.WEB_PROJECTS": projects,
            "dbo.WEB_PUBLICATIONS": pubs,
            "dbo.WEB_PROJECTS_LOCATION": locations,
            "dbo.WEB_PROJECTS_VALUES": values,
            "dbo.WEB_CESIUM_MODELS": cesium_models,
            "dbo.MOSAICEARTH_EXTERNAL_ASSETS": ext_assets,
            "dbo.WEB_MYMAPS": public_mymaps,
            "dbo.WEB_MYMAPS_ITEMS": mymap_items,
            "dbo.MOSAICEARTH_HIERARCHY_LINKS": hierarchy_links,
        },
    }
    return json.dumps(seed_payload, ensure_ascii=False)


DESKTOP_SERVER_PY = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MosaicEarth Desktop — Identical Full Web Platform Running Locally on PC (v3.0.0)
Runs the full MosaicEarth FastAPI engine locally on http://127.0.0.1:8095 with:
- Real 3D Globe (/en/Map) + Cesium Ion 3D World Terrain Relief
- Real 1st-Person Flight Simulator (/Fly.aspx) + All 3D Aircraft + M AGL Radar
- Full 4-Level MyEarth Hierarchy (/en/Projects) + Photogrammetry + Direct Import + Gaussian Splats
- Transparent local file cache & local PC storage (~/MosaicEarth_Local_Data)
"""
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

DESKTOP_VERSION = "3.6.3"
PORT = int(os.environ.get("MOSAICEARTH_DESKTOP_PORT", "8095"))
CLOUD_ORIGIN = os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app").rstrip("/")
LOCAL_DATA_DIR = Path(os.environ.get("MOSAICEARTH_LOCAL_DIR", str(Path.home() / "MosaicEarth_Local_Data")))
WEB_CACHE_DIR = LOCAL_DATA_DIR / "web_cache"
APP_ROOT_DIR = Path(__file__).resolve().parent

LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
WEB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
(LOCAL_DATA_DIR / "blobs" / "imagenes").mkdir(parents=True, exist_ok=True)
(LOCAL_DATA_DIR / "blobs" / "assets").mkdir(parents=True, exist_ok=True)
(LOCAL_DATA_DIR / "blobs" / "results").mkdir(parents=True, exist_ok=True)

if sys.stdout is None or sys.stderr is None:
    _log_f = open(LOCAL_DATA_DIR / "desktop_server.log", "a", encoding="utf-8", errors="ignore")
    if sys.stdout is None:
        sys.stdout = _log_f
    if sys.stderr is None:
        sys.stderr = _log_f

os.environ["MOSAICEARTH_DESKTOP_LOCAL"] = "1"
os.environ["MOSAICEARTH_DESKTOP_MODE"] = "1"
os.environ["MOSAICEARTH_DATA_DIR"] = str(LOCAL_DATA_DIR)
os.environ["MOSAICEARTH_WEB_ROOT"] = str(WEB_CACHE_DIR)
os.environ["MOSAICEARTH_CLOUD_ORIGIN"] = CLOUD_ORIGIN
os.environ["MOSAICEARTH_PUBLIC_URL"] = f"http://127.0.0.1:{PORT}"
_proj_grids_dir = str(APP_ROOT_DIR / "homelab" / "proj_grids")
_existing_proj = os.environ.get("PROJ_DATA") or os.environ.get("PROJ_LIB") or "/usr/share/proj"
os.environ["PROJ_DATA"] = f"{_existing_proj}:{_proj_grids_dir}" if os.name != "nt" else f"{_existing_proj};{_proj_grids_dir}"
os.environ["PROJ_LIB"] = os.environ["PROJ_DATA"]
os.environ["PROJ_NETWORK"] = "OFF"

if str(APP_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(APP_ROOT_DIR))


def open_native_app_window(url: str) -> None:
    if platform.system() == "Windows":
        for candidate in [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ]:
            if os.path.exists(candidate):
                subprocess.Popen([candidate, f"--app={url}", "--window-size=1480,920"])
                return
    webbrowser.open(url)


def ensure_previous_server_stopped() -> bool:
    """Returns True if v3.2.0 is already running; stops any older server on PORT."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/desktop/version", timeout=0.9) as r:
            st = json.loads(r.read().decode("utf-8", errors="ignore"))
            if st.get("version") == DESKTOP_VERSION and st.get("engine") == "homelab-fastapi":
                return True
    except Exception:
        pass

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/local/shutdown", method="POST", data=b"{}")
        urllib.request.urlopen(req, timeout=1.0)
        time.sleep(0.6)
    except Exception:
        pass

    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"],
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            for line in out.splitlines():
                if f":{PORT} " in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        if pid.isdigit() and int(pid) != os.getpid():
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                            )
                            time.sleep(0.4)
        except Exception:
            pass
    return False


def _background_desktop_startup_tasks() -> None:
    """Ensures Local NodeODM / Native Windows ODM is ready, cleans any legacy placeholder meshes, and syncs live public catalog + 3D heights."""
    time.sleep(1.5)
    try:
        # Remove any legacy 816-byte placeholder green pyramid model.glb files from earlier versions
        blobs_img = LOCAL_DATA_DIR / "blobs" / "imagenes"
        if blobs_img.is_dir():
            for glb_p in blobs_img.rglob("model.glb"):
                try:
                    if glb_p.stat().st_size <= 1024:
                        ts_p = glb_p.parent / "tileset.json"
                        glb_p.unlink(missing_ok=True)
                        if ts_p.is_file():
                            ts_p.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        from homelab.db_store import get_store
        store = get_store()
        with store.lock:
            changed_proj = False
            for p in store.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("STATE_ID") or 0) == 3 and str(p.get("ENGINE_NODEODM_URL") or "").startswith("native-odm://"):
                    p["STATE_ID"] = 0
                    p["LAST_ERROR"] = ""
                    p["ENGINE_NODEODM_URL"] = ""
                    changed_proj = True
            if changed_proj:
                store.persist_table("dbo.WEB_PROJECTS")
        store.ensure_local_nodeodm_started()
    except Exception:
        pass

    try:
        from homelab.db_store import get_store
        store = get_store()
        req = urllib.request.Request(
            f"{CLOUD_ORIGIN}/api/v2/desktop/public-catalog",
            headers={"User-Agent": "MosaicEarth-Desktop/3.3"},
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            if resp.status == 200:
                cat = json.loads(resp.read().decode("utf-8", errors="ignore"))
                if cat.get("ok"):
                    with store.lock:
                        for tbl_name in (
                            "dbo.WEB_PUBLICATIONS",
                            "dbo.WEB_PROJECTS",
                            "dbo.WEB_PROJECTS_LOCATION",
                            "dbo.WEB_PROJECTS_VALUES",
                            "dbo.MOSAICEARTH_EXTERNAL_ASSETS",
                            "dbo.WEB_MYMAPS",
                            "dbo.WEB_MYMAPS_ITEMS",
                        ):
                            remote_rows = cat.get(tbl_name) or []
                            if not remote_rows:
                                continue
                            local_rows = store.tables.setdefault(tbl_name, [])
                            existing_ids = {
                                (int(r.get("ID") or 0), int(r.get("MYMAP_ID") or 0), int(r.get("PROJECT_ID") or 0), int(r.get("PUBLICATION_ID") or 0))
                                for r in local_rows
                            }
                            added = False
                            for rr in remote_rows:
                                key = (int(rr.get("ID") or 0), int(rr.get("MYMAP_ID") or 0), int(rr.get("PROJECT_ID") or 0), int(rr.get("PUBLICATION_ID") or 0))
                                if key not in existing_ids:
                                    local_rows.append(rr)
                                    existing_ids.add(key)
                                    added = True
                            if added:
                                store.persist_table(tbl_name)

                        # Sync dbo.WEB_CESIUM_MODELS so all online 3D Models & Point Clouds have exact terrain Height offsets
                        remote_models = cat.get("dbo.WEB_CESIUM_MODELS") or []
                        if remote_models:
                            local_models = store.tables.setdefault("dbo.WEB_CESIUM_MODELS", [])
                            by_key = {
                                (int(m.get("Project_id") or m.get("PROJECT_ID") or 0), int(m.get("ModelType") or 0)): m
                                for m in local_models
                            }
                            changed_models = False
                            for rm in remote_models:
                                k = (int(rm.get("Project_id") or rm.get("PROJECT_ID") or 0), int(rm.get("ModelType") or 0))
                                if k[0] <= 0:
                                    continue
                                if k not in by_key:
                                    local_models.append(rm)
                                    by_key[k] = rm
                                    changed_models = True
                                elif float(by_key[k].get("Height") or 0.0) == 0.0 and float(rm.get("Height") or 0.0) != 0.0:
                                    by_key[k]["Height"] = rm.get("Height")
                                    changed_models = True
                            if changed_models:
                                store.persist_table("dbo.WEB_CESIUM_MODELS")
    except Exception:
        pass


def auto_sync_latest_app_bundle() -> None:
    """Checks if a newer MosaicEarth Desktop version is available on CLOUD_ORIGIN and hot-updates homelab/*.py."""
    import io
    import zipfile
    try:
        req = urllib.request.Request(f"{CLOUD_ORIGIN}/api/desktop/version", headers={"User-Agent": "MosaicEarth-Desktop/3.4"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            if resp.status == 200:
                info = json.loads(resp.read().decode("utf-8", errors="ignore"))
                remote_ver = str(info.get("version") or "")
                if remote_ver and remote_ver != DESKTOP_VERSION:
                    b_req = urllib.request.Request(f"{CLOUD_ORIGIN}/api/desktop/app-bundle.zip?t={int(time.time())}", headers={"User-Agent": "MosaicEarth-Desktop/3.4"})
                    with urllib.request.urlopen(b_req, timeout=25.0) as b_resp:
                        if b_resp.status == 200:
                            with zipfile.ZipFile(io.BytesIO(b_resp.read()), "r") as zf:
                                zf.extractall(APP_ROOT_DIR)
    except Exception:
        pass


if __name__ == "__main__":
    start_url = f"http://127.0.0.1:{PORT}/en/Map"
    if ensure_previous_server_stopped():
        if "--no-window" not in sys.argv:
            open_native_app_window(start_url)
        sys.exit(0)

    auto_sync_latest_app_bundle()

    import uvicorn
    from homelab.app import app

    threading.Thread(target=_background_desktop_startup_tasks, daemon=True).start()

    if "--no-window" not in sys.argv:
        threading.Timer(0.9, lambda: open_native_app_window(start_url)).start()

    # Bind to 0.0.0.0 so Local PC Share links (http://<LAN_IP>:8095/...) work while the app is open
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
'''


def build_desktop_app_bundle_zip(
    licensed_user_id: int = 0,
    licensed_email: str = "",
    license_token: str = "",
) -> bytes:
    """Packages mosaicearth_desktop.py, homelab/* (full FastAPI platform), desktop_seed.json, license manifest, and icons into a zip archive."""
    homelab_dir = Path(__file__).resolve().parent
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mosaicearth_desktop.py", DESKTOP_SERVER_PY)
        zf.writestr("MosaicEarth.ico", build_mosaicearth_ico_bytes())
        zf.writestr("MosaicEarth.png", _make_me_png_icon(256))
        zf.writestr("desktop_seed.json", build_desktop_seed_json())
        if licensed_user_id > 0 and licensed_email and license_token:
            import datetime as _dt
            now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            lic_payload = json.dumps(
                {
                    "licensed_user_id": int(licensed_user_id),
                    "licensed_email": str(licensed_email).strip().lower(),
                    "license_token": str(license_token).strip(),
                    "session_bound": True,
                    "issued_at_utc": now_iso,
                    "last_verified_utc": now_iso,
                    "offline_grace_days": 30,
                    "max_workstations": 2,
                },
                indent=2,
            )
            zf.writestr("desktop_license.json", lic_payload)

        for item in sorted(homelab_dir.iterdir()):
            if item.is_file() and item.suffix in (".py", ".js"):
                zf.writestr(f"homelab/{item.name}", item.read_bytes())

        proj_grids_dir = homelab_dir / "proj_grids"
        if proj_grids_dir.is_dir():
            for gfile in sorted(proj_grids_dir.iterdir()):
                if gfile.is_file():
                    zf.writestr(f"homelab/proj_grids/{gfile.name}", gfile.read_bytes())
        egm96_sys = Path("/usr/share/proj/egm96_15.gtx")
        if egm96_sys.is_file() and not (proj_grids_dir / "egm96_15.gtx").is_file():
            zf.writestr("homelab/proj_grids/egm96_15.gtx", egm96_sys.read_bytes())

    return buf.getvalue()


def build_unix_turnkey_installer_sh(
    os_kind: str = "linux",
    cloud_base_url: str = "https://app.mosaicearth.app",
    licensed_user_id: int = 0,
    licensed_email: str = "",
    license_token: str = "",
) -> str:
    is_mac = os_kind == "mac"
    os_title = "macOS (Apple Silicon & Intel)" if is_mac else "Linux (Ubuntu / Debian / Fedora)"
    lic_qs = f"&uid={int(licensed_user_id)}&email={licensed_email}&token={license_token}" if (licensed_user_id > 0 and license_token) else ""
    lic_json = json.dumps({
        "licensed_user_id": int(licensed_user_id),
        "licensed_email": str(licensed_email).strip().lower(),
        "license_token": str(license_token).strip(),
        "session_bound": True,
    })
    return f"""#!/usr/bin/env bash
# ============================================================================
# MosaicEarth Desktop v3.6.3 — Licensed Turnkey Installer ({os_title})
# Bound to MosaicEarth Account: {licensed_email or 'Licensed User'}
# ============================================================================
set -e
CLOUD_URL="{cloud_base_url}"
INSTALL_DIR="$HOME/.local/share/MosaicEarthDesktop"
LOCAL_DATA_DIR="$HOME/MosaicEarth_Local_Data"

echo "============================================================================"
echo "  MosaicEarth Desktop v3.6.3 — Automated Installer ({os_title})"
echo "  Licensed & Session-Bound to: {licensed_email or 'Authorized Account'}"
echo "============================================================================"
echo "[1/5] Creating local installation & data directories..."
pkill -f "python.*mosaicearth_desktop" 2>/dev/null || true
mkdir -p "$INSTALL_DIR/app" "$LOCAL_DATA_DIR/projects"

echo "[2/5] Downloading MosaicEarth Desktop full platform bundle & official ME icon..."
curl -fsSL "$CLOUD_URL/api/desktop/app-bundle.zip?t=$(date +%s){lic_qs}" -o "$INSTALL_DIR/app-bundle.zip"
python3 -m zipfile -e "$INSTALL_DIR/app-bundle.zip" "$INSTALL_DIR/app"
cat > "$LOCAL_DATA_DIR/desktop_license.json" <<'LIC_EOF'
{lic_json}
LIC_EOF
cp -f "$LOCAL_DATA_DIR/desktop_license.json" "$INSTALL_DIR/app/desktop_license.json"
chmod +x "$INSTALL_DIR/app/mosaicearth_desktop.py"

echo "[3/5] Preparing local Python virtual environment (FastAPI + Uvicorn)..."
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade fastapi uvicorn python-multipart httpx pillow numpy "laspy[lazrs]" trimesh scipy pyproj imageio imageio-ffmpeg

cat > "$INSTALL_DIR/launch_mosaicearth.sh" <<'EOF'
#!/usr/bin/env bash
nohup "$HOME/.local/share/MosaicEarthDesktop/venv/bin/python" "$HOME/.local/share/MosaicEarthDesktop/app/mosaicearth_desktop.py" "$@" >/dev/null 2>&1 &
EOF
chmod +x "$INSTALL_DIR/launch_mosaicearth.sh"

echo "[4/6] Checking Official OpenDroneMap Engine (opendronemap/nodeodm:latest)..."
if curl -fsSL --max-time 2 http://127.0.0.1:3000/info 2>/dev/null | grep -q "engineVersion"; then
    echo "  -> Local OpenDroneMap (NodeODM) is already running on port 3000."
elif curl -fsSL --max-time 2 http://127.0.0.1:3001/info 2>/dev/null | grep -q "engineVersion"; then
    echo "  -> Local OpenDroneMap (NodeODM) is already running on port 3001."
else
    if ! command -v docker >/dev/null 2>&1; then
        echo "  -> Docker not detected. Attempting automated Docker installation..."
        if command -v apt-get >/dev/null 2>&1; then
            sudo apt-get update -y && sudo apt-get install -y docker.io || true
            sudo systemctl enable --now docker || true
        fi
    fi
    if command -v docker >/dev/null 2>&1; then
        docker pull opendronemap/nodeodm:latest 2>/dev/null || sg docker -c "docker pull opendronemap/nodeodm:latest" 2>/dev/null || sudo docker pull opendronemap/nodeodm:latest || true
        docker rm -f mosaicearth-local-nodeodm 2>/dev/null || sg docker -c "docker rm -f mosaicearth-local-nodeodm" 2>/dev/null || true
        docker run -d --name mosaicearth-local-nodeodm --restart unless-stopped -p 3000:3000 -v "$LOCAL_DATA_DIR/nodeodm:/var/www/data" opendronemap/nodeodm:latest 2>/dev/null \
            || sg docker -c "docker run -d --name mosaicearth-local-nodeodm --restart unless-stopped -p 3000:3000 -v \"$LOCAL_DATA_DIR/nodeodm:/var/www/data\" opendronemap/nodeodm:latest" 2>/dev/null \
            || sudo docker run -d --name mosaicearth-local-nodeodm --restart unless-stopped -p 3000:3000 -v "$LOCAL_DATA_DIR/nodeodm:/var/www/data" opendronemap/nodeodm:latest || true
    fi
fi

echo "[5/6] Creating 'MosaicEarth Desktop' icon on your Desktop..."
if [ "{os_kind}" = "mac" ]; then
    APP_BUNDLE="$HOME/Desktop/MosaicEarth Desktop.command"
    cp "$INSTALL_DIR/launch_mosaicearth.sh" "$APP_BUNDLE"
    chmod +x "$APP_BUNDLE"
else
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    mkdir -p "$DESKTOP_DIR" "$HOME/.local/share/applications"
    cat > "$DESKTOP_DIR/MosaicEarth-Desktop.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=MosaicEarth Desktop
Comment=Local 3D Geospatial Workstation & OpenDroneMap Photogrammetry
Exec=$INSTALL_DIR/launch_mosaicearth.sh
Icon=$INSTALL_DIR/app/MosaicEarth.png
Terminal=false
Categories=Science;Geoscience;Graphics;
EOF
    chmod +x "$DESKTOP_DIR/MosaicEarth-Desktop.desktop"
    cp "$DESKTOP_DIR/MosaicEarth-Desktop.desktop" "$HOME/.local/share/applications/MosaicEarth-Desktop.desktop"
    gio set "$DESKTOP_DIR/MosaicEarth-Desktop.desktop" metadata::trusted true 2>/dev/null || true
fi

echo "[6/6] Starting MosaicEarth Desktop (Local Mode on http://127.0.0.1:8095 + Local OpenDroneMap)..."
"$INSTALL_DIR/launch_mosaicearth.sh" "$@"
echo "Done! You can now open MosaicEarth Desktop anytime from the icon on your Desktop."
"""


def build_windows_turnkey_installer_bat(
    cloud_base_url: str = "https://app.mosaicearth.app",
    licensed_user_id: int = 0,
    licensed_email: str = "",
    license_token: str = "",
) -> str:
    """Returns a hybrid Windows .bat + PowerShell WinForms GUI Installer for MosaicEarth Desktop v3.6.3 bound to the user's license."""
    lic_qs = f"&uid={int(licensed_user_id)}&email={licensed_email}&token={license_token}" if (licensed_user_id > 0 and license_token) else ""
    lic_json = json.dumps({
        "licensed_user_id": int(licensed_user_id),
        "licensed_email": str(licensed_email).strip().lower(),
        "license_token": str(license_token).strip(),
        "session_bound": True,
    })
    tpl = r"""@echo off
REM ============================================================================
REM MosaicEarth Desktop v3.0 — Automated Turnkey Graphical Installer for Windows
REM Installs the Complete MosaicEarth Platform Locally (3D Globe with Terrain,
REM Flight Simulator with 3D Aircraft, MyEarth Hierarchy, and Photogrammetry).
REM ============================================================================
title MosaicEarth Desktop Installer
echo Launching MosaicEarth Desktop Graphical Setup Wizard...
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$batPath = '%~f0'; $lines = [System.IO.File]::ReadAllLines($batPath); $idx = [Array]::IndexOf($lines, '# === POWERSHELL_INSTALLER_START ==='); $psCode = ($lines[($idx+1)..($lines.Length-1)] -join [Environment]::NewLine); Invoke-Expression $psCode"
exit /b 0

# === POWERSHELL_INSTALLER_START ===
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$CloudUrl = "__CLOUD_BASE_URL__"
$InstallDir = Join-Path $env:LOCALAPPDATA "MosaicEarthDesktop"
$RuntimeDir = Join-Path $InstallDir "runtime"
$AppDir = Join-Path $InstallDir "app"
$LocalDataDir = Join-Path $env:USERPROFILE "MosaicEarth_Local_Data"
$IcoPath = Join-Path $InstallDir "MosaicEarth.ico"
$ExePath = Join-Path $InstallDir "MosaicEarthDesktop.exe"

$form = New-Object System.Windows.Forms.Form
$form.Text = "MosaicEarth Desktop v3.0 — Setup Wizard"
$form.Size = New-Object System.Drawing.Size(580, 410)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(12, 18, 14)
$form.ForeColor = [System.Drawing.Color]::White

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = "MosaicEarth Desktop (Full Local Platform)"
$lblTitle.Font = New-Object System.Drawing.Font("Segoe UI", 16, [System.Drawing.FontStyle]::Bold)
$lblTitle.ForeColor = [System.Drawing.Color]::FromArgb(153, 204, 0)
$lblTitle.Location = New-Object System.Drawing.Point(24, 20)
$lblTitle.Size = New-Object System.Drawing.Size(520, 32)
$form.Controls.Add($lblTitle)

$lblSub = New-Object System.Windows.Forms.Label
$lblSub.Text = "Installing full 3D Globe, Terrain, Flight Simulator, MyEarth & Local Photogrammetry..."
$lblSub.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)
$lblSub.ForeColor = [System.Drawing.Color]::FromArgb(200, 215, 205)
$lblSub.Location = New-Object System.Drawing.Point(24, 54)
$lblSub.Size = New-Object System.Drawing.Size(520, 24)
$form.Controls.Add($lblSub)

$prog = New-Object System.Windows.Forms.ProgressBar
$prog.Location = New-Object System.Drawing.Point(24, 88)
$prog.Size = New-Object System.Drawing.Size(516, 24)
$prog.Style = "Continuous"
$prog.Value = 5
$form.Controls.Add($prog)

$txtLog = New-Object System.Windows.Forms.TextBox
$txtLog.Multiline = $true
$txtLog.ReadOnly = $true
$txtLog.ScrollBars = "Vertical"
$txtLog.BackColor = [System.Drawing.Color]::FromArgb(18, 26, 21)
$txtLog.ForeColor = [System.Drawing.Color]::FromArgb(215, 235, 220)
$txtLog.Font = New-Object System.Drawing.Font("Consolas", 9)
$txtLog.Location = New-Object System.Drawing.Point(24, 124)
$txtLog.Size = New-Object System.Drawing.Size(516, 180)
$form.Controls.Add($txtLog)

$btnLaunch = New-Object System.Windows.Forms.Button
$btnLaunch.Text = "Installing..."
$btnLaunch.Enabled = $false
$btnLaunch.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$btnLaunch.BackColor = [System.Drawing.Color]::FromArgb(153, 204, 0)
$btnLaunch.ForeColor = [System.Drawing.Color]::FromArgb(10, 15, 12)
$btnLaunch.FlatStyle = "Flat"
$btnLaunch.Location = New-Object System.Drawing.Point(300, 318)
$btnLaunch.Size = New-Object System.Drawing.Size(240, 38)
$btnLaunch.Add_Click({
    Start-Process -FilePath $ExePath
    $form.Close()
})
$form.Controls.Add($btnLaunch)

function Write-Step($pct, $msg) {
    $prog.Value = $pct
    $txtLog.AppendText("[" + (Get-Date -Format "HH:mm:ss") + "] " + $msg + [Environment]::NewLine)
    [System.Windows.Forms.Application]::DoEvents()
}

$form.Add_Shown({
    $form.Refresh()
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

        Write-Step 8 "Stopping any previous MosaicEarth Desktop background server..."
        Get-Process -Name "MosaicEarthDesktop", "pythonw", "python", "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400

        Write-Step 16 "Creating local installation & data folders..."
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
        New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $LocalDataDir "projects") | Out-Null

        Write-Step 30 "Downloading MosaicEarth Desktop full platform bundle & unified ME icon..."
        $bundleZip = Join-Path $InstallDir "app-bundle.zip"
        Invoke-WebRequest -Uri "$CloudUrl/api/desktop/app-bundle.zip?t=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())__LIC_QS__" -OutFile $bundleZip -UseBasicParsing
        Invoke-WebRequest -Uri "$CloudUrl/api/desktop/icon.ico?t=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" -OutFile $IcoPath -UseBasicParsing
        Expand-Archive -LiteralPath $bundleZip -DestinationPath $AppDir -Force
        $licJson = '__LIC_JSON__'
        Set-Content -LiteralPath (Join-Path $LocalDataDir "desktop_license.json") -Value $licJson -Encoding UTF8
        Set-Content -LiteralPath (Join-Path $AppDir "desktop_license.json") -Value $licJson -Encoding UTF8
        if (Test-Path $IcoPath) {
            $form.Icon = New-Object System.Drawing.Icon($IcoPath)
        }

        $pyExe = Join-Path $RuntimeDir "python.exe"
        $pywExe = Join-Path $RuntimeDir "pythonw.exe"
        if (-not (Test-Path $pyExe)) {
            Write-Step 48 "Installing embedded Python 3.11 runtime..."
            $pyZip = Join-Path $InstallDir "python-embed.zip"
            Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" -OutFile $pyZip -UseBasicParsing
            Expand-Archive -LiteralPath $pyZip -DestinationPath $RuntimeDir -Force
            Remove-Item $pyZip -Force -ErrorAction SilentlyContinue
        }

        Write-Step 56 "Configuring Python site-packages & application paths..."
        $pthPath = Join-Path $RuntimeDir "python311._pth"
        $pthContent = "python311.zip`r`n.`r`n..\app`r`nLib\site-packages`r`nimport site`r`n"
        Set-Content -LiteralPath $pthPath -Value $pthContent -Encoding ASCII

        $uvicornCheck = Join-Path $RuntimeDir "Lib\site-packages\uvicorn"
        if (-not (Test-Path $uvicornCheck)) {
            Write-Step 65 "Installing FastAPI & Uvicorn local web engine (one-time setup)..."
            $getPip = Join-Path $InstallDir "get-pip.py"
            Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
            $p1 = Start-Process -FilePath $pyExe -ArgumentList "`"$getPip`" --no-warn-script-location" -WorkingDirectory $RuntimeDir -WindowStyle Hidden -Wait -PassThru
            Write-Step 74 "Installing FastAPI, Uvicorn & HTTPX packages..."
            $p2 = Start-Process -FilePath $pyExe -ArgumentList "-m pip install --no-warn-script-location --quiet fastapi uvicorn python-multipart httpx pillow numpy `"laspy[lazrs]`" trimesh scipy pyproj imageio imageio-ffmpeg" -WorkingDirectory $RuntimeDir -WindowStyle Hidden -Wait -PassThru
        } else {
            Write-Step 74 "FastAPI & Uvicorn local engine already installed."
        }

        Write-Step 80 "Configuring Official Native Windows OpenDroneMap v3.6.2 Engine (0.00 EUR · No Docker required)..."
        New-Item -ItemType Directory -Force -Path (Join-Path $LocalDataDir "blobs\imagenes") | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $LocalDataDir "blobs\assets") | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $LocalDataDir "blobs\results") | Out-Null
        $OdmDir = Join-Path $InstallDir "ODM"
        $OdmRunPy = Join-Path $OdmDir "run.py"
        if (-not (Test-Path $OdmRunPy)) {
            try {
                Write-Step 82 "Downloading Official Native Windows OpenDroneMap v3.6.2 (one-time setup)..."
                $OdmSetupExe = Join-Path $InstallDir "ODM_Setup_3.6.2.exe"
                Invoke-WebRequest -Uri "https://github.com/OpenDroneMap/ODM/releases/download/v3.6.2/ODM_Setup_3.6.2.exe" -OutFile $OdmSetupExe -UseBasicParsing
                Write-Step 85 "Installing Native OpenDroneMap v3.6.2 silently into MosaicEarth Desktop..."
                Start-Process -FilePath $OdmSetupExe -ArgumentList "/VERYSILENT /SILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=`"$OdmDir`"" -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue
                Remove-Item $OdmSetupExe -Force -ErrorAction SilentlyContinue
            } catch {
                Write-Step 85 "OpenDroneMap cloud-assisted local fallback configured."
            }
        } else {
            Write-Step 85 "Official Native Windows OpenDroneMap v3.6.2 already installed."
        }

        Write-Step 88 "Configuring Windows Firewall permission for Local Share (TCP port 8095)..."
        Start-Process -FilePath "netsh" -ArgumentList "advfirewall firewall add rule name=`"MosaicEarth Desktop Local Share (TCP 8095)`" dir=in action=allow protocol=TCP localport=8095" -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue

        Write-Step 91 "Building native Windows application (MosaicEarthDesktop.exe)..."
        $csCode = @'
using System;
using System.Diagnostics;
using System.IO;
class Program {
    [STAThread]
    static void Main() {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string pyw = Path.Combine(baseDir, "runtime", "pythonw.exe");
        string script = Path.Combine(baseDir, "app", "mosaicearth_desktop.py");
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = pyw;
        psi.Arguments = "\"" + script + "\"";
        psi.WorkingDirectory = Path.Combine(baseDir, "app");
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
    }
}
'@
        $csPath = Join-Path $InstallDir "Launcher.cs"
        Set-Content -LiteralPath $csPath -Value $csCode -Encoding UTF8
        $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
        if (-not (Test-Path $csc)) {
            $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
        }
        & $csc /nologo /target:winexe "/out:$ExePath" "/win32icon:$IcoPath" "$csPath" | Out-Null

        Write-Step 93 "Creating 'MosaicEarth Desktop' icon on Desktop & Start Menu..."
        $wsh = New-Object -ComObject WScript.Shell
        $desktopPath = [Environment]::GetFolderPath("Desktop")
        $lnkDesktop = $wsh.CreateShortcut((Join-Path $desktopPath "MosaicEarth Desktop.lnk"))
        $lnkDesktop.TargetPath = $ExePath
        $lnkDesktop.WorkingDirectory = $InstallDir
        $lnkDesktop.IconLocation = "$IcoPath,0"
        $lnkDesktop.Description = "MosaicEarth Desktop — Local Geospatial Workstation"
        $lnkDesktop.Save()

        $startMenuPath = [Environment]::GetFolderPath("Programs")
        $lnkStart = $wsh.CreateShortcut((Join-Path $startMenuPath "MosaicEarth Desktop.lnk"))
        $lnkStart.TargetPath = $ExePath
        $lnkStart.WorkingDirectory = $InstallDir
        $lnkStart.IconLocation = "$IcoPath,0"
        $lnkStart.Description = "MosaicEarth Desktop — Local Geospatial Workstation"
        $lnkStart.Save()

        Write-Step 100 "Installation complete! Launching MosaicEarth Desktop (3D Globe, Flight Sim & MyEarth)..."
        $lblSub.Text = "Installed! Opening MosaicEarth Desktop (identical to Web, running locally on your PC)."
        $btnLaunch.Text = "OPEN MOSAICEARTH DESKTOP"
        $btnLaunch.Enabled = $true

        Start-Process -FilePath $ExePath
    } catch {
        Write-Step 100 ("ERROR: " + $_.Exception.Message)
        $btnLaunch.Text = "Close"
        $btnLaunch.Enabled = $true
    }
})

[void]$form.ShowDialog()
"""
    return (
        tpl.replace("__CLOUD_BASE_URL__", cloud_base_url)
        .replace("__LIC_QS__", lic_qs)
        .replace("__LIC_JSON__", lic_json)
        .replace("\r\n", "\n")
        .replace("\n", "\r\n")
    )
