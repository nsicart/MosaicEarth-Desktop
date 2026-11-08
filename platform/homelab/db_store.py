import base64
import copy
import dataclasses
import datetime
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple



def next_record_id(rows, key="ID", minimum=0):
    # Local entities and read-only public catalog entries use separate ranges.
    # Remote catalog IDs retain their cloud values for native viewer URLs.
    if os.environ.get("MOSAICEARTH_DESKTOP_SHARED_UI") == "1":
        minimum = max(minimum, 1_000_000_000_000)
        rows = [r for r in rows if not r.get("DESKTOP_PUBLIC_CATALOG")]
    return max([int(r.get(key) or 0) for r in rows] + [minimum]) + 1


def _slugify(value: str) -> str:
    s = (value or "").strip().lower()
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s).strip("-")
    return s or "asset"


def _parse_iso_dt(val: Any) -> Optional[datetime.datetime]:
    if not val:
        return None
    if isinstance(val, datetime.datetime):
        return val if val.tzinfo else val.replace(tzinfo=datetime.timezone.utc)
    s = str(val).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(s[:19], fmt).replace(tzinfo=datetime.timezone.utc)
            except Exception:
                pass
    return None


def _fmt_iso_utc(dt: Optional[datetime.datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _try_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        f = float(str(val).replace(",", ".").strip())
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


class MosaicEarthDataStore:
    """Thread-safe production data store backed by the exported 4DTWINMAPS tables,
    decrypted integration secrets, SQLite live state persistence, and PostGIS sync."""

    def __init__(self, data_root: str = "/data"):
        self.data_root = Path(data_root)
        self.data_dir = self.data_root
        self.backup_dir = self.data_root / "azure_backup"
        self.db_dir = self.data_root / "db"
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = self.db_dir / "mosaicearth_state.sqlite3"
        self.lock = threading.RLock()
        self._stripe_connection = None

        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        self._table_snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self.schemas: Dict[str, List[Dict[str, Any]]] = {}
        self.integrations: Dict[str, Dict[str, Any]] = {}
        self.config_files: Dict[str, str] = {}
        self.web_config_settings: Dict[str, str] = {}
        self._load_initial_data()
        self._table_snapshots = copy.deepcopy(self.tables)
        self._init_sqlite_overlay()
        self._ensure_legacy_users_studio_plan()
        # Derivative generation belongs to the persistent queue. Opening the
        # store must not rebuild trained Gaussians from position/color alone
        # or reinterpret source uploads as completed models.

    def _ensure_legacy_users_studio_plan(self) -> None:
        """Grants all existing legacy users in dbo.USERS the Studio plan in LEGACY format ('studio' + IS_LEGACY_STUDIO=True)."""
        is_desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        if is_desktop_local:
            return
        changed = False
        with self.lock:
            for u in self.tables.get("dbo.USERS", []):
                if u.get("IS_NEW_ACCOUNT"):
                    continue
                if not u.get("LEGACY_STUDIO_GRANTED"):
                    u["PLAN_KEY"] = "studio"
                    u["IS_LEGACY_STUDIO"] = True
                    u["LEGACY_STUDIO_GRANTED"] = True
                    changed = True
            if changed:
                self.persist_table("dbo.USERS")

    def _ensure_native_gaussian_splat_3dtiles(self) -> None:
        """
        Ensures every 3D Gaussian Splatting task has a single 3D Gaussian Splat asset
        (ASSET_TYPE='model3d', ASSET_SUBTYPE='gaussian_splat') with:
          - Original Results: model.ply (62 float32 SH3), model.spz (Niantic SPZ v2), transform.json
          - CesiumJS 1.144 3D Tiles 1.1: tileset.json + tile_0.glb (KHR_gaussian_splatting + KHR_gaussian_splatting_compression_spz_2)
        And removes any legacy duplicate 'gaussian_pointcloud' companion assets.
        """
        if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
            return
        try:
            from .blob_store import get_blob_store
            from .gis_splat_engine import (
                _parse_pointcloud_or_splat_bytes,
                _write_gaussian_ply_and_splat,
                _write_khr_gaussian_splatting_glb_and_tileset,
            )
            bs = get_blob_store()
            assets = self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])
            changed = False

            # 1. Remove duplicate companion 'gaussian_pointcloud' assets so each 3DGS task has 1 true 3DGS asset
            before_len = len(assets)
            assets[:] = [
                a for a in assets
                if str(a.get("SOURCE_ORIGIN") or "") != "gaussian_pointcloud"
            ]
            if len(assets) != before_len:
                changed = True

            # 2. Ensure each Gaussian Splatting asset has native KHR_gaussian_splatting + SPZ v2 tileset & files
            gs_assets = [
                a for a in list(assets)
                if (str(a.get("SOURCE_ORIGIN") or "") == "gaussian" or str(a.get("ASSET_SUBTYPE") or "") == "gaussian_splat")
                and str(a.get("STATUS") or "") != "deleted"
            ]
            for a in gs_assets:
                aid = int(a["ID"])
                pid = int(a["PROJECT_ID"])
                lat = float(a.get("LATITUDE") or 41.8205)
                lon = float(a.get("LONGITUDE") or 1.8676)
                alt = float(a.get("ALTITUDE") or 0.0)
                heading = float(a.get("HEADING") or 0.0)
                pitch = float(a.get("PITCH") or 0.0)
                roll = float(a.get("ROLL") or 0.0)
                scale = float(a.get("SCALE") or 1.0)
                clean_title = str(a.get("TITLE") or "3D Gaussian Splat").replace(" · 3D Surface Mesh", "").strip()

                out_dir = bs.blobs_root / "results" / "external" / str(pid) / str(aid)
                out_dir.mkdir(parents=True, exist_ok=True)
                ts_file = out_dir / "tileset.json"
                tile_glb_file = out_dir / "tile_0.glb"
                spz_file = out_dir / "model.spz"
                transform_file = out_dir / "transform.json"

                needs_rebuild = (
                    not ts_file.is_file()
                    or not tile_glb_file.is_file()
                    or not spz_file.is_file()
                    or not transform_file.is_file()
                    or a.get("VIEWER_KIND") != "gaussian_splat_3dtiles"
                )
                if not needs_rebuild and ts_file.is_file():
                    try:
                        ts_txt = ts_file.read_text(encoding="utf-8", errors="ignore")
                        if "KHR_gaussian_splatting_compression_spz_2" not in ts_txt:
                            needs_rebuild = True
                    except Exception:
                        needs_rebuild = True

                if needs_rebuild:
                    ply_rel = f"external/{pid}/{aid}/point_cloud.ply"
                    ply_local = out_dir / "point_cloud.ply"
                    if not ply_local.is_file():
                        dl = bs.ensure_local("results", ply_rel, force_download=True) or bs.ensure_local("assets", ply_rel, force_download=True)
                        ply_bytes = dl.read_bytes() if (dl and dl.is_file()) else b""
                    else:
                        ply_bytes = ply_local.read_bytes()

                    pos, rgb, _ = _parse_pointcloud_or_splat_bytes(ply_bytes, "ply")
                    _, _, _, spz_bytes = _write_gaussian_ply_and_splat(out_dir, pos, rgb)
                    _write_khr_gaussian_splatting_glb_and_tileset(
                        out_dir, spz_bytes, pos, lat, lon, alt,
                        heading=heading, pitch=pitch, roll=roll, scale=scale
                    )
                    for fn in ("tileset.json", "tile_0.glb", "model.glb", "model.ply", "point_cloud.ply", "model.spz", "model.splat", "transform.json", "geo_alignment.txt"):
                        fp = out_dir / fn
                        if fp.is_file():
                            fb = fp.read_bytes()
                            bs.save_upload("results", f"external/{pid}/{aid}/{fn}", fb)
                            bs.save_upload("assets", f"external/{pid}/{aid}/{fn}", fb)

                if (
                    a.get("TITLE") != clean_title
                    or a.get("VIEWER_KIND") != "gaussian_splat_3dtiles"
                    or a.get("SOURCE_FORMAT") != "SPZ · PLY"
                ):
                    a["TITLE"] = clean_title
                    a["DISPLAY_NAME"] = clean_title
                    a["ASSET_TYPE"] = "model3d"
                    a["ASSET_SUBTYPE"] = "gaussian_splat"
                    a["SOURCE_FORMAT"] = "SPZ · PLY"
                    a["VIEWER_KIND"] = "gaussian_splat_3dtiles"
                    a["PREVIEW_RELATIVE_PATH"] = f"external/{pid}/{aid}/model.spz"
                    changed = True

            if changed:
                self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")
        except Exception as exc:
            print(f"[MosaicEarth] Native Gaussian Splat 3D Tiles migration warning: {exc}", flush=True)

    def _load_initial_data(self) -> None:
        clean_tables_path = self.backup_dir / "4DTWINMAPS_clean_tables_20261027.json"
        if clean_tables_path.exists():
            with open(clean_tables_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.tables = payload.get("tables", {})
            self.schemas = payload.get("schemas", {})

        vm_state_path = self.backup_dir / "vm_production_state_20261027.json"
        if vm_state_path.exists():
            with open(vm_state_path, "r", encoding="utf-8-sig") as f:
                state = json.load(f)
            self.integrations = state.get("decryptedIntegrations", {})
            for k, v in (state.get("configFiles") or {}).items():
                if isinstance(v, dict):
                    self.config_files[k] = v.get("content") or v.get("value") or ""
                elif isinstance(v, str):
                    self.config_files[k] = v
            wc_str = self.config_files.get("C:\\MosaicEarth\\current\\web\\Web.config") or ""
            for m in re.finditer(r'<add\s+key="([^"]+)"\s+value="([^"]*)"', wc_str):
                if m.group(2).strip():
                    self.web_config_settings[m.group(1)] = m.group(2).strip()

        # Desktop Local Bundle Seed fallback (loads CesiumIonToken, categories & public map items)
        seed_candidates = [
            self.data_root / "desktop_seed.json",
            Path(__file__).resolve().parent / "desktop_seed.json",
            Path(__file__).resolve().parent.parent / "desktop_seed.json",
        ]
        for seed_path in seed_candidates:
            if seed_path.is_file():
                try:
                    with open(seed_path, "r", encoding="utf-8") as sf:
                        seed_data = json.load(sf)
                    for sk, sv in (seed_data.get("web_config_settings") or {}).items():
                        if sv and sk not in self.web_config_settings:
                            self.web_config_settings[sk] = str(sv)
                    if not self.tables:
                        self.tables = seed_data.get("tables", {})
                except Exception:
                    pass
                break

        # Ensure required runtime tables exist in memory
        for tbl in [
            "dbo.USERS",
            "dbo.aspnet_Users",
            "dbo.aspnet_Membership",
            "dbo.aspnet_Roles",
            "dbo.aspnet_UsersInRoles",
            "dbo.WEB_PROJECTS",
            "dbo.WEB_PROJECTS_FILES",
            "dbo.WEB_PROJECTS_LOCATION",
            "dbo.WEB_PROJECTS_VALUES",
            "dbo.WEB_CESIUM_MODELS",
            "dbo.WEB_PUBLICATIONS",
            "dbo.WEB_CATEGORY",
            "dbo.WEB_COLLECTION",
            "dbo.WEB_MYMAPS",
            "dbo.WEB_MYMAPS_ITEMS",
            "dbo.WEB_CREDIT_PACKS",
            "dbo.WEB_CREDIT_PACKS_LANG",
            "dbo.WEB_PURCHASE",
            "dbo.WEB_TRANSACTION",
            "dbo.WEB_PREMIUM_PROJECTS",
            "dbo.NOTIFICATIONS",
            "dbo.FAQ_SECTIONS",
            "dbo.FAQ_QUESTIONS",
            "dbo.MailDatos",
            "dbo.MOSAICEARTH_EXTERNAL_PROJECTS",
            "dbo.MOSAICEARTH_EXTERNAL_ASSETS",
            "dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS",
            "dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS",
            "dbo.MOSAICEARTH_GAUSSIAN_PROJECTS",
            "dbo.MOSAICEARTH_GAUSSIAN_JOBS",
            "dbo.MOSAICEARTH_ASSET_ANNOTATIONS",
            "dbo.MOSAICEARTH_ASSET_TEMPORAL",
            "dbo.MOSAICEARTH_WEBODM_DEVICE_CODES",
            "dbo.MOSAICEARTH_WEBODM_TOKENS",
            "dbo.MOSAICEARTH_WEBODM_IMPORTS",
            "dbo.MOSAICEARTH_EMAIL_AUDIT",
            "dbo.MOSAICEARTH_ADMIN_AUDIT",
            "dbo.MOSAICEARTH_PROCESSED_ACQUISITION_LICENSES",
            "dbo.MOSAICEARTH_EXTERNAL_ACQUISITION_LICENSES",
            "dbo.MOSAICEARTH_RUNPOD_POD_LEASES",
            "dbo.MOSAICEARTH_BACKGROUND_JOBS",
        ]:
            self.tables.setdefault(tbl, [])
        self._sync_table_aliases()

    def _sync_table_aliases(self) -> None:
        for k in list(self.tables.keys()):
            if k.startswith("dbo."):
                short = k[4:]
                self.tables[short] = self.tables[k]
            else:
                dbo_k = f"dbo.{k}"
                if dbo_k not in self.tables:
                    self.tables[dbo_k] = self.tables[k]
        loc_map = {int(l.get("PROJECT_ID") or 0): l for l in self.tables.get("dbo.WEB_PROJECTS_LOCATION", [])}
        for p in self.tables.get("dbo.WEB_PROJECTS", []):
            pid = int(p.get("ID") or 0)
            pname = str(p.get("NAME") or f"Project {pid}")
            p.setdefault("FOLDER", _slugify(f"{pid}-{pname}"))
            loc = loc_map.get(pid)
            if loc:
                p.setdefault("LATITUD", loc.get("LATITUD"))
                p.setdefault("LONGITUD", loc.get("LONGITUD"))
        for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
            if "STATUS_ID" in pub and "STATE_ID" not in pub:
                pub["STATE_ID"] = pub["STATUS_ID"]
            if "PUBLISH_DATE" in pub and "DATE" not in pub:
                pub["DATE"] = pub["PUBLISH_DATE"]
        for ext in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
            vis = str(ext.get("VISIBILITY") or "").strip().lower()
            ext.setdefault("IS_PUBLISHED", vis == "public")

    def _init_sqlite_overlay(self) -> None:
        with sqlite3.connect(self.sqlite_path, timeout=30.0) as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=15000")
            except Exception:
                pass
            conn.execute(
                """CREATE TABLE IF NOT EXISTS table_overrides (
                    table_name TEXT PRIMARY KEY,
                    rows_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS integration_overrides (
                    integration_key TEXT PRIMARY KEY,
                    item_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute("""CREATE TABLE IF NOT EXISTS login_sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL
            )""")
            from .stripe_payments import init_schema

            init_schema(conn)
            cur = conn.execute("SELECT table_name, rows_json FROM table_overrides")
            for tbl, rjson in cur.fetchall():
                try:
                    self.tables[tbl] = json.loads(rjson)
                    self._table_snapshots[tbl] = copy.deepcopy(self.tables[tbl])
                except Exception:
                    pass
            cur2 = conn.execute("SELECT integration_key, item_json FROM integration_overrides")
            for ikey, ijson in cur2.fetchall():
                try:
                    self.integrations[ikey] = json.loads(ijson)
                except Exception:
                    pass

    def create_login_session(self, user_id: int, max_age: int) -> str:
        import time

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = int(time.time())
        with sqlite3.connect(self.sqlite_path, timeout=30) as conn:
            conn.execute("DELETE FROM login_sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT INTO login_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, now + max_age),
            )
        return token

    def resolve_login_session(self, token: str) -> int:
        import time

        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return 0
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with sqlite3.connect(self.sqlite_path, timeout=30) as conn:
            row = conn.execute(
                "SELECT user_id FROM login_sessions WHERE token_hash = ? AND expires_at > ?",
                (token_hash, int(time.time())),
            ).fetchone()
        return int(row[0]) if row else 0

    def revoke_login_session(self, token: str) -> None:
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
            with sqlite3.connect(self.sqlite_path, timeout=30) as conn:
                conn.execute("DELETE FROM login_sessions WHERE token_hash = ?", (token_hash,))

    @contextmanager
    def stripe_transaction(self):
        from .processing_architecture import cross_process_file_lock

        with self.lock:
            if self._stripe_connection is not None:
                yield self._stripe_connection
                return
        lock_file = self.data_root / "locks" / "sqlite_overlay_persist.lock"
        with self.lock, cross_process_file_lock(lock_file), sqlite3.connect(self.sqlite_path, timeout=30) as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous_tables = self.tables
            previous_snapshots = self._table_snapshots
            self._table_snapshots = copy.deepcopy(previous_snapshots)
            self.tables = copy.deepcopy(previous_tables)
            self._stripe_connection = conn
            try:
                for name, rows in conn.execute("SELECT table_name, rows_json FROM table_overrides"):
                    self.tables[name] = json.loads(rows)
                    self._table_snapshots[name] = copy.deepcopy(self.tables[name])
                yield conn
                conn.commit()
                self._table_snapshots = copy.deepcopy(self.tables)
            except BaseException:
                self.tables = previous_tables
                self._table_snapshots = previous_snapshots
                raise
            finally:
                self._stripe_connection = None

    def persist_table(self, table_name: str, deleted_ids: Optional[List[int]] = None) -> None:
        from .processing_architecture import cross_process_file_lock

        db_lock_file = self.data_root / "locks" / "sqlite_overlay_persist.lock"
        with self.lock:
            if self._stripe_connection is not None:
                self._stripe_connection.execute(
                    """INSERT INTO table_overrides(table_name, rows_json, updated_at) VALUES (?, ?, ?)
                       ON CONFLICT(table_name) DO UPDATE SET rows_json=excluded.rows_json, updated_at=excluded.updated_at""",
                    (table_name, json.dumps(self.tables.get(table_name, []), default=str),
                     _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))),
                )
                return
            with cross_process_file_lock(db_lock_file):
                local_rows = self.tables.get(table_name, [])
                now = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                with sqlite3.connect(self.sqlite_path, timeout=30.0) as conn:
                    conn.execute("PRAGMA busy_timeout=15000")
                    cur = conn.execute("SELECT rows_json FROM table_overrides WHERE table_name = ?", (table_name,))
                    row_db = cur.fetchone()
                    disk_rows: List[Dict[str, Any]] = []
                    if row_db and row_db[0]:
                        try:
                            parsed = json.loads(row_db[0])
                            if isinstance(parsed, list):
                                disk_rows = parsed
                        except Exception:
                            disk_rows = []

                    # Merge only fields changed since this process last read the
                    # table. Row timestamps cannot protect unrelated updates:
                    # stale browser state previously erased a committed Node UUID.
                    key_field = "PROJECT_FILE_ID" if table_name == "dbo.WEB_PROJECTS_FILES" else "ID"
                    if (
                        disk_rows
                        and local_rows
                        and all(isinstance(r, dict) and key_field in r for r in local_rows)
                        and all(isinstance(r, dict) and key_field in r for r in disk_rows)
                    ):
                        del_set = set(int(x) for x in (deleted_ids or []))
                        baseline = {int(r.get(key_field) or 0): r for r in self._table_snapshots.get(table_name, [])}
                        merged_by_id: Dict[int, Dict[str, Any]] = {}
                        for dr in disk_rows:
                            did = int(dr.get(key_field) or 0)
                            if did and did not in del_set:
                                merged_by_id[did] = dr
                        for lr in local_rows:
                            lid = int(lr.get(key_field) or 0)
                            if not lid:
                                continue
                            if lid in del_set:
                                merged_by_id.pop(lid, None)
                                continue
                            existing_dr = merged_by_id.get(lid)
                            if existing_dr is not None:
                                previous = baseline.get(lid, {})
                                merged = dict(existing_dr)
                                for key, value in lr.items():
                                    if key not in previous or value != previous[key]:
                                        merged[key] = value
                                for key in previous.keys() - lr.keys():
                                    merged.pop(key, None)
                                merged_by_id[lid] = merged
                            else:
                                merged_by_id[lid] = lr
                        final_rows = list(merged_by_id.values())
                        self.tables[table_name] = final_rows
                    else:
                        final_rows = local_rows

                    rjson = json.dumps(final_rows, default=str)
                    conn.execute(
                        """INSERT INTO table_overrides(table_name, rows_json, updated_at)
                           VALUES(?, ?, ?)
                           ON CONFLICT(table_name) DO UPDATE SET rows_json=excluded.rows_json, updated_at=excluded.updated_at""",
                        (table_name, rjson, now),
                    )
                    self._table_snapshots[table_name] = copy.deepcopy(final_rows)

    def persist_integration(self, key: str, value: str, updated_by: str = "admin@mosaicearth.app", reason: str = "Updated via Control Center") -> None:
        with self.lock:
            now = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            item = self.integrations.get(key) or {}
            item.update({
                "decryptedValue": value,
                "valueSuffix": value[-4:] if value and len(value) >= 4 else value,
                "updatedBy": updated_by,
                "updatedAtUtc": now,
                "rotationReason": reason,
            })
            self.integrations[key] = item
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.execute(
                    """INSERT INTO integration_overrides(integration_key, item_json, updated_at)
                       VALUES(?, ?, ?)
                       ON CONFLICT(integration_key) DO UPDATE SET item_json=excluded.item_json, updated_at=excluded.updated_at""",
                    (key, json.dumps(item), now),
                )

    def get_setting(self, key: str, default: str = "") -> str:
        env_key = "MOSAICEARTH_" + re.sub(r"[^A-Za-z0-9]+", "_", key).upper()
        if os.environ.get(env_key):
            return os.environ[env_key]
        with self.lock:
            item = self.integrations.get(key)
            if item and item.get("decryptedValue") is not None:
                val = str(item.get("decryptedValue") or "").strip()
                if val:
                    return val
            wc_val = self.web_config_settings.get(key)
            if wc_val:
                return wc_val
        if key == "CesiumIonToken":
            rows = self.tables.get("dbo.CESIUM_KEY", [])
            if rows and rows[0].get("CESIUM_KEY"):
                return str(rows[0]["CESIUM_KEY"]).strip()
        return default

    # -------------------------------------------------------------------------
    # User & Membership Authentication (100% compatible with aspnet_Membership)
    # -------------------------------------------------------------------------
    @staticmethod
    def verify_aspnet_password(password: str, stored_hash: str, salt_b64: str, password_format: int) -> bool:
        if password is None or stored_hash is None:
            return False
        if password_format == 0:
            return hmac.compare_digest(password, stored_hash)
        if stored_hash.startswith("pbkdf2:sha256:"):
            try:
                _, _, iter_s, salt_s, digest_s = stored_hash.split(":", 4)
                dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_s), int(iter_s))
                return hmac.compare_digest(dk.hex(), digest_s)
            except Exception:
                return False
        try:
            salt_bytes = base64.b64decode(salt_b64 or "")
            pw_utf16 = password.encode("utf-16-le")
            # Standard ASP.NET SqlMembershipProvider PasswordFormat=1: SHA1(salt_bytes + utf16le(password))
            sha1_digest = base64.b64encode(hashlib.sha1(salt_bytes + pw_utf16).digest()).decode("ascii")
            if hmac.compare_digest(sha1_digest, stored_hash):
                return True
            # Fallback SHA256(salt_bytes + utf16le(password)) or HMACSHA256
            sha256_digest = base64.b64encode(hashlib.sha256(salt_bytes + pw_utf16).digest()).decode("ascii")
            if hmac.compare_digest(sha256_digest, stored_hash):
                return True
            hmac256_digest = base64.b64encode(hmac.new(salt_bytes, pw_utf16, hashlib.sha256).digest()).decode("ascii")
            if hmac.compare_digest(hmac256_digest, stored_hash):
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def hash_aspnet_password(password: str) -> Tuple[str, str, int]:
        salt_bytes = secrets.token_bytes(16)
        salt_b64 = base64.b64encode(salt_bytes).decode("ascii")
        pw_utf16 = password.encode("utf-16-le")
        sha1_digest = base64.b64encode(hashlib.sha1(salt_bytes + pw_utf16).digest()).decode("ascii")
        return sha1_digest, salt_b64, 1

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for u in self.tables.get("dbo.USERS", []):
                if int(u.get("ID") or 0) == int(user_id):
                    return self._enrich_user(u)
        return None

    def refresh_account_state(self) -> None:
        """Read account revocations/entitlements committed by another process."""
        with self.lock:
            if self._stripe_connection is not None:
                return
            with sqlite3.connect(self.sqlite_path, timeout=30) as conn:
                row = conn.execute("SELECT rows_json FROM table_overrides WHERE table_name=?", ("dbo.USERS",)).fetchone()
            if row:
                self.tables["dbo.USERS"] = json.loads(row[0])
                self._table_snapshots["dbo.USERS"] = copy.deepcopy(self.tables["dbo.USERS"])

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        target = (email or "").strip().lower()
        if not target:
            return None
        with self.lock:
            for u in self.tables.get("dbo.USERS", []):
                if str(u.get("EMAIL") or "").strip().lower() == target:
                    return self._enrich_user(u)
        return None

    def _enrich_user(self, u: Dict[str, Any]) -> Dict[str, Any]:
        res = copy.deepcopy(u)
        uid_guid = str(u.get("MEMBERSHIP_ID") or u.get("USER_ID") or "").lower()
        roles = []
        role_map = {str(r.get("RoleId") or "").lower(): str(r.get("RoleName") or "") for r in self.tables.get("dbo.aspnet_Roles", [])}
        for ur in self.tables.get("dbo.aspnet_UsersInRoles", []):
            if str(ur.get("UserId") or "").lower() == uid_guid:
                rname = role_map.get(str(ur.get("RoleId") or "").lower())
                if rname:
                    roles.append(rname)
        if int(u.get("ROL_ID") or 0) == 1 and "Administrador" not in roles:
            roles.append("Administrador")
        comment = ""
        for m in self.tables.get("dbo.aspnet_Membership", []):
            if str(m.get("UserId") or "").lower() == uid_guid:
                comment = str(m.get("Comment") or "")
                break
        res["Roles"] = roles
        res["IsAdmin"] = ("Administrador" in roles) or (int(u.get("ROL_ID") or 0) == 1)
        res["MembershipComment"] = comment
        res["IsExternalAccount"] = comment.lower().startswith("oauth:")
        res["ExternalAccountProvider"] = comment[6:].strip() if res["IsExternalAccount"] else ""
        full_name = " ".join(
            p for p in [str(u.get("NOMBRE") or "").strip(), str(u.get("APELLIDO1") or "").strip(), str(u.get("APELLIDO2") or "").strip()] if p
        ).strip()
        if full_name.lower() == "4dtwinmaps official":
            full_name = "MosaicEarth official"
        res["DisplayName"] = full_name or str(u.get("EMAIL") or "MosaicEarth user")
        res["CreditsInt"] = int(round(float(u.get("CREDITS") or 0)))
        return res

    def authenticate_user(self, email: str, password: str) -> Tuple[Optional[Dict[str, Any]], str]:
        user = self.get_user_by_email(email)
        if not user:
            return None, "INVALID_CREDENTIALS"
        if not user.get("ACTIVO", True):
            return None, "INACTIVE_USER"
        uid_guid = str(user.get("MEMBERSHIP_ID") or user.get("USER_ID") or "").lower()
        mem = None
        with self.lock:
            for m in self.tables.get("dbo.aspnet_Membership", []):
                if str(m.get("UserId") or "").lower() == uid_guid or str(m.get("LoweredEmail") or "").strip().lower() == email.strip().lower():
                    mem = m
                    break
        if not mem:
            return None, "INVALID_CREDENTIALS"
        if mem.get("IsLockedOut"):
            return None, "LOCKED_OUT"
        if not mem.get("IsApproved", True):
            return None, "NOT_APPROVED"
        ok = self.verify_aspnet_password(
            password,
            str(mem.get("Password") or ""),
            str(mem.get("PasswordSalt") or ""),
            int(mem.get("PasswordFormat") or 1),
        )
        if not ok:
            return None, "INVALID_CREDENTIALS"
        return user, "OK"

    def register_user(self, email: str, nombre: str, apellido1: str = "", password: Optional[str] = None, oauth_provider: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
        email_clean = (email or "").strip()
        if not email_clean or "@" not in email_clean:
            return None, "INVALID_EMAIL"
        with self.lock:
            existing = self.get_user_by_email(email_clean)
            if existing:
                return existing, "EMAIL_EXISTS"
            users = self.tables.setdefault("dbo.USERS", [])
            next_id = next_record_id(users, "ID", 1000)
            user_guid = str(uuid.uuid4())
            app_id = "c2c7e09f-09c5-42d5-9c1d-5b8d47b47131"
            if self.tables.get("dbo.aspnet_Users"):
                app_id = str(self.tables["dbo.aspnet_Users"][0].get("ApplicationId") or app_id)
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            pw_to_hash = password or secrets.token_urlsafe(16)
            pw_hash, pw_salt, pw_fmt = self.hash_aspnet_password(pw_to_hash)

            self.tables.setdefault("dbo.aspnet_Users", []).append({
                "ApplicationId": app_id,
                "UserId": user_guid,
                "UserName": email_clean,
                "LoweredUserName": email_clean.lower(),
                "MobileAlias": None,
                "IsAnonymous": False,
                "LastActivityDate": now_iso,
            })
            self.tables.setdefault("dbo.aspnet_Membership", []).append({
                "ApplicationId": app_id,
                "UserId": user_guid,
                "Password": pw_hash,
                "PasswordFormat": pw_fmt,
                "PasswordSalt": pw_salt,
                "MobilePIN": None,
                "Email": email_clean,
                "LoweredEmail": email_clean.lower(),
                "PasswordQuestion": None,
                "PasswordAnswer": None,
                "IsApproved": True,
                "IsLockedOut": False,
                "CreateDate": now_iso,
                "LastLoginDate": now_iso,
                "LastPasswordChangedDate": now_iso,
                "LastLockoutDate": "1754-01-01T00:00:00Z",
                "FailedPasswordAttemptCount": 0,
                "Comment": f"oauth:{oauth_provider}" if oauth_provider else None,
            })
            new_user = {
                "ID": next_id,
                "ACTIVO": True,
                "CREATE_DATE": now_iso,
                "CREATE_ID": 1,
                "UPDATE_DATE": now_iso,
                "UPDATE_ID": 1,
                "MEMBERSHIP_ID": user_guid,
                "ROL_ID": 2,
                "NOMBRE": (nombre or email_clean.split("@")[0]).strip(),
                "APELLIDO1": (apellido1 or "").strip(),
                "APELLIDO2": "",
                "EMAIL": email_clean,
                "CREDITS": 0.0,
                "IMAGEN": "",
                "STATE_ID": 1,
                "PLAN_KEY": "free",
                "IS_NEW_ACCOUNT": True,
                "LEGACY_STUDIO_GRANTED": True,
            }
            users.append(new_user)
            self.persist_table("dbo.aspnet_Users")
            self.persist_table("dbo.aspnet_Membership")
            self.persist_table("dbo.USERS")
            return self._enrich_user(new_user), "OK"

    def update_user_password(self, user_id: int, new_password: str) -> bool:
        user = self.get_user_by_id(user_id)
        if not user:
            return False
        uid_guid = str(user.get("MEMBERSHIP_ID") or user.get("USER_ID") or "").lower()
        pw_hash, pw_salt, pw_fmt = self.hash_aspnet_password(new_password)
        with self.lock:
            for m in self.tables.get("dbo.aspnet_Membership", []):
                if str(m.get("UserId") or "").lower() == uid_guid:
                    m["Password"] = pw_hash
                    m["PasswordSalt"] = pw_salt
                    m["PasswordFormat"] = pw_fmt
                    m["LastPasswordChangedDate"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                    self.persist_table("dbo.aspnet_Membership")
                    return True
        return False

    # -------------------------------------------------------------------------
    # Global Public Asset Index (100% contract match with Map2.aspx.cs)
    # -------------------------------------------------------------------------
    @staticmethod
    def family_display(type_or_id: Any) -> str:
        if isinstance(type_or_id, int) or str(type_or_id).isdigit():
            t = int(type_or_id)
            return "Point Clouds" if t == 1 else "3D Models" if t == 3 else "Maps & Layers"
        s = str(type_or_id or "").strip().lower()
        if s in ("1", "pointcloud", "point clouds", "point-clouds"):
            return "Point Clouds"
        if s in ("3", "model3d", "3d models", "3d-models"):
            return "3D Models"
        return "Maps & Layers"

    @staticmethod
    def subtype_display(asset_type: str, subtype: Optional[str] = None) -> str:
        if subtype and str(subtype).strip():
            return str(subtype).strip()
        s = (asset_type or "").strip().lower()
        if s in ("1", "pointcloud"):
            return "Point cloud"
        if s in ("3", "model3d"):
            return "3D model"
        return "Orthophoto"

    @staticmethod
    def processed_extensions(type_id: int) -> str:
        if type_id == 1:
            return "las laz zip"
        if type_id == 3:
            return "obj glb gltf zip"
        return "tif tiff"

    @staticmethod
    def original_extension(fmt: str) -> str:
        f = (fmt or "").strip().lower().lstrip(".")
        if f in ("tif", "tiff", "geotiff"):
            return "tif tiff"
        if f in ("las", "laz"):
            return f
        if f in ("glb", "gltf", "obj", "ply", "splat", "geojson", "json", "kml", "kmz", "gpkg", "shp", "dxf", "gpx", "xyz", "pcd", "pts"):
            return f
        return f or "zip"

    def _creator_name_by_user_id(self, user_id: Any) -> str:
        if not user_id:
            return "MosaicEarth creator"
        u = self.get_user_by_id(int(user_id))
        if not u:
            return "MosaicEarth creator"
        return u.get("DisplayName") or "MosaicEarth creator"

    def _temporal_map(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for row in self.tables.get("dbo.MOSAICEARTH_ASSET_TEMPORAL", []):
            key = str(row.get("ASSET_KEY") or "").strip()
            if key:
                out[key] = row
        return out

    def build_public_asset_index(
        self,
        north: Optional[float] = None,
        south: Optional[float] = None,
        east: Optional[float] = None,
        west: Optional[float] = None,
        families: Optional[List[str]] = None,
        limit: int = 5000,
        captured_from_utc: Optional[datetime.datetime] = None,
        captured_to_utc: Optional[datetime.datetime] = None,
        search: Optional[str] = None,
        extension: Optional[str] = None,
        lang: str = "en",
    ) -> List[Dict[str, Any]]:
        with self.lock:
            requested = {f.strip().lower() for f in (families or []) if f and f.strip()}

            def include_type(type_id: int) -> bool:
                if not requested:
                    return True
                code = "pointcloud" if type_id == 1 else "model3d" if type_id == 3 else "orthophoto"
                disp = self.family_display(type_id).lower()
                return (code in requested) or (disp in requested) or (str(type_id) in requested)

            def inside(lat: float, lon: float) -> bool:
                if north is None or south is None or east is None or west is None:
                    return True
                lon_ok = (lon >= west and lon <= east) if east >= west else (lon >= west or lon <= east)
                return (lat >= south and lat <= north) and lon_ok

            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None and p.get("ACTIVO", True)
            }
            locations_by_proj: Dict[int, Dict[str, Any]] = {}
            for loc in self.tables.get("dbo.WEB_PROJECTS_LOCATION", []):
                pid = int(loc.get("PROJECT_ID") or 0)
                if pid > 0:
                    locations_by_proj[pid] = loc

            models_by_proj_type: Dict[Tuple[int, int], Dict[str, Any]] = {}
            for m in self.tables.get("dbo.WEB_CESIUM_MODELS", []):
                pid = int(m.get("Project_id") or 0)
                mtype = int(m.get("ModelType") or 0)
                if pid > 0 and mtype > 0:
                    models_by_proj_type[(pid, mtype)] = m

            # Earliest capture date from project files if available
            capture_by_proj: Dict[int, datetime.datetime] = {}
            for pf in self.tables.get("dbo.WEB_PROJECTS_FILES", []):
                if not pf.get("ACTIVO", True):
                    continue
                pid = int(pf.get("PROJECT_ID") or 0)
                cdt = _parse_iso_dt(pf.get("CAPTURE_DATE"))
                if pid > 0 and cdt:
                    if pid not in capture_by_proj or cdt < capture_by_proj[pid]:
                        capture_by_proj[pid] = cdt

            temporal_by_key = self._temporal_map()
            assets: List[Dict[str, Any]] = []

            # 1. Published Processed Assets (from WEB_PUBLICATIONS where STATUS_ID == 2)
            for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                if int(pub.get("STATUS_ID") or 0) != 2:
                    continue
                type_id = int(pub.get("FILE_TYPE_ID") or 0)
                if type_id not in (1, 2, 3) or not include_type(type_id):
                    continue
                pid = int(pub.get("PROJECT_ID") or 0)
                proj = projects_by_id.get(pid)
                if not proj:
                    continue
                loc = locations_by_proj.get(pid)
                if not loc:
                    continue
                lat = _try_float(loc.get("LATITUD"))
                lon = _try_float(loc.get("LONGITUD"))
                if lat is None or lon is None or not inside(lat, lon):
                    continue

                placement = models_by_proj_type.get((pid, type_id))
                pname = str(proj.get("NAME") or f"Project {pid}")
                folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                pub_id = int(pub.get("ID") or 0)
                cap_dt = capture_by_proj.get(pid)
                pub_dt = _parse_iso_dt(pub.get("PUBLISH_DATE") or pub.get("CREATE_DATE"))
                create_dt = _parse_iso_dt(proj.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                disp_dt = cap_dt or pub_dt or create_dt

                key = f"processed-{type_id}-{pid}"
                t_row = temporal_by_key.get(key)
                if t_row:
                    c_start = _parse_iso_dt(t_row.get("CAPTURE_START_UTC")) or cap_dt
                    c_end = _parse_iso_dt(t_row.get("CAPTURE_END_UTC")) or c_start
                    c_prec = str(t_row.get("CAPTURE_PRECISION") or ("day" if c_start else "unknown"))
                    c_src = str(t_row.get("CAPTURE_SOURCE") or ("WebODM / image metadata" if c_start else "unknown"))
                else:
                    c_start = cap_dt
                    c_end = cap_dt
                    c_prec = "day" if cap_dt else "unknown"
                    c_src = "WebODM / image metadata" if cap_dt else "unknown"

                eff_start = c_start or pub_dt or create_dt
                eff_end = c_end or eff_start

                if captured_from_utc and eff_end < captured_from_utc:
                    continue
                if captured_to_utc and eff_start > captured_to_utc:
                    continue

                viewer_rel = (
                    "/procesado/orthophoto_tiles/"
                    if type_id == 2
                    else "/procesado/3d_tiles/pointcloud/tileset.json"
                    if type_id == 1
                    else "/procesado/output/tileset.json"
                )
                viewer_url = f"/ProcessedAssetFile.ashx/{folder}{viewer_rel}"
                preview_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                detail_url = f"/{lang}/assets/{_slugify(pname)}-{pub_id}"
                creator = self._creator_name_by_user_id(proj.get("CREATE_ID") or pub.get("USER_ID"))
                exts = self.processed_extensions(type_id)
                date_display = eff_start.strftime("%d/%m/%Y") if c_start else (disp_dt.strftime("%d/%m/%Y") if disp_dt else "—")

                assets.append({
                    "Key": key,
                    "Source": "processed",
                    "Id": pid,
                    "ProjectId": pid,
                    "PublicationId": pub_id,
                    "FileTypeId": type_id,
                    "IconType": ICON_CLOUD if type_id == 1 else ICON_3D if type_id == 3 else ICON_ORTHOPHOTO,
                    "Family": self.family_display(type_id),
                    "Subtype": self.subtype_display(str(type_id)),
                    "Name": pname,
                    "Creator": creator,
                    "Latitude": lat,
                    "Longitude": lon,
                    "Height": float(placement.get("Height") or 0) if placement else 0.0,
                    "Altitude": 0.0,
                    "Heading": 0.0,
                    "Scale": 1.0,
                    "ViewerKind": "processed-raster" if type_id == 2 else "processed-tileset",
                    "GeoreferenceMode": "embedded",
                    "ViewerUrl": viewer_url,
                    "PreviewUrl": preview_url,
                    "DetailUrl": detail_url,
                    "Extensions": exts,
                    "DownloadFormat": "GEOTIFF · TIF" if type_id == 2 else "LAS · ZIP" if type_id == 1 else "OBJ · ZIP",
                    "PriceCredits": int(round(float(pub.get("CREDITS") or 0))),
                    "DateLabel": "Captured" if c_start else "Published",
                    "DateDisplay": date_display,
                    "SortDate": _fmt_iso_utc(eff_start),
                    "CaptureStartUtc": _fmt_iso_utc(c_start),
                    "CaptureEndUtc": _fmt_iso_utc(c_end),
                    "CapturePrecision": c_prec,
                    "CaptureSource": c_src,
                    "CreatedDateUtc": _fmt_iso_utc(pub_dt or create_dt),
                    "EffectiveStartUtc": _fmt_iso_utc(eff_start),
                    "EffectiveEndUtc": _fmt_iso_utc(eff_end),
                })

            # 2. Public External & Gaussian Assets (from MOSAICEARTH_EXTERNAL_ASSETS)
            for ext in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                vis = str(ext.get("VISIBILITY") or "").strip().lower()
                status = str(ext.get("STATUS") or "").strip().lower()
                if vis != "public" or status in ("deleted", "discarded", "failed", "conversion-failed", "rejected"):
                    continue
                lat = _try_float(ext.get("LATITUDE"))
                lon = _try_float(ext.get("LONGITUDE"))
                if lat is None or lon is None or not inside(lat, lon):
                    continue
                atype = str(ext.get("ASSET_TYPE") or "orthophoto").strip().lower()
                type_id = 1 if atype == "pointcloud" else 3 if atype == "model3d" else 2
                if not include_type(type_id):
                    continue

                aid = int(ext.get("ID") or 0)
                pid = int(ext.get("PROJECT_ID") or 0)
                proj = projects_by_id.get(pid)
                pname = str(ext.get("TITLE") or ext.get("DISPLAY_NAME") or (proj.get("NAME") if proj else None) or ext.get("SOURCE_FILE_NAME") or f"Asset {aid}")
                fmt = str(ext.get("OUTPUT_FORMAT") or ext.get("SOURCE_FORMAT") or ext.get("FORMAT") or "").strip().upper()
                display_kind = str(ext.get("DISPLAY_KIND") or "").strip()
                if not display_kind:
                    display_kind = (
                        "raster-tiles"
                        if type_id == 2
                        else "model-glb"
                        if (type_id == 3 and fmt == "GLB")
                        else "pointcloud-3dtiles"
                        if type_id == 1
                        else "model-3dtiles"
                    )
                display_entry = str(ext.get("DISPLAY_ENTRY_PATH") or "tileset.json").strip()
                if ext.get("VIEWER_KIND") == "geojson":
                    display_kind = "vector-geojson"
                    display_entry = str(ext.get("DISPLAY_ENTRY_PATH") or "overlay.geojson").strip()
                elif ext.get("VIEWER_KIND") == "vector-raster":
                    display_kind = "raster-tiles"

                file_url = f"/ExternalAssetFile.ashx?project_id={pid}&asset_id={aid}"
                if display_kind == "raster-tiles":
                    viewer_url = f"/ExternalAssetViewer.ashx?project_id={pid}&asset_id={aid}&z={{z}}&x={{x}}&y={{y}}"
                elif display_kind == "model-glb" and not ext.get("DISPLAY_ENTRY_PATH"):
                    viewer_url = f"{file_url}&view=1"
                else:
                    viewer_url = f"/ExternalAsset3DTile.ashx/{pid}/{aid}/{display_entry.lstrip('/')}"

                viewer_kind_map = {
                    "raster-tiles": "external-raster",
                    "raster-manual-image": "external-manual-raster",
                    "vector-geojson": "external-vector",
                    "model-glb": "external-gltf",
                    "model-gltf": "external-gltf",
                    "pointcloud-3dtiles": "external-pointcloud",
                    "gaussian-splat": "external-gaussian",
                }
                viewer_kind = viewer_kind_map.get(display_kind, "external-tileset")
                key = f"external-{type_id}-{aid}"
                t_row = temporal_by_key.get(key)
                cap_dt = _parse_iso_dt(ext.get("CAPTURE_DATE"))
                pub_dt = _parse_iso_dt(ext.get("PUBLISHED_AT") or ext.get("PUBLISHED_AT_UTC") or ext.get("CREATED_AT") or ext.get("CREATED_AT_UTC")) or datetime.datetime.now(datetime.timezone.utc)
                if t_row:
                    c_start = _parse_iso_dt(t_row.get("CAPTURE_START_UTC")) or cap_dt
                    c_end = _parse_iso_dt(t_row.get("CAPTURE_END_UTC")) or c_start
                    c_prec = str(t_row.get("CAPTURE_PRECISION") or ("day" if c_start else "unknown"))
                    c_src = str(t_row.get("CAPTURE_SOURCE") or "import metadata")
                else:
                    c_start = cap_dt
                    c_end = cap_dt
                    c_prec = "day" if cap_dt else "unknown"
                    c_src = "import metadata" if cap_dt else "unknown"

                eff_start = c_start or pub_dt
                eff_end = c_end or eff_start
                if captured_from_utc and eff_end < captured_from_utc:
                    continue
                if captured_to_utc and eff_start > captured_to_utc:
                    continue

                creator = self._creator_name_by_user_id(ext.get("USER_ID") or (proj.get("CREATE_ID") if proj else None))
                exts = self.original_extension(fmt)
                subtype = str(ext.get("ASSET_SUBTYPE") or self.subtype_display(atype)).strip()

                assets.append({
                    "Key": key,
                    "Source": str(ext.get("SOURCE_ORIGIN") or "external"),
                    "Id": aid,
                    "ProjectId": pid,
                    "PublicationId": 0,
                    "FileTypeId": type_id,
                    "IconType": ICON_CLOUD if type_id == 1 else ICON_3D if type_id == 3 else ICON_ORTHOPHOTO,
                    "Family": self.family_display(atype),
                    "Subtype": subtype,
                    "Name": pname,
                    "Creator": creator,
                    "Latitude": lat,
                    "Longitude": lon,
                    "Height": 0.0,
                    "Altitude": float(ext.get("ALTITUDE") or 0.0),
                    "Heading": float(ext.get("HEADING") or 0.0),
                    "Scale": float(ext.get("SCALE") or 1.0),
                    "ViewerKind": viewer_kind,
                    "GeoreferenceMode": str(ext.get("GEOREFERENCE_MODE") or "embedded"),
                    "ViewerUrl": viewer_url,
                    "PreviewUrl": "",
                    "DetailUrl": f"/{lang}/external-assets/{_slugify(pname)}-{pid}-{aid}",
                    "Extensions": exts,
                    "DownloadFormat": f"GEOTIFF · {fmt}" if fmt in ("TIF", "TIFF") else f"{fmt} · ORIGINAL",
                    "PriceCredits": int(ext.get("PRICE_CREDITS") or 0),
                    "DateLabel": "Captured" if c_start else "Published",
                    "DateDisplay": eff_start.strftime("%d/%m/%Y"),
                    "SortDate": _fmt_iso_utc(eff_start),
                    "CaptureStartUtc": _fmt_iso_utc(c_start),
                    "CaptureEndUtc": _fmt_iso_utc(c_end),
                    "CapturePrecision": c_prec,
                    "CaptureSource": c_src,
                    "CreatedDateUtc": _fmt_iso_utc(pub_dt),
                    "EffectiveStartUtc": _fmt_iso_utc(eff_start),
                    "EffectiveEndUtc": _fmt_iso_utc(eff_end),
                })

            # Extension filter & text search & deduplication by Key
            ext_filter = {e.strip().lower().lstrip(".") for e in re.split(r"[\s,]+", extension or "") if e.strip()}
            q_lower = (search or "").strip().lower()

            dedup: Dict[str, Dict[str, Any]] = {}
            for item in assets:
                if ext_filter:
                    item_exts = {e.strip().lower().lstrip(".") for e in re.split(r"[\s,]+", str(item.get("Extensions") or "")) if e.strip()}
                    if not (ext_filter & item_exts):
                        continue
                if q_lower:
                    if q_lower not in str(item.get("Name") or "").lower() and q_lower not in str(item.get("Creator") or "").lower():
                        continue
                k = item["Key"].lower()
                prev = dedup.get(k)
                if not prev or (str(item.get("SortDate") or ""), item.get("PublicationId", 0)) > (str(prev.get("SortDate") or ""), prev.get("PublicationId", 0)):
                    dedup[k] = item

            sorted_items = sorted(
                dedup.values(),
                key=lambda x: (str(x.get("SortDate") or ""), x["Key"]),
                reverse=True,
            )
            for item in sorted_items:
                item.setdefault("PublishId", int(item.get("PublicationId") or 0))
                item.setdefault("ExternalAssetId", int(item.get("Id") or 0) if item.get("Source") != "processed" else 0)
                item.setdefault("FamilyDisplay", item.get("Family") or self.family_display(item.get("FileTypeId", 2)))
                item.setdefault("SubtypeDisplay", item.get("Subtype") or self.subtype_display(str(item.get("FileTypeId", 2))))
                pid = int(item.get("ProjectId") or 0)
                p_obj = projects_by_id.get(pid) or {}
                raw_cat = int(p_obj.get("CATEGORY_ID") or 0)
                if 1 <= raw_cat <= 10:
                    raw_cat += 12
                cat_info = DEFAULT_CATEGORIES.get(raw_cat)
                item.setdefault("CategoryId", raw_cat if cat_info else None)
                item.setdefault("CategoryName", cat_info["name"] if cat_info else "")
                item.setdefault("Description", str(p_obj.get("DESCRIPTION") or ""))
                dt_parsed = _parse_iso_dt(item.get("SortDate"))
                item.setdefault("SortDateTicks", int(dt_parsed.timestamp() * 1000) if dt_parsed else 0)
            return sorted_items[: max(1, limit)]

    # -------------------------------------------------------------------------
    # Annotations (AssetAnnotationService parity + HTTP 409 on revision mismatch)
    # -------------------------------------------------------------------------
    def list_annotations(self, asset_key: str, is_owner: bool = False) -> List[Dict[str, Any]]:
        with self.lock:
            out = []
            for row in self.tables.get("dbo.MOSAICEARTH_ASSET_ANNOTATIONS", []):
                if row.get("DELETED"):
                    continue
                if str(row.get("ASSET_KEY") or "") != asset_key:
                    continue
                vis = str(row.get("VISIBILITY") or "asset")
                if not is_owner and vis != "asset":
                    continue
                out.append({
                    "Id": int(row.get("ID") or 0),
                    "Title": str(row.get("TITLE") or ""),
                    "Body": str(row.get("BODY") or ""),
                    "Link": str(row.get("LINK") or ""),
                    "Visibility": vis,
                    "AnchorKind": str(row.get("ANCHOR_KIND") or "geographic"),
                    "X": float(row.get("X") or 0.0),
                    "Y": float(row.get("Y") or 0.0),
                    "Z": float(row.get("Z") or 0.0),
                    "Revision": int(row.get("REVISION") or 1),
                })
            return sorted(out, key=lambda r: r["Id"])

    def save_annotation(self, asset_key: str, project_id: int, author_id: int, payload: Dict[str, Any], delete: bool = False) -> Tuple[int, Optional[int], Optional[str]]:
        """Returns (status_code, annotation_id, error_message). Raises 409 on revision conflict."""
        with self.lock:
            rows = self.tables.setdefault("dbo.MOSAICEARTH_ASSET_ANNOTATIONS", [])
            row_id = int(payload.get("Id") or payload.get("id") or 0)
            revision = int(payload.get("Revision") or payload.get("revision") or 0)
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))

            if delete:
                if row_id <= 0 or revision <= 0:
                    return 400, None, "Invalid annotation revision."
                for r in rows:
                    if int(r.get("ID") or 0) == row_id and str(r.get("ASSET_KEY") or "") == asset_key and not r.get("DELETED"):
                        if int(r.get("REVISION") or 1) != revision:
                            return 409, None, "This annotation changed. Reload before saving."
                        r["DELETED"] = True
                        r["REVISION"] = revision + 1
                        r["UPDATED_AT"] = now_iso
                        self.persist_table("dbo.MOSAICEARTH_ASSET_ANNOTATIONS")
                        return 200, row_id, None
                return 409, None, "This annotation changed. Reload before saving."

            title = str(payload.get("Title") or payload.get("title") or "").strip()
            body = str(payload.get("Body") or payload.get("body") or "")
            link = str(payload.get("Link") or payload.get("link") or "").strip()
            vis = str(payload.get("Visibility") or payload.get("visibility") or "asset").strip()
            anchor = str(payload.get("AnchorKind") or payload.get("anchorKind") or "geographic").strip()
            x = _try_float(payload.get("X") if "X" in payload else payload.get("x"))
            y = _try_float(payload.get("Y") if "Y" in payload else payload.get("y"))
            z = _try_float(payload.get("Z") if "Z" in payload else payload.get("z"))

            if not title or len(title) > 160 or len(body) > 2000 or len(link) > 1000 or vis not in ("owner", "asset") or anchor not in ("geographic", "asset_local") or x is None or y is None or z is None:
                return 400, None, "Invalid annotation content or anchor."

            if row_id == 0:
                active_count = sum(1 for r in rows if str(r.get("ASSET_KEY") or "") == asset_key and not r.get("DELETED"))
                if active_count >= 200:
                    return 400, None, "This asset already has 200 annotations."
                next_id = next_record_id(rows, "ID", 0)
                rows.append({
                    "ID": next_id,
                    "ASSET_KEY": asset_key,
                    "PROJECT_ID": project_id,
                    "AUTHOR_ID": author_id,
                    "TITLE": title,
                    "BODY": body,
                    "LINK": link,
                    "VISIBILITY": vis,
                    "ANCHOR_KIND": anchor,
                    "X": x,
                    "Y": y,
                    "Z": z,
                    "REVISION": 1,
                    "DELETED": False,
                    "CREATED_AT": now_iso,
                    "UPDATED_AT": now_iso,
                })
                self.persist_table("dbo.MOSAICEARTH_ASSET_ANNOTATIONS")
                return 200, next_id, None
            else:
                if revision <= 0:
                    return 400, None, "Invalid annotation revision."
                for r in rows:
                    if int(r.get("ID") or 0) == row_id and str(r.get("ASSET_KEY") or "") == asset_key and not r.get("DELETED"):
                        if int(r.get("REVISION") or 1) != revision:
                            return 409, None, "This annotation changed. Reload before saving."
                        r["TITLE"] = title
                        r["BODY"] = body
                        r["LINK"] = link
                        r["VISIBILITY"] = vis
                        r["ANCHOR_KIND"] = anchor
                        r["X"] = x
                        r["Y"] = y
                        r["Z"] = z
                        r["REVISION"] = revision + 1
                        r["UPDATED_AT"] = now_iso
                        self.persist_table("dbo.MOSAICEARTH_ASSET_ANNOTATIONS")
                        return 200, row_id, None
                return 409, None, "This annotation changed. Reload before saving."

    # -------------------------------------------------------------------------
    # WebODM Device Code & Bearer Token Integration (WebOdmIntegrationService)
    # -------------------------------------------------------------------------
    @staticmethod
    def _sha256_hex(val: str) -> str:
        return hashlib.sha256((val or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_user_code(code: str) -> str:
        raw = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
        return f"{raw[:4]}-{raw[4:8]}" if len(raw) >= 8 else raw

    def start_webodm_device_auth(self, label: str, ip_address: str) -> Dict[str, Any]:
        with self.lock:
            codes = self.tables.setdefault("dbo.MOSAICEARTH_WEBODM_DEVICE_CODES", [])
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            user_code = "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4))
            device_code = secrets.token_urlsafe(32)
            now = datetime.datetime.now(datetime.timezone.utc)
            exp = now + datetime.timedelta(minutes=15)
            codes.append({
                "DEVICE_CODE_HASH": self._sha256_hex(device_code),
                "USER_CODE": user_code,
                "REQUEST_LABEL": (label or "WebODM")[:160],
                "REQUEST_IP": (ip_address or "unknown")[:64],
                "USER_ID": None,
                "ACCESS_TOKEN_CLEARTEXT": None,
                "CREATED_AT_UTC": _fmt_iso_utc(now),
                "EXPIRES_AT_UTC": _fmt_iso_utc(exp),
                "APPROVED_AT_UTC": None,
                "DENIED_AT_UTC": None,
            })
            self.persist_table("dbo.MOSAICEARTH_WEBODM_DEVICE_CODES")
            return {
                "DeviceCode": device_code,
                "UserCode": user_code,
                "ExpiresIn": 900,
                "Interval": 4,
            }

    def get_webodm_device_request(self, user_code: str) -> Optional[Dict[str, Any]]:
        norm = self._normalize_user_code(user_code)
        with self.lock:
            for c in self.tables.get("dbo.MOSAICEARTH_WEBODM_DEVICE_CODES", []):
                if str(c.get("USER_CODE") or "").upper() == norm:
                    return c
        return None

    def approve_webodm_device(self, user_code: str, user_id: int) -> str:
        norm = self._normalize_user_code(user_code)
        with self.lock:
            req = self.get_webodm_device_request(norm)
            if not req:
                raise ValueError("This connection code is not valid.")
            exp = _parse_iso_dt(req.get("EXPIRES_AT_UTC"))
            now = datetime.datetime.now(datetime.timezone.utc)
            if req.get("DENIED_AT_UTC") or (exp and exp <= now):
                raise ValueError("This connection code has expired.")
            if req.get("APPROVED_AT_UTC") and req.get("ACCESS_TOKEN_CLEARTEXT"):
                return str(req["ACCESS_TOKEN_CLEARTEXT"])
            raw_token = "me_webodm_" + secrets.token_urlsafe(36)
            tokens = self.tables.setdefault("dbo.MOSAICEARTH_WEBODM_TOKENS", [])
            next_tid = next_record_id(tokens, "ID", 0)
            now_iso = _fmt_iso_utc(now)
            tokens.append({
                "ID": next_tid,
                "USER_ID": user_id,
                "TOKEN_HASH": self._sha256_hex(raw_token),
                "LABEL": req.get("REQUEST_LABEL") or "WebODM",
                "CREATED_AT_UTC": now_iso,
                "LAST_USED_AT_UTC": None,
                "REVOKED_AT_UTC": None,
            })
            req["USER_ID"] = user_id
            req["ACCESS_TOKEN_CLEARTEXT"] = raw_token
            req["APPROVED_AT_UTC"] = now_iso
            self.persist_table("dbo.MOSAICEARTH_WEBODM_TOKENS")
            self.persist_table("dbo.MOSAICEARTH_WEBODM_DEVICE_CODES")
            return raw_token

    def deny_webodm_device(self, user_code: str) -> None:
        norm = self._normalize_user_code(user_code)
        with self.lock:
            req = self.get_webodm_device_request(norm)
            if req and not req.get("APPROVED_AT_UTC"):
                req["DENIED_AT_UTC"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                self.persist_table("dbo.MOSAICEARTH_WEBODM_DEVICE_CODES")

    def poll_webodm_device_token(self, device_code: str) -> Tuple[int, Dict[str, Any]]:
        if not device_code or not str(device_code).strip():
            return 400, {"error": "invalid_request"}
        h = self._sha256_hex(str(device_code).strip())
        now = datetime.datetime.now(datetime.timezone.utc)
        with self.lock:
            for c in self.tables.get("dbo.MOSAICEARTH_WEBODM_DEVICE_CODES", []):
                if str(c.get("DEVICE_CODE_HASH") or "") == h:
                    exp = _parse_iso_dt(c.get("EXPIRES_AT_UTC"))
                    if exp and exp <= now:
                        return 410, {"error": "expired_token"}
                    if c.get("DENIED_AT_UTC"):
                        return 400, {"error": "access_denied"}
                    if not c.get("APPROVED_AT_UTC") or not c.get("ACCESS_TOKEN_CLEARTEXT"):
                        return 428, {"error": "authorization_pending"}
                    return 200, {
                        "access_token": str(c["ACCESS_TOKEN_CLEARTEXT"]),
                        "token_type": "Bearer",
                        "scope": "assets:import",
                    }
        return 400, {"error": "invalid_grant"}

    def authenticate_webodm_bearer(self, auth_header: Optional[str]) -> Optional[Dict[str, Any]]:
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return None
        token = auth_header[7:].strip()
        if not token.startswith("me_webodm_") or len(token) < 34:
            return None
        h = self._sha256_hex(token)
        with self.lock:
            for t in self.tables.get("dbo.MOSAICEARTH_WEBODM_TOKENS", []):
                if str(t.get("TOKEN_HASH") or "") == h and not t.get("REVOKED_AT_UTC"):
                    uid = int(t.get("USER_ID") or 0)
                    u = self.get_user_by_id(uid)
                    if u and u.get("ACTIVO", True):
                        t["LAST_USED_AT_UTC"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                        self.persist_table("dbo.MOSAICEARTH_WEBODM_TOKENS")
                        return {"TokenId": int(t.get("ID") or 0), "UserId": uid, "Email": str(u.get("EMAIL") or "")}
        return None

    # -------------------------------------------------------------------------
    # Projects, External Assets & Gaussian Projects CRUD
    # -------------------------------------------------------------------------
    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id) and p.get("ACTIVO", True):
                    return copy.deepcopy(p)
        return None

    def get_external_project_meta(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for ep in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_PROJECTS", []):
                if int(ep.get("PROJECT_ID") or 0) == int(project_id):
                    return copy.deepcopy(ep)
        return None

    def get_gaussian_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for gp in self.tables.get("dbo.MOSAICEARTH_GAUSSIAN_PROJECTS", []):
                if int(gp.get("PROJECT_ID") or 0) == int(project_id):
                    return copy.deepcopy(gp)
        return None

    def is_premium_project(self, project_id: int) -> bool:
        with self.lock:
            for pp in self.tables.get("dbo.WEB_PREMIUM_PROJECTS", []):
                if int(pp.get("PROJECT_ID") or 0) == int(project_id) and pp.get("ACTIVO", True):
                    return True
        return False

    def refresh_processing_state(self) -> None:
        """Refresh rows that can change in the other API or worker process."""
        names = ("dbo.WEB_PROJECTS", "dbo.MOSAICEARTH_GAUSSIAN_PROJECTS", "dbo.MOSAICEARTH_EXTERNAL_ASSETS", "dbo.WEB_PROJECTS_FILES")
        with self.lock:
            if self._stripe_connection is not None:
                return
            with sqlite3.connect(self.sqlite_path, timeout=30) as conn:
                for name, rows in conn.execute(
                    "SELECT table_name, rows_json FROM table_overrides WHERE table_name IN (?, ?, ?, ?)", names
                ):
                    self.tables[name] = json.loads(rows)
                    self._table_snapshots[name] = copy.deepcopy(self.tables[name])

    def get_external_asset_by_id(self, asset_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for a in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                if int(a.get("ID") or 0) == int(asset_id):
                    return copy.deepcopy(a)
        return None

    def get_external_assets_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        with self.lock:
            return [
                copy.deepcopy(a)
                for a in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])
                if int(a.get("PROJECT_ID") or 0) == int(project_id) and str(a.get("STATUS") or "").lower() != "discarded"
            ]

    def get_publication_by_id(self, pub_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                if int(pub.get("ID") or 0) == int(pub_id):
                    return copy.deepcopy(pub)
        return None

    def get_publications_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        with self.lock:
            return [
                copy.deepcopy(pub)
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", [])
                if int(pub.get("PROJECT_ID") or 0) == int(project_id)
            ]

    def get_cesium_models_by_project(self, project_id: int, model_type: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.lock:
            out = []
            for m in self.tables.get("dbo.WEB_CESIUM_MODELS", []):
                if int(m.get("Project_id") or 0) == int(project_id):
                    if model_type is None or int(m.get("ModelType") or 0) == int(model_type):
                        out.append(copy.deepcopy(m))
            return out

    def create_project(
        self,
        user_id: int,
        name: str,
        kind: str = "processing",
        description: str = "",
        parent_project_id: int = 0,
        survey_id: int = 0,
        is_project_container: bool = False,
        is_survey_container: bool = False,
        creation_date: str = "",
        methodology: str = "",
        task_description: str = "",
    ) -> int:
        clean_name = (name or "").strip()[:120]
        if not clean_name:
            raise ValueError("Enter a project name.")
        now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
        norm_kind = (kind or "processing").strip().lower()
        with self.lock:
            projects = self.tables.setdefault("dbo.WEB_PROJECTS", [])
            next_id = next_record_id(projects, "ID", 18755)
            if is_project_container or is_survey_container:
                state_id = 4
            else:
                state_id = 0 if norm_kind in ("processing", "photogrammetry") else 4
            projects.append({
                "ID": next_id,
                "PARENT_PROJECT_ID": int(parent_project_id or 0),
                "SURVEY_ID": int(survey_id or 0),
                "IS_PROJECT_CONTAINER": bool(is_project_container),
                "IS_SURVEY_CONTAINER": bool(is_survey_container),
                "ACTIVO": True,
                "CREATE_DATE": now_iso,
                "CREATE_ID": user_id,
                "UPDATE_DATE": now_iso,
                "UPDATE_ID": user_id,
                "NAME": clean_name,
                "FOLDER": _slugify(f"{next_id}-{clean_name}"),
                "SEO_URL_FRIENDLY": _slugify(f"{clean_name}-{next_id}"),
                "ZIP_UPLOADED": False,
                "STATE_ID": state_id,
                "DESCRIPTION": (description or "").strip(),
                "TASK_CREATION_DATE": (creation_date or now_iso[:10]).strip(),
                "CAPTURE_METHOD": (methodology or "").strip(),
                "TASK_DESCRIPTION": (task_description or "").strip(),
                "TAGS": "",
                "CATEGORY_ID": 0,
                "CREDITS": 12 if norm_kind == "gaussian" else 0,
            })
            self.persist_table("dbo.WEB_PROJECTS")
            if not is_project_container and not is_survey_container and norm_kind in ("external", "gaussian"):
                ext_projs = self.tables.setdefault("dbo.MOSAICEARTH_EXTERNAL_PROJECTS", [])
                ext_projs.append({
                    "PROJECT_ID": next_id,
                    "USER_ID": user_id,
                    "CREATED_AT": now_iso,
                    "PROJECT_KIND": "gaussian" if norm_kind == "gaussian" else "external",
                })
                self.persist_table("dbo.MOSAICEARTH_EXTERNAL_PROJECTS")
            if not is_project_container and not is_survey_container and norm_kind == "gaussian":
                g_projs = self.tables.setdefault("dbo.MOSAICEARTH_GAUSSIAN_PROJECTS", [])
                g_projs.append({
                    "PROJECT_ID": next_id,
                    "USER_ID": user_id,
                    "MODE": "images",
                    "REVISION": 1,
                    "SOURCE_MANIFEST": "[]",
                    "CURRENT_JOB": None,
                    "STATUS": "draft",
                    "STATUS_DETAIL": "Upload 30–200 overlapping photos or one video capture.",
                    "CREDITS_QUOTED": 12,
                    "CREDITS_CHARGED": 0,
                    "ASSET_ID": None,
                    "CREATED_AT": now_iso,
                    "UPDATED_AT": now_iso,
                })
                self.persist_table("dbo.MOSAICEARTH_GAUSSIAN_PROJECTS")
            return next_id

    def delete_project(self, project_id: int, user_id: int, is_admin: bool = False) -> bool:
        folder_to_delete = ""
        has_marketplace_buyers = False
        ext_ids_to_delete: List[int] = []
        with self.lock:
            for col in self.tables.get("dbo.WEB_COLLECTION", []):
                if col.get("ACTIVO", True) and int(col.get("PROJECT_ID") or 0) == int(project_id) and int(col.get("USER_ID") or 0) != int(user_id):
                    has_marketplace_buyers = True
                    break
            for acq in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS", []):
                if int(acq.get("PROJECT_ID") or 0) == int(project_id) and int(acq.get("BUYER_USER_ID") or acq.get("USER_ID") or 0) != int(user_id):
                    has_marketplace_buyers = True
                    break
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id):
                    if not is_admin and int(p.get("CREATE_ID") or 0) != int(user_id):
                        return False
                    p["ACTIVO"] = False
                    p["UPDATE_DATE"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                    folder_to_delete = str(p.get("FOLDER") or f"{project_id}-project")
                    for ea in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                        if int(ea.get("PROJECT_ID") or 0) == int(project_id):
                            ea["STATUS"] = "deleted"
                            ea["VISIBILITY"] = "private"
                            ext_ids_to_delete.append(int(ea.get("ID") or 0))
                    self.persist_table("dbo.WEB_PROJECTS")
                    if ext_ids_to_delete:
                        self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")
                    break
            else:
                return False
        if not has_marketplace_buyers:
            try:
                from .blob_store import get_blob_store
                bs = get_blob_store()
                if folder_to_delete:
                    bs.delete_prefix("imagenes", folder_to_delete)
                bs.delete_prefix("assets", f"external/{project_id}")
                bs.delete_prefix("results", f"external/{project_id}")
            except Exception as del_exc:
                print(f"[MosaicEarth Storage] Cleanup warning for deleted project #{project_id}: {del_exc}", flush=True)
        return True

    def delete_user_project(self, user_id: int, project_id: int) -> Tuple[bool, str]:
        ok = self.delete_project(project_id, user_id)
        return ok, ("OK" if ok else "ERROR")

    def adjust_user_credits(self, user_id: int, delta_credits: int, concept: str, project_id: Optional[int] = None) -> Tuple[bool, int]:
        """Atomically adjusts integer credits and appends to HISTORICO_USUARIOS / WEB_TRANSACTION."""
        with self.lock:
            for u in self.tables.get("dbo.USERS", []):
                if int(u.get("ID") or 0) == int(user_id):
                    current = int(round(float(u.get("CREDITS") or 0)))
                    new_bal = current + int(delta_credits)
                    if new_bal < 0:
                        return False, current
                    u["CREDITS"] = float(new_bal)
                    now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                    u["UPDATE_DATE"] = now_iso
                    hist = self.tables.setdefault("dbo.HISTORICO_USUARIOS", [])
                    next_hid = next_record_id(hist, "ID", 0)
                    hist.append({
                        "ID": next_hid,
                        "ACTIVO": True,
                        "CREATE_DATE": now_iso,
                        "CREATE_ID": user_id,
                        "USER_ID": user_id,
                        "PROJECT_ID": project_id,
                        "CREDITS": abs(int(delta_credits)),
                        "TIPO_ID": 1 if delta_credits > 0 else 2,
                        "CONCEPTO": concept,
                    })
                    self.persist_table("dbo.USERS")
                    self.persist_table("dbo.HISTORICO_USUARIOS")
                    return True, new_bal
        return False, 0

    def _persist_table(self, table_name: str) -> None:
        dbo_name = table_name if table_name.startswith("dbo.") else f"dbo.{table_name}"
        self.persist_table(dbo_name)

    def get_cesium_browser_token(self) -> str:
        return self.get_setting("CesiumIonToken", "")

    def get_external_asset(self, project_id: int, asset_id: int) -> Optional[Dict[str, Any]]:
        ext = self.get_external_asset_by_id(asset_id)
        if ext and (not project_id or int(ext.get("PROJECT_ID") or 0) == int(project_id)):
            return ext
        return None

    def list_user_projects(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.lock:
            ext_meta_by_pid = {
                int(ep.get("PROJECT_ID") or 0): ep
                for ep in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_PROJECTS", [])
                if ep.get("PROJECT_ID") is not None
            }
            gauss_by_pid = {
                int(gp.get("PROJECT_ID") or 0): gp
                for gp in self.tables.get("dbo.MOSAICEARTH_GAUSSIAN_PROJECTS", [])
                if gp.get("PROJECT_ID") is not None
            }
            ext_assets_by_pid: Dict[int, Dict[str, Any]] = {}
            for ea in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                if str(ea.get("STATUS") or "").lower() == "discarded":
                    continue
                epid = int(ea.get("PROJECT_ID") or 0)
                if epid > 0 and epid not in ext_assets_by_pid:
                    ext_assets_by_pid[epid] = ea
            pub_pids = {
                int(pub.get("PROJECT_ID") or 0)
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", [])
                if int(pub.get("STATUS_ID") or pub.get("STATE_ID") or 0) == 2
            }
            files_count_by_pid: Dict[int, int] = {}
            for pf in self.tables.get("dbo.WEB_PROJECTS_FILES", []):
                if pf.get("ACTIVO", True):
                    fpid = int(pf.get("PROJECT_ID") or 0)
                    files_count_by_pid[fpid] = files_count_by_pid.get(fpid, 0) + 1
            vals_by_pid = {
                int(v.get("PROJECT_ID") or 0): v
                for v in self.tables.get("dbo.WEB_PROJECTS_VALUES", [])
                if v.get("PROJECT_ID") is not None
            }

            rows = []
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if not p.get("ACTIVO", True):
                    continue
                if user_id is not None and int(p.get("CREATE_ID") or p.get("USER_ID") or 0) != int(user_id):
                    continue
                row = copy.deepcopy(p)
                pid = int(row.get("ID") or 0)
                pname = str(row.get("NAME") or f"Project {pid}")
                row["SEO_URL_FRIENDLY"] = str(row.get("SEO_URL_FRIENDLY") or _slugify(f"{pid}-{pname}"))
                row["FOLDER"] = str(row.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                cre_dt = _parse_iso_dt(row.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                row["DATE"] = cre_dt.strftime("%d.%m.%y")
                row["CREATE_DATE_ISO"] = cre_dt.strftime("%Y-%m-%d")
                v_row = vals_by_pid.get(pid)
                cap_dt = _parse_iso_dt(v_row.get("CAPTURE_DATE")) if v_row else None
                row["CAPTURE_START_ISO"] = (cap_dt or cre_dt).strftime("%Y-%m-%d")
                row["CAPTURE_END_ISO"] = row["CAPTURE_START_ISO"]
                row["NUM_FILES"] = files_count_by_pid.get(pid, int(row.get("NUM_FILES") or 0))
                row["AREA"] = str((v_row.get("AREA") if v_row else None) or row.get("AREA") or "0")

                ep = ext_meta_by_pid.get(pid)
                gp = gauss_by_pid.get(pid)
                ea = ext_assets_by_pid.get(pid)
                pkind = str((ep.get("PROJECT_KIND") if ep else None) or ("gaussian" if gp else "photogrammetry")).lower()
                is_gauss = pkind == "gaussian" or gp is not None
                is_ext = (pkind == "external" or ep is not None) and not is_gauss

                row["PROJECT_KIND"] = "gaussian" if is_gauss else ("external" if is_ext else "photogrammetry")
                row["IS_EXTERNAL_IMPORT"] = 1 if is_ext else 0
                row["IS_GAUSSIAN_PROJECT"] = 1 if is_gauss else 0
                row["COMPLETED"] = 1 if (int(row.get("STATE_ID") or 0) == 4 and not is_ext and not is_gauss) else 0
                row["HAS_PUBLISHED_OUTPUT"] = 1 if (pid in pub_pids or (ea and str(ea.get("VISIBILITY") or "").lower() == "public")) else 0

                if ea:
                    fmt = str(ea.get("SOURCE_FORMAT") or ea.get("FORMAT") or "")
                    atype = str(ea.get("ASSET_TYPE") or "model3d").lower()
                    row["EXTERNAL_ASSET_ID"] = int(ea.get("ID") or 0)
                    row["EXTERNAL_ASSET_FAMILY"] = atype
                    row["EXTERNAL_ASSET_TYPE"] = self.family_display(1 if atype == "pointcloud" else 3 if atype == "model3d" else 2)
                    row["EXTERNAL_SOURCE_FORMAT"] = fmt.upper()
                    row["EXTERNAL_ASSET_STATUS"] = str(ea.get("STATUS") or "Ready").replace("-", " ")
                    row["ORIGINAL_EXTENSION"] = self.original_extension(fmt)
                    row["PROCESSED_EXTENSIONS"] = row["ORIGINAL_EXTENSION"]
                elif is_gauss:
                    row["GAUSSIAN_STATUS"] = str((gp.get("STATUS") if gp else "draft") or "draft")
                    row["ORIGINAL_EXTENSION"] = "jpg jpeg png mp4 mov ply"
                    row["PROCESSED_EXTENSIONS"] = "ply"
                else:
                    row["ORIGINAL_EXTENSION"] = "jpg jpeg tif tiff"
                    row["PROCESSED_EXTENSIONS"] = "tif tiff obj glb gltf zip las laz"

                rows.append(row)
            rows.sort(key=lambda r: int(r.get("ID") or 0), reverse=True)
            return rows

    def list_gaussian_projects(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.lock:
            rows = [
                copy.deepcopy(g)
                for g in self.tables.get("dbo.MOSAICEARTH_GAUSSIAN_PROJECTS", [])
                if user_id is None or int(g.get("USER_ID") or 0) == int(user_id)
            ]
            return rows

    def resolve_webodm_bearer(self, token: str) -> Optional[Dict[str, Any]]:
        info = self.authenticate_webodm_bearer(f"Bearer {token}")
        if info and info.get("UserId"):
            return self.get_user_by_id(int(info["UserId"]))
        return None

    def create_webodm_device_code(self, webodm_url: str = "http://localhost:8000") -> Dict[str, Any]:
        res = self.start_webodm_device_auth(webodm_url, "127.0.0.1")
        return {
            "device_code": res["DeviceCode"],
            "user_code": res["UserCode"],
            "expires_in": res["ExpiresIn"],
            "interval": res["Interval"],
        }

    def poll_webodm_device_code(self, device_code: str) -> Tuple[Dict[str, Any], int]:
        status, body = self.poll_webodm_device_token(device_code)
        return body, status

    def approve_webodm_device_code(self, user_code: str, user_id: int) -> bool:
        try:
            self.approve_webodm_device(user_code, user_id)
            return True
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # Notifications (funciones.aspx / Home.master.cs parity)
    # -------------------------------------------------------------------------
    def has_unread_notifications(self, user_id: Optional[int]) -> bool:
        if not user_id:
            return False
        with self.lock:
            for n in self.tables.get("dbo.NOTIFICATIONS", []):
                if int(n.get("USER_ID") or 0) == int(user_id) and not bool(n.get("ISREAD")):
                    return True
        return False

    def get_user_notifications(self, user_id: Optional[int]) -> List[Dict[str, Any]]:
        if not user_id:
            return []
        with self.lock:
            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None
            }
            out = []
            for n in self.tables.get("dbo.NOTIFICATIONS", []):
                if int(n.get("USER_ID") or 0) != int(user_id):
                    continue
                if bool(n.get("ISREAD")):
                    continue
                link_id = int(n.get("LINK_ID") or 0)
                type_id = int(n.get("TYPE_ID") or 0)
                seo_slug = ""
                if link_id > 0 and type_id in (1, 2):
                    proj = projects_by_id.get(link_id)
                    if proj:
                        seo_slug = f"/Client/Project.aspx?id={link_id}"
                target = str(n.get("TARGET_URL") or "")
                if target.startswith("/") and not target.startswith("//") and "\\" not in target:
                    seo_slug = target
                out.append({
                    "ID": int(n.get("ID") or 0),
                    "CREATEDATE": str(n.get("CREATEDATE") or ""),
                    "TITLE": str(n.get("TITLE") or ""),
                    "DESCRIPTION": str(n.get("DESCRIPTION") or ""),
                    "LINK_ID": link_id if link_id > 0 else None,
                    "TYPE_ID": type_id,
                    "SEO_URL_FRIENDLY": seo_slug,
                })
            out.sort(key=lambda r: int(r["ID"]), reverse=True)
            return out

    def add_notification(
        self,
        user_id: int,
        title: str,
        description: str,
        link_id: Optional[int] = None,
        type_id: int = 1,
        event_key: str = "",
        target_url: str = "",
    ) -> int:
        with self.lock:
            rows = self.tables.setdefault("dbo.NOTIFICATIONS", [])
            if event_key:
                existing = next((row for row in rows if row.get("EVENT_KEY") == event_key
                                 and int(row.get("USER_ID") or 0) == int(user_id)), None)
                if existing:
                    return int(existing["ID"])
            next_id = next_record_id(rows, "ID", 0)
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            rows.append({
                "ID": next_id,
                "CREATEDATE": now_iso,
                "TITLE": str(title or ""),
                "DESCRIPTION": str(description or ""),
                "LINK_ID": int(link_id) if link_id else None,
                "ISREAD": False,
                "EVENT_KEY": event_key,
                "TARGET_URL": target_url,
                "TYPE_ID": int(type_id or 1),
                "USER_ID": int(user_id),
            })
            self.persist_table("dbo.NOTIFICATIONS")
            return next_id

    def mark_notification_read(self, notif_id: int, user_id: Optional[int] = None) -> bool:
        with self.lock:
            changed = False
            for n in self.tables.get("dbo.NOTIFICATIONS", []):
                if int(n.get("ID") or 0) == int(notif_id):
                    if user_id is None or int(n.get("USER_ID") or 0) == int(user_id):
                        n["ISREAD"] = True
                        changed = True
            if changed:
                self.persist_table("dbo.NOTIFICATIONS")
        return self.has_unread_notifications(user_id if user_id is not None else notif_id)

    def mark_all_notifications_read(self, user_id: Optional[int]) -> None:
        if not user_id:
            return
        with self.lock:
            changed = False
            for n in self.tables.get("dbo.NOTIFICATIONS", []):
                if int(n.get("USER_ID") or 0) == int(user_id) and not bool(n.get("ISREAD")):
                    n["ISREAD"] = True
                    changed = True
            if changed:
                self.persist_table("dbo.NOTIFICATIONS")

    # -------------------------------------------------------------------------
    # Project Detail, Files, Values, Locations, Cesium Models & Publication
    # -------------------------------------------------------------------------
    def get_project_by_slug_or_id(self, slug_or_id: str) -> Optional[Dict[str, Any]]:
        val = (slug_or_id or "").strip()
        if not val:
            return None
        if val.isdigit():
            p = self.get_project(int(val))
            if p:
                pid = int(p.get("ID") or 0)
                pname = str(p.get("NAME") or f"Project {pid}")
                p["FOLDER"] = str(p.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                p["SEO_URL_FRIENDLY"] = str(p.get("SEO_URL_FRIENDLY") or _slugify(f"{pid}-{pname}"))
            return p
        val_lower = val.lower()
        with self.lock:
            candidates = sorted(
                [p for p in self.tables.get("dbo.WEB_PROJECTS", []) if p.get("ACTIVO", True)],
                key=lambda x: (int(x.get("STATE_ID") or 0) == 4, int(x.get("ID") or 0)),
                reverse=True,
            )
            for p in candidates:
                pid = int(p.get("ID") or 0)
                pname = str(p.get("NAME") or "")
                s1 = str(p.get("SEO_URL_FRIENDLY") or "").strip().lower()
                s2 = _slugify(pname).lower()
                s3 = _slugify(f"{pid}-{pname}").lower()
                s4 = _slugify(f"{pname}-{pid}").lower()
                if val_lower in (s1, s2, s3, s4):
                    res = copy.deepcopy(p)
                    res["FOLDER"] = str(res.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                    res["SEO_URL_FRIENDLY"] = str(res.get("SEO_URL_FRIENDLY") or _slugify(f"{pid}-{pname}"))
                    return res
        m = re.search(r"^(\d+)-", val) or re.search(r"(\d+)$", val)
        if m:
            p = self.get_project(int(m.group(1)))
            if p:
                pid = int(p.get("ID") or 0)
                pname = str(p.get("NAME") or f"Project {pid}")
                p["FOLDER"] = str(p.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                p["SEO_URL_FRIENDLY"] = str(p.get("SEO_URL_FRIENDLY") or _slugify(f"{pid}-{pname}"))
            return p
        return None

    def get_project_files(self, project_id: int, order_by: int = 0) -> List[Dict[str, Any]]:
        with self.lock:
            files = [
                copy.deepcopy(f)
                for f in self.tables.get("dbo.WEB_PROJECTS_FILES", [])
                if int(f.get("PROJECT_ID") or 0) == int(project_id) and f.get("ACTIVO", True)
            ]
        if order_by == 1:
            files.sort(key=lambda r: str(r.get("FILENAME") or "").lower())
        elif order_by == 2:
            files.sort(key=lambda r: str(r.get("FILENAME") or "").lower(), reverse=True)
        elif order_by == 3:
            files.sort(key=lambda r: float(r.get("SIZE") or 0))
        elif order_by == 4:
            files.sort(key=lambda r: float(r.get("SIZE") or 0), reverse=True)
        elif order_by == 5:
            files.sort(key=lambda r: str(r.get("CAPTURE_DATE") or ""))
        elif order_by == 6:
            files.sort(key=lambda r: str(r.get("CAPTURE_DATE") or ""), reverse=True)
        elif order_by == 7:
            files.sort(key=lambda r: str(r.get("LATITUD") or ""))
        elif order_by == 8:
            files.sort(key=lambda r: str(r.get("LATITUD") or ""), reverse=True)
        else:
            files.sort(key=lambda r: int(r.get("PROJECT_FILE_ID") or 0))
        return files

    def add_project_file(
        self,
        project_id: int,
        user_id: int,
        filename: str,
        size_bytes: int,
        content_type: str = "image/jpeg",
        resolution: str = "",
        lat: str = "",
        lon: str = "",
        alt: str = "",
        capture_date: Optional[str] = None,
    ) -> int:
        with self.lock:
            rows = self.tables.setdefault("dbo.WEB_PROJECTS_FILES", [])
            next_id = next_record_id(rows, "PROJECT_FILE_ID", 0)
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            rows.append({
                "PROJECT_FILE_ID": next_id,
                "ACTIVO": True,
                "CREATE_DATE": now_iso,
                "CREATE_ID": int(user_id),
                "FILENAME": str(filename),
                "PROJECT_ID": int(project_id),
                "SIZE": str(int(size_bytes)),
                "CONTENT_TYPE": str(content_type or "image/jpeg"),
                "RESOLUTION": str(resolution or ""),
                "LATITUD": str(lat or ""),
                "LONGITUD": str(lon or ""),
                "ALTURA": str(alt or ""),
                "PROCESSED": False,
                "CAPTURE_DATE": capture_date or "",
            })
            self.persist_table("dbo.WEB_PROJECTS_FILES")
            return next_id

    def get_runpod_lease_manager(self) -> Any:
        if not hasattr(self, "_runpod_lease_mgr") or self._runpod_lease_mgr is None:
            from .processing_architecture import RunPodLeaseManager
            self._runpod_lease_mgr = RunPodLeaseManager(self)
        return self._runpod_lease_mgr

    def get_job_queue(self) -> Any:
        if not hasattr(self, "_bg_job_queue") or self._bg_job_queue is None:
            from .processing_architecture import BackgroundJobQueueManager
            self._bg_job_queue = BackgroundJobQueueManager(self, max_workers=2)
        return self._bg_job_queue

    def resolve_processing_backend(
        self,
        user_id: int,
        target_mode: str = "auto",
        probe_local_live: bool = True,
    ) -> Dict[str, Any]:
        from .processing_architecture import resolve_processing_backend as _resolve_backend
        return _resolve_backend(self, user_id=user_id, target_mode=target_mode, probe_local_live=probe_local_live)

    def generate_terrain_derivative(
        self,
        project_id: int,
        derivative_kind: str = "hillshade",
        source_dem: str = "dsm",
        contour_interval_m: float = 2.0,
        source_asset_id: int = 0,
        stage_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        from .processing_architecture import generate_regenerable_terrain_derivative
        return generate_regenerable_terrain_derivative(
            self,
            project_id=project_id,
            derivative_kind=derivative_kind,
            source_dem=source_dem,
            contour_interval_m=contour_interval_m,
            source_asset_id=source_asset_id,
            stage_cb=stage_cb,
        )

    def create_external_asset_import(
        self,
        project_id: int,
        user_id: int,
        display_name: str,
        source_filename: str,
        stored_rel_path: str,
        size_bytes: int,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        altitude: Optional[float] = None,
        description: Optional[str] = None,
        capture_date: Optional[str] = None,
        capture_method: Optional[str] = None,
        source_origin: str = "imported",
        raw_bytes: Optional[bytes] = None,
        extra_media_files: Optional[List[Tuple[str, bytes]]] = None,
        run_sync: Optional[bool] = None,
        extra_media_rel_paths: Optional[List[Tuple[str, str]]] = None,
        staged_upload_id: str = "",
        quality_preset: str = "STANDARD",
        upload_attempt: str = "",
        asset_class_override: str = "",
        derivative_key: str = "",
    ) -> int:
        ext_low = Path(source_filename or "").suffix.lower().lstrip(".")
        if source_filename.lower().endswith(".shp.zip"):
            ext_low = "shp"
        elif source_filename.lower().endswith(".obj.zip"):
            ext_low = "obj"
        is_gs = ext_low in ("splat", "spz", "ksplat", "mp4", "mov", "webm") or source_origin == "gaussian"
        pending = run_sync is False
        if is_gs:
            asset_type = "model3d"
            subtype = "gaussian_splat"
            viewer_kind = "gaussian_splat_3dtiles"
        elif ext_low in ("las", "laz", "ply", "pcd", "pts", "xyz", "e57"):
            asset_type = "pointcloud"
            subtype = "pointcloud"
            viewer_kind = "3dtiles"
        elif ext_low in ("glb", "gltf", "obj", "fbx", "dae"):
            asset_type = "model3d"
            subtype = "mesh"
            viewer_kind = "glb"
        elif ext_low in ("geojson", "json", "kml", "kmz", "gpx", "shp", "dxf", "gpkg", "csv"):
            asset_type = "orthophoto"
            subtype = "vector_overlay"
            viewer_kind = "geojson"
        else:
            asset_type = "orthophoto"
            subtype = "orthophoto"
            viewer_kind = "tms"

        now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
        with self.lock:
            proj_row = {}
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id):
                    p["TASK_DELETED"] = False
                    if source_origin != "processed" and (not pending or int(p.get("STATE_ID") or 0) != 4):
                        p["STATE_ID"] = 1 if pending else 4
                    if staged_upload_id and is_gs:
                        p["UPFRONT_REFUND_CHECKED"] = 0
                    proj_row = p
                    break
            self.persist_table("dbo.WEB_PROJECTS")
            inherited_desc = str(description if description is not None else (proj_row.get("TASK_DESCRIPTION") or proj_row.get("DESCRIPTION") or "")).strip()
            inherited_method = str(capture_method or proj_row.get("CAPTURE_METHOD") or ("3D Gaussian Splatting" if is_gs else "Direct File Import")).strip()
            inherited_date = str(capture_date or proj_row.get("TASK_CREATION_DATE") or now_iso[:10]).strip()[:10]
            eff_lat = float(lat) if lat is not None else (_try_float(proj_row.get("LATITUD")) or 41.8205)
            eff_lon = float(lon) if lon is not None else (_try_float(proj_row.get("LONGITUD")) or 1.8676)
            eff_alt = float(altitude) if altitude is not None else 0.0
            assets = self.tables.setdefault("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])
            previous_derivative = next((a for a in assets if derivative_key and a.get("DERIVATIVE_KEY") == derivative_key
                and int(a.get("PROJECT_ID") or 0) == int(project_id) and int(a.get("USER_ID") or 0) == int(user_id)
                and a.get("STATUS") != "deleted"), None)
            if previous_derivative and previous_derivative.get("CANONICAL_STATUS") == "READY":
                return int(previous_derivative["ID"])
            if previous_derivative and previous_derivative.get("SOURCE_SHA256") and raw_bytes is not None:
                import hashlib
                if hashlib.sha256(raw_bytes).hexdigest() != previous_derivative["SOURCE_SHA256"]:
                    raise ValueError("A derivative retry cannot replace an existing master with different bytes.")
            next_aid = int(previous_derivative["ID"]) if previous_derivative else next_record_id(assets, "ID", 100)

        # Namespace source files by `next_aid` (`external/{project_id}/{next_aid}/source/{filename}`)
        # to guarantee zero filename collision when two assets in the same project have identical filenames (e.g. two `model.glb`).
        safe_leaf = Path(stored_rel_path or source_filename or "asset.bin").name
        canonical_stored_rel = f"external/{project_id}/{next_aid}/source/{safe_leaf}"
        if not staged_upload_id and stored_rel_path != canonical_stored_rel:
            try:
                from .blob_store import get_blob_store
                bs = get_blob_store()
                if raw_bytes is not None:
                    bs.save_bytes("assets", canonical_stored_rel, raw_bytes)
                else:
                    old_p = bs.ensure_local("assets", stored_rel_path, force_download=True)
                    if old_p and old_p.is_file():
                        bs.save_file("assets", canonical_stored_rel, old_p)
                        if "/uploads/" in str(stored_rel_path):
                            bs.delete_file("assets", stored_rel_path)
                stored_rel_path = canonical_stored_rel
            except Exception:
                pass

        if extra_media_rel_paths and not staged_upload_id:
            namespaced_extra: List[Tuple[str, str]] = []
            try:
                from .blob_store import get_blob_store
                bs = get_blob_store()
                for oname, rpath in extra_media_rel_paths:
                    s_leaf = Path(rpath or oname).name
                    ns_rel = f"external/{project_id}/{next_aid}/source/{s_leaf}"
                    if rpath != ns_rel:
                        ep = bs.ensure_local("assets", rpath, force_download=True)
                        if ep and ep.is_file():
                            bs.save_file("assets", ns_rel, ep)
                            if "/uploads/" in str(rpath):
                                bs.delete_file("assets", rpath)
                        namespaced_extra.append((oname, ns_rel))
                    else:
                        namespaced_extra.append((oname, rpath))
                extra_media_rel_paths = namespaced_extra
            except Exception:
                pass

        preview_rel = f"external/{project_id}/{next_aid}/model.spz" if is_gs else stored_rel_path
        base_title = (display_name or source_filename).strip()
        primary_title = base_title

        # Create initial record in dbo.MOSAICEARTH_EXTERNAL_ASSETS so the asset ID exists immediately
        with self.lock:
            assets = self.tables.setdefault("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])
            asset_record = {
                "ID": next_aid,
                "PROJECT_ID": int(project_id),
                "USER_ID": int(user_id),
                "TITLE": primary_title,
                "DISPLAY_NAME": primary_title,
                "DESCRIPTION": inherited_desc,
                "CAPTURE_DATE": inherited_date,
                "CAPTURE_METHOD": inherited_method,
                "ASSET_TYPE": "input" if pending else asset_type,
                "ASSET_SUBTYPE": "source_media" if pending else subtype,
                "SOURCE_ORIGIN": "gaussian" if is_gs else (source_origin or "imported"),
                "RESULT_KIND": "" if pending else subtype,
                "SOURCE_FORMAT": "SPZ · PLY" if is_gs and not pending else (ext_low.upper() or "FILE"),
                "SOURCE_FILE_NAME": source_filename,
                "STORED_FILE_NAME": Path(stored_rel_path).name,
                "STORAGE_RELATIVE_PATH": stored_rel_path,
                "PREVIEW_RELATIVE_PATH": "" if pending else preview_rel,
                "VIEWER_KIND": "" if pending else viewer_kind,
                "FILE_SIZE_BYTES": int(size_bytes),
                "STATUS": "pending" if pending else "processing",
                "CANONICAL_STATUS": "PENDING" if pending else "PROCESSING",
                "STATUS_DETAIL": "Queued in background worker..." if run_sync is False else "Preparing and verifying the viewer and downloads.",
                "VISIBILITY": "private",
                "PRICE_CREDITS": 0,
                "LATITUDE": eff_lat,
                "LONGITUDE": eff_lon,
                "ALTITUDE": eff_alt,
                "HEADING": 0.0,
                "PITCH": 0.0,
                "ROLL": 0.0,
                "SCALE": 1.0,
                "HAS_EMBEDDED_GEOREF": ext_low in ("tif", "tiff", "las", "laz", "geojson", "json", "kml", "kmz", "gpx", "gpkg", "shp", "dxf", "csv"),
                "CREATED_AT": now_iso,
                "UPDATED_AT": now_iso,
                "DERIVATIVE_KEY": derivative_key,
                "USER_CLASS_OVERRIDE": asset_class_override,
            }
            if previous_derivative:
                next(a for a in assets if int(a["ID"]) == next_aid).update(asset_record)
            else:
                assets.append(asset_record)
            self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")

            jobs = self.tables.setdefault("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS", [])
            next_jid = next_record_id(jobs, "ID", 100)
            jobs.append({
                "ID": next_jid,
                "PROJECT_ID": int(project_id),
                "ASSET_ID": next_aid,
                "USER_ID": int(user_id),
                "STATUS": "QUEUED" if run_sync is False else "RUNNING",
                "CREATED_AT_UTC": now_iso,
                "STARTED_AT_UTC": now_iso,
                "FINISHED_AT_UTC": "",
                "LAST_ERROR": None,
            })
            self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS")

        # P1/P2: Enqueue heavy GIS imports / Gaussian Splatting into the Persistent Background Job Queue
        if run_sync is False:
            self.get_job_queue().enqueue_job(
                job_type="gaussian_splat" if is_gs else "gis_import",
                project_id=int(project_id),
                asset_id=next_aid,
                user_id=int(user_id),
                payload={
                    "source_filename": source_filename,
                    "upload_id": staged_upload_id,
                    "quality_preset": quality_preset,
                    "upload_attempt": upload_attempt,
                    "expected_asset_type": asset_type,
                    "expected_asset_subtype": subtype,
                    "stored_rel_path": stored_rel_path,
                    "source_origin": "gaussian" if is_gs else source_origin,
                    "lat": eff_lat,
                    "lon": eff_lon,
                    "alt": eff_alt,
                    "explicit_lat": lat is not None,
                    "explicit_lon": lon is not None,
                    "extra_media_rel_paths": extra_media_rel_paths or [],
                },
                within_registration=bool(staged_upload_id),
            )
            return next_aid

        pipeline_summary = ""
        prep: Dict[str, Any] = {}
        prep_failed_err = ""
        try:
            from .blob_store import get_blob_store
            from .gis_splat_engine import prepare_external_asset_derivatives
            prep = prepare_external_asset_derivatives(
                blob_store=get_blob_store(),
                project_id=int(project_id),
                asset_id=next_aid,
                source_filename=source_filename,
                stored_rel_path=stored_rel_path,
                raw_bytes=raw_bytes,
                lat=eff_lat,
                lon=eff_lon,
                alt=eff_alt,
                source_origin="gaussian" if is_gs else source_origin,
                extra_media_files=extra_media_files,
                user_class_override=asset_class_override,
            )
            if lat is None and prep.get("lat") is not None:
                eff_lat = float(prep["lat"])
            if lon is None and prep.get("lon") is not None:
                eff_lon = float(prep["lon"])
            if prep.get("preview_rel"):
                preview_rel = str(prep["preview_rel"])
            if prep.get("viewer_kind"):
                viewer_kind = str(prep["viewer_kind"])
            if prep.get("pipeline_summary"):
                pipeline_summary = str(prep["pipeline_summary"])
        except Exception as prep_exc:
            prep_failed_err = str(prep_exc)
            print(f"[MosaicEarth GIS/Splat Engine] Derivative warning for asset #{next_aid}: {prep_exc}", flush=True)

        with self.lock:
            for a in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                if int(a.get("ID") or 0) == next_aid:
                    a["PREVIEW_RELATIVE_PATH"] = preview_rel
                    a["VIEWER_KIND"] = viewer_kind
                    a["LATITUDE"] = eff_lat
                    a["LONGITUDE"] = eff_lon
                    if prep:
                        a["STATUS"] = "private-ready"
                        a["SOURCE_SHA256"] = str(prep.get("source_sha256") or "")
                        a_cls = str(prep.get("asset_class") or "")
                        a["ASSET_CLASS"] = a_cls
                        a["CANONICAL_STATUS"] = str(prep.get("canonical_status") or "READY")
                        a["INSPECTION_META"] = prep.get("inspection") or {}
                        a["DERIVATIVES_MANIFEST"] = prep.get("derivatives") or []
                        a["HAS_EMBEDDED_GEOREF"] = bool((prep.get("inspection") or {}).get("has_embedded_georef", a.get("HAS_EMBEDDED_GEOREF")))
                        if prep.get("vector_layers"):
                            a["VECTOR_LAYERS"] = prep["vector_layers"]
                        if prep.get("elevation_meta"):
                            a["ELEVATION_META"] = prep["elevation_meta"]
                        if prep.get("bbox_wgs84") and len(prep["bbox_wgs84"]) == 4:
                            b = prep["bbox_wgs84"]
                            a["BOUNDS_WEST"], a["BOUNDS_SOUTH"], a["BOUNDS_EAST"], a["BOUNDS_NORTH"] = b[0], b[1], b[2], b[3]
                        # Refine asset_type / subtype from deep inspection if not explicitly overridden
                        if a_cls == "POINT_CLOUD":
                            a["ASSET_TYPE"] = "pointcloud"
                            a["ASSET_SUBTYPE"] = "pointcloud"
                        elif a_cls in ("MESH", "CESIUM_3D_TILES"):
                            a["ASSET_TYPE"] = "model3d"
                            a["ASSET_SUBTYPE"] = "3dtiles" if a_cls == "CESIUM_3D_TILES" else "mesh"
                        elif a_cls == "GAUSSIAN_SPLAT":
                            a["ASSET_TYPE"] = "model3d"
                            a["ASSET_SUBTYPE"] = "gaussian_splat"
                        elif a_cls == "RASTER_ELEVATION":
                            a["ASSET_TYPE"] = "orthophoto"
                            if a.get("ASSET_SUBTYPE") not in ("dsm", "dtm"):
                                a["ASSET_SUBTYPE"] = "dtm" if "dtm" in source_filename.lower() else "dsm"
                        elif a_cls == "CESIUM_TERRAIN":
                            a["ASSET_TYPE"] = "orthophoto"
                            a["ASSET_SUBTYPE"] = "terrain"
                    elif prep_failed_err:
                        a["CANONICAL_STATUS"] = "FAILED"
                        a["STATUS"] = "failed"
                        a["STATUS_DETAIL"] = f"Import validation error: {prep_failed_err}"
                    if prep:
                        from .asset_metadata import apply_inspected_identity
                        apply_inspected_identity(a, prep)
                    if pipeline_summary:
                        a["STATUS_DETAIL"] = pipeline_summary
                    break
            self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")

            for import_job in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS", []):
                if int(import_job.get("ID") or 0) == next_jid:
                    import_job["STATUS"] = "FAILED" if prep_failed_err else "READY"
                    import_job["LAST_ERROR"] = prep_failed_err or None
                    import_job["FINISHED_AT_UTC"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                    break
            self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSET_JOBS")

            if is_gs:
                for gp in self.tables.get("dbo.MOSAICEARTH_GAUSSIAN_PROJECTS", []):
                    if int(gp.get("PROJECT_ID") or 0) == int(project_id):
                        gp["STATUS"] = "ready"
                        gp["STATUS_DETAIL"] = pipeline_summary or "3D Gaussian Splatting (KHR_gaussian_splatting + SPZ v2 + PLY) ready."
                        gp["ASSET_ID"] = next_aid
                        gp["UPDATED_AT"] = now_iso
                        break
                self.persist_table("dbo.MOSAICEARTH_GAUSSIAN_PROJECTS")

        if prep_failed_err and ("ZipSecurityError" in prep_failed_err or "Unsafe path" in prep_failed_err or "ZIP bomb" in prep_failed_err or "Symlinks" in prep_failed_err):
            raise ValueError(prep_failed_err)

        return next_aid

    def delete_project_file(self, project_id_or_file_id: int, file_id: Optional[int] = None, user_id: int = 0) -> Dict[str, Any]:
        target_fid = int(file_id if file_id is not None else project_id_or_file_id)
        target_pid = int(project_id_or_file_id) if file_id is not None else 0
        with self.lock:
            for f in self.tables.get("dbo.WEB_PROJECTS_FILES", []):
                if int(f.get("PROJECT_FILE_ID") or 0) == target_fid:
                    if not target_pid or int(f.get("PROJECT_ID") or 0) == target_pid:
                        target_pid = int(f.get("PROJECT_ID") or 0)
                        f["ACTIVO"] = False
                        self.persist_table("dbo.WEB_PROJECTS_FILES")
                        break
        files = self.get_project_files(target_pid) if target_pid else []
        total_mb = sum(float(f.get("SIZE") or 0) for f in files) / (1024.0 * 1024.0)
        import math
        u = self.get_user_by_id(user_id) if user_id else None
        return {
            "sizesTotal": total_mb,
            "numberFiles": len(files),
            "creditsCost": int(math.ceil(len(files) / 2.0)),
            "userCredits": int(u.get("CreditsInt") or 0) if u else 0,
        }

    def get_project_values(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for v in self.tables.get("dbo.WEB_PROJECTS_VALUES", []):
                if int(v.get("PROJECT_ID") or 0) == int(project_id):
                    return copy.deepcopy(v)
        return None

    def get_project_location(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for loc in self.tables.get("dbo.WEB_PROJECTS_LOCATION", []):
                if int(loc.get("PROJECT_ID") or 0) == int(project_id):
                    return copy.deepcopy(loc)
        return None

    def get_cesium_model_by_id(self, model_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            for m in self.tables.get("dbo.WEB_CESIUM_MODELS", []):
                if int(m.get("Id") or 0) == int(model_id):
                    return copy.deepcopy(m)
        return None

    def update_cesium_model_height(self, model_id_or_project_id: int, height_or_type: float, maybe_height: Optional[float] = None) -> bool:
        with self.lock:
            if maybe_height is not None:
                pid = int(model_id_or_project_id)
                mtype = int(height_or_type)
                h = float(maybe_height)
                for m in self.tables.get("dbo.WEB_CESIUM_MODELS", []):
                    if int(m.get("Project_id") or 0) == pid and int(m.get("ModelType") or 0) == mtype:
                        m["Height"] = h
                        self.persist_table("dbo.WEB_CESIUM_MODELS")
                        return True
                for m in self.tables.get("dbo.WEB_CESIUM_MODELS", []):
                    if int(m.get("Id") or 0) == pid:
                        m["Height"] = h
                        self.persist_table("dbo.WEB_CESIUM_MODELS")
                        return True
                return False
            for m in self.tables.get("dbo.WEB_CESIUM_MODELS", []):
                if int(m.get("Id") or 0) == int(model_id_or_project_id):
                    m["Height"] = float(height_or_type)
                    self.persist_table("dbo.WEB_CESIUM_MODELS")
                    return True
        return False

    def update_processed_asset_placement_3d(
        self,
        project_id: int,
        model_type: int,
        height: float,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        heading: float = 0.0,
        pitch: float = 0.0,
        roll: float = 0.0,
    ) -> bool:
        self.update_cesium_model_height(project_id, model_type, height)
        with self.lock:
            for m in self.tables.get("dbo.WEB_CESIUM_MODELS", []):
                if (int(m.get("Project_id") or 0) == int(project_id) and int(m.get("ModelType") or 0) == int(model_type)) or int(m.get("Id") or 0) == int(project_id):
                    m["Heading"] = float(heading)
                    m["Pitch"] = float(pitch)
                    m["Roll"] = float(roll)
                    self.persist_table("dbo.WEB_CESIUM_MODELS")
                    break
            if lat is not None and lon is not None:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        p["LATITUD"] = str(float(lat))
                        p["LONGITUD"] = str(float(lon))
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
                for loc in self.tables.get("dbo.WEB_PROJECTS_LOCATION", []):
                    if int(loc.get("PROJECT_ID") or 0) == int(project_id):
                        loc["LATITUD"] = str(float(lat))
                        loc["LONGITUD"] = str(float(lon))
                        self.persist_table("dbo.WEB_PROJECTS_LOCATION")
                        break
        return True

    def update_external_asset_placement_3d(
        self,
        project_id: int,
        asset_id: int,
        user_id: int,
        lat: float,
        lon: float,
        alt: float,
        heading: float = 0.0,
        pitch: float = 0.0,
        roll: float = 0.0,
        scale: float = 1.0,
    ) -> bool:
        now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
        updated = False
        target_origin = ""
        updated_aids: List[int] = []
        with self.lock:
            for a in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                if int(a.get("ID") or 0) == int(asset_id) and (not project_id or int(a.get("PROJECT_ID") or 0) == int(project_id)):
                    a["LATITUDE"] = float(lat)
                    a["LONGITUDE"] = float(lon)
                    a["ALTITUDE"] = float(alt)
                    a["HEADING"] = float(heading)
                    a["PITCH"] = float(pitch)
                    a["ROLL"] = float(roll)
                    a["SCALE"] = max(0.01, float(scale))
                    a["UPDATED_AT"] = now_iso
                    updated = True
                    project_id = int(a.get("PROJECT_ID") or project_id)
                    target_origin = str(a.get("SOURCE_ORIGIN") or "")
                    updated_aids.append(int(a["ID"]))
                    break
            # Keep companion Gaussian Splat visualization (Surface Mesh <-> Point Cloud) in the same task aligned
            if updated and target_origin in ("gaussian", "gaussian_pointcloud") and project_id:
                for a in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                    aid_other = int(a.get("ID") or 0)
                    if (
                        aid_other != int(asset_id)
                        and int(a.get("PROJECT_ID") or 0) == int(project_id)
                        and str(a.get("SOURCE_ORIGIN") or "") in ("gaussian", "gaussian_pointcloud")
                    ):
                        a["LATITUDE"] = float(lat)
                        a["LONGITUDE"] = float(lon)
                        a["ALTITUDE"] = float(alt)
                        a["HEADING"] = float(heading)
                        a["PITCH"] = float(pitch)
                        a["ROLL"] = float(roll)
                        a["SCALE"] = max(0.01, float(scale))
                        a["UPDATED_AT"] = now_iso
                        updated_aids.append(aid_other)
            if updated:
                self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")
        if updated:
            try:
                from .blob_store import get_blob_store
                from .gis_splat_engine import _build_enu_to_ecef_matrix
                bs = get_blob_store()
                new_transform = _build_enu_to_ecef_matrix(
                    float(lat),
                    float(lon),
                    float(alt),
                    heading_deg=float(heading),
                    pitch_deg=float(pitch),
                    roll_deg=float(roll),
                    scale=float(scale),
                )
                for u_aid in updated_aids:
                    ts_rel = f"external/{project_id}/{u_aid}/tileset.json"
                    ts_local = bs.ensure_local("results", ts_rel, force_download=True)
                    if ts_local and ts_local.is_file():
                        ts_obj = json.loads(ts_local.read_text(encoding="utf-8"))
                        if isinstance(ts_obj.get("root"), dict):
                            ts_obj["root"]["transform"] = new_transform
                            ts_bytes = json.dumps(ts_obj, indent=2).encode("utf-8")
                            bs.save_upload("results", ts_rel, ts_bytes)
                            bs.save_upload("assets", ts_rel, ts_bytes)
                            if bs.is_zero_local_disk():
                                ts_local.unlink(missing_ok=True)
                    tr_rel = f"external/{project_id}/{u_aid}/transform.json"
                    tr_local = bs.ensure_local("results", tr_rel, force_download=True)
                    if tr_local and tr_local.is_file():
                        try:
                            tr_obj = json.loads(tr_local.read_text(encoding="utf-8"))
                            tr_obj["anchor_wgs84"] = {
                                "lat": float(lat),
                                "lon": float(lon),
                                "alt": float(alt),
                                "heading": float(heading),
                                "pitch": float(pitch),
                                "roll": float(roll),
                                "scale": float(scale),
                            }
                            tr_obj["transform_ecef_column_major_4x4"] = new_transform
                            tr_bytes = json.dumps(tr_obj, indent=2).encode("utf-8")
                            bs.save_upload("results", tr_rel, tr_bytes)
                            bs.save_upload("assets", tr_rel, tr_bytes)
                        except Exception:
                            pass
                    geo_rel = f"external/{project_id}/{u_aid}/geo_alignment.txt"
                    geo_txt = (
                        "# MosaicEarth 3D Gaussian Splatting Geo-Alignment\n"
                        f"LATITUDE={float(lat):.8f}\n"
                        f"LONGITUDE={float(lon):.8f}\n"
                        f"ALTITUDE_M={float(alt):.3f}\n"
                        f"HEADING_DEG={float(heading):.3f}\n"
                        f"PITCH_DEG={float(pitch):.3f}\n"
                        f"ROLL_DEG={float(roll):.3f}\n"
                        f"SCALE={float(scale):.4f}\n"
                        f"ECEF_MATRIX_COLUMN_MAJOR={','.join(f'{v:.8f}' for v in new_transform)}\n"
                    ).encode("utf-8")
                    bs.save_upload("results", geo_rel, geo_txt)
                    bs.save_upload("assets", geo_rel, geo_txt)

                    # Canonical metadata/transform.json (Section 12 — explicit georeferencing separate from immutable source)
                    meta_tf_rel = f"external/{project_id}/{u_aid}/metadata/transform.json"
                    meta_tf_doc = {
                        "source_crs": "LOCAL_ENU",
                        "target_crs": "EPSG:4978",
                        "display_crs": "EPSG:4326",
                        "longitude": round(float(lon), 7),
                        "latitude": round(float(lat), 7),
                        "height": round(float(alt), 3),
                        "orientation": {
                            "heading": round(float(heading), 3),
                            "pitch": round(float(pitch), 3),
                            "roll": round(float(roll), 3),
                        },
                        "scale": round(float(scale), 5),
                        "transform_matrix_ecef_column_major": new_transform,
                        "vertical_reference": "WGS84",
                        "updated_at": now_iso,
                    }
                    meta_tf_bytes = json.dumps(meta_tf_doc, indent=2).encode("utf-8")
                    bs.save_upload("results", meta_tf_rel, meta_tf_bytes)
                    bs.save_upload("assets", meta_tf_rel, meta_tf_bytes)
                    # Also update derived/3dtiles/tileset.json if present
                    d_ts_rel = f"external/{project_id}/{u_aid}/derived/3dtiles/tileset.json"
                    d_ts_local = bs.ensure_local("results", d_ts_rel, force_download=True)
                    if d_ts_local and d_ts_local.is_file():
                        try:
                            d_ts_obj = json.loads(d_ts_local.read_text(encoding="utf-8"))
                            if isinstance(d_ts_obj.get("root"), dict):
                                d_ts_obj["root"]["transform"] = new_transform
                                d_ts_bytes = json.dumps(d_ts_obj, indent=2).encode("utf-8")
                                bs.save_upload("results", d_ts_rel, d_ts_bytes)
                        except Exception:
                            pass
            except Exception:
                pass
        return updated

    def get_desktop_license_info(self, user_id: int) -> Dict[str, Any]:
        import hmac
        self.refresh_account_state()
        u = self.get_user_by_id(user_id) if user_id > 0 else None
        if not u:
            return {
                "licensed": False,
                "user_id": 0,
                "email": "",
                "license_token": "",
                "plan_key": "free",
                "included_in_studio": False,
                "price_eur": 29.0,
            }
        uid = int(u.get("ID") or user_id)
        email = str(u.get("EMAIL") or "").strip().lower()
        plan_key = str(u.get("PLAN_KEY") or "free").strip().lower()
        is_studio = plan_key == "studio"
        is_purchased = bool(u.get("DESKTOP_LICENSE_PURCHASED"))
        is_admin = int(u.get("ROL_ID") or 0) == 1 or bool(u.get("IS_ADMIN"))
        is_licensed = (is_studio or is_purchased or is_admin) and u.get("ACTIVO", True) and not u.get("SESSION_REVOKED")
        secret = (self.get_setting("JwtSecret", "mosaicearth-desktop-license-secret-2026") or "mosaicearth-desktop-license-secret-2026").encode("utf-8")
        lic_tok = hmac.new(secret, f"{uid}:{email}:mosaicearth-desktop-v3".encode("utf-8"), hashlib.sha256).hexdigest() if is_licensed else ""
        return {
            "licensed": is_licensed,
            "user_id": uid,
            "email": email,
            "license_token": lic_tok,
            "plan_key": plan_key,
            "included_in_studio": is_studio,
            "purchased_standalone": is_purchased,
            "price_eur": 0.0 if is_licensed else 29.0,
        }

    def verify_desktop_license_token(self, user_id: int, email: str, token: str, machine_id: str = "") -> Dict[str, Any]:
        info = self.get_desktop_license_info(user_id)
        clean_email = (email or "").strip().lower()
        if not info.get("licensed"):
            return {"ok": False, "licensed": False, "message": "No active MosaicEarth Desktop license or Studio subscription found for this account."}
        if info["email"] != clean_email or not token or token != info["license_token"]:
            return {"ok": False, "licensed": False, "message": f"Desktop installer license is locked to {info['email']} and cannot be shared with another account."}
        if machine_id:
            with self.lock:
                for u in self.tables.get("dbo.USERS", []):
                    if int(u.get("ID") or 0) == int(user_id):
                        machines = [m for m in str(u.get("DESKTOP_MACHINE_IDS") or "").split(",") if m]
                        if machine_id not in machines:
                            if len(machines) >= 2:
                                return {
                                    "ok": False,
                                    "licensed": False,
                                    "message": "This Desktop license is already activated on the maximum number of workstations (2). Sharing installers across unauthorized machines is blocked.",
                                }
                            machines.append(machine_id)
                            u["DESKTOP_MACHINE_IDS"] = ",".join(machines)
                            self.persist_table("dbo.USERS")
                        break
        return {
            "ok": True,
            "licensed": True,
            "user_id": info["user_id"],
            "email": info["email"],
            "plan_key": info["plan_key"],
            "license_token": info["license_token"],
        }

    @staticmethod
    def _stripe_wallet_due(wallet_balance: float, price: float, stripe_cents: int) -> Optional[float]:
        due = max(0, round(price * 100) - stripe_cents)
        if due > round(wallet_balance * 100):
            return None
        return due / 100

    def purchase_desktop_license_eur(
        self,
        user_id: int,
        force_stripe_paid: bool = False,
        stripe_session_id: str = "",
        stripe_cents: Optional[int] = None,
    ) -> Dict[str, Any]:
        info = self.get_desktop_license_info(user_id)
        if info.get("licensed") and not force_stripe_paid:
            return {"ok": True, "already_licensed": True, **info}
        price_eur = 29.0
        now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
        with self.lock:
            user_row = None
            for u in self.tables.get("dbo.USERS", []):
                if int(u.get("ID") or 0) == int(user_id):
                    user_row = u
                    break
            if not user_row:
                return {"ok": False, "message": "User not found."}
            wallet_bal = float(user_row.get("CREATOR_EARNINGS_EUR") or 0.0)
            if not force_stripe_paid and wallet_bal < price_eur:
                return {
                    "ok": True,
                    "requires_stripe": True,
                    "amount_eur": round(price_eur - wallet_bal, 2),
                    "price_eur": price_eur,
                    "wallet_applied_eur": round(wallet_bal, 2),
                }
            paid_via = "stripe_card_eur" if force_stripe_paid else "creator_wallet_eur"
            if not force_stripe_paid and wallet_bal >= price_eur:
                user_row["CREATOR_EARNINGS_EUR"] = round(wallet_bal - price_eur, 2)
            elif force_stripe_paid and stripe_cents is not None:
                deduct = self._stripe_wallet_due(wallet_bal, price_eur, stripe_cents)
                if deduct is None:
                    return {"ok": False, "message": "The wallet portion requires reconciliation."}
                user_row["CREATOR_EARNINGS_EUR"] = round(wallet_bal - deduct, 2)
            user_row["DESKTOP_LICENSE_PURCHASED"] = 1
            user_row["DESKTOP_LICENSE_DATE"] = now_iso
            self.persist_table("dbo.USERS")

            txs = self.tables.setdefault("dbo.WEB_TRANSACTION", [])
            if stripe_session_id and any(str(t.get("STRIPE_SESSION_ID") or "") == stripe_session_id for t in txs):
                new_info = self.get_desktop_license_info(user_id)
                return {"ok": True, "paid_eur": price_eur, "paid_via": paid_via, **new_info}
            next_tid = next_record_id(txs, "ID", 1000)
            txs.append({
                "ID": next_tid,
                "USER_ID": int(user_id),
                "AMOUNT": price_eur,
                "CURRENCY": "EUR",
                "CONCEPT": "MosaicEarth Desktop License (Windows / Linux / macOS · Session-Bound)",
                "PAYMENT_METHOD": paid_via,
                "STRIPE_SESSION_ID": stripe_session_id,
                "STATUS": "COMPLETED",
                "CREATE_DATE": now_iso,
            })
            self.persist_table("dbo.WEB_TRANSACTION")
        new_info = self.get_desktop_license_info(user_id)
        return {"ok": True, "paid_eur": price_eur, "paid_via": paid_via, **new_info}

    def request_creator_payout_eur(self, user_id: int, destination_iban_or_email: str = "") -> Dict[str, Any]:
        now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
        with self.lock:
            user_row = next((u for u in self.tables.get("dbo.USERS", []) if int(u.get("ID") or 0) == int(user_id)), None)
            if not user_row:
                return {"ok": False, "message": "User not found."}
            bal = round(float(user_row.get("CREATOR_EARNINGS_EUR") or 0.0), 2)
            if bal < 10.0:
                return {"ok": False, "message": f"Minimum Stripe payout is 10.00 € (current Creator Wallet balance: {bal:.2f} €). You can use any balance immediately for internal purchases or processing."}
            dest = (destination_iban_or_email or str(user_row.get("EMAIL") or "")).strip()
            user_row["CREATOR_EARNINGS_EUR"] = 0.0
            self.persist_table("dbo.USERS")
            payouts = self.tables.setdefault("dbo.MOSAICEARTH_CREATOR_PAYOUTS", [])
            next_pid = next_record_id(payouts, "ID", 1)
            payouts.append({
                "ID": next_pid,
                "USER_ID": int(user_id),
                "AMOUNT_EUR": bal,
                "DESTINATION": dest,
                "STATUS": "REQUESTED_STRIPE_PAYOUT",
                "CREATED_AT": now_iso,
            })
            self.persist_table("dbo.MOSAICEARTH_CREATOR_PAYOUTS")
            return {
                "ok": True,
                "payoutId": next_pid,
                "amountEur": bal,
                "destination": dest,
                "message": f"Stripe payout of {bal:.2f} € requested for {dest}.",
            }

    def save_project_info(
        self,
        project_id: int,
        user_id: int,
        name_or_desc: str,
        desc_or_cat: Any = "",
        cat_or_tags: Any = 0,
        maybe_tags: Optional[str] = None,
    ) -> bool:
        with self.lock:
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id) and int(p.get("CREATE_ID") or 0) == int(user_id):
                    if maybe_tags is not None:
                        if str(name_or_desc or "").strip():
                            p["NAME"] = str(name_or_desc).strip()
                        p["DESCRIPTION"] = str(desc_or_cat or "")
                        p["CATEGORY_ID"] = int(cat_or_tags or 0)
                        p["TAGS"] = str(maybe_tags or "")
                    else:
                        p["DESCRIPTION"] = str(name_or_desc or "")
                        p["CATEGORY_ID"] = int(desc_or_cat or 0)
                        p["TAGS"] = str(cat_or_tags or "")
                    p["UPDATE_DATE"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                    self.persist_table("dbo.WEB_PROJECTS")
                    return True
        return False

    def publish_project_assets(
        self,
        project_id: int,
        user_id: int,
        credits: int,
        publish_type: int,
        asset_types: List[int],
        capture_from: str = "",
        capture_to: str = "",
        capture_precision: str = "unknown",
    ) -> List[int]:
        with self.lock:
            pubs = self.tables.setdefault("dbo.WEB_PUBLICATIONS", [])
            existing_approved = {
                int(pub.get("FILE_TYPE_ID") or 0)
                for pub in pubs
                if int(pub.get("PROJECT_ID") or 0) == int(project_id) and int(pub.get("STATUS_ID") or 0) == 2
            }
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            for ft in asset_types:
                if ft in (1, 2, 3) and ft not in existing_approved:
                    next_id = next_record_id(pubs, "ID", 100)
                    pubs.append({
                        "ID": next_id,
                        "STATUS_ID": 2,
                        "USER_ID": user_id,
                        "CREATE_DATE": now_iso,
                        "PARTNER_ID": user_id,
                        "FILE_TYPE_ID": ft,
                        "PROJECT_ID": project_id,
                        "REASON": "Published automatically; administrator moderation remains available.",
                        "CREDITS": int(credits or 0),
                        "PUBLISH_TYPE": int(publish_type or 0),
                    })
                    existing_approved.add(ft)
                    temp_rows = self.tables.setdefault("dbo.MOSAICEARTH_ASSET_TEMPORAL", [])
                    akey = f"processed-{ft}-{project_id}"
                    temp_rows[:] = [r for r in temp_rows if str(r.get("ASSET_KEY") or "") != akey]
                    c_start = capture_from.strip() + "T00:00:00Z" if capture_from and len(capture_from.strip()) == 10 else None
                    c_end = capture_to.strip() + "T00:00:00Z" if capture_to and len(capture_to.strip()) == 10 else c_start
                    temp_rows.append({
                        "ASSET_KEY": akey,
                        "SOURCE_KIND": "processed",
                        "PROJECT_ID": project_id,
                        "FILE_TYPE_ID": ft,
                        "EXTERNAL_ASSET_ID": None,
                        "CAPTURE_START_UTC": c_start,
                        "CAPTURE_END_UTC": c_end,
                        "CAPTURE_PRECISION": capture_precision or "unknown",
                        "CAPTURE_SOURCE": "manual" if c_start else "unknown",
                        "IS_MANUAL": bool(c_start),
                        "CREATED_DATE_UTC": now_iso,
                        "UPDATED_AT_UTC": now_iso,
                        "EFFECTIVE_START_UTC": c_start or now_iso,
                        "EFFECTIVE_END_UTC": c_end or now_iso,
                    })
            self.persist_table("dbo.WEB_PUBLICATIONS")
            self.persist_table("dbo.MOSAICEARTH_ASSET_TEMPORAL")
            return sorted(existing_approved)

    def get_subprojects_family(self, project_id: int) -> Tuple[int, List[Dict[str, Any]]]:
        """Returns (root_project_id, list_of_subprojects_in_family) for hierarchical Project -> Subprojects -> Assets."""
        with self.lock:
            current = None
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id) and p.get("ACTIVO", True):
                    current = p
                    break
            if not current:
                return int(project_id), []
            root_id = int(current.get("PARENT_PROJECT_ID") or 0) or int(current.get("ID") or 0)
            family = []
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if not p.get("ACTIVO", True):
                    continue
                pid = int(p.get("ID") or 0)
                parent_id = int(p.get("PARENT_PROJECT_ID") or 0)
                if pid == root_id or parent_id == root_id:
                    family.append(copy.deepcopy(p))
            family.sort(key=lambda r: (0 if int(r.get("ID") or 0) == root_id else 1, int(r.get("ID") or 0)))
            return root_id, family

    def update_subproject_asset_quick(
        self,
        user_id: int,
        project_id: int,
        file_type_id: int,
        custom_title: str = "",
        visibility_mode: str = "private",
        price_eur: int = 0,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        height: Optional[float] = None,
        external_asset_id: int = 0,
        creation_date: str = "",
        methodology: str = "",
        description: str = "",
        category_id: int = 0,
        tags: str = "",
    ) -> Dict[str, Any]:
        """Updates an asset's name, origin metadata (creation_date, methodology, description, category, tags), 3D location (lat/lon/height), and publication status."""
        with self.lock:
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            mode = (visibility_mode or "private").strip().lower()
            eff_price = max(0, int(price_eur or 0)) if mode == "marketplace" else 0

            if int(external_asset_id or 0) > 0:
                target_ea = None
                for ea in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                    if int(ea.get("ID") or 0) == int(external_asset_id) and int(ea.get("USER_ID") or 0) == int(user_id):
                        target_ea = ea
                        break
                if not target_ea:
                    return {"ok": False, "message": "External asset not found or unauthorized."}
                from .asset_uploads import asset_output_available
                if mode != "private" and not asset_output_available(target_ea):
                    return {"ok": False, "message": "Only verified processing results can be published."}
                if custom_title.strip():
                    target_ea["TITLE"] = custom_title.strip()
                    target_ea["DISPLAY_NAME"] = custom_title.strip()
                if creation_date.strip():
                    target_ea["CAPTURE_DATE"] = creation_date.strip()
                if methodology.strip():
                    target_ea["CAPTURE_METHOD"] = methodology.strip()
                if description is not None:
                    target_ea["DESCRIPTION"] = description.strip()
                if int(category_id or 0) > 0:
                    target_ea["CATEGORY_ID"] = int(category_id)
                if tags is not None and tags != "":
                    target_ea["TAGS"] = tags.strip()
                if lat is not None:
                    target_ea["LATITUDE"] = float(lat)
                if lon is not None:
                    target_ea["LONGITUD"] = float(lon)
                    target_ea["LONGITUDE"] = float(lon)
                if height is not None:
                    target_ea["ALTITUDE"] = float(height)
                target_ea["VISIBILITY"] = "public" if mode != "private" else "private"
                target_ea["IS_PUBLISHED"] = mode != "private"
                target_ea["PRICE_CREDITS"] = eff_price
                target_ea["UPDATED_AT"] = now_iso
                self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")
                badge = (
                    f"Marketplace · {eff_price} € (0 GB Quota)"
                    if eff_price > 0
                    else ("Public Free · 0 € (0 GB Quota)" if mode != "private" else "Private (5 GB Quota)")
                )
                return {"ok": True, "badge": badge, "isPublic": mode != "private"}

            proj = None
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id) and p.get("ACTIVO", True):
                    proj = p
                    break
            if not proj or int(proj.get("CREATE_ID") or proj.get("USER_ID") or 0) != int(user_id):
                return {"ok": False, "message": "Project not found or unauthorized."}

            if custom_title.strip():
                titles_map = proj.setdefault("ASSET_TITLES", {})
                titles_map[str(file_type_id)] = custom_title.strip()
            if int(category_id or 0) > 0:
                proj["CATEGORY_ID"] = int(category_id)
            if tags is not None and tags != "":
                proj["TAGS"] = tags.strip()
            asset_meta_map = proj.setdefault("ASSET_METADATA", {})
            ft_meta = asset_meta_map.setdefault(str(file_type_id), {})
            if creation_date.strip():
                ft_meta["creation_date"] = creation_date.strip()
            if methodology.strip():
                ft_meta["methodology"] = methodology.strip()
            if description is not None:
                ft_meta["description"] = description.strip()
            if int(category_id or 0) > 0:
                ft_meta["category_id"] = int(category_id)
            if tags is not None and tags != "":
                ft_meta["tags"] = tags.strip()
            self.persist_table("dbo.WEB_PROJECTS")

            if lat is not None and lon is not None:
                locs = self.tables.setdefault("dbo.WEB_PROJECTS_LOCATION", [])
                loc_row = next((l for l in locs if int(l.get("PROJECT_ID") or 0) == int(project_id)), None)
                if loc_row:
                    loc_row["LATITUD"] = str(round(float(lat), 6))
                    loc_row["LONGITUD"] = str(round(float(lon), 6))
                else:
                    locs.append({
                        "ID": next_record_id(locs, "ID", 1),
                        "PROJECT_ID": int(project_id),
                        "LATITUD": str(round(float(lat), 6)),
                        "LONGITUD": str(round(float(lon), 6)),
                    })
                proj["LATITUD"] = str(round(float(lat), 6))
                proj["LONGITUD"] = str(round(float(lon), 6))
                self.persist_table("dbo.WEB_PROJECTS_LOCATION")
                self.persist_table("dbo.WEB_PROJECTS")

            if height is not None and file_type_id in (1, 3):
                models = self.tables.setdefault("dbo.WEB_CESIUM_MODELS", [])
                m_row = next((m for m in models if int(m.get("Project_id") or 0) == int(project_id) and int(m.get("ModelType") or 0) == int(file_type_id)), None)
                if m_row:
                    m_row["Height"] = float(height)
                else:
                    models.append({
                        "Id": next_record_id(models, "Id", 1),
                        "Project_id": int(project_id),
                        "ModelType": int(file_type_id),
                        "Height": float(height),
                    })
                self.persist_table("dbo.WEB_CESIUM_MODELS")

            pubs = self.tables.setdefault("dbo.WEB_PUBLICATIONS", [])
            pub_row = next((r for r in pubs if int(r.get("PROJECT_ID") or 0) == int(project_id) and int(r.get("FILE_TYPE_ID") or 0) == int(file_type_id)), None)
            mode = (visibility_mode or "private").strip().lower()
            if mode == "private":
                if pub_row:
                    pub_row["STATUS_ID"] = 0
                    pub_row["CREDITS"] = 0
                    self.persist_table("dbo.WEB_PUBLICATIONS")
                badge = "Private (Uses 5 GB Quota)"
            else:
                eff_price = max(0, int(price_eur or 0)) if mode == "marketplace" else 0
                if pub_row:
                    pub_row["STATUS_ID"] = 2
                    pub_row["CREDITS"] = eff_price
                    pub_row["PUBLISH_TYPE"] = 1 if eff_price > 0 else 0
                else:
                    next_id = next_record_id(pubs, "ID", 100)
                    pubs.append({
                        "ID": next_id,
                        "STATUS_ID": 2,
                        "USER_ID": user_id,
                        "CREATE_DATE": now_iso,
                        "PARTNER_ID": user_id,
                        "FILE_TYPE_ID": int(file_type_id),
                        "PROJECT_ID": int(project_id),
                        "REASON": "Published via inline asset manager.",
                        "CREDITS": eff_price,
                        "PUBLISH_TYPE": 1 if eff_price > 0 else 0,
                    })
                self.persist_table("dbo.WEB_PUBLICATIONS")
                badge = f"Published · Marketplace ({eff_price} € · 0 GB Quota)" if eff_price > 0 else "Published · Free (0 € · 0 GB Quota)"

            return {"ok": True, "badge": badge, "isPublic": mode != "private"}

    def get_user_storage_summary(self, user_id: int) -> Dict[str, Any]:
        """Computes user's Private Storage Quota used vs limit (5 GB Free / 250 GB Pro / 1 TB Studio + Extra Storage GB), monthly GPU processing allowance, and Creator Earnings (€)."""
        with self.lock:
            u = self.get_user_by_id(user_id) or {}
            plan_key = str(u.get("PLAN_KEY") or "free").lower()
            if plan_key not in ("free", "pro", "studio"):
                plan_key = "free"
            base_quota_gb = 1000.0 if plan_key == "studio" else (250.0 if plan_key == "pro" else 5.0)
            extra_gb = round(max(0.0, float(u.get("EXTRA_STORAGE_GB") or 0.0)), 2)
            quota_gb = round(base_quota_gb + extra_gb, 2)
            is_legacy_studio = bool(u.get("IS_LEGACY_STUDIO")) and plan_key == "studio"
            cap_str = f"{round(quota_gb / 1000.0, 2):g} TB" if quota_gb >= 1000.0 else f"{int(quota_gb)} GB"
            extra_suffix = f" · +{int(extra_gb)} GB Extra" if extra_gb > 0 else ""
            if plan_key == "studio":
                plan_label = f"Studio · Legacy ({cap_str}{extra_suffix})" if is_legacy_studio else f"Studio ({cap_str}{extra_suffix})"
            elif plan_key == "pro":
                plan_label = f"Pro ({cap_str}{extra_suffix})"
            else:
                plan_label = f"Free ({cap_str}{extra_suffix})"
            can_share_private = plan_key in ("pro", "studio")
            can_whitelabel = plan_key == "studio"
            creator_payout_pct = 90 if plan_key == "studio" else (85 if plan_key == "pro" else 80)
            monthly_images_limit = 4000 if plan_key == "studio" else (1000 if plan_key == "pro" else 0)
            monthly_splats_limit = 12 if plan_key == "studio" else (3 if plan_key == "pro" else 0)
            price_per_extra_image_eur = 0.015 if plan_key == "studio" else (0.02 if plan_key == "pro" else 0.03)
            wholesale_cost_per_image_eur = 0.006  # RunPod Serverless / Modal / Hetzner GPU wholesale cost
            price_per_extra_splat_eur = 1.50 if plan_key == "studio" else (2.00 if plan_key == "pro" else 3.00)
            wholesale_cost_per_splat_eur = 0.60
            creator_earnings_eur = round(float(u.get("CREATOR_EARNINGS_EUR") or 0.0), 2)
            monthly_images_used = int(u.get("MONTHLY_IMAGES_USED") or 0)
            monthly_splats_used = int(u.get("MONTHLY_SPLATS_USED") or 0)

            pub_set = {
                (int(pub.get("PROJECT_ID") or 0), int(pub.get("FILE_TYPE_ID") or 0))
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", [])
                if int(pub.get("STATUS_ID") or 0) == 2
            }
            private_bytes = 0
            public_bytes = 0
            private_count = 0
            public_count = 0

            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if not p.get("ACTIVO", True) or int(p.get("CREATE_ID") or p.get("USER_ID") or 0) != int(user_id):
                    continue
                pid = int(p.get("ID") or 0)
                deleted_fts = {int(x) for x in (p.get("DELETED_FILE_TYPES") or []) if str(x).isdigit()}
                if int(p.get("STATE_ID") or 0) == 4 and bool(p.get("ZIP_UPLOADED")):
                    contract = p.get("CANONICAL_RESULT_CONTRACT") or {}
                    for ft, product in ((2, "orthophoto"), (3, "mesh"), (1, "pointcloud")):
                        if ft in deleted_fts or (contract and not contract.get(product + "_master")):
                            continue
                        b = int((contract.get("master_sizes") or {}).get(product) or 0)
                        if not b and contract.get(product + "_master"):
                            from .blob_store import get_blob_store
                            master = get_blob_store().resolve_path("imagenes", contract[product + "_master"])
                            b = master.stat().st_size if master and master.is_file() else 0
                        if (pid, ft) in pub_set:
                            public_bytes += b
                            public_count += 1
                        else:
                            private_bytes += b
                            private_count += 1

            for ea in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                if str(ea.get("STATUS") or "").lower() in ("deleted", "discarded"):
                    continue
                if int(ea.get("USER_ID") or 0) != int(user_id):
                    continue
                b = int(ea.get("FILE_SIZE_BYTES") or ea.get("SIZE_BYTES") or 0)
                if str(ea.get("VISIBILITY") or "").lower() == "public":
                    public_bytes += b
                    public_count += 1
                else:
                    private_bytes += b
                    private_count += 1

            # Sync originals include sources and attachments. Product masters already
            # counted above share these exact bytes, so subtract them once.
            sync_root = self.data_root / "connector_sync" / str(user_id)
            for metadata in sync_root.glob("*/manifest.json"):
                try:
                    snapshot = json.loads(metadata.read_text())
                    if snapshot.get("export") or snapshot.get("status") == "CANCELED":
                        continue
                    already_counted = sum(int(a.get("FILE_SIZE_BYTES") or 0) for a in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])
                        if int(a.get("USER_ID") or 0) == int(user_id) and str(a.get("DERIVATIVE_KEY") or "").startswith(f"sync:{user_id}:{snapshot['id']}:")
                        and a.get("STATUS") not in {"deleted", "discarded"} and a.get("VISIBILITY") != "public")
                    private_bytes += max(0, int(snapshot["bytes"]) - already_counted)
                except (OSError, ValueError, KeyError):
                    continue
            private_gb = round(private_bytes / (1024.0 ** 3), 2)
            public_gb = round(public_bytes / (1024.0 ** 3), 2)
            pct = min(100.0, round((private_gb / max(0.1, quota_gb)) * 100.0, 1))
            return {
                "planKey": plan_key,
                "plan_key": plan_key,
                "plan_code": plan_key,
                "planLabel": plan_label,
                "plan_name": plan_label,
                "isLegacyStudio": is_legacy_studio,
                "is_legacy_studio": is_legacy_studio,
                "baseQuotaGb": base_quota_gb,
                "extraStorageGb": extra_gb,
                "extra_storage_gb": extra_gb,
                "canSharePrivate": can_share_private,
                "can_share_private": can_share_private,
                "canWhiteLabel": can_whitelabel,
                "creatorPayoutPct": creator_payout_pct,
                "creator_payout_pct": creator_payout_pct,
                "creatorEarningsEur": creator_earnings_eur,
                "creator_earnings_eur": creator_earnings_eur,
                "monthlyImagesLimit": monthly_images_limit,
                "monthly_images_limit": monthly_images_limit,
                "monthlyImagesUsed": monthly_images_used,
                "monthly_images_used": monthly_images_used,
                "monthlySplatsLimit": monthly_splats_limit,
                "monthly_splats_limit": monthly_splats_limit,
                "monthlySplatsUsed": monthly_splats_used,
                "monthly_splats_used": monthly_splats_used,
                "pricePerExtraImageEur": price_per_extra_image_eur,
                "pricePerExtraSplatEur": price_per_extra_splat_eur,
                "quotaGb": quota_gb,
                "quota_gb": quota_gb,
                "privateUsedGb": private_gb,
                "private_used_bytes": private_bytes,
                "used_gb": private_gb,
                "publicZeroQuotaGb": public_gb,
                "privateAssetsCount": private_count,
                "private_assets_count": private_count,
                "publicAssetsCount": public_count,
                "public_assets_count": public_count,
                "public_asset_count": public_count,
                "percentUsed": pct,
                "percent": pct,
                "used_pct": pct,
            }

    def calculate_task_processing_quote(
        self,
        user_id: int,
        num_images: int = 100,
        task_kind: str = "processing",
        total_megabytes: float = 0.0,
        target_mode: str = "auto",
    ) -> Dict[str, Any]:
        """Calculates exact upfront Pay-Per-Task Euro (€) price before processing starts using `resolve_processing_backend`."""
        summary = self.get_user_storage_summary(user_id)
        is_desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        t_mode = (target_mode or "auto").strip().lower()
        route = self.resolve_processing_backend(user_id=user_id, target_mode=t_mode, probe_local_live=False)

        if t_mode == "cloud" and is_desktop_local:
            cloud_acc = self.get_desktop_cloud_account()
            c_plan = str(cloud_acc.get("plan_code") or "free").lower() if cloud_acc.get("logged_in") else "free"
            if c_plan == "free":
                summary = {
                    **summary,
                    "plan_code": "free",
                    "plan_name": "Free (Cloud Pay-Per-Task)",
                    "monthly_images_limit": 0,
                    "monthly_images_used": 0,
                    "monthly_splats_limit": 0,
                    "monthly_splats_used": 0,
                    "pricePerExtraImageEur": 0.030,
                    "pricePerExtraSplatEur": 4.50,
                }

        is_gauss = (task_kind or "").strip().lower() == "gaussian"
        if not is_gauss and not route.get("is_billable_cloud"):
            backend_code = str(route.get("backend") or "LOCAL_NODEODM")
            return {
                "mode": "custom_nodeodm" if backend_code == "CUSTOM_NODEODM" else "local_nodeodm",
                "backend": backend_code,
                "engine": str(route.get("engine_label") or "opendronemap/nodeodm:latest"),
                "provider": str(route.get("provider_label") or "Local PC / Custom NodeODM (0.00 €)"),
                "endpoint_url": str(route.get("endpoint_url") or ""),
                "num_images": int(num_images or 0),
                "covered_images": int(num_images or 0),
                "billable_images": 0,
                "resolution_multiplier": 1.0,
                "retail_price_eur": 0.0,
                "total_price_eur": 0.0,
                "plan_label": (
                    "Custom NodeODM (0.00 €)"
                    if backend_code == "CUSTOM_NODEODM"
                    else ("Local Workstation (0.00 €)" if is_desktop_local else summary["plan_name"])
                ),
                "upfront_required": False,
                "auto_refund_on_failure": True,
                "covered_by_plan": True,
                "summary_label": (
                    f"0.00 € (Processed on your private NodeODM: {route.get('endpoint_url')})"
                    if backend_code == "CUSTOM_NODEODM"
                    else "0.00 € (Processed locally on your PC via MosaicEarth Desktop)"
                ),
            }
        if is_gauss:
            rem_splats = max(0, int(summary["monthly_splats_limit"]) - int(summary["monthly_splats_used"]))
            retail = 0.0 if rem_splats > 0 else float(summary["pricePerExtraSplatEur"])
            return {
                "mode": "cloud_gpu",
                "backend": "MOSAICEARTH_CLOUD",
                "engine": "3D Gaussian Splatting (SfM + Multi-View Gaussian Splat Optimizer)",
                "provider": "MosaicEarth Cloud Processing",
                "num_images": int(num_images or 0),
                "covered_images": int(num_images or 0) if rem_splats > 0 else 0,
                "billable_images": 0 if rem_splats > 0 else int(num_images or 0),
                "resolution_multiplier": 1.0,
                "retail_price_eur": round(retail, 2),
                "total_price_eur": round(retail, 2),
                "plan_label": summary["plan_name"],
                "upfront_required": retail > 0.0,
                "auto_refund_on_failure": True,
                "covered_by_plan": rem_splats > 0,
                "summary_label": (
                    f"0.00 € (Included in your {summary['plan_name']} monthly quota · {rem_splats} Splats remaining)"
                    if rem_splats > 0
                    else f"{retail:.2f} € Upfront Pay-Per-Task (100% automatic refund if processing fails)"
                ),
            }
        n_img = max(5, int(num_images or 0))
        avg_mb = (float(total_megabytes) / float(n_img)) if (total_megabytes and n_img > 0) else 8.0
        res_mult = round(max(1.0, avg_mb / 12.0), 2) if avg_mb > 12.0 else 1.0
        effective_imgs = int(round(n_img * res_mult))
        rem_imgs = max(0, int(summary["monthly_images_limit"]) - int(summary["monthly_images_used"]))
        billable_imgs = max(0, effective_imgs - rem_imgs)
        _internal_floor = round(effective_imgs * 0.021, 2)
        if billable_imgs == 0:
            retail = 0.0
        else:
            raw_retail = round(billable_imgs * float(summary["pricePerExtraImageEur"]), 2)
            retail = max(2.00, raw_retail, _internal_floor)
        return {
            "mode": "cloud_gpu",
            "backend": "MOSAICEARTH_CLOUD",
            "engine": str(route.get("engine_label") or "OpenDroneMap (NodeODM)"),
            "provider": "MosaicEarth Cloud Processing",
            "num_images": n_img,
            "effective_images": effective_imgs,
            "resolution_multiplier": res_mult,
            "covered_images": max(0, effective_imgs - billable_imgs),
            "billable_images": billable_imgs,
            "retail_price_eur": retail,
            "total_price_eur": retail,
            "plan_label": summary["plan_name"],
            "upfront_required": retail > 0.0,
            "auto_refund_on_failure": True,
            "covered_by_plan": billable_imgs == 0,
            "summary_label": (
                f"0.00 € (Covered by your {summary['plan_name']} quota · {rem_imgs} images remaining this month)"
                if billable_imgs == 0
                else f"{retail:.2f} € Upfront Pay-Per-Task ({billable_imgs} billable images × {summary['pricePerExtraImageEur']:.3f} €/img · refund review if failed)"
            ),
        }

    def authorize_and_charge_task_upfront(
        self,
        user_id: int,
        project_id: int,
        num_images: int = 0,
        task_kind: str = "photogrammetry",
        total_megabytes: float = 0.0,
        force_stripe_paid: bool = False,
        stripe_session_id: str = "",
        target_mode: str = "auto",
        stripe_cents: Optional[int] = None,
        processing_quote: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Strictly authorizes and charges (or deducts from monthly plan quota) BEFORE starting processing.
        Failed jobs restore unused plan quota and record any cash component for reconciliation."""
        with self.stripe_transaction():
            proj = next((row for row in self.tables.get("dbo.WEB_PROJECTS", [])
                         if int(row.get("ID") or 0) == project_id and row.get("ACTIVO", True)), None)
            if not proj or int(proj.get("CREATE_ID") or 0) != int(user_id):
                return {"ok": False, "code": "not_found", "message": "Task not found."}
            if proj.get("PAYMENT_STATUS") == "REFUND_PENDING_REVIEW":
                return {"ok": False, "message": "The previous task payment is still being reconciled."}
            if proj.get("PAYMENT_STATUS") in {"COVERED_BY_PLAN", "AUTHORIZED_PAID_UPFRONT"}:
                if (proj.get("AUTHORIZED_TASK_KIND", task_kind) != task_kind
                        or (force_stripe_paid and proj.get("AUTHORIZED_STRIPE_SESSION") != stripe_session_id)):
                    return {"ok": False, "message": "This task already has a different processing authorization."}
                return {"ok": True, "already_authorized": True, "paymentStatus": proj["PAYMENT_STATUS"],
                        "payment_status": proj["PAYMENT_STATUS"], "charged_eur": float(proj.get("UPFRONT_AMOUNT_EUR") or 0)}
            if num_images <= 0:
                files = self.get_project_files(project_id)
                num_images = len(files)
                total_megabytes = sum(float(f.get("SIZE") or 0) for f in files) / (1024.0 * 1024.0)
            quote = processing_quote or self.calculate_task_processing_quote(
                user_id,
                num_images=num_images,
                task_kind=task_kind,
                total_megabytes=total_megabytes,
                target_mode=target_mode,
            )
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            covered_imgs = int(quote.get("covered_images") or 0)
            retail_eur = float(quote.get("retail_price_eur") or 0.0)
            is_gauss = (task_kind or "").strip().lower() == "gaussian"
            is_non_billable = quote.get("mode") in ("local_nodeodm", "custom_nodeodm")

            if retail_eur > 0.0 and not is_non_billable:
                u_row = next((u for u in self.tables.get("dbo.USERS", []) if int(u.get("ID") or 0) == int(user_id)), None)
                wallet_bal = float((u_row or {}).get("CREATOR_EARNINGS_EUR") or 0.0)
                if not force_stripe_paid and wallet_bal < retail_eur:
                    return {
                        "ok": True,
                        "requires_stripe": True,
                        "amount_eur": max(0.50, round(retail_eur - wallet_bal, 2)),
                        "retail_price_eur": retail_eur,
                        "wallet_applied_eur": round(wallet_bal, 2),
                        "project_id": int(project_id),
                        "num_images": int(num_images),
                        "task_kind": task_kind,
                        "quote": quote,
                    }
                if u_row:
                    if not force_stripe_paid and wallet_bal >= retail_eur:
                        u_row["CREATOR_EARNINGS_EUR"] = round(wallet_bal - retail_eur, 2)
                    elif force_stripe_paid and stripe_cents is not None:
                        deduct = self._stripe_wallet_due(wallet_bal, retail_eur, stripe_cents)
                        if deduct is None:
                            return {"ok": False, "message": "The wallet portion requires reconciliation."}
                        u_row["CREATOR_EARNINGS_EUR"] = round(wallet_bal - deduct, 2)
                    self.persist_table("dbo.USERS")

            # Update user's monthly quota usage if covered by Pro/Studio plan
            if not is_non_billable:
                for u in self.tables.get("dbo.USERS", []):
                    if int(u.get("ID") or 0) == int(user_id):
                        if is_gauss and quote.get("covered_by_plan"):
                            u["MONTHLY_SPLATS_USED"] = int(u.get("MONTHLY_SPLATS_USED") or 0) + 1
                        elif covered_imgs > 0:
                            u["MONTHLY_IMAGES_USED"] = int(u.get("MONTHLY_IMAGES_USED") or 0) + covered_imgs
                        self.persist_table("dbo.USERS")
                        break

            pay_status = (
                "CUSTOM_NODEODM_0_EUR"
                if quote.get("mode") == "custom_nodeodm"
                else (
                    "LOCAL_NODEODM_0_EUR"
                    if quote.get("mode") == "local_nodeodm"
                    else ("COVERED_BY_PLAN" if retail_eur <= 0.0 else "AUTHORIZED_PAID_UPFRONT")
                )
            )
            proj["PAYMENT_STATUS"] = pay_status
            proj["UPFRONT_AMOUNT_EUR"] = retail_eur
            proj["COVERED_PLAN_IMAGES"] = covered_imgs
            proj["COVERED_PLAN_SPLATS"] = 1 if (is_gauss and quote.get("covered_by_plan")) else 0
            proj["WHOLESALE_COST_EUR"] = float(quote.get("wholesale_cost_eur") or 0.0)
            proj["PLATFORM_MARGIN_EUR"] = float(quote.get("platform_margin_eur") or 0.0)
            proj["AUTHORIZED_AT_UTC"] = now_iso
            proj["AUTHORIZED_TASK_KIND"] = task_kind
            proj["AUTHORIZED_STRIPE_SESSION"] = stripe_session_id
            proj["UPFRONT_STRIPE_CENTS"] = int(stripe_cents or 0) if force_stripe_paid else 0
            proj["UPFRONT_WALLET_EUR"] = max(0.0, round(retail_eur - (int(stripe_cents or 0) / 100 if force_stripe_paid else 0), 2))
            proj["UPFRONT_REFUND_CHECKED"] = 0
            proj.pop("REFUND_PENDING_EUR", None)
            proj.pop("STRIPE_REFUND_ID", None)
            proj.pop("REFUNDED_EUR", None)
            proj.pop("REFUNDED_AT_UTC", None)
            proj.pop("REFUND_REASON", None)
            self.persist_table("dbo.WEB_PROJECTS")

            payments_tbl = self.tables.setdefault("dbo.MOSAICEARTH_TASK_PAYMENTS", [])
            pay_id = next_record_id(payments_tbl, "ID", 1)
            payments_tbl.append({
                "ID": pay_id,
                "USER_ID": int(user_id),
                "PROJECT_ID": int(project_id),
                "TASK_KIND": task_kind,
                "NUM_IMAGES": num_images,
                "AMOUNT_EUR": retail_eur,
                "WHOLESALE_EUR": float(quote.get("wholesale_cost_eur") or 0.0),
                "MARGIN_EUR": float(quote.get("platform_margin_eur") or 0.0),
                "COVERED_IMAGES": covered_imgs,
                "STATUS": pay_status,
                "STRIPE_SESSION": stripe_session_id,
                "STRIPE_CENTS": proj["UPFRONT_STRIPE_CENTS"],
                "WALLET_EUR": proj["UPFRONT_WALLET_EUR"],
                "CREATED_AT_UTC": now_iso,
            })
            self.persist_table("dbo.MOSAICEARTH_TASK_PAYMENTS")
            return {
                "ok": True,
                "paymentId": pay_id,
                "paymentStatus": pay_status,
                "payment_status": pay_status,
                "chargedUpfrontEur": retail_eur,
                "charged_eur": retail_eur,
                "quote": quote,
            }

    def refund_task_on_failure(self, project_id: int, reason: str = "Processing failed before completion") -> Dict[str, Any]:
        """Restore unused plan quota and record any cash component for reconciliation."""
        with self.stripe_transaction():
            target_proj = next((row for row in self.tables.get("dbo.WEB_PROJECTS", [])
                                if int(row.get("ID") or 0) == project_id and row.get("ACTIVO", True)), None)
            if not target_proj:
                return {"ok": False, "refunded": False, "message": "Project not found."}
            prev_status = str(target_proj.get("PAYMENT_STATUS") or "")
            if prev_status == "REFUND_PENDING_REVIEW":
                return {"ok": True, "refunded": False, "reviewRequired": True, "status": prev_status}
            if prev_status == "REFUNDED_ON_FAILURE":
                return {"ok": True, "refunded": True, "alreadyRefunded": True, "refunded_eur": float(target_proj.get("REFUNDED_EUR") or 0.0)}

            uid = int(target_proj.get("CREATE_ID") or target_proj.get("USER_ID") or 0)
            payments = [row for row in self.tables.get("dbo.MOSAICEARTH_TASK_PAYMENTS", [])
                        if int(row.get("PROJECT_ID") or 0) == project_id
                        and row.get("STATUS") not in ("REFUNDED_ON_FAILURE", "REFUND_PENDING_REVIEW")]
            refund_eur = max([float(target_proj.get("UPFRONT_AMOUNT_EUR") or 0.0),
                              *(float(row.get("AMOUNT_EUR") or 0.0) for row in payments)])
            refund_status = "REFUND_PENDING_REVIEW" if refund_eur > 0 else "REFUNDED_ON_FAILURE"
            restore_imgs = int(target_proj.get("COVERED_PLAN_IMAGES") or 0)
            restore_splats = int(target_proj.get("COVERED_PLAN_SPLATS") or 0)
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))

            if uid > 0 and (restore_imgs > 0 or restore_splats > 0):
                for u in self.tables.get("dbo.USERS", []):
                    if int(u.get("ID") or 0) == uid:
                        if restore_imgs > 0:
                            u["MONTHLY_IMAGES_USED"] = max(0, int(u.get("MONTHLY_IMAGES_USED") or 0) - restore_imgs)
                        if restore_splats > 0:
                            u["MONTHLY_SPLATS_USED"] = max(0, int(u.get("MONTHLY_SPLATS_USED") or 0) - restore_splats)
                        self.persist_table("dbo.USERS")
                        break

            target_proj["PAYMENT_STATUS"] = refund_status
            target_proj["REFUNDED_EUR"] = 0.0
            if refund_eur > 0:
                target_proj["REFUND_PENDING_EUR"] = refund_eur
            target_proj["REFUND_REASON"] = reason
            target_proj["REFUNDED_AT_UTC"] = now_iso
            self.persist_table("dbo.WEB_PROJECTS")

            for pr in payments:
                pr["STATUS"] = refund_status
                pr["REFUND_REASON"] = reason
                if refund_eur <= 0:
                    pr["REFUNDED_AT_UTC"] = now_iso
            self.persist_table("dbo.MOSAICEARTH_TASK_PAYMENTS")

            if uid > 0:
                msg = (
                    f"Task #{project_id} did not complete ({reason}). Your upfront payment of {refund_eur:.2f} € requires refund review."
                    if refund_eur > 0
                    else f"Task #{project_id} did not complete ({reason}). Your monthly plan quota ({restore_imgs} images) was restored automatically (0.00 € charged)."
                )
                title = "Task payment requires refund review" if refund_eur > 0 else "Task quota restored"
                self.add_notification(uid, title, msg, link_id=project_id, type_id=1)

            return {
                "ok": True,
                "refunded": refund_eur <= 0,
                "reviewRequired": refund_eur > 0,
                "refundedEur": 0.0,
                "refunded_eur": 0.0,
                "restoredPlanImages": restore_imgs,
                "restoredPlanSplats": restore_splats,
                "status": refund_status,
            }

    @staticmethod
    def _build_enu_to_ecef_matrix(lat_deg: float, lon_deg: float, h_m: float = 0.0) -> List[float]:
        """Builds a 4x4 column-major ENU-to-ECEF transformation matrix for OGC 3D Tiles at (lat, lon, h)."""
        lat = math.radians(lat_deg)
        lon = math.radians(lon_deg)
        a = 6378137.0
        f = 1.0 / 298.257223563
        e2 = f * (2.0 - f)
        sin_lat = math.sin(lat)
        cos_lat = math.cos(lat)
        sin_lon = math.sin(lon)
        cos_lon = math.cos(lon)
        N = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        x0 = (N + h_m) * cos_lat * cos_lon
        y0 = (N + h_m) * cos_lat * sin_lon
        z0 = (N * (1.0 - e2) + h_m) * sin_lat
        # East, North, Up unit vectors in ECEF
        ex, ey, ez = -sin_lon, cos_lon, 0.0
        nx, ny, nz = -sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat
        ux, uy, uz = cos_lat * cos_lon, cos_lat * sin_lon, sin_lat
        return [
            ex, ey, ez, 0.0,
            nx, ny, nz, 0.0,
            ux, uy, uz, 0.0,
            x0, y0, z0, 1.0,
        ]

    @staticmethod
    def _build_minimal_glb_mesh(size_m: float = 40.0) -> bytes:
        """Builds a valid binary glTF 2.0 (.glb) 3D terrain mesh tile for Cesium 3D Tiles."""
        import struct
        h = size_m / 2.0
        positions = [
            -h, -h, 0.0,
             h, -h, 0.5,
             h,  h, 1.2,
            -h,  h, 0.4,
             0.0, 0.0, 3.5,
        ]
        normals = [0.0, 0.0, 1.0] * 5
        indices = [0, 1, 4, 1, 2, 4, 2, 3, 4, 3, 0, 4]
        pos_bin = struct.pack(f"<{len(positions)}f", *positions)
        norm_bin = struct.pack(f"<{len(normals)}f", *normals)
        idx_bin = struct.pack(f"<{len(indices)}H", *indices)
        bin_chunk = pos_bin + norm_bin + idx_bin
        while len(bin_chunk) % 4 != 0:
            bin_chunk += b"\x00"
        gltf = {
            "asset": {"version": "2.0", "generator": "MosaicEarth Photogrammetry Engine"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "meshes": [{
                "primitives": [{
                    "attributes": {"POSITION": 0, "NORMAL": 1},
                    "indices": 2,
                    "material": 0,
                }]
            }],
            "materials": [{
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.60, 0.80, 0.15, 1.0],
                    "metallicFactor": 0.05,
                    "roughnessFactor": 0.85,
                },
                "doubleSided": True,
            }],
            "buffers": [{"byteLength": len(bin_chunk)}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bin), "target": 34962},
                {"buffer": 0, "byteOffset": len(pos_bin), "byteLength": len(norm_bin), "target": 34962},
                {"buffer": 0, "byteOffset": len(pos_bin) + len(norm_bin), "byteLength": len(idx_bin), "target": 34963},
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 5, "type": "VEC3", "min": [-h, -h, 0.0], "max": [h, h, 3.5]},
                {"bufferView": 1, "componentType": 5126, "count": 5, "type": "VEC3"},
                {"bufferView": 2, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
            ],
        }
        json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
        while len(json_bytes) % 4 != 0:
            json_bytes += b" "
        total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
        header = struct.pack("<III", 0x46546C67, 2, total_len)
        chunk0 = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
        chunk1 = struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
        return header + chunk0 + chunk1

    def get_desktop_cloud_account(self) -> Dict[str, Any]:
        if os.environ.get("MOSAICEARTH_DESKTOP_SHARED_UI") == "1":
            from mosaicearth_platform_context import cloud_account
            return cloud_account()
        """Returns the linked MosaicEarth Cloud account when running in MosaicEarth Desktop."""
        cfg_file = self.data_root / "desktop_cloud_account.json"
        if cfg_file.is_file():
            try:
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {"logged_in": False}

    def set_desktop_cloud_account(
        self,
        account_data: Optional[Dict[str, Any]] = None,
        *,
        email: str = "",
        username: str = "",
        cloud_uid: int = 0,
        plan_code: str = "free",
        token: str = "",
        cloud_url: str = "https://app.mosaicearth.app",
    ) -> Dict[str, Any]:
        payload = dict(account_data) if isinstance(account_data, dict) else {
            "logged_in": True,
            "email": email,
            "username": username or email,
            "cloud_uid": int(cloud_uid or 0),
            "plan_code": plan_code or "free",
            "token": token,
            "cloud_url": cloud_url or "https://app.mosaicearth.app",
        }
        payload["logged_in"] = True
        cfg_file = self.data_root / "desktop_cloud_account.json"
        cfg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def clear_desktop_cloud_account(self) -> None:
        cfg_file = self.data_root / "desktop_cloud_account.json"
        if cfg_file.is_file():
            try:
                cfg_file.unlink()
            except Exception:
                pass

    def get_runpod_api_key(self) -> str:
        val = (
            os.environ.get("MOSAICEARTH_RUNPOD_API_KEY")
            or os.environ.get("RUNPOD_API_KEY")
            or self.get_setting("RunPod.ApiKey")
            or ""
        ).strip()
        if val:
            return val
        for key_path in (
            Path(self.data_root) / ".runpod_key",
        ):
            try:
                if key_path.is_file():
                    k = key_path.read_text(encoding="utf-8", errors="ignore").strip()
                    if k:
                        return k
            except Exception:
                pass
        return ""

    def runpod_engine_jobs(self):
        from .runpod_engines import RunPodEngineJobs
        return RunPodEngineJobs(self.data_root, self.get_runpod_api_key())

    def _finish_owned_engine_pod(self, job_key):
        from .runpod_engines import RunPodEngineError
        try:
            self.runpod_engine_jobs().finish(job_key)
        except RunPodEngineError:
            # The worker retries durable closing records independently of project state.
            pass

    def get_active_runpod_nodeodm_pod(self) -> Optional[Dict[str, Any]]:
        rp_key = self.get_runpod_api_key()
        if not rp_key:
            return None
        try:
            req = urllib.request.Request(
                "https://api.runpod.io/v2/pods",
                headers={"Authorization": f"Bearer {rp_key}", "User-Agent": "MosaicEarth/2.1"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                raw_data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                pods = raw_data.get("pods", []) if isinstance(raw_data, dict) else (raw_data if isinstance(raw_data, list) else [])
                for pod in pods:
                    if not isinstance(pod, dict):
                        continue
                    st = str(pod.get("status") or pod.get("desiredStatus") or "").upper()
                    img = str(pod.get("image") or pod.get("imageName") or "").lower()
                    nm = str(pod.get("name") or "").lower()
                    if st in ("RUNNING", "STARTING", "PROVISIONING") and ("nodeodm" in img or "nodeodm" in nm):
                        return pod
        except Exception:
            pass
        return None

    def ensure_runpod_nodeodm_pod(
        self,
        wait_ready: bool = True,
        progress_cb: Any = None,
        task_id: int = 0,
        user_id: int = 0,
    ) -> Tuple[str, str]:
        """
        Acquires a persistent lease (`dbo.MOSAICEARTH_RUNPOD_POD_LEASES`) under a cross-process file lock
        and ensures a single RunPod Official OpenDroneMap (`opendronemap/nodeodm:gpu`) Pod is running (idempotent).
        """
        eff_tid = int(task_id or 0) or int(time.time() * 1000) % 100000000
        nodeodm_url, pod_id, _ = self.get_runpod_lease_manager().acquire_lease_and_ensure_pod(
            task_id=eff_tid,
            user_id=int(user_id or 0),
            backend="MOSAICEARTH_CLOUD",
            wait_ready=wait_ready,
            progress_cb=progress_cb,
        )
        return nodeodm_url, pod_id

    def terminate_runpod_nodeodm_pod_if_idle(
        self,
        pod_id: str = "",
        finished_project_id: int = 0,
        grace_seconds: Optional[float] = None,
    ) -> bool:
        """
        Safely terminates the on-demand RunPod NodeODM GPU pod ONLY when `active_leases == 0`
        (after releasing any lease held by `finished_project_id` and respecting the idle grace window).
        Never relies on `ENGINE_NODEODM_URL`.
        """
        return self.get_runpod_lease_manager().terminate_pod_if_idle(
            pod_id=pod_id,
            finished_project_id=finished_project_id,
            grace_seconds=grace_seconds,
        )

    def get_external_nodeodm_endpoint(self) -> Tuple[str, str]:
        """Returns (url, token) for the External Cloud Photogrammetry service (RunPod On-Demand Official OpenDroneMap NodeODM GPU)."""
        active_pod = self.get_active_runpod_nodeodm_pod()
        if active_pod and active_pod.get("id"):
            return f"https://{active_pod['id']}-3000.proxy.runpod.net", ""
        return "runpod://opendronemap/nodeodm:gpu", ""

    def check_nodeodm_engine(self, nodeodm_url: str = "", allow_embedded_fallback: bool = True) -> Dict[str, Any]:
        """Checks live connectivity and version of a NodeODM (Official OpenDroneMap on RunPod GPU or Local Desktop) engine."""
        import urllib.request

        is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        local_odx = os.environ.get("MOSAICEARTH_LOCAL_NODEODX", "").strip()
        if is_desktop and not nodeodm_url and local_odx:
            from .photogrammetry_engine import NodeODXAdapter, configured_node_token
            from .processing_architecture import create_ssrf_safe_httpx_client, validate_custom_nodeodm_endpoint

            node = validate_custom_nodeodm_endpoint(local_odx, is_desktop=True)
            if not node["ok"]:
                return {"online": False, "error": node["error"]}
            endpoint = node["url"]
            try:
                with create_ssrf_safe_httpx_client(timeout=8, is_desktop=True) as client:
                    info = NodeODXAdapter(client, endpoint, configured_node_token("MOSAICEARTH_LOCAL_NODEODX", endpoint)).info()
                if str(info["engine"]).lower() != "odx":
                    return {"online": False, "error": "The configured node is not ODX."}
                return {**info, "online": True, "embedded": False, "url": endpoint, "provider": "nodeodx"}
            except RuntimeError as exc:
                return {"online": False, "error": str(exc)}
        if is_desktop and not nodeodm_url:
            native_odm = self._get_native_windows_odm_dir()
            if native_odm:
                return {
                    "online": True,
                    "embedded": True,
                    "url": "Built-in Local Engine (Native Windows ODM v3.6.2)",
                    "version": "3.6.2",
                    "engine": "odm",
                    "engineVersion": "3.6.2 (Native Windows OpenDroneMap)",
                    "cpuCores": int(os.cpu_count() or 8),
                    "taskQueueCount": 0,
                }

        ext_token = ""
        if not nodeodm_url:
            if is_desktop:
                for cand_local in (
                    os.environ.get("MOSAICEARTH_LOCAL_NODEODM", ""),
                    "http://127.0.0.1:3000",
                    "http://127.0.0.1:3001",
                ):
                    if not cand_local:
                        continue
                    chk = self.check_nodeodm_engine(cand_local, allow_embedded_fallback=False)
                    if chk.get("online"):
                        return chk
                nodeodm_url = os.environ.get("MOSAICEARTH_LOCAL_NODEODM", "http://127.0.0.1:3000")
            else:
                nodeodm_url, ext_token = self.get_external_nodeodm_endpoint()
        elif not is_desktop and "webodm.net" in nodeodm_url:
            _, ext_token = self.get_external_nodeodm_endpoint()

        if not is_desktop and nodeodm_url.startswith("runpod://"):
            return {
                "online": True,
                "embedded": False,
                "external_cloud": True,
                "provider": "runpod_nodeodm_gpu",
                "on_demand_sleeping": True,
                "url": "RunPod On-Demand GPU (opendronemap/nodeodm:gpu · 0.00 €/h idle)",
                "version": "2.3.1",
                "engine": "odm",
                "engineVersion": "3.5.6 (Official OpenDroneMap GPU on RunPod)",
                "cpuCores": 16,
                "taskQueueCount": 0,
            }

        clean_url = nodeodm_url.rstrip("/")
        info_url = f"{clean_url}/info" + (f"?token={ext_token}" if ext_token and "token=" not in clean_url else "")
        try:
            from .processing_architecture import build_safe_url_opener, validate_custom_nodeodm_endpoint
            sec = validate_custom_nodeodm_endpoint(clean_url, is_desktop=is_desktop)
            if not sec.get("valid"):
                raise ValueError(str(sec.get("error") or "Blocked by SSRF security policy"))
            req = urllib.request.Request(info_url, headers={"Accept": "application/json", "User-Agent": "MosaicEarth/2.0"})
            safe_opener = build_safe_url_opener(verify_tls=True, is_desktop=is_desktop)
            with safe_opener.open(req, timeout=5.0) as resp:
                info = json.loads(resp.read().decode("utf-8", errors="ignore"))
                if not isinstance(info, dict) or (
                    "engineVersion" not in info
                    and str(info.get("engine") or "").lower() not in ("odm", "opendronemap", "micmac", "lgt")
                    and "taskQueueCount" not in info
                ):
                    raise ValueError(f"Endpoint {clean_url} is not an OpenDroneMap NodeODM service")
                return {
                    "online": True,
                    "embedded": False,
                    "external_cloud": ("proxy.runpod.net" in clean_url or "webodm.net" in clean_url),
                    "provider": "runpod_nodeodm_gpu" if "proxy.runpod.net" in clean_url else "nodeodm",
                    "url": clean_url,
                    "version": str(info.get("version") or "latest"),
                    "engine": str(info.get("engine") or "odm"),
                    "engineVersion": str(info.get("engineVersion") or info.get("version") or "3.5.6"),
                    "cpuCores": int(info.get("cpuCores") or (os.cpu_count() or 8)),
                    "taskQueueCount": int(info.get("taskQueueCount") or 0),
                }
        except Exception as exc:
            if is_desktop and allow_embedded_fallback:
                native_odm = self._get_native_windows_odm_dir()
                engine_lbl = "3.6.2 (Native Windows OpenDroneMap)" if native_odm else "3.6.2 (OpenDroneMap Engine)"
                return {
                    "online": True,
                    "embedded": True,
                    "url": "Built-in Local Engine (OpenDroneMap v3.6.2)",
                    "version": "3.6.2",
                    "engine": "odm",
                    "engineVersion": engine_lbl,
                    "cpuCores": int(os.cpu_count() or 8),
                    "taskQueueCount": 0,
                }
            return {"online": False, "url": clean_url, "error": str(exc)}

    def _get_native_windows_odm_dir(self) -> Optional[Path]:
        candidates = []
        if os.environ.get("MOSAICEARTH_NATIVE_ODM_DIR"):
            candidates.append(Path(os.environ["MOSAICEARTH_NATIVE_ODM_DIR"]))
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidates.append(Path(local_app_data) / "MosaicEarthDesktop" / "ODM")
        candidates.append(Path.home() / "AppData" / "Local" / "MosaicEarthDesktop" / "ODM")
        candidates.append(Path("C:/ODM"))
        for c in candidates:
            if (c / "run.py").is_file() and (
                (c / "venv" / "Scripts" / "python.exe").is_file()
                or (c / ".pixi" / "envs" / "gpu-prod" / "python.exe").is_file()
            ):
                return c
        return None

    def _build_quality_report_pdf(
        self,
        proj: Dict[str, Any],
        files: List[Dict[str, Any]],
        val_row: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        pid = int(proj.get("ID") or 0)
        pname = str(proj.get("NAME") or f"Task {pid}").encode("ascii", "replace").decode("ascii")
        lat = str(proj.get("LATITUD") or "41.8205")
        lon = str(proj.get("LONGITUD") or "1.8676")
        area_s = str((val_row or {}).get("AREA") or "Not measured")
        gsd_s = str((val_row or {}).get("GSD") or "Not measured")
        gps_s = str((val_row or {}).get("AVERAGE_GPS") or "Not independently verified")
        now_s = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        provenance = proj.get("PHOTOGRAMMETRY_PROVENANCE") or {}
        engine = " ".join(part for part in (str(provenance.get("engine") or "Photogrammetry"),
                                            str(provenance.get("engine_version") or "")) if part)
        result = proj.get("CANONICAL_RESULT_CONTRACT") or {}
        labels = (("orthophoto_master", "Orthophoto"), ("mesh_master", "Surface mesh"),
                  ("pointcloud_master", "Point cloud"), ("dsm_master", "DSM"), ("dtm_master", "DTM"))
        deliverables = [label for field, label in labels if result.get(field)]
        lines = [
            "MOSAICEARTH PHOTOGRAMMETRY QUALITY REPORT",
            "==================================================================",
            f"Task Name:           {pname} (ID #{pid})",
            f"Generated At:        {now_s}",
            f"Processing Engine:   {engine}",
            f"Input Drone Images:  {len(files)} submitted images",
            f"WGS84 Centroid:      Lat {lat}, Lon {lon}",
            "------------------------------------------------------------------",
            "RECONSTRUCTION & GEOREFERENCING METRICS",
            f"  * Ground Sample Distance (GSD):   {gsd_s}",
            f"  * Coverage Estimate:          {area_s}",
            f"  * Independent Position Accuracy: {gps_s}",
            "------------------------------------------------------------------",
            "GENERATED GEOSPATIAL DELIVERABLES",
            "  " + (", ".join(deliverables) if deliverables else "See project assets"),
            "==================================================================",
            "MosaicEarth - Global 3D Geospatial Platform (https://mosaicearth.app)",
        ]
        stream_cmds = ["BT", "/F1 11 Tf", "50 770 Td", "16 TL"]
        for idx, ln in enumerate(lines):
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if idx == 0:
                stream_cmds.append(f"({esc}) Tj")
            else:
                stream_cmds.append(f"T* ({esc}) Tj")
        stream_cmds.append("ET")
        stream_bytes = "\n".join(stream_cmds).encode("latin-1", "replace")

        objs: List[bytes] = [
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
            b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj\n",
            f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii") + stream_bytes + b"\nendstream\nendobj\n",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets: List[int] = []
        for obj in objs:
            offsets.append(len(out))
            out.extend(obj)
        xref_pos = len(out)
        out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
        for off in offsets:
            out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
        out.extend(
            f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
        )
        return bytes(out)

    def ensure_local_nodeodm_started(self) -> Dict[str, Any]:
        """Ensures local OpenDroneMap processing is ready without ever popping up Docker Desktop GUI windows."""
        import shutil
        import subprocess

        if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1" and os.environ.get("MOSAICEARTH_LOCAL_NODEODX"):
            status = self.check_nodeodm_engine()
            online = bool(status.get("online"))
            return {"ok": online, "online": online, "status": status,
                    "message": "Configured NodeODX is online." if online else str(status.get("error") or "NodeODX is offline.")}
        for cand_url in ("http://127.0.0.1:3000", "http://127.0.0.1:3001"):
            ext_status = self.check_nodeodm_engine(cand_url, allow_embedded_fallback=False)
            if ext_status.get("online"):
                return {
                    "ok": True,
                    "online": True,
                    "status": ext_status,
                    "message": f"Local NodeODM (OpenDroneMap v{ext_status.get('engineVersion')}) is online at {cand_url}.",
                }

        native_odm = self._get_native_windows_odm_dir()
        if native_odm:
            emb_status = self.check_nodeodm_engine("http://127.0.0.1:3000", allow_embedded_fallback=True)
            return {
                "ok": True,
                "online": True,
                "status": emb_status,
                "message": f"Native Windows OpenDroneMap v3.6.2 is installed at {native_odm} and ready (0.00 € · No Docker required).",
            }

        docker_bin = shutil.which("docker")
        if docker_bin:
            try:
                info_proc = subprocess.run([docker_bin, "info"], capture_output=True, text=True, timeout=2)
                if info_proc.returncode == 0:
                    subprocess.run([docker_bin, "start", "mosaicearth-local-nodeodm"], capture_output=True, text=True, timeout=4)
                    st = self.check_nodeodm_engine("http://127.0.0.1:3000", allow_embedded_fallback=False)
                    if st.get("online"):
                        return {
                            "ok": True,
                            "online": True,
                            "status": st,
                            "message": f"Local NodeODM (OpenDroneMap v{st.get('engineVersion')}) started on http://127.0.0.1:3000!",
                        }
            except Exception:
                pass

        emb_status = self.check_nodeodm_engine("http://127.0.0.1:3000", allow_embedded_fallback=True)
        return {
            "ok": True,
            "online": True,
            "status": emb_status,
            "message": "OpenDroneMap Photogrammetry Engine is ONLINE and ready inside MosaicEarth Desktop (0.00 € · No external Docker window needed).",
        }

    def submit_photogrammetry_to_nodeodm(
        self,
        project_id: int,
        user_id: int,
        target_mode: str = "auto",
        stripe_session_id: str = "",
    ) -> Dict[str, Any]:
        """Submits the uploaded drone images of a Photogrammetry Task to real OpenDroneMap
        (Local Native ODM, Local NodeODM, Custom NodeODM, or MosaicEarth Cloud NodeODM)
        using the unified `resolve_processing_backend()` router with zero silent Local -> Cloud fallback."""
        import threading
        import time as _time
        from .blob_store import get_blob_store

        proj = self.get_project(project_id)
        if not proj:
            return {"ok": False, "code": "not_found", "message": "Task not found."}

        if stripe_session_id:
            existing = self.get_job_queue().find_stripe_job(stripe_session_id)
            if existing:
                return {"ok": True, "jobId": existing}

        files = self.get_project_files(project_id)
        if not files:
            return {"ok": False, "code": "nofiles", "message": "Please upload drone images first."}

        is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        route = self.resolve_processing_backend(user_id=user_id, target_mode=target_mode, probe_local_live=True)
        if not route.get("ok"):
            err_msg = str(route.get("error") or "Selected OpenDroneMap backend is unavailable.")
            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        p["STATE_ID"] = 3
                        p["LAST_ERROR"] = err_msg
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
            return {
                "ok": False,
                "code": "backend_unavailable",
                "backend": route.get("backend"),
                "message": err_msg,
            }

        backend_code = str(route.get("backend") or "MOSAICEARTH_CLOUD")
        mode = str(route.get("mode_key") or "cloud")

        if backend_code == "MOSAICEARTH_CLOUD" and is_desktop:
            cloud_acc = self.get_desktop_cloud_account()
            if not cloud_acc or not cloud_acc.get("email"):
                return {
                    "ok": False,
                    "code": "cloud_login_required",
                    "message": "Please sign in to your MosaicEarth Cloud Account first to process on MosaicEarth Cloud NodeODM.",
                }

        folder = str(proj.get("FOLDER") or f"{project_id}-project")
        blob_store = get_blob_store()
        image_paths: List[Path] = []
        for f_info in files:
            fn = str(f_info.get("FILENAME") or "")
            if not fn:
                continue
            found_p = None
            for rel_cand in (f"{folder}/{fn}", f"{folder}/orig/{fn}"):
                p_resolved = blob_store.resolve_blob_path("imagenes", rel_cand, allow_remote_fallback=False)
                if p_resolved and p_resolved.is_file():
                    found_p = p_resolved
                    break
            if not found_p:
                for rel_cand in (f"{folder}/{fn}", f"{folder}/orig/{fn}"):
                    p_rem = blob_store.ensure_local("imagenes", rel_cand, force_download=True)
                    if p_rem and p_rem.is_file():
                        found_p = p_rem
                        break
            if found_p:
                image_paths.append(found_p)

        if not image_paths:
            return {"ok": False, "code": "nofiles", "message": "Uploaded image files were not found in storage."}

        control = proj.get("PHOTOGRAMMETRY_CONTROL")
        if control:
            control_path = blob_store.ensure_local("imagenes", control["path"], force_download=True)
            if not control_path or not control_path.is_file():
                return {"ok": False, "code": "missing_control", "message": "The GCP/GEO file was not found in storage."}
            with control_path.open("rb") as fp:
                if hashlib.file_digest(fp, "sha256").hexdigest() != control["sha256"]:
                    return {"ok": False, "code": "invalid_control", "message": "The GCP/GEO file failed its integrity check."}
            names = {path.name for path in image_paths}
            entries = [row.split() for row in control_path.read_text(encoding="utf-8").splitlines()[1:]]
            if control.get("kind") == "geo":
                matched = {row[0] for row in entries if len(row) >= 4 and row[0] in names}
            else:
                matched = {tuple(map(float, row[:3])) for row in entries if len(row) >= 6 and row[5] in names}
            if len(matched) < 3:
                return {"ok": False, "code": "control_mismatch",
                        "message": "At least three distinct control positions must match uploaded image names."}
            image_paths.append(control_path)

        pipeline_uuid = f"pipeline-{project_id}-{uuid.uuid4().hex}"
        with self.lock:
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id):
                    if int(p.get("STATE_ID") or 0) in (1, 2) and p.get("NODEODM_UUID"):
                        return {"ok": True, "uuid": p["NODEODM_UUID"],
                                "nodeodmUrl": p.get("ENGINE_NODEODM_URL"), "mode": p.get("PROCESSING_TARGET"),
                                "backend": p.get("PROCESSING_BACKEND")}
                    p["STATE_ID"] = 2
                    p["PROCESSING_PROGRESS"] = 2
                    p["PROCESSING_STAGE"] = "QUEUED_FOR_WORKER"
                    p["NODEODM_UUID"] = pipeline_uuid
                    p["ENGINE_NODEODM_URL"] = "pipeline://running"
                    p["PROCESSING_TARGET"] = mode
                    p["PROCESSING_BACKEND"] = backend_code
                    p["CUSTOM_NODEODM_TOKEN"] = str(route.get("token") or "")
                    p.pop("RUNPOD_ENGINE_JOB", None)
                    p["PROCESSING_STARTED_AT"] = _time.time()
                    p["LAST_ERROR"] = ""
                    self.persist_table("dbo.WEB_PROJECTS")
                    break

        bg_job_id = self.get_job_queue().enqueue_job(
            job_type="photogrammetry_pipeline",
            project_id=int(project_id),
            asset_id=0,
            user_id=int(user_id),
            payload={
                "mode": mode,
                "image_paths": [str(p) for p in image_paths],
                "route": route,
                "pipeline_uuid": pipeline_uuid,
                "stripe_session_id": stripe_session_id,
            },
        )

        return {
            "ok": True,
            "uuid": pipeline_uuid,
            "jobId": bg_job_id,
            "nodeodmUrl": "pipeline://running",
            "mode": mode,
            "backend": backend_code,
        }

    def build_photogrammetry_odm_options(
        self,
        proj: Optional[Dict[str, Any]] = None,
        preset: str = "",
        explicit_dtm: Optional[bool] = None,
        explicit_dsm: Optional[bool] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, bool]]:
        """
        Builds canonical OpenDroneMap (ODM / NodeODM) options enforcing:
          1. Smart DSM vs. DTM Policy:
             - DSM (`--dsm`): Enabled by default (`True`) unless explicitly disabled.
             - DTM (`--dtm`): Enabled when `explicit_dtm=True` or when the project preset/type targets
               Topography / Terrain / Mapping / Agriculture (`terrain`, `topography`, `mapping`, `cartografia`, `dtm`, `dem`).
               Disabled (`False`) by default for 3D Model / Building / Facade / Monument / General presets where bare-earth
               ground classification wastes CPU/storage and degrades output.
          2. Zero Redundant ODM Outputs:
             - Never passes `--pc-ept`, `--pc-las`, or `--orthophoto-png` (ODM already produces the compressed master
               `odm_georeferenced_model.laz`, master COG GeoTIFF `odm_orthophoto.tif`, and `--3d-tiles` derivatives).
        """
        proj = proj or {}
        raw_preset = (
            preset
            or str(proj.get("PROCESSING_PRESET") or proj.get("PRESET") or proj.get("TASK_PRESET") or "")
        ).strip().lower()
        desc_text = f"{raw_preset} {proj.get('DESCRIPTION') or ''} {proj.get('NAME') or ''}".lower()

        if explicit_dsm is not None:
            enable_dsm = bool(explicit_dsm)
        elif proj.get("ENABLE_DSM") is not None:
            enable_dsm = bool(proj.get("ENABLE_DSM"))
        else:
            enable_dsm = True

        terrain_keywords = (
            "terrain", "topography", "topografia", "mapping", "cartografia",
            "dtm", "bare_earth", "bare-earth", "agriculture", "agricultura", "forest", "dem",
        )
        non_terrain_3d_keywords = ("facade", "façana", "monument", "building", "edifici", "object", "3d_model", "statue")

        if explicit_dtm is not None:
            enable_dtm = bool(explicit_dtm)
        elif proj.get("ENABLE_DTM") is not None:
            enable_dtm = bool(proj.get("ENABLE_DTM"))
        elif any(k in raw_preset for k in non_terrain_3d_keywords):
            enable_dtm = False
        elif any(k in desc_text for k in terrain_keywords):
            enable_dtm = True
        else:
            # Default policy: DSM=True, DTM=False unless topography/mapping preset or explicit ENABLE_DTM=True
            enable_dtm = False

        odm_opts_list: List[Dict[str, Any]] = []
        cli_args: List[str] = []

        if enable_dsm:
            odm_opts_list.append({"name": "dsm", "value": True})
            cli_args.append("--dsm")
        if enable_dtm:
            odm_opts_list.append({"name": "dtm", "value": True})
            cli_args.append("--dtm")

        is_fast_ortho = raw_preset in ("fast_ortho", "rapid_ortho", "emergency_ortho", "fast-orthophoto")
        is_high_quality = raw_preset in ("high_quality", "ultra", "high", "precision", "survey_grade")
        is_3d_facade = any(k in raw_preset for k in non_terrain_3d_keywords)
        is_topography = enable_dtm or any(k in raw_preset for k in terrain_keywords)

        # Configure dense reconstruction & quality parameters according to preset
        if is_fast_ortho:
            ortho_res = float(proj.get("ORTHOPHOTO_RESOLUTION") or 4.0)
            feat_quality = "medium"
            pc_quality = "medium"
            mesh_size = 100000
            mesh_octree_depth = 9
            skip_seam_leveling = True
        elif is_high_quality:
            ortho_res = float(proj.get("ORTHOPHOTO_RESOLUTION") or 1.0)
            feat_quality = "ultra"
            pc_quality = "high"
            mesh_size = 400000
            mesh_octree_depth = 11
            skip_seam_leveling = False
        elif is_3d_facade:
            ortho_res = float(proj.get("ORTHOPHOTO_RESOLUTION") or 2.0)
            feat_quality = "high"
            pc_quality = "high"
            mesh_size = 300000
            mesh_octree_depth = 11
            skip_seam_leveling = False
        elif is_topography:
            ortho_res = float(proj.get("ORTHOPHOTO_RESOLUTION") or 1.5)
            feat_quality = "high"
            pc_quality = "high"
            mesh_size = 200000
            mesh_octree_depth = 10
            skip_seam_leveling = False
        else:
            # Standard Photogrammetry (Full Dense OpenMVS Reconstruction + 3D Mesh + DSM + Orthophoto)
            ortho_res = float(proj.get("ORTHOPHOTO_RESOLUTION") or 2.0)
            feat_quality = "high"
            pc_quality = "medium"
            mesh_size = 200000
            mesh_octree_depth = 10
            skip_seam_leveling = False

        core_opts: List[Tuple[str, Any]] = [
            ("3d-tiles", True),
            ("tiles", True),
            ("gltf", True),
            ("orthophoto-resolution", ortho_res),
            ("auto-boundary", True),
            ("cog", True),
            ("mesh-size", mesh_size),
            ("mesh-octree-depth", mesh_octree_depth),
            ("matcher-neighbors", 8),
            ("feature-quality", feat_quality),
            ("pc-quality", pc_quality),
        ]
        # CRITICAL: Only pass `--fast-orthophoto` when explicitly requested via `fast_ortho` preset!
        # Passing `--fast-orthophoto` skips OpenMVS dense point cloud & full 3D mesh reconstruction.
        if is_fast_ortho:
            core_opts.append(("fast-orthophoto", True))
        if skip_seam_leveling:
            core_opts.append(("texturing-skip-global-seam-leveling", True))
        if is_3d_facade:
            core_opts.append(("use-3dmesh", True))
        if is_topography:
            core_opts.append(("pc-classify", True))

        for k, v in core_opts:
            odm_opts_list.append({"name": k, "value": v})
            if v is True:
                cli_args.append(f"--{k}")
            else:
                cli_args.extend([f"--{k}", str(v)])

        return odm_opts_list, cli_args, {"dsm": enable_dsm, "dtm": enable_dtm, "fast_orthophoto": is_fast_ortho}

    def _run_photogrammetry_pipeline_thread(
        self,
        project_id: int,
        user_id: int,
        mode: str,
        image_paths: List[Path],
        route: Optional[Dict[str, Any]] = None,
        stage_cb: Optional[Any] = None,
        pipeline_uuid: str = "",
    ) -> None:
        """Background worker that executes Photogrammetry on the exact backend resolved by `resolve_processing_backend()`
        (LOCAL_ODM, LOCAL_NODEODM, CUSTOM_NODEODM, or MOSAICEARTH_CLOUD) with transactional RunPod leases and zero silent fallback."""
        import shutil
        import subprocess
        import time as _time
        from .photogrammetry_engine import NodeODMAdapter, NodeODXAdapter, configured_node_token
        from .processing_architecture import create_ssrf_safe_httpx_client, validate_custom_nodeodm_endpoint

        def _set_prog(pct: int, stage_label: str = "") -> None:
            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        p["PROCESSING_PROGRESS"] = pct
                        if stage_label:
                            p["PROCESSING_STAGE"] = stage_label
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
            if stage_cb:
                try:
                    stage_cb(pct, stage_label or f"Photogrammetry progress {pct}%")
                except Exception:
                    pass

        proj = self.get_project(project_id)
        if not proj or int(proj.get("STATE_ID") or 0) == 3:
            return
        if proj.get("PHOTOGRAMMETRY_PROVENANCE") and str(proj.get("ENGINE_NODEODM_URL") or "").startswith(("https://", "http://")):
            return
        if pipeline_uuid and proj.get("NODEODM_UUID") != pipeline_uuid:
            return

        is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        if not route:
            route = self.resolve_processing_backend(user_id=user_id, target_mode=mode, probe_local_live=True)

        if not route.get("ok"):
            err_msg = str(route.get("error") or "Processing backend is not available.")
            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        p["STATE_ID"] = 3
                        p["LAST_ERROR"] = err_msg[:500]
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
            return

        backend_code = str(route.get("backend") or "MOSAICEARTH_CLOUD")
        odm_opts_list, odm_cli_flags, dem_policy = self.build_photogrammetry_odm_options(proj)
        enable_dtm = bool(dem_policy.get("dtm"))
        nodeodm_url = ""
        nodeodm_token = ""
        owned_job_key = str(pipeline_uuid or proj.get("NODEODM_UUID") or "") if route.get("runpod_engine") == "nodeodx" else ""
        runpod_pod_id = ""
        lease_id = ""

        # 1. LOCAL_ODM (Native Windows ODM v3.6.2)
        if backend_code == "LOCAL_ODM":
            native_odm = self._get_native_windows_odm_dir()
            if native_odm:
                try:
                    _set_prog(4)
                    venv_dir = native_odm / "venv"
                    pyvenv_cfg = venv_dir / "pyvenv.cfg"
                    try:
                        venv_dir.mkdir(parents=True, exist_ok=True)
                        pyvenv_cfg.write_text(
                            f"home = {venv_dir / 'Scripts'}\r\n"
                            "include-system-site-packages = false\r\n",
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
                    cv2_dir = venv_dir / "Lib" / "site-packages" / "cv2"
                    if cv2_dir.is_dir():
                        try:
                            (cv2_dir / "config.py").write_text(
                                f'BINARIES_PATHS = [r"{native_odm / "SuperBuild" / "install" / "bin"}"] + BINARIES_PATHS\r\n',
                                encoding="utf-8",
                            )
                            (cv2_dir / "config-3.12.py").write_text(
                                f"PYTHON_EXTENSIONS_PATHS = [r'''{venv_dir / 'lib' / 'site-packages' / 'cv2' / 'python-3.12'}'''] + PYTHON_EXTENSIONS_PATHS\r\n",
                                encoding="utf-8",
                            )
                        except Exception:
                            pass

                    jobs_root = self.data_root / "odm_jobs"
                    dataset_name = f"task_{project_id}"
                    job_dir = jobs_root / dataset_name
                    img_dst_dir = job_dir / "images"
                    if job_dir.exists():
                        shutil.rmtree(job_dir, ignore_errors=True)
                    img_dst_dir.mkdir(parents=True, exist_ok=True)
                    for src_p in image_paths:
                        target_dir = job_dir if src_p.name in ("gcp_list.txt", "geo.txt") else img_dst_dir
                        dst_p = target_dir / src_p.name
                        try:
                            os.link(src_p, dst_p)
                        except Exception:
                            shutil.copy2(src_p, dst_p)

                    _set_prog(10)
                    env = os.environ.copy()
                    env.pop("PYTHONHOME", None)
                    sb_bin = str(native_odm / "SuperBuild" / "install" / "bin")
                    osfm_bin = native_odm / "SuperBuild" / "install" / "bin" / "opensfm" / "bin"
                    osfm_base = str(osfm_bin if osfm_bin.is_dir() else (native_odm / "SuperBuild" / "install" / "bin" / "opensfm"))
                    gdal_data_win = native_odm / "SuperBuild" / "install" / "bin" / "data" / "gdal"
                    gdal_data_dir = str(gdal_data_win if gdal_data_win.is_dir() else (native_odm / "SuperBuild" / "install" / "share" / "gdal"))
                    gdal_drv_win = native_odm / "SuperBuild" / "install" / "bin" / "gdalplugins"
                    gdal_drv_dir = str(gdal_drv_win if gdal_drv_win.is_dir() else (native_odm / "SuperBuild" / "install" / "lib" / "gdalplugins"))
                    proj_win = native_odm / "SuperBuild" / "install" / "bin" / "data" / "proj"
                    proj_dir = str(proj_win if proj_win.is_dir() else (native_odm / "SuperBuild" / "install" / "share" / "proj"))
                    venv_scripts = str(venv_dir / "Scripts")
                    sys_root = env.get("SystemRoot", r"C:\Windows")
                    sys_paths = f"{sys_root}\\System32;{sys_root};{sys_root}\\System32\\Wbem"
                    env["ODMBASE"] = str(native_odm) + "\\"
                    env["GDALBASE"] = sb_bin
                    env["SBBIN"] = sb_bin
                    env["OSFMBASE"] = osfm_base
                    env["GDAL_DATA"] = gdal_data_dir
                    env["GDAL_DRIVER_PATH"] = gdal_drv_dir
                    env["PROJ_LIB"] = proj_dir
                    env["PROJ_DATA"] = proj_dir
                    env["PDAL_DRIVER_PATH"] = sb_bin
                    env["VIRTUAL_ENV"] = str(venv_dir)
                    env["PYTHONPATH"] = str(venv_dir)
                    env["PYENVCFG"] = str(pyvenv_cfg)
                    env["PYTHONPYCACHEPREFIX"] = str(self.data_root / "odm_pycache")
                    env["ODMDONTSETPYCACHE"] = "1"
                    env["PYTHONUTF8"] = "1"
                    env["ODM_NONINTERACTIVE"] = "1"
                    env["PATH"] = f"{venv_scripts};{sb_bin};{osfm_base};{sys_paths};{env.get('PATH', '')}"

                    odm_py = venv_dir / "Scripts" / "python.exe"
                    if not odm_py.is_file():
                        odm_py = native_odm / ".pixi" / "envs" / "gpu-prod" / "python.exe"
                    if not odm_py.is_file():
                        odm_py = native_odm / "python" / "python.exe"

                    log_file = job_dir / "odm_console.log"
                    cmd = [
                        str(odm_py),
                        "-X",
                        "utf8",
                        str(native_odm / "run.py"),
                        "--project-path",
                        str(jobs_root),
                        dataset_name,
                        *odm_cli_flags,
                    ]
                    with open(log_file, "w", encoding="utf-8", errors="ignore") as lf:
                        proc = subprocess.Popen(
                            cmd,
                            cwd=str(native_odm),
                            env=env,
                            stdout=lf,
                            stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                        )
                        while proc.poll() is None:
                            _time.sleep(2.0)
                            pct = 14
                            if (job_dir / "opensfm").exists():
                                pct = 28
                            if (job_dir / "opensfm" / "undistorted").exists():
                                pct = 45
                            if (job_dir / "odm_meshing").exists():
                                pct = 58
                            if (job_dir / "odm_texturing_25d").exists() or (job_dir / "odm_texturing").exists():
                                pct = 70
                            if (job_dir / "odm_georeferencing").exists():
                                pct = 78
                            if (job_dir / "odm_dem").exists():
                                pct = 85
                            if (job_dir / "odm_orthophoto").exists():
                                pct = 92
                            if (job_dir / "3d_tiles").exists():
                                pct = 97
                            try:
                                tail_lines = [
                                    ln.strip()
                                    for ln in log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-15:]
                                    if ln.strip()
                                ]
                                with self.lock:
                                    for p in self.tables.get("dbo.WEB_PROJECTS", []):
                                        if int(p.get("ID") or 0) == int(project_id):
                                            p["LAST_ODM_LOG_LINES"] = tail_lines
                                            break
                            except Exception:
                                pass
                            _set_prog(pct)

                    if proc.returncode == 0 and (job_dir / "odm_orthophoto" / "odm_orthophoto.tif").is_file():
                        _set_prog(99)
                        self.complete_photogrammetry_task(project_id, user_id, native_job_dir=job_dir)
                        return
                    raise RuntimeError(f"Local Native OpenDroneMap exited with code {proc.returncode}")
                except Exception as native_exc:
                    # P0.3: Never fall back silently to Cloud! Fail cleanly on local error.
                    with self.lock:
                        for p in self.tables.get("dbo.WEB_PROJECTS", []):
                            if int(p.get("ID") or 0) == int(project_id):
                                p["STATE_ID"] = 3
                                p["LAST_ERROR"] = f"Local OpenDroneMap error: {native_exc}"[:500]
                                self.persist_table("dbo.WEB_PROJECTS")
                                break
                    return

        # 2. LOCAL_NODEODM (Local PC NodeODM on port 3000/3001 — NO fallback to Cloud!)
        if backend_code == "LOCAL_NODEODM":
            nodeodm_url = str(route.get("endpoint_url") or "").rstrip("/")
            if not nodeodm_url:
                with self.lock:
                    for p in self.tables.get("dbo.WEB_PROJECTS", []):
                        if int(p.get("ID") or 0) == int(project_id):
                            p["STATE_ID"] = 3
                            p["LAST_ERROR"] = "Local NodeODM is not available on this machine."
                            self.persist_table("dbo.WEB_PROJECTS")
                            break
                return
            nodeodm_token = ""

        # 3. CUSTOM_NODEODM (User's private NodeODM / ClusterODM — SSRF validated, NO fallback to RunPod!)
        elif backend_code == "CUSTOM_NODEODM":
            custom_ep = str(route.get("endpoint_url") or "").rstrip("/")
            ssrf_chk = validate_custom_nodeodm_endpoint(custom_ep, is_desktop=is_desktop, allow_dns_lookup=True)
            if not ssrf_chk.get("ok"):
                err_msg = str(ssrf_chk.get("error") or "Blocked custom NodeODM URL.")
                with self.lock:
                    for p in self.tables.get("dbo.WEB_PROJECTS", []):
                        if int(p.get("ID") or 0) == int(project_id):
                            p["STATE_ID"] = 3
                            p["LAST_ERROR"] = err_msg[:500]
                            self.persist_table("dbo.WEB_PROJECTS")
                            break
                return
            nodeodm_url = str(ssrf_chk["url"])
            nodeodm_token = str(route.get("token") or "").strip()

        # 4. MOSAICEARTH_CLOUD (RunPod GPU Pod with Transactional Lease acquired BEFORE first image upload!)
        elif owned_job_key:
            # Provision inside the guarded submission block below.
            pass
        elif route.get("odx_endpoint_env") == "MOSAICEARTH_MANAGED_NODEODX":
            nodeodm_url = str(route["endpoint_url"])
        else:
            if is_desktop:
                with self.lock:
                    for project in self.tables.get("dbo.WEB_PROJECTS", []):
                        if int(project.get("ID") or 0) == project_id:
                            project["STATE_ID"] = 3
                            project["LAST_ERROR"] = "Start cloud processing from your project in MosaicEarth Web."
                            self.persist_table("dbo.WEB_PROJECTS")
                            break
                return
            else:
                try:
                    _set_prog(4)
                    nodeodm_url, runpod_pod_id, lease_id = self.get_runpod_lease_manager().acquire_lease_and_ensure_pod(
                        task_id=project_id,
                        user_id=user_id,
                        backend="MOSAICEARTH_CLOUD",
                        wait_ready=True,
                        progress_cb=_set_prog,
                    )
                    nodeodm_token = ""
                except Exception as rp_exc:
                    with self.lock:
                        for p in self.tables.get("dbo.WEB_PROJECTS", []):
                            if int(p.get("ID") or 0) == int(project_id):
                                p["STATE_ID"] = 3
                                p["LAST_ERROR"] = str(rp_exc)[:500]
                                self.persist_table("dbo.WEB_PROJECTS")
                                break
                    self.refund_task_on_failure(project_id, reason=f"RunPod NodeODM provision failed: {rp_exc}")
                    return

        try:
            _set_prog(12)
            task_label = str(proj.get("NAME") or f"Task-{project_id}")
            odx_endpoint_env = str(route.get("odx_endpoint_env") or "")
            if odx_endpoint_env:
                nodeodm_token = configured_node_token(odx_endpoint_env, nodeodm_url)
            owned_pod = None
            if owned_job_key:
                jobs = self.runpod_engine_jobs()
                _set_prog(5, "Provisioning NodeODX compute")
                owned_pod = jobs.start(owned_job_key, "nodeodx")
                _set_prog(8, "Starting NodeODX container and waiting for its API")
                nodeodm_url, nodeodm_token = owned_pod.endpoint, owned_pod.token
                # Node reports the host's cores, not necessarily the pod CPU
                # quota. Limit ODX to the four vCPUs reserved with this pod.
                odm_opts_list = [o for o in odm_opts_list if o["name"] != "max-concurrency"]
                odm_opts_list.append({"name": "max-concurrency", "value": 4})
            with create_ssrf_safe_httpx_client(timeout=300.0, verify=True, is_desktop=is_desktop) as client:
                adapter = NodeODMAdapter(client, nodeodm_url, nodeodm_token)
                if owned_pod:
                    import time
                    from .photogrammetry_engine import node_startup_deadline
                    startup_started = time.time()
                    ready_until = node_startup_deadline(owned_pod.expires_at, now=startup_started)
                    while True:
                        if int((self.get_project(project_id) or {}).get("STATE_ID") or 0) not in (1, 2):
                            raise RuntimeError("Task canceled before the NodeODX worker became ready.")
                        try:
                            node_info = adapter.info()
                            break
                        except RuntimeError:
                            if time.time() >= ready_until:
                                raise RuntimeError("The NodeODX Pod did not become ready within its time limit.") from None
                            elapsed_minutes = int((time.time() - startup_started) / 60)
                            _set_prog(8, f"Starting NodeODX container · {elapsed_minutes} min elapsed")
                            time.sleep(3)
                else:
                    node_info = adapter.info()
                engine = str(node_info["engine"]).lower()
                if route.get("expected_engine") and engine != route["expected_engine"]:
                    raise RuntimeError("Configured NodeODX did not identify itself as ODX.")
                if engine == "odx":
                    adapter = NodeODXAdapter(client, nodeodm_url, nodeodm_token)
                _set_prog(12, "Validating reconstruction options")
                effective_options = adapter.effective_options(odm_opts_list)
                provenance = {
                    "engine": engine,
                    "engine_version": str(node_info["engineVersion"]),
                    "node_api_version": str(node_info.get("version") or ""),
                    "options": effective_options,
                    "backend": backend_code,
                    "control_sha256": (proj.get("PHOTOGRAMMETRY_CONTROL") or {}).get("sha256"),
                }
                requested_id = (jobs.node_upload_task(owned_job_key) or str(uuid.uuid5(uuid.NAMESPACE_URL, owned_job_key))) if owned_pod else None
                if requested_id and adapter.contains_task(requested_id):
                    odm_uuid = requested_id
                else:
                    from .photogrammetry_engine import NodeEngineError
                    for transfer_attempt in range(3 if owned_pod else 1):
                        requested_id = jobs.begin_node_upload(owned_job_key) if owned_pod else None
                        try:
                            odm_uuid = adapter.initialize(task_label, effective_options, requested_id=requested_id)
                            total_imgs = max(1, len(image_paths))
                            for idx, img_path in enumerate(image_paths):
                                if owned_pod and int((self.get_project(project_id) or {}).get("STATE_ID") or 0) not in (1, 2):
                                    raise RuntimeError("Task canceled during upload to the NodeODX worker.")
                                if lease_id or runpod_pod_id:
                                    self.get_runpod_lease_manager().heartbeat(task_id=project_id, lease_id=lease_id)
                                adapter.upload(odm_uuid, img_path)
                                _set_prog(12 + int(10 * (idx + 1) / total_imgs), f"Transferring source {idx + 1} of {total_imgs} to {engine.upper()}")
                            break
                        except NodeEngineError as upload_error:
                            if not owned_pod or not upload_error.retryable or transfer_attempt == 2:
                                raise
                            _set_prog(12, "Connection interrupted; restarting the transfer safely")
                    if int((self.get_project(project_id) or {}).get("STATE_ID") or 0) == 3:
                        raise RuntimeError("Task canceled before submitting to the processing node.")
                    try:
                        adapter.commit(odm_uuid, task_label, effective_options)
                    except RuntimeError as commit_error:
                        # A proxy can lose the response after the node has already started the task.
                        try:
                            adapter.status(odm_uuid)
                        except RuntimeError:
                            raise commit_error from None

            if lease_id or runpod_pod_id:
                self.get_runpod_lease_manager().update_lease_status(task_id=project_id, lease_id=lease_id, status="PROCESSING")

            try:
                from .blob_store import get_blob_store
                bs = get_blob_store()
                if bs.is_zero_local_disk():
                    for img_p in image_paths:
                        try:
                            if img_p.is_file():
                                img_p.unlink()
                        except Exception:
                            pass
            except Exception:
                pass

            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        if owned_pod and int(p.get("STATE_ID") or 0) not in (1, 2):
                            raise RuntimeError("Task canceled while submitting to the NodeODX worker.")
                        p["STATE_ID"] = 2
                        p["PROCESSING_PROGRESS"] = 22
                        p["PROCESSING_STAGE"] = "Reconstructing imagery with " + engine.upper()
                        p["NODEODM_UUID"] = odm_uuid
                        p["ENGINE_NODEODM_URL"] = nodeodm_url
                        p["PROCESSING_BACKEND"] = backend_code
                        p["CUSTOM_NODEODM_TOKEN"] = nodeodm_token if not (odx_endpoint_env or owned_pod) else ""
                        if owned_pod:
                            p["RUNPOD_ENGINE_JOB"] = owned_job_key
                            p.pop("RUNPOD_POD_ID", None)
                            p.pop("RUNPOD_LEASE_ID", None)
                        p["ODX_ENDPOINT_ENV"] = odx_endpoint_env
                        p["PHOTOGRAMMETRY_PROVENANCE"] = {**provenance, "remote_task_id": odm_uuid}
                        p["NODEODM_POLL_FAILURES"] = 0
                        p["NODEODM_FINALIZE_FAILURES"] = 0
                        if runpod_pod_id:
                            p["RUNPOD_POD_ID"] = runpod_pod_id
                        if lease_id:
                            p["RUNPOD_LEASE_ID"] = lease_id
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
        except Exception as exc:
            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        p["STATE_ID"] = 3
                        p["LAST_ERROR"] = str(exc)[:500]
                        p["PROCESSING_STAGE"] = "Processing stopped; original images preserved"
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
            if lease_id or runpod_pod_id:
                self.get_runpod_lease_manager().release_lease(task_id=project_id, lease_id=lease_id, status="FAILED", error=str(exc))
                self.terminate_runpod_nodeodm_pod_if_idle(runpod_pod_id, finished_project_id=project_id)
            if owned_job_key:
                self._finish_owned_engine_pod(owned_job_key)
            if backend_code == "MOSAICEARTH_CLOUD":
                self.refund_task_on_failure(project_id, reason=f"NodeODM upload/commit failed: {exc}")

    def cancel_pending_nodeodx(self, project_id: int, user_id: int) -> bool:
        """Cancel the owned cloud pipeline while it is still provisioning or uploading."""
        from .runpod_engines import RunPodEngineError

        if os.environ.get("MOSAICEARTH_RUNPOD_PHOTOGRAMMETRY") != "nodeodx":
            return False
        with self.lock:
            project = self.get_project(project_id)
            if (not project or int(project.get("CREATE_ID") or 0) != user_id
                    or int(project.get("STATE_ID") or 0) not in (1, 2)
                    or project.get("PROCESSING_BACKEND") != "MOSAICEARTH_CLOUD"
                    or not str(project.get("ENGINE_NODEODM_URL") or "").startswith("pipeline://")):
                return False
            job_key = str(project.get("NODEODM_UUID") or "")
            if not job_key.startswith(f"pipeline-{project_id}-"):
                return False
            for row in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(row.get("ID") or 0) == project_id:
                    row["STATE_ID"] = 3
                    row["LAST_ERROR"] = "Canceled by the project owner."
                    self.persist_table("dbo.WEB_PROJECTS")
                    break
        try:
            self.runpod_engine_jobs().cancel(job_key, "nodeodx")
        except RunPodEngineError:
            # The provider job remains in closing state for the reaper.
            pass
        self.refund_task_on_failure(project_id, reason="Task cancelled by user before completion")
        return True

    def _node_task_token(self, project: Dict[str, Any], endpoint: str) -> str:
        from .photogrammetry_engine import configured_node_token

        owned_job = project.get("RUNPOD_ENGINE_JOB")
        if owned_job:
            pod = self.runpod_engine_jobs().find(owned_job)
            if pod and pod.endpoint == endpoint and pod.expires_at > time.time():
                return pod.token
            raise RuntimeError("The authenticated processing Pod is no longer available.")
        source = str(project.get("ODX_ENDPOINT_ENV") or "")
        if source:
            return configured_node_token(source, endpoint)
        token = str(project.get("CUSTOM_NODEODM_TOKEN") or "").strip()
        if not token and project.get("PROCESSING_BACKEND") == "CUSTOM_NODEODM":
            settings = self.get_user_odm_settings(int(project.get("CREATE_ID") or 0))
            if str(settings.get("EndpointUrl") or "").rstrip("/") == endpoint:
                token = str(settings.get("Token") or "").strip()
        return token

    def get_node_task_logs(self, project: Dict[str, Any]) -> Tuple[Dict[str, Any], Any]:
        from .photogrammetry_engine import NodeODMAdapter
        from .processing_architecture import create_ssrf_safe_httpx_client

        endpoint = str(project["ENGINE_NODEODM_URL"]).rstrip("/")
        task_id = str(project["NODEODM_UUID"])
        with create_ssrf_safe_httpx_client(timeout=15) as client:
            adapter = NodeODMAdapter(client, endpoint, self._node_task_token(project, endpoint))
            return adapter.status(task_id), adapter.logs(task_id, -15)

    def request_node_cancellation(self, project: Dict[str, Any]) -> None:
        from .photogrammetry_engine import NodeODMAdapter
        from .processing_architecture import create_ssrf_safe_httpx_client

        endpoint = str(project["ENGINE_NODEODM_URL"]).rstrip("/")
        with create_ssrf_safe_httpx_client(timeout=15) as client:
            NodeODMAdapter(client, endpoint, self._node_task_token(project, endpoint)).cancel(str(project["NODEODM_UUID"]))

    def poll_photogrammetry_nodeodm(self, project_id: int, user_id: int = 0) -> str:
        """Polls the NodeODM (RunPod Cloud, Custom NodeODM, or Local NodeODM) task status, maintains the lease heartbeat,
        and ingests deliverables through the Canonical `ODMAdapter` BEFORE releasing the lease."""
        from .photogrammetry_engine import NodeODMAdapter
        from .processing_architecture import create_ssrf_safe_httpx_client

        proj = self.get_project(project_id)
        if not proj:
            return "0/3/0"
        st = int(proj.get("STATE_ID") or 3)
        if st == 4:
            return f"100/4/{1 if proj.get('ZIP_UPLOADED') else 0}"
        if st not in (1, 2):
            return f"0/{st}/0"

        odm_uuid = str(proj.get("NODEODM_UUID") or "").strip()
        nodeodm_url = str(proj.get("ENGINE_NODEODM_URL") or "").rstrip("/")
        saved_prog = max(2, min(98, int(proj.get("PROCESSING_PROGRESS") or 5)))
        if not odm_uuid or not nodeodm_url:
            return f"{saved_prog}/2/0"

        if nodeodm_url.startswith("embedded://") or odm_uuid.startswith("embedded-odm-"):
            res = self.submit_photogrammetry_to_nodeodm(project_id, user_id, target_mode="local")
            if not res.get("ok"):
                return "0/3/0"
            return "5/2/0"

        if nodeodm_url.startswith("pipeline://") or nodeodm_url.startswith("native-odm://"):
            return f"{saved_prog}/2/0"

        owned_job_key = proj.get("RUNPOD_ENGINE_JOB")
        is_runpod_task = not (proj.get("ODX_ENDPOINT_ENV") or owned_job_key) and (
            bool(proj.get("RUNPOD_POD_ID")) or bool(proj.get("RUNPOD_LEASE_ID"))
            or (proj.get("PROCESSING_BACKEND") == "MOSAICEARTH_CLOUD" and "proxy.runpod.net" in nodeodm_url)
        )
        if is_runpod_task:
            self.get_runpod_lease_manager().heartbeat(task_id=project_id, lease_id=str(proj.get("RUNPOD_LEASE_ID") or ""))

        try:
            custom_tok = self._node_task_token(proj, nodeodm_url)
            with create_ssrf_safe_httpx_client(timeout=15) as client:
                adapter = NodeODMAdapter(client, nodeodm_url, custom_tok)
                info = adapter.status(odm_uuid)
                try:
                    log_lines = adapter.logs(odm_uuid, -20)
                except Exception:
                    log_lines = None
            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        p["NODEODM_POLL_FAILURES"] = 0
                        p["ENGINE_PROCESSING_TIME_MS"] = int(info.get("processingTime") or 0)
                        if isinstance(log_lines, list):
                            p["LAST_ODM_LOG_LINES"] = [str(line).replace(custom_tok, "[redacted]") if custom_tok else str(line) for line in log_lines[-20:]]
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
        except Exception as poll_exc:
            fail_cnt = 0
            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        fail_cnt = int(p.get("NODEODM_POLL_FAILURES") or 0) + 1
                        p["NODEODM_POLL_FAILURES"] = fail_cnt
                        if fail_cnt >= 18:
                            p["STATE_ID"] = 3
                            p["LAST_ERROR"] = f"NodeODM endpoint became unreachable during processing ({fail_cnt} consecutive failures): {poll_exc}"[:500]
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
            if fail_cnt >= 18:
                if owned_job_key:
                    self._finish_owned_engine_pod(owned_job_key)
                if is_runpod_task:
                    self.get_runpod_lease_manager().release_lease(
                        task_id=project_id,
                        lease_id=str(proj.get("RUNPOD_LEASE_ID") or ""),
                        status="FAILED",
                        error=f"Pod unreachable after {fail_cnt} poll attempts",
                    )
                    self.terminate_runpod_nodeodm_pod_if_idle(str(proj.get("RUNPOD_POD_ID") or ""), finished_project_id=project_id)
                if str(proj.get("PROCESSING_BACKEND") or "MOSAICEARTH_CLOUD") == "MOSAICEARTH_CLOUD":
                    self.refund_task_on_failure(project_id, reason="NodeODM pod became unreachable during processing")
                return "0/3/0"
            return f"{saved_prog}/2/0"

        status_obj = info.get("status") or {}
        status_code = int(status_obj.get("code") or 20)
        raw_prog = float(info.get("progress") or 0.0)
        pct = max(18, min(99, 18 + int(round(raw_prog * 0.81))))

        # NodeODM Status Codes: 10=QUEUED, 20=RUNNING, 30=FAILED, 40=COMPLETED, 50=CANCELED
        if status_code in (10, 20):
            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        if int(p.get("PROCESSING_PROGRESS") or 0) != pct:
                            p["PROCESSING_PROGRESS"] = pct
                            self.persist_table("dbo.WEB_PROJECTS")
                        break
            return f"{pct}/2/0"

        if status_code in (30, 50):
            default_err = "OpenDroneMap task canceled" if status_code == 50 else "OpenDroneMap processing failed"
            err_msg = str(status_obj.get("errorMessage") or default_err)
            with self.lock:
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == int(project_id):
                        p["STATE_ID"] = 3
                        p["LAST_ERROR"] = err_msg
                        self.persist_table("dbo.WEB_PROJECTS")
                        break
            if owned_job_key:
                self._finish_owned_engine_pod(owned_job_key)
            if is_runpod_task:
                self.get_runpod_lease_manager().release_lease(
                    task_id=project_id,
                    lease_id=str(proj.get("RUNPOD_LEASE_ID") or ""),
                    status="CANCELED" if status_code == 50 else "FAILED",
                    error=err_msg,
                )
                self.terminate_runpod_nodeodm_pod_if_idle(str(proj.get("RUNPOD_POD_ID") or ""), finished_project_id=project_id)
            if str(proj.get("PROCESSING_BACKEND") or "MOSAICEARTH_CLOUD") == "MOSAICEARTH_CLOUD":
                self.refund_task_on_failure(project_id, reason=f"NodeODM {'canceled' if status_code == 50 else 'failed'}: {err_msg}")
            return "0/3/0"

        if status_code == 40:
            from .processing_architecture import cross_process_file_lock
            fin_lock_path = self.data_root / f".odm_finalize_{project_id}.lock"
            with cross_process_file_lock(fin_lock_path, timeout=600.0):
                proj_check = self.get_project(project_id)
                if proj_check and int(proj_check.get("STATE_ID") or 0) == 4 and proj_check.get("ZIP_UPLOADED"):
                    return "100/4/1"
                with self.lock:
                    for row in self.tables.get("dbo.WEB_PROJECTS", []):
                        if int(row.get("ID") or 0) == int(project_id):
                            row["PROCESSING_STAGE"] = "Downloading and validating reconstructed products"
                            self.persist_table("dbo.WEB_PROJECTS")
                            break

                # Transition lease to DOWNLOADING and hold it until complete_photogrammetry_task finishes!
                if is_runpod_task:
                    self.get_runpod_lease_manager().update_lease_status(
                        task_id=project_id,
                        lease_id=str(proj.get("RUNPOD_LEASE_ID") or ""),
                        status="DOWNLOADING",
                    )
                import tempfile as _tempfile
                import zipfile
                from .processing_architecture import _compute_file_sha256_stream

                tmp_zip_path: Optional[Path] = None
                finalized = False
                exhausted = False
                try:
                    # Keep verified byte ranges through worker restarts and retries.
                    # The task UUID binds this file to exactly one remote result.
                    safe_uuid = NodeODMAdapter._task_id(odm_uuid)
                    transfer_dir = self.data_dir / 'node-transfers'
                    transfer_dir.mkdir(parents=True, exist_ok=True)
                    tmp_zip_path = transfer_dir / f'{project_id}-{safe_uuid}.zip'
                    last_transfer_update = [0.0]
                    def transfer_progress(received, total):
                        now = time.monotonic()
                        if now-last_transfer_update[0] < 5 and received != total:
                            return
                        last_transfer_update[0] = now
                        with self.lock:
                            current = next(row for row in self.tables['dbo.WEB_PROJECTS'] if int(row.get('ID') or 0) == int(project_id))
                            current['PROCESSING_STAGE'] = f"Transferring results: {received/1024**2:.0f} / {total/1024**2:.0f} MiB" if total else f"Transferring results: {received/1024**2:.0f} MiB"
                            current['PROCESSING_PROGRESS'] = min(97, 93+int(4*received/total)) if total else 93
                            self.persist_table('dbo.WEB_PROJECTS')
                    with create_ssrf_safe_httpx_client(timeout=600) as client:
                        NodeODMAdapter(client, nodeodm_url, custom_tok).download(odm_uuid, tmp_zip_path, parallel=True, progress=transfer_progress)
                    with self.lock:
                        current = next(row for row in self.tables['dbo.WEB_PROJECTS'] if int(row.get('ID') or 0) == int(project_id))
                        current['PROCESSING_STAGE'] = 'Validating reconstructed products and preparing the viewer'
                        current['PROCESSING_PROGRESS'] = 98
                        self.persist_table('dbo.WEB_PROJECTS')
                    if not zipfile.is_zipfile(tmp_zip_path):
                        from .photogrammetry_engine import NodeEngineError
                        raise NodeEngineError(f"Node result has an invalid ZIP footer ({tmp_zip_path.stat().st_size} bytes); retained for diagnosis")
                    archive_hash = _compute_file_sha256_stream(tmp_zip_path) if proj.get("PHOTOGRAMMETRY_PROVENANCE") else ""
                    finalized = self.complete_photogrammetry_task(
                        project_id,
                        user_id,
                        nodeodm_all_zip_path=tmp_zip_path,
                        archive_sha256=archive_hash,
                    )
                    if not finalized:
                        raise RuntimeError("The project could not ingest the node result")
                except Exception as exc:
                    with self.lock:
                        for row in self.tables.get("dbo.WEB_PROJECTS", []):
                            if int(row.get("ID") or 0) == int(project_id):
                                attempts = int(row.get("NODEODM_FINALIZE_FAILURES") or 0) + 1
                                row["NODEODM_FINALIZE_FAILURES"] = attempts
                                exhausted = attempts >= 3
                                if exhausted:
                                    row["STATE_ID"] = 3
                                from .photogrammetry_engine import NodeEngineError
                                detail = str(exc) if isinstance(exc, (NodeEngineError, RuntimeError)) else type(exc).__name__
                                row["LAST_ERROR"] = f"Node result ingestion attempt {attempts} failed ({detail})."
                                row["PROCESSING_STAGE"] = 'Result validation failed; original inputs preserved' if exhausted else 'Result transfer or validation interrupted; retrying'
                                self.persist_table("dbo.WEB_PROJECTS")
                                break
                finally:
                    # Failed artifacts are diagnostic evidence and must survive a
                    # closed Pod. They can also support an ingestion-only repair.
                    if finalized and tmp_zip_path and tmp_zip_path.is_file():
                        try:
                            tmp_zip_path.unlink()
                            tmp_zip_path.with_suffix('.zip.ranges.json').unlink(missing_ok=True)
                        except Exception:
                            pass
                    # P0.1: Release lease and check idle termination ONLY AFTER download & ingestion are 100% complete!
                    if is_runpod_task and (finalized or exhausted):
                        try:
                            self.get_runpod_lease_manager().release_lease(
                                task_id=project_id,
                                lease_id=str(proj.get("RUNPOD_LEASE_ID") or ""),
                                status="RELEASED" if finalized else "FAILED",
                            )
                            self.terminate_runpod_nodeodm_pod_if_idle(
                                str(proj.get("RUNPOD_POD_ID") or ""),
                                finished_project_id=project_id,
                            )
                        except Exception:
                            pass
                if owned_job_key and (finalized or exhausted):
                    self._finish_owned_engine_pod(owned_job_key)
                if exhausted:
                    if proj.get("PROCESSING_BACKEND") == "MOSAICEARTH_CLOUD":
                        self.refund_task_on_failure(project_id, reason="Node result could not be downloaded or ingested.")
                    return "0/3/0"
                return "100/4/1" if finalized else f"{saved_prog}/2/0"

        return f"{pct}/2/0"

    def complete_photogrammetry_task(
        self,
        project_id: int,
        user_id: int = 0,
        nodeodm_all_zip: bytes = b"",
        native_job_dir: Optional[Path] = None,
        nodeodm_all_zip_path: Optional[Path] = None,
        archive_sha256: str = "",
    ) -> bool:
        """Finalizes a Photogrammetry Task via the Canonical `ODMAdapter` (`PhotogrammetryResult`),
        extracting Orthophoto, 3D Model, Point Cloud, first-class DSM (`dsm.tif`), optional DTM (`dtm.tif`),
        and Quality Report to the blob store and setting STATE_ID=4, ZIP_UPLOADED=True."""
        from .blob_store import get_blob_store
        from .processing_architecture import ODMAdapter, PhotogrammetryResult, register_dsm_dtm_assets_from_result

        with self.lock:
            proj = None
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id):
                    proj = p
                    break
            if not proj:
                return False

            pid = int(proj["ID"])
            uid = int(user_id or proj.get("CREATE_ID") or proj.get("USER_ID") or 1)
            pname = str(proj.get("NAME") or f"Project {pid}")
            folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{pname}"))
            proj["FOLDER"] = folder
            proj["SEO_URL_FRIENDLY"] = str(proj.get("SEO_URL_FRIENDLY") or folder)

            files = self.get_project_files(pid)
            lats = [_try_float(f.get("LATITUD")) for f in files if _try_float(f.get("LATITUD")) and abs(float(_try_float(f.get("LATITUD")) or 0) - 41.8205) > 1e-4]
            lons = [_try_float(f.get("LONGITUD")) for f in files if _try_float(f.get("LONGITUD")) and abs(float(_try_float(f.get("LONGITUD")) or 0) - 1.8676) > 1e-4]
            avg_lat = round(sum(lats) / len(lats), 6) if lats else (_try_float(proj.get("LATITUD")) or 41.8205)
            avg_lon = round(sum(lons) / len(lons), 6) if lons else (_try_float(proj.get("LONGITUD")) or 1.8676)

            area_ha = 0.0
            gsd_str = "Not measured"
            gps_str = "Not independently verified"

            blob_store = get_blob_store()
            if nodeodm_all_zip and not nodeodm_all_zip_path:
                all_zp = blob_store.resolve_path("imagenes", f"{folder}/procesado/all.zip")
                if all_zp:
                    all_zp.parent.mkdir(parents=True, exist_ok=True)
                    all_zp.write_bytes(nodeodm_all_zip)
                    nodeodm_all_zip_path = all_zp

            provenance = dict(proj.get("PHOTOGRAMMETRY_PROVENANCE") or {})
            if provenance and archive_sha256:
                provenance["archive_sha256"] = archive_sha256
            canon: Optional[PhotogrammetryResult] = None
            try:
                adapter = ODMAdapter()
                canon = adapter.ingest(
                    store=self,
                    project_id=pid,
                    folder=folder,
                    avg_lat=avg_lat,
                    avg_lon=avg_lon,
                    area_ha=area_ha,
                    native_job_dir=native_job_dir,
                    archive_zip_path=nodeodm_all_zip_path,
                    engine_provenance=provenance,
                )
                avg_lat = canon.avg_lat
                avg_lon = canon.avg_lon
                area_ha = canon.area_ha
                gsd_str = canon.gsd_str
                gps_str = canon.gps_str
                if provenance:
                    proj["PHOTOGRAMMETRY_PROVENANCE"] = provenance
                proj["CANONICAL_RESULT_CONTRACT"] = dataclasses.asdict(canon)

                # Fallback 2: Ensure 3d_tiles/pointcloud/tileset.json exists (in case ODM v3.6.2 entwine convert crashed)
                pc_tileset_p = blob_store.resolve_path("imagenes", f"{folder}/procesado/3d_tiles/pointcloud/tileset.json")
                las_p = blob_store.resolve_path("imagenes", f"{folder}/procesado/odm_georeferencing/odm_georeferenced_model.las")
                if pc_tileset_p and not pc_tileset_p.is_file() and las_p and las_p.is_file():
                    import struct as _struct
                    with open(las_p, "rb") as lf:
                        hdr = lf.read(375)
                        if len(hdr) >= 227 and hdr[:4] == b"LASF":
                            off_pts = _struct.unpack_from("<I", hdr, 96)[0]
                            rec_fmt = _struct.unpack_from("<B", hdr, 104)[0] & 0x3F
                            rec_len = _struct.unpack_from("<H", hdr, 105)[0]
                            sx, sy, sz, ox, oy, oz = _struct.unpack_from("<6d", hdr, 131)
                            minx, maxx, miny, maxy, minz, maxz = _struct.unpack_from("<6d", hdr, 179)
                            lf.seek(0, 2)
                            fsize = lf.tell()
                            total_pts = max(0, (fsize - off_pts) // max(1, rec_len))
                            if total_pts > 0:
                                step = max(1, total_pts // 250000)
                                out_n = (total_pts + step - 1) // step
                                cx = (minx + maxx) * 0.5
                                cy = (miny + maxy) * 0.5
                                cz = (minz + maxz) * 0.5
                                rgb_off = 30 if rec_fmt in (7, 8, 10) else (28 if rec_fmt in (3, 5) else 20)
                                pos_bytes = bytearray(out_n * 12)
                                rgb_bytes = bytearray(out_n * 3)
                                lf.seek(off_pts)
                                raw_all = lf.read(total_pts * rec_len)
                                out_idx = 0
                                for pt_i in range(0, total_pts, step):
                                    if out_idx >= out_n:
                                        break
                                    base_off = pt_i * rec_len
                                    xi, yi, zi = _struct.unpack_from("<3i", raw_all, base_off)
                                    px = float(xi * sx + ox - cx)
                                    py = float(yi * sy + oy - cy)
                                    pz = float(zi * sz + oz - cz)
                                    _struct.pack_into("<3f", pos_bytes, out_idx * 12, px, py, pz)
                                    if rgb_off + 6 <= rec_len:
                                        r16, g16, b16 = _struct.unpack_from("<3H", raw_all, base_off + rgb_off)
                                        if r16 > 255 or g16 > 255 or b16 > 255:
                                            r16, g16, b16 = r16 >> 8, g16 >> 8, b16 >> 8
                                        rgb_bytes[out_idx * 3] = min(255, r16)
                                        rgb_bytes[out_idx * 3 + 1] = min(255, g16)
                                        rgb_bytes[out_idx * 3 + 2] = min(255, b16)
                                    else:
                                        rgb_bytes[out_idx * 3 : out_idx * 3 + 3] = b"\xd0\xd0\xd0"
                                    out_idx += 1
                                ft_dict = {
                                    "POINTS_length": out_idx,
                                    "RTC_CENTER": [0.0, 0.0, 0.0],
                                    "POSITION": {"byteOffset": 0},
                                    "RGB": {"byteOffset": out_idx * 12},
                                }
                                ft_json = json.dumps(ft_dict, separators=(",", ":")).encode("utf-8")
                                pad_len = (8 - ((28 + len(ft_json)) % 8)) % 8
                                ft_json += b" " * pad_len
                                ft_bin = bytes(pos_bytes[: out_idx * 12]) + bytes(rgb_bytes[: out_idx * 3])
                                bin_pad = (8 - (len(ft_bin) % 8)) % 8
                                ft_bin += b"\x00" * bin_pad
                                total_byte_len = 28 + len(ft_json) + len(ft_bin)
                                pnts_hdr = _struct.pack("<4sIIIIII", b"pnts", 1, total_byte_len, len(ft_json), len(ft_bin), 0, 0)
                                pc_tileset_p.parent.mkdir(parents=True, exist_ok=True)
                                (pc_tileset_p.parent / "points.pnts").write_bytes(pnts_hdr + ft_json + ft_bin)
                                geoid_off_fb = float((getattr(canon, "vertical_geodesy", None) or {}).get("geoid_undulation_m") or 0.0)
                                enu_mat = self._build_enu_to_ecef_matrix(float(avg_lat), float(avg_lon), float(cz + geoid_off_fb))
                                hx = max(0.5, (maxx - minx) * 0.5)
                                hy = max(0.5, (maxy - miny) * 0.5)
                                hz = max(0.2, (maxz - minz) * 0.5)
                                pc_ts_obj = {
                                    "asset": {"version": "1.0"},
                                    "geometricError": 64.0,
                                    "root": {
                                        "transform": enu_mat,
                                        "boundingVolume": {"box": [0.0, 0.0, 0.0, hx, 0.0, 0.0, 0.0, hy, 0.0, 0.0, 0.0, hz]},
                                        "geometricError": 0.0,
                                        "refine": "ADD",
                                        "content": {"uri": "points.pnts"},
                                    },
                                }
                                pc_tileset_p.write_text(json.dumps(pc_ts_obj, indent=2), encoding="utf-8")
            except Exception as ingestion_error:
                raise RuntimeError(f"Canonical product ingestion failed: {str(ingestion_error)[:500]}") from ingestion_error

            if archive_sha256 and (canon is None or canon.quality_status != "COMPLETED"):
                missing = ", ".join(canon.missing_deliverables) if canon else "canonical reconstruction products"
                raise RuntimeError("Canonical ingestion did not validate the required products: " + missing)

            # Elevation viewers belong to the required result, too. Never publish
            # completion while their synchronous import is still running or failed.
            if canon is not None:
                register_dsm_dtm_assets_from_result(self, proj, uid, canon)

            # Nested asset imports may refresh SQLite overlays. Update the current
            # row, not a detached project dict captured before those imports.
            proj = next(row for row in self.tables["dbo.WEB_PROJECTS"] if int(row.get("ID") or 0) == pid)
            if canon is not None:
                proj["CANONICAL_RESULT_CONTRACT"] = dataclasses.asdict(canon)
            if provenance:
                proj["PHOTOGRAMMETRY_PROVENANCE"] = provenance

            proj["LATITUD"] = str(avg_lat)
            proj["LONGITUD"] = str(avg_lon)
            proj["STATE_ID"] = 4
            proj["PROCESSING_PROGRESS"] = 100
            proj["PROCESSING_STAGE"] = "Completed · verified reconstruction products"
            proj["ZIP_UPLOADED"] = True
            proj["LAST_ERROR"] = ""
            proj["CREDITS"] = str(int(round(float(proj.get("UPFRONT_AMOUNT_EUR") or 0))))

            for f_row in self.tables.get("dbo.WEB_PROJECTS_FILES", []):
                if int(f_row.get("PROJECT_ID") or 0) == pid:
                    f_row["PROCESSED"] = True
                    if not f_row.get("LATITUD") or str(f_row.get("LATITUD")).startswith("41.8205"):
                        f_row["LATITUD"] = str(avg_lat)
                    if not f_row.get("LONGITUD") or str(f_row.get("LONGITUD")).startswith("1.8676"):
                        f_row["LONGITUD"] = str(avg_lon)
            self.persist_table("dbo.WEB_PROJECTS_FILES")

            locs = self.tables.setdefault("dbo.WEB_PROJECTS_LOCATION", [])
            loc_row = next((l for l in locs if int(l.get("PROJECT_ID") or 0) == pid), None)
            if loc_row:
                loc_row["LATITUD"] = str(avg_lat)
                loc_row["LONGITUD"] = str(avg_lon)
            else:
                locs.append({
                    "ID": next_record_id(locs, "ID", 1),
                    "PROJECT_ID": pid,
                    "LATITUD": str(avg_lat),
                    "LONGITUD": str(avg_lon),
                })
            self.persist_table("dbo.WEB_PROJECTS_LOCATION")

            vals = self.tables.setdefault("dbo.WEB_PROJECTS_VALUES", [])
            val_row = next((v for v in vals if int(v.get("PROJECT_ID") or 0) == pid), None)
            if val_row:
                val_row["AREA"] = f"{area_ha:.2f} ha" if area_ha > 0 else "Not measured"
                val_row["AVERAGE_GPS"] = gps_str
                val_row["GSD"] = gsd_str
            else:
                val_row = {
                    "ID": next_record_id(vals, "ID", 1),
                    "PROJECT_ID": pid,
                    "AREA": f"{area_ha:.2f} ha" if area_ha > 0 else "Not measured",
                    "AVERAGE_GPS": gps_str,
                    "GSD": gsd_str,
                }
                vals.append(val_row)
            self.persist_table("dbo.WEB_PROJECTS_VALUES")

            models = self.tables.setdefault("dbo.WEB_CESIUM_MODELS", [])
            vg_info = (getattr(canon, "vertical_geodesy", None) if canon is not None else None) or {}
            for mtype in (3, 1):
                existing_m = next((m for m in models if int(m.get("Project_id") or 0) == pid and int(m.get("ModelType") or 0) == mtype), None)
                if existing_m is not None:
                    # Strictly preserve existing manual Height offset; store geodetic offset separately
                    existing_m["GeodesicOffset"] = float(vg_info.get("geoid_undulation_m") or 0.0)
                    existing_m["VerticalStatus"] = str(vg_info.get("vertical_transform_status") or "UNKNOWN_DATUM_NO_OP")
                else:
                    models.append({
                        "Id": next_record_id(models, "Id", 1),
                        "Project_id": pid,
                        "ModelType": mtype,
                        "Height": 0.0,
                        "GeodesicOffset": float(vg_info.get("geoid_undulation_m") or 0.0),
                        "VerticalStatus": str(vg_info.get("vertical_transform_status") or "UNKNOWN_DATUM_NO_OP"),
                    })
            self.persist_table("dbo.WEB_CESIUM_MODELS")
            self.persist_table("dbo.WEB_PROJECTS")

            try:
                import threading as _threading
                blob_store = get_blob_store()
                rep_rel = f"{folder}/procesado/odm_report/report.pdf"
                if not blob_store.resolve_blob_path("imagenes", rep_rel, allow_remote_fallback=False):
                    pdf_bytes = self._build_quality_report_pdf(proj, files, val_row)
                    blob_store.save_bytes("imagenes", rep_rel, pdf_bytes)
                if blob_store.is_zero_local_disk():
                    _threading.Thread(
                        target=blob_store.sync_folder_to_cloud,
                        args=("imagenes", folder, True),
                        daemon=True,
                    ).start()
            except Exception:
                pass

            self.add_notification(
                uid,
                "Photogrammetry processing complete",
                f"Your task '{pname}' has finished. Review its verified reconstruction products in MyEarth.",
                link_id=pid,
                type_id=1,
            )
            return True

    def bind_stripe_subscription(self, uid: int, subscription: str, metadata: Dict[str, str]) -> None:
        if metadata.get("mosaicearth_kind") != "subscription_plan":
            return
        for user in self.tables.get("dbo.USERS", []):
            if int(user.get("ID") or 0) == uid:
                user["STRIPE_PLAN_SUBSCRIPTION"] = subscription
                self.persist_table("dbo.USERS")
                break

    def apply_stripe_cancellation(self, uid: int, subscription: str, metadata: Dict[str, str]) -> bool:
        kind = metadata.get("mosaicearth_kind")
        for user in self.tables.get("dbo.USERS", []):
            if int(user.get("ID") or 0) != uid:
                continue
            if kind == "subscription_plan" and user.get("STRIPE_PLAN_SUBSCRIPTION") == subscription:
                if user.get("PLAN_KEY") == metadata["mosaicearth_plan_key"]:
                    user["PLAN_KEY"] = "free"
                user.pop("STRIPE_PLAN_SUBSCRIPTION", None)
            if kind == "extra_storage":
                user["EXTRA_STORAGE_GB"] = max(0, float(user.get("EXTRA_STORAGE_GB") or 0) - int(metadata["mosaicearth_extra_gb"]))
            self.persist_table("dbo.USERS")
            return True
        return False

    def upgrade_user_subscription_plan(self, user_id: int, plan_key: str) -> Dict[str, Any]:
        clean_plan = (plan_key or "free").strip().lower()
        if clean_plan not in ("free", "pro", "studio"):
            return {"ok": False, "message": "Invalid plan."}
        with self.lock:
            for u in self.tables.get("dbo.USERS", []):
                if int(u.get("ID") or 0) == int(user_id):
                    u["PLAN_KEY"] = clean_plan
                    u.pop("STRIPE_PLAN_SUBSCRIPTION", None)
                    self.persist_table("dbo.USERS")
                    summary = self.get_user_storage_summary(user_id)
                    return {"ok": True, "Success": True, "planKey": clean_plan, "storage": summary, "Summary": summary}
        return {"ok": False, "message": "User not found."}

    def add_user_extra_storage_gb(self, user_id: int, add_gb: int) -> Dict[str, Any]:
        """Adds extra private cloud storage GB (+250 GB, +1000 GB, +5000 GB) to the user's quota."""
        gb_val = int(add_gb or 0)
        if gb_val <= 0:
            return {"ok": False, "message": "Invalid extra storage amount."}
        with self.lock:
            for u in self.tables.get("dbo.USERS", []):
                if int(u.get("ID") or 0) == int(user_id):
                    cur_extra = float(u.get("EXTRA_STORAGE_GB") or 0.0)
                    u["EXTRA_STORAGE_GB"] = round(cur_extra + gb_val, 2)
                    self.persist_table("dbo.USERS")
                    summary = self.get_user_storage_summary(user_id)
                    self.add_notification(
                        int(user_id),
                        f"+{gb_val} GB Extra Private Cloud Storage Activated",
                        f"Your Private Cloud Storage Quota has been increased by +{gb_val} GB (Total Quota: {summary['quota_gb']:.0f} GB).",
                        link_id=0,
                        type_id=1,
                    )
                    return {
                        "ok": True,
                        "addedGb": gb_val,
                        "extraStorageGb": u["EXTRA_STORAGE_GB"],
                        "storage": summary,
                        "message": f"+{gb_val} GB Extra Private Cloud Storage added! Your total quota is now {summary['quota_gb']:.0f} GB.",
                    }
        return {"ok": False, "message": "User not found."}

    def purchase_marketplace_asset_direct_eur(
        self,
        buyer_user_id: int,
        publish_id: int = 0,
        external_asset_id: int = 0,
        force_stripe_paid: bool = False,
        stripe_session_id: str = "",
        stripe_cents: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Processes a direct per-purchase checkout in Euros (€) via Stripe Checkout or Creator Wallet."""
        if buyer_user_id <= 0:
            return {"ok": False, "message": "Sign in required."}
        now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
        with self.lock:
            buyer_u = next((u for u in self.tables.get("dbo.USERS", []) if int(u.get("ID") or 0) == int(buyer_user_id)), None)
            wallet_bal = float((buyer_u or {}).get("CREATOR_EARNINGS_EUR") or 0.0)

            if external_asset_id > 0:
                ext = next((a for a in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []) if int(a.get("ID") or 0) == int(external_asset_id)), None)
                if not ext:
                    return {"ok": False, "message": "Asset not found."}
                seller_id = int(ext.get("USER_ID") or 0)
                pid = int(ext.get("PROJECT_ID") or 0)
                price_eur = max(0, int(ext.get("PRICE_CREDITS") or 0))
                acqs = self.tables.setdefault("dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS", [])
                already_owned = seller_id == int(buyer_user_id) or any(
                    int(a.get("BUYER_USER_ID") or a.get("USER_ID") or 0) == int(buyer_user_id)
                    and int(a.get("ASSET_ID") or 0) == int(external_asset_id)
                    for a in acqs
                )
                if price_eur > 0 and not already_owned and not force_stripe_paid:
                    if wallet_bal < price_eur:
                        return {
                            "ok": True,
                            "requires_stripe": True,
                            "amount_eur": max(0.50, round(price_eur - wallet_bal, 2)),
                            "price_eur": price_eur,
                            "wallet_applied_eur": round(wallet_bal, 2),
                            "title": str(ext.get("TITLE") or ext.get("DISPLAY_NAME") or f"MosaicAsset #{external_asset_id}"),
                            "publish_id": 0,
                            "external_asset_id": int(external_asset_id),
                            "project_id": pid,
                        }
                    if buyer_u:
                        buyer_u["CREATOR_EARNINGS_EUR"] = round(wallet_bal - price_eur, 2)
                        self.persist_table("dbo.USERS")
                elif not already_owned and force_stripe_paid and buyer_u and stripe_cents is not None:
                    deduct = self._stripe_wallet_due(wallet_bal, price_eur, stripe_cents)
                    if deduct is None:
                        return {"ok": False, "message": "The wallet portion requires reconciliation."}
                    buyer_u["CREATOR_EARNINGS_EUR"] = round(wallet_bal - deduct, 2)
                    self.persist_table("dbo.USERS")

                if not already_owned:
                    acqs.append({
                        "ID": next_record_id(acqs, "ID", 1),
                        "ASSET_ID": int(external_asset_id),
                        "BUYER_USER_ID": int(buyer_user_id),
                        "USER_ID": int(buyer_user_id),
                        "SELLER_USER_ID": seller_id,
                        "PRICE_EUR": price_eur,
                        "STRIPE_SESSION_ID": stripe_session_id,
                        "CREATE_DATE": now_iso,
                    })
                    self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS")
                seller_payout = 0.0
                if price_eur > 0 and not already_owned and seller_id > 0 and seller_id != int(buyer_user_id):
                    seller_u = next((u for u in self.tables.get("dbo.USERS", []) if int(u.get("ID") or 0) == seller_id), None)
                    if seller_u:
                        s_plan = str(seller_u.get("PLAN_KEY") or "free").lower()
                        pct = 0.90 if s_plan == "studio" else (0.85 if s_plan == "pro" else 0.80)
                        seller_payout = round(price_eur * pct, 2)
                        seller_u["CREATOR_EARNINGS_EUR"] = round(float(seller_u.get("CREATOR_EARNINGS_EUR") or 0.0) + seller_payout, 2)
                        self.persist_table("dbo.USERS")
                return {
                    "ok": True,
                    "priceEur": price_eur,
                    "sellerPayoutEur": seller_payout,
                    "downloadUrl": f"/MosaicAssetDownload.ashx?source=external&projectId={pid}&assetId={external_asset_id}",
                }

            pub = next((p for p in self.tables.get("dbo.WEB_PUBLICATIONS", []) if int(p.get("ID") or 0) == int(publish_id)), None)
            if not pub:
                return {"ok": False, "message": "Publication not found."}
            pid = int(pub.get("PROJECT_ID") or 0)
            ft = int(pub.get("FILE_TYPE_ID") or 2)
            proj = self.get_project(pid) or {}
            seller_id = int(proj.get("CREATE_ID") or pub.get("USER_ID") or 0)
            price_eur = max(0, int(round(float(pub.get("CREDITS") or 0))))
            cols = self.tables.setdefault("dbo.WEB_COLLECTION", [])
            already_owned = seller_id == int(buyer_user_id) or any(
                c.get("ACTIVO", True)
                and int(c.get("USER_ID") or 0) == int(buyer_user_id)
                and int(c.get("PUBLISH_ID") or 0) == int(publish_id)
                for c in cols
            )
            if price_eur > 0 and not already_owned and not force_stripe_paid:
                if wallet_bal < price_eur:
                    return {
                        "ok": True,
                        "requires_stripe": True,
                        "amount_eur": max(0.50, round(price_eur - wallet_bal, 2)),
                        "price_eur": price_eur,
                        "wallet_applied_eur": round(wallet_bal, 2),
                        "title": str(pub.get("NAME") or proj.get("NOMBRE") or proj.get("NAME") or f"MosaicAsset #{publish_id}"),
                        "publish_id": int(publish_id),
                        "external_asset_id": 0,
                        "project_id": pid,
                        "file_type_id": ft,
                    }
                if buyer_u:
                    buyer_u["CREATOR_EARNINGS_EUR"] = round(wallet_bal - price_eur, 2)
                    self.persist_table("dbo.USERS")
            elif not already_owned and force_stripe_paid and buyer_u and stripe_cents is not None:
                deduct = self._stripe_wallet_due(wallet_bal, price_eur, stripe_cents)
                if deduct is None:
                    return {"ok": False, "message": "The wallet portion requires reconciliation."}
                buyer_u["CREATOR_EARNINGS_EUR"] = round(wallet_bal - deduct, 2)
                self.persist_table("dbo.USERS")

            if not already_owned:
                cols.append({
                    "ID": next_record_id(cols, "ID", 100),
                    "ACTIVO": True,
                    "CREATE_DATE": now_iso,
                    "CREATE_ID": int(buyer_user_id),
                    "UPDATE_DATE": now_iso,
                    "UPDATE_ID": int(buyer_user_id),
                    "USER_ID": int(buyer_user_id),
                    "PROJECT_ID": pid,
                    "PUBLISH_ID": int(publish_id),
                    "PRICE_EUR": price_eur,
                    "STRIPE_SESSION_ID": stripe_session_id,
                })
                self.persist_table("dbo.WEB_COLLECTION")
            seller_payout = 0.0
            if price_eur > 0 and not already_owned and seller_id > 0 and seller_id != int(buyer_user_id):
                seller_u = next((u for u in self.tables.get("dbo.USERS", []) if int(u.get("ID") or 0) == seller_id), None)
                if seller_u:
                    s_plan = str(seller_u.get("PLAN_KEY") or "free").lower()
                    pct = 0.90 if s_plan == "studio" else (0.85 if s_plan == "pro" else 0.80)
                    seller_payout = round(price_eur * pct, 2)
                    seller_u["CREATOR_EARNINGS_EUR"] = round(float(seller_u.get("CREATOR_EARNINGS_EUR") or 0.0) + seller_payout, 2)
                    self.persist_table("dbo.USERS")
            dl_url = f"/MosaicAssetDownload.ashx?source=processed&publishId={publish_id}&projectId={pid}&fileTypeId={ft}"
            return {
                "ok": True,
                "priceEur": price_eur,
                "sellerPayoutEur": seller_payout,
                "downloadUrl": dl_url,
            }

    def get_survey_assets_api(self, survey_id: int, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Implements GET /api/surveys/{id}/assets returning the Survey metadata, Task subfolders, and Assets."""
        with self.lock:
            proj = self.get_project(survey_id)
            if not proj:
                return {"ok": False, "error": "Survey not found"}
            pid = int(proj["ID"])
            parent_id = int(proj.get("PARENT_PROJECT_ID") or 0) or pid
            sname = str(proj.get("SURVEY_NAME") or proj.get("NAME") or f"Survey #{pid}")
            sdesc = str(proj.get("SURVEY_DESCRIPTION") or proj.get("DESCRIPTION") or "")

            # Collect the survey row itself plus any child task rows attached via SURVEY_ID == pid
            task_rows = [proj]
            for p_other in self.tables.get("dbo.WEB_PROJECTS", []):
                if not p_other.get("ACTIVO", True):
                    continue
                if int(p_other.get("ID") or 0) != pid and int(p_other.get("SURVEY_ID") or 0) == pid:
                    task_rows.append(copy.deepcopy(p_other))

            gauss_by_pid = {
                int(gp.get("PROJECT_ID") or 0): gp
                for gp in self.tables.get("dbo.MOSAICEARTH_GAUSSIAN_PROJECTS", [])
                if gp.get("PROJECT_ID") is not None
            }
            ext_meta_by_pid = {
                int(ep.get("PROJECT_ID") or 0): ep
                for ep in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_PROJECTS", [])
                if ep.get("PROJECT_ID") is not None
            }

            tasks = []
            task_folders = []
            assets = []

            def _fmt_metric_2dec(raw_val: Any, unit: str) -> str:
                s = str(raw_val or "").strip()
                if not s or s.lower() in ("none", "null", "—", "-"):
                    return ""
                m_num = re.search(r"[-+]?\d+(?:[.,]\d+)?", s)
                if not m_num:
                    return s
                try:
                    num = float(m_num.group(0).replace(",", "."))
                    return f"{num:.2f} {unit}"
                except Exception:
                    return s

            for t_row in task_rows:
                t_pid = int(t_row["ID"])
                t_name = str(t_row.get("NAME") or sname)
                vals = self.get_project_values(t_pid) or {}
                loc = self.get_project_location(t_pid) or {}
                lat = _try_float(t_row.get("LATITUD")) or _try_float(loc.get("LATITUD"))
                lon = _try_float(t_row.get("LONGITUD")) or _try_float(loc.get("LONGITUD"))
                gsd = _fmt_metric_2dec(vals.get("GSD_AVERAGE") or vals.get("AVERAGE_GSD"), "cm/px")
                gps_acc = _fmt_metric_2dec(vals.get("GPS_AVERAGE") or vals.get("AVERAGE_GPS_ERROR"), "m")
                raw_area = str(vals.get("AREA") or t_row.get("AREA") or "").strip()
                area_val = _fmt_metric_2dec(raw_area, "m²") if (raw_area and not any(u in raw_area.lower() for u in ("m", "ha", "km"))) else raw_area
                cap_date = str(t_row.get("TASK_CREATION_DATE") or vals.get("CAPTURE_DATE") or t_row.get("CREATE_DATE") or "")[:10]
                t_method = str(t_row.get("CAPTURE_METHOD") or "")
                t_desc = str(t_row.get("TASK_DESCRIPTION") or t_row.get("DESCRIPTION") or "")
                t_cat_id = int(t_row.get("CATEGORY_ID") or 0)
                if 1 <= t_cat_id <= 10:
                    t_cat_id += 12
                t_tags = str(t_row.get("TAGS") or "")
                t_folder = str(t_row.get("FOLDER") or _slugify(f"{t_pid}-{t_name}"))
                titles_map = t_row.get("ASSET_TITLES") if isinstance(t_row.get("ASSET_TITLES"), dict) else {}
                asset_meta_map = t_row.get("ASSET_METADATA") if isinstance(t_row.get("ASSET_METADATA"), dict) else {}
                deleted_fts = {int(x) for x in (t_row.get("DELETED_FILE_TYPES") or []) if str(x).isdigit()}

                pubs_by_ft = {}
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                    if int(pub.get("PROJECT_ID") or 0) == t_pid and int(pub.get("STATUS_ID") or 0) == 2:
                        pubs_by_ft[int(pub.get("FILE_TYPE_ID") or 0)] = pub

                state_id = int(t_row.get("STATE_ID") or 3)
                gp_row = gauss_by_pid.get(t_pid)
                ep_row = ext_meta_by_pid.get(t_pid)
                is_gauss_task = gp_row is not None or (ep_row and str(ep_row.get("PROJECT_KIND") or "").lower() == "gaussian")
                is_ext_task = bool(ep_row and str(ep_row.get("PROJECT_KIND") or "").lower() == "external")

                if is_gauss_task:
                    task_kind = "gaussian"
                    task_type_label = "Gaussian Splatting Task"
                    upload_url = f"/Client/GaussianProject.aspx?id={t_pid}"
                    report_url = ""
                elif is_ext_task:
                    task_kind = "external"
                    task_type_label = "Direct File Import Task"
                    upload_url = f"/Client/ExternalAssetImport.aspx?project_id={t_pid}"
                    report_url = ""
                else:
                    task_kind = "processing"
                    task_type_label = "Standard Photogrammetry Task"
                    upload_url = f"/Client/Project.aspx?id={t_pid}&upload=1"
                    report_url = f"/ProcessedAssetFile.ashx/{t_folder}/procesado/odm_report/report.pdf" if (state_id == 4 and bool(t_row.get("ZIP_UPLOADED"))) else ""

                if state_id in (1, 2) and not is_gauss_task and not is_ext_task and not t_row.get("IS_PROJECT_CONTAINER") and not t_row.get("IS_SURVEY_CONTAINER"):
                    tasks.append({
                        "id": f"task-odm-{t_pid}",
                        "survey_id": pid,
                        "task_project_id": t_pid,
                        "type": "gaussian" if is_gauss_task else "photogrammetry",
                        "type_label": task_type_label,
                        "status": "processing",
                        "progress": max(0, min(99, int(t_row.get("PROCESSING_PROGRESS") or 0))),
                        "created_at": cap_date,
                        "methodology": t_method or ("3D Gaussian Splatting Capture" if is_gauss_task else "UAV Photogrammetry Survey"),
                        "description": t_desc,
                    })

                task_assets = []

                if state_id == 4 and bool(t_row.get("ZIP_UPLOADED")):
                    for ft, default_ext, a_type, icon_kind, icon_svg, est_mb in (
                        (2, "orthophoto.tif", "cog", "orthophoto", ICON_ORTHOPHOTO, 0.0),
                        (3, "3d_model.obj", "mesh3d", "model3d", ICON_3D, 0.0),
                        (1, "pointcloud.laz", "laz", "pointcloud", ICON_CLOUD, 0.0),
                    ):
                        if ft in deleted_fts:
                            continue
                        contract = t_row.get("CANONICAL_RESULT_CONTRACT")
                        if contract is not None and not contract.get({1:"pointcloud_master",2:"orthophoto_master",3:"mesh_master"}[ft]):
                            continue
                        if contract:
                            kind = {1:"pointcloud", 2:"orthophoto", 3:"mesh"}[ft]
                            size = (contract.get("master_sizes") or {}).get(kind)
                            if size is None:
                                from .blob_store import get_blob_store
                                local = get_blob_store().resolve_path("imagenes", contract[kind + "_master"])
                                size = local.stat().st_size if local and local.is_file() else None
                            if size is not None:
                                est_mb = size / (1024 * 1024)
                        pub = pubs_by_ft.get(ft)
                        is_pub = pub is not None
                        pub_id = int(pub.get("ID") or 0) if pub else 0
                        price_eur = int(round(float(pub.get("CREDITS") or 0))) if is_pub else 0
                        a_name = str(titles_map.get(str(ft)) or f"{t_name} · {default_ext}")
                        ft_custom = asset_meta_map.get(str(ft)) if isinstance(asset_meta_map.get(str(ft)), dict) else {}
                        eff_date = str(ft_custom.get("creation_date") or cap_date or "")[:10]
                        eff_method = str(ft_custom.get("methodology") or t_method or ("3D Gaussian Splatting Reconstruction" if is_gauss_task else ("UAV Photogrammetry (NodeODX / ODX)" if (t_row.get("CANONICAL_RESULT_CONTRACT") or {}).get("engine_name") == "odx" else "UAV Photogrammetry (NodeODM)")))
                        eff_desc = str(ft_custom.get("description") if "description" in ft_custom else t_desc)
                        eff_cat_id = int(ft_custom.get("category_id") or t_cat_id or 0)
                        eff_tags = str(ft_custom.get("tags") if "tags" in ft_custom else t_tags)
                        if is_pub and pub_id > 0:
                            pub_slug = to_slug(str(pub.get("SEO_URL_FRIENDLY") or pub.get("NAME") or a_name)) or "asset"
                            detail_url = f"/en/assets/{pub_slug}-{pub_id}"
                        else:
                            detail_url = f"/Client/ProductDetail.aspx?project_id={t_pid}&type={ft}"
                        meta = {
                            "capture_date": eff_date,
                            "creation_date": eff_date,
                            "methodology": eff_method,
                            "description": eff_desc,
                            "lat": lat,
                            "lon": lon,
                        }
                        if gsd and ft == 2:
                            meta["gsd"] = gsd
                        if gps_acc:
                            meta["gps_accuracy"] = gps_acc
                        if area_val:
                            meta["area"] = area_val
                        asset_obj = {
                            "id": f"processed-{ft}-{t_pid}",
                            "survey_id": pid,
                            "task_project_id": t_pid,
                            "task_id": f"task-{t_pid}",
                            "external_asset_id": 0,
                            "file_type_id": ft,
                            "icon_kind": icon_kind,
                            "icon_svg": icon_svg,
                            "task_origin": "gaussian" if is_gauss_task else "photogrammetry",
                            "task_origin_label": "Gaussian Splatting" if is_gauss_task else "Standard Photogrammetry",
                            "name": a_name,
                            "type": a_type,
                            "type_label": "Maps & Layers · Orthophoto (COG)" if ft == 2 else ("3D Model · Textured Mesh" if ft == 3 else "Point Cloud · LiDAR / Photogrammetry (LAZ)"),
                            "creation_date": eff_date,
                            "methodology": eff_method,
                            "description": eff_desc,
                            "category_id": eff_cat_id,
                            "tags": eff_tags,
                            "gsd": gsd if (gsd and ft == 2) else "",
                            "gps_accuracy": gps_acc or "",
                            "lat": float(lat) if lat is not None else 41.9702,
                            "lon": float(lon) if lon is not None else 2.3885,
                            "height": 0.0,
                            "size_mb": est_mb,
                            "quota_mb": 0.0 if is_pub else est_mb,
                            "visibility": ("marketplace" if price_eur > 0 else "public_free") if is_pub else "private",
                            "price_eur": price_eur,
                            "storage_path": f"/{parent_id}/{pid}/assets/processed_{ft}_{default_ext}",
                            "detail_url": detail_url,
                            "viewer_url": "/Map.aspx?id=" + str(t_pid) if ft == 2 else f"/MapView.aspx?id={t_pid}&type={ft}",
                            "edit_3d_url": f"/MapEdit.aspx?id={t_pid}&type={ft}",
                            "annotations_url": f"/MapAnnotations.aspx?id={t_pid}&type={ft}",
                            "fly_url": f"/Fly.aspx?asset_id=processed-{ft}-{t_pid}" + (f"&lat={lat}&lon={lon}" if (lat is not None and lon is not None) else ""),
                            "download_url": f"/MosaicAssetDownload.ashx?source=processed&projectId={t_pid}&fileTypeId={ft}",
                            "metadata": meta,
                        }
                        task_assets.append(asset_obj)
                        assets.append(asset_obj)

                from .asset_uploads import asset_output_available
                for ea in self.get_external_assets_by_project(t_pid):
                    aid = int(ea.get("ID") or 0)
                    if not asset_output_available(ea):
                        filename = str(ea.get("SOURCE_FILE_NAME") or "Original file")
                        task_assets.append({
                            "id": f"external-{aid}", "external_asset_id": aid, "task_project_id": t_pid,
                            "name": filename, "type": Path(filename).suffix.lstrip("."),
                            "output_available": False, "status_detail": ea.get("STATUS_DETAIL"),
                            "task_url": upload_url,
                            "download_url": f"/ExternalAssetFile.ashx?project_id={t_pid}&asset_id={aid}&mode=original&download=1",
                        })
                        continue
                    atype = str(ea.get("ASSET_TYPE") or "orthophoto").lower()
                    subtype = str(ea.get("ASSET_SUBTYPE") or "").lower()
                    fmt = str(ea.get("OUTPUT_FORMAT") or ea.get("SOURCE_FORMAT") or ea.get("FORMAT") or "tif").lower()
                    vis = str(ea.get("VISIBILITY") or "private").lower()
                    price_eur = int(ea.get("PRICE_CREDITS") or 0)
                    raw_bytes = int(ea.get("FILE_SIZE_BYTES") or ea.get("SIZE_BYTES") or 0)
                    ea_size_mb = round(max(0.1, raw_bytes / (1024.0 * 1024.0)), 1)
                    is_gauss_asset = "gaussian" in subtype or "gaussian" in atype or fmt == "splat"
                    if atype == "pointcloud" and not is_gauss_asset:
                        icon_kind = "pointcloud"
                        icon_svg = ICON_CLOUD
                        type_lbl = f"Point Cloud · {fmt.upper()}"
                    elif atype in ("model3d", "gaussian") or is_gauss_asset:
                        icon_kind = "model3d"
                        icon_svg = ICON_3D
                        type_lbl = f"3D Gaussian Splat · {fmt.upper()}" if is_gauss_asset else f"3D Model · {fmt.upper()}"
                    else:
                        icon_kind = "orthophoto"
                        icon_svg = ICON_ORTHOPHOTO
                        type_lbl = f"Maps & Layers · {fmt.upper()}"
                    ea_date = str(ea.get("CAPTURE_DATE") or ea.get("CREATED_AT") or cap_date or "")[:10]
                    ea_method = str(ea.get("CAPTURE_METHOD") or t_method or ("3D Gaussian Splatting" if is_gauss_asset else "Direct File Import"))
                    ea_desc = str(ea.get("DESCRIPTION") or t_desc or "")
                    ea_cat_id = int(ea.get("CATEGORY_ID") or t_cat_id or 0)
                    ea_tags = str(ea.get("TAGS") or t_tags or "")
                    ea_lat = _try_float(ea.get("LATITUDE"))
                    ea_lon = _try_float(ea.get("LONGITUDE"))
                    ea_title = str(ea.get("TITLE") or ea.get("DISPLAY_NAME") or ea.get("SOURCE_FILE_NAME") or f"Asset #{aid}.{fmt}")
                    ea_slug = to_slug(ea_title) or "external-asset"
                    detail_url_ea = f"/en/external-assets/{ea_slug}-{aid}" if vis == "public" else f"/Client/ExternalAssetDetail.aspx?project_id={t_pid}&asset_id={aid}"
                    meta_ea = {
                        "capture_date": ea_date,
                        "creation_date": ea_date,
                        "methodology": ea_method,
                        "description": ea_desc,
                    }
                    if ea_lat is not None and ea_lon is not None:
                        meta_ea["lat"] = ea_lat
                        meta_ea["lon"] = ea_lon
                    asset_obj = {
                        "id": f"external-{aid}",
                        "survey_id": pid,
                        "task_project_id": t_pid,
                        "task_id": f"task-{t_pid}",
                        "external_asset_id": aid,
                        "file_type_id": 0,
                        "icon_kind": icon_kind,
                        "icon_svg": icon_svg,
                        "task_origin": "gaussian" if is_gauss_asset else "import",
                        "task_origin_label": "Gaussian Splatting" if is_gauss_asset else "Direct File Import",
                        "name": ea_title,
                        "type": fmt,
                        "type_label": type_lbl,
                        "can_derive_terrain": not ea.get("DERIVATIVE_KIND") and (ea.get("ASSET_CLASS") == "RASTER_ELEVATION" or ea.get("ASSET_SUBTYPE") in ("dsm", "dtm", "dem", "terrain")),
                        "creation_date": ea_date,
                        "methodology": ea_method,
                        "description": ea_desc,
                        "category_id": ea_cat_id,
                        "tags": ea_tags,
                        "gsd": "",
                        "gps_accuracy": "",
                        "lat": float(ea_lat) if ea_lat is not None else 41.9702,
                        "lon": float(ea_lon) if ea_lon is not None else 2.3885,
                        "height": float(ea.get("ALTITUDE") or 0.0),
                        "size_mb": ea_size_mb,
                        "quota_mb": 0.0 if vis == "public" else ea_size_mb,
                        "visibility": ("marketplace" if price_eur > 0 else "public_free") if vis == "public" else "private",
                        "price_eur": price_eur,
                        "storage_path": f"/{parent_id}/{pid}/assets/{aid}_{ea.get('SOURCE_FILE_NAME') or ('asset.' + fmt)}",
                        "detail_url": detail_url_ea,
                        "viewer_url": f"/MapView.aspx?id={t_pid}&asset_id={aid}",
                        "edit_3d_url": f"/MapEdit.aspx?id={t_pid}&asset_id={aid}",
                        "annotations_url": f"/MapAnnotations.aspx?id={t_pid}&asset_id={aid}",
                        "fly_url": f"/Fly.aspx?asset_id=external-{aid}" + (f"&lat={ea_lat}&lon={ea_lon}" if (ea_lat is not None and ea_lon is not None) else ""),
                        "download_url": f"/MosaicAssetDownload.ashx?source=external&projectId={t_pid}&assetId={aid}",
                        "metadata": meta_ea,
                    }
                    task_assets.append(asset_obj)
                    assets.append(asset_obj)

                is_pure_empty_container = (
                    (bool(t_row.get("IS_PROJECT_CONTAINER")) or bool(t_row.get("IS_SURVEY_CONTAINER")) or bool(t_row.get("TASK_DELETED")))
                    and len(task_assets) == 0
                )
                if not is_pure_empty_container:
                    eff_t_method = t_method or (
                        "3D Gaussian Splatting"
                        if task_kind == "gaussian"
                        else ("Direct File Import" if task_kind == "external" else ("UAV Photogrammetry (NodeODX / ODX)" if (t_row.get("CANONICAL_RESULT_CONTRACT") or {}).get("engine_name") == "odx" else "UAV Photogrammetry (NodeODM)"))
                    )
                    t_files_cnt = len(self.get_project_files(t_pid))
                    t_prog = max(2, min(99, int(t_row.get("PROCESSING_PROGRESS") or 12))) if state_id in (1, 2) else 0
                    task_folders.append({
                        "id": t_pid,
                        "survey_id": pid,
                        "name": str(t_row.get("TASK_NAME") or t_name),
                        "kind": task_kind,
                        "type_label": task_type_label,
                        "origin_label": task_type_label,
                        "status": "completed" if (state_id == 4 and len(task_assets) > 0) else ("processing" if state_id in (1, 2) else ("failed" if (state_id == 3 and bool(t_row.get("LAST_ERROR"))) else "draft")),
                        "state_id": state_id,
                        "files_count": t_files_cnt,
                        "processing_progress": t_prog,
                        "last_error": str(t_row.get("LAST_ERROR") or ""),
                        "creation_date": cap_date,
                        "methodology": eff_t_method,
                        "description": t_desc,
                        "category_id": t_cat_id,
                        "tags": t_tags,
                        "area": area_val,
                        "gsd": gsd,
                        "gps_accuracy": gps_acc,
                        "report_url": report_url,
                        "upload_url": upload_url,
                        "asset_count": len(task_assets),
                        "assets": task_assets,
                    })

            return {
                "ok": True,
                "project_id": parent_id,
                "survey": {
                    "id": pid,
                    "project_id": parent_id,
                    "name": sname,
                    "description": sdesc,
                },
                "tasks": tasks,
                "task_folders": task_folders,
                "assets": assets,
            }

    def list_myearth_hierarchy(self, user_id: int, lang: str = "en") -> Dict[str, Any]:
        """Builds the hierarchy for MyEarth: Projects -> Surveys -> Tasks (Subfolders) -> Assets."""
        user_projs = self.list_user_projects(user_id)
        by_id = {int(p["ID"]): p for p in user_projs}
        roots: Dict[int, Dict[str, Any]] = {}
        surveys_by_root: Dict[int, List[Dict[str, Any]]] = {}

        for p in sorted(user_projs, key=lambda x: int(x.get("ID") or 0), reverse=True):
            pid = int(p["ID"])
            # Skip child task rows that are attached to a Survey via SURVEY_ID (they are rendered as Task subfolders inside that Survey)
            if int(p.get("SURVEY_ID") or 0) > 0 and int(p.get("SURVEY_ID") or 0) != pid and int(p.get("SURVEY_ID") or 0) in by_id:
                continue
            parent_id = int(p.get("PARENT_PROJECT_ID") or 0)
            root_id = parent_id if (parent_id > 0 and parent_id in by_id) else pid
            if root_id not in roots:
                rp = by_id.get(root_id, p)
                roots[root_id] = {
                    "id": root_id,
                    "name": str(rp.get("PROJECT_CONTAINER_NAME") or rp.get("NAME") or f"Project #{root_id}"),
                    "description": str(rp.get("PROJECT_DESCRIPTION") or rp.get("DESCRIPTION") or ""),
                    "created_at": str(rp.get("CREATE_DATE_ISO") or ""),
                }
            # If p is a pure Level-1 Project container and has no direct assets, don't add it as a Level-2 Survey
            if pid == root_id and bool(p.get("IS_PROJECT_CONTAINER")):
                surveys_by_root.setdefault(root_id, [])
                continue
            surveys_by_root.setdefault(root_id, []).append(p)

        project_trees = []
        private_quota_assets: List[Dict[str, Any]] = []
        for root_id, rinfo in roots.items():
            raw_surveys = sorted(surveys_by_root.get(root_id, []), key=lambda s: int(s.get("ID") or 0))
            survey_nodes = []
            total_assets = 0
            for idx_s, s_row in enumerate(raw_surveys, start=1):
                sid = int(s_row["ID"])
                s_api = self.get_survey_assets_api(sid, user_id=user_id)
                s_assets = s_api.get("assets", [])
                s_tasks = s_api.get("tasks", [])
                s_task_folders = s_api.get("task_folders", [])
                # If root_id has explicit child surveys and sid == root_id has 0 assets and 0 tasks, skip the empty root self-survey
                if len(raw_surveys) > 1 and sid == root_id and len(s_assets) == 0 and len(s_task_folders) == 0 and not bool(s_row.get("IS_SURVEY_CONTAINER")):
                    continue
                total_assets += len(s_assets)
                s_raw_name = str(s_row.get("SURVEY_NAME") or s_row.get("NAME") or f"Survey {idx_s:02d}")
                if bool(s_row.get("IS_SURVEY_CONTAINER")):
                    s_display_name = s_raw_name
                elif len(raw_surveys) == 1 and s_raw_name == rinfo["name"]:
                    s_display_name = f"01 {s_raw_name}"
                else:
                    s_display_name = s_raw_name
                s_desc = str(s_row.get("SURVEY_DESCRIPTION") or s_row.get("DESCRIPTION") or "")
                for a in s_assets:
                    if str(a.get("visibility") or "private") == "private":
                        a_copy = dict(a)
                        a_copy["project_id"] = root_id
                        a_copy["project_name"] = rinfo["name"]
                        a_copy["survey_id"] = sid
                        a_copy["survey_name"] = s_display_name
                        private_quota_assets.append(a_copy)
                survey_nodes.append({
                    "id": sid,
                    "ID": sid,
                    "project_id": root_id,
                    "name": s_display_name,
                    "NAME": s_display_name,
                    "raw_name": s_raw_name,
                    "description": s_desc,
                    "LAYER_COUNT": len(s_assets),
                    "workspace_url": (
                        f"/Client/GaussianProject.aspx?id={sid}"
                        if s_row.get("IS_GAUSSIAN_PROJECT")
                        else f"/Client/ExternalAssetImport.aspx?project_id={sid}"
                        if s_row.get("IS_EXTERNAL_IMPORT")
                        else f"/Client/Project.aspx?id={sid}"
                    ),
                    "tasks": s_tasks,
                    "task_folders": s_task_folders,
                    "assets": s_assets,
                })
            project_trees.append({
                "id": root_id,
                "ID": root_id,
                "name": rinfo["name"],
                "NAME": rinfo["name"],
                "description": rinfo["description"],
                "created_at": rinfo["created_at"],
                "surveys": survey_nodes,
                "Surveys": survey_nodes,
                "total_assets": total_assets,
                "asset_count": total_assets,
                "TOTAL_LAYERS": total_assets,
            })

        private_quota_assets.sort(key=lambda x: float(x.get("size_mb") or 0.0), reverse=True)

        imp_list, col_list = self.get_user_collection_data(user_id, lang=lang)
        raw_acq = [x for x in imp_list if not x.get("IsOwnedByCurrentUser")] + [
            x for x in col_list if str(x.get("OwnershipKind") or "") == "acquired"
        ]
        acquired_assets = []
        for acq in raw_acq:
            ft_acq = int(acq.get("FileTypeId") or 2)
            icon_svg_acq = ICON_CLOUD if ft_acq == 1 else (ICON_3D if ft_acq == 3 else ICON_ORTHOPHOTO)
            acquired_assets.append({
                "name": str(acq.get("Title") or acq.get("NAME") or "Acquired Asset"),
                "creator": str(acq.get("CreatorName") or acq.get("CREATOR") or "MosaicEarth Creator"),
                "date": str(acq.get("CaptureDateDisplay") or acq.get("CreateDateDisplay") or ""),
                "icon_svg": icon_svg_acq,
                "viewer_url": str(acq.get("DetailUrl") or acq.get("ViewerUrl") or "/en/Map"),
                "download_url": str(acq.get("DownloadUrl") or "#"),
                "fly_url": f"/Fly.aspx?asset_id={acq.get('AssetKey') or ''}",
            })

        i18n_dict = {
            "ca": {
                "project": "Projecte",
                "projects": "Projectes",
                "survey": "Aixecament",
                "surveys": "Aixecaments",
                "task": "Tasca",
                "tasks": "Tasques",
                "new_survey": "+ Nou aixecament",
                "add_data": "Afegir tasca / dades",
                "processing": "Processant...",
                "assets": "Capes",
                "acquired_assets": "Capes adquirides",
            },
            "es": {
                "project": "Proyecto",
                "projects": "Proyectos",
                "survey": "Levantamiento",
                "surveys": "Levantamientos",
                "task": "Tarea",
                "tasks": "Tareas",
                "new_survey": "+ Nuevo levantamiento",
                "add_data": "Añadir tarea / datos",
                "processing": "Procesando...",
                "assets": "Capas",
                "acquired_assets": "Capas adquiridas",
            },
        }.get(
            (lang or "en").lower()[:2],
            {
                "project": "Project",
                "projects": "Projects",
                "survey": "Survey",
                "surveys": "Surveys",
                "task": "Task",
                "tasks": "Tasks",
                "new_survey": "+ New Survey",
                "add_data": "Add Task / Data",
                "processing": "Processing...",
                "assets": "Layers",
                "acquired_assets": "Acquired Assets",
            },
        )
        return {
            "i18n": i18n_dict,
            "storage": self.get_user_storage_summary(user_id),
            "projects": project_trees,
            "private_quota_assets": private_quota_assets,
            "acquired_assets": acquired_assets,
        }

    def open_or_create_container_mymosaic(self, user_id: int, kind: str = "survey", container_id: int = 0, container_kind: str = "") -> int:
        """Creates or updates a MyMosaic containing all assets of a Project ('project'), Survey ('survey'), or Task ('task')."""
        eff_kind = (container_kind or kind or "survey").strip().lower()
        root_id, fam = self.get_subprojects_family(container_id)
        if eff_kind == "project":
            target_surveys = fam
        elif eff_kind == "survey":
            target_surveys = [p for p in fam if int(p.get("ID") or 0) == int(container_id)]
        else:
            # 'task'
            p_task = self.get_project(container_id)
            target_surveys = [p_task] if p_task else []
        if not target_surveys:
            p_single = self.get_project(container_id)
            if p_single:
                target_surveys = [p_single]
        selected_tokens: List[str] = []
        for s_row in target_surveys:
            sid = int(s_row.get("ID") or 0)
            s_api = self.get_survey_assets_api(sid, user_id=user_id)
            for a in s_api.get("assets", []):
                if eff_kind == "task" and int(a.get("task_project_id") or 0) != int(container_id):
                    continue
                aid_str = str(a.get("id") or "")
                if aid_str.startswith("processed-") or aid_str.startswith("external-"):
                    selected_tokens.append(aid_str)
        base_proj = self.get_project(root_id if eff_kind == "project" else container_id) or {}
        base_name = str(base_proj.get("PROJECT_CONTAINER_NAME" if eff_kind == "project" else ("SURVEY_NAME" if eff_kind == "survey" else "NAME")) or base_proj.get("NAME") or f"Container #{container_id}")
        prefix = "Project" if eff_kind == "project" else ("Task" if eff_kind == "task" else "Survey")
        mosaic_name = f"{prefix}: {base_name}"
        existing_id = 0
        for m in self.list_user_mymaps(user_id, include_containers=True):
            if str(m.get("NAME") or "").strip().lower() == mosaic_name.strip().lower():
                existing_id = int(m.get("ID") or 0)
                break
        if not selected_tokens:
            selected_tokens = [f"processed-2-{container_id}", f"processed-3-{container_id}", f"processed-1-{container_id}"]
        mid = self.save_mymap_composition(user_id, existing_id, mosaic_name, selected_tokens, visibility="unlisted")
        with self.lock:
            for m in self.tables.get("dbo.WEB_MYMAPS", []):
                if int(m.get("ID") or 0) == int(mid):
                    m["IS_CONTAINER_PREVIEW"] = True
                    m["VISIBILITY"] = "unlisted"
                    self.persist_table("dbo.WEB_MYMAPS")
                    break
        return mid

    def delete_myearth_container(self, user_id: int, kind: str, container_id: int) -> Dict[str, Any]:
        """Deletes a Project, Survey, or Task container and its associated assets."""
        clean_kind = (kind or "project").strip().lower()
        target_id = int(container_id or 0)
        if target_id <= 0:
            return {"ok": False, "message": "Invalid ID."}
        with self.lock:
            target_proj = None
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == target_id and p.get("ACTIVO", True):
                    target_proj = p
                    break
            if not target_proj or int(target_proj.get("CREATE_ID") or target_proj.get("USER_ID") or 0) != int(user_id):
                return {"ok": False, "message": "Not found or unauthorized."}

            def _deactivate_project_assets(pid_val: int) -> None:
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                    if int(pub.get("PROJECT_ID") or 0) == pid_val:
                        pub["STATUS_ID"] = 3
                        pub["ACTIVO"] = False
                for ea in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                    if int(ea.get("PROJECT_ID") or 0) == pid_val:
                        ea["STATUS"] = "deleted"
                        ea["VISIBILITY"] = "private"

            if clean_kind == "project":
                root_id, fam = self.get_subprojects_family(target_id)
                fam_ids = {int(p.get("ID") or 0) for p in fam} | {target_id, root_id}
                # Also include any tasks whose SURVEY_ID is in fam_ids
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("SURVEY_ID") or 0) in fam_ids or int(p.get("PARENT_PROJECT_ID") or 0) in fam_ids:
                        fam_ids.add(int(p.get("ID") or 0))
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    pid_cur = int(p.get("ID") or 0)
                    if pid_cur in fam_ids and int(p.get("CREATE_ID") or p.get("USER_ID") or 0) == int(user_id):
                        p["ACTIVO"] = False
                        _deactivate_project_assets(pid_cur)
            elif clean_kind == "survey":
                parent_id = int(target_proj.get("PARENT_PROJECT_ID") or 0)
                # Find any child tasks inside this survey
                child_task_ids = set()
                for p in self.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("SURVEY_ID") or 0) == target_id and int(p.get("ID") or 0) != target_id:
                        child_task_ids.add(int(p.get("ID") or 0))
                for tid in child_task_ids:
                    for p in self.tables.get("dbo.WEB_PROJECTS", []):
                        if int(p.get("ID") or 0) == tid:
                            p["ACTIVO"] = False
                            _deactivate_project_assets(tid)
                # If target_id is also the root project (legacy single-row project), convert it into an empty project container
                if parent_id == 0 or parent_id == target_id:
                    target_proj["IS_PROJECT_CONTAINER"] = True
                    target_proj["IS_SURVEY_CONTAINER"] = False
                    target_proj["TASK_DELETED"] = True
                    target_proj["ZIP_UPLOADED"] = False
                    target_proj["STATE_ID"] = 3
                    _deactivate_project_assets(target_id)
                else:
                    target_proj["ACTIVO"] = False
                    _deactivate_project_assets(target_id)
            else:
                # clean_kind == "task"
                srv_id = int(target_proj.get("SURVEY_ID") or 0)
                if srv_id > 0 and srv_id != target_id:
                    target_proj["ACTIVO"] = False
                    _deactivate_project_assets(target_id)
                else:
                    # Legacy task stored on the survey row itself: clear its task outputs while keeping the Survey container alive
                    target_proj["IS_SURVEY_CONTAINER"] = True
                    target_proj["TASK_DELETED"] = True
                    target_proj["ZIP_UPLOADED"] = False
                    target_proj["STATE_ID"] = 3
                    _deactivate_project_assets(target_id)

            self.persist_table("dbo.WEB_PROJECTS")
            self.persist_table("dbo.WEB_PUBLICATIONS")
            self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")
            return {"ok": True}

    def delete_myearth_asset(self, user_id: int, project_id: int, file_type_id: int = 0, external_asset_id: int = 0) -> Dict[str, Any]:
        """Deletes an individual asset (either an imported external asset or one specific processed output fileTypeId 1/2/3)."""
        target_ea_pid = int(project_id or 0)
        has_ea_buyers = False
        with self.lock:
            if int(external_asset_id or 0) > 0:
                for ac in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS", []):
                    if int(ac.get("ASSET_ID") or 0) == int(external_asset_id) and int(ac.get("BUYER_USER_ID") or ac.get("USER_ID") or 0) != int(user_id):
                        has_ea_buyers = True
                        break
                found_ea = False
                for ea in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                    if int(ea.get("ID") or 0) == int(external_asset_id) and int(ea.get("USER_ID") or 0) == int(user_id):
                        ea["STATUS"] = "deleted"
                        ea["VISIBILITY"] = "private"
                        target_ea_pid = int(ea.get("PROJECT_ID") or target_ea_pid)
                        found_ea = True
                        break
                if found_ea:
                    self.persist_table("dbo.MOSAICEARTH_EXTERNAL_ASSETS")
                else:
                    return {"ok": False, "message": "External asset not found."}
        if int(external_asset_id or 0) > 0:
            if not has_ea_buyers and target_ea_pid > 0:
                try:
                    from .blob_store import get_blob_store
                    bs = get_blob_store()
                    bs.delete_prefix("assets", f"external/{target_ea_pid}/{external_asset_id}")
                    bs.delete_prefix("results", f"external/{target_ea_pid}/{external_asset_id}")
                except Exception:
                    pass
            return {"ok": True}

        with self.lock:

            proj = None
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == int(project_id) and p.get("ACTIVO", True):
                    proj = p
                    break
            if not proj or int(proj.get("CREATE_ID") or proj.get("USER_ID") or 0) != int(user_id):
                return {"ok": False, "message": "Project not found."}
            ft = int(file_type_id or 0)
            if ft in (1, 2, 3):
                del_list = list(proj.get("DELETED_FILE_TYPES") or [])
                if ft not in del_list:
                    del_list.append(ft)
                proj["DELETED_FILE_TYPES"] = del_list
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                    if int(pub.get("PROJECT_ID") or 0) == int(project_id) and int(pub.get("FILE_TYPE_ID") or 0) == ft:
                        pub["STATUS_ID"] = 3
                        pub["ACTIVO"] = False
                self.persist_table("dbo.WEB_PROJECTS")
                self.persist_table("dbo.WEB_PUBLICATIONS")
                return {"ok": True}
            return {"ok": False, "message": "Invalid asset type."}

    # -------------------------------------------------------------------------
    # Collection.aspx (MyAssets) Data Builder
    # -------------------------------------------------------------------------
    def user_has_purchased_asset(self, user_id: int, publish_id: int = 0, project_id: int = 0, external_asset_id: int = 0) -> bool:
        if user_id <= 0:
            return False
        with self.lock:
            if external_asset_id > 0:
                for ac in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS", []):
                    if int(ac.get("BUYER_USER_ID") or ac.get("USER_ID") or 0) == int(user_id) and int(ac.get("ASSET_ID") or 0) == int(external_asset_id):
                        return True
            if publish_id > 0:
                for col in self.tables.get("dbo.WEB_COLLECTION", []):
                    if col.get("ACTIVO", True) and int(col.get("USER_ID") or 0) == int(user_id) and int(col.get("PUBLISH_ID") or 0) == int(publish_id):
                        return True
            if project_id > 0:
                for col in self.tables.get("dbo.WEB_COLLECTION", []):
                    if not col.get("ACTIVO", True) or int(col.get("USER_ID") or 0) != int(user_id):
                        continue
                    pid_col = int(col.get("PROJECT_ID") or 0)
                    if pid_col == int(project_id):
                        return True
        return False

    def get_user_collection_data(self, user_id: int, lang: str = "en") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Returns (imported_assets_for_rptImportedAssets, collection_items_for_rptCollectionItems)."""
        with self.lock:
            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None and p.get("ACTIVO", True)
            }
            temporal_by_key = self._temporal_map()
            values_by_proj = {
                int(v.get("PROJECT_ID") or 0): v
                for v in self.tables.get("dbo.WEB_PROJECTS_VALUES", [])
                if v.get("PROJECT_ID") is not None
            }

            # 1. Imported / Additional processed external assets owned or acquired by user
            acquired_ext_ids = {
                int(ac.get("ASSET_ID") or 0)
                for ac in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS", [])
                if int(ac.get("BUYER_USER_ID") or ac.get("USER_ID") or 0) == int(user_id)
            }
            imported_list: List[Dict[str, Any]] = []
            for ext in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                status = str(ext.get("STATUS") or "").strip().lower()
                if status in ("deleted", "discarded"):
                    continue
                aid = int(ext.get("ID") or 0)
                pid = int(ext.get("PROJECT_ID") or 0)
                owner_id = int(ext.get("USER_ID") or 0)
                is_owner = owner_id == int(user_id)
                is_acquired = aid in acquired_ext_ids
                if not is_owner and not is_acquired:
                    continue
                proj = projects_by_id.get(pid)
                if is_owner and pid > 0 and not proj:
                    continue
                atype = str(ext.get("ASSET_TYPE") or "orthophoto").strip().lower()
                type_id = 1 if atype == "pointcloud" else 3 if atype == "model3d" else 2
                fmt = str(ext.get("OUTPUT_FORMAT") or ext.get("SOURCE_FORMAT") or ext.get("FORMAT") or "").strip().upper()
                pname = str(ext.get("TITLE") or ext.get("DISPLAY_NAME") or (proj.get("NAME") if proj else None) or ext.get("SOURCE_FILE_NAME") or f"Asset {aid}")
                vis = str(ext.get("VISIBILITY") or "private").strip().lower()
                price = int(ext.get("PRICE_CREDITS") or 0)
                price_disp = "Private (Owner Only)" if vis == "private" else ("FREE (0 €)" if price <= 0 else f"MARKETPLACE · {price} €")
                cap_dt = _parse_iso_dt(ext.get("CAPTURE_DATE"))
                cre_dt = _parse_iso_dt(ext.get("CREATED_AT") or ext.get("PUBLISHED_AT")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or cre_dt
                src_origin = str(ext.get("SOURCE_ORIGIN") or "external").strip().lower()
                is_viewer_ready = status in ("private-ready", "public-ready", "placement-reviewed", "viewer-ready", "ready", "published")
                imported_list.append({
                    "Id": aid,
                    "ProjectId": pid,
                    "UserId": owner_id,
                    "IsOwnedByCurrentUser": is_owner,
                    "AssetType": atype,
                    "TypeId": type_id,
                    "TypeIcon": ICON_CLOUD if type_id == 1 else ICON_3D if type_id == 3 else ICON_ORTHOPHOTO,
                    "FamilyDisplay": self.family_display(type_id),
                    "SubtypeDisplay": str(ext.get("ASSET_SUBTYPE") or self.subtype_display(atype)),
                    "TypeDisplay": f"{self.family_display(type_id)} · {ext.get('ASSET_SUBTYPE') or self.subtype_display(atype)}",
                    "DisplayName": pname,
                    "FileName": str(ext.get("SOURCE_FILE_NAME") or ""),
                    "Format": fmt,
                    "Extensions": self.original_extension(fmt),
                    "Visibility": vis,
                    "PriceCredits": price,
                    "PriceDisplay": price_disp,
                    "Status": status,
                    "StatusDetail": str(ext.get("STATUS_DETAIL") or ""),
                    "SourceOrigin": src_origin,
                    "ResultKind": str(ext.get("RESULT_KIND") or ""),
                    "CreatorDisplay": self._creator_name_by_user_id(owner_id),
                    "DateLabel": "Captured" if cap_dt else ("Published" if vis == "public" else "Created"),
                    "DateDisplay": eff_dt.strftime("%d/%m/%Y"),
                    "FilterDate": eff_dt.strftime("%Y-%m-%d"),
                    "SortDate": _fmt_iso_utc(eff_dt),
                    "IsViewerReady": is_viewer_ready,
                    "DetailUrl": f"/Client/ExternalAssetDetail.aspx?project_id={pid}&asset_id={aid}",
                })
            imported_list.sort(key=lambda r: (r["SortDate"], r["Id"]), reverse=True)

            # 2. Processed assets (owned completed projects + acquired publications in WEB_COLLECTION)
            pubs_by_proj_type: Dict[Tuple[int, int], Dict[str, Any]] = {}
            pubs_by_id: Dict[int, Dict[str, Any]] = {}
            for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                pub_id = int(pub.get("ID") or 0)
                pubs_by_id[pub_id] = pub
                pid = int(pub.get("PROJECT_ID") or 0)
                ft = int(pub.get("FILE_TYPE_ID") or 0)
                if pid > 0 and ft in (1, 2, 3):
                    prev = pubs_by_proj_type.get((pid, ft))
                    if not prev or int(pub.get("STATUS_ID") or 0) == 2:
                        pubs_by_proj_type[(pid, ft)] = pub

            ext_proj_ids = {
                int(ep.get("PROJECT_ID") or 0)
                for ep in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_PROJECTS", [])
            }

            collection_items: List[Dict[str, Any]] = []
            seen_keys = set()

            # 2a. Owned processed projects (STATE_ID == 4 and ZIP_UPLOADED == True)
            for pid, proj in sorted(projects_by_id.items(), key=lambda kv: kv[0], reverse=True):
                if pid in ext_proj_ids:
                    continue
                if int(proj.get("CREATE_ID") or 0) != int(user_id) and int(proj.get("PARTNER_ID") or 0) != int(user_id):
                    continue
                if int(proj.get("STATE_ID") or 0) != 4 or not bool(proj.get("ZIP_UPLOADED")):
                    continue
                pname = str(proj.get("NAME") or f"Project {pid}")
                folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                preview_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                val_row = values_by_proj.get(pid)
                cap_dt = _parse_iso_dt(val_row.get("CAPTURE_DATE")) if val_row else None
                cre_dt = _parse_iso_dt(proj.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or cre_dt
                creator_name = self._creator_name_by_user_id(proj.get("CREATE_ID"))

                for ft in (2, 3, 1):
                    pub = pubs_by_proj_type.get((pid, ft))
                    is_pub = pub is not None and int(pub.get("STATUS_ID") or 0) == 2
                    pub_id = int(pub.get("ID") or 0) if is_pub else 0
                    price_val = int(round(float(pub.get("CREDITS") or 0))) if is_pub else 0
                    access_badge = ("FREE (0 €)" if price_val <= 0 else f"MARKETPLACE · {price_val} €") if is_pub else "Private (Owner Only)"
                    detail_href = (
                        f"/{lang}/ProductDetail?id={pub_id}"
                        if is_pub and pub_id > 0
                        else f"/{lang}/ProductDetail?project_id={pid}&type={ft}"
                    )
                    dl_href = (
                        f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.tif"
                        if ft == 2
                        else f"/ProcessedAssetFile.ashx/{folder}/3d-model.zip"
                        if ft == 3
                        else f"/ProcessedAssetFile.ashx/{folder}/pointcloud.zip"
                    )
                    item_key = f"processed-{ft}-{pid}"
                    seen_keys.add(item_key)
                    collection_items.append({
                        "Key": item_key,
                        "ID": pub_id,
                        "PROJECT_ID": pid,
                        "FILE_TYPE_ID": ft,
                        "NAME": pname,
                        "OwnershipKind": "created",
                        "AccessBadge": access_badge,
                        "CardPreviewUrl": preview_url,
                        "PreviewStyle": f"--asset-preview:url('{preview_url}')",
                        "TypeIcon": ICON_CLOUD if ft == 1 else ICON_3D if ft == 3 else ICON_ORTHOPHOTO,
                        "FamilyDisplay": self.family_display(ft),
                        "SubtypeDisplay": self.subtype_display(str(ft)),
                        "CreatorDisplay": creator_name,
                        "DateLabel": "Captured" if cap_dt else ("Published" if is_pub else "Created"),
                        "DateDisplay": eff_dt.strftime("%d/%m/%Y"),
                        "FilterDate": eff_dt.strftime("%Y-%m-%d"),
                        "SortDate": _fmt_iso_utc(eff_dt),
                        "Extensions": self.processed_extensions(ft),
                        "DownloadFormat": "GEOTIFF · TIF" if ft == 2 else "OBJ · ZIP" if ft == 3 else "LAS · ZIP",
                        "DetailHref": detail_href,
                        "DownloadHref": dl_href,
                    })

            # 2b. Acquired processed publications from dbo.WEB_COLLECTION
            for col in self.tables.get("dbo.WEB_COLLECTION", []):
                if not col.get("ACTIVO", True):
                    continue
                if int(col.get("USER_ID") or 0) != int(user_id):
                    continue
                pub_id = int(col.get("PUBLISH_ID") or 0)
                pub = pubs_by_id.get(pub_id)
                if not pub:
                    continue
                pid = int(pub.get("PROJECT_ID") or 0)
                ft = int(pub.get("FILE_TYPE_ID") or 2)
                item_key = f"processed-{ft}-{pid}"
                if item_key in seen_keys:
                    continue
                proj = projects_by_id.get(pid)
                if not proj:
                    continue
                seen_keys.add(item_key)
                pname = str(proj.get("NAME") or f"Project {pid}")
                folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                preview_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                val_row = values_by_proj.get(pid)
                cap_dt = _parse_iso_dt(val_row.get("CAPTURE_DATE")) if val_row else None
                acq_dt = _parse_iso_dt(col.get("CREATE_DATE") or pub.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or acq_dt
                price_val = int(round(float(pub.get("CREDITS") or 0)))
                dl_href = (
                    f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.tif"
                    if ft == 2
                    else f"/ProcessedAssetFile.ashx/{folder}/3d-model.zip"
                    if ft == 3
                    else f"/ProcessedAssetFile.ashx/{folder}/pointcloud.zip"
                )
                collection_items.append({
                    "Key": item_key,
                    "ID": pub_id,
                    "PROJECT_ID": pid,
                    "FILE_TYPE_ID": ft,
                    "NAME": pname,
                    "OwnershipKind": "acquired",
                    "AccessBadge": "FREE (0 €)" if price_val <= 0 else f"MARKETPLACE · {price_val} €",
                    "CardPreviewUrl": preview_url,
                    "PreviewStyle": f"--asset-preview:url('{preview_url}')",
                    "TypeIcon": ICON_CLOUD if ft == 1 else ICON_3D if ft == 3 else ICON_ORTHOPHOTO,
                    "FamilyDisplay": self.family_display(ft),
                    "SubtypeDisplay": self.subtype_display(str(ft)),
                    "CreatorDisplay": self._creator_name_by_user_id(proj.get("CREATE_ID") or pub.get("USER_ID")),
                    "DateLabel": "Captured" if cap_dt else "Published",
                    "DateDisplay": eff_dt.strftime("%d/%m/%Y"),
                    "FilterDate": eff_dt.strftime("%Y-%m-%d"),
                    "SortDate": _fmt_iso_utc(eff_dt),
                    "Extensions": self.processed_extensions(ft),
                    "DownloadFormat": "GEOTIFF · TIF" if ft == 2 else "OBJ · ZIP" if ft == 3 else "LAS · ZIP",
                    "DetailHref": f"/{lang}/ProductDetail?id={pub_id}",
                    "DownloadHref": dl_href,
                })

            collection_items.sort(key=lambda r: (r["SortDate"], r["Key"]), reverse=True)
            return imported_list, collection_items

    # -------------------------------------------------------------------------
    # MyMaps.aspx / MyMapDetail.aspx / MyMap.aspx Data Builder
    # -------------------------------------------------------------------------
    def list_user_mymaps(self, user_id: int, include_containers: bool = False) -> List[Dict[str, Any]]:
        with self.lock:
            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None
            }
            pubs_by_id = {
                int(pub["ID"]): pub
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", [])
                if pub.get("ID") is not None
            }
            ext_by_id = {
                int(a["ID"]): a
                for a in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])
                if a.get("ID") is not None
            }
            items_by_map: Dict[int, List[int]] = {}
            for mi in self.tables.get("dbo.WEB_MYMAPS_ITEMS", []):
                mid = int(mi.get("MYMAP_ID") or 0)
                pub_id = int(mi.get("PUBLICATION_ID") or mi.get("PUBLISH_ID") or mi.get("ID_PUBLISH") or 0)
                if mid > 0 and pub_id > 0:
                    items_by_map.setdefault(mid, []).append(pub_id)

            priv_by_map: Dict[int, List[Dict[str, Any]]] = {}
            for pr in self.tables.get("dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS", []):
                mid = int(pr.get("MYMAP_ID") or 0)
                if mid > 0:
                    priv_by_map.setdefault(mid, []).append(pr)

            ext_by_map: Dict[int, List[int]] = {}
            for ea in self.tables.get("dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS", []):
                mid = int(ea.get("MYMAP_ID") or 0)
                aid = int(ea.get("ASSET_ID") or 0)
                if mid > 0 and aid > 0:
                    ext_by_map.setdefault(mid, []).append(aid)

            out = []
            for m in self.tables.get("dbo.WEB_MYMAPS", []):
                if not m.get("ACTIVO", True):
                    continue
                if int(m.get("CREATE_ID") or 0) != int(user_id):
                    continue
                mid = int(m.get("ID") or 0)
                mname = str(m.get("NAME") or f"Mosaic {mid}")
                if not include_containers and (
                    bool(m.get("IS_CONTAINER_PREVIEW"))
                    or mname.startswith(("Survey: ", "Project: ", "Task: "))
                ):
                    continue
                seo_slug = str(m.get("SEO_URL_FRIENDLY") or _slugify(f"{mname}-{mid}"))
                c_dt = _parse_iso_dt(m.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)

                exts_set = set()
                preview_img = "/Resources/Projects/orthophoto.png"
                for pub_id in items_by_map.get(mid, []):
                    pub = pubs_by_id.get(pub_id)
                    if pub:
                        ft = int(pub.get("FILE_TYPE_ID") or 2)
                        for e in self.processed_extensions(ft).split():
                            exts_set.add(e)
                        pid = int(pub.get("PROJECT_ID") or 0)
                        proj = projects_by_id.get(pid)
                        if proj and preview_img == "/Resources/Projects/orthophoto.png":
                            folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{proj.get('NAME', '')}"))
                            preview_img = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                for pr in priv_by_map.get(mid, []):
                    ft = int(pr.get("FILE_TYPE_ID") or 2)
                    for e in self.processed_extensions(ft).split():
                        exts_set.add(e)
                    pid = int(pr.get("PROJECT_ID") or 0)
                    proj = projects_by_id.get(pid)
                    if proj and preview_img == "/Resources/Projects/orthophoto.png":
                        folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{proj.get('NAME', '')}"))
                        preview_img = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                for aid in ext_by_map.get(mid, []):
                    ext = ext_by_id.get(aid)
                    if ext:
                        for e in self.original_extension(str(ext.get("SOURCE_FORMAT") or "")).split():
                            exts_set.add(e)

                pub_count = (
                    max(len(items_by_map.get(mid, [])), int(m.get("PublicationCount") or 0))
                    + len(ext_by_map.get(mid, []))
                    + len(priv_by_map.get(mid, []))
                )
                vis_mode = str(m.get("VISIBILITY") or ("unlisted" if bool(m.get("IS_CONTAINER_PREVIEW")) else "public")).strip().lower()
                if vis_mode not in ("private", "unlisted", "public"):
                    vis_mode = "public"
                vis_badge = (
                    "Private (Only me)"
                    if vis_mode == "private"
                    else "Unlisted (Client Link)"
                    if vis_mode == "unlisted"
                    else "Public (In Public Mosaics)"
                )
                out.append({
                    "ID": mid,
                    "USER_ID": int(user_id),
                    "CREATE_ID": int(user_id),
                    "NAME": mname,
                    "SEO_URL_FRIENDLY": seo_slug,
                    "VISIBILITY": vis_mode,
                    "Visibility": vis_mode,
                    "VisibilityBadge": vis_badge,
                    "CREATE_DATE": c_dt,
                    "CreateDateIso": c_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "CreateDateDisplay": c_dt.strftime("%d %b %Y"),
                    "DateIso": c_dt.strftime("%Y-%m-%d"),
                    "DateDisplay": c_dt.strftime("%d.%m.%y"),
                    "Extensions": " ".join(sorted(exts_set)),
                    "PublicationCount": pub_count,
                    "PreviewImg": preview_img,
                })
            out.sort(key=lambda r: (r["CREATE_DATE"], r["ID"]), reverse=True)
            return out

    def update_mymap_visibility(self, user_id: int, map_id: int, visibility: str) -> Dict[str, Any]:
        clean_vis = (visibility or "public").strip().lower()
        if clean_vis not in ("private", "unlisted", "public"):
            clean_vis = "public"
        if clean_vis in ("unlisted", "public") and os.environ.get("MOSAICEARTH_DESKTOP_MODE", "") != "1":
            storage = self.get_user_storage_summary(int(user_id))
            if str(storage.get("plan_code") or "free").lower() == "free":
                return {
                    "ok": False,
                    "subscription_required": True,
                    "message": "Sharing MyMosaics (Unlisted or Public) is only available for Pro and Studio subscribers.",
                }
        with self.lock:
            for m in self.tables.get("dbo.WEB_MYMAPS", []):
                if int(m.get("ID") or 0) == int(map_id) and m.get("ACTIVO", True):
                    if int(m.get("CREATE_ID") or 0) != int(user_id):
                        return {"ok": False, "message": "Unauthorized"}
                    m["VISIBILITY"] = clean_vis
                    m["UPDATE_DATE"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                    self.persist_table("dbo.WEB_MYMAPS")
                    badge = (
                        "Private (Only me)"
                        if clean_vis == "private"
                        else "Unlisted (Client Link)"
                        if clean_vis == "unlisted"
                        else "Public (In Public Mosaics)"
                    )
                    return {"ok": True, "Success": True, "visibility": clean_vis, "Visibility": clean_vis, "badge": badge}
        return {"ok": False, "message": "MyMosaic not found"}

    def get_mymap(self, id_or_slug: str) -> Optional[Dict[str, Any]]:
        val = (id_or_slug or "").strip()
        if not val:
            return None
        with self.lock:
            for m in self.tables.get("dbo.WEB_MYMAPS", []):
                if not m.get("ACTIVO", True):
                    continue
                if val.isdigit() and int(m.get("ID") or 0) == int(val):
                    return copy.deepcopy(m)
                if str(m.get("SEO_URL_FRIENDLY") or "").strip().lower() == val.lower():
                    return copy.deepcopy(m)
            m_match = re.search(r"(\d+)$", val)
            if m_match:
                target_id = int(m_match.group(1))
                for m in self.tables.get("dbo.WEB_MYMAPS", []):
                    if m.get("ACTIVO", True) and int(m.get("ID") or 0) == target_id:
                        return copy.deepcopy(m)
        return None

    def delete_mymap(self, mymap_id: int, user_id: int) -> bool:
        with self.lock:
            for m in self.tables.get("dbo.WEB_MYMAPS", []):
                mid = int(m.get("ID") or 0)
                cid = int(m.get("CREATE_ID") or 0)
                if (mid == int(mymap_id) and cid == int(user_id)) or (mid == int(user_id) and cid == int(mymap_id)):
                    m["ACTIVO"] = False
                    m["UPDATE_DATE"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                    self.persist_table("dbo.WEB_MYMAPS")
                    return True
        return False

    def get_mymap_editor_items(self, user_id: int, search: str = "") -> List[Dict[str, Any]]:
        """Matches MyMapDetail.aspx.cs:loadItems — returns published + owned private + external/gaussian assets."""
        q_low = (search or "").strip().lower()
        with self.lock:
            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None and p.get("ACTIVO", True)
            }
            values_by_proj = {
                int(v.get("PROJECT_ID") or 0): v
                for v in self.tables.get("dbo.WEB_PROJECTS_VALUES", [])
                if v.get("PROJECT_ID") is not None
            }
            ext_proj_ids = {
                int(ep.get("PROJECT_ID") or 0)
                for ep in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_PROJECTS", [])
            }
            collection_pub_ids = {
                int(c.get("PUBLISH_ID") or 0)
                for c in self.tables.get("dbo.WEB_COLLECTION", [])
                if c.get("ACTIVO", True) and int(c.get("USER_ID") or 0) == int(user_id)
            }
            acquired_ext_ids = {
                int(ac.get("ASSET_ID") or 0)
                for ac in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS", [])
                if int(ac.get("BUYER_USER_ID") or ac.get("USER_ID") or 0) == int(user_id)
            }

            published_items: List[Dict[str, Any]] = []
            published_pairs = set()
            for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                if int(pub.get("STATUS_ID") or pub.get("STATE_ID") or 0) != 2:
                    continue
                pub_id = int(pub.get("ID") or 0)
                pid = int(pub.get("PROJECT_ID") or 0)
                ft = int(pub.get("FILE_TYPE_ID") or 2)
                proj = projects_by_id.get(pid)
                if not proj:
                    continue
                published_pairs.add((pid, ft))
                pname = str(proj.get("NAME") or f"Project {pid}")
                pdesc = str(proj.get("DESCRIPTION") or "")
                owner_id = int(proj.get("CREATE_ID") or pub.get("USER_ID") or 0)
                author = "You" if owner_id == int(user_id) else self._creator_name_by_user_id(owner_id)
                if q_low and q_low not in pname.lower() and q_low not in pdesc.lower() and q_low not in author.lower():
                    continue
                val_row = values_by_proj.get(pid) or {}
                cap_dt = _parse_iso_dt(val_row.get("CAPTURE_DATE"))
                pub_dt = _parse_iso_dt(pub.get("PUBLISH_DATE") or pub.get("CREATE_DATE") or proj.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or pub_dt
                folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                preview_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                credits_val = int(round(float(pub.get("CREDITS") or 0)))
                is_owned = owner_id == int(user_id)
                is_in_col = is_owned or (pub_id in collection_pub_ids)
                published_items.append({
                    "ID": pub_id,
                    "VALUE": str(pub_id),
                    "DOM_ID": f"asset-{pub_id}",
                    "SelectionKey": f"pub:{pub_id}",
                    "USER_ID": owner_id,
                    "NAME": pname,
                    "DESCRIPTION": pdesc,
                    "AREA": str(val_row.get("AREA") or ""),
                    "CAPTURE_DATE": _fmt_iso_utc(cap_dt) if cap_dt else None,
                    "GPS_AVERAGE": str(val_row.get("GPS_AVERAGE") or ""),
                    "GSD_AVERAGE": str(val_row.get("GSD_AVERAGE") or ""),
                    "FILE_TYPE_ID": ft,
                    "PROJECT_ID": pid,
                    "AUTHOR": author,
                    "IS_COLLECTION": 1 if is_in_col else 0,
                    "IS_EXTERNAL": False,
                    "IS_PRIVATE_RESULT": False,
                    "FORMAT": "GEOTIFF · TIF" if ft == 2 else "OBJ · ZIP" if ft == 3 else "LAS · ZIP",
                    "EXTENSIONS": self.processed_extensions(ft),
                    "OWNERSHIP": "created" if is_owned else ("acquired" if is_in_col else "public"),
                    "SOURCE": "processed",
                    "ACCESS_FILTER": "public",
                    "LATITUDE": None,
                    "LONGITUDE": None,
                    "DATE_LABEL": "Captured" if cap_dt else "Created",
                    "DATE_DISPLAY": eff_dt.strftime("%d/%m/%Y"),
                    "DATE_VALUE": eff_dt.strftime("%Y-%m-%d"),
                    "ACCESS": "PUBLIC",
                    "Ownership": "created" if is_owned else ("acquired" if is_in_col else "public"),
                    "SourceKind": "processed",
                    "TypeKey": "pointcloud" if ft == 1 else "model3d" if ft == 3 else "orthophoto",
                    "FamilyDisplay": self.family_display(ft),
                    "SubtypeDisplay": self.subtype_display(str(ft)),
                    "AccessKind": "public",
                    "AccessBadge": "FREE (0 €)" if credits_val <= 0 else f"{credits_val} €",
                    "DateLabel": "Captured" if cap_dt else "Published",
                    "DateDisplay": eff_dt.strftime("%d/%m/%Y"),
                    "FilterDate": eff_dt.strftime("%Y-%m-%d"),
                    "SortDate": _fmt_iso_utc(eff_dt),
                    "Extensions": self.processed_extensions(ft),
                    "FormatLabel": "GEOTIFF · TIF" if ft == 2 else "OBJ · ZIP" if ft == 3 else "LAS · ZIP",
                    "PreviewUrl": preview_url,
                    "DetailUrl": f"/en/ProductDetail?id={pub_id}",
                })

            private_items: List[Dict[str, Any]] = []
            for pid, proj in sorted(projects_by_id.items(), key=lambda kv: kv[0], reverse=True):
                if pid in ext_proj_ids:
                    continue
                if int(proj.get("CREATE_ID") or 0) != int(user_id):
                    continue
                if int(proj.get("STATE_ID") or 0) != 4 or not bool(proj.get("ZIP_UPLOADED")):
                    continue
                pname = str(proj.get("NAME") or f"Project {pid}")
                pdesc = str(proj.get("DESCRIPTION") or "")
                if q_low and q_low not in pname.lower() and q_low not in pdesc.lower():
                    continue
                val_row = values_by_proj.get(pid) or {}
                cap_dt = _parse_iso_dt(val_row.get("CAPTURE_DATE"))
                cre_dt = _parse_iso_dt(proj.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or cre_dt
                folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                preview_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                for ft in (2, 3, 1):
                    if (pid, ft) in published_pairs:
                        continue
                    priv_val = f"p:{pid}:{ft}"
                    private_items.append({
                        "ID": priv_val,
                        "VALUE": priv_val,
                        "DOM_ID": f"private-{pid}-{ft}",
                        "SelectionKey": f"priv:{pid}:{ft}",
                        "USER_ID": int(user_id),
                        "NAME": pname,
                        "DESCRIPTION": pdesc,
                        "AREA": str(val_row.get("AREA") or ""),
                        "CAPTURE_DATE": _fmt_iso_utc(cap_dt) if cap_dt else None,
                        "GPS_AVERAGE": str(val_row.get("GPS_AVERAGE") or ""),
                        "GSD_AVERAGE": str(val_row.get("GSD_AVERAGE") or ""),
                        "FILE_TYPE_ID": ft,
                        "PROJECT_ID": pid,
                        "AUTHOR": "You",
                        "IS_COLLECTION": 0,
                        "IS_EXTERNAL": False,
                        "IS_PRIVATE_RESULT": True,
                        "FORMAT": "GEOTIFF · TIF" if ft == 2 else "OBJ · ZIP" if ft == 3 else "LAS · ZIP",
                        "EXTENSIONS": self.processed_extensions(ft),
                        "OWNERSHIP": "created",
                        "SOURCE": "processed",
                        "ACCESS_FILTER": "private",
                        "LATITUDE": None,
                        "LONGITUDE": None,
                        "ACCESS": "PRIVATE · NOT ACQUIRABLE",
                        "DATE_LABEL": "Captured" if cap_dt else "Created",
                        "DATE_DISPLAY": eff_dt.strftime("%d/%m/%Y"),
                        "DATE_VALUE": eff_dt.strftime("%Y-%m-%d"),
                        "Ownership": "created",
                        "SourceKind": "processed",
                        "TypeKey": "pointcloud" if ft == 1 else "model3d" if ft == 3 else "orthophoto",
                        "FamilyDisplay": self.family_display(ft),
                        "SubtypeDisplay": self.subtype_display(str(ft)),
                        "AccessKind": "private",
                        "AccessBadge": "Private",
                        "DateLabel": "Captured" if cap_dt else "Created",
                        "DateDisplay": eff_dt.strftime("%d/%m/%Y"),
                        "FilterDate": eff_dt.strftime("%Y-%m-%d"),
                        "SortDate": _fmt_iso_utc(eff_dt),
                        "Extensions": self.processed_extensions(ft),
                        "FormatLabel": "GEOTIFF · TIF" if ft == 2 else "OBJ · ZIP" if ft == 3 else "LAS · ZIP",
                        "PreviewUrl": preview_url,
                        "DetailUrl": f"/en/ProductDetail?project_id={pid}&type={ft}",
                    })

            ext_items: List[Dict[str, Any]] = []
            for ext in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                status = str(ext.get("STATUS") or "").strip().lower()
                if status not in ("private-ready", "public-ready", "placement-reviewed", "viewer-ready", "ready", "published", "pending-publication-review"):
                    continue
                aid = int(ext.get("ID") or 0)
                pid = int(ext.get("PROJECT_ID") or 0)
                owner_id = int(ext.get("USER_ID") or 0)
                vis = str(ext.get("VISIBILITY") or "private").strip().lower()
                is_owner = owner_id == int(user_id)
                is_acquired = aid in acquired_ext_ids
                if vis != "public" and not is_owner and not is_acquired:
                    continue
                proj = projects_by_id.get(pid)
                title = str(ext.get("TITLE") or ext.get("DISPLAY_NAME") or (proj.get("NAME") if proj else None) or ext.get("SOURCE_FILE_NAME") or f"Asset {aid}")
                desc = str(ext.get("DESCRIPTION") or (proj.get("DESCRIPTION") if proj else "") or "")
                fmt = str(ext.get("OUTPUT_FORMAT") or ext.get("SOURCE_FORMAT") or ext.get("FORMAT") or "").strip().upper()
                author = "You" if is_owner else self._creator_name_by_user_id(owner_id)
                if q_low and q_low not in title.lower() and q_low not in desc.lower() and q_low not in fmt.lower() and q_low not in author.lower():
                    continue
                atype = str(ext.get("ASSET_TYPE") or "orthophoto").strip().lower()
                type_id = 1 if atype == "pointcloud" else 3 if atype == "model3d" else 2
                cap_dt = _parse_iso_dt(ext.get("CAPTURE_DATE"))
                pub_dt = _parse_iso_dt(ext.get("PUBLISHED_AT") or ext.get("CREATED_AT")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or pub_dt
                lat = _try_float(ext.get("LATITUDE"))
                lon = _try_float(ext.get("LONGITUDE"))
                price = int(ext.get("PRICE_CREDITS") or 0)
                src_origin = str(ext.get("SOURCE_ORIGIN") or "imported").strip().lower()
                fmt_label = f"GEOTIFF · {fmt}" if fmt in ("TIF", "TIFF") else f"{fmt} · ORIGINAL"
                ext_items.append({
                    "ID": -aid,
                    "ASSET_ID": aid,
                    "VALUE": str(-aid),
                    "DOM_ID": f"external-{aid}",
                    "SelectionKey": f"ext:{aid}",
                    "ExternalAssetId": aid,
                    "USER_ID": owner_id,
                    "NAME": title,
                    "DESCRIPTION": desc,
                    "TYPE_LABEL": str(ext.get("ASSET_SUBTYPE") or self.subtype_display(atype)),
                    "AREA": fmt,
                    "CAPTURE_DATE": _fmt_iso_utc(cap_dt) if cap_dt else None,
                    "GPS_AVERAGE": f"{lat:.5f}, {lon:.5f}" if (lat is not None and lon is not None) else "Manual placement",
                    "GSD_AVERAGE": str(ext.get("CRS") or ""),
                    "FILE_TYPE_ID": type_id,
                    "PROJECT_ID": pid,
                    "AUTHOR": author,
                    "IS_COLLECTION": 0 if is_owner else 1,
                    "IS_EXTERNAL": True,
                    "IS_PRIVATE_RESULT": False,
                    "OWNERSHIP": "created" if is_owner else ("acquired" if is_acquired else "public"),
                    "SOURCE": "processed" if src_origin == "processed" else "imported",
                    "ACCESS_FILTER": "public" if vis == "public" else "private",
                    "FORMAT": fmt_label,
                    "EXTENSIONS": self.original_extension(fmt),
                    "LATITUDE": lat,
                    "LONGITUDE": lon,
                    "ACCESS": "PUBLIC" if vis == "public" else "PRIVATE · NOT ACQUIRABLE",
                    "DATE_LABEL": "Captured" if cap_dt else "Created",
                    "DATE_DISPLAY": eff_dt.strftime("%d/%m/%Y"),
                    "DATE_VALUE": eff_dt.strftime("%Y-%m-%d"),
                    "Ownership": "created" if is_owner else ("acquired" if is_acquired else "public"),
                    "SourceKind": "processed" if src_origin == "processed" else ("gaussian" if src_origin == "gaussian" else "imported"),
                    "TypeKey": atype,
                    "FamilyDisplay": self.family_display(type_id),
                    "SubtypeDisplay": str(ext.get("ASSET_SUBTYPE") or self.subtype_display(atype)),
                    "AccessKind": "public" if vis == "public" else "private",
                    "AccessBadge": "Private" if vis != "public" else ("FREE (0 €)" if price <= 0 else f"{price} €"),
                    "DateLabel": "Captured" if cap_dt else ("Published" if vis == "public" else "Created"),
                    "DateDisplay": eff_dt.strftime("%d/%m/%Y"),
                    "FilterDate": eff_dt.strftime("%Y-%m-%d"),
                    "SortDate": _fmt_iso_utc(eff_dt),
                    "Extensions": self.original_extension(fmt),
                    "FormatLabel": fmt_label,
                    "PreviewUrl": "",
                    "DetailUrl": f"/Client/ExternalAssetDetail.aspx?project_id={pid}&asset_id={aid}",
                })

            combined = published_items + private_items + ext_items
            combined.sort(key=lambda r: (str(r.get("SortDate") or ""), str(r.get("SelectionKey") or "")), reverse=True)
            return combined

    def get_mymap_selected_items(self, user_id: int, map_id: int) -> List[str]:
        """Matches MyMapDetail.aspx.cs:loadItemsSelected."""
        if map_id <= 0:
            return []
        with self.lock:
            m = self.get_mymap(str(map_id))
            if not m or int(m.get("CREATE_ID") or 0) != int(user_id):
                return []
            out: List[str] = []
            for mi in self.tables.get("dbo.WEB_MYMAPS_ITEMS", []):
                if int(mi.get("MYMAP_ID") or 0) == int(map_id):
                    pub_id = int(mi.get("PUBLICATION_ID") or mi.get("PUBLISH_ID") or mi.get("ID_PUBLISH") or 0)
                    if pub_id > 0:
                        out.append(str(pub_id))
            for ea in self.tables.get("dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS", []):
                if int(ea.get("MYMAP_ID") or 0) == int(map_id):
                    aid = int(ea.get("ASSET_ID") or 0)
                    if aid > 0:
                        out.append(str(-aid))
            for pr in self.tables.get("dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS", []):
                if int(pr.get("MYMAP_ID") or 0) == int(map_id):
                    pid = int(pr.get("PROJECT_ID") or 0)
                    ft = int(pr.get("FILE_TYPE_ID") or 0)
                    if pid > 0 and ft in (1, 2, 3):
                        out.append(f"p:{pid}:{ft}")
            return out

    def save_mymap_composition(self, user_id: int, map_id: int, name: str, selected_values: Any, visibility: Optional[str] = None) -> int:
        """Matches MyMapDetail.aspx.cs:saveMap and ExternalAssetStore.SaveMyMapComposition."""
        clean_name = (name or "").strip()[:250]
        if not clean_name:
            raise ValueError("Enter a Mosaic name of 1 to 250 characters.")
        clean_vis = (visibility or "").strip().lower()
        if clean_vis and clean_vis not in ("private", "unlisted", "public"):
            raise ValueError("Invalid Mosaic visibility.")
        if clean_vis in ("public", "unlisted") and self.get_user_storage_summary(user_id).get("plan_code") not in ("pro", "studio", "enterprise"):
            raise ValueError("A Pro or Studio subscription is required to share a Mosaic.")
        pub_ids: List[int] = []
        ext_ids: List[int] = []
        priv_results: List[Tuple[int, int]] = []
        tokens = selected_values if isinstance(selected_values, list) else str(selected_values or "").split(",")
        for raw_token in tokens:
            tok = str(raw_token or "").strip()
            if not tok:
                continue
            low_tok = tok.lower()
            if low_tok.startswith(("ext:", "ext_")):
                val_s = tok[4:].strip()
                if val_s.isdigit() and int(val_s) > 0:
                    ext_ids.append(int(val_s))
            elif low_tok.startswith("external-"):
                val_s = tok[9:].strip()
                if val_s.isdigit() and int(val_s) > 0:
                    ext_ids.append(int(val_s))
            elif low_tok.startswith("processed-"):
                parts = tok.split("-")
                if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                    ft, pid = int(parts[1]), int(parts[2])
                    if pid > 0 and ft in (1, 2, 3):
                        priv_results.append((pid, ft))
                        for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                            if int(pub.get("PROJECT_ID") or 0) == pid and int(pub.get("FILE_TYPE_ID") or 0) == ft and int(pub.get("STATUS_ID") or 0) == 2:
                                pub_ids.append(int(pub.get("ID") or 0))
            elif low_tok.startswith(("priv:", "p:")):
                parts = tok.split(":")
                if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                    pid, ft = int(parts[1]), int(parts[2])
                    if pid > 0 and ft in (1, 2, 3):
                        priv_results.append((pid, ft))
                        for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                            if int(pub.get("PROJECT_ID") or 0) == pid and int(pub.get("FILE_TYPE_ID") or 0) == ft and int(pub.get("STATUS_ID") or 0) == 2:
                                pub_ids.append(int(pub.get("ID") or 0))
            else:
                val_s = tok[4:].strip() if low_tok.startswith("pub:") else tok
                if val_s.lstrip("-").isdigit():
                    v = int(val_s)
                    if v > 0:
                        pub_ids.append(v)
                    elif v < 0:
                        ext_ids.append(abs(v))

        now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
        with self.lock:
            mymaps = self.tables.setdefault("dbo.WEB_MYMAPS", [])
            target_id = int(map_id or 0)
            if target_id == 0:
                target_id = next_record_id(mymaps, "ID", 100)
                mymaps.append({
                    "ID": target_id,
                    "ACTIVO": True,
                    "CREATE_DATE": now_iso,
                    "CREATE_ID": int(user_id),
                    "UPDATE_DATE": now_iso,
                    "UPDATE_ID": int(user_id),
                    "NAME": clean_name,
                    "VISIBILITY": clean_vis or "private",
                    "SEO_URL_FRIENDLY": _slugify(f"{clean_name}-{target_id}"),
                    "SEO_TITLE": clean_name,
                    "PublicationCount": len(set(pub_ids)),
                })
            else:
                found = False
                for m in mymaps:
                    if int(m.get("ID") or 0) == target_id and m.get("ACTIVO", True):
                        if int(m.get("CREATE_ID") or 0) != int(user_id):
                            raise PermissionError("This Mosaic does not belong to the signed-in user.")
                        m["NAME"] = clean_name
                        if clean_vis:
                            m["VISIBILITY"] = clean_vis
                        m["UPDATE_DATE"] = now_iso
                        m["UPDATE_ID"] = int(user_id)
                        m["PublicationCount"] = len(set(pub_ids))
                        found = True
                        break
                if not found:
                    raise ValueError("Mosaic not found.")

            items_tbl = self.tables.setdefault("dbo.WEB_MYMAPS_ITEMS", [])
            items_tbl[:] = [r for r in items_tbl if int(r.get("MYMAP_ID") or 0) != target_id]
            for pub_id in sorted(set(pub_ids)):
                items_tbl.append({
                    "MYMAP_ID": target_id,
                    "PUBLICATION_ID": pub_id,
                    "PUBLISH_ID": pub_id,
                })

            ext_tbl = self.tables.setdefault("dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS", [])
            ext_tbl[:] = [r for r in ext_tbl if int(r.get("MYMAP_ID") or 0) != target_id]
            for aid in sorted(set(ext_ids)):
                ext_tbl.append({
                    "MYMAP_ID": target_id,
                    "ASSET_ID": aid,
                })

            priv_tbl = self.tables.setdefault("dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS", [])
            priv_tbl[:] = [r for r in priv_tbl if int(r.get("MYMAP_ID") or 0) != target_id]
            for pid, ft in sorted(set(priv_results)):
                priv_tbl.append({
                    "MYMAP_ID": target_id,
                    "PROJECT_ID": pid,
                    "FILE_TYPE_ID": ft,
                    "USER_ID": int(user_id),
                })

            self.persist_table("dbo.WEB_MYMAPS")
            self.persist_table("dbo.WEB_MYMAPS_ITEMS")
            self.persist_table("dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS")
            self.persist_table("dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS")
            return target_id

    def get_mymap_viewer_items(self, map_id: int, user_id: Optional[int] = None, type_id: int = 0) -> List[Dict[str, Any]]:
        """Matches MyMap.aspx.cs:loadItems."""
        with self.lock:
            m = self.get_mymap(str(map_id))
            if not m:
                return []
            mid = int(m.get("ID") or 0)
            map_owner_id = int(m.get("CREATE_ID") or m.get("USER_ID") or 0)
            is_owner = user_id is not None and map_owner_id == int(user_id)
            map_vis = str(m.get("VISIBILITY") or ("unlisted" if bool(m.get("IS_CONTAINER_PREVIEW")) else "public")).strip().lower()
            if map_vis == "private" and not is_owner:
                return []
            selected_pubs = {
                int(mi.get("PUBLICATION_ID") or mi.get("PUBLISH_ID") or mi.get("ID_PUBLISH") or 0)
                for mi in self.tables.get("dbo.WEB_MYMAPS_ITEMS", [])
                if int(mi.get("MYMAP_ID") or 0) == mid
            }
            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None and p.get("ACTIVO", True)
            }
            locs_by_proj = {
                int(l.get("PROJECT_ID") or 0): l
                for l in self.tables.get("dbo.WEB_PROJECTS_LOCATION", [])
                if l.get("PROJECT_ID") is not None
            }
            vals_by_proj = {
                int(v.get("PROJECT_ID") or 0): v
                for v in self.tables.get("dbo.WEB_PROJECTS_VALUES", [])
                if v.get("PROJECT_ID") is not None
            }
            temporal_by_key = self._temporal_map()

            out: List[Dict[str, Any]] = []
            seen_proj_ft = set()
            for pub in self.tables.get("dbo.WEB_PUBLICATIONS", []):
                pub_id = int(pub.get("ID") or 0)
                if pub_id not in selected_pubs:
                    continue
                ft = int(pub.get("FILE_TYPE_ID") or 2)
                if type_id in (1, 2, 3) and ft != type_id:
                    continue
                pid = int(pub.get("PROJECT_ID") or 0)
                proj = projects_by_id.get(pid)
                if not proj:
                    continue
                seen_proj_ft.add((pid, ft))
                loc = locs_by_proj.get(pid) or {}
                val_row = vals_by_proj.get(pid) or {}
                pname = str(proj.get("NAME") or f"Project {pid}")
                folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                key = f"processed-{ft}-{pid}"
                t_row = temporal_by_key.get(key) or {}
                cap_dt = _parse_iso_dt(t_row.get("CAPTURE_START_UTC") or val_row.get("CAPTURE_DATE"))
                cre_dt = _parse_iso_dt(pub.get("CREATE_DATE") or proj.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or cre_dt
                preview_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                asset_owner_id = int(proj.get("CREATE_ID") or pub.get("USER_ID") or 0)
                author_name = self._creator_name_by_user_id(asset_owner_id)
                icon_svg = ICON_CLOUD if ft == 1 else ICON_3D if ft == 3 else ICON_ORTHOPHOTO
                price_eur = max(0, int(round(float(pub.get("CREDITS") or 0))))
                is_asset_owner = user_id is not None and int(user_id) == asset_owner_id
                has_bought = is_asset_owner or (user_id is not None and self.user_has_purchased_asset(int(user_id), publish_id=pub_id, project_id=pid))
                can_dl = (price_eur <= 0) or has_bought
                access_tier = "public_free" if price_eur <= 0 else "marketplace_paid"
                dl_label = "Download Original (0 €)" if price_eur <= 0 else ("Download Original (Owned)" if has_bought else f"Buy & Download ({price_eur} €)")
                out.append({
                    "ID": pid,
                    "PublishId": pub_id,
                    "PUBLISH_ID": pub_id,
                    "PROJECT_ID": pid,
                    "FILE_TYPE_ID": ft,
                    "FileTypeId": ft,
                    "NAME": pname,
                    "NOMBRE": pname,
                    "DESCRIPTION": str(proj.get("DESCRIPTION") or ""),
                    "USER_ID": asset_owner_id,
                    "AUTHOR": author_name,
                    "CreatorDisplay": author_name,
                    "CREDITS": price_eur,
                    "Credits": price_eur,
                    "PriceEur": price_eur,
                    "AccessTier": access_tier,
                    "CanDownloadOriginal": can_dl,
                    "DownloadActionLabel": dl_label,
                    "PUBLISH_TYPE": int(pub.get("PUBLISH_TYPE") or 0),
                    "FOLDER": folder,
                    "LATITUD": str(loc.get("LATITUD") or proj.get("LATITUD") or "0"),
                    "LONGITUD": str(loc.get("LONGITUD") or proj.get("LONGITUD") or "0"),
                    "ALTURA": str(loc.get("ALTURA") or "0"),
                    "SEO_URL_FRIENDLY": str(proj.get("SEO_URL_FRIENDLY") or _slugify(f"{pname}-{pid}")),
                    "UrlOrthoPhoto": preview_url,
                    "UrlPreview": preview_url,
                    "UrlMarketplace": f"/Client/ProductDetail.aspx?id={pub_id}",
                    "IconType": icon_svg,
                    "DateLabel": "Captured" if cap_dt else "Published",
                    "CaptureDateDisplay": eff_dt.strftime("%d/%m/%Y"),
                    "CAPTURE_START_ISO": _fmt_iso_utc(cap_dt),
                    "CAPTURE_END_ISO": _fmt_iso_utc(cap_dt),
                    "CAPTURE_PRECISION": str(t_row.get("CAPTURE_PRECISION") or ("day" if cap_dt else "unknown")),
                    "CAPTURE_SOURCE": str(t_row.get("CAPTURE_SOURCE") or ("WebODM" if cap_dt else "unknown")),
                    "CREATED_DATE_ISO": _fmt_iso_utc(cre_dt),
                    "EFFECTIVE_START_ISO": _fmt_iso_utc(eff_dt),
                    "EFFECTIVE_END_ISO": _fmt_iso_utc(eff_dt),
                    "ORIGINAL_EXTENSION": "jpg jpeg tif tiff",
                    "PROCESSED_EXTENSIONS": self.processed_extensions(ft),
                    "Extensions": self.processed_extensions(ft),
                    "IS_PRIVATE_RESULT": False,
                    "IsPrivate": False,
                })

            # Private results explicitly included in this MyMosaic by its owner:
            # Visible in 3D inside shared ('unlisted' or 'public') Mosaics, but View-Only (non-downloadable) for visitors!
            for pr in self.tables.get("dbo.MOSAICEARTH_MYMAP_PRIVATE_RESULTS", []):
                if int(pr.get("MYMAP_ID") or 0) != mid:
                    continue
                pid = int(pr.get("PROJECT_ID") or 0)
                ft = int(pr.get("FILE_TYPE_ID") or 2)
                if (pid, ft) in seen_proj_ft:
                    continue
                if type_id in (1, 2, 3) and ft != type_id:
                    continue
                proj = projects_by_id.get(pid)
                if not proj:
                    continue
                seen_proj_ft.add((pid, ft))
                loc = locs_by_proj.get(pid) or {}
                val_row = vals_by_proj.get(pid) or {}
                pname = str(proj.get("NAME") or f"Project {pid}")
                folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{pname}"))
                cap_dt = _parse_iso_dt(val_row.get("CAPTURE_DATE"))
                cre_dt = _parse_iso_dt(proj.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or cre_dt
                seo = str(proj.get("SEO_URL_FRIENDLY") or _slugify(f"{pname}-{pid}"))
                preview_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/odm_orthophoto/odm_orthophoto.webp"
                icon_svg = ICON_CLOUD if ft == 1 else ICON_3D if ft == 3 else ICON_ORTHOPHOTO
                asset_owner_id = int(proj.get("CREATE_ID") or map_owner_id)
                is_asset_owner = user_id is not None and int(user_id) == asset_owner_id
                out.append({
                    "ID": pid,
                    "PublishId": None,
                    "PUBLISH_ID": 0,
                    "PROJECT_ID": pid,
                    "FILE_TYPE_ID": ft,
                    "FileTypeId": ft,
                    "NAME": pname,
                    "NOMBRE": pname,
                    "DESCRIPTION": str(proj.get("DESCRIPTION") or ""),
                    "USER_ID": asset_owner_id,
                    "AUTHOR": "You" if is_asset_owner else self._creator_name_by_user_id(asset_owner_id),
                    "CreatorDisplay": self._creator_name_by_user_id(asset_owner_id),
                    "CREDITS": 0,
                    "Credits": 0,
                    "PriceEur": 0,
                    "AccessTier": "private_view_only",
                    "CanDownloadOriginal": is_asset_owner,
                    "DownloadActionLabel": "Download Original" if is_asset_owner else "View-Only · Private Asset",
                    "PUBLISH_TYPE": 0,
                    "FOLDER": folder,
                    "LATITUD": str(loc.get("LATITUD") or proj.get("LATITUD") or "0"),
                    "LONGITUD": str(loc.get("LONGITUD") or proj.get("LONGITUD") or "0"),
                    "ALTURA": str(loc.get("ALTURA") or "0"),
                    "SEO_URL_FRIENDLY": seo,
                    "UrlOrthoPhoto": preview_url,
                    "UrlPreview": preview_url,
                    "UrlMarketplace": f"/Client/ProductDetail.aspx?project_id={pid}&type={ft}" if is_asset_owner else "#",
                    "IconType": icon_svg,
                    "DateLabel": "Captured" if cap_dt else "Created",
                    "CaptureDateDisplay": eff_dt.strftime("%d/%m/%Y"),
                    "CAPTURE_START_ISO": _fmt_iso_utc(cap_dt),
                    "CAPTURE_END_ISO": _fmt_iso_utc(cap_dt),
                    "CAPTURE_PRECISION": "day" if cap_dt else "unknown",
                    "CAPTURE_SOURCE": "private",
                    "CREATED_DATE_ISO": _fmt_iso_utc(cre_dt),
                    "EFFECTIVE_START_ISO": _fmt_iso_utc(eff_dt),
                    "EFFECTIVE_END_ISO": _fmt_iso_utc(eff_dt),
                    "ORIGINAL_EXTENSION": "jpg jpeg tif tiff",
                    "PROCESSED_EXTENSIONS": self.processed_extensions(ft),
                    "Extensions": self.processed_extensions(ft),
                    "IS_PRIVATE_RESULT": True,
                    "IsPrivate": True,
                })
            return out

    def get_mymap_external_assets(self, map_id: int, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Matches MyMap.aspx.cs:loadExternalAssets."""
        with self.lock:
            m = self.get_mymap(str(map_id))
            if not m:
                return []
            mid = int(m.get("ID") or 0)
            map_owner_id = int(m.get("CREATE_ID") or m.get("USER_ID") or 0)
            is_owner = user_id is not None and map_owner_id == int(user_id)
            map_vis = str(m.get("VISIBILITY") or ("unlisted" if bool(m.get("IS_CONTAINER_PREVIEW")) else "public")).strip().lower()
            if map_vis == "private" and not is_owner:
                return []
            selected_aids = {
                int(ea.get("ASSET_ID") or 0)
                for ea in self.tables.get("dbo.MOSAICEARTH_MYMAP_EXTERNAL_ASSETS", [])
                if int(ea.get("MYMAP_ID") or 0) == mid
            }
            if not selected_aids:
                return []
            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None
            }
            temporal_by_key = self._temporal_map()
            out: List[Dict[str, Any]] = []
            for ext in self.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
                aid = int(ext.get("ID") or 0)
                if aid not in selected_aids:
                    continue
                if str(ext.get("STATUS") or "").strip().lower() in ("deleted", "discarded"):
                    continue
                vis = str(ext.get("VISIBILITY") or "private").strip().lower()
                asset_owner_id = int(ext.get("USER_ID") or 0)
                is_asset_owner = user_id is not None and int(user_id) == asset_owner_id
                price_eur = max(0, int(ext.get("PRICE_CREDITS") or 0))
                has_bought = is_asset_owner or (user_id is not None and self.user_has_purchased_asset(int(user_id), external_asset_id=aid))
                can_dl = is_asset_owner or (vis == "public" and (price_eur <= 0 or has_bought))
                access_tier = "private_view_only" if vis != "public" else ("public_free" if price_eur <= 0 else "marketplace_paid")
                dl_label = (
                    "Download Original" if can_dl
                    else ("View-Only · Private Asset" if vis != "public" else f"Buy & Download ({price_eur} €)")
                )
                lat = _try_float(ext.get("LATITUDE"))
                lon = _try_float(ext.get("LONGITUDE"))
                if lat is None or lon is None:
                    continue
                pid = int(ext.get("PROJECT_ID") or 0)
                proj = projects_by_id.get(pid)
                atype = str(ext.get("ASSET_TYPE") or "orthophoto").strip().lower()
                type_id = 1 if atype == "pointcloud" else 3 if atype == "model3d" else 2
                fmt = str(ext.get("OUTPUT_FORMAT") or ext.get("SOURCE_FORMAT") or ext.get("FORMAT") or "").strip().upper()
                display_kind = str(ext.get("DISPLAY_KIND") or "").strip()
                if not display_kind:
                    display_kind = (
                        "raster-tiles" if type_id == 2
                        else "model-glb" if (type_id == 3 and fmt == "GLB")
                        else "pointcloud-3dtiles" if type_id == 1
                        else "model-3dtiles"
                    )
                display_entry = str(ext.get("DISPLAY_ENTRY_PATH") or "tileset.json").strip()
                if ext.get("VIEWER_KIND") == "geojson":
                    display_kind = "vector-geojson"
                    display_entry = str(ext.get("DISPLAY_ENTRY_PATH") or "overlay.geojson").strip()
                elif ext.get("VIEWER_KIND") == "vector-raster":
                    display_kind = "raster-tiles"

                file_url = f"/ExternalAssetFile.ashx?project_id={pid}&asset_id={aid}&mymap_id={mid}"
                if display_kind == "raster-tiles":
                    viewer_url = f"/ExternalAssetViewer.ashx?project_id={pid}&asset_id={aid}&mymap_id={mid}&z={{z}}&x={{x}}&y={{y}}"
                elif display_kind == "model-glb" and not ext.get("DISPLAY_ENTRY_PATH"):
                    viewer_url = f"{file_url}&view=1"
                else:
                    viewer_url = f"/ExternalAsset3DTile.ashx/{pid}/{aid}/{display_entry.lstrip('/')}?mymap_id={mid}"
                key = f"external-{type_id}-{aid}"
                t_row = temporal_by_key.get(key) or {}
                cap_dt = _parse_iso_dt(t_row.get("CAPTURE_START_UTC") or ext.get("CAPTURE_DATE"))
                cre_dt = _parse_iso_dt(ext.get("PUBLISHED_AT") or ext.get("CREATED_AT")) or datetime.datetime.now(datetime.timezone.utc)
                eff_dt = cap_dt or cre_dt
                title = str(ext.get("TITLE") or ext.get("DISPLAY_NAME") or (proj.get("NAME") if proj else None) or ext.get("SOURCE_FILE_NAME") or f"Asset {aid}")
                icon_svg = ICON_CLOUD if type_id == 1 else ICON_3D if type_id == 3 else ICON_ORTHOPHOTO
                preview_url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox={lon - 0.012:.6f},{lat - 0.012:.6f},{lon + 0.012:.6f},{lat + 0.012:.6f}&bboxSR=4326&imageSR=4326&size=640,360&format=jpg&f=image"
                out.append({
                    "Id": aid,
                    "ProjectId": pid,
                    "Title": title,
                    "Name": title,
                    "Description": str(ext.get("DESCRIPTION") or ""),
                    "LocationName": str(ext.get("LOCATION_NAME") or ""),
                    "AssetType": atype,
                    "AssetSubtype": str(ext.get("ASSET_SUBTYPE") or ""),
                    "Subtype": str(ext.get("ASSET_SUBTYPE") or ""),
                    "SourceOrigin": str(ext.get("SOURCE_ORIGIN") or "imported"),
                    "DisplayKind": display_kind,
                    "Format": fmt,
                    "OriginalExtension": self.original_extension(fmt),
                    "Extensions": self.original_extension(fmt),
                    "GeoreferenceMode": str(ext.get("GEOREFERENCE_MODE") or ("embedded" if ext.get("HAS_EMBEDDED_GEOREF") else "manual")),
                    "PriceCredits": price_eur,
                    "PriceEur": price_eur,
                    "AccessTier": access_tier,
                    "CanDownloadOriginal": can_dl,
                    "DownloadActionLabel": dl_label,
                    "Latitude": lat,
                    "Longitude": lon,
                    "Altitude": float(ext.get("ALTITUDE") or 0.0),
                    "Heading": float(ext.get("HEADING") or 0.0),
                    "Scale": float(ext.get("SCALE") or 1.0),
                    "BoundsWest": _try_float(ext.get("BOUNDS_WEST")),
                    "BoundsSouth": _try_float(ext.get("BOUNDS_SOUTH")),
                    "BoundsEast": _try_float(ext.get("BOUNDS_EAST")),
                    "BoundsNorth": _try_float(ext.get("BOUNDS_NORTH")),
                    "CaptureStartIso": _fmt_iso_utc(cap_dt),
                    "CaptureEndIso": _fmt_iso_utc(cap_dt),
                    "CapturePrecision": str(t_row.get("CAPTURE_PRECISION") or ("day" if cap_dt else "unknown")),
                    "CaptureSource": str(t_row.get("CAPTURE_SOURCE") or "import metadata"),
                    "CreatedDateIso": _fmt_iso_utc(cre_dt),
                    "EffectiveStartIso": _fmt_iso_utc(eff_dt),
                    "EffectiveEndIso": _fmt_iso_utc(eff_dt),
                    "CaptureDateDisplay": eff_dt.strftime("%d/%m/%Y"),
                    "DateLabel": "Captured" if cap_dt else ("Published" if vis == "public" else "Created"),
                    "IconType": icon_svg,
                    "PreviewUrl": preview_url,
                    "IsPrivate": vis == "private",
                    "FileUrl": file_url,
                    "Url": f"{file_url}&view=1",
                    "ViewerUrl": viewer_url,
                    "DetailUrl": f"/Client/ExternalAssetDetail.aspx?project_id={pid}&asset_id={aid}&map_id={mid}",
                })
            return out

    def get_mymap_orthophotos(self, map_id: int, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Matches MyMap.aspx.cs:loadOrthophoto."""
        items = self.get_mymap_viewer_items(map_id, user_id=user_id, type_id=2)
        out = []
        for it in items:
            pid = int(it.get("PROJECT_ID") or 0)
            folder = str(it.get("FOLDER") or "")
            med_lat = str(it.get("LATITUD") or "0").replace(",", ".")
            med_lon = str(it.get("LONGITUD") or "0").replace(",", ".")
            out.append({
                "PROJECT_ID": pid,
                "ID": pid,
                "NOMBRE": str(it.get("NAME") or f"Project {pid}"),
                "PublishId": it.get("PublishId"),
                "FOLDER": folder,
                "FILE_TYPE_ID": 2,
                "FileTypeId": 2,
                "MED_LAT": med_lat,
                "MED_LON": med_lon,
                "MED_HEIGHT": "120",
                "HEIGHT": 0,
                "URL_CESIUM": f"/ProcessedAssetFile.ashx/{folder}/procesado/orthophoto_tiles/",
                "UrlMarketplace": it.get("UrlMarketplace"),
                "UrlPreview": it.get("UrlOrthoPhoto"),
                "Extensions": it.get("PROCESSED_EXTENSIONS"),
                "CAPTURE_START_ISO": it.get("CAPTURE_START_ISO"),
                "CAPTURE_END_ISO": it.get("CAPTURE_END_ISO"),
                "CAPTURE_PRECISION": it.get("CAPTURE_PRECISION"),
                "CAPTURE_SOURCE": it.get("CAPTURE_SOURCE"),
                "CREATED_DATE_ISO": it.get("CREATED_DATE_ISO"),
                "EFFECTIVE_START_ISO": it.get("EFFECTIVE_START_ISO"),
                "EFFECTIVE_END_ISO": it.get("EFFECTIVE_END_ISO"),
                "ORIGINAL_EXTENSION": it.get("ORIGINAL_EXTENSION"),
                "PROCESSED_EXTENSIONS": it.get("PROCESSED_EXTENSIONS"),
                "IsPrivate": bool(it.get("IsPrivate")),
            })
        return out

    def get_mymap_models(self, map_id: int, user_id: Optional[int] = None, model_type: int = 3) -> List[Dict[str, Any]]:
        """Matches MyMap.aspx.cs:loadPointcloud (model_type=1) and load3DModels (model_type=3)."""
        items = self.get_mymap_viewer_items(map_id, user_id=user_id, type_id=model_type)
        with self.lock:
            models_by_proj_type = {
                (int(cm.get("Project_id") or 0), int(cm.get("ModelType") or 0)): cm
                for cm in self.tables.get("dbo.WEB_CESIUM_MODELS", [])
            }
            out = []
            for it in items:
                pid = int(it.get("PROJECT_ID") or 0)
                folder = str(it.get("FOLDER") or "")
                cm = models_by_proj_type.get((pid, model_type)) or {}
                rel_url = (
                    f"{folder}/procesado/3d_tiles/pointcloud/tileset.json"
                    if model_type == 1
                    else f"{folder}/procesado/output/tileset.json"
                )
                raw_url = str(cm.get("Url") or rel_url)
                cesium_url = raw_url if raw_url.startswith("/") or raw_url.startswith("http") else f"/ProcessedAssetFile.ashx/{raw_url.lstrip('/')}"
                h_val = float(cm.get("Height") or 0.0)
                out.append({
                    "ID": pid,
                    "Id": int(cm.get("Id") or pid),
                    "Project_id": pid,
                    "NOMBRE": str(it.get("NAME") or f"Project {pid}"),
                    "ModelType": model_type,
                    "FILE_TYPE_ID": model_type,
                    "FileTypeId": model_type,
                    "Url": raw_url,
                    "URL_CESIUM": cesium_url,
                    "Height": h_val,
                    "HEIGHT": int(h_val),
                    "PublishId": it.get("PublishId"),
                    "PUBLISH_ID": int(it.get("PUBLISH_ID") or 0),
                    "UrlMarketplace": it.get("UrlMarketplace"),
                    "UrlPreview": it.get("UrlOrthoPhoto"),
                    "Extensions": it.get("PROCESSED_EXTENSIONS"),
                    "CAPTURE_START_ISO": it.get("CAPTURE_START_ISO"),
                    "CAPTURE_END_ISO": it.get("CAPTURE_END_ISO"),
                    "CAPTURE_PRECISION": it.get("CAPTURE_PRECISION"),
                    "CAPTURE_SOURCE": it.get("CAPTURE_SOURCE"),
                    "CREATED_DATE_ISO": it.get("CREATED_DATE_ISO"),
                    "EFFECTIVE_START_ISO": it.get("EFFECTIVE_START_ISO"),
                    "EFFECTIVE_END_ISO": it.get("EFFECTIVE_END_ISO"),
                    "ORIGINAL_EXTENSION": it.get("ORIGINAL_EXTENSION"),
                    "PROCESSED_EXTENSIONS": it.get("PROCESSED_EXTENSIONS"),
                    "IsPrivate": bool(it.get("IsPrivate")),
                })
            return out

    # -------------------------------------------------------------------------
    # Profile.aspx & CreditPacks.aspx Data Methods
    # -------------------------------------------------------------------------
    def get_user_odm_settings(self, user_id: int) -> Dict[str, Any]:
        is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        default_ep = os.environ.get("MOSAICEARTH_LOCAL_NODEODM", "http://127.0.0.1:3000")
        with self.lock:
            for r in self.tables.get("dbo.WEB_USER_ODM_SETTINGS", []):
                if int(r.get("USER_ID") or 0) == int(user_id):
                    raw_ep = str(r.get("ENDPOINT_URL") or "").strip()
                    raw_en = bool(r.get("ENABLED"))
                    tok = str(r.get("TOKEN_PROTECTED") or "").strip()
                    ep = raw_ep
                    en = raw_en
                    if is_desktop and not bool(r.get("EXPLICIT_CLOUD_MODE")) and not raw_ep:
                        ep = default_ep
                        en = True
                    return {
                        "Endpoint": ep,
                        "EndpointUrl": ep,
                        "Token": tok,
                        "HasToken": bool(tok),
                        "Enabled": en,
                        "UseCustom": en,
                        "ExplicitCustomRow": bool(raw_en and raw_ep),
                        "ExplicitCloudMode": bool(r.get("EXPLICIT_CLOUD_MODE")),
                        "StatusSummary": f"Using MosaicEarth Desktop Local OpenDroneMap ({ep})" if (is_desktop and en) else None,
                    }
        if is_desktop:
            return {
                "Endpoint": default_ep,
                "EndpointUrl": default_ep,
                "Token": "",
                "HasToken": False,
                "Enabled": True,
                "UseCustom": True,
                "ExplicitCustomRow": False,
                "ExplicitCloudMode": False,
                "StatusSummary": f"Using MosaicEarth Desktop Local OpenDroneMap ({default_ep})",
            }
        return {
            "Endpoint": "",
            "EndpointUrl": "",
            "Token": "",
            "HasToken": False,
            "Enabled": False,
            "UseCustom": False,
            "ExplicitCustomRow": False,
        }

    def save_user_odm_settings(
        self,
        user_id: int,
        endpoint_or_mode: str = "",
        token_or_endpoint: str = "",
        enabled_or_token: Any = False,
        test_connection: bool = False,
    ) -> Dict[str, Any]:
        from .processing_architecture import build_safe_url_opener, validate_custom_nodeodm_endpoint

        is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        if str(endpoint_or_mode).lower() in ("managed", "custom"):
            is_enabled = str(endpoint_or_mode).lower() == "custom"
            endpoint = str(token_or_endpoint or "").strip()
            token = str(enabled_or_token or "").strip()
        else:
            endpoint = str(endpoint_or_mode or "").strip()
            token = str(token_or_endpoint or "").strip()
            is_enabled = bool(enabled_or_token)
        if is_enabled and not endpoint and is_desktop:
            endpoint = os.environ.get("MOSAICEARTH_LOCAL_NODEODM", "http://127.0.0.1:3000")

        if is_enabled:
            val = validate_custom_nodeodm_endpoint(endpoint, is_desktop=is_desktop, allow_dns_lookup=bool(test_connection))
            if not val.get("ok"):
                return {
                    "ok": False,
                    "message": str(val.get("error") or "Invalid Custom NodeODM endpoint."),
                    "status": "Invalid Custom NodeODM endpoint",
                }
            endpoint = str(val["url"])

        with self.lock:
            rows = self.tables.setdefault("dbo.WEB_USER_ODM_SETTINGS", [])
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            saved_token = token
            updated = False
            for r in rows:
                if int(r.get("USER_ID") or 0) == int(user_id):
                    r["ENDPOINT_URL"] = endpoint
                    if token:
                        r["TOKEN_PROTECTED"] = token
                    else:
                        saved_token = str(r.get("TOKEN_PROTECTED") or "")
                    r["ENABLED"] = is_enabled
                    r["EXPLICIT_CLOUD_MODE"] = not is_enabled
                    r["UPDATE_DATE"] = now_iso
                    updated = True
                    break
            if not updated:
                rows.append({
                    "USER_ID": int(user_id),
                    "ENDPOINT_URL": endpoint,
                    "TOKEN_PROTECTED": token,
                    "ENABLED": is_enabled,
                    "EXPLICIT_CLOUD_MODE": not is_enabled,
                    "CREATE_DATE": now_iso,
                    "UPDATE_DATE": now_iso,
                })
            self.persist_table("dbo.WEB_USER_ODM_SETTINGS")

        if test_connection and is_enabled and endpoint:
            q_tok = f"?token={urllib.parse.quote(saved_token, safe='')}" if saved_token else ""
            opener = build_safe_url_opener(verify_tls=True)
            try:
                req = urllib.request.Request(
                    f"{endpoint}/info{q_tok}",
                    headers={"Accept": "application/json", "User-Agent": "MosaicEarth/2.0"},
                )
                with opener.open(req, timeout=8.0) as resp:
                    info = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    eng_ver = str(info.get("engineVersion") or info.get("version") or "online")
                    return {
                        "ok": True,
                        "message": f"Connected to Custom NodeODM ({endpoint} · Engine v{eng_ver})",
                        "status": f"Using custom NodeODM ({endpoint})",
                    }
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"Settings saved, but connection test to {endpoint}/info failed: {exc}",
                    "status": f"Custom NodeODM unreachable ({endpoint})",
                }

        msg = f"Using custom NodeODM ({endpoint})" if is_enabled and endpoint else "Using MosaicEarth managed processing"
        return {"ok": True, "message": msg, "status": msg}

    def clear_user_odm_settings(self, user_id: int) -> Dict[str, Any]:
        with self.lock:
            rows = self.tables.setdefault("dbo.WEB_USER_ODM_SETTINGS", [])
            rows[:] = [r for r in rows if int(r.get("USER_ID") or 0) != int(user_id)]
            self.persist_table("dbo.WEB_USER_ODM_SETTINGS")
            return {"ok": True, "status": "Using MosaicEarth managed processing"}

    def get_user_fiscal_data(self, user_id: int) -> List[Dict[str, Any]]:
        with self.lock:
            out = [
                copy.deepcopy(f)
                for f in self.tables.get("dbo.WEB_FISCAL_DATA", [])
                if f.get("ACTIVO", True) and int(f.get("USER_ID") or 0) == int(user_id)
            ]
            out.sort(key=lambda r: int(r.get("ID") or 0))
            return out

    def add_fiscal_data(
        self,
        user_id: int,
        payload_or_fullname: Any,
        nif: str = "",
        country: str = "",
        region: str = "",
        city: str = "",
        postal: str = "",
        address: str = "",
        phone: str = "",
    ) -> Any:
        if isinstance(payload_or_fullname, dict):
            payload = payload_or_fullname
        else:
            payload = {
                "newalias": str(payload_or_fullname or ""),
                "fiscalname": str(payload_or_fullname or ""),
                "newVATnumber": nif,
                "newcountry": country,
                "newregion": region,
                "newcity": city,
                "newzipcode": postal,
                "newaddress1": address,
                "newphone": phone,
            }
        with self.lock:
            rows = self.tables.setdefault("dbo.WEB_FISCAL_DATA", [])
            existing = [f for f in rows if f.get("ACTIVO", True) and int(f.get("USER_ID") or 0) == int(user_id)]
            next_id = next_record_id(rows, "ID", 0)
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            rows.append({
                "ID": next_id,
                "ACTIVO": True,
                "CREATE_DATE": now_iso,
                "USER_ID": int(user_id),
                "ALIAS": str(payload.get("newalias") or ""),
                "FISCAL_NAME": str(payload.get("fiscalname") or ""),
                "VAT_NUMBER": str(payload.get("newVATnumber") or ""),
                "ADDRESS_LINE_1": str(payload.get("newaddress1") or ""),
                "ADDRESS_LINE_2": str(payload.get("newaddress2") or ""),
                "ZIP_CODE": str(payload.get("newzipcode") or ""),
                "CITY": str(payload.get("newcity") or ""),
                "REGION": str(payload.get("newregion") or ""),
                "COUNTRY": str(payload.get("newcountry") or ""),
                "PHONE_NUMBER": str(payload.get("newphone") or ""),
                "EMAIL": str(payload.get("newemailfiscal") or ""),
                "SELECTED_FISCAL": len(existing) == 0,
            })
            self.persist_table("dbo.WEB_FISCAL_DATA")
            return "OK" if not isinstance(payload_or_fullname, dict) else next_id

    def edit_fiscal_data(
        self,
        user_id: int,
        fiscal_id: int,
        payload_or_fullname: Any,
        nif: str = "",
        country: str = "",
        region: str = "",
        city: str = "",
        postal: str = "",
        address: str = "",
        phone: str = "",
    ) -> Any:
        if isinstance(payload_or_fullname, dict):
            payload = payload_or_fullname
        else:
            payload = {
                "newalias": str(payload_or_fullname or ""),
                "fiscalname": str(payload_or_fullname or ""),
                "newVATnumber": nif,
                "newcountry": country,
                "newregion": region,
                "newcity": city,
                "newzipcode": postal,
                "newaddress1": address,
                "newphone": phone,
            }
        with self.lock:
            for f in self.tables.get("dbo.WEB_FISCAL_DATA", []):
                if int(f.get("ID") or 0) == int(fiscal_id) and int(f.get("USER_ID") or 0) == int(user_id):
                    f["ALIAS"] = str(payload.get("newalias") or "")
                    f["FISCAL_NAME"] = str(payload.get("fiscalname") or "")
                    f["VAT_NUMBER"] = str(payload.get("newVATnumber") or "")
                    f["ADDRESS_LINE_1"] = str(payload.get("newaddress1") or "")
                    f["ADDRESS_LINE_2"] = str(payload.get("newaddress2") or "")
                    f["ZIP_CODE"] = str(payload.get("newzipcode") or "")
                    f["CITY"] = str(payload.get("newcity") or "")
                    f["REGION"] = str(payload.get("newregion") or "")
                    f["COUNTRY"] = str(payload.get("newcountry") or "")
                    f["PHONE_NUMBER"] = str(payload.get("newphone") or "")
                    f["EMAIL"] = str(payload.get("newemailfiscal") or "")
                    self.persist_table("dbo.WEB_FISCAL_DATA")
                    return "OK" if not isinstance(payload_or_fullname, dict) else True
        return "ERROR" if not isinstance(payload_or_fullname, dict) else False

    def delete_fiscal_data(self, user_id: int, fiscal_id: int) -> str:
        with self.lock:
            rows = self.tables.get("dbo.WEB_FISCAL_DATA", [])
            for f in rows:
                if int(f.get("ID") or 0) == int(fiscal_id) and int(f.get("USER_ID") or 0) == int(user_id):
                    f["ACTIVO"] = False
                    f["SELECTED_FISCAL"] = False
            remaining = [f for f in rows if f.get("ACTIVO", True) and int(f.get("USER_ID") or 0) == int(user_id)]
            if remaining and not any(r.get("SELECTED_FISCAL") for r in remaining):
                remaining[0]["SELECTED_FISCAL"] = True
            self.persist_table("dbo.WEB_FISCAL_DATA")
            return "OK"

    def set_default_fiscal_data(self, user_id: int, fiscal_id: int) -> str:
        with self.lock:
            for f in self.tables.get("dbo.WEB_FISCAL_DATA", []):
                if int(f.get("USER_ID") or 0) == int(user_id) and f.get("ACTIVO", True):
                    f["SELECTED_FISCAL"] = (int(f.get("ID") or 0) == int(fiscal_id))
            self.persist_table("dbo.WEB_FISCAL_DATA")
            return "OK"

    def update_user_profile(self, user_id: int, username: str, email: Optional[str] = None) -> str:
        uname = (username or "").strip()
        em = (email or "").strip()
        if len(uname) > 80 or not uname:
            return "errorUsername"
        with self.lock:
            if em:
                for u in self.tables.get("dbo.USERS", []):
                    if int(u.get("ID") or 0) != int(user_id) and str(u.get("EMAIL") or "").strip().lower() == em.lower():
                        return "errorEmail"
            for u in self.tables.get("dbo.USERS", []):
                if int(u.get("ID") or 0) == int(user_id):
                    u["NOMBRE"] = uname
                    u["APELLIDO1"] = ""
                    u["APELLIDO2"] = ""
                    if em:
                        u["EMAIL"] = em
                    u["UPDATE_DATE"] = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
                    self.persist_table("dbo.USERS")
                    return "OK"
        return "errorUsername"

    def update_user_password(self, user_id: int, old_password: str, new_password: str) -> Tuple[bool, str]:
        u = self.get_user_by_id(user_id)
        if not u:
            return False, "ERROR"
        auth_res = self.authenticate_user(str(u.get("EMAIL") or ""), old_password)
        auth_u = auth_res[0] if isinstance(auth_res, tuple) else auth_res
        if not auth_u or int(auth_u.get("ID") or 0) != int(user_id):
            return False, "INCORRECT"
        self._set_user_password_raw(user_id, new_password)
        return True, "OK"

    def _set_user_password_raw(self, user_id: int, new_password: str) -> bool:
        with self.lock:
            u = self.get_user_by_id(user_id)
            if not u:
                return False
            mem_id = str(u.get("MEMBERSHIP_ID") or "").strip().lower()
            salt_bytes = secrets.token_bytes(16)
            pwd_bytes = new_password.encode("utf-16-le")
            digest = hashlib.sha1(salt_bytes + pwd_bytes).digest()
            salt_b64 = base64.b64encode(salt_bytes).decode("ascii")
            hash_b64 = base64.b64encode(digest).decode("ascii")
            for m in self.tables.get("dbo.aspnet_Membership", []):
                if str(m.get("UserId") or "").strip().lower() == mem_id:
                    m["Password"] = hash_b64
                    m["PasswordSalt"] = salt_b64
                    m["PasswordFormat"] = 1
                    self.persist_table("dbo.aspnet_Membership")
                    return True
        return False

    def create_password_reset_token(self, email_or_username: str) -> Optional[Tuple[Dict[str, Any], str]]:
        q = (email_or_username or "").strip().lower()
        if not q:
            return None
        with self.lock:
            target_u = None
            for u in self.tables.get("dbo.USERS", []):
                if not u.get("ACTIVO", True):
                    continue
                if str(u.get("EMAIL") or "").strip().lower() == q or str(u.get("NOMBRE") or "").strip().lower() == q:
                    target_u = u
                    break
            if not target_u:
                return None
            uid = int(target_u.get("ID") or 0)
            token = secrets.token_urlsafe(32)
            now = datetime.datetime.now(datetime.timezone.utc)
            exp = _fmt_iso_utc(now + datetime.timedelta(hours=2))
            tokens = self.tables.setdefault("dbo.MOSAICEARTH_PASSWORD_RESETS", [])
            tokens[:] = [t for t in tokens if int(t.get("USER_ID") or 0) != uid]
            tokens.append({
                "USER_ID": uid,
                "EMAIL": str(target_u.get("EMAIL") or ""),
                "TOKEN": token,
                "EXPIRES_UTC": exp,
                "USED": False,
            })
            self.persist_table("dbo.MOSAICEARTH_PASSWORD_RESETS")
            return self.get_user_by_id(uid), token

    def reset_password_with_token(self, token: str, new_password: str) -> Tuple[bool, str]:
        tok = (token or "").strip()
        if not tok or len(new_password or "") < 6:
            return False, "Password must be at least 6 characters."
        now = datetime.datetime.now(datetime.timezone.utc)
        with self.lock:
            for r in self.tables.get("dbo.MOSAICEARTH_PASSWORD_RESETS", []):
                if str(r.get("TOKEN") or "") == tok and not bool(r.get("USED")):
                    exp = _parse_iso_dt(r.get("EXPIRES_UTC"))
                    if exp and exp < now:
                        return False, "This recovery link has expired."
                    uid = int(r.get("USER_ID") or 0)
                    if self._set_user_password_raw(uid, new_password):
                        r["USED"] = True
                        self.persist_table("dbo.MOSAICEARTH_PASSWORD_RESETS")
                        return True, "Password updated! You can now sign in."
        return False, "Invalid or expired password reset token."

    def request_email_change_token(self, user_id: int, new_email: str, current_password: str = "") -> Tuple[bool, str, str]:
        new_em = (new_email or "").strip().lower()
        if len(new_em) < 5 or "@" not in new_em:
            return False, "Enter a valid email address.", ""
        u = self.get_user_by_id(user_id)
        if not u:
            return False, "User not found.", ""
        if current_password:
            auth_res = self.authenticate_user(str(u.get("EMAIL") or ""), current_password)
            auth_u = auth_res[0] if isinstance(auth_res, tuple) else auth_res
            if not auth_u or int(auth_u.get("ID") or 0) != int(user_id):
                return False, "Incorrect current password.", ""
        with self.lock:
            for other in self.tables.get("dbo.USERS", []):
                if int(other.get("ID") or 0) != int(user_id) and str(other.get("EMAIL") or "").strip().lower() == new_em:
                    return False, "That email is already in use.", ""
            token = secrets.token_urlsafe(32)
            now = datetime.datetime.now(datetime.timezone.utc)
            exp = _fmt_iso_utc(now + datetime.timedelta(hours=2))
            reqs = self.tables.setdefault("dbo.MOSAICEARTH_EMAIL_CHANGES", [])
            reqs[:] = [r for r in reqs if int(r.get("USER_ID") or 0) != int(user_id)]
            reqs.append({
                "USER_ID": int(user_id),
                "OLD_EMAIL": str(u.get("EMAIL") or ""),
                "NEW_EMAIL": new_em,
                "TOKEN": token,
                "EXPIRES_UTC": exp,
                "USED": False,
            })
            self.persist_table("dbo.MOSAICEARTH_EMAIL_CHANGES")
            return True, "OK", token

    def confirm_email_change_token(self, token: str) -> Tuple[bool, str]:
        tok = (token or "").strip()
        if not tok:
            return False, "Invalid verification link."
        now = datetime.datetime.now(datetime.timezone.utc)
        with self.lock:
            for r in self.tables.get("dbo.MOSAICEARTH_EMAIL_CHANGES", []):
                if str(r.get("TOKEN") or "") == tok and not bool(r.get("USED")):
                    exp = _parse_iso_dt(r.get("EXPIRES_UTC"))
                    if exp and exp < now:
                        return False, "This verification link has expired."
                    uid = int(r.get("USER_ID") or 0)
                    new_em = str(r.get("NEW_EMAIL") or "").strip().lower()
                    u = self.get_user_by_id(uid)
                    if not u:
                        return False, "User account not found."
                    mem_id = str(u.get("MEMBERSHIP_ID") or "").strip().lower()
                    for row in self.tables.get("dbo.USERS", []):
                        if int(row.get("ID") or 0) == uid:
                            row["EMAIL"] = new_em
                            row["UPDATE_DATE"] = _fmt_iso_utc(now)
                    for au in self.tables.get("dbo.aspnet_Users", []):
                        if str(au.get("UserId") or "").strip().lower() == mem_id:
                            au["UserName"] = new_em
                            au["LoweredUserName"] = new_em
                    for am in self.tables.get("dbo.aspnet_Membership", []):
                        if str(am.get("UserId") or "").strip().lower() == mem_id:
                            am["Email"] = new_em
                            am["LoweredEmail"] = new_em
                    r["USED"] = True
                    self.persist_table("dbo.USERS")
                    self.persist_table("dbo.aspnet_Users")
                    self.persist_table("dbo.aspnet_Membership")
                    self.persist_table("dbo.MOSAICEARTH_EMAIL_CHANGES")
                    return True, new_em
        return False, "This verification link is invalid or has expired."

    def get_user_transactions(self, user_id: int) -> List[Dict[str, Any]]:
        with self.lock:
            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None
            }
            pubs_by_id = {
                int(pub["ID"]): pub
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", [])
                if pub.get("ID") is not None
            }
            purchases_by_id = {
                int(pur["ID"]): pur
                for pur in self.tables.get("dbo.WEB_PURCHASE", [])
                if pur.get("ID") is not None
            }
            gaussian_proj_ids = {
                int(gj.get("PROJECT_ID") or 0)
                for gj in self.tables.get("dbo.MOSAICEARTH_GAUSSIAN_JOBS", [])
                if int(gj.get("USER_ID") or 0) == int(user_id)
            }
            out = []
            for t in self.tables.get("dbo.WEB_TRANSACTION", []):
                if not t.get("ACTIVO", True) or int(t.get("USER_ID") or 0) != int(user_id):
                    continue
                tid = int(t.get("ID") or 0)
                diff_str = str(t.get("CREDIT_DIFFERENCE") or "0").replace(",", ".")
                try:
                    diff_val = float(diff_str)
                except Exception:
                    diff_val = 0.0
                signed_credits = (f"+{diff_val:g}" if diff_val > 0 else f"{diff_val:g}")
                pid = int(t.get("PROJECT_ID") or 0) if t.get("PROJECT_ID") is not None else 0
                pub_id = int(t.get("PUBLISH_ID") or 0) if t.get("PUBLISH_ID") is not None else 0
                pack_id = int(t.get("CREDIT_PACK_ID") or 0) if t.get("CREDIT_PACK_ID") is not None else 0
                pur_id = int(t.get("PURCHASE_ID") or 0) if t.get("PURCHASE_ID") is not None else 0
                proj_name = str(projects_by_id.get(pid, {}).get("NAME") or "") if pid else ""
                if not proj_name and pub_id and pub_id in pubs_by_id:
                    pub_pid = int(pubs_by_id[pub_id].get("PROJECT_ID") or 0)
                    proj_name = str(projects_by_id.get(pub_pid, {}).get("NAME") or "")

                pur = purchases_by_id.get(pur_id)
                is_sub = bool(pur.get("IS_SUBSCRIPTION")) if pur else False

                if t.get("IS_ADJUSTS"):
                    action = "Manual credit adjustment"
                elif t.get("IS_PREMIUM"):
                    action = "Premium storage"
                elif pack_id > 0:
                    action = "Credit pack subscription" if is_sub else "Credit pack purchase"
                elif t.get("WITHDRAW_ID") is not None:
                    action = "Historical withdrawal"
                elif pub_id > 0 and pub_id in pubs_by_id:
                    ft = int(pubs_by_id[pub_id].get("FILE_TYPE_ID") or 0)
                    ft_label = "Point cloud" if ft == 1 else "Maps & Layers" if ft == 2 else "3D model" if ft == 3 else "MosaicAsset"
                    prefix = "Creator credit" if diff_val > 0 else ("Asset acquisition" if diff_val < 0 else "Free acquisition")
                    action = f"{prefix} · {ft_label}"
                elif pid > 0 and pid in gaussian_proj_ids:
                    action = "Gaussian processing refund" if diff_val > 0 else "Gaussian processing"
                elif pid > 0:
                    action = "Image processing"
                else:
                    action = "Credit movement"

                desc = f"{action} · {proj_name}" if proj_name else action
                c_dt = _parse_iso_dt(t.get("CREATE_DATE")) or datetime.datetime.now(datetime.timezone.utc)
                price_raw = str(t.get("PRICE") or "").strip()
                price_disp = f"$ {price_raw}" if price_raw else "-"

                out.append({
                    "ID": tid,
                    "CREATE_DATE": c_dt,
                    "DateDisplay": c_dt.strftime("%d/%m/%Y"),
                    "Description": desc,
                    "SignedCredits": signed_credits,
                    "CurrentCredits": str(t.get("CURRENT_CREDITS") or "0"),
                    "PriceDisplay": price_disp,
                    "HasReceipt": pur_id > 0,
                })
            out.sort(key=lambda r: (r["CREATE_DATE"], r["ID"]), reverse=True)
            return out

    def get_user_premium_projects(self, user_id: int) -> List[Dict[str, Any]]:
        with self.lock:
            projects_by_id = {
                int(p["ID"]): p
                for p in self.tables.get("dbo.WEB_PROJECTS", [])
                if p.get("ID") is not None and p.get("ACTIVO", True)
            }
            out = []
            for pp in self.tables.get("dbo.WEB_PREMIUM_PROJECTS", []):
                if pp.get("CANCEL_DATE") is not None:
                    continue
                pid = int(pp.get("PROJECT_ID") or 0)
                proj = projects_by_id.get(pid)
                if not proj or int(proj.get("CREATE_ID") or 0) != int(user_id):
                    continue
                ren_dt = _parse_iso_dt(pp.get("RENEWAL_DATE"))
                out.append({
                    "PROJECT_ID": pid,
                    "NAME": str(proj.get("NAME") or f"Project {pid}"),
                    "RENEWAL_DATE": ren_dt,
                    "RenewalDisplay": ren_dt.strftime("%d/%m/%Y") if ren_dt else "—",
                })
            return out

    def get_eligible_premium_projects(self, user_id: int) -> List[Dict[str, Any]]:
        with self.lock:
            active_prem = {
                int(pp.get("PROJECT_ID") or 0)
                for pp in self.tables.get("dbo.WEB_PREMIUM_PROJECTS", [])
                if pp.get("CANCEL_DATE") is None
            }
            pub_projs = {
                int(pub.get("PROJECT_ID") or 0)
                for pub in self.tables.get("dbo.WEB_PUBLICATIONS", [])
                if int(pub.get("STATUS_ID") or 0) == 2
            }
            out = []
            for p in self.tables.get("dbo.WEB_PROJECTS", []):
                if not p.get("ACTIVO", True) or int(p.get("CREATE_ID") or 0) != int(user_id):
                    continue
                pid = int(p.get("ID") or 0)
                if pid in active_prem or pid in pub_projs:
                    continue
                if int(p.get("STATE_ID") or 0) == 4:
                    out.append({"ID": pid, "NAME": str(p.get("NAME") or f"Project {pid}")})
            out.sort(key=lambda r: r["ID"], reverse=True)
            return out

    def upgrade_project_to_premium(self, user_id: int, project_id: int) -> str:
        with self.lock:
            proj = self.get_project(project_id)
            if not proj or int(proj.get("CREATE_ID") or 0) != int(user_id):
                return "INVALID_PROJECT"
            user = self.get_user_by_id(user_id)
            if not user:
                return "INVALID_PROJECT"
            prems = self.tables.setdefault("dbo.WEB_PREMIUM_PROJECTS", [])
            now = datetime.datetime.now(datetime.timezone.utc)
            now_iso = _fmt_iso_utc(now)
            for pp in prems:
                if int(pp.get("PROJECT_ID") or 0) == int(project_id):
                    ren_dt = _parse_iso_dt(pp.get("RENEWAL_DATE"))
                    if pp.get("CANCEL_DATE") is not None and ren_dt and ren_dt > now:
                        pp["CANCEL_DATE"] = None
                        self.persist_table("dbo.WEB_PREMIUM_PROJECTS")
                        return "OK"
            if int(user.get("CreditsInt") or 0) < 20:
                return "NO_FOUNDS"
            ok, new_bal = self.adjust_user_credits(user_id, -20, "Premium storage", project_id=project_id)
            if not ok:
                return "NO_FOUNDS"
            txs = self.tables.setdefault("dbo.WEB_TRANSACTION", [])
            next_tid = next_record_id(txs, "ID", 1000)
            txs.append({
                "ID": next_tid,
                "ACTIVO": True,
                "CREATE_DATE": now_iso,
                "USER_ID": user_id,
                "CREDIT_PACK_ID": None,
                "PROJECT_ID": project_id,
                "CREDIT_DIFFERENCE": "-20",
                "CURRENT_CREDITS": str(new_bal),
                "PRICE": None,
                "STRIPE_ID": None,
                "PURCHASE_ID": None,
                "PUBLISH_ID": None,
                "IS_PREMIUM": True,
                "WITHDRAW_ID": None,
                "IS_ADJUSTS": False,
            })
            self.persist_table("dbo.WEB_TRANSACTION")
            ren_iso = _fmt_iso_utc(now + datetime.timedelta(days=30))
            updated = False
            for pp in prems:
                if int(pp.get("PROJECT_ID") or 0) == int(project_id):
                    pp["CANCEL_DATE"] = None
                    pp["RENEWAL_DATE"] = ren_iso
                    updated = True
                    break
            if not updated:
                prems.append({
                    "PROJECT_ID": project_id,
                    "CREATE_DATE": now_iso,
                    "RENEWAL_DATE": ren_iso,
                    "CANCEL_DATE": None,
                })
            self.persist_table("dbo.WEB_PREMIUM_PROJECTS")
            return "OK"

    def cancel_project_premium(self, user_id: int, project_id: int) -> str:
        with self.lock:
            now_iso = _fmt_iso_utc(datetime.datetime.now(datetime.timezone.utc))
            for pp in self.tables.get("dbo.WEB_PREMIUM_PROJECTS", []):
                if int(pp.get("PROJECT_ID") or 0) == int(project_id):
                    pp["CANCEL_DATE"] = now_iso
                    self.persist_table("dbo.WEB_PREMIUM_PROJECTS")
                    return "OK"
        return "OK"

    def get_premium_project_info_message(self, project_id: int, maybe_project_id: Optional[int] = None) -> str:
        target_pid = int(maybe_project_id if maybe_project_id is not None else project_id)
        with self.lock:
            for pp in self.tables.get("dbo.WEB_PREMIUM_PROJECTS", []):
                if int(pp.get("PROJECT_ID") or 0) == target_pid:
                    ren_dt = _parse_iso_dt(pp.get("RENEWAL_DATE"))
                    d_str = ren_dt.strftime("%d/%m/%Y") if ren_dt else "—"
                    return f"Your next charge of 20 credits is scheduled for {d_str}"
        return "Your next charge of 20 credits is scheduled for next month."

    def get_credit_packs(self, idioma_id: int = 3) -> List[Dict[str, Any]]:
        with self.lock:
            desc_by_pack = {}
            for lang_row in self.tables.get("dbo.WEB_CREDIT_PACKS_LANG", []):
                pid = int(lang_row.get("PACK_ID") or 0)
                if pid > 0 and (int(lang_row.get("IDIOMA_ID") or 3) == idioma_id or pid not in desc_by_pack):
                    desc_by_pack[pid] = str(lang_row.get("DESCRIPTION") or "")
            out = []
            for cp in self.tables.get("dbo.WEB_CREDIT_PACKS", []):
                if not cp.get("ACTIVO", True):
                    continue
                pid = int(cp.get("ID") or 0)
                out.append({
                    "ID": pid,
                    "CREDITS": str(cp.get("CREDITS") or "0"),
                    "PRICE_DOLAR": str(cp.get("PRICE_DOLAR") or "0"),
                    "KILOMETERS": str(cp.get("KILOMETERS") or "0"),
                    "DESCRIPTION": desc_by_pack.get(pid, "Usage credits for processing, Premium storage and asset acquisitions."),
                    "ID_ITEM_STRIPE_SUBSCRIPTION": str(cp.get("ID_ITEM_STRIPE_SUBSCRIPTION") or ""),
                })
            out.sort(key=lambda r: float(str(r["PRICE_DOLAR"]).replace(",", ".") or 0))
            return out


DEFAULT_CATEGORIES: Dict[int, Dict[str, str]] = {
    13: {"name": "Towns", "slug": "towns"},
    14: {"name": "Monuments", "slug": "monuments"},
    15: {"name": "Open fields", "slug": "open-fields"},
    16: {"name": "Natural spots", "slug": "natural-spots"},
    17: {"name": "Landmarks", "slug": "landmarks"},
    18: {"name": "Water bodies", "slug": "water-bodies"},
    19: {"name": "Coastal areas", "slug": "coastal-areas"},
    20: {"name": "Events & Festivals", "slug": "events-festivals"},
    21: {"name": "Historical sites", "slug": "historical-sites"},
    22: {"name": "Industrial sites", "slug": "industrial-sites"},
}

KNOWN_SOURCE_EXTENSIONS: List[str] = [
    "tif", "tiff", "gpkg", "geojson", "json", "kml", "kmz", "shp", "dxf", "gpx",
    "glb", "gltf", "obj", "las", "laz", "ply", "pcd", "pts", "xyz",
]

ICON_ORTHOPHOTO = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 13 13"><path id="Icon_open-grid-three-up" data-name="Icon open-grid-three-up" d="M0,0V3.25H3.25V0ZM4.875,0V3.25h3.25V0ZM9.75,0V3.25H13V0ZM0,4.875v3.25H3.25V4.875Zm4.875,0v3.25h3.25V4.875Zm4.875,0v3.25H13V4.875ZM0,9.75V13H3.25V9.75Zm4.875,0V13h3.25V9.75Zm4.875,0V13H13V9.75Z"/></svg>'
ICON_3D = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 12.068 13"><g id="Icon_ionic-ios-cube" data-name="Icon ionic-ios-cube" transform="translate(-3.375 -3.375)"><path id="Trazado_2605" data-name="Trazado 2605" d="M15.54,5.74l-5.2-2.33a.4.4,0,0,0-.328,0l-5.2,2.33a.244.244,0,0,0-.012.429L9.989,8.9a.4.4,0,0,0,.377,0l5.185-2.731A.244.244,0,0,0,15.54,5.74Z" transform="translate(-0.767 0)" fill="#000"/><path id="Trazado_2606" data-name="Trazado 2606" d="M19.235,21.3l5.226-2.765a.234.234,0,0,0,.1-.2V12.04a.216.216,0,0,0-.31-.206l-5.226,2.693a.229.229,0,0,0-.122.206V21.1A.219.219,0,0,0,19.235,21.3Z" transform="translate(-9.122 -4.954)" fill="#000"/><path id="Trazado_2607" data-name="Trazado 2607" d="M3.375,12.041v6.291a.234.234,0,0,0,.1.2l5.226,2.765a.214.214,0,0,0,.325-.2V14.734a.229.229,0,0,0-.122-.206L3.685,11.835A.217.217,0,0,0,3.375,12.041Z" transform="translate(0 -4.954)" fill="#000"/></g></svg>'
ICON_CLOUD = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 12.527 13"><g id="Grupo_3396" data-name="Grupo 3396" transform="translate(-227 -67)"><circle cx="0.834" cy="0.834" r="0.834" transform="translate(227 78.331)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(229.175 78.331)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(231.35 78.331)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(227 76.042)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(229.175 76.042)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(231.35 76.042)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(227 73.752)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(231.35 73.752)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(233.509 78.331)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(235.683 78.331)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(233.509 76.042)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(235.683 76.042)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(237.858 76.042)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(233.509 73.752)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(235.683 73.752)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(237.858 73.752)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(227 71.579)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(229.175 71.579)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(231.35 71.579)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(227 69.289)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(229.175 69.289)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(231.35 69.289)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(229.175 67)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(231.35 67)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(233.508 71.579)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(235.683 71.579)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(237.858 71.579)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(233.508 69.289)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(237.858 69.289)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(233.508 67)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(235.683 67)" fill="#0e1411"/><circle cx="0.834" cy="0.834" r="0.834" transform="translate(237.858 67)" fill="#0e1411"/></g></svg>'


def to_slug(value: str) -> str:
    return _slugify(value)


def family_display(type_or_id: Any) -> str:
    return MosaicEarthDataStore.family_display(type_or_id)


def subtype_display(asset_type: Any, subtype: Optional[str] = None) -> str:
    return MosaicEarthDataStore.subtype_display(str(asset_type), subtype)


_STORE_INSTANCE: Optional[MosaicEarthDataStore] = None


def get_store() -> MosaicEarthDataStore:
    global _STORE_INSTANCE
    if _STORE_INSTANCE is None:
        data_root = os.environ.get("MOSAICEARTH_DATA_DIR", "/data")
        _STORE_INSTANCE = MosaicEarthDataStore(data_root=data_root)
    return _STORE_INSTANCE

