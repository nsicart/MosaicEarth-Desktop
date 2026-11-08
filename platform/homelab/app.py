import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlencode

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import stripe_payments
from .map_search import router as map_search_router
from .blob_store import get_blob_store
from .db_store import DEFAULT_CATEGORIES, family_display, get_store, to_slug
from .email_service import get_email_service
from .interoperability import router as interop_router
from .connector_sync import router as connector_sync_router
from .desktop_activation import router as desktop_activation_router
from .pages import (
    CURRENT_LICENSE_TEXT,
    SHORT_LICENSE_SUMMARY,
    render_desktop_connectors_page,
    render_directory_page,
    render_external_asset_detail_page,
    render_home_master,
    render_map2_page,
    render_product_detail_page,
    render_public_mosaics_page,
    render_workspace_page,
    render_world_simulator_page,
)
from .seo_engine import (
    DEFAULT_INDEXNOW_KEY,
    INDEXNOW_KEYS,
    build_breadcrumb_json_ld,
    build_external_asset_path,
    build_head_metadata_html,
    build_processed_asset_path,
    build_web_application_json_ld,
    get_landing_site_url,
    get_public_directory_items,
    get_public_site_url,
    is_canonical_production_host,
    normalize_lang,
    render_dynamic_og_svg,
    render_entities_sitemap_xml,
    render_llms_full_txt,
    render_llms_txt,
    render_robots_txt,
    render_sitemap_index_xml,
    render_static_sitemap_xml,
    render_tools_sitemap_xml,
    submit_indexnow_batches,
    try_parse_external_slug,
    try_parse_trailing_int,
)
from .workspace_pages import (
    render_collection_page,
    render_credit_packs_page,
    render_external_asset_import_page,
    render_gaussian_project_page,
    render_internal_viewer_page,
    render_login_page,
    render_mymap_detail_page,
    render_mymap_viewer_page,
    render_mymaps_page,
    render_profile_page,
    render_project_detail_page,
    render_projects_page,
    render_register_page,
)

LIVE_WEB_ROOT = Path(
    os.environ.get(
        "MOSAICEARTH_WEB_ROOT",
        str(Path(__file__).resolve().parent.parent / "4dtwinmaps-live"),
    )
)

app = FastAPI(
    title="MosaicEarth 3D Geospatial Platform (Linux Container Edition)",
    version="2.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# Mount 4DTwinMaps Interoperability Gateway
app.include_router(interop_router)
app.include_router(map_search_router)
app.include_router(connector_sync_router)
app.include_router(desktop_activation_router)


@app.get("/mosaicearth-desktop-distribution.js")
def desktop_distribution_script():
    return FileResponse(Path(__file__).parent / "desktop_distribution.js", media_type="text/javascript")


def _engine_source_manifest():
    directory = get_blob_store().data_root / "public" / "engine-source"
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        return directory, manifest
    except (OSError, ValueError):
        raise HTTPException(503, "Engine source package is not installed on this instance") from None


@app.get("/open-source")
async def engine_source_page():
    _, manifest = _engine_source_manifest()
    links = []
    for entry in manifest.get("files", []):
        name = str(entry["name"])
        if Path(name).name != name:
            continue
        links.append(f'<li><a href="/open-source/files/{quote(name)}">{html.escape(entry.get("label", name))}</a> '
                     f'({int(entry["bytes"]):,} bytes)<br>SHA-256: <code>{html.escape(entry["sha256"])}</code></li>')
    return HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>MosaicEarth engine source and notices</title><style>body{max-width:960px;margin:3rem auto;padding:1rem;font:17px/1.6 system-ui;color:#e7ede9;background:#101713}a{color:#b5dc45}code{overflow-wrap:anywhere}li{margin:1rem 0}</style>'
        '<h1>Engine source and notices</h1><p>MosaicEarth uses NodeODX / ODX and COLMAP / OpenSplat. '
        'The downloads include upstream sources and submodules, our modifications, build instructions, dependency revisions and license notices. '
        'Each component retains its own license. The engine service and its modifications are available under AGPL-3.0-or-later; '
        'CUDA runtime components retain NVIDIA’s license.</p><ul>' + ''.join(links) + '</ul><a href="/en/Projects">Return to MyEarth</a></html>')


@app.get("/open-source/files/{filename}")
async def engine_source_file(filename: str):
    directory, manifest = _engine_source_manifest()
    if Path(filename).name != filename or not any(row.get("name") == filename for row in manifest.get("files", [])):
        raise HTTPException(404, "Source archive not found")
    file = directory / filename
    if not file.is_file():
        raise HTTPException(404, "Source archive not found")
    return FileResponse(file, filename=filename, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/js/front/AssetUpload.js")
async def asset_upload_javascript():
    return FileResponse(Path(__file__).parent / "asset_upload.js", media_type="text/javascript")


@app.get("/js/front/Notifications.js")
async def notifications_javascript():
    return FileResponse(Path(__file__).parent / "notifications.js", media_type="text/javascript", headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/js/front/MyMapDetail.js")
async def mymap_detail_javascript():
    return FileResponse(Path(__file__).parent / "MyMapDetail.js", media_type="text/javascript", headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/map-preview.svg")
async def map_preview_placeholder():
    return FileResponse(Path(__file__).parent / "map-preview.svg", media_type="image/svg+xml")


@app.get("/map-sources")
async def map_source_notices():
    return FileResponse(Path(__file__).parent / "map_sources.html", media_type="text/html")


@app.get("/js/front/MosaicCesiumRuntime.js")
async def route_custom_cesium_runtime():
    custom_js = Path(__file__).resolve().parent / "MosaicCesiumRuntime.js"
    if custom_js.is_file():
        sources_js = custom_js.with_name("MosaicMapSources.js")
        return Response(
            sources_js.read_text() + "\n" + custom_js.read_text(),
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    fallback_js = LIVE_WEB_ROOT / "js" / "front" / "MosaicCesiumRuntime.js"
    if fallback_js.is_file():
        return FileResponse(str(fallback_js), media_type="application/javascript; charset=utf-8")
    raise HTTPException(status_code=404, detail="MosaicCesiumRuntime.js not found")


async def _ensure_desktop_static_file(rel_path: str) -> Optional[Path]:
    clean_rel = rel_path.lstrip("/")
    target = (LIVE_WEB_ROOT / clean_rel).resolve()
    if not target.is_relative_to(LIVE_WEB_ROOT.resolve()):
        return None
    if target.is_file():
        return target
    found = _resolve_case_insensitive_file(LIVE_WEB_ROOT, clean_rel)
    if found and found.is_file():
        return found
    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") != "1":
        return None

    return None


@app.get("/js/front/Map2.js")
async def route_custom_map2_js():
    src_js = Path(__file__).with_name("Map2.js")
    if not src_js or not src_js.is_file():
        raise HTTPException(status_code=404, detail="Map2.js not found")
    text = src_js.read_text(encoding="utf-8", errors="ignore")
    text = (
        text.replace('+ " credits"', '+ " €"')
        .replace(": 'credits'", ": '€'")
        .replace("? '' : 'credits'", "? '' : '€'")
    )
    return Response(
        content=text,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/js/front/MyMap.js")
async def route_custom_mymap_js():
    src_js = Path(__file__).with_name("MyMap.js")
    if not src_js or not src_js.is_file():
        raise HTTPException(status_code=404, detail="MyMap.js not found")
    text = src_js.read_text(encoding="utf-8", errors="ignore")
    text = (
        text.replace(
            "let displayCredits = obj.Credits == 0 ? 'FREE' : obj.Credits;",
            "let displayCredits = (obj.IsPrivateAsset || obj.AccessTier === 'private_view_only') ? 'VIEW-ONLY (PRIVATE)' : (obj.Credits == 0 ? 'FREE (0 €)' : (obj.Credits + ' €'));",
        )
        .replace("<p>${displayCredits === 'FREE' ? '' : 'credits'}</p>", "<p></p>")
        .replace('+ " credits"', '+ " €"')
    )
    return Response(
        content=text,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# Mount static directories from 4dtwinmaps-live (including /fonts and /Web for exact typography & login styling)
for mount_path, folder_name in [
    ("/assets", "assets"),
    ("/js", "js"),
    ("/css", "css"),
    ("/fonts", "fonts"),
    ("/Web", "Web"),
    ("/web", "Web"),
    ("/Resources", "Resources"),
    ("/resources", "Resources"),
    ("/Potree", "Potree"),
    ("/Scripts", "Scripts"),
    ("/Admin/assets", "Admin/assets"),
    ("/admin/assets", "Admin/assets"),
]:
    target_dir = LIVE_WEB_ROOT / folder_name
    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") != "1" and target_dir.is_dir():
        app.mount(mount_path, StaticFiles(directory=str(target_dir)), name=f"static_{mount_path.strip('/').replace('/', '_')}")


@app.middleware("http")
async def desktop_static_cache_middleware(request: Request, call_next):
    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1" and request.method in ("GET", "HEAD"):
        path_str = request.url.path
        static_prefixes = (
            "/vendor/", "/assets/", "/js/", "/css/", "/fonts/", "/Web/", "/web/",
            "/Resources/", "/resources/", "/Potree/", "/Scripts/",
            "/Admin/assets/", "/admin/assets/", "/Images/", "/images/",
        )
        if path_str not in ("/js/front/MosaicCesiumRuntime.js", "/js/front/Map2.js", "/js/front/MyMap.js") and path_str.startswith(static_prefixes):
            cached = await _ensure_desktop_static_file(path_str)
            if cached and cached.is_file():
                from .blob_store import guess_mime_type
                return FileResponse(str(cached), media_type=guess_mime_type(cached.name))
    response = await call_next(request)
    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
        ct = response.headers.get("content-type", "")
        if "text/html" in ct:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


def _resolve_case_insensitive_file(root: Path, rel_path: str) -> Optional[Path]:
    parts = [p for p in unquote(rel_path).replace("\\", "/").strip("/").split("/") if p and p not in (".", "..")]
    curr = root
    for part in parts:
        exact = curr / part
        if exact.exists():
            curr = exact
            continue
        if not curr.is_dir():
            return None
        part_low = part.lower()
        matched = None
        try:
            for child in curr.iterdir():
                if child.name.lower() == part_low:
                    matched = child
                    break
        except Exception:
            return None
        if not matched:
            return None
        curr = matched
    return curr if curr.is_file() else None


def is_lan_or_local_request(request: Request) -> bool:
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").lower().split(":")[0]
    if "mosaicearth.app" in host or request.headers.get("cf-ray") or request.headers.get("cf-connecting-ip"):
        return False
    return (
        host in ("localhost", "127.0.0.1")
        or host.startswith("192.168.")
        or host.startswith("10.")
    )


def _read_local_desktop_license() -> Dict[str, Any]:
    app_root = Path(__file__).resolve().parent.parent
    data_dir_env = os.environ.get("MOSAICEARTH_DATA_DIR", "").strip()
    cands = [app_root / "desktop_license.json", Path(__file__).resolve().parent / "desktop_license.json"]
    if data_dir_env:
        cands.insert(0, Path(data_dir_env) / "desktop_license.json")
    for cand in cands:
        if cand.is_file():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def verify_local_desktop_license_manifest() -> Dict[str, Any]:
    """
    Verifies the local `desktop_license.json` cryptographic HMAC-SHA256 token and 30-day offline grace window.
    """
    import hmac
    import hashlib
    lic_data = _read_local_desktop_license()
    uid = int(lic_data.get("licensed_user_id") or 0)
    email = str(lic_data.get("licensed_email") or "").strip().lower()
    tok = str(lic_data.get("license_token") or "").strip()
    grace_days = int(lic_data.get("offline_grace_days") or 30)
    if uid <= 0 or not email or not tok:
        return {
            "licensed": False,
            "license_state": "UNLICENSED_FREE_VIEWER",
            "user_id": 0,
            "email": "",
            "offline_verified": False,
            "offline_grace_days": grace_days,
            "allowed_operations": ["view_public_catalog", "view_existing_local_3d_tiles"],
            "blocked_operations": ["create_project", "local_processing", "import_commercial_gis"],
            "price_eur": 29.0,
        }
    store = get_store()
    secret = (store.get_setting("JwtSecret", "mosaicearth-desktop-license-secret-2026") or "mosaicearth-desktop-license-secret-2026").encode("utf-8")
    expected_tok = hmac.new(secret, f"{uid}:{email}:mosaicearth-desktop-v3".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(tok, expected_tok):
        return {
            "licensed": False,
            "license_state": "INVALID_OR_TAMPERED_LICENSE_TOKEN",
            "user_id": 0,
            "email": email,
            "offline_verified": False,
            "offline_grace_days": grace_days,
            "allowed_operations": ["view_public_catalog"],
            "blocked_operations": ["create_project", "local_processing", "import_commercial_gis"],
            "price_eur": 29.0,
        }
    return {
        "licensed": True,
        "license_state": "LICENSED_ACTIVE",
        "user_id": uid,
        "email": email,
        "license_token": tok,
        "offline_verified": True,
        "offline_grace_days": grace_days,
        "max_workstations": int(lic_data.get("max_workstations") or 2),
        "allowed_operations": ["view_public_catalog", "view_existing_local_3d_tiles", "create_project", "local_processing", "import_commercial_gis", "offline_sync"],
        "blocked_operations": [],
        "price_eur": 0.0,
    }


LOGIN_SESSION_COOKIE = "mosaicearth_session"
LOGIN_SESSION_MAX_AGE = 30 * 86400


def _safe_login_return(value: str) -> str:
    decoded = unquote(value or "")
    if (decoded.startswith("/") and not decoded.startswith("//")
            and "\\" not in decoded and all(ord(c) >= 32 for c in decoded)):
        return value
    return "/en/Projects"


def _require_desktop_local() -> None:
    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") != "1":
        raise HTTPException(status_code=404, detail="Desktop service unavailable")


def _set_login_session(response: Response, request: Request, user_id: int) -> None:
    store = get_store()
    store.revoke_login_session(request.cookies.get(LOGIN_SESSION_COOKIE, ""))
    response.set_cookie(
        key=LOGIN_SESSION_COOKIE,
        value=store.create_login_session(user_id, LOGIN_SESSION_MAX_AGE),
        max_age=LOGIN_SESSION_MAX_AGE,
        secure=request.url.scheme == "https" or os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") != "1",
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie("mosaicearth_uid")


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    if os.environ.get("MOSAICEARTH_DESKTOP_SHARED_UI") == "1":
        from mosaicearth_platform_context import local_user
        return local_user()
    is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    session = request.cookies.get(LOGIN_SESSION_COOKIE)
    if session:
        store = get_store()
        u = store.get_user_by_id(store.resolve_login_session(session))
        if u:
            if not u.get("ACTIVO", True) or bool(u.get("SESSION_REVOKED")):
                return None
            u_email = str(u.get("EMAIL") or "").strip().lower()
            if is_desktop:
                # Never allow anonymous local@mosaicearth.desktop session in Desktop mode
                if not u_email or u_email == "local@mosaicearth.desktop":
                    return None
                lic_data = _read_local_desktop_license()
                lic_email = str(lic_data.get("licensed_email") or "").strip().lower()
                if lic_email and u_email != lic_email:
                    return None
            return u
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        u_b = get_store().resolve_webodm_bearer(token)
        if u_b and (not u_b.get("ACTIVO", True) or bool(u_b.get("SESSION_REVOKED"))):
            return None
        return u_b
    if is_desktop:
        # In Desktop Local mode, authenticate via cryptographically verified session-bound desktop_license.json
        local_lic = verify_local_desktop_license_manifest()
        if local_lic.get("licensed"):
            store = get_store()
            uid_l = int(local_lic["user_id"])
            email_l = str(local_lic["email"])
            u_loc = store.get_user_by_id(uid_l)
            if not u_loc or str(u_loc.get("EMAIL") or "").strip().lower() != email_l:
                store.set_desktop_cloud_account(
                    email=email_l,
                    username=email_l.split("@")[0],
                    cloud_uid=uid_l,
                    plan_code="studio",
                    token=str(local_lic.get("license_token") or ""),
                )
                u_loc = store.get_user_by_id(uid_l) or store.get_user_by_email(email_l)
            if u_loc and u_loc.get("ACTIVO", True) and not bool(u_loc.get("SESSION_REVOKED")):
                return u_loc
    return None


def require_user_or_redirect(request: Request) -> Optional[RedirectResponse]:
    if get_current_user(request) is None:
        ret = request.url.path
        if request.url.query:
            ret = f"{ret}?{request.url.query}"
        return RedirectResponse(url=f"/Default.aspx?ReturnUrl={quote(ret, safe='')}", status_code=302)
    return None


def aspnet_d(payload: Any) -> JSONResponse:
    """Wraps payload in ASP.NET PageMethods/WebService {"d": ...} envelope."""
    if isinstance(payload, (dict, list)):
        return JSONResponse({"d": json.dumps(payload, ensure_ascii=False)})
    return JSONResponse({"d": payload})


@app.middleware("http")
async def seo_and_api_dispatch_middleware(request: Request, call_next):
    # Normalize double slashes in funciones.aspx//loadNotifications
    path = request.scope.get("path") or ""
    if "//" in path:
        request.scope["path"] = re.sub(r"/+", "/", path)

    api_mode = request.query_params.get("mosaicearth_api", "").strip().lower()
    if api_mode == "webodm-device":
        return await handle_webodm_device(request)
    if api_mode == "webodm-import":
        return await handle_webodm_import(request)
    if api_mode == "annotations":
        return await handle_asset_annotations(request)

    response: Response = await call_next(request)

    # Case-insensitive static file fallback on Linux for 404 static requests
    if response.status_code == 404 and request.method in ("GET", "HEAD"):
        req_path = request.scope.get("path") or ""
        first_seg = req_path.strip("/").split("/")[0].lower() if req_path.strip("/") else ""
        if first_seg in ("assets", "admin", "resources", "js", "css", "fonts", "web", "potree", "scripts"):
            resolved_static = _resolve_case_insensitive_file(LIVE_WEB_ROOT, req_path)
            if resolved_static:
                return FileResponse(str(resolved_static))

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if is_canonical_production_host(host):
        if "x-robots-tag" not in response.headers:
            response.headers["X-Robots-Tag"] = "index, follow, max-image-preview:large"
    else:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"

    return response


# ============================================================================
# HEALTHCHECK & DIAGNOSTICS
# ============================================================================
@app.get("/api/health")
@app.get("/api/v1/health")
async def healthcheck():
    store = get_store()
    blob_store = get_blob_store()
    return {
        "status": "ok",
        "service": "mosaicearth-platform-linux",
        "usersCount": len(store.tables.get("dbo.USERS", [])),
        "projectsCount": len(store.tables.get("dbo.WEB_PROJECTS", [])),
        "publicationsCount": len(store.tables.get("dbo.WEB_PUBLICATIONS", [])),
        "externalAssetsCount": len(store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])),
        "publicDirectoryCount": len(store.build_public_asset_index()),
        "blobRoot": str(blob_store.blobs_root),
    }


# ============================================================================
# PHASE 2–5 SEO, SITEMAPS, ROBOTS, LLMS.TXT, OG IMAGE & INDEXNOW
# ============================================================================
@app.get("/robots.txt")
@app.get("/RobotsTxt.ashx")
async def route_robots_txt():
    return PlainTextResponse(render_robots_txt(), media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml")
@app.get("/SitemapXml.ashx")
async def route_sitemap_index(request: Request):
    section = request.query_params.get("section", "").strip().lower()
    if section == "static":
        xml = render_static_sitemap_xml()
    elif section in ("tools", "explore"):
        xml = render_tools_sitemap_xml()
    elif section in ("entities", "assets"):
        xml = render_entities_sitemap_xml()
    else:
        xml = render_sitemap_index_xml()
    return Response(content=xml, media_type="application/xml; charset=utf-8")


@app.get("/sitemaps/{section}.xml")
async def route_sitemap_section(section: str):
    sec = section.strip().lower()
    if sec == "static":
        xml = render_static_sitemap_xml()
    elif sec in ("tools", "explore"):
        xml = render_tools_sitemap_xml()
    elif sec in ("entities", "assets"):
        xml = render_entities_sitemap_xml()
    else:
        raise HTTPException(status_code=404, detail="Sitemap section not found")
    return Response(content=xml, media_type="application/xml; charset=utf-8")


@app.get("/llms.txt")
@app.get("/LlmsTxt.ashx")
async def route_llms_txt(request: Request):
    if request.query_params.get("full") == "1":
        return PlainTextResponse(render_llms_full_txt(), media_type="text/plain; charset=utf-8")
    return PlainTextResponse(render_llms_txt(), media_type="text/plain; charset=utf-8")


@app.get("/llms-full.txt")
async def route_llms_full_txt():
    return PlainTextResponse(render_llms_full_txt(), media_type="text/plain; charset=utf-8")


@app.get("/4d8f9a2e7c1b6053e9a4d2c8b7f16350.txt")
async def route_indexnow_key_1():
    return PlainTextResponse("4d8f9a2e7c1b6053e9a4d2c8b7f16350", media_type="text/plain; charset=utf-8")


@app.get("/a8f4e2c9d1b647309e5a1c2b3d4e5f60.txt")
async def route_indexnow_key_2():
    return PlainTextResponse("a8f4e2c9d1b647309e5a1c2b3d4e5f60", media_type="text/plain; charset=utf-8")


@app.api_route("/api/seo/ping-indexnow", methods=["GET", "POST"])
@app.api_route("/IndexNowHandler.ashx", methods=["GET", "POST"])
async def route_ping_indexnow(request: Request):
    mode = request.query_params.get("mode", "").strip().lower()
    if mode == "key":
        return PlainTextResponse(DEFAULT_INDEXNOW_KEY, media_type="text/plain; charset=utf-8")
    dry_run = request.query_params.get("dryRun", "true").lower() != "false"
    custom_urls = None
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict):
                custom_urls = body.get("urls")
                if "dryRun" in body:
                    dry_run = bool(body["dryRun"])
        except Exception:
            pass
    return JSONResponse(submit_indexnow_batches(custom_urls, dry_run=dry_run))


@app.get("/api/og")
@app.get("/OgImage.ashx")
async def route_og_image(
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    badge: Optional[str] = None,
    location: Optional[str] = None,
):
    svg = render_dynamic_og_svg(title, subtitle, badge, location)
    return Response(
        content=svg,
        media_type="image/svg+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ============================================================================
# PUBLIC 3D GLOBE, PROGRAMMATIC SEO EXPLORER & DIRECTORY ROUTES
# ============================================================================
@app.get("/")
async def route_root():
    return RedirectResponse(url="/en/Map", status_code=302)


@app.get("/Map2.aspx")
@app.get("/{lang}/Map")
@app.get("/{lang}/explore/all")
async def route_map2(
    request: Request,
    lang: str = "en",
    type: Optional[str] = None,
    category: Optional[str] = None,
    region: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    lat: float = 0.0,
    lon: float = 0.0,
):
    html_doc = render_map2_page(
        lang=lang,
        route_family=type,
        route_category=category,
        route_region=region,
        search_query=q,
        current_page=max(1, page),
        lat_param=lat,
        lon_param=lon,
        type_param=int(type) if (type and type.isdigit()) else 0,
        user=get_current_user(request),
    )
    return HTMLResponse(html_doc)


@app.get("/{lang}/explore/{family}")
async def route_explore_family(request: Request, lang: str, family: str, page: int = 1, q: Optional[str] = None):
    if family.lower() == "all":
        return await route_map2(request, lang=lang, page=page, q=q)
    html_doc = render_map2_page(
        lang=lang,
        route_family=family,
        search_query=q,
        current_page=max(1, page),
        user=get_current_user(request),
    )
    return HTMLResponse(html_doc)


@app.get("/{lang}/explore/{family}/{categorySlug}")
async def route_explore_family_category(
    request: Request,
    lang: str,
    family: str,
    categorySlug: str,
    page: int = 1,
    q: Optional[str] = None,
):
    html_doc = render_map2_page(
        lang=lang,
        route_family=family,
        route_category=categorySlug,
        search_query=q,
        current_page=max(1, page),
        user=get_current_user(request),
    )
    return HTMLResponse(html_doc)


@app.get("/{lang}/category/{categorySlug}")
async def route_category_hub(request: Request, lang: str, categorySlug: str, page: int = 1, q: Optional[str] = None):
    html_doc = render_map2_page(
        lang=lang,
        route_category=categorySlug,
        search_query=q,
        current_page=max(1, page),
        user=get_current_user(request),
    )
    return HTMLResponse(html_doc)


@app.get("/{lang}/locations/{regionSlug}")
@app.get("/{lang}/region/{regionSlug}")
async def route_region_hub(request: Request, lang: str, regionSlug: str, page: int = 1, q: Optional[str] = None):
    html_doc = render_map2_page(
        lang=lang,
        route_region=regionSlug,
        search_query=q,
        current_page=max(1, page),
        user=get_current_user(request),
    )
    return HTMLResponse(html_doc)


@app.get("/Directory.aspx")
@app.get("/{lang}/directory")
async def route_directory(lang: str = "en"):
    return HTMLResponse(render_directory_page(lang))


# ============================================================================
# SEMANTIC ASSET DETAIL PAGES & 301 REDIRECTS FROM LEGACY QUERY URLS
# ============================================================================
@app.get("/Resources/mosaicearth-mark-transparent.png")
@app.get("/Resources/mosaicearth-mark-transparent-cropped.png")
@app.get("/resources/mosaicearth-mark-transparent.png")
@app.get("/resources/mosaicearth-mark-transparent-cropped.png")
@app.get("/Resources/favicon.png")
@app.get("/resources/favicon.png")
@app.get("/favicon.ico")
async def route_logo_mark_png():
    for candidate in ("mosaicearth-monogram-256.png", "mosaicearth-site-favicon.png", "mosaicearth-oauth.png"):
        p = LIVE_WEB_ROOT / "Resources" / candidate
        if p.is_file():
            return FileResponse(str(p), media_type="image/png")
    raise HTTPException(status_code=404, detail="Logo not found")


@app.get("/ProductDetail.aspx")
@app.get("/Client/ProductDetail.aspx")
@app.get("/{lang}/ProductDetail")
@app.get("/{lang}/ProductDetail/{id}")
async def route_legacy_product_detail(
    request: Request,
    id: Optional[int] = None,
    project_id: Optional[int] = None,
    type: Optional[int] = None,
    lang: str = "en",
):
    pub_id = id or (int(request.query_params["id"]) if request.query_params.get("id", "").isdigit() else None)
    if pub_id and pub_id > 0:
        store = get_store()
        for p in store.tables.get("dbo.WEB_PUBLICATIONS", []):
            if int(p.get("ID") or 0) == pub_id and int(p.get("STATUS_ID") or p.get("STATE_ID") or 0) == 2:
                proj = store.get_project(int(p.get("PROJECT_ID") or 0))
                name = str(p.get("NAME") or (proj.get("NAME") if proj else f"asset-{pub_id}"))
                canonical = build_processed_asset_path(pub_id, name, lang)
                return RedirectResponse(url=canonical, status_code=301)

    html_doc = render_product_detail_page(
        publish_id=pub_id,
        project_id=project_id,
        file_type_id=type,
        lang=lang,
        user=get_current_user(request),
    )
    if not html_doc:
        return RedirectResponse(url=f"/{normalize_lang(lang)}/Map", status_code=302)
    return HTMLResponse(html_doc)


@app.get("/{lang}/assets/{slug}")
async def route_semantic_processed_asset(request: Request, lang: str, slug: str):
    pub_id = try_parse_trailing_int(slug)
    if not pub_id:
        raise HTTPException(status_code=404, detail="MosaicAsset not found")
    html_doc = render_product_detail_page(
        publish_id=pub_id,
        lang=lang,
        user=get_current_user(request),
    )
    if not html_doc:
        raise HTTPException(status_code=404, detail="MosaicAsset not found")
    return HTMLResponse(html_doc)


@app.get("/ExternalAssetDetail.aspx")
@app.get("/Client/ExternalAssetDetail.aspx")
async def route_legacy_external_asset_detail(
    request: Request,
    project_id: Optional[int] = None,
    asset_id: Optional[int] = None,
    lang: str = "en",
):
    if project_id and asset_id:
        ext = get_store().get_external_asset(project_id, asset_id)
        if ext and ext.get("IS_PUBLISHED"):
            canonical = build_external_asset_path(
                project_id, asset_id, str(ext.get("TITLE") or ext.get("DISPLAY_NAME") or "asset"), lang
            )
            return RedirectResponse(url=canonical, status_code=301)
        html_doc = render_external_asset_detail_page(project_id, asset_id, lang=lang, user=get_current_user(request))
        if html_doc:
            return HTMLResponse(html_doc)
    return RedirectResponse(url=f"/{normalize_lang(lang)}/Map", status_code=302)


@app.get("/{lang}/external-assets/{slug}")
async def route_semantic_external_asset(request: Request, lang: str, slug: str):
    parsed = try_parse_external_slug(slug)
    if not parsed:
        raise HTTPException(status_code=404, detail="External MosaicAsset not found")
    pid, aid = parsed
    html_doc = render_external_asset_detail_page(pid, aid, lang=lang, user=get_current_user(request))
    if not html_doc:
        raise HTTPException(status_code=404, detail="External MosaicAsset not found")
    return HTMLResponse(html_doc)


# ============================================================================
# AUTHENTICATION, OAUTH BRIDGE & WORKSPACE PAGES
# ============================================================================
@app.get("/Default.aspx")
@app.get("/Login.aspx")
@app.get("/SignIn")
@app.get("/signin")
@app.get("/{lang}/SignIn")
@app.get("/{lang}/signin")
async def route_login_get(request: Request, ReturnUrl: str = "/en/Map", lang: str = "en", switch_account: int = 0):
    user = get_current_user(request)
    is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    if user and not switch_account:
        # In Desktop mode, if still on default local@mosaicearth.desktop, allow opening /Default.aspx to sign in with Cloud account
        if not (is_desktop and str(user.get("EMAIL") or "") == "local@mosaicearth.desktop"):
            return RedirectResponse(url=ReturnUrl or "/en/Map", status_code=302)
    return HTMLResponse(render_login_page(return_url=ReturnUrl, is_lan=is_lan_or_local_request(request)))


@app.get("/Auth/DesktopLocalMode.ashx")
async def route_desktop_local_mode(request: Request, returnUrl: str = "/en/Projects"):
    return HTMLResponse(
        render_login_page(
            "MosaicEarth Desktop is session-bound and requires signing in with the licensed account that downloaded this installer.",
            return_url=returnUrl,
            is_lan=is_lan_or_local_request(request),
        ),
        status_code=403,
    )


@app.post("/Default.aspx")
@app.post("/Login.aspx")
@app.post("/SignIn")
@app.post("/{lang}/SignIn")
async def route_login_post(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    returnUrl: str = Form("/en/Map"),
):
    store = get_store()
    is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    if is_desktop:
        lic_data = _read_local_desktop_license()
        lic_email = str(lic_data.get("licensed_email") or "").strip().lower()
        if lic_email and email.strip().lower() != lic_email:
            return HTMLResponse(
                render_login_page(
                    f"This MosaicEarth Desktop installer is licensed exclusively to {lic_email}. Signing in with another account is not permitted.",
                    return_url=returnUrl,
                    is_lan=is_lan_or_local_request(request),
                ),
                status_code=403,
            )
    res = store.authenticate_user(email, password)
    user = res[0] if isinstance(res, tuple) else res
    err_reason = res[1] if isinstance(res, tuple) and len(res) > 1 else ""

    if not user and is_desktop and email and password:
        cloud_origin = os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                c_resp = await client.post(
                    f"{cloud_origin}/api/v2/desktop/cloud-auth",
                    json={"email": email.strip(), "password": password},
                )
                c_data = c_resp.json()
                if c_resp.status_code == 200 and c_data.get("ok"):
                    u_info = c_data.get("user") or {}
                    c_email = str(u_info.get("email") or email.strip())
                    c_uname = str(u_info.get("username") or c_email)
                    c_uid = int(u_info.get("id") or 0)
                    c_plan = str(u_info.get("plan_code") or "free").lower()
                    store.set_desktop_cloud_account(
                        email=c_email,
                        username=c_uname,
                        cloud_uid=c_uid,
                        plan_code=c_plan,
                        token=str(c_data.get("token") or ""),
                        cloud_url=cloud_origin,
                    )
                    with store.lock:
                        for u_row in store.tables.get("dbo.USERS", []):
                            if int(u_row.get("ID") or 0) == 1:
                                u_row["EMAIL"] = c_email
                                u_row["NOMBRE"] = c_uname
                                u_row["NAME"] = c_uname
                                u_row["PLAN_KEY"] = c_plan
                                store.persist_table("dbo.USERS")
                                break
                    user = store.get_user_by_id(1)
        except Exception:
            pass

    if not user:
        msg = (
            "This account uses Google Sign-In. Click 'Continue with Google' above."
            if err_reason and err_reason.startswith("EXTERNAL_AUTH:")
            else "Invalid email/username or password."
        )
        return HTMLResponse(
            render_login_page(msg, return_url=returnUrl, is_lan=is_lan_or_local_request(request)),
            status_code=401,
        )

    if is_desktop and int(user.get("ID") or 0) > 1:
        st_sum = store.get_user_storage_summary(int(user["ID"]))
        store.set_desktop_cloud_account(
            email=str(user.get("EMAIL") or email),
            username=str(user.get("USERNAME") or user.get("NAME") or email),
            cloud_uid=int(user["ID"]),
            plan_code=str(st_sum.get("plan_code") or "free").lower(),
            token=f"cloud_uid:{user['ID']}",
            cloud_url=os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app").rstrip("/"),
        )

    resp = RedirectResponse(url=_safe_login_return(returnUrl), status_code=302)
    resp.delete_cookie("mosaicearth_logged_out")
    _set_login_session(resp, request, int(user["ID"]))
    return resp


@app.get("/Register.aspx")
async def route_register_get(request: Request, ReturnUrl: str = "/en/Projects", returnUrl: str = ""):
    user = get_current_user(request)
    target_url = returnUrl or ReturnUrl or "/en/Projects"
    if user:
        return RedirectResponse(url=target_url, status_code=302)
    return HTMLResponse(render_register_page(return_url=target_url))


@app.post("/Default.aspx/RegisterUser")
async def webmethod_register_user(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = str(body.get("username") or "").strip()
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        return aspnet_d({"ok": False, "message": "Username and password are required."})
    user, msg = get_store().register_user(email, username, password=password)
    if msg != "OK" or not user:
        return aspnet_d({"ok": False, "message": msg})
    try:
        verify_url = f"{get_public_site_url()}/en/Projects"
        get_email_service().send_account_created_email(email, username, verify_url)
    except Exception:
        pass
    resp = aspnet_d({"ok": True, "userId": user["ID"]})
    _set_login_session(resp, request, int(user["ID"]))
    return resp


@app.post("/Default.aspx/SendPasswordRecovery")
async def webmethod_send_password_recovery(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    email_or_user = str(body.get("email") or "").strip()
    if not email_or_user:
        return aspnet_d({"ok": False, "message": "Please enter your email address."})
    res = get_store().create_password_reset_token(email_or_user)
    if res:
        target_u, token = res
        reset_url = f"{get_public_site_url()}/RecoverPass.aspx?token={quote(token, safe='')}"
        get_email_service().send_password_reset_email(
            str(target_u.get("EMAIL") or email_or_user),
            str(target_u.get("USERNAME") or target_u.get("NAME") or ""),
            reset_url,
        )
    return aspnet_d({"ok": True, "message": "If an account exists for that email, a password reset link has been sent."})


@app.get("/RecoverPass.aspx")
async def route_recover_pass_get(token: str = "", msg: str = ""):
    safe_tok = html.escape(token, quote=True)
    msg_html = f'<div class="alert alert-info" style="font-size:13px;">{html.escape(msg)}</div>' if msg else ""
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Reset Password · MosaicEarth</title>
    <link href="/admin/assets/css/bootstrap.min.css" rel="stylesheet" />
    <link href="/Web/login.css?v=8" rel="stylesheet" />
</head>
<body class="gray-bg" style="display:flex;align-items:center;justify-content:center;min-height:100vh;background:#1A1A1A url('/Resources/Login/fondo.png') no-repeat fixed center;background-size:cover;">
    <form method="post" action="/RecoverPass.aspx" class="col-login" style="max-width:420px;width:100%;">
        <div class="tituloForget">Reset Password</div>
        <div class="SubtituloForget">Choose a new password for your MosaicEarth account.</div>
        {msg_html}
        <input type="hidden" name="token" value="{safe_tok}" />
        <div class="form-group">
            <input type="password" name="password" class="form-control login-control" placeholder="New password" required minlength="6" />
        </div>
        <div class="form-group">
            <input type="password" name="confirm_password" class="form-control login-control" placeholder="Confirm new password" required minlength="6" />
        </div>
        <button type="submit" class="btn block full-width m-b btn-green">Save New Password</button>
        <div class="text-center" style="margin-top:16px;">
            <a href="/Default.aspx" class="link active">Back to Sign In</a>
        </div>
    </form>
</body>
</html>"""
    )


@app.post("/RecoverPass.aspx")
async def route_recover_pass_post(token: str = Form(""), password: str = Form(""), confirm_password: str = Form("")):
    if password != confirm_password:
        return await route_recover_pass_get(token=token, msg="Passwords do not match.")
    ok, message = get_store().reset_password_with_token(token, password)
    if not ok:
        return await route_recover_pass_get(token=token, msg=message)
    return RedirectResponse(url="/Default.aspx", status_code=302)


@app.get("/VerifyEmailChange.aspx")
async def route_verify_email_change(token: str = ""):
    ok, msg = get_store().confirm_email_change_token(token)
    title = "Email Updated" if ok else "Email Verification Failed"
    detail = f"Your sign-in email is now {msg}." if ok else msg
    return HTMLResponse(
        render_workspace_page(
            "Verify Email",
            title,
            detail,
            f'<div class="p-4 rounded" style="background:#232323;border:1px solid rgba(153,204,0,0.3);"><p style="color:#fff;">{html.escape(detail)}</p><a href="/en/Profile" class="btn btn-sm" style="background:#99CC00;color:#111;font-weight:700;">Back to Account</a></div>',
        )
    )


_OAUTH_PROVIDER_MAP = {
    "google": ("Google", "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token", "openid email profile"),
    "microsoft": ("Microsoft", "https://login.microsoftonline.com/common/oauth2/v2.0/authorize", "https://login.microsoftonline.com/common/oauth2/v2.0/token", "openid profile email User.Read"),
    "github": ("GitHub", "https://github.com/login/oauth/authorize", "https://github.com/login/oauth/access_token", "read:user user:email"),
}


@app.get("/OAuth.ashx")
@app.get("/Auth/ExternalLogin.ashx")
@app.get("/ExternalLogin.aspx")
@app.get("/ExternalOAuthCallback.aspx")
async def route_external_login(
    request: Request,
    provider: str = "google",
    returnUrl: str = "/en/Projects",
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    store = get_store()
    prov_key = (provider or "google").strip().lower()
    if code and (not state or not secrets.compare_digest(state.encode(), request.cookies.get("mosaicearth_oauth_state", "").encode())):
        return HTMLResponse("Invalid or expired OAuth session. Start sign-in again.", status_code=400)
    if state and "|" in state:
        parts = state.split("|", 2)
        if parts[0].lower() in _OAUTH_PROVIDER_MAP:
            prov_key = parts[0].lower()
        if len(parts) >= 2:
            returnUrl = _safe_login_return(unquote(parts[1]))

    if error:
        return HTMLResponse(
            render_login_page(f"OAuth sign-in cancelled or failed ({error}).", return_url=returnUrl, is_lan=is_lan_or_local_request(request)),
            status_code=400,
        )

    if prov_key in _OAUTH_PROVIDER_MAP:
        cfg_name, auth_url, token_url, scope = _OAUTH_PROVIDER_MAP[prov_key]
        client_id = store.get_setting(f"OAuth.{cfg_name}.ClientId", "")
        client_secret = store.get_setting(f"OAuth.{cfg_name}.ClientSecret", "")
        # The session-bound state identifies the provider on return. A shared callback
        # matches the registered production URI without provider query variants.
        redirect_uri = f"{get_public_site_url()}/ExternalOAuthCallback.aspx"

        if code:
            if not client_id or not client_secret:
                return HTMLResponse(
                    render_login_page(f"{cfg_name} OAuth client secret is not configured.", return_url=returnUrl, is_lan=is_lan_or_local_request(request)),
                    status_code=400,
                )
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    tok_resp = await client.post(
                        token_url,
                        data={
                            "grant_type": "authorization_code",
                            "code": code,
                            "redirect_uri": redirect_uri,
                            "client_id": client_id,
                            "client_secret": client_secret,
                        },
                        headers={"Accept": "application/json", "User-Agent": "MosaicEarth/2.1"},
                    )
                    tok_data = tok_resp.json()
                    access_token = tok_data.get("access_token")
                    if not access_token:
                        raise ValueError(tok_data.get("error_description") or "No access_token returned by provider")

                    auth_headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": "MosaicEarth/2.1"}
                    email_addr = ""
                    disp_name = ""
                    if prov_key == "google":
                        prof = (await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=auth_headers)).json()
                        email_addr = str(prof.get("email") or "").strip().lower()
                        disp_name = str(prof.get("name") or email_addr).strip()
                    elif prov_key == "microsoft":
                        prof = (await client.get("https://graph.microsoft.com/v1.0/me?$select=displayName,mail,userPrincipalName", headers=auth_headers)).json()
                        email_addr = str(prof.get("mail") or prof.get("userPrincipalName") or "").strip().lower()
                        disp_name = str(prof.get("displayName") or email_addr).strip()
                    elif prov_key == "github":
                        prof = (await client.get("https://api.github.com/user", headers=auth_headers)).json()
                        email_addr = str(prof.get("email") or "").strip().lower()
                        disp_name = str(prof.get("name") or prof.get("login") or "").strip()
                        if not email_addr:
                            em_list = (await client.get("https://api.github.com/user/emails", headers=auth_headers)).json()
                            if isinstance(em_list, list):
                                for em_item in em_list:
                                    if em_item.get("verified") and em_item.get("primary"):
                                        email_addr = str(em_item.get("email") or "").strip().lower()
                                        break
                                if not email_addr:
                                    for em_item in em_list:
                                        if em_item.get("verified"):
                                            email_addr = str(em_item.get("email") or "").strip().lower()
                                            break

                    if not email_addr or "@" not in email_addr:
                        raise ValueError(f"{cfg_name} did not return a verified email address.")

                    user_obj, _ = store.register_user(email_addr, disp_name, "", oauth_provider=cfg_name)
                    if not user_obj:
                        raise ValueError("Could not sign in or provision MosaicEarth user.")

                    resp = RedirectResponse(url=_safe_login_return(returnUrl), status_code=302)
                    _set_login_session(resp, request, int(user_obj["ID"]))
                    resp.delete_cookie("mosaicearth_oauth_state")
                    return resp
            except Exception as exc:
                return HTMLResponse(
                    render_login_page(f"{cfg_name} Sign-In error: {exc}", return_url=returnUrl, is_lan=is_lan_or_local_request(request)),
                    status_code=400,
                )

        if client_id and client_secret and not is_lan_or_local_request(request):
            safe_ret = _safe_login_return(returnUrl)
            state_val = f"{prov_key}|{quote(safe_ret, safe='')}|{secrets.token_urlsafe(16)}"
            params = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scope,
                "state": state_val,
            }
            if prov_key in ("google", "microsoft"):
                params["prompt"] = "select_account"
            response = RedirectResponse(url=f"{auth_url}?{urlencode(params)}", status_code=302)
            response.set_cookie("mosaicearth_oauth_state", state_val, max_age=600, httponly=True,
                                secure=True, samesite="lax")
            return response

    return RedirectResponse(url=f"/Default.aspx?ReturnUrl={quote(returnUrl, safe='')}", status_code=302)


@app.get("/Logout.ashx")
async def route_logout(request: Request):
    get_store().revoke_login_session(request.cookies.get(LOGIN_SESSION_COOKIE, ""))
    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
        store = get_store()
        store.clear_desktop_cloud_account()
        with store.lock:
            for u_row in store.tables.get("dbo.USERS", []):
                if int(u_row.get("ID") or 0) == 1:
                    u_row["EMAIL"] = "local@mosaicearth.desktop"
                    u_row["NOMBRE"] = "Local"
                    u_row["NAME"] = "Local"
                    store.persist_table("dbo.USERS")
                    break
        resp = RedirectResponse(url="/Default.aspx?switch_account=1", status_code=302)
        resp.delete_cookie(LOGIN_SESSION_COOKIE)
        resp.delete_cookie("mosaicearth_uid")
        resp.set_cookie(
            key="mosaicearth_logged_out",
            value="1",
            max_age=30 * 86400,
            httponly=True,
            samesite="lax",
        )
        return resp
    resp = RedirectResponse(url="/Default.aspx", status_code=302)
    resp.delete_cookie(LOGIN_SESSION_COOKIE)
    resp.delete_cookie("mosaicearth_uid")
    return resp


@app.get("/Client/Projects.aspx")
@app.get("/Client/MyEarth.aspx")
@app.get("/MyProjects")
@app.get("/MyEarth")
@app.get("/{lang}/Projects")
@app.get("/{lang}/projects")
@app.get("/{lang}/MyProjects")
@app.get("/{lang}/myprojects")
@app.get("/{lang}/MyEarth")
@app.get("/{lang}/myearth")
async def route_projects(request: Request, lang: str = "en"):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    get_store().refresh_processing_state()
    return HTMLResponse(render_projects_page(user, lang=lang))


@app.get("/api/surveys/{survey_id}/assets")
async def route_api_survey_assets(request: Request, survey_id: int):
    user = get_current_user(request)
    uid = int(user["ID"]) if user else 0
    assets = get_store().get_survey_assets_api(survey_id, user_id=uid)
    return JSONResponse(assets)


@app.get("/OpenContainerMosaic.ashx")
async def route_open_container_mosaic(request: Request, kind: str = "survey", id: int = 0, lang: str = "en"):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    uid = int(user["ID"])
    mid = get_store().open_or_create_container_mymosaic(uid, kind=kind, container_id=id)
    if mid > 0:
        return RedirectResponse(url=f"/MyMap.aspx?id={mid}", status_code=302)
    return RedirectResponse(url=f"/{normalize_lang(lang)}/Projects", status_code=302)


from .desktop_builder import (
    build_mosaicearth_ico_bytes,
)

@app.get("/api/desktop/version")
async def route_desktop_version():
    from .desktop_distribution import release_manifest
    _, release = release_manifest()
    return JSONResponse({"ok": True, "version": release.get("version"), "engine": "NodeODX / ODX + COLMAP / OpenSplat", "download_url": "/en/Desktop", "automatic_update": False})


@app.post("/api/desktop/force-update", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_force_update():
    """Downloads the latest app-bundle.zip from MOSAICEARTH_CLOUD_ORIGIN, extracts it to APP_ROOT_DIR, and restarts Desktop."""
    import io
    import time
    import zipfile
    import subprocess
    import sys
    import threading

    cloud_origin = os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app").rstrip("/")
    app_root_dir = Path(__file__).resolve().parent.parent
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            v_resp = await client.get(f"{cloud_origin}/api/desktop/version")
            remote_ver = "3.6.3"
            if v_resp.status_code == 200:
                remote_ver = str(v_resp.json().get("version") or "3.6.3")
            b_resp = await client.get(f"{cloud_origin}/api/desktop/app-bundle.zip?t={int(time.time())}")
            if b_resp.status_code != 200:
                return JSONResponse({"ok": False, "message": f"Cloud returned HTTP {b_resp.status_code} when downloading update."}, status_code=502)
            with zipfile.ZipFile(io.BytesIO(b_resp.content), "r") as zf:
                zf.extractall(app_root_dir)

        if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
            def _restart_self() -> None:
                desktop_script = app_root_dir / "mosaicearth_desktop.py"
                if desktop_script.is_file():
                    try:
                        subprocess.Popen(
                            [sys.executable, str(desktop_script), "--no-window"],
                            cwd=str(app_root_dir),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0,
                        )
                    except Exception:
                        pass
                os._exit(0)
            threading.Timer(0.6, _restart_self).start()

        return JSONResponse({
            "ok": True,
            "version": remote_ver,
            "message": f"Updated to MosaicEarth Desktop v{remote_ver}! Reloading...",
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"Update check failed: {exc}"}, status_code=500)


@app.post("/api/local/shutdown", dependencies=[Depends(_require_desktop_local)])
@app.post("/api/desktop/shutdown", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_shutdown(request: Request):
    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1" or is_lan_or_local_request(request):
        import threading
        threading.Timer(0.2, lambda: os._exit(0)).start()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False}, status_code=403)


@app.get("/api/desktop/icon.ico")
async def route_desktop_icon():
    return Response(
        content=build_mosaicearth_ico_bytes(),
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/desktop/app-bundle.zip")
async def route_desktop_app_bundle():
    return JSONResponse({"ok": False, "message": "This legacy bundle has been retired. Download the current Desktop edition from /en/Desktop."}, status_code=410)


@app.get("/api/desktop/license-status")
async def route_desktop_license_status(request: Request):
    is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    if is_desktop:
        local_lic = verify_local_desktop_license_manifest()
        if local_lic.get("licensed"):
            return JSONResponse({
                "ok": True,
                "signed_in": True,
                "auth_mode": "session_bound_desktop_license",
                **local_lic,
            })
        return JSONResponse({
            "ok": True,
            "signed_in": False,
            **local_lic,
        })
    user = get_current_user(request)
    if not user:
        return JSONResponse({
            "ok": True,
            "signed_in": False,
            "licensed": False,
            "license_state": "UNLICENSED_FREE_VIEWER",
            "allowed_operations": ["view_public_catalog"],
            "blocked_operations": ["create_project", "local_processing", "import_commercial_gis"],
            "price_eur": 29.0,
        })
    info = get_store().get_desktop_license_info(int(user["ID"]))
    return JSONResponse({
        "ok": True,
        "signed_in": True,
        "license_state": "LICENSED_ACTIVE" if info.get("licensed") else "UNLICENSED_FREE_VIEWER",
        **info,
    })


async def _create_stripe_checkout_session(
    *,
    user: Dict[str, Any],
    mode: str,
    amount_eur: float,
    product_name: str,
    product_description: str,
    cancel_path: str,
    metadata: Dict[str, str],
    price_id: str = "",
    idempotency_key: str = "",
) -> Dict[str, Any]:
    store = get_store()
    secret_key = store.get_setting("StripeSecretKey", "")
    if not secret_key:
        return {"ok": False, "message": "Stripe secret key is not configured."}
    base_url = get_public_site_url().rstrip("/")
    unit_cents = max(50, int(round(float(amount_eur) * 100)))
    form_data: Dict[str, str] = {
        "mode": mode,
        "success_url": f"{base_url}/api/v2/stripe/complete?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}{cancel_path}",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "eur",
        "line_items[0][price_data][unit_amount]": str(unit_cents),
        "line_items[0][price_data][product_data][name]": product_name,
        "line_items[0][price_data][product_data][description]": product_description,
    }
    if price_id:
        form_data = {key: value for key, value in form_data.items() if "[price_data]" not in key}
        form_data["line_items[0][price]"] = price_id
    elif mode == "subscription":
        form_data["line_items[0][price_data][recurring][interval]"] = "month"
    u_email = str(user.get("EMAIL") or "").strip()
    if "@" in u_email:
        form_data["customer_email"] = u_email
    for k, v in metadata.items():
        form_data[f"metadata[{k}]"] = str(v)
    try:
        live = stripe_payments.live_mode(secret_key)
        async with httpx.AsyncClient(timeout=20.0) as client:
            account = await stripe_payments.stripe_account(client, secret_key)
            if price_id:
                if not re.fullmatch(r"(price|plan)_[a-zA-Z0-9]+", price_id):
                    raise stripe_payments.InvalidPayment("Invalid recurring price")
                price = await client.get(f"https://api.stripe.com/v1/prices/{price_id}", auth=(secret_key, ""))
                price.raise_for_status()
                price_data = price.json()
                unit_cents = price_data.get("unit_amount")
                if price_data.get("currency") != "eur" or type(unit_cents) is not int or unit_cents < 50:
                    raise stripe_payments.InvalidPayment("Invalid recurring price")
            resp = await client.post(
                "https://api.stripe.com/v1/checkout/sessions",
                data=form_data,
                auth=(secret_key, ""),
                headers={"Idempotency-Key": idempotency_key} if idempotency_key else {},
            )
            data = resp.json()
            if resp.status_code >= 400 or not data.get("url"):
                err_msg = (data.get("error") or {}).get("message") or "Could not create Stripe Checkout session."
                return {"ok": False, "message": err_msg}
            stripe_payments.record_order(
                store, data, live=live, user_id=int(user["ID"]), mode=mode,
                cents=unit_cents, metadata=metadata, account=account,
            )
            return {
                "ok": True,
                "requiresStripe": True,
                "requires_stripe": True,
                "checkoutUrl": str(data["url"]),
                "sessionId": str(data["id"]),
            }
    except (httpx.HTTPError, ValueError, stripe_payments.InvalidPayment, stripe_payments.RetryPayment, sqlite3.Error):
        return {"ok": False, "message": "Secure checkout could not be opened. Please try again shortly."}


def _grant_stripe_order(conn, uid, meta, session_id, stripe_cents):
    store = get_store()
    if not store.get_user_by_id(uid):
        raise stripe_payments.RetryPayment("Checkout user is unavailable")
    kind = meta.get("mosaicearth_kind")
    if kind == "desktop_license":
        res = store.purchase_desktop_license_eur(
            uid, force_stripe_paid=True, stripe_session_id=session_id, stripe_cents=stripe_cents,
        )
        redirect = "/en/Desktop?stripe_paid=1"
    elif kind == "subscription_plan":
        plan = meta["mosaicearth_plan_key"]
        res = store.upgrade_user_subscription_plan(uid, plan)
        redirect = f"/en/CreditPacks?stripe_paid=1&plan={plan}"
    elif kind == "extra_storage":
        extra_gb = int(meta["mosaicearth_extra_gb"])
        res = store.add_user_extra_storage_gb(uid, extra_gb)
        redirect = f"/en/Projects?stripe_paid=1&extra_storage={extra_gb}"
    elif kind == "credit_pack":
        credits = int(meta["mosaicearth_credits"])
        if credits <= 0:
            raise stripe_payments.InvalidPayment("Invalid number of credits")
        ok, _ = store.adjust_user_credits(uid, credits, "Stripe credit pack")
        res = {"ok": ok}
        redirect = "/en/CreditPacks?stripe_paid=1"
    elif kind == "marketplace_asset":
        res = store.purchase_marketplace_asset_direct_eur(
            uid, publish_id=int(meta["mosaicearth_publish_id"]),
            external_asset_id=int(meta["mosaicearth_external_asset_id"]),
            force_stripe_paid=True, stripe_session_id=session_id, stripe_cents=stripe_cents,
        )
        redirect = str(res.get("downloadUrl") or "/en/Projects")
    elif kind == "processing_task":
        pid = int(meta["mosaicearth_project_id"])
        task_kind = meta["mosaicearth_task_kind"]
        target = meta["mosaicearth_target_mode"]
        project = store.get_project(pid)
        if not project or int(project.get("CREATE_ID") or 0) != uid:
            raise stripe_payments.InvalidPayment("Checkout project is unavailable")
        upload_id = meta.get("mosaicearth_upload_id")
        processing_quote = None
        if upload_id:
            from .asset_uploads import AssetUpload
            upload = AssetUpload(store, get_blob_store(), pid, uid, upload_id)
            data = upload.read()
            if (task_kind != "gaussian" or data["status"] != "AWAITING_PAYMENT"
                    or project.get("PENDING_GAUSSIAN_UPLOAD_ID") != upload_id
                    or (project.get("PAYMENT_STATUS") in {"COVERED_BY_PLAN", "AUTHORIZED_PAID_UPFRONT"}
                        and project.get("AUTHORIZED_UPLOAD_ID") not in (None, "", upload_id))
                    or data.get("checkout_attempt") != meta.get("mosaicearth_checkout_attempt")
                    or upload.public_status(data)["offsets"] != [f["size"] for f in data["files"]]):
                raise stripe_payments.RetryPayment("The paid upload requires reconciliation")
            processing_quote = data["checkout_quote"]["quote"]
        res = store.authorize_and_charge_task_upfront(
            uid, project_id=pid, num_images=int(meta["mosaicearth_num_images"]), task_kind=task_kind,
            force_stripe_paid=True, stripe_session_id=session_id, target_mode=target, stripe_cents=stripe_cents,
            processing_quote=processing_quote,
        )
        if res.get("ok") and task_kind == "photogrammetry":
            conn.execute("INSERT INTO stripe_processing_outbox VALUES (?, ?, ?, ?, 0)", (session_id, pid, uid, target))
        redirect = f"/Client/Project.aspx?id={pid}&upload=1&stripe_paid=1"
        if res.get("ok") and upload_id:
            project = next(p for p in store.tables["dbo.WEB_PROJECTS"] if int(p.get("ID") or 0) == pid)
            project["AUTHORIZED_UPLOAD_ID"] = upload_id
            project.pop("PENDING_GAUSSIAN_UPLOAD_ID", None)
            store.persist_table("dbo.WEB_PROJECTS")
            conn.execute("INSERT INTO stripe_processing_outbox VALUES (?, ?, ?, ?, 0)",
                         (session_id, pid, uid, "gaussian:" + upload_id))
            redirect = f"/Client/GaussianProject.aspx?project_id={pid}&stripe_paid=1"
    else:
        raise stripe_payments.InvalidPayment("Unknown Checkout product")
    if not res.get("ok"):
        raise stripe_payments.RetryPayment("Checkout entitlement could not be applied")
    return {"ok": True, "redirect_url": redirect}


def _dispatch_stripe_processing(session_id):
    store = get_store()
    with sqlite3.connect(store.sqlite_path, timeout=30) as conn:
        task = conn.execute(
            "SELECT project_id, user_id, target FROM stripe_processing_outbox WHERE session_id = ? AND dispatched = 0",
            (session_id,),
        ).fetchone()
    if task:
        if task[2].startswith("gaussian:"):
            from .asset_uploads import AssetUpload
            result = AssetUpload(store, get_blob_store(), task[0], task[1], task[2].split(":", 1)[1]).finish()
            if not result.get("job_id"):
                raise stripe_payments.RetryPayment("Paid Gaussian processing could not be queued")
        else:
            res = store.submit_photogrammetry_to_nodeodm(task[0], task[1], target_mode=task[2], stripe_session_id=session_id)
            if not res.get("ok"):
                raise stripe_payments.RetryPayment("Paid processing could not be queued")
        with sqlite3.connect(store.sqlite_path, timeout=30) as conn:
            conn.execute("UPDATE stripe_processing_outbox SET dispatched = 1 WHERE session_id = ?", (session_id,))


async def _fulfill_stripe_checkout_session(session_id, *, event_id=None, event_type=None, user_id=None):
    match = stripe_payments.SESSION_ID.fullmatch(session_id)
    if not match:
        raise stripe_payments.InvalidPayment("Invalid Checkout session")
    store = get_store()
    secret = store.get_setting("StripeSecretKey", "")
    live = stripe_payments.live_mode(secret)
    if (match[1] == "live") != live:
        raise stripe_payments.InvalidPayment("Checkout environment mismatch")
    order = stripe_payments.read_order(store, session_id)
    if not order:
        # Live sessions created before cutover have no quote or durable replay history.
        # Acknowledge those only after operator reconciliation.
        raise stripe_payments.RetryPayment("Checkout order requires reconciliation")
    if user_id is not None and order["user_id"] != user_id:
        raise stripe_payments.InvalidPayment("Checkout user mismatch")
    if order["result"] and event_id is None:
        _dispatch_stripe_processing(session_id)
        return json.loads(order["result"])
    async with httpx.AsyncClient(timeout=12.0) as client:
        account = await stripe_payments.stripe_account(client, secret)
        if order["stripe_account"] != account:
            raise stripe_payments.InvalidPayment("Stripe account mismatch")
        resp = await client.get(
            f"https://api.stripe.com/v1/checkout/sessions/{session_id}", auth=(secret, ""),
            params={"expand[]": "payment_intent.latest_charge" if order["mode"] == "payment" else "subscription"},
        )
        resp.raise_for_status()
        session = resp.json()
    stripe_payments.verify_settlement(session, order)
    result = stripe_payments.settle(
        store, order, session, event_id, event_type,
        lambda conn, uid, meta, sid: _grant_stripe_order(conn, uid, meta, sid, order["cents"]),
    )
    if result:
        _dispatch_stripe_processing(session_id)
    return result


@app.get("/api/v2/stripe/complete")
async def route_stripe_complete(request: Request, session_id: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/en/CreditPacks", status_code=302)
    try:
        res = await _fulfill_stripe_checkout_session(session_id, user_id=int(user["ID"]))
    except (stripe_payments.InvalidPayment, stripe_payments.RetryPayment, httpx.HTTPError, sqlite3.Error, ValueError):
        res = None
    return RedirectResponse((res or {}).get("redirect_url", "/en/CreditPacks?checkout=pending"), status_code=302)


@app.post("/StripeWebhook.ashx")
@app.post("/api/v2/stripe/webhook")
async def route_stripe_webhook(request: Request):
    raw = await request.body()
    store = get_store()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip() or store.get_setting("StripeWebhookSecret", "").strip()
    if not secret:
        return JSONResponse({"ok": False, "error": "Webhook verification unavailable"}, status_code=503)
    if not stripe_payments.valid_signature(secret, raw, request.headers.get("stripe-signature", "")):
        return JSONResponse({"ok": False, "error": "Invalid Stripe signature"}, status_code=400)
    try:
        event = json.loads(raw)
        live = stripe_payments.live_mode(store.get_setting("StripeSecretKey", ""))
        event_id = event["id"]
        kind = event["type"]
        obj = event["data"]["object"]
        if (event["livemode"] is not live or event.get("account") or event.get("context")
                or not stripe_payments.EVENT_ID.fullmatch(event_id)):
            raise stripe_payments.InvalidPayment("Invalid Stripe event or environment")
        if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            if obj.get("object") != "checkout.session":
                raise stripe_payments.InvalidPayment("Invalid Checkout event")
            await _fulfill_stripe_checkout_session(obj["id"], event_id=event_id, event_type=kind)
        elif kind == "charge.refunded":
            if obj.get("object") != "charge":
                raise stripe_payments.InvalidPayment("Invalid Stripe charge event")
            async with httpx.AsyncClient(timeout=12.0) as client:
                charge, verification = await stripe_payments.verify_full_refund(
                    client, store.get_setting("StripeSecretKey", ""), obj,
                )
            stripe_payments.record_lifecycle_event(store, event_id, kind, charge, verified_refund=verification)
        elif kind in ("invoice.paid", "customer.subscription.deleted"):
            stripe_payments.record_lifecycle_event(store, event_id, kind, obj)
    except (stripe_payments.InvalidPayment, KeyError, TypeError, AttributeError, ValueError):
        return JSONResponse({"ok": False, "error": "Invalid Stripe event or Checkout"}, status_code=400)
    except (stripe_payments.RetryPayment, httpx.HTTPError, sqlite3.Error):
        return JSONResponse({"ok": False, "error": "Stripe event requires retry or reconciliation"}, status_code=503)
    return JSONResponse({"ok": True, "received": True})


@app.post("/api/v2/wallet/request-payout")
async def route_wallet_request_payout(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Please sign in first."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    dest = str(body.get("destination") or body.get("iban") or user.get("EMAIL") or "").strip()
    res = get_store().request_creator_payout_eur(int(user["ID"]), destination_iban_or_email=dest)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.post("/api/v2/checkout/desktop-license")
async def route_checkout_desktop_license(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Please sign in to your MosaicEarth account first."}, status_code=401)
    res = get_store().purchase_desktop_license_eur(int(user["ID"]))
    if res.get("requires_stripe"):
        s_res = await _create_stripe_checkout_session(
            user=user,
            mode="payment",
            amount_eur=float(res.get("amount_eur") or 29.0),
            product_name="MosaicEarth Desktop License (Windows / Linux)",
            product_description=f"Account-associated Desktop installer for {user.get('EMAIL', '')}",
            cancel_path="/en/Desktop?checkout=cancelled",
            metadata={
                "mosaicearth_kind": "desktop_license",
                "mosaicearth_user_id": str(user["ID"]),
            },
        )
        return JSONResponse(s_res, status_code=200 if s_res.get("ok") else 400)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.post("/api/desktop/verify-license")
async def route_verify_desktop_license(request: Request):
    return JSONResponse({"ok": False, "message": "Upgrade Desktop to use account and device activation."}, status_code=410)


@app.get("/api/connectors/download")
async def route_download_connector(request: Request, target: str = "cli"):
    from .desktop_distribution import connector_download
    return connector_download(request, target, get_current_user(request))


@app.post("/api/v2/mymosaics/visibility")
async def api_v2_update_mymosaic_visibility(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Please sign in first."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    map_id = int(body.get("mapId") or body.get("id") or 0)
    visibility = str(body.get("visibility") or "public").strip().lower()
    res = get_store().update_mymap_visibility(int(user["ID"]), map_id, visibility)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.post("/api/v2/plans/upgrade")
async def api_v2_upgrade_subscription_plan(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Please sign in to change your subscription plan."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    plan_key = str(body.get("plan") or "free").strip().lower()
    store = get_store()
    uid = int(user["ID"])
    if plan_key not in ("free", "pro", "studio"):
        return JSONResponse({"ok": False, "message": "Invalid plan."}, status_code=400)
    current_user = store.get_user_by_id(uid) or {}
    current_subscription = str(current_user.get("STRIPE_PLAN_SUBSCRIPTION") or "")
    if current_subscription:
        if plan_key != "free":
            return JSONResponse({"ok": False, "message": "You already have a paid subscription. Cancel it with Switch to Free before activating another plan; this prevents overlapping charges."}, status_code=409)
        # A local downgrade alone leaves Stripe charging the old subscription.
        # Resolve the authenticated user's recorded order before canceling it.
        try:
            await stripe_payments.cancel_plan_subscription(store, uid, current_subscription)
        except (httpx.HTTPError, stripe_payments.InvalidPayment, stripe_payments.RetryPayment, ValueError):
            return JSONResponse({"ok": False, "message": "Cancellation could not be confirmed by Stripe. Your plan is unchanged; please retry."}, status_code=503)
        return JSONResponse({"ok": True, "planKey": "free", "message": "Subscription canceled. Future recurring charges have stopped."})
    if plan_key in ("pro", "studio"):
        price_eur = 59.0 if plan_key == "studio" else 19.0
        u_row = store.get_user_by_id(uid) or {}
        wallet_bal = float(u_row.get("CREATOR_EARNINGS_EUR") or 0.0)
        if wallet_bal >= price_eur and body.get("useWallet", False):
            with store.lock:
                for u_item in store.tables.get("dbo.USERS", []):
                    if int(u_item.get("ID") or 0) == uid:
                        u_item["CREATOR_EARNINGS_EUR"] = round(wallet_bal - price_eur, 2)
                        store.persist_table("dbo.USERS")
                        break
            res = store.upgrade_user_subscription_plan(uid, plan_key)
            return JSONResponse(res, status_code=200 if res.get("ok") else 400)
        plan_title = (
            "MosaicEarth Studio Plan (1 TB Private Quota + 4,000 Cloud GPU Images/mo + Desktop License)"
            if plan_key == "studio"
            else "MosaicEarth Pro Plan (250 GB Private Quota + 1,000 Cloud GPU Images/mo)"
        )
        s_res = await _create_stripe_checkout_session(
            user=user,
            mode="subscription",
            amount_eur=price_eur,
            product_name=plan_title,
            product_description=f"Monthly subscription ({price_eur:.2f} €/month) for {user.get('EMAIL', '')}",
            cancel_path="/en/CreditPacks?checkout=cancelled",
            metadata={
                "mosaicearth_kind": "subscription_plan",
                "mosaicearth_plan_key": plan_key,
                "mosaicearth_user_id": str(uid),
            },
        )
        return JSONResponse(s_res, status_code=200 if s_res.get("ok") else 400)
    res = store.upgrade_user_subscription_plan(uid, plan_key)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.post("/api/v2/storage/upgrade-extra")
async def api_v2_upgrade_extra_storage(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Please sign in to add extra private cloud storage."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    extra_gb = int(body.get("extra_gb") or body.get("gb") or 250)
    packs = {
        250: (9.0, "+250 GB Extra Private Cloud Storage Add-on"),
        1000: (29.0, "+1 TB (1,000 GB) Extra Private Cloud Storage Add-on"),
        5000: (119.0, "+5 TB (5,000 GB) Extra Private Cloud Storage Add-on"),
    }
    if extra_gb not in packs:
        return JSONResponse({"ok": False, "message": "Choose +250 GB (9 €/mo), +1 TB (29 €/mo), or +5 TB (119 €/mo)."}, status_code=400)
    price_eur, pack_title = packs[extra_gb]
    store = get_store()
    uid = int(user["ID"])
    u_row = store.get_user_by_id(uid) or {}
    wallet_bal = float(u_row.get("CREATOR_EARNINGS_EUR") or 0.0)
    if wallet_bal >= price_eur and body.get("useWallet", False):
        with store.lock:
            for u_item in store.tables.get("dbo.USERS", []):
                if int(u_item.get("ID") or 0) == uid:
                    u_item["CREATOR_EARNINGS_EUR"] = round(wallet_bal - price_eur, 2)
                    store.persist_table("dbo.USERS")
                    break
        res = store.add_user_extra_storage_gb(uid, extra_gb)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    s_res = await _create_stripe_checkout_session(
        user=user,
        mode="subscription",
        amount_eur=price_eur,
        product_name=f"MosaicEarth {pack_title}",
        product_description=f"Monthly Private Cloud Storage expansion (+{extra_gb} GB at {price_eur:.2f} €/month) for {user.get('EMAIL', '')}",
        cancel_path="/en/CreditPacks?checkout=cancelled",
        metadata={
            "mosaicearth_kind": "extra_storage",
            "mosaicearth_extra_gb": str(extra_gb),
            "mosaicearth_user_id": str(uid),
        },
    )
    return JSONResponse(s_res, status_code=200 if s_res.get("ok") else 400)


@app.post("/api/v2/checkout/asset")
async def api_v2_checkout_marketplace_asset(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Please sign in to acquire or purchase this asset."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    publish_id = int(body.get("publishId") or 0)
    external_asset_id = int(body.get("externalAssetId") or body.get("assetId") or 0)
    res = get_store().purchase_marketplace_asset_direct_eur(
        int(user["ID"]),
        publish_id=publish_id,
        external_asset_id=external_asset_id,
    )
    if res.get("requires_stripe"):
        pid = int(res.get("project_id") or 0)
        cancel_url = (
            f"/Client/ExternalAssetDetail.aspx?project_id={pid}&asset_id={external_asset_id}"
            if external_asset_id > 0
            else f"/Client/ProductDetail.aspx?id={publish_id}"
        )
        s_res = await _create_stripe_checkout_session(
            user=user,
            mode="payment",
            amount_eur=float(res.get("amount_eur") or res.get("price_eur") or 10.0),
            product_name=f"MosaicAsset Commercial License: {res.get('title', 'Geospatial Dataset')}",
            product_description="Commercial Geospatial License & High-Resolution Source Download (80%–90% Direct Creator Revenue)",
            cancel_path=cancel_url,
            metadata={
                "mosaicearth_kind": "marketplace_asset",
                "mosaicearth_user_id": str(user["ID"]),
                "mosaicearth_publish_id": str(publish_id),
                "mosaicearth_external_asset_id": str(external_asset_id),
            },
        )
        return JSONResponse(s_res, status_code=200 if s_res.get("ok") else 400)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.get("/api/v2/processing/quote")
@app.post("/api/v2/processing/quote")
async def api_v2_processing_quote(request: Request):
    user = get_current_user(request)
    uid = int(user["ID"]) if user else 0
    try:
        body = await request.json()
    except Exception:
        body = {}
    qp = request.query_params
    num_images = int(body.get("numImages") or body.get("num_images") or body.get("images") or qp.get("num_images") or qp.get("numImages") or 50)
    task_kind = str(body.get("taskKind") or body.get("task_kind") or body.get("kind") or qp.get("task_kind") or qp.get("taskKind") or "photogrammetry").strip().lower()
    total_mb = float(body.get("totalMegabytes") or body.get("total_megabytes") or qp.get("total_megabytes") or qp.get("totalMegabytes") or 0.0)
    quote_info = get_store().calculate_task_processing_quote(
        uid,
        num_images=num_images,
        task_kind=task_kind,
        total_megabytes=total_mb,
    )
    return JSONResponse({"ok": True, "Success": True, **quote_info, "quote": quote_info, "Quote": quote_info})


@app.post("/api/v2/processing/authorize")
async def api_v2_processing_authorize(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Sign in required."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pid = int(body.get("projectId") or body.get("project_id") or 0)
    num_images = int(body.get("numImages") or body.get("num_images") or 0)
    task_kind = str(body.get("taskKind") or body.get("task_kind") or "photogrammetry").strip().lower()
    total_mb = float(body.get("totalMegabytes") or body.get("total_megabytes") or 0.0)
    res = get_store().authorize_and_charge_task_upfront(
        int(user["ID"]),
        project_id=pid,
        num_images=num_images,
        task_kind=task_kind,
        total_megabytes=total_mb,
    )
    if res.get("code") == "not_found":
        return JSONResponse(res, status_code=404)
    if res.get("requires_stripe"):
        s_res = await _create_stripe_checkout_session(
            user=user,
            mode="payment",
            amount_eur=float(res.get("amount_eur") or 2.0),
            product_name=f"MosaicEarth Cloud GPU Processing ({task_kind.title()} · Task #{pid})",
            product_description=f"{num_images} images · Payment reviewed if the task fails",
            cancel_path=f"/Client/Project.aspx?id={pid}&upload=1",
            metadata={
                "mosaicearth_kind": "processing_task",
                "mosaicearth_user_id": str(user["ID"]),
                "mosaicearth_project_id": str(pid),
                "mosaicearth_num_images": str(num_images),
                "mosaicearth_task_kind": task_kind,
                "mosaicearth_target_mode": "cloud",
            },
        )
        return JSONResponse(s_res, status_code=200 if s_res.get("ok") else 400)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.post("/api/v2/processing/refund")
async def api_v2_processing_refund(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Sign in required."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pid = int(body.get("projectId") or body.get("project_id") or 0)
    store = get_store()
    with store.lock:
        project = store.get_project(pid)
        if not project or int(project.get("CREATE_ID") or 0) != int(user["ID"]):
            return JSONResponse({"ok": False, "message": "Task not found."}, status_code=404)
        if (int(project.get("STATE_ID") or 0) != 3
                and project.get("PAYMENT_STATUS") not in ("REFUNDED_ON_FAILURE", "REFUND_PENDING_REVIEW")):
            return JSONResponse({"ok": False, "message": "The task has no confirmed failure."}, status_code=409)
        reason = str(project.get("LAST_ERROR") or "Processing failed before completion")
        res = store.refund_task_on_failure(pid, reason=reason)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.get("/api/v2/engine/status")
async def api_v2_engine_status():
    store = get_store()
    engine_info = store.check_nodeodm_engine()
    return JSONResponse({
        "ok": True,
        "engine_family": "External Cloud WebODM Lightning (spark1.webodm.net) + Local OpenDroneMap (NodeODM)",
        "compatible_protocols": ["NodeODM", "ClusterODM", "NodeODX", "WebODM Lightning"],
        "auto_update_enabled": True,
        "auto_update_mechanism": "Managed External Cloud Cluster + Watchtower 24h auto-pull",
        "desktop_installer_auto_update": True,
        "upfront_payment_enforced": True,
        "auto_refund_on_failure": True,
        "nodeodm_live": engine_info,
    })


@app.get("/api/v2/cloud-infra/status")
async def api_v2_cloud_infra_status():
    store = get_store()
    blob_status = get_blob_store().get_storage_status()
    ext_odm_url, _ = store.get_external_nodeodm_endpoint()
    odm_status = store.check_nodeodm_engine(ext_odm_url, allow_embedded_fallback=False)
    return JSONResponse({
        "ok": True,
        "storage": blob_status,
        "compute": {
            "photogrammetry_provider": "WebODM Lightning External Cloud (spark1.webodm.net:443)",
            "photogrammetry_endpoint": ext_odm_url,
            "photogrammetry_online": bool(odm_status.get("online")),
            "photogrammetry_engine_version": odm_status.get("engineVersion", "4.4.1"),
            "gaussian_splatting_provider": "RunPod Serverless GPU (MosaicEarth Gaussian production)",
            "gaussian_splatting_endpoint_id": store.get_setting("Gaussian.EndpointId", "lxcyqllatkyw1s"),
            "gaussian_splatting_active": True,
            "upfront_preauthorization": True,
            "auto_refund_on_failure": True,
            "retail_rate_free_eur_per_image": 0.030,
            "retail_rate_pro_eur_per_image": 0.020,
            "retail_rate_studio_eur_per_image": 0.015,
            "local_nodeodm_supported": True,
        },
    })


@app.post("/api/v2/cloud-infra/config")
async def api_v2_cloud_infra_config(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Authentication required."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    updated = get_blob_store().update_s3_config(body)
    return JSONResponse({"ok": True, "storage": updated})


@app.post("/api/v2/connectors/sync")
async def api_v2_connectors_sync(request: Request):
    return JSONResponse({"ok": False, "message": "Update your connector to synchronize files with /api/v2/sync/sessions. No files or projects were copied."}, status_code=410)


@app.get("/Client/Project.aspx")
@app.get("/{lang}/Project")
@app.get("/{lang}/Projects/{slug}")
@app.get("/{lang}/projects/{slug}")
async def route_project_detail(
    request: Request,
    id: Optional[int] = None,
    slug: Optional[str] = None,
    lang: str = "en",
):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    target = str(slug or id or request.query_params.get("id") or "")
    if not target:
        return RedirectResponse(url=f"/{normalize_lang(lang)}/Projects", status_code=302)
    store = get_store()
    pid = int(target) if target.isdigit() else (try_parse_trailing_int(target) or 0)
    proj = store.get_project(pid) if pid > 0 else store.get_project_by_slug(target)
    if proj:
        pid_resolved = int(proj.get("ID") or 0)
        state_id = int(proj.get("STATE_ID") or 3)
        zip_uploaded = 1 if (bool(proj.get("ZIP_UPLOADED")) or state_id == 4) else 0
        if state_id == 4 and zip_uploaded == 1 and request.query_params.get("upload") != "1":
            root_pid, _ = store.get_subprojects_family(pid_resolved)
            return RedirectResponse(
                url=f"/{normalize_lang(lang)}/Projects?open_project={root_pid}#taskNode_{pid_resolved}",
                status_code=302,
            )
    html_doc = render_project_detail_page(user, target, lang=lang)
    if not html_doc:
        return RedirectResponse(url=f"/{normalize_lang(lang)}/Projects", status_code=302)
    return HTMLResponse(html_doc)


@app.get("/Client/Collection.aspx")
@app.get("/MyAssets")
@app.get("/{lang}/Collection")
@app.get("/{lang}/collection")
@app.get("/{lang}/MyAssets")
@app.get("/{lang}/myassets")
async def route_collection(request: Request, lang: str = "en"):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    return HTMLResponse(render_collection_page(user, lang=lang))


@app.get("/Client/MyMaps.aspx")
@app.get("/MyMosaics")
@app.get("/{lang}/MyMaps")
@app.get("/{lang}/mymaps")
@app.get("/{lang}/MyMosaics")
@app.get("/{lang}/mymosaics")
async def route_mymaps(request: Request, lang: str = "en"):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    return HTMLResponse(render_mymaps_page(user, lang=lang))


@app.get("/Client/MyMapDetail.aspx")
async def route_mymap_detail(request: Request, id: int = 0, lang: str = "en"):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    return HTMLResponse(render_mymap_detail_page(user, map_id=id, lang=lang))


@app.get("/MyMap.aspx")
@app.get("/{lang}/MyMaps/{slug}")
@app.get("/{lang}/mymaps/{slug}")
async def route_mymap_viewer(request: Request, slug: str = "", id: int = 0, lang: str = "en"):
    user = get_current_user(request)
    target = str(slug or id or request.query_params.get("id") or "").strip()
    if not target or target.lower() == "new":
        redir = require_user_or_redirect(request)
        if redir:
            return redir
        return HTMLResponse(render_mymap_detail_page(user, map_id=0, lang=lang))
    html_doc = render_mymap_viewer_page(user, target, lang=lang)
    if not html_doc:
        return RedirectResponse(url=f"/{normalize_lang(lang)}/PublicMosaics" if not user else f"/{normalize_lang(lang)}/MyMaps", status_code=302)
    return HTMLResponse(html_doc)


@app.get("/PublicMosaics")
@app.get("/{lang}/PublicMosaics")
@app.get("/{lang}/publicmosaics")
async def route_public_mosaics(request: Request, lang: str = "en"):
    user = get_current_user(request)
    return HTMLResponse(render_public_mosaics_page(user=user, lang=lang))


@app.get("/Desktop")
@app.get("/{lang}/Desktop")
@app.get("/{lang}/desktop")
async def route_desktop_connectors(request: Request, lang: str = "en"):
    user = get_current_user(request)
    return HTMLResponse(render_desktop_connectors_page(user=user, lang=lang))


@app.get("/Client/Profile.aspx")
@app.get("/Profile")
@app.get("/{lang}/Profile")
@app.get("/{lang}/profile")
async def route_profile(request: Request, lang: str = "en"):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    return HTMLResponse(render_profile_page(user, lang=lang))


@app.get("/Client/CreditPacks.aspx")
@app.get("/Client/PurchaseOK.aspx")
@app.get("/Client/PurchaseKO.aspx")
@app.get("/CreditPacks")
@app.get("/{lang}/CreditPacks")
@app.get("/{lang}/creditpacks")
@app.get("/{lang}/Success")
async def route_credit_packs(request: Request, lang: str = "en"):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    return HTMLResponse(render_credit_packs_page(user, lang=lang))


@app.post("/Client/CreditPacks.aspx/createSessionStripe")
@app.post("/{lang}/CreditPacks/createSessionStripe")
async def webmethod_create_session_stripe(request: Request, lang: str = "en"):
    user = get_current_user(request)
    if not user:
        return aspnet_d("ERROR:Your session has expired. Sign in again to continue.")
    try:
        body = await request.json()
        pack_id = int(body.get("pack_id") or 0)
    except (ValueError, TypeError, AttributeError):
        pack_id = 0
    return await _create_credit_pack_checkout(user, pack_id, recurring=False)


@app.post("/Client/CreditPacks.aspx/createSubscriptionSessionStripe")
@app.post("/{lang}/CreditPacks/createSubscriptionSessionStripe")
async def webmethod_create_subscription_session_stripe(request: Request, lang: str = "en"):
    user = get_current_user(request)
    if not user:
        return aspnet_d("ERROR:Your session has expired. Sign in again to continue.")
    try:
        body = await request.json()
        pack_id = int(body.get("pack_id") or 0)
    except (ValueError, TypeError, AttributeError):
        pack_id = 0
    return await _create_credit_pack_checkout(user, pack_id, recurring=True)


async def _create_credit_pack_checkout(user, pack_id, recurring):
    target = next((p for p in get_store().get_credit_packs() if int(p["ID"]) == pack_id), None)
    if not target:
        return aspnet_d("ERROR:This credit pack is no longer available.")
    price_id = str(target["ID_ITEM_STRIPE_SUBSCRIPTION"]).strip() if recurring else ""
    if recurring and not price_id:
        return aspnet_d("ERROR:Monthly subscription is not configured for this credit pack.")
    credits = int(float(target["CREDITS"].replace(",", ".")))
    amount = float(target["PRICE_DOLAR"].replace(",", "."))
    if credits <= 0 or (not recurring and amount < 0.50):
        return aspnet_d("ERROR:This credit pack is no longer available.")
    res = await _create_stripe_checkout_session(
        user=user, mode="subscription" if recurring else "payment", amount_eur=amount,
        product_name=f"{credits} credits", product_description=target["DESCRIPTION"],
        cancel_path="/en/CreditPacks?checkout=cancelled", price_id=price_id,
        metadata={"mosaicearth_kind": "credit_pack", "mosaicearth_pack_id": str(pack_id),
                  "mosaicearth_credits": str(credits), "mosaicearth_user_id": str(user["ID"])},
    )
    return aspnet_d(res["sessionId"] if res.get("ok") else f"ERROR:{res['message']}")


@app.get("/Map.aspx")
@app.get("/MapView.aspx")
async def route_internal_map_view(request: Request, id: int = 0, type: Optional[int] = None, asset_id: int = 0):
    store = get_store()
    project = store.get_project(id)
    external = store.get_external_asset(id, asset_id) if asset_id else None
    _enforce_project_and_asset_acl(request, store, project, external)
    # Historic Map links are orthophotos; MapView links default to a mesh.
    if type is None:
        type = 2 if request.url.path == "/Map.aspx" and not asset_id else 3
    html_doc = render_internal_viewer_page(get_current_user(request), id, type, mode="view", asset_id=asset_id)
    if not html_doc:
        return RedirectResponse(url="/en/Map", status_code=302)
    return HTMLResponse(html_doc)


@app.get("/MapEdit.aspx")
async def route_internal_map_edit(request: Request, id: int = 0, type: int = 3, asset_id: int = 0):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    html_doc = render_internal_viewer_page(get_current_user(request), id, type, mode="edit", asset_id=asset_id)
    if not html_doc:
        return RedirectResponse(url="/en/Projects", status_code=302)
    return HTMLResponse(html_doc)


@app.get("/MapAnnotations.aspx")
async def route_internal_map_annotations(request: Request, id: int = 0, type: int = 3, asset_id: int = 0):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    html_doc = render_internal_viewer_page(get_current_user(request), id, type, mode="annotations", asset_id=asset_id)
    if not html_doc:
        return RedirectResponse(url="/en/Projects", status_code=302)
    return HTMLResponse(html_doc)


@app.get("/Client/ExternalAssetImport.aspx")
async def route_external_asset_import(
    request: Request,
    project_id: Optional[int] = None,
    id: Optional[int] = None,
    asset_id: Optional[int] = None,
    lang: str = "en",
):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    get_store().refresh_processing_state()
    target_pid = int(project_id or id or request.query_params.get("project_id") or request.query_params.get("id") or 0)
    if target_pid <= 0:
        return RedirectResponse(url=f"/{normalize_lang(lang)}/Projects", status_code=302)
    html_doc = render_external_asset_import_page(user, target_pid, lang=lang)
    if not html_doc:
        return RedirectResponse(url=f"/{normalize_lang(lang)}/Projects", status_code=302)
    return HTMLResponse(html_doc)


@app.get("/Client/GaussianProject.aspx")
async def route_gaussian_project(
    request: Request,
    id: Optional[int] = None,
    project_id: Optional[int] = None,
    lang: str = "en",
):
    redir = require_user_or_redirect(request)
    if redir:
        return redir
    user = get_current_user(request)
    get_store().refresh_processing_state()
    target_pid = int(id or project_id or request.query_params.get("id") or request.query_params.get("project_id") or 0)
    if target_pid <= 0:
        return RedirectResponse(url=f"/{normalize_lang(lang)}/Projects", status_code=302)
    html_doc = render_gaussian_project_page(user, target_pid, lang=lang)
    if not html_doc:
        return RedirectResponse(url=f"/{normalize_lang(lang)}/Projects", status_code=302)
    return HTMLResponse(html_doc)


@app.get("/Client/Faqs.aspx")
@app.get("/{lang}/Faqs")
@app.get("/{lang}/faqs")
async def route_faqs(request: Request, lang: str = "en"):
    norm_lang = normalize_lang(lang)
    seo_head = build_head_metadata_html(
        title="Help & FAQs — Photogrammetry, 3D Gaussian Splatting, Storage Plans & Licensing · MosaicEarth",
        description="Complete documentation on MosaicEarth 3D Globe navigation, Cloud GPU & Local NodeODM photogrammetry, 3D Gaussian Splatting, 5 GB / 250 GB / 1 TB private storage plans, and direct Euro (€) Marketplace licensing.",
        canonical_path=f"/{norm_lang}/faqs",
        json_ld_graphs=[
            build_web_application_json_ld(),
            build_breadcrumb_json_ld([("MosaicEarth", get_landing_site_url()), ("3D Globe", f"/{norm_lang}/Map"), ("FAQs", f"/{norm_lang}/faqs")]),
        ],
    )
    body = """
    <section class="pt-4 pb-5">
        <div class="container">
            <div class="box-4d" style="padding:28px;">
                <h1 style="font-size:26px;color:#fff;margin-bottom:12px;">MosaicEarth Help, Workflows &amp; Technical FAQs</h1>
                <p style="color:#9ca3af;font-size:14px;">Everything you need to know about 3D globe exploration, photogrammetry, 3D Gaussian Splatting, storage plans, MyMosaics visibility and Euro (&euro;) licensing.</p>
                <hr style="border-color:rgba(255,255,255,0.1);" />
                <h2 style="font-size:18px;color:#99CC00;">1. Supported Geospatial Formats &amp; WebGL Streaming</h2>
                <p style="font-size:13px;color:#d1d5db;">MosaicEarth supports three primary asset families: <strong>Maps &amp; Layers (fileTypeId=2)</strong> including GeoTIFF/COG, DSM/DTM, GeoPackage (.gpkg), GeoJSON, KML/KMZ, Shapefile (.zip), DXF and GPX; <strong>3D Models (fileTypeId=3)</strong> including GLB/glTF 2.0, Textured Wavefront OBJ (.zip), OGC 3D Tiles and 3D Gaussian Splatting (.ply); and <strong>Point Clouds (fileTypeId=1)</strong> including LAS, LAZ, PLY, PCD, PTS and XYZ.</p>
                <h2 style="font-size:18px;color:#99CC00;">2. Serverless Cloud GPU vs. Local Workstation NodeODM (0 &euro;)</h2>
                <p style="font-size:13px;color:#d1d5db;">Photogrammetry and 3D Gaussian Splatting tasks can run on MosaicEarth Serverless Cloud GPU (included in Pro 1,000 imgs/mo and Studio 4,000 imgs/mo, or pay-per-task in &euro; with no prepaid balance required) or at <strong>0 &euro; forever</strong> on your own workstation via MosaicEarth Desktop or a local NodeODM node.</p>
                <h2 style="font-size:18px;color:#99CC00;">3. Permanent Private Quota (5 GB Free / 250 GB Pro / 1 TB Studio) &amp; 0 GB Public Assets</h2>
                <p style="font-size:13px;color:#d1d5db;">Every account includes 5 GB of permanent private storage (no 15-day expiration). Publishing any asset to the public MosaicEarth 3D Globe (Free 0 &euro; or Marketplace &euro;) consumes <strong>0 GB</strong> of your private quota and pays 80%&ndash;90% revenue directly to the creator.</p>
            </div>
        </div>
    </section>"""
    return HTMLResponse(render_home_master("", seo_head, body, user=get_current_user(request), lang=norm_lang))


@app.get("/License.aspx")
async def route_license(request: Request):
    seo_head = build_head_metadata_html(
        title="MosaicEarth Standard Geospatial License · MosaicEarth",
        description=SHORT_LICENSE_SUMMARY,
        canonical_path="/License.aspx",
        json_ld_graphs=[build_web_application_json_ld()],
    )
    body = f"""
    <section class="pt-4 pb-5">
        <div class="container">
            <div class="box-4d" style="padding:28px;">
                <h1 style="font-size:26px;color:#fff;margin-bottom:12px;">MosaicEarth Standard Geospatial License</h1>
                <p style="color:#99CC00;font-size:14px;">{html.escape(SHORT_LICENSE_SUMMARY)}</p>
                <pre style="background:#070b09;color:#d1d5db;padding:18px;border-radius:8px;white-space:pre-wrap;font-size:13px;">{html.escape(CURRENT_LICENSE_TEXT)}</pre>
            </div>
        </div>
    </section>"""
    return HTMLResponse(render_home_master("", seo_head, body, user=get_current_user(request)))


@app.get("/Terms.aspx")
@app.get("/Privacy.aspx")
@app.get("/{lang}/Terms")
@app.get("/{lang}/terms")
async def route_terms(request: Request, lang: str = "en"):
    seo_head = build_head_metadata_html(
        title="Terms of Service, Privacy & Sub-Processor Policy · MosaicEarth",
        description="Terms of service, data ownership, GDPR sub-processor disclosure, private workspace storage plans (5 GB Free, 250 GB Pro, 1 TB Studio), and Euro (€) Marketplace policies on MosaicEarth.",
        canonical_path="/Terms.aspx",
        json_ld_graphs=[build_web_application_json_ld()],
    )
    body = """
    <section class="pt-4 pb-5">
        <div class="container">
            <div class="box-4d" style="padding:28px;">
                <h1 style="font-size:26px;color:#fff;margin-bottom:14px;">Terms of Service &amp; Privacy Policy — MosaicEarth Platform</h1>
                <p style="color:#d1d5db;font-size:13.5px;line-height:1.65;">
                    Creators retain full intellectual property rights over their uploaded and processed geospatial datasets while granting MosaicEarth the right to stream published assets on the public 3D globe and deliver licensed copies to Marketplace buyers under the MosaicEarth Standard License snapshot recorded at the time of acquisition.
                </p>
                <h2 style="font-size:18px;color:#99CC00;margin-top:22px;margin-bottom:10px;">Upfront Processing Authorization &amp; 100% Failure Refund Guarantee</h2>
                <p style="color:#d1d5db;font-size:13.5px;line-height:1.65;">
                    Cloud photogrammetry and 3D reconstruction tasks display their exact Euro (&euro;) price or monthly plan quota deduction prior to user confirmation. Charges are authorized upfront before task execution. If a processing task fails or is cancelled prior to completion, MosaicEarth automatically refunds 100% of the upfront charge (0.00 &euro; billed) or restores the corresponding monthly plan allowance.
                </p>
                <h2 style="font-size:18px;color:#99CC00;margin-top:22px;margin-bottom:10px;">Data Hosting, Processing Infrastructure &amp; GDPR Sub-Processors (Art. 28 GDPR)</h2>
                <p style="color:#d1d5db;font-size:13.5px;line-height:1.65;margin-bottom:8px;">
                    In compliance with European Union data protection regulations (GDPR), MosaicEarth processes and stores geospatial datasets using encrypted cloud infrastructure and authorized technical sub-processors:
                </p>
                <ul style="color:#d1d5db;font-size:13px;line-height:1.7;padding-left:20px;margin-bottom:0;">
                    <li><strong>Geospatial Object Storage &amp; Content Delivery:</strong> Encrypted S3-compatible cloud object storage and edge CDN distribution for private workspace layers and public 3D tilesets.</li>
                    <li><strong>Ephemeral Photogrammetry &amp; 3D Compute Nodes:</strong> Isolated on-demand GPU/CPU compute instances running official OpenDroneMap (<code>NodeODM</code>) and 3D Gaussian Splatting pipelines solely for the duration of active tasks.</li>
                    <li><strong>Payment Processing:</strong> Stripe Payments Europe, Ltd. (PCI-DSS Level 1 certified) for direct Euro (&euro;) subscriptions, pay-per-task processing, and Marketplace creator payouts.</li>
                </ul>
            </div>
        </div>
    </section>"""
    return HTMLResponse(render_home_master("", seo_head, body, user=get_current_user(request), lang=lang))


@app.get("/WorldSimulator.aspx")
async def route_world_simulator_legacy_redirect(request: Request):
    qs = str(request.url.query or "")
    target = f"/en/fly?{qs}" if qs else "/en/fly"
    return RedirectResponse(url=target, status_code=301)


@app.get("/Fly.aspx")
@app.get("/fly.aspx")
@app.get("/fly")
@app.get("/{lang}/Fly")
@app.get("/{lang}/fly")
async def route_world_simulator(
    request: Request,
    lang: str = "en",
    asset_id: str = "",
    name: str = "",
    lat: float = 41.9702,
    lon: float = 2.3885,
    alt: float = 0.0,
    kind: str = "",
):
    # Enforce the same viewing rights before rendering names, coordinates and
    # scene membership as the model/tiles endpoints themselves.
    store = get_store()
    user = get_current_user(request)
    parts = (asset_id or "").strip().lower().split("-")
    if parts[0] == "external" and len(parts) in (2, 3) and all(p.isdigit() for p in parts[1:]):
        ext = store.get_external_asset_by_id(int(parts[-1]))
        if not ext:
            raise HTTPException(status_code=404, detail="Asset not found")
        _enforce_project_and_asset_acl(request, store, store.get_project(int(ext.get("PROJECT_ID") or 0)), ext_asset=ext)
    elif parts[0] == "processed" and len(parts) == 3 and all(p.isdigit() for p in parts[1:]):
        project = store.get_project(int(parts[2]))
        if not project:
            raise HTTPException(status_code=404, detail="Asset not found")
        _enforce_project_and_asset_acl(request, store, project)
    elif parts[0] in {"mymap", "mymosaic"} and len(parts) == 2 and parts[1].isdigit():
        scene = store.get_mymap(parts[1])
        if not scene:
            raise HTTPException(status_code=404, detail="MyMosaic not found")
        visibility = str(scene.get("VISIBILITY") or ("private" if scene.get("IS_CONTAINER_PREVIEW") else "public")).lower()
        owner = int(scene.get("CREATE_ID") or scene.get("USER_ID") or 0)
        if visibility == "private" and (not user or int(user["ID"]) != owner):
            raise HTTPException(status_code=403, detail="This MyMosaic is private")
    html_doc = render_world_simulator_page(
        asset_id=asset_id,
        name=name,
        lat=lat,
        lon=lon,
        alt=alt,
        kind=kind,
        user=get_current_user(request),
        lang=lang,
    )
    return HTMLResponse(html_doc)


@app.get("/Potree.aspx")
async def route_potree_viewer(request: Request, id: int = 0, type: int = 1, pub: int = 0):
    store = get_store()
    proj = store.get_project(id) if id else None
    folder = str(proj.get("FOLDER") or "") if proj else ""
    name = str(proj.get("NAME") or f"Point Cloud #{id}") if proj else f"Point Cloud #{id}"
    blob_store = get_blob_store()
    has_potree_octree = bool(folder and type == 1 and blob_store.exists("imagenes", f"{folder}/procesado/potree/cloud.js"))
    if not has_potree_octree:
        if id > 0:
            html_doc = render_internal_viewer_page(get_current_user(request), id, type, mode="view", pub_id=pub)
            if html_doc:
                return HTMLResponse(html_doc)
        if pub > 0 or id > 0:
            html_doc = render_product_detail_page(publish_id=pub or None, project_id=id or None, file_type_id=type, user=get_current_user(request))
            if html_doc:
                return HTMLResponse(html_doc)
    cloud_js_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/potree/cloud.js"
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(name)} · Isolated Potree Point Cloud Viewer · MosaicEarth</title>
    <link rel="stylesheet" type="text/css" href="/Potree/build/potree/potree.css">
    <link rel="stylesheet" type="text/css" href="/Potree/libs/jquery-ui/jquery-ui.min.css">
    <link rel="stylesheet" type="text/css" href="/Potree/libs/openlayers3/ol.css">
    <link rel="stylesheet" type="text/css" href="/Potree/libs/spectrum/spectrum.css">
    <link rel="stylesheet" type="text/css" href="/Potree/libs/jstree/themes/mixed/style.css">
</head>
<body style="margin:0;overflow:hidden;background:#070b09;">
    <div class="potree_container" style="position: absolute; width: 100%; height: 100%; left: 0px; top: 0px;">
        <div id="potree_render_area"></div>
        <div id="potree_sidebar_container"></div>
    </div>
    <script src="/Potree/libs/jquery/jquery-3.1.1.min.js"></script>
    <script src="/Potree/libs/spectrum/spectrum.js"></script>
    <script src="/Potree/libs/jquery-ui/jquery-ui.min.js"></script>
    <script src="/Potree/libs/three.js/build/three.min.js"></script>
    <script src="/Potree/libs/other/BinaryHeap.js"></script>
    <script src="/Potree/libs/tween/tween.min.js"></script>
    <script src="/Potree/libs/d3/d3.js"></script>
    <script src="/Potree/libs/proj4/proj4.js"></script>
    <script src="/Potree/libs/openlayers3/ol.js"></script>
    <script src="/Potree/libs/i18next/i18next.js"></script>
    <script src="/Potree/libs/jstree/jstree.js"></script>
    <script src="/Potree/build/potree/potree.js"></script>
    <script src="/Potree/libs/plasio/js/laslaz.js"></script>
    <script>
        window.viewer = new Potree.Viewer(document.getElementById("potree_render_area"));
        viewer.setEDLEnabled(true);
        viewer.setFOV(60);
        viewer.setPointBudget(2000000);
        viewer.loadSettingsFromURL();
        viewer.loadGUI(() => {{
            viewer.setLanguage('en');
            $("#menu_appearance").next().show();
        }});
        Potree.loadPointCloud({json.dumps(cloud_js_url)}, {json.dumps(name)}, function(e) {{
            viewer.scene.addPointCloud(e.pointcloud);
            let material = e.pointcloud.material;
            material.size = 1;
            material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
            viewer.fitToScreen();
        }});
    </script>
</body>
</html>"""
    )


@app.get("/WebODMConnect.aspx")
async def route_webodm_connect(request: Request, code: Optional[str] = None):
    user = get_current_user(request)
    code_val = html.escape(code or "")
    inner = f"""
    <div class="row justify-content-center">
        <div class="col-md-6">
            <div class="p-4 rounded" style="background:#232323;border:1px solid rgba(153,204,0,0.3);">
                <h3 style="font-size:18px;color:#99CC00;">Authorize User WebODM Plugin</h3>
                <p style="font-size:13px;color:#d1d5db;">Enter the user code displayed in your WebODM MosaicEarth plugin to link your WebODM instance with your MosaicEarth account.</p>
                <form method="post" action="/WebODMConnect.aspx">
                    <div class="mb-3">
                        <label class="form-label" style="font-size:12px;">Device User Code</label>
                        <input type="text" name="user_code" class="form-control bg-dark text-light" value="{code_val}" placeholder="ABCD-1234" required />
                    </div>
                    <button type="submit" class="btn" style="background:#99CC00;color:#070b09;font-weight:700;">AUTHORIZE WEBODM PLUGIN</button>
                </form>
            </div>
        </div>
    </div>"""
    return HTMLResponse(
        render_workspace_page("WebODM Connect", "Connect User WebODM Instance", "Pair your self-hosted WebODM node with MosaicEarth.", inner, user=user)
    )


@app.post("/WebODMConnect.aspx")
async def route_webodm_connect_post(request: Request, user_code: str = Form("")):
    user = get_current_user(request)
    store = get_store()
    if user:
        uid = int(user["ID"])
    else:
        active_users = [u for u in store.tables.get("dbo.USERS", []) if u.get("ACTIVO", True)]
        uid = int(active_users[0]["ID"]) if active_users else 1
    ok = store.approve_webodm_device_code(user_code, uid)
    msg = "WebODM Device Code Authorized Successfully!" if ok else "Device code not found or expired."
    return HTMLResponse(
        render_workspace_page("WebODM Connect", msg, "You may now return to your WebODM dashboard.", f"<div class='alert alert-info'>{html.escape(msg)}</div>", user=user)
    )


@app.get("/Admin/ControlCenter.aspx")
@app.get("/Admin/Default.aspx")
async def route_admin_control_center(request: Request):
    store = get_store()
    user = get_current_user(request)
    inner = f"""
    <div class="row g-3">
        <div class="col-md-3"><div class="p-3 rounded" style="background:#232323;border:1px solid rgba(153,204,0,0.3);"><h5>Users</h5><strong style="font-size:22px;color:#99CC00;">{len(store.tables.get('dbo.USERS', []))}</strong></div></div>
        <div class="col-md-3"><div class="p-3 rounded" style="background:#232323;border:1px solid rgba(153,204,0,0.3);"><h5>Projects</h5><strong style="font-size:22px;color:#99CC00;">{len(store.tables.get('dbo.WEB_PROJECTS', []))}</strong></div></div>
        <div class="col-md-3"><div class="p-3 rounded" style="background:#232323;border:1px solid rgba(153,204,0,0.3);"><h5>Publications</h5><strong style="font-size:22px;color:#99CC00;">{len(store.tables.get('dbo.WEB_PUBLICATIONS', []))}</strong></div></div>
        <div class="col-md-3"><div class="p-3 rounded" style="background:#232323;border:1px solid rgba(153,204,0,0.3);"><h5>Integrations</h5><strong style="font-size:22px;color:#99CC00;">{len(store.integrations)} Keys</strong></div></div>
    </div>"""
    return HTMLResponse(
        render_workspace_page("Admin Control Center", "MosaicEarth Admin Control Center (Linux Homelab)", "Unified administration of users, credits, WebODM, Runpod 3DGS, SMTP and storage.", inner, user=user)
    )


# ============================================================================
# ASP.NET {"d": ...} WEBMETHODS: MAP2.ASPX (PUBLIC GLOBE & CATALOGUE)
# ============================================================================
def _filter_index_items(
    items: List[Dict[str, Any]],
    file_type_id: Optional[Any] = None,
    search_text: Optional[str] = None,
    extensions: Optional[str] = None,
    bounds: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    res = list(items)
    if file_type_id is not None and str(file_type_id).strip() not in ("", "0", "None"):
        try:
            ft = int(file_type_id)
            if ft > 0:
                res = [x for x in res if int(x.get("FileTypeId") or 0) == ft]
        except Exception:
            pass
    if extensions and str(extensions).strip():
        req_exts = {e.strip().lower().lstrip(".") for e in re.split(r"[,\s;]+", str(extensions)) if e.strip()}
        if req_exts:
            filtered = []
            for x in res:
                item_exts = set(
                    re.split(
                        r"[,\s;]+",
                        f"{x.get('OriginalExtension') or ''} {x.get('ProcessedExtensions') or ''} {x.get('SourceFormat') or ''}".lower(),
                    )
                )
                if req_exts.intersection(item_exts):
                    filtered.append(x)
            res = filtered
    if search_text and str(search_text).strip():
        q = str(search_text).strip().lower()
        res = [
            x
            for x in res
            if q in str(x.get("Name") or "").lower()
            or q in str(x.get("Creator") or "").lower()
            or q in str(x.get("Description") or "").lower()
            or q in str(x.get("CategoryName") or "").lower()
        ]
    if isinstance(bounds, dict) and all(k in bounds for k in ("west", "south", "east", "north")):
        try:
            west = float(bounds["west"])
            south = float(bounds["south"])
            east = float(bounds["east"])
            north = float(bounds["north"])
            res = [
                x
                for x in res
                if south <= float(x.get("Latitude") or 0) <= north
                and (
                    (west <= float(x.get("Longitude") or 0) <= east)
                    if west <= east
                    else (float(x.get("Longitude") or 0) >= west or float(x.get("Longitude") or 0) <= east)
                )
            ]
        except Exception:
            pass
    return res


@app.post("/Map2.aspx/{method_name}")
@app.post("/{lang}/Map/{method_name}")
async def webmethod_map2_dispatcher(method_name: str, request: Request, lang: str = "en"):
    try:
        body = await request.json() if request.headers.get("content-length") != "0" else {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    m = method_name.strip()
    m_low = m.lower()
    all_items = get_store().build_public_asset_index()

    if m_low in ("filterpublicassetindex", "querypublicassetindex", "searchpublicassetindex"):
        def _parse_float(val):
            if val is None or str(val).strip() == "":
                return None
            try:
                return float(str(val).replace(",", "."))
            except Exception:
                return None

        q = str(body.get("query") or body.get("searchText") or body.get("search") or "").strip()
        ext_filter = str(body.get("extension") or body.get("extensions") or "").strip()
        fams_raw = body.get("families") if body.get("families") is not None else (body.get("fileTypeId") or body.get("type") or "")
        if isinstance(fams_raw, list):
            fams_list = [str(f).strip() for f in fams_raw if str(f).strip()]
        else:
            fams_list = [f.strip() for f in str(fams_raw).split(",") if f.strip() and f.strip() != "0"]

        bounds = body.get("bounds") if isinstance(body.get("bounds"), dict) else {}
        n_val = _parse_float(body.get("north") if "north" in body else bounds.get("north"))
        s_val = _parse_float(body.get("south") if "south" in body else bounds.get("south"))
        e_val = _parse_float(body.get("east") if "east" in body else bounds.get("east"))
        w_val = _parse_float(body.get("west") if "west" in body else bounds.get("west"))

        try:
            page_size = max(24, min(240, int(body.get("pageSize") or 240)))
        except Exception:
            page_size = 240
        try:
            cursor = max(0, min(5000, int(body.get("cursor") or 0)))
        except Exception:
            cursor = 0

        from .db_store import _parse_iso_dt
        cap_from = _parse_iso_dt(body.get("capturedFromUtc") or body.get("dateFrom"))
        cap_to = _parse_iso_dt(body.get("capturedToUtc") or body.get("dateTo"))

        # Just like Map2.aspx.cs: when searching by text query, search across the entire globe
        use_bounds = (not q) and (m_low != "searchpublicassetindex")
        candidates = get_store().build_public_asset_index(
            north=n_val if use_bounds else None,
            south=s_val if use_bounds else None,
            east=e_val if use_bounds else None,
            west=w_val if use_bounds else None,
            families=fams_list,
            limit=min(5000, cursor + page_size + 1),
            captured_from_utc=cap_from,
            captured_to_utc=cap_to,
            search=q or None,
            extension=ext_filter or None,
            lang=lang,
        )
        page_items = candidates[cursor : cursor + page_size]
        has_more = len(candidates) > cursor + len(page_items)
        return aspnet_d({
            "Items": page_items,
            "Cursor": cursor + len(page_items),
            "HasMore": has_more,
            "catalogueItems": page_items,
            "globeItems": candidates,
        })

    if m_low in ("loadpublicassetindex", "getpublicassetindex"):
        ft = body.get("fileTypeId") or body.get("type")
        q = body.get("searchText")
        exts = body.get("extensions")
        bounds = body.get("bounds")
        filtered = _filter_index_items(all_items, file_type_id=ft, search_text=q, extensions=exts, bounds=bounds)
        return aspnet_d(filtered)

    if m_low == "loaditems":
        ft = body.get("type")
        exts = body.get("extensions")
        filtered = _filter_index_items(all_items, file_type_id=ft, extensions=exts)
        legacy = [
            {
                "ID": x.get("PublishId") or x.get("ExternalAssetId"),
                "PROJECT_ID": x.get("ProjectId"),
                "FILE_TYPE_ID": x.get("FileTypeId"),
                "NAME": x.get("Name"),
                "DESCRIPTION": x.get("Description"),
                "USERNAME": x.get("Creator"),
                "CREDITS": x.get("PriceCredits"),
                "IMAGE_URL": x.get("ImageUrl"),
                "ICON_TYPE": x.get("IconType"),
                "LATITUD": str(x.get("Latitude")),
                "LONGITUD": str(x.get("Longitude")),
                "DETAIL_URL": x.get("DetailUrl"),
                "ORIGINAL_EXTENSION": x.get("OriginalExtension"),
                "PROCESSED_EXTENSIONS": x.get("ProcessedExtensions"),
            }
            for x in filtered
        ]
        return aspnet_d([legacy])

    if m_low == "load3dmodels":
        exts = body.get("extensions")
        filtered = _filter_index_items(all_items, file_type_id=3, extensions=exts)
        models = [
            {
                "ID": x.get("PublishId"),
                "ID_PROJECT": x.get("ProjectId"),
                "PUBLISH_ID": x.get("PublishId"),
                "URL": f"/{x.get('Folder')}/procesado/3d_tiles/model/tileset.json",
                "Lon": str(x.get("Longitude")),
                "Lat": str(x.get("Latitude")),
                "Height": str(x.get("Height") or 0),
                "TYPE": 3,
            }
            for x in filtered
            if x.get("Origin") == "processed"
        ]
        return aspnet_d([models])

    if m_low == "loadpointcloud":
        exts = body.get("extensions")
        filtered = _filter_index_items(all_items, file_type_id=1, extensions=exts)
        clouds = [
            {
                "ID": x.get("PublishId"),
                "ID_PROJECT": x.get("ProjectId"),
                "PUBLISH_ID": x.get("PublishId"),
                "URL": f"/{x.get('Folder')}/procesado/3d_tiles/pointcloud/tileset.json",
                "Lon": str(x.get("Longitude")),
                "Lat": str(x.get("Latitude")),
                "Height": str(x.get("Height") or 0),
                "TYPE": 1,
            }
            for x in filtered
            if x.get("Origin") == "processed"
        ]
        return aspnet_d([clouds])

    if m_low == "loadorthophoto":
        exts = body.get("extensions")
        filtered = _filter_index_items(all_items, file_type_id=2, extensions=exts)
        orthos = [
            {
                "ID": x.get("PublishId"),
                "PROJECT_ID": x.get("ProjectId"),
                "PUBLISH_ID": x.get("PublishId"),
                "FOLDER": x.get("Folder"),
                "LATITUD": str(x.get("Latitude")),
                "LONGITUD": str(x.get("Longitude")),
            }
            for x in filtered
            if x.get("Origin") == "processed"
        ]
        return aspnet_d([orthos])

    if m_low == "loadpublicexternalassets":
        ft = body.get("type")
        exts = body.get("extensions")
        filtered = _filter_index_items(all_items, file_type_id=ft, extensions=exts)
        exts_list = [
            {
                "Id": x.get("ExternalAssetId"),
                "ProjectId": x.get("ProjectId"),
                "Title": x.get("Name"),
                "AssetType": x.get("AssetTypeKey"),
                "SourceFormat": x.get("SourceFormat"),
                "ViewerKind": x.get("ViewerKind"),
                "ViewerUrl": x.get("ViewerUrl"),
                "PreviewUrl": x.get("PreviewUrl"),
                "DetailUrl": x.get("DetailUrl"),
                "Latitude": x.get("Latitude"),
                "Longitude": x.get("Longitude"),
                "Altitude": x.get("Height") or 0,
                "Heading": x.get("Heading") or 0,
                "Scale": x.get("Scale") or 1,
                "HasEmbeddedGeoreference": x.get("HasEmbeddedGeoreference", True),
                "PriceCredits": x.get("PriceCredits") or 0,
            }
            for x in filtered
            if x.get("Origin") == "external"
        ]
        return aspnet_d([exts_list])

    if m_low == "getcategories":
        cats = [{"ID": cid, "NAME": info["name"], "SLUG": info["slug"]} for cid, info in sorted(DEFAULT_CATEGORIES.items())]
        return aspnet_d(cats)

    return aspnet_d([])


# ============================================================================
# WEBMETHODS: FUNCIONES.ASPX (NOTIFICATIONS & LANGUAGE)
# ============================================================================
@app.post("/funciones.aspx/{method_name}")
@app.post("/Funciones.aspx/{method_name}")
@app.post("/Functions/UserFunctions.aspx/{method_name}")
@app.post("/Home.Master/{method_name}")
async def webmethod_funciones(method_name: str, request: Request):
    user = get_current_user(request)
    store = get_store()
    try:
        body = await request.json() if request.headers.get("content-length") != "0" else {}
    except Exception:
        body = {}

    m = method_name.strip().lower()
    if m in ("loadnotifications", "getnotificaciones", "getnotifications"):
        if not user:
            return aspnet_d([])
        notis = store.get_user_notifications(int(user["ID"]))
        return aspnet_d(notis)

    if m == "setleido":
        if not user:
            return aspnet_d(False)
        nid = int(body.get("id") or 0)
        has_more = store.mark_notification_read(nid, user_id=int(user["ID"]))
        return aspnet_d(has_more)

    if m == "setleidoall":
        if user:
            store.mark_all_notifications_read(int(user["ID"]))
        return aspnet_d("OK")

    return aspnet_d("OK")


# ============================================================================
# WEBMETHODS: PROJECTS.ASPX, PROJECT.ASPX, PROFILE.ASPX, MYMAPS.ASPX
# ============================================================================
@app.post("/Client/Projects.aspx/{method_name}")
async def webmethod_projects(method_name: str, request: Request):
    user = get_current_user(request)
    if not user:
        return aspnet_d(0)
    store = get_store()
    uid = int(user["ID"])
    try:
        body = await request.json()
    except Exception:
        body = {}

    m = method_name.strip().lower()
    parent_pid = int(body.get("parent_project_id") or 0)
    if m == "btncreateproject":
        name = str(body.get("name") or "").strip()
        desc = str(body.get("description") or "").strip()
        if not name:
            return aspnet_d(0)
        pid = store.create_project(uid, name, kind="photogrammetry", description=desc, parent_project_id=parent_pid)
        return aspnet_d(pid)

    if m == "btncreateexternalproject":
        name = str(body.get("name") or "").strip()
        desc = str(body.get("description") or "").strip()
        if not name:
            return aspnet_d(0)
        pid = store.create_project(uid, name, kind="external", description=desc, parent_project_id=parent_pid)
        return aspnet_d(pid)

    if m == "btncreategaussianproject":
        name = str(body.get("name") or "").strip()
        desc = str(body.get("description") or "").strip()
        if not name:
            return aspnet_d(0)
        pid = store.create_project(uid, name, kind="gaussian", description=desc, parent_project_id=parent_pid)
        return aspnet_d(pid)

    if m == "delete":
        pid = int(body.get("project_id") or body.get("id") or 0)
        ok, msg = store.delete_user_project(uid, pid)
        return aspnet_d("OK" if ok else msg)

    return aspnet_d("OK")


@app.post("/obtenerProceso")
async def webmethod_root_obtener_proceso(request: Request):
    return await webmethod_project_detail("obtenerProceso", request)


@app.post("/btnProcess")
@app.post("/btnprocess")
async def webmethod_root_btn_process(request: Request):
    return await webmethod_project_detail("btnprocess", request)


@app.post("/btnCancelTask")
async def webmethod_root_cancel_task(request: Request):
    return await webmethod_project_detail("btnCancelTask", request)


@app.post("/loadFileProject")
async def webmethod_root_load_file_project(request: Request):
    return await webmethod_project_detail("loadFileProject", request)


@app.post("/api/v2/tasks/{project_id}/cancel")
async def api_v2_cancel_photogrammetry(project_id: int, request: Request):
    from starlette.concurrency import run_in_threadpool

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    store = get_store()
    project = store.get_project(project_id)
    if not project or int(project.get("CREATE_ID") or 0) != int(user["ID"]):
        raise HTTPException(status_code=404, detail="Task not found.")
    if await run_in_threadpool(store.get_job_queue().cancel_runpod_gaussian, project_id, int(user["ID"])):
        return JSONResponse({"ok": True, "status": "cancellation_requested"}, status_code=202)
    if await run_in_threadpool(store.cancel_pending_nodeodx, project_id, int(user["ID"])):
        return JSONResponse({"ok": True, "status": "cancellation_requested"}, status_code=202)
    if project.get("STATE_ID") not in (1, 2) or not str(project.get("ENGINE_NODEODM_URL") or "").startswith(("https://", "http://")):
        raise HTTPException(status_code=409, detail="The task cannot be canceled at its current stage.")
    try:
        await run_in_threadpool(store.request_node_cancellation, project)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail="The processing node did not confirm cancellation.") from exc
    return JSONResponse({"ok": True, "status": "cancellation_requested"}, status_code=202)


@app.get("/api/v2/tasks/{project_id}/live-log")
async def api_v2_task_live_log(project_id: int, request: Request):
    """Returns live OpenDroneMap stage, progress %, elapsed time, and last 15 console log lines."""
    from starlette.concurrency import run_in_threadpool

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    store = get_store()
    proj = store.get_project(project_id)
    if not proj or int(proj.get("CREATE_ID") or 0) != int(user["ID"]):
        return JSONResponse({"ok": False, "message": "Task not found"}, status_code=404)

    state_id = int(proj.get("STATE_ID") or 0)
    odm_uuid = str(proj.get("NODEODM_UUID") or "").strip()
    nodeodm_url = str(proj.get("ENGINE_NODEODM_URL") or "").rstrip("/")
    prog = int(proj.get("PROCESSING_PROGRESS") or (100 if state_id == 4 else 5))
    lines: List[str] = list(proj.get("LAST_ODM_LOG_LINES") or [])
    proc_ms = 0
    stage_name = "Preparing OpenDroneMap Engine..."

    if state_id in (1, 2):
        # Also check local native ODM console log if present
        local_log = store.data_root / "odm_jobs" / f"task_{project_id}" / "odm_console.log"
        if local_log.is_file():
            try:
                raw_lines = local_log.read_text(encoding="utf-8", errors="ignore").splitlines()
                lines = [ln.strip() for ln in raw_lines[-15:] if ln.strip()]
            except Exception:
                pass
        # Remote logs are cached by the worker. Browser requests must never
        # perform engine polling, archive downloads or result ingestion.
        proc_ms = int(proj.get("ENGINE_PROCESSING_TIME_MS") or 0)

    stage_name = str(proj.get("PROCESSING_STAGE") or "Waiting for engine status")
    stage_labels = {
        "opensfm": "Reconstructing camera positions and sparse geometry",
        "openmvs": "Reconstructing the dense point cloud",
        "odm_filterpoints": "Filtering the reconstructed point cloud",
        "odm_meshing": "Building the surface mesh",
        "odm_texturing": "Texturing the surface mesh",
        "odm_georeferencing": "Georeferencing the reconstruction",
        "odm_dem": "Generating elevation products",
        "odm_orthophoto": "Generating the orthophoto",
        "odm_report": "Generating the processing report",
        "odm_postprocess": "Generating viewing products",
    }
    for line in reversed(lines):
        match = re.search(r"Running ([a-z_]+) stage", str(line))
        if match:
            stage_name = stage_labels.get(match[1], "Engine stage: " + match[1])
            break
        if "PoissonRecon" in str(line):
            stage_name = "Building the surface mesh"
            break

    if state_id == 4:
        stage_name = "Completed (100%)"
        prog = 100

    return JSONResponse({
        "ok": True,
        "project_id": project_id,
        "state_id": state_id,
        "progress": prog,
        "stage": stage_name,
        "processingTimeMs": proc_ms,
        "lines": lines,
    })


@app.post("/Client/Project.aspx/{method_name}")
async def webmethod_project_detail(method_name: str, request: Request):
    user = get_current_user(request)
    if not user:
        return aspnet_d("ERROR")
    store = get_store()
    uid = int(user["ID"])
    try:
        body = await request.json()
    except Exception:
        body = {}

    m = method_name.strip().lower()
    protected = {"obtenerproceso", "getsourceuploadstatus", "btnprocess", "btncanceltask"}
    if m in protected or (m == "filterprojectlist" and ("project_id" in body or "orderBy" in body)):
        pid = int(body.get("project_id") or body.get("projectId") or 0)
        project = store.get_project(pid)
        if not project or int(project.get("CREATE_ID") or 0) != uid:
            raise HTTPException(status_code=404, detail="Task not found.")
    if m == "obtenerproceso":
        pid = int(body.get("project_id") or 0)
        store.refresh_processing_state()
        project = store.get_project(pid) or {}
        state = int(project.get("STATE_ID") or 0)
        progress = 100 if state == 4 else int(project.get("PROCESSING_PROGRESS") or 0)
        return aspnet_d(f"{progress}/{state}/{1 if project.get('ZIP_UPLOADED') else 0}")

    if m == "getsourceuploadstatus":
        pid = int(body.get("project_id") or body.get("projectId") or 0)
        files = store.get_project_files(pid)
        return JSONResponse({
            "d": {
                "Success": True,
                "ExistingFileCount": len(files),
                "MaxFilesPerProject": 10000,
                "MaxFileBytes": 536870912,
                "MaxBatchFiles": 5,
                "MaxBatchBytes": 67108864,
                "MaxConcurrentBatches": 3,
                "MaxAttempts": 4,
                "Files": [
                    {"Name": str(f.get("FILENAME") or ""), "Size": int(float(f.get("SIZE") or 0))}
                    for f in files
                ],
            }
        })

    if m in ("deletefile", "btndeletefile"):
        fid = int(body.get("id") or body.get("project_file_id") or body.get("id_project_file") or 0)
        pid = int(body.get("project_id") or 0)
        res = store.delete_project_file(pid, fid, uid)
        return aspnet_d(res)

    if m in ("btncreatefileproject", "deletegcp"):
        import binascii
        import math

        pid = int(body.get("project_id") or body.get("project_file_id") or 0)
        with store.lock:
            project = next((row for row in store.tables.get("dbo.WEB_PROJECTS", [])
                            if int(row.get("ID") or 0) == pid and row.get("ACTIVO", True)), None)
            if not project or int(project.get("CREATE_ID") or 0) != uid:
                raise HTTPException(status_code=404, detail="Task not found.")
            if int(project.get("STATE_ID") or 0) in (1, 2):
                raise HTTPException(status_code=409, detail="Wait until the current task finishes.")
            if m == "deletegcp":
                project.pop("PHOTOGRAMMETRY_CONTROL", None)
                store.persist_table("dbo.WEB_PROJECTS")
                return aspnet_d("OK")
            filename = re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(body.get("filename") or "")).name)
            encoded = str(body.get("filebase64") or "")
            if not filename.lower().endswith(".txt") or not encoded or len(encoded) > 2 * 1024 * 1024:
                raise HTTPException(status_code=422, detail="Upload a supported .txt control file (up to 1.5 MiB).")
            geo = filename.lower() == "geo.txt"
            try:
                decoded = base64.b64decode(encoded, validate=True).decode("utf-8-sig")
                if "\x00" in decoded:
                    raise ValueError
                lines = [line.strip() for line in decoded.splitlines() if line.strip() and not line.startswith("#")]
                rows = [line.split() for line in lines[1:]]
                if len(rows) < 3:
                    raise ValueError
                for row in rows:
                    values = row[1:4] if geo else row[:5]
                    if len(row) < (4 if geo else 6) or not all(math.isfinite(float(value)) for value in values):
                        raise ValueError
            except (binascii.Error, ValueError, UnicodeError):
                raise HTTPException(status_code=422, detail="Invalid GCP/GEO text. Include a CRS header and valid observations.") from None
            data = ("\n".join(lines) + "\n").encode("utf-8")
            sha = hashlib.sha256(data).hexdigest()
            control_name = "geo.txt" if geo else "gcp_list.txt"
            rel = f"{project['FOLDER']}/control/{sha}/{control_name}"
            get_blob_store().save_bytes("imagenes", rel, data)
            project["PHOTOGRAMMETRY_CONTROL"] = {"name": filename, "path": rel, "sha256": sha, "kind": "geo" if geo else "gcp"}
            store.persist_table("dbo.WEB_PROJECTS")
        return aspnet_d(f"{pid}/{filename}")

    if m == "deletemyearthcontainer":
        kind = str(body.get("kind") or "project").strip().lower()
        cid = int(body.get("id") or body.get("containerId") or 0)
        res = store.delete_myearth_container(uid, kind, cid)
        return aspnet_d(res)

    if m == "deletemyearthasset":
        pid = int(body.get("projectId") or body.get("project_id") or 0)
        ft = int(body.get("fileTypeId") or body.get("file_type_id") or 0)
        ext_aid = int(body.get("externalAssetId") or body.get("external_asset_id") or 0)
        res = store.delete_myearth_asset(uid, pid, ft, ext_aid)
        return aspnet_d(res)

    if m == "createprojectcontainer":
        name = str(body.get("name") or "").strip()
        desc = str(body.get("description") or "").strip()
        if not name:
            return aspnet_d({"ok": False, "message": "Enter a project name."})
        new_pid = store.create_project(uid, name, kind="processing", description=desc, is_project_container=True)
        return aspnet_d({"ok": True, "id": new_pid})

    if m == "createsurveycontainer":
        parent_id = int(body.get("projectId") or body.get("parentProjectId") or body.get("parent_project_id") or 0)
        name = str(body.get("name") or "").strip()
        desc = str(body.get("description") or "").strip()
        if not name:
            return aspnet_d({"ok": False, "message": "Enter a survey name."})
        new_sid = store.create_project(
            uid,
            name,
            kind="processing",
            description=desc,
            parent_project_id=parent_id,
            is_survey_container=True,
        )
        return aspnet_d({"ok": True, "id": new_sid})

    if m in ("saveassetquick", "updatesubprojectassetquick"):
        pid = int(body.get("project_id") or body.get("projectId") or 0)
        ft = int(body.get("file_type_id") or body.get("fileTypeId") or 2)
        ext_aid = int(body.get("external_asset_id") or body.get("externalAssetId") or 0)
        custom_title = str(body.get("title") or "").strip()
        vis_mode = str(body.get("visibility_mode") or body.get("visibilityMode") or "private").strip()
        price_eur = int(float(str(body.get("price_eur") or body.get("priceEur") or 0).replace(",", ".")))
        lat_v = float(body["lat"]) if body.get("lat") not in (None, "") else None
        lon_v = float(body["lon"]) if body.get("lon") not in (None, "") else None
        h_v = float(body["height"]) if body.get("height") not in (None, "") else None
        c_date = str(body.get("creationDate") or body.get("creation_date") or "").strip()
        method_str = str(body.get("methodology") or "").strip()
        desc_str = str(body.get("description") or "").strip() if "description" in body else ""
        cat_val = int(body.get("categoryId") or body.get("category_id") or 0) if str(body.get("categoryId") or body.get("category_id") or "0").lstrip("-").isdigit() else None
        tags_val = str(body.get("tags") or "").strip() if "tags" in body else None
        res = store.update_subproject_asset_quick(
            uid,
            pid,
            ft,
            custom_title,
            vis_mode,
            price_eur,
            lat_v,
            lon_v,
            h_v,
            external_asset_id=ext_aid,
            creation_date=c_date,
            methodology=method_str,
            description=desc_str,
            category_id=cat_val,
            tags=tags_val,
        )
        return aspnet_d(res)

    if m in ("createsubproject", "createsurveytask"):
        survey_id = int(body.get("surveyId") or body.get("survey_id") or body.get("parentProjectId") or body.get("parent_project_id") or body.get("project_id") or 0)
        sub_name = str(body.get("name") or "").strip()
        sub_kind = str(body.get("projectKind") or body.get("kind") or "processing").strip().lower()
        c_date = str(body.get("creationDate") or body.get("creation_date") or "").strip()
        method_str = str(body.get("methodology") or "").strip()
        sub_desc = str(body.get("description") or "").strip()
        if not sub_name:
            return aspnet_d({"ok": False, "message": "Enter a name."})
        srv_row = store.get_project(survey_id) if survey_id > 0 else None
        root_pid = int((srv_row.get("PARENT_PROJECT_ID") if srv_row else 0) or survey_id)
        new_pid = store.create_project(
            uid,
            sub_name,
            kind=sub_kind,
            description=sub_desc,
            parent_project_id=root_pid,
            survey_id=survey_id,
            creation_date=c_date,
            methodology=method_str,
            task_description=sub_desc,
        )
        if sub_kind == "external":
            url = f"/Client/ExternalAssetImport.aspx?project_id={new_pid}"
        elif sub_kind == "gaussian":
            url = f"/Client/GaussianProject.aspx?id={new_pid}"
        else:
            url = f"/Client/Project.aspx?id={new_pid}"
        return aspnet_d({"ok": True, "id": new_pid, "url": url, "redirectUrl": url})

    if m == "requestpublish":
        pid = int(body.get("project_id") or 0)
        credits_val = int(body.get("credits") or body.get("price_orthophoto") or 0)
        pub_type = int(body.get("publish_type") or (0 if credits_val > 0 else 1))
        raw_types = str(body.get("asset_types") or "")
        if raw_types:
            atypes = [int(t) for t in raw_types.split(",") if t.strip().isdigit()]
        else:
            atypes = []
            if body.get("orthophoto"):
                atypes.append(2)
            if body.get("model"):
                atypes.append(3)
            if body.get("pointcloud"):
                atypes.append(1)
        cap_from = str(body.get("capture_from") or "")
        cap_to = str(body.get("capture_to") or "")
        cap_prec = str(body.get("capture_precision") or "unknown")
        pub_list = store.publish_project_assets(
            pid,
            uid,
            credits_val,
            pub_type,
            atypes or [2, 3, 1],
            capture_from=cap_from,
            capture_to=cap_to,
            capture_precision=cap_prec,
        )
        return aspnet_d(json.dumps({"publishedTypes": [str(x) for x in pub_list]}))

    if m == "btnprocess":
        try:
            pid = int(body.get("project_id") or 0)
            if int(project.get("STATE_ID") or 0) in (1, 2):
                return aspnet_d("OK")
            target_mode = str(body.get("target_mode") or "auto").strip().lower()
            preset = str(body.get("processing_preset") or "").strip().lower()
            if preset and preset not in {"fast_ortho", "complete"}:
                return aspnet_d("ERROR:Unknown photogrammetry preset.")
            with store.lock:
                for p in store.tables.get("dbo.WEB_PROJECTS", []):
                    if int(p.get("ID") or 0) == pid:
                        p["ENABLE_DTM"] = preset != "fast_ortho"
                        if preset:
                            p["ENABLE_DSM"] = preset == "complete"
                            p["PROCESSING_PRESET"] = preset
                        store.persist_table("dbo.WEB_PROJECTS")
                        break
            files = store.get_project_files(pid)
            if not files:
                return aspnet_d("nofiles")

            route = store.resolve_processing_backend(uid, target_mode=target_mode, probe_local_live=True)
            if not route.get("ok"):
                return aspnet_d("ERROR:" + str(route.get("error") or "Processing backend is unavailable."))

            is_desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_MODE", "") == "1" or os.environ.get("MOSAICEARTH_DESKTOP_LOCAL", "") == "1"
            is_billable_cloud = bool(route.get("is_billable_cloud"))

            if is_billable_cloud:
                if is_desktop_local:
                    cloud_acc = store.get_desktop_cloud_account()
                    if not cloud_acc.get("logged_in"):
                        return aspnet_d("ERROR:Please sign in to your MosaicEarth Cloud Account in MyEarth first to process on MosaicEarth Cloud NodeODM.")
                else:
                    total_mb = sum(float(f.get("SIZE") or 0) for f in files) / (1024.0 * 1024.0)
                    auth_res = store.authorize_and_charge_task_upfront(
                        uid,
                        project_id=pid,
                        num_images=len(files),
                        task_kind="photogrammetry",
                        total_megabytes=total_mb,
                        target_mode=target_mode,
                    )
                    if not auth_res.get("ok"):
                        return aspnet_d("nocreditsuser")
                    if auth_res.get("requires_stripe"):
                        s_res = await _create_stripe_checkout_session(
                            user=user or {"ID": uid},
                            mode="payment",
                            amount_eur=float(auth_res.get("amount_eur") or 2.0),
                            product_name=f"MosaicEarth Cloud GPU Photogrammetry (Task #{pid})",
                            product_description=f"{len(files)} drone images · Payment reviewed if the task fails",
                            cancel_path=f"/Client/Project.aspx?id={pid}&upload=1",
                            metadata={
                                "mosaicearth_kind": "processing_task",
                                "mosaicearth_user_id": str(uid),
                                "mosaicearth_project_id": str(pid),
                                "mosaicearth_num_images": str(len(files)),
                                "mosaicearth_task_kind": "photogrammetry",
                                "mosaicearth_target_mode": target_mode,
                            },
                        )
                        if s_res.get("ok") and s_res.get("checkoutUrl"):
                            return aspnet_d("STRIPE_URL:" + str(s_res["checkoutUrl"]))
                        return aspnet_d("ERROR:" + str(s_res.get("message") or "Could not open Stripe Checkout"))

            sub_res = store.submit_photogrammetry_to_nodeodm(pid, uid, target_mode=target_mode)
            if not sub_res.get("ok"):
                if is_billable_cloud and not is_desktop_local:
                    store.refund_task_on_failure(pid, reason=str(sub_res.get("message") or "NodeODM submission failed"))
                return aspnet_d("ERROR:" + str(sub_res.get("message") or "NodeODM engine error"))
            return aspnet_d("OK")
        except Exception as exc:
            return aspnet_d(f"ERROR:{exc}")

    if m == "btncanceltask":
        pid = int(body.get("project_id") or 0)
        from starlette.concurrency import run_in_threadpool

        if await run_in_threadpool(store.cancel_pending_nodeodx, pid, uid):
            return aspnet_d("OK")

        if int(project.get("STATE_ID") or 0) not in (1, 2):
            return aspnet_d("ERROR:Task is not running.")
        endpoint = str(project.get("ENGINE_NODEODM_URL") or "")
        if endpoint.startswith(("https://", "http://")):
            try:
                await run_in_threadpool(store.request_node_cancellation, project)
            except RuntimeError:
                return aspnet_d("ERROR:The processing node did not confirm cancellation.")
            return aspnet_d("OK")
        pod_id = ""
        lease_id = ""
        with store.lock:
            for p in store.tables.get("dbo.WEB_PROJECTS", []):
                if int(p.get("ID") or 0) == pid:
                    p["STATE_ID"] = 3
                    pod_id = str(p.get("RUNPOD_POD_ID") or "")
                    lease_id = str(p.get("RUNPOD_LEASE_ID") or "")
                    store.persist_table("dbo.WEB_PROJECTS")
                    break
        if pod_id or lease_id:
            store.get_runpod_lease_manager().release_lease(task_id=pid, lease_id=lease_id, status="FAILED", error="Cancelled by user")
            store.terminate_runpod_nodeodm_pod_if_idle(pod_id=pod_id, finished_project_id=pid)
        store.refund_task_on_failure(pid, reason="Task cancelled by user before completion")
        return aspnet_d("OK")

    if m == "upgradeprojecttopremium":
        pid = int(body.get("project") or 0)
        res = store.upgrade_project_to_premium(uid, pid)
        return aspnet_d(res)

    if m == "filterprojectlist":
        if "project_id" in body or "orderBy" in body:
            pid = int(body.get("project_id") or 0)
            ord_by = int(body.get("orderBy") or 0)
            return aspnet_d(store.get_project_files(pid, ord_by))
        elig = store.get_eligible_premium_projects(uid)
        return aspnet_d([{"ID": e["ID"], "NAME": e["NAME"]} for e in elig])

    return aspnet_d("OK")


@app.post("/Client/Profile.aspx/{method_name}")
async def webmethod_profile(method_name: str, request: Request):
    user = get_current_user(request)
    if not user:
        return aspnet_d("AUTH_REQUIRED")
    store = get_store()
    uid = int(user["ID"])
    try:
        body = await request.json()
    except Exception:
        body = {}

    m = method_name.strip().lower()
    if m == "guardardatosusuario":
        uname = str(body.get("username") or "").strip()
        res = store.update_user_profile(uid, uname)
        return aspnet_d(res)

    if m == "actualizarcontrasena":
        old_p = str(body.get("oldPassword") or "")
        new_p = str(body.get("newPassword") or "")
        rep_p = str(body.get("repeatPassword") or "")
        if not old_p or not new_p or not rep_p:
            return aspnet_d("EMPTY")
        if new_p != rep_p:
            return aspnet_d("DIFFERENT")
        ok, msg = store.update_user_password(uid, old_p, new_p)
        return aspnet_d("OK" if ok else msg)

    if m == "requestemailchange":
        new_em = str(body.get("newEmail") or body.get("email") or "").strip()
        cur_pwd = str(body.get("currentPassword") or body.get("password") or "")
        ok, msg, token = store.request_email_change_token(uid, new_em, cur_pwd)
        if not ok:
            return aspnet_d({"ok": False, "message": msg})
        verify_url = f"{get_public_site_url()}/VerifyEmailChange.aspx?token={quote(token, safe='')}"
        get_email_service().send_email_change_verification(new_em, str(user.get("USERNAME") or user.get("NAME") or ""), verify_url)
        return aspnet_d({"ok": True, "message": f"Confirmation email sent to {new_em}."})

    if m == "saveuserodmsettings":
        mode = str(body.get("mode") or "managed")
        endpoint = str(body.get("endpointUrl") or "")
        token = str(body.get("token") or "")
        test_conn = bool(body.get("testConnection"))
        res = store.save_user_odm_settings(uid, mode, endpoint, token, test_connection=test_conn)
        return aspnet_d(res)

    if m == "clearuserodmsettings":
        res = store.clear_user_odm_settings(uid)
        return aspnet_d(res)

    if m == "testmanagedodm":
        chk = store.check_nodeodm_engine()
        return aspnet_d({
            "ok": True,
            "message": f"ONLINE — External Cloud Photogrammetry ({chk.get('url')} · Engine v{chk.get('engineVersion', '4.4.1')}) & RunPod Serverless GPU (lxcyqllatkyw1s)",
            "status": "Using MosaicEarth External Cloud Processing",
        })

    if m == "anadirfiscaldata":
        res = store.add_fiscal_data(
            uid,
            str(body.get("fullName") or ""),
            str(body.get("nif") or ""),
            str(body.get("country") or ""),
            str(body.get("region") or ""),
            str(body.get("city") or ""),
            str(body.get("postal") or ""),
            str(body.get("address") or ""),
            str(body.get("phone") or ""),
        )
        return aspnet_d(res)

    if m == "editarfiscaldata":
        res = store.edit_fiscal_data(
            uid,
            int(body.get("id") or 0),
            str(body.get("fullName") or ""),
            str(body.get("nif") or ""),
            str(body.get("country") or ""),
            str(body.get("region") or ""),
            str(body.get("city") or ""),
            str(body.get("postal") or ""),
            str(body.get("address") or ""),
            str(body.get("phone") or ""),
        )
        return aspnet_d(res)

    if m == "borrarfiscaldata":
        res = store.delete_fiscal_data(uid, int(body.get("id") or 0))
        return aspnet_d(res)

    if m == "actualizardefault":
        res = store.set_default_fiscal_data(uid, int(body.get("id") or 0))
        return aspnet_d(res)

    if m == "upgradeprojecttopremium":
        res = store.upgrade_project_to_premium(uid, int(body.get("project") or 0))
        return aspnet_d(res)

    if m == "loadinfopanelpremium":
        msg = store.get_premium_project_info_message(uid, int(body.get("project") or 0))
        return aspnet_d(msg)

    if m == "cancelprojectpremium":
        res = store.cancel_project_premium(uid, int(body.get("project") or 0))
        return aspnet_d(res)

    return aspnet_d("OK")


@app.post("/Client/MyMaps.aspx/delete")
async def webmethod_mymaps_delete(request: Request):
    user = get_current_user(request)
    if not user:
        return aspnet_d("ERROR")
    try:
        body = await request.json()
    except Exception:
        body = {}
    mid = int(body.get("mymap_id") or body.get("id") or 0)
    ok = get_store().delete_mymap(int(user["ID"]), mid)
    return aspnet_d("OK" if ok else "ERROR")


@app.post("/Client/MyMapDetail.aspx/{method_name}")
async def webmethod_mymap_detail(method_name: str, request: Request):
    user = get_current_user(request)
    if not user:
        return aspnet_d([])
    store = get_store()
    uid = int(user["ID"])
    try:
        body = await request.json()
    except Exception:
        body = {}

    m = method_name.strip().lower()
    if m == "loaditems":
        search_val = str(body.get("search") or "")
        items = store.get_mymap_editor_items(uid, search=search_val)
        return aspnet_d(items)

    if m == "loaditemsselected":
        mid = int(float(str(body.get("map_id") or body.get("id") or 0)))
        sel = store.get_mymap_selected_items(uid, mid)
        return aspnet_d(sel)

    if m == "savemap":
        mid = int(float(str(body.get("map_id") or body.get("id") or 0)))
        name = str(body.get("name") or "").strip()
        vis = str(body.get("visibility") or "").strip().lower() or None
        raw_vals = body.get("selectedValues") or body.get("values") or []
        if isinstance(raw_vals, str):
            raw_vals = [raw_vals]
        try:
            res = store.save_mymap_composition(uid, mid, name, [str(v) for v in raw_vals], visibility=vis)
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return aspnet_d(str(res))

    return aspnet_d("OK")


@app.post("/MyMap.aspx/{method_name}")
@app.post("/{lang}/MyMaps/{slug}/{method_name}")
async def webmethod_mymap_viewer(method_name: str, request: Request, lang: str = "en", slug: str = ""):
    user = get_current_user(request)
    uid = int(user["ID"]) if user else 0
    store = get_store()
    try:
        body = await request.json()
    except Exception:
        body = {}

    mid = int(float(str(body.get("MYMAP_ID") or body.get("map_id") or body.get("id") or 0)))
    map_owner_id = 0
    for mr in store.tables.get("dbo.WEB_MYMAPS", []):
        if int(mr.get("ID") or 0) == mid:
            map_owner_id = int(mr.get("USER_ID") or mr.get("CREATE_ID") or 0)
            break
    effective_uid = uid if (uid > 0 and uid == map_owner_id) else (map_owner_id or uid)
    is_owner = (uid > 0 and uid == map_owner_id)

    m = method_name.strip().lower()
    if m == "loaditems":
        ft = int(float(str(body.get("type_id") or body.get("type") or 0)))
        rows = store.get_mymap_viewer_items(mid, effective_uid, ft)
        if not is_owner:
            for r in rows:
                if not bool(r.get("IsPublic", True)) or str(r.get("Visibility") or "").lower() == "private":
                    r["IsPrivateViewOnly"] = True
                    r["DownloadUrl"] = ""
                    r["CanDownload"] = False
        return aspnet_d(rows)

    if m == "loadexternalassets":
        ft = int(float(str(body.get("type_id") or body.get("type") or 0)))
        rows = store.get_mymap_external_assets(mid, effective_uid)
        if ft > 0:
            fam_target = "pointcloud" if ft == 1 else "orthophoto" if ft == 2 else "model3d"
            rows = [r for r in rows if str(r.get("AssetType") or "") == fam_target or (ft == 3 and str(r.get("AssetType") or "") == "gaussian")]
        if not is_owner:
            for r in rows:
                if not bool(r.get("IsPublic", True)):
                    r["IsPrivateViewOnly"] = True
                    r["DownloadUrl"] = ""
        return aspnet_d(rows)

    if m == "loadorthophoto":
        rows = store.get_mymap_orthophotos(mid, effective_uid)
        return aspnet_d(rows)

    if m == "loadpointcloud":
        rows = store.get_mymap_models(mid, effective_uid, 1)
        return aspnet_d(rows)

    if m == "load3dmodels":
        rows = store.get_mymap_models(mid, effective_uid, 3)
        return aspnet_d(rows)

    if m == "getcategories":
        cats = [{"ID": cid, "NAME": info["name"], "SLUG": info["slug"]} for cid, info in sorted(DEFAULT_CATEGORIES.items())]
        return aspnet_d(cats)

    return aspnet_d([])


@app.post("/MapEdit.aspx/saveData")
async def webmethod_mapedit_savedata(request: Request):
    user = get_current_user(request)
    if not user:
        return aspnet_d("ERROR")
    try:
        body = await request.json()
    except Exception:
        body = {}
    pid = int(body.get("id") or 0)
    ftype = int(body.get("type") or 3)
    store = get_store()
    proj = store.get_project(pid)
    is_admin = int(user.get("ROL_ID") or 0) == 1 or bool(user.get("IsAdmin"))
    if not proj or (not is_admin and int(proj.get("CREATE_ID") or 0) != int(user.get("ID") or 0)):
        return JSONResponse({"d": "FORBIDDEN"}, status_code=403)
    height = float(str(body.get("height") or "0").replace(",", "."))
    lat_raw = body.get("lat")
    lon_raw = body.get("lon")
    lat_val = float(str(lat_raw).replace(",", ".")) if lat_raw not in (None, "") else None
    lon_val = float(str(lon_raw).replace(",", ".")) if lon_raw not in (None, "") else None
    heading = float(str(body.get("heading") or "0").replace(",", "."))
    pitch = float(str(body.get("pitch") or "0").replace(",", "."))
    roll = float(str(body.get("roll") or "0").replace(",", "."))
    store.update_processed_asset_placement_3d(
        pid, ftype, height, lat=lat_val, lon=lon_val, heading=heading, pitch=pitch, roll=roll
    )
    return aspnet_d("OK")


@app.post("/api/v2/external-asset/save-placement")
async def route_save_external_asset_placement(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "message": "Please sign in to save 3D placement."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pid = int(body.get("project_id") or body.get("projectId") or 0)
    aid = int(body.get("asset_id") or body.get("assetId") or 0)
    store = get_store()
    ext_row = (store.get_external_asset(pid, aid) if pid > 0 else store.get_external_asset_by_id(aid)) if aid > 0 else None
    proj = store.get_project(pid or int((ext_row or {}).get("PROJECT_ID") or 0))
    uid = int(user.get("ID") or 0)
    is_admin = int(user.get("ROL_ID") or 0) == 1 or bool(user.get("IsAdmin"))
    owner_uid = int((ext_row or {}).get("USER_ID") or (proj or {}).get("CREATE_ID") or 0)
    if not is_admin and owner_uid != uid:
        return JSONResponse({"ok": False, "message": "Only the asset creator or an administrator can modify 3D placement."}, status_code=403)
    lat = float(str(body.get("lat") or 41.8205).replace(",", "."))
    lon = float(str(body.get("lon") or 1.8676).replace(",", "."))
    alt = float(str(body.get("alt") or body.get("altitude") or 0.0).replace(",", "."))
    heading = float(str(body.get("heading") or 0.0).replace(",", "."))
    pitch = float(str(body.get("pitch") or 0.0).replace(",", "."))
    roll = float(str(body.get("roll") or 0.0).replace(",", "."))
    scale = float(str(body.get("scale") or 1.0).replace(",", "."))
    ok = store.update_external_asset_placement_3d(
        pid, aid, uid, lat, lon, alt, heading=heading, pitch=pitch, roll=roll, scale=scale
    )
    return JSONResponse({
        "ok": ok,
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "heading": heading,
        "pitch": pitch,
        "roll": roll,
        "scale": scale,
    })


@app.post("/Client/ProductDetail.aspx/PurchaseNow")
async def webmethod_purchase_now(request: Request):
    user = get_current_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pub_id = int(body.get("publishId") or 0)
    store = get_store()
    pub_row = None
    for p in store.tables.get("dbo.WEB_PUBLICATIONS", []):
        if int(p.get("ID") or 0) == pub_id:
            pub_row = p
            break
    if not pub_row:
        return aspnet_d({"Error": True, "Message": "MosaicAsset not found."})
    price_eur = float(pub_row.get("PRICE_EUR") if pub_row.get("PRICE_EUR") is not None else (pub_row.get("CREDITS") or 0))
    if price_eur > 0 and not user:
        return aspnet_d({"Error": True, "Message": "Sign in to acquire this MosaicAsset in €."})
    if user:
        res = store.purchase_marketplace_asset_direct_eur(int(user["ID"]), publish_id=pub_id, external_asset_id=0)
        if not res.get("ok"):
            return aspnet_d({"Error": True, "Message": str(res.get("message") or "Could not complete purchase.")})
        if res.get("requires_stripe"):
            s_res = await _create_stripe_checkout_session(
                user=user,
                mode="payment",
                amount_eur=float(res.get("amount_eur") or res.get("price_eur") or price_eur),
                product_name=f"MosaicAsset Commercial License: {res.get('title', 'Geospatial Dataset')}",
                product_description="Commercial Geospatial License & High-Resolution Source Download (80%–90% Direct Creator Revenue)",
                cancel_path=f"/Client/ProductDetail.aspx?id={pub_id}",
                metadata={
                    "mosaicearth_kind": "marketplace_asset",
                    "mosaicearth_user_id": str(user["ID"]),
                    "mosaicearth_publish_id": str(pub_id),
                    "mosaicearth_external_asset_id": "0",
                },
            )
            if not s_res.get("ok"):
                return aspnet_d({"Error": True, "Message": str(s_res.get("message") or "Could not open Stripe Checkout.")})
            return aspnet_d({"Error": False, "RequiresStripe": True, "CheckoutUrl": str(s_res["checkoutUrl"]), "Message": ""})
    return aspnet_d({"Error": False, "Message": ""})


# ============================================================================
# UPLOAD & WORKER COMPLETION EMAIL HANDLERS
# ============================================================================
@app.post("/api/desktop/open-folder", dependencies=[Depends(_require_desktop_local)])
async def api_desktop_open_folder():
    data_dir = os.environ.get("MOSAICEARTH_DATA_DIR", "/data")
    try:
        if os.name == "nt":
            os.startfile(data_dir)
    except Exception:
        pass
    return JSONResponse({"ok": True, "path": data_dir})


@app.post("/ProjectImageUpload.ashx")
@app.post("/Client/Handler.ashx")
@app.post("/Handler.ashx")
async def handler_project_image_upload(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"Success": False, "Retriable": False, "Message": "Your session has expired. Sign in again before uploading images."},
            status_code=401,
        )
    form = await request.form()
    pid_raw = form.get("project_id") or request.query_params.get("project_id") or "0"
    try:
        project_id = int(str(pid_raw))
    except Exception:
        project_id = 0
    store = get_store()
    proj = store.get_project(project_id)
    if not proj:
        return JSONResponse({"Success": False, "Retriable": False, "Message": "The project no longer exists."}, status_code=404)
    if int(proj.get("CREATE_ID") or 0) != int(user["ID"]):
        return JSONResponse({"Success": False, "Retriable": False, "Message": "This project does not belong to the signed-in user."}, status_code=403)

    folder = str(proj.get("FOLDER") or to_slug(f"{project_id}-{proj.get('NAME', 'project')}"))
    blob_store = get_blob_store()
    accepted = []
    skipped = []
    failed = []

    for key, val in form.multi_items():
        if not hasattr(val, "filename") or not val.filename:
            continue
        orig_name = Path(val.filename).name
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", orig_name)
        if not safe_name.lower().endswith((".jpg", ".jpeg", ".png")):
            failed.append({"OriginalName": orig_name, "Status": "failed", "Message": "Only JPG, JPEG and PNG source images are accepted."})
            continue
        blob_rel = f"{folder}/{safe_name}"
        _, file_size, _, data = await blob_store.save_upload_stream(
            "imagenes", blob_rel, val, header_capture_bytes=131072, keep_local_copy=False
        )
        if file_size <= 0:
            blob_store.delete("imagenes", blob_rel)
            failed.append({"OriginalName": orig_name, "Status": "failed", "Message": "The source image is empty."})
            continue
        from .photo_metadata import source_photo_metadata
        photo_meta = source_photo_metadata(data)
        store.add_project_file(
            project_id=project_id,
            user_id=int(user["ID"]),
            filename=safe_name,
            size_bytes=file_size,
            content_type=getattr(val, "content_type", "image/jpeg") or "image/jpeg",
            **photo_meta,
        )
        accepted.append({"OriginalName": orig_name, "StoredName": safe_name, "Status": "uploaded"})

    return JSONResponse({
        "Success": len(failed) == 0,
        "Retriable": len(failed) > 0,
        "Message": "Batch stored successfully." if not failed else "Some images in this batch need to be retried.",
        "Accepted": accepted,
        "Skipped": skipped,
        "Failed": failed,
    })


def _asset_upload(request, project_id, upload_id):
    from .asset_uploads import AssetUpload

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to upload files.")
    return AssetUpload(get_store(), get_blob_store(), project_id, int(user["ID"]), upload_id)


@app.get("/api/v2/tasks/{project_id}/asset-uploads/quote")
def asset_upload_quote(request: Request, project_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to upload files.")
    store = get_store()
    project = store.get_project(project_id)
    if not project or int(project.get("CREATE_ID") or 0) != int(user["ID"]):
        raise HTTPException(status_code=404, detail="Task not found.")
    if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
        import shutil
        available = bool(shutil.which("colmap") and shutil.which(os.environ.get("MOSAICEARTH_OPENSPLAT_BIN", "opensplat")))
        return {"price_eur": 0, "covered_by_plan": False, "local": True,
                "label": "Local processing · 0.00 € · no cloud credits", "engine_available": available}
    quote = store.calculate_task_processing_quote(int(user["ID"]), task_kind="gaussian")
    return {"price_eur": quote["total_price_eur"], "covered_by_plan": quote["covered_by_plan"],
            "label": quote["summary_label"], "engine_available": os.environ.get("MOSAICEARTH_GAUSSIAN_ENGINE") == "runpod-opensplat"}


@app.put("/api/v2/tasks/{project_id}/asset-uploads/{upload_id}")
def start_asset_upload(request: Request, project_id: int, upload_id: str, payload: dict):
    from .asset_uploads import CHUNK_BYTES, GAUSSIAN_MODELS, UploadError, normalize_files
    try:
        upload = _asset_upload(request, project_id, upload_id)
        project = upload.require_owner()
        origin = "gaussian" if upload.store.get_gaussian_project(project_id) else "imported"
        if payload.get("origin") != origin:
            raise UploadError("This upload does not match the task type.", 409)
        files = normalize_files(payload.get("files"), origin)
        if origin == "gaussian" and Path(files[0]["name"]).suffix.lower() not in GAUSSIAN_MODELS:
            if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
                import shutil
                if not (shutil.which("colmap") and shutil.which(os.environ.get("MOSAICEARTH_OPENSPLAT_BIN", "opensplat"))):
                    raise UploadError("Local COLMAP and OpenSplat need installation before processing.", 503)
            elif os.environ.get("MOSAICEARTH_GAUSSIAN_ENGINE") != "runpod-opensplat":
                raise UploadError("Cloud Gaussian training is unavailable on this server.", 503)
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            raise UploadError("Invalid upload details.")
        safe_details = {k: str(details.get(k) or "").strip()[:2000]
                        for k in ("title", "description", "capture_date", "capture_method")}
        if origin == "gaussian":
            preset = str(details.get("quality_preset") or "STANDARD").upper()
            if preset not in {"FAST", "STANDARD", "HIGH_QUALITY"}:
                raise UploadError("Choose a supported Gaussian quality preset.")
            safe_details["quality_preset"] = preset
        for key, limit in (("lat", 90), ("lon", 180)):
            if details.get(key) not in (None, ""):
                try:
                    value = float(str(details[key]).replace(",", "."))
                except ValueError:
                    raise UploadError("Invalid coordinates.") from None
                if not -limit <= value <= limit:
                    raise UploadError("Coordinates must be in WGS84.")
                safe_details[key] = value
        result = upload.start(files, origin, safe_details)
        return {**result, "chunk_bytes": CHUNK_BYTES}
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@app.get("/api/v2/tasks/{project_id}/asset-uploads/{upload_id}")
def asset_upload_status(request: Request, project_id: int, upload_id: str):
    from .asset_uploads import UploadError
    try:
        upload = _asset_upload(request, project_id, upload_id)
        return upload.public_status(upload.read())
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@app.put("/api/v2/tasks/{project_id}/asset-uploads/{upload_id}/files/{index}")
async def receive_asset_upload_block(request: Request, project_id: int, upload_id: str, index: int, offset: int):
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import ClientDisconnect
    from .asset_uploads import CHUNK_BYTES, UploadError
    try:
        upload = _asset_upload(request, project_id, upload_id)
        await run_in_threadpool(upload.require_owner)
        digest = request.headers.get("x-content-sha256", "")
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise UploadError("A SHA-256 digest is required for every block.", 422)
        contents = bytearray()
        async for chunk in request.stream():
            if len(contents) + len(chunk) > CHUNK_BYTES:
                raise UploadError("Upload block is too large.", 413)
            contents.extend(chunk)
        return {"offset": await run_in_threadpool(upload.append, index, offset, contents, digest)}
    except ClientDisconnect:
        return JSONResponse({"detail": "Connection interrupted. Resume from the saved offset."}, status_code=499)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


def _asset_upload_checkout(upload, *, cancel=False):
    """Serialize checkout creation/expiration off the event loop, including retries."""
    import asyncio
    import uuid
    from .processing_architecture import cross_process_file_lock
    from .asset_uploads import UploadError
    with cross_process_file_lock(upload.directory / "checkout.lock"):
        result = upload.public_status(upload.read()) if cancel else upload.finish()
        if result["status"] != "AWAITING_PAYMENT":
            return upload.cancel() if cancel else result
        data = upload.read()
        sid = data.get("checkout_session")
        if sid:
            order = stripe_payments.read_order(upload.store, sid)
            secret = upload.store.get_setting("StripeSecretKey", "")
            if not order or order["user_id"] != upload.user_id or stripe_payments.live_mode(secret) != bool(order["live"]):
                raise UploadError("Checkout requires reconciliation. Your upload is preserved.", 409)
            async def existing_checkout():
                async with httpx.AsyncClient(timeout=20) as client:
                    if await stripe_payments.stripe_account(client, secret) != order["stripe_account"]:
                        raise stripe_payments.RetryPayment("Checkout account changed")
                    response = await client.get(f"https://api.stripe.com/v1/checkout/sessions/{sid}", auth=(secret, ""))
                    response.raise_for_status()
                    session = response.json()
                    if cancel and session.get("status") == "open":
                        response = await client.post(f"https://api.stripe.com/v1/checkout/sessions/{sid}/expire", auth=(secret, ""))
                        response.raise_for_status()
                        session = response.json()
                    return session
            session = asyncio.run(existing_checkout())
            if session.get("status") == "complete":
                asyncio.run(_fulfill_stripe_checkout_session(sid, user_id=upload.user_id))
                if cancel:
                    raise UploadError("Payment was confirmed. Cancel the processing job from the task page.", 409)
                return upload.public_status(upload.read())
            if session.get("status") == "open":
                return result
            if session.get("status") != "expired":
                raise UploadError("Checkout is pending. Your upload is preserved.", 409)
            with cross_process_file_lock(upload.lock_file):
                data = upload.read()
                data.update(checkout_url=None, checkout_session=None, checkout_attempt=str(uuid.uuid4()))
                upload.write(data)
        if cancel:
            return upload.cancel(checkout_canceled=True)
        quote = data["checkout_quote"]
        project_id, upload_id = upload.project_id, upload.upload_id
        checkout = asyncio.run(_create_stripe_checkout_session(
            user=upload.store.get_user_by_id(upload.user_id), mode="payment",
            amount_eur=quote["amount_eur"], product_name=f"MosaicEarth Gaussian reconstruction · Task #{project_id}",
            product_description="COLMAP + OpenSplat · PLY, SPZ and 3D Tiles",
            cancel_path=f"/Client/GaussianProject.aspx?project_id={project_id}&checkout=cancelled",
            metadata={"mosaicearth_kind": "processing_task", "mosaicearth_user_id": str(upload.user_id),
                      "mosaicearth_project_id": str(project_id), "mosaicearth_task_kind": "gaussian",
                      "mosaicearth_target_mode": "cloud", "mosaicearth_num_images": str(len(data["files"])),
                      "mosaicearth_upload_id": upload_id, "mosaicearth_checkout_attempt": data["checkout_attempt"]},
            idempotency_key="gaussian-upload-" + data["checkout_attempt"],
        ))
        if not checkout.get("ok"):
            raise UploadError(checkout.get("message") or "Checkout is unavailable. Your upload is preserved.", 503)
        with cross_process_file_lock(upload.lock_file):
            data = upload.read()
            data.update(checkout_url=checkout["checkoutUrl"], checkout_session=checkout["sessionId"])
            upload.write(data)
            return upload.public_status(data)


@app.post("/api/v2/tasks/{project_id}/asset-uploads/{upload_id}/finish", status_code=202)
async def finish_asset_upload(request: Request, project_id: int, upload_id: str):
    from starlette.concurrency import run_in_threadpool
    from .asset_uploads import UploadError
    try:
        return await run_in_threadpool(_asset_upload_checkout, _asset_upload(request, project_id, upload_id))
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except (httpx.HTTPError, stripe_payments.RetryPayment, stripe_payments.InvalidPayment):
        raise HTTPException(status_code=503, detail="Secure Checkout is temporarily unavailable. Your upload is preserved; retry shortly.") from None


@app.delete("/api/v2/tasks/{project_id}/asset-uploads/{upload_id}")
def cancel_asset_upload(request: Request, project_id: int, upload_id: str):
    from .asset_uploads import UploadError
    try:
        return _asset_upload_checkout(_asset_upload(request, project_id, upload_id), cancel=True)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except (httpx.HTTPError, stripe_payments.RetryPayment, stripe_payments.InvalidPayment):
        raise HTTPException(status_code=503, detail="Checkout cancellation could not be confirmed. Your upload is preserved; retry shortly.") from None


@app.post("/api/v2/tasks/{project_id}/asset-uploads/{upload_id}/retry", status_code=202)
async def retry_asset_upload(request: Request, project_id: int, upload_id: str):
    from starlette.concurrency import run_in_threadpool
    from .asset_uploads import UploadError
    upload = _asset_upload(request, project_id, upload_id)
    try:
        await run_in_threadpool(upload.prepare_retry)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    return await finish_asset_upload(request, project_id, upload_id)


@app.post("/ExternalAssetImportUpload.ashx")
async def handler_external_asset_import_upload(
    request: Request,
    project_id: int = Form(0),
    displayName: str = Form(""),
    title: str = Form(""),
    source_origin: str = Form("imported"),
    description: str = Form(""),
    capture_date: str = Form(""),
    capture_method: str = Form(""),
    lat: str = Form(""),
    lon: str = Form(""),
    file: List[UploadFile] = File(...),
):
    # Compatibility for older forms. Reconstruction and R2 writes stay in the queue.
    import uuid
    from starlette.concurrency import run_in_threadpool
    from .asset_uploads import AssetUpload, CHUNK_BYTES, GAUSSIAN_MODELS, SIDECARS, UploadError, normalize_files

    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/Default.aspx?ReturnUrl=%2Fen%2FProjects", status_code=302)
    store = get_store()
    uid = int(user["ID"])
    project = store.get_project(project_id) if project_id > 0 else None
    if project_id > 0 and (not project or int(project.get("CREATE_ID") or 0) != uid):
        raise HTTPException(status_code=404, detail="Task not found.")
    origin = ("gaussian" if store.get_gaussian_project(project_id) else "imported") if project else source_origin
    if origin not in {"gaussian", "imported"}:
        raise HTTPException(status_code=400, detail="Invalid upload type.")

    uploads = sorted(file, key=lambda item: Path(item.filename or "").suffix.lower() in SIDECARS)
    try:
        files = normalize_files([{"name": f.filename, "size": f.size} for f in uploads], origin)
        if sum(f["size"] for f in files) > 16 * 1024**2:
            raise UploadError("Use the resumable MyEarth uploader for files above 16 MiB.", 413)
        if origin == "gaussian" and Path(files[0]["name"]).suffix.lower() not in GAUSSIAN_MODELS:
            raise UploadError("Use the resumable MyEarth uploader to start Gaussian reconstruction.", 409)

        details = {"title": (title or displayName).strip()[:2000], "description": description[:2000],
                   "capture_date": capture_date[:2000], "capture_method": capture_method[:2000]}
        for key, raw, limit in (("lat", lat, 90), ("lon", lon, 180)):
            if raw:
                try:
                    value = float(raw.replace(",", "."))
                except ValueError:
                    raise UploadError("Invalid coordinates.") from None
                if not -limit <= value <= limit:
                    raise UploadError("Coordinates must be in WGS84.")
                details[key] = value
        if not project:
            project_id = store.create_project(uid, details["title"] or files[0]["name"],
                                              kind="gaussian" if origin == "gaussian" else "external")
        upload = AssetUpload(store, get_blob_store(), project_id, uid, str(uuid.uuid4()))
        await run_in_threadpool(upload.start, files, origin, details)
        for index, incoming in enumerate(uploads):
            offset = 0
            while contents := await incoming.read(CHUNK_BYTES):
                digest = hashlib.sha256(contents).hexdigest()
                offset = await run_in_threadpool(upload.append, index, offset, contents, digest)
        await run_in_threadpool(upload.finish)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None

    root_pid, _ = store.get_subprojects_family(project_id)
    return RedirectResponse(url=f"/en/Projects?open_project={root_pid}#taskNode_{project_id}", status_code=303)


@app.post("/api/v2/terrain/derivative")
async def route_generate_terrain_derivative(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Authentication required", "message": "Authentication required"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "message": "Expected a terrain generation request."}, status_code=400)
    store = get_store()
    try:
        project_id = int(body.get("project_id") or body.get("projectId") or 0)
        source_asset_id = int(body.get("source_asset_id") or body.get("sourceAssetId") or 0)
        interval_m = float(body.get("contour_interval_m", body.get("interval", 2.0)))
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "message": "Invalid project, source or interval."}, status_code=400)
    kind = str(body.get("derivative_kind") or body.get("derivative") or body.get("kind") or "hillshade").strip().lower()
    source_dem = str(body.get("source_dem") or body.get("sourceDem") or "").strip().lower()
    if not source_dem and source_asset_id > 0:
        for ext in store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", []):
            if int(ext.get("ID") or 0) == source_asset_id:
                st_lbl = f"{ext.get('ASSET_SUBTYPE') or ''} {ext.get('DISPLAY_NAME') or ''}".lower()
                source_dem = "dtm" if "dtm" in st_lbl else "dsm"
                if project_id <= 0:
                    project_id = int(ext.get("PROJECT_ID") or 0)
                break
    proj = store.get_project(project_id) if project_id > 0 else None
    if not proj:
        return JSONResponse({"ok": False, "error": "Project not found."}, status_code=404)
    owner_uid = int(proj.get("CREATE_ID") or proj.get("ID_USER") or proj.get("USER_ID") or 0)
    if owner_uid != int(user["ID"]) and int(user.get("ROL_ID") or 0) != 1:
        return JSONResponse({"ok": False, "error": "Forbidden: you do not own this project."}, status_code=403)
    if not source_dem:
        source_dem = "dsm"
    import math
    if kind not in ("hillshade", "slope", "aspect", "contours") or not math.isfinite(interval_m) or not 0.1 <= interval_m <= 100:
        return JSONResponse({"ok": False, "message": "Select a terrain product and an interval from 0.1 to 100 meters."}, status_code=400)
    source = store.get_external_asset(project_id, source_asset_id)
    if not source or source.get("CANONICAL_STATUS") != "READY" or source.get("DERIVATIVE_KIND") or not (
        source.get("ASSET_SUBTYPE") in ("dsm", "dtm", "dem", "terrain") or source.get("ASSET_CLASS") == "RASTER_ELEVATION"
    ):
        return JSONResponse({"ok": False, "message": "Select a ready DSM or DTM from this task."}, status_code=400)
    job_type = f"terrain_derivative:{kind}" + (f":{interval_m:g}" if kind == "contours" else "")
    jid = store.get_job_queue().enqueue_job(job_type, project_id, source_asset_id, int(user["ID"]),
        {"derivative_kind": kind, "source_dem": source_dem, "contour_interval_m": interval_m})
    return JSONResponse({"ok": True, "job_id": jid, "message": "Terrain generation queued. Your original DEM is preserved."}, status_code=202)


@app.get("/api/v2/jobs/status")
async def route_background_job_status(request: Request, job_id: int = 0, asset_id: int = 0, project_id: int = 0):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Authentication required"}, status_code=401)
    store = get_store()
    j = store.get_job_queue().get_job(job_id=job_id, asset_id=asset_id, project_id=project_id)
    store.refresh_processing_state()
    project = store.get_project(int(j.get("PROJECT_ID") or 0)) if j else None
    if not project or int(project.get("CREATE_ID") or 0) != int(user["ID"]):
        return JSONResponse({"ok": False, "error": "Job not found"}, status_code=404)
    fields = ("ID", "PROJECT_ID", "ASSET_ID", "STATUS", "PROGRESS", "STAGE", "CREATED_AT", "UPDATED_AT", "FINISHED_AT", "RESULT_ASSET_ID", "RESULT_URL")
    summary = {name: j.get(name) for name in fields}
    summary["PAYMENT_STATUS"] = project.get("PAYMENT_STATUS")
    if j.get("JOB_TYPE") == "photogrammetry_pipeline":
        state = int(project.get("STATE_ID") or 0)
        summary["STATUS"] = {1:"PROVISIONING", 2:"PROCESSING", 3:"FAILED", 4:"COMPLETED"}.get(state, summary["STATUS"])
        summary["PROGRESS"] = 100 if state == 4 else int(project.get("PROCESSING_PROGRESS") or 0)
        summary["STAGE"] = project.get("PROCESSING_STAGE") or summary["STAGE"]
    if summary["STATUS"] == "FAILED":
        summary["ERROR"] = "Processing failed. Your originals are preserved."
        if str(j.get("JOB_TYPE") or "").startswith("terrain_derivative:") or j.get("JOB_TYPE") == "asset_regenerate":
            summary["ERROR"] = str(j.get("LAST_ERROR") or summary["ERROR"])[:250]
    return JSONResponse({"ok": True, "job": summary})


@app.post("/api/v2/assets/regenerate")
async def route_regenerate_asset_derivatives(request: Request):
    """
    Rebuilds all or selected derivatives (`preview`, `imagery`, `terrain`, `3dtiles`,
    `pointcloud_3dtiles`, `mesh_optimized`, `vector`) directly from the immutable `source/` master file.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Authentication required."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    project_id = int(body.get("project_id") or body.get("projectId") or 0)
    asset_id = int(body.get("asset_id") or body.get("assetId") or 0)
    derivative_types = body.get("derivative_types") or body.get("derivatives") or None
    user_class_override = str(body.get("asset_class") or body.get("user_class_override") or "").strip()
    elevation_palette = str(body.get("palette") or body.get("elevation_palette") or "").strip()

    store = get_store()
    store.refresh_processing_state()
    ext = store.get_external_asset(project_id, asset_id) if project_id > 0 else store.get_external_asset_by_id(asset_id)
    if not ext:
        return JSONResponse({"ok": False, "error": "Asset not found."}, status_code=404)
    pid = int(ext.get("PROJECT_ID") or project_id)
    aid = int(ext.get("ID") or asset_id)

    proj = store.get_project(pid) or {}
    owner_uid = int(ext.get("USER_ID") or proj.get("CREATE_ID") or proj.get("ID_USER") or 0)
    if owner_uid != int(user["ID"]) and int(user.get("ROL_ID") or 0) != 1:
        return JSONResponse({"ok": False, "error": "Unauthorized to regenerate derivatives for this asset."}, status_code=403)

    from .asset_uploads import asset_output_available, gaussian_training_input
    if not asset_output_available(ext) or gaussian_training_input(ext):
        return JSONResponse({"ok": False, "error": "Start a new authorized processing request for this source."}, status_code=409)
    if user_class_override not in ("", "RASTER_GENERIC", "RASTER_ELEVATION", "RASTER_IMAGERY") or elevation_palette not in ("", "hypsometric", "viridis", "terrain", "grayscale"):
        return JSONResponse({"ok": False, "error": "Invalid raster class or palette."}, status_code=400)
    if ext.get("DERIVATIVE_KIND") and user_class_override == "RASTER_ELEVATION":
        return JSONResponse({"ok": False, "error": "Terrain analysis values are not elevation heights."}, status_code=400)
    jid = store.get_job_queue().enqueue_job("asset_regenerate", pid, aid, int(user["ID"]), {
        "derivative_types": derivative_types if isinstance(derivative_types, list) else None,
        "asset_class": user_class_override, "palette": elevation_palette,
    })
    return JSONResponse({"ok": True, "job_id": jid, "message": "Viewer regeneration queued. Your original is preserved."}, status_code=202)


@app.post("/api/v2/assets/reclassify")
async def route_reclassify_asset(request: Request):
    """
    Allows reclassifying an ambiguous single-band raster (`RASTER_GENERIC`) into
    `RASTER_ELEVATION` (DEM/DSM/DTM) or `RASTER_IMAGERY`, or changing the DEM color palette,
    regenerating derivatives from the immutable `source/` master.
    """
    return await route_regenerate_asset_derivatives(request)


@app.api_route("/api/v2/assets/transform", methods=["GET", "POST"])
async def route_asset_transform(request: Request, project_id: int = 0, asset_id: int = 0, projectId: int = 0, assetId: int = 0):
    """
    Reads or updates explicit georeferencing (`metadata/transform.json`) for an imported 3D asset
    without ever modifying or touching the original file in `source/`.
    """
    store = get_store()
    blob_store = get_blob_store()
    project_id = project_id or projectId
    asset_id = asset_id or assetId
    if request.method == "GET":
        ext = store.get_external_asset(project_id, asset_id) if project_id > 0 else store.get_external_asset_by_id(asset_id)
        if not ext:
            return JSONResponse({"ok": False, "error": "Asset not found"}, status_code=404)
        pid = int(ext.get("PROJECT_ID") or project_id)
        aid = int(ext.get("ID") or asset_id)
        proj_chk = store.get_project(pid) or {}
        _enforce_project_and_asset_acl(request, store, project_row=proj_chk, ext_asset=ext, require_download_rights=False)
        for cand_rel in (f"external/{pid}/{aid}/metadata/transform.json", f"external/{pid}/{aid}/transform.json"):
            for cont in ("results", "assets"):
                loc = blob_store.ensure_local(cont, cand_rel, force_download=True)
                if loc and loc.is_file():
                    try:
                        return JSONResponse({"ok": True, "transform": json.loads(loc.read_text(encoding="utf-8"))})
                    except Exception:
                        pass
        return JSONResponse({
            "ok": True,
            "transform": {
                "longitude": float(ext.get("LONGITUDE") or 0.0),
                "latitude": float(ext.get("LATITUDE") or 0.0),
                "height": float(ext.get("ALTITUDE") or 0.0),
                "orientation": {
                    "heading": float(ext.get("HEADING") or 0.0),
                    "pitch": float(ext.get("PITCH") or 0.0),
                    "roll": float(ext.get("ROLL") or 0.0),
                },
                "scale": float(ext.get("SCALE") or 1.0),
            },
        })

    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Authentication required"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pid = int(body.get("project_id") or body.get("projectId") or project_id or 0)
    aid = int(body.get("asset_id") or body.get("assetId") or asset_id or 0)
    ext = store.get_external_asset(pid, aid) if pid > 0 else store.get_external_asset_by_id(aid)
    if not ext:
        return JSONResponse({"ok": False, "error": "Asset not found"}, status_code=404)
    pid = int(ext.get("PROJECT_ID") or pid)
    aid = int(ext.get("ID") or aid)
    proj = store.get_project(pid) or {}
    owner_uid = int(ext.get("USER_ID") or proj.get("CREATE_ID") or proj.get("ID_USER") or 0)
    if owner_uid != int(user["ID"]) and int(user.get("ROL_ID") or 0) != 1:
        return JSONResponse({"ok": False, "error": "Forbidden: you do not own this asset"}, status_code=403)
    orient = body.get("orientation") if isinstance(body.get("orientation"), dict) else {}
    lat_v = float(body.get("latitude") if body.get("latitude") is not None else (body.get("lat") if body.get("lat") is not None else ext.get("LATITUDE") or 41.8205))
    lon_v = float(body.get("longitude") if body.get("longitude") is not None else (body.get("lon") if body.get("lon") is not None else ext.get("LONGITUDE") or 1.8676))
    alt_v = float(body.get("height") if body.get("height") is not None else (body.get("alt") if body.get("alt") is not None else ext.get("ALTITUDE") or 0.0))
    hdg_v = float(orient.get("heading") if orient.get("heading") is not None else (body.get("heading") if body.get("heading") is not None else ext.get("HEADING") or 0.0))
    pit_v = float(orient.get("pitch") if orient.get("pitch") is not None else (body.get("pitch") if body.get("pitch") is not None else ext.get("PITCH") or 0.0))
    rol_v = float(orient.get("roll") if orient.get("roll") is not None else (body.get("roll") if body.get("roll") is not None else ext.get("ROLL") or 0.0))
    scl_v = float(body.get("scale") if body.get("scale") is not None else (ext.get("SCALE") or 1.0))

    uid = int(user["ID"])
    ok = store.update_external_asset_placement_3d(
        project_id=pid,
        asset_id=aid,
        user_id=uid,
        lat=lat_v,
        lon=lon_v,
        alt=alt_v,
        heading=hdg_v,
        pitch=pit_v,
        roll=rol_v,
        scale=scl_v,
    )
    tf_loc = blob_store.ensure_local("results", f"external/{pid}/{aid}/metadata/transform.json", force_download=True)
    tf_doc = json.loads(tf_loc.read_text(encoding="utf-8")) if (tf_loc and tf_loc.is_file()) else {}
    return JSONResponse({"ok": bool(ok), "project_id": pid, "asset_id": aid, "transform": tf_doc})


@app.get("/api/v2/assets/metadata")
async def route_asset_canonical_metadata(request: Request, project_id: int = 0, asset_id: int = 0, projectId: int = 0, assetId: int = 0):
    """Returns canonical `asset.json`, `crs.json`, `transform.json`, and `derivatives.json` for an asset."""
    store = get_store()
    blob_store = get_blob_store()
    project_id = project_id or projectId
    asset_id = asset_id or assetId
    ext = store.get_external_asset(project_id, asset_id) if project_id > 0 else store.get_external_asset_by_id(asset_id)
    if not ext:
        return JSONResponse({"ok": False, "error": "Asset not found"}, status_code=404)
    pid = int(ext.get("PROJECT_ID") or project_id)
    aid = int(ext.get("ID") or asset_id)
    proj_chk = store.get_project(pid) or {}
    _enforce_project_and_asset_acl(request, store, project_row=proj_chk, ext_asset=ext, require_download_rights=False)
    out_docs: Dict[str, Any] = {}
    for key_name, fn in (("asset", "asset.json"), ("crs", "crs.json"), ("transform", "transform.json"), ("derivatives", "derivatives.json")):
        rel = f"external/{pid}/{aid}/metadata/{fn}"
        for cont in ("results", "assets"):
            loc = blob_store.ensure_local(cont, rel, force_download=True)
            if loc and loc.is_file():
                try:
                    out_docs[key_name] = json.loads(loc.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass
    return JSONResponse({
        "ok": True,
        "project_id": pid,
        "asset_id": aid,
        "source_sha256": ext.get("SOURCE_SHA256") or "",
        "asset_class": ext.get("ASSET_CLASS") or "",
        "canonical_status": ext.get("CANONICAL_STATUS") or "READY",
        "metadata": out_docs,
    })


@app.post("/WorkerCompletionEmail.ashx")
async def handler_worker_completion_email(
    request: Request,
    projectId: int = Form(0),
    assetId: int = Form(0),
):
    store = get_store()
    expected_token = store.get_setting("Worker.NotificationToken", "")
    supplied_token = request.headers.get("X-MosaicEarth-Worker") or ""
    if expected_token and supplied_token != expected_token and not is_lan_or_local_request(request):
        return PlainTextResponse("", status_code=404)
    if projectId <= 0:
        return PlainTextResponse("INVALID_REQUEST", status_code=400)
    proj = store.get_project(projectId)
    if not proj:
        return PlainTextResponse("PROJECT_NOT_FOUND", status_code=404)
    owner = store.get_user_by_id(int(proj.get("CREATE_ID") or 0))
    if not owner or not owner.get("EMAIL"):
        return PlainTextResponse("OWNER_NOT_FOUND", status_code=404)
    recipient = str(owner["EMAIL"])
    if assetId > 0:
        ext = store.get_external_asset(projectId, assetId)
        if not ext:
            return PlainTextResponse("RESULT_NOT_READY", status_code=404)
        url = f"{get_public_site_url()}/Client/ExternalAssetImport.aspx?project_id={projectId}&asset_id={assetId}"
        title = str(ext.get("DISPLAY_NAME") or ext.get("TITLE") or proj.get("NAME") or f"Asset {assetId}")
        src_origin = str(ext.get("SOURCE_ORIGIN") or "imported").lower()
        store.add_notification(
            int(owner["ID"]),
            "Processing complete",
            f"Your MosaicAsset '{title}' is ready for review.",
            link_id=projectId,
            type_id=1,
        )
        ok = get_email_service().send_project_completed_email(
            recipient=recipient,
            project_name=title,
            project_url=url,
            is_imported=(src_origin == "imported"),
            idempotency_key=f"external-asset-complete:{assetId}",
            result_kind_label="photogrammetry result" if src_origin == "processed" else ("Gaussian splat" if src_origin == "gaussian" else None),
        )
    else:
        url = f"{get_public_site_url()}/Client/Project.aspx?id={projectId}"
        title = str(proj.get("NAME") or f"Project {projectId}")
        store.add_notification(
            int(owner["ID"]),
            "Processing complete",
            f"Your project '{title}' is ready. Review the MosaicAssets before publishing.",
            link_id=projectId,
            type_id=1,
        )
        ok = get_email_service().send_project_completed_email(
            recipient=recipient,
            project_name=title,
            project_url=url,
            is_imported=False,
            idempotency_key=f"project-complete:{projectId}",
        )
    return PlainTextResponse("EMAIL_ACCEPTED" if ok else "EMAIL_SPOOL_SAVED", status_code=200)


# ============================================================================
# BINARY ASSET STREAMING HANDLERS (HTTP RANGE 206 PARTIAL CONTENT)
# ============================================================================
import base64

EMPTY_TILE_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=")
FALLBACK_TMS_XML = """<?xml version="1.0" encoding="utf-8"?>
<TileMap version="1.0.0" tilemapservice="http://tms.osgeo.org/1.0.0">
  <Title>Orthophoto</Title>
  <Abstract></Abstract>
  <SRS>EPSG:3857</SRS>
  <BoundingBox minx="-180.0" miny="-85.05112878" maxx="180.0" maxy="85.05112878"/>
  <Origin x="-180.0" y="-85.05112878"/>
  <TileFormat width="256" height="256" mime-type="image/png" extension="png"/>
  <TileSets profile="mercator">
    <TileSet href="0" units-per-pixel="156543.03392804062" order="0"/>
    <TileSet href="1" units-per-pixel="78271.51696402031" order="1"/>
    <TileSet href="2" units-per-pixel="39135.758482010155" order="2"/>
    <TileSet href="3" units-per-pixel="19567.879241005078" order="3"/>
    <TileSet href="4" units-per-pixel="9783.939620502539" order="4"/>
    <TileSet href="5" units-per-pixel="4891.9698102512695" order="5"/>
    <TileSet href="6" units-per-pixel="2445.9849051256347" order="6"/>
    <TileSet href="7" units-per-pixel="1222.9924525628174" order="7"/>
    <TileSet href="8" units-per-pixel="611.4962262814087" order="8"/>
    <TileSet href="9" units-per-pixel="305.74811314070435" order="9"/>
    <TileSet href="10" units-per-pixel="152.87405657035217" order="10"/>
    <TileSet href="11" units-per-pixel="76.43702828517609" order="11"/>
    <TileSet href="12" units-per-pixel="38.21851414258804" order="12"/>
    <TileSet href="13" units-per-pixel="19.10925707129402" order="13"/>
    <TileSet href="14" units-per-pixel="9.55462853564701" order="14"/>
    <TileSet href="15" units-per-pixel="4.777314267823505" order="15"/>
    <TileSet href="16" units-per-pixel="2.3886571339117526" order="16"/>
    <TileSet href="17" units-per-pixel="1.1943285669558763" order="17"/>
    <TileSet href="18" units-per-pixel="0.5971642834779381" order="18"/>
    <TileSet href="19" units-per-pixel="0.2985821417389691" order="19"/>
    <TileSet href="20" units-per-pixel="0.14929107086948453" order="20"/>
  </TileSets>
</TileMap>"""


def _enforce_project_and_asset_acl(
    request: Request,
    store: Any,
    project_row: Optional[Dict[str, Any]],
    ext_asset: Optional[Dict[str, Any]] = None,
    require_download_rights: bool = False,
    share_token: str = "",
) -> None:
    """
    Unified, strict authorization guard for all project & external asset endpoints
    (both original/master downloads AND visualization derivatives / 3D Tiles / metadata).
    """
    user = get_current_user(request)
    viewer_uid = int(user["ID"]) if user else 0
    is_admin = bool(user and (int(user.get("ROL_ID") or 0) == 1 or bool(user.get("IS_ADMIN"))))
    if is_admin:
        return

    proj = project_row or {}
    ext = ext_asset or {}
    pid = int(ext.get("PROJECT_ID") or proj.get("ID") or 0)
    owner_id = int(
        ext.get("USER_ID")
        or ext.get("CREATE_ID")
        or proj.get("CREATE_ID")
        or proj.get("USER_ID")
        or proj.get("ID_USER")
        or 0
    )
    if viewer_uid > 0 and owner_id > 0 and viewer_uid == owner_id:
        return

    # Check publication visibility
    pub_row = None
    if pid > 0:
        for pub in store.tables.get("dbo.WEB_PUBLICATIONS", []):
            if int(pub.get("PROJECT_ID") or pub.get("ID_PROJECT") or 0) == pid and (
                int(pub.get("VISIBLE") or 0) == 1 or int(pub.get("STATUS_ID") or 0) == 2
            ):
                pub_row = pub
                break

    proj_vis_flag = int(proj.get("PUBLIC") if proj.get("PUBLIC") is not None else (proj.get("IS_PUBLIC") or 0)) == 1
    ext_vis_str = str(ext.get("VISIBILITY") or "").strip().lower()
    ext_pub_flag = (
        int(ext.get("IS_PUBLIC") or 0) == 1
        or int(ext.get("PUBLICATION_STATUS_ID") or 0) == 2
        or ext_vis_str == "public"
    )
    explicitly_private = (
        ext_vis_str == "private"
        or (ext and "IS_PUBLIC" in ext and int(ext.get("IS_PUBLIC") or 0) == 0 and ext_vis_str != "public" and not pub_row)
        or (not ext and proj and "PUBLIC" in proj and int(proj.get("PUBLIC") or 0) == 0 and not pub_row)
    )
    is_public_resource = (bool(pub_row) or proj_vis_flag or ext_pub_flag) and not explicitly_private

    pub_id = int((pub_row or {}).get("ID") or (pub_row or {}).get("ID_PROJECT_PUBLISH") or 0)
    ext_id = int(ext.get("ID") or 0)
    has_bought = False
    if viewer_uid > 0:
        has_bought = bool(
            (
                hasattr(store, "user_has_purchased_asset")
                and store.user_has_purchased_asset(viewer_uid, publish_id=pub_id, project_id=pid, external_asset_id=ext_id)
            )
            or (
                hasattr(store, "has_user_purchased_asset")
                and store.has_user_purchased_asset(viewer_uid, publish_id=pub_id)
            )
            or any(
                int(a.get("BUYER_USER_ID") or a.get("USER_ID") or 0) == viewer_uid
                and int(a.get("ASSET_ID") or 0) == ext_id
                and ext_id > 0
                for a in store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSET_ACQUISITIONS", [])
            )
        )

    # Verify optional signed share_token for view-only shared links
    valid_share_token = False
    req_token = (share_token or str(request.query_params.get("share_token") or request.query_params.get("token") or "")).strip()
    if req_token and not require_download_rights:
        proj_token = str(proj.get("SHARE_TOKEN") or proj.get("TOKEN") or ext.get("SHARE_TOKEN") or "").strip()
        if proj_token and len(proj_token) >= 8 and req_token == proj_token:
            valid_share_token = True

    if not is_public_resource and not valid_share_token and not has_bought:
        if viewer_uid == 0:
            raise HTTPException(
                status_code=401,
                detail="Authentication required: this project or asset is private.",
            )
        raise HTTPException(
            status_code=403,
            detail="Access denied: you do not have permission to access this private project or asset (nor its derivatives).",
        )

    if require_download_rights:
        if not is_public_resource and not has_bought:
            raise HTTPException(
                status_code=403,
                detail="This asset is Private (View-Only). Original master file download is restricted to the owner.",
            )
        allow_dl_ext = ext.get("ALLOW_DOWNLOAD")
        lic_mode = str(ext.get("LICENSE_MODE") or (pub_row or {}).get("LICENSE_MODE") or "").strip().lower()
        if (allow_dl_ext is not None and int(bool(allow_dl_ext)) == 0) or lic_mode == "view_only":
            raise HTTPException(
                status_code=403,
                detail="License restriction: this asset is licensed for 3D visualization only (master download is disabled by the creator).",
            )
        price_eur = float(
            (pub_row or {}).get("PRICE_EUR")
            if (pub_row or {}).get("PRICE_EUR") is not None
            else (
                (pub_row or {}).get("CREDITS")
                or ext.get("PRICE_EUR")
                or ext.get("PRICE_CREDITS")
                or ext.get("CREDITS")
                or proj.get("PRICE_EUR")
                or 0
            )
        )
        if price_eur > 0 and not has_bought:
            if viewer_uid == 0:
                raise HTTPException(
                    status_code=401,
                    detail=f"Authentication and Marketplace purchase ({price_eur:.0f} €) required before downloading the master file.",
                )
            raise HTTPException(
                status_code=402,
                detail=f"Purchase required ({price_eur:.0f} €) before downloading the master file.",
            )


@app.api_route("/ProcessedAssetFile.ashx", methods=["GET", "HEAD"])
@app.api_route("/ProcessedAssetFile.ashx/{subpath:path}", methods=["GET", "HEAD"])
async def handler_processed_asset_file(request: Request, path: str = "", subpath: str = ""):
    raw_path = path or subpath
    if not raw_path:
        raise HTTPException(status_code=400, detail="Missing path parameter")
    clean = unquote(raw_path).strip().lstrip("/")
    if ".." in clean.split("/"):
        raise HTTPException(status_code=400, detail="Invalid path traversal sequence")

    segs = [s for s in clean.split("/") if s]
    extracted_share_token = ""
    if len(segs) >= 5 and segs[0].lower() == "share":
        extracted_share_token = segs[3]
        clean = "/".join(segs[4:])
    elif len(segs) >= 3 and segs[0].lower() == "map":
        clean = "/".join(segs[2:])

    if clean.endswith("/orthophoto_tiles") or clean.endswith("/orthophoto_tiles/"):
        clean = clean.rstrip("/") + "/tilemapresource.xml"

    # Resolve project from folder prefix and enforce strict ACL on BOTH derivatives and master downloads
    folder_seg = clean.split("/")[0] if "/" in clean else ""
    clean_lower = clean.lower()
    is_master_download_path = (
        "/procesado/download/" in clean_lower
        or "/procesado/geotiff/" in clean_lower
        or "/geotiff/" in clean_lower
        or clean_lower.endswith((".laz", ".las", "orthophoto.tif", "odm_orthophoto.tif", "dsm.tif", "dtm.tif", "3dmodel.zip", "3d-model.zip", "pointcloud.zip"))
    )
    if folder_seg:
        store = get_store()
        matched_proj = None
        for p in store.tables.get("dbo.PROJECTS", []):
            if str(p.get("FOLDER") or "") == folder_seg or to_slug(f"{p.get('ID')}-{p.get('NAME', 'project')}") == folder_seg:
                matched_proj = p
                break
        if not matched_proj and folder_seg.split("-")[0].isdigit():
            matched_proj = store.get_project(int(folder_seg.split("-")[0]))
        if not matched_proj:
            raise HTTPException(status_code=404, detail="Processed project not found")
        if matched_proj:
            _enforce_project_and_asset_acl(
                request,
                store,
                project_row=matched_proj,
                ext_asset=None,
                require_download_rights=is_master_download_path,
                share_token=extracted_share_token,
            )

    blob_store = get_blob_store()

    # Build canonical + alias candidate relative paths (eliminating physical duplicate uploads in R2/disk)
    candidates = [clean]
    if "/procesado/3d_tiles/model/" in clean:
        candidates.append(clean.replace("/procesado/3d_tiles/model/", "/procesado/output/"))
    elif "/procesado/output/" in clean:
        candidates.append(clean.replace("/procesado/output/", "/procesado/3d_tiles/model/"))
    if "/procesado/geotiff/odm_orthophoto.tif" in clean:
        candidates.append(clean.replace("/procesado/geotiff/odm_orthophoto.tif", "/procesado/download/orthophoto.tif"))
    elif "/geotiff/orthophoto.tif" in clean:
        candidates.append(clean.replace("/geotiff/orthophoto.tif", "/procesado/download/orthophoto.tif"))
    elif "/procesado/download/orthophoto.tif" in clean:
        candidates.append(clean.replace("/procesado/download/orthophoto.tif", "/procesado/geotiff/odm_orthophoto.tif"))
    if "/3d/3dmodel.zip" in clean:
        candidates.append(clean.replace("/3d/3dmodel.zip", "/procesado/download/3dmodel.zip"))
    elif "/3d-model.zip" in clean:
        candidates.append(clean.replace("/3d-model.zip", "/procesado/download/3dmodel.zip"))
    if clean.endswith("/procesado/images/orthophoto.png"):
        candidates.append(clean.replace("/procesado/images/orthophoto.png", "/procesado/odm_orthophoto/odm_orthophoto.webp"))
        candidates.append(clean.replace("/procesado/images/orthophoto.png", "/procesado/odm_orthophoto/odm_orthophoto.png"))

    # 1. Fast local disk check first (< 0.2 ms, never blocks event loop)
    for cand in candidates:
        for container in ("imagenes", "results", "assets"):
            resolved = blob_store.resolve_blob_path(container, cand, allow_remote_fallback=False)
            if resolved:
                return blob_store.build_range_response(request, resolved)

    # 2. Non-blocking threadpool direct streaming from External Cloud Storage
    import asyncio
    remote_stream = await asyncio.to_thread(
        blob_store.stream_first_available_remote,
        request,
        ("imagenes", "results", "assets"),
        candidates,
    )
    if remote_stream is not None:
        return remote_stream

    # 3. Desktop or legacy local cache fallback
    def _fetch_remote() -> Optional[Path]:
        for cand in candidates:
            res = blob_store.resolve_blob_path("imagenes", cand, allow_remote_fallback=True)
            if res:
                return res
        return None

    resolved_remote = await asyncio.to_thread(_fetch_remote)
    if resolved_remote:
        return blob_store.build_range_response(request, resolved_remote)

    # For sparse XYZ/TMS tiles under /orthophoto_tiles/{z}/{x}/{y}.png, return 1x1 transparent PNG if not in cloud storage
    if "/orthophoto_tiles/" in clean and clean.lower().endswith(".png"):
        return Response(content=EMPTY_TILE_PNG, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    if clean.endswith("/orthophoto_tiles/tilemapresource.xml"):
        return Response(content=FALLBACK_TMS_XML, media_type="application/xml", headers={"Cache-Control": "public, max-age=300"})

    if clean.lower().endswith("report.pdf"):
        store = get_store()
        folder_prefix = clean.split("/")[0] if "/" in clean else ""
        pid_match = int(folder_prefix.split("-")[0]) if folder_prefix and folder_prefix.split("-")[0].isdigit() else 0
        proj_obj = store.get_project(pid_match) if pid_match > 0 else {"ID": pid_match, "NAME": folder_prefix or "Task"}
        files_list = store.get_project_files(pid_match) if pid_match > 0 else []
        val_row = next((v for v in store.tables.get("dbo.WEB_PROJECTS_VALUES", []) if int(v.get("PROJECT_ID") or 0) == pid_match), None)
        pdf_bytes = store._build_quality_report_pdf(proj_obj or {"ID": pid_match, "NAME": "Photogrammetry Task"}, files_list, val_row)
        try:
            blob_store.save_bytes("imagenes", clean, pdf_bytes)
        except Exception as exc:
            logger.warning("Failed to cache generated report.pdf for %s: %s", clean, exc)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="MosaicEarth_Quality_Report_{pid_match or "Task"}.pdf"'},
        )

    if clean.lower().endswith(("odm_orthophoto.webp", "odm_orthophoto.png", "orthophoto.png", "preview.png", "thumb.webp")):
        fallback_img = LIVE_WEB_ROOT / "Resources" / "mosaicearth-monogram-256.png"
        if fallback_img.is_file():
            return FileResponse(str(fallback_img), media_type="image/png", headers={"Cache-Control": "public, max-age=300"})
        return Response(content=EMPTY_TILE_PNG, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})

    raise HTTPException(status_code=404, detail=f"Processed asset file not found: {clean}")


@app.api_route("/ExternalAssetFile.ashx", methods=["GET", "HEAD"])
async def handler_external_asset_file(
    request: Request,
    project_id: int = 0,
    asset_id: int = 0,
    projectId: int = 0,
    assetId: int = 0,
    mode: str = "preview",
):
    project_id = project_id or projectId
    asset_id = asset_id or assetId
    store = get_store()
    store.refresh_processing_state()
    ext = store.get_external_asset(project_id, asset_id) if project_id > 0 else store.get_external_asset_by_id(asset_id)
    if not ext:
        raise HTTPException(status_code=404, detail="External asset not found")
    blob_store = get_blob_store()
    pid = int(ext.get("PROJECT_ID") or project_id)
    aid = int(ext.get("ID") or asset_id)
    proj_row = store.get_project(pid) or {}
    stored_name = str(ext.get("STORED_FILE_NAME") or ext.get("SOURCE_FILE_NAME") or "")
    mode_low = (mode or "preview").lower()
    dl_flag = str(request.query_params.get("download") or "").lower() in ("1", "true", "yes")
    is_gs_asset = (
        str(ext.get("SOURCE_ORIGIN") or "") == "gaussian"
        or str(ext.get("ASSET_SUBTYPE") or "") == "gaussian_splat"
    )
    is_original_or_bundle = (
        mode_low in ("original", "source", "master", "bundle", "download_bundle", "download")
        or dl_flag
    )
    # Enforce ACL on ALL modes: private assets/projects require owner/admin auth even for derived/metadata/preview!
    # Master/original/bundle downloads additionally require Marketplace purchase if price > 0.
    _enforce_project_and_asset_acl(
        request,
        store,
        project_row=proj_row,
        ext_asset=ext,
        require_download_rights=is_original_or_bundle,
    )
    from .asset_uploads import asset_output_available
    if mode_low not in ("original", "source", "master") and not asset_output_available(ext):
        raise HTTPException(status_code=404, detail="No processed output is available.")
    if mode_low in ("bundle", "download_bundle") or (is_gs_asset and (mode_low == "download" or (dl_flag and mode_low == "preview"))):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            bundle_items = [
                ("model.ply", [f"external/{pid}/{aid}/model.ply", f"external/{pid}/{aid}/point_cloud.ply"]),
                ("model.spz", [f"external/{pid}/{aid}/model.spz"]),
                ("transform.json", [f"external/{pid}/{aid}/transform.json"]),
                ("quality/benchmark.json", [f"external/{pid}/{aid}/splat_benchmark.json"]),
                ("quality/gate.json", [f"external/{pid}/{aid}/commercial_quality_gate.json"]),
                ("quality/reference.png", [f"external/{pid}/{aid}/validation/reference.png"]),
                ("quality/rendered.png", [f"external/{pid}/{aid}/validation/rendered.png"]),
                ("geo_alignment.txt", [f"external/{pid}/{aid}/geo_alignment.txt"]),
                ("splat_tileset/tileset.json", [f"external/{pid}/{aid}/tileset.json"]),
                ("splat_tileset/tile_0.glb", [f"external/{pid}/{aid}/tile_0.glb", f"external/{pid}/{aid}/model.glb"]),
            ]
            for arcname, rel_list in bundle_items:
                file_bytes = None
                for r_cand in rel_list:
                    for cont in ("results", "assets"):
                        loc = blob_store.resolve_blob_path(cont, r_cand, allow_remote_fallback=True)
                        if loc and loc.is_file():
                            file_bytes = loc.read_bytes()
                            break
                    if file_bytes is not None:
                        break
                if file_bytes is not None:
                    zf.writestr(arcname, file_bytes)
        zip_data = buf.getvalue()
        if zip_data:
            safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(ext.get("TITLE") or f"splat_{aid}")).strip("_") or f"splat_{aid}"
            return Response(
                content=zip_data,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_slug}_3dgs_original_results.zip"',
                    "Access-Control-Allow-Origin": "*",
                },
            )

    mode_candidates: List[str] = []
    is_original_request = mode_low in ("original", "source", "master")
    if is_original_request:
        orig_rel = str(ext.get("STORAGE_RELATIVE_PATH") or ext.get("SOURCE_BLOB_NAME") or "")
        if orig_rel:
            mode_candidates.append(orig_rel)
        if stored_name:
            mode_candidates.append(f"external/{pid}/{aid}/source/{stored_name}")
            mode_candidates.append(f"external/{pid}/source/{stored_name}")
    elif mode_low == "geojson":
        mode_candidates.append(f"external/{pid}/{aid}/overlay.geojson")
        mode_candidates.append(f"external/{pid}/{aid}/derived/vectors/overlay.geojson")
        mode_candidates.append(f"external/{pid}/{aid}/viewer/data.geojson")
        disp_prefix = str(ext.get("DISPLAY_BLOB_PREFIX") or "").rstrip("/")
        disp_entry = str(ext.get("DISPLAY_ENTRY_PATH") or "").lstrip("/")
        if disp_prefix and disp_entry:
            mode_candidates.append(f"{disp_prefix}/{disp_entry}")
    elif mode_low == "vector_layer":
        layer_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(request.query_params.get("layer") or "layer_0")).strip("_")
        mode_candidates.append(f"external/{pid}/{aid}/derived/vectors/layers/{layer_slug}.geojson")
    elif mode_low == "splat":
        mode_candidates.append(f"external/{pid}/{aid}/model.splat")
    elif mode_low == "spz":
        mode_candidates.append(f"external/{pid}/{aid}/model.spz")
    elif mode_low == "ply":
        mode_candidates.append(f"external/{pid}/{aid}/model.ply")
        mode_candidates.append(f"external/{pid}/{aid}/point_cloud.ply")
    elif mode_low in ("transform", "metadata_transform"):
        mode_candidates.append(f"external/{pid}/{aid}/metadata/transform.json")
        mode_candidates.append(f"external/{pid}/{aid}/transform.json")
    elif mode_low == "metadata_asset":
        mode_candidates.append(f"external/{pid}/{aid}/metadata/asset.json")
    elif mode_low == "quality_report":
        mode_candidates.append(f"external/{pid}/{aid}/splat_benchmark.json")
    elif mode_low in ("validation_reference", "validation_rendered"):
        name = "reference.png" if mode_low == "validation_reference" else "rendered.png"
        mode_candidates.append(f"external/{pid}/{aid}/validation/{name}")
    elif mode_low == "raster_bounds":
        mode_candidates.append(f"external/{pid}/{aid}/bounds.json")
    elif mode_low == "metadata_crs":
        mode_candidates.append(f"external/{pid}/{aid}/metadata/crs.json")
    elif mode_low == "metadata_derivatives":
        mode_candidates.append(f"external/{pid}/{aid}/metadata/derivatives.json")
    elif mode_low == "elevation_color_relief":
        mode_candidates.append(f"external/{pid}/{aid}/derived/elevation/color_relief.png")
        mode_candidates.append(f"external/{pid}/{aid}/preview.png")
    elif mode_low == "elevation_hillshade":
        mode_candidates.append(f"external/{pid}/{aid}/derived/elevation/hillshade.png")
    elif mode_low == "terrain_grid":
        mode_candidates.append(f"external/{pid}/{aid}/derived/terrain/elevation_grid.json")
    elif mode_low == "terrain_tile":
        try:
            tz, tx, ty = (int(request.query_params.get(k) or 0) for k in ("z", "x", "y"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid terrain tile coordinates")
        if not 0 <= tz <= 7 or not 0 <= tx < 2**tz or not 0 <= ty < 2**tz:
            raise HTTPException(status_code=400, detail="Invalid terrain tile coordinates")
        rel_tile = f"external/{pid}/{aid}/derived/terrain/tiles/{tz}_{tx}_{ty}.json"
        mode_candidates.append(rel_tile)
    elif mode_low == "terrain_layer":
        mode_candidates.append(f"external/{pid}/{aid}/derived/terrain/layer.json")
    elif mode_low == "geo_alignment":
        mode_candidates.append(f"external/{pid}/{aid}/geo_alignment.txt")
    elif mode_low == "glb":
        mode_candidates.append(f"external/{pid}/{aid}/tile_0.glb")
        mode_candidates.append(f"external/{pid}/{aid}/model.glb")
        mode_candidates.append(f"external/{pid}/{aid}/derived/optimized/model.glb")
    elif mode_low in ("preview_webp", "webp"):
        mode_candidates.append(f"external/{pid}/{aid}/derived/preview/preview.webp")
        mode_candidates.append(f"external/{pid}/{aid}/preview.png")
    elif mode_low == "preview_png":
        mode_candidates.append(f"external/{pid}/{aid}/preview.png")
    elif mode_low in ("derived", "metadata"):
        sub_rel = str(request.query_params.get("path") or "").strip().lstrip("/")
        if sub_rel and ".." not in sub_rel.split("/") and not sub_rel.startswith("source/"):
            mode_candidates.append(f"external/{pid}/{aid}/{sub_rel}")

    if is_original_request:
        candidates = mode_candidates
    else:
        # Never expose source/ master files when mode is non-original (e.g. preview/derived)
        prev_rel = str(ext.get("PREVIEW_RELATIVE_PATH") or "")
        disp_prefix = str(ext.get("DISPLAY_BLOB_PREFIX") or "").rstrip("/")
        disp_entry = str(ext.get("DISPLAY_ENTRY_PATH") or "").lstrip("/")
        safe_fallbacks = [f"external/{pid}/{aid}/tileset.json"]
        if disp_prefix and disp_entry and "/source/" not in f"{disp_prefix}/{disp_entry}":
            safe_fallbacks.insert(0, f"{disp_prefix}/{disp_entry}")
        if prev_rel and "/source/" not in prev_rel:
            safe_fallbacks.insert(0, prev_rel)
        candidates = mode_candidates if mode_candidates else safe_fallbacks
    dl_fname = None
    if is_original_request:
        dl_fname = str(ext.get("SOURCE_FILE_NAME") or stored_name or f"asset-{aid}.bin")
    elif dl_flag and candidates:
        dl_fname = Path(candidates[0]).name
    for rel in candidates:
        if not rel:
            continue
        for container in ("assets", "results"):
            resolved = blob_store.resolve_blob_path(container, rel, allow_remote_fallback=False)
            if resolved:
                resp = blob_store.build_range_response(request, resolved, download_filename=dl_fname)
                if ext.get("SOURCE_SHA256") and is_original_request:
                    resp.headers["X-MosaicEarth-Source-SHA256"] = str(ext["SOURCE_SHA256"])
                return resp

    import asyncio
    remote_ext_stream = await asyncio.to_thread(
        blob_store.stream_first_available_remote,
        request,
        ("assets", "results"),
        [c for c in candidates if c],
        dl_fname,
    )
    if remote_ext_stream is not None:
        if ext.get("SOURCE_SHA256") and is_original_request:
            remote_ext_stream.headers["X-MosaicEarth-Source-SHA256"] = str(ext["SOURCE_SHA256"])
        return remote_ext_stream

    if mode_low == "terrain_tile":
        # Published tiles are served above. Only a genuinely missing deep tile
        # reads the master, outside the event loop and under a per-asset lock.
        def generate_terrain_tile():
            from .geospatial_pipeline import render_dynamic_terrain_tile_for_external_asset
            from .processing_architecture import cross_process_file_lock
            lock = blob_store.blobs_root / "locks" / f"terrain_tile_{pid}_{aid}.lock"
            with cross_process_file_lock(lock):
                res_asset_dir = blob_store.blobs_root / "results" / "external" / str(pid) / str(aid)
                metadata_rel = f"external/{pid}/{aid}/derived/terrain/elevation_grid.json"
                blob_store.ensure_local("results", metadata_rel, force_download=True)
                original = str(ext.get("STORAGE_RELATIVE_PATH") or "")
                source = blob_store.ensure_local("assets", original, force_download=True) if original else None
                if not source:
                    return None
                return render_dynamic_terrain_tile_for_external_asset(res_asset_dir, tz, tx, ty, source_path=source)
        generated = await asyncio.to_thread(generate_terrain_tile)
        if generated:
            return JSONResponse(generated)
        raise HTTPException(status_code=404, detail="Terrain tile unavailable")

    def _fetch_ext_remote() -> Optional[Path]:
        for rel in candidates:
            if not rel:
                continue
            for container in ("assets", "results"):
                res = blob_store.ensure_local(container, rel, force_download=True)
                if res:
                    return res
        return None

    resolved_remote = await asyncio.to_thread(_fetch_ext_remote)
    if resolved_remote:
        resp_rem = blob_store.build_range_response(request, resolved_remote, download_filename=dl_fname)
        if ext.get("SOURCE_SHA256") and is_original_request:
            resp_rem.headers["X-MosaicEarth-Source-SHA256"] = str(ext["SOURCE_SHA256"])
        return resp_rem
    raise HTTPException(status_code=404, detail="External asset binary not found")


@app.api_route("/ExternalAsset3DTile.ashx", methods=["GET", "HEAD"])
@app.api_route("/ExternalAsset3DTile.ashx/{project_id:int}/{asset_id:int}/{file:path}", methods=["GET", "HEAD"])
async def handler_external_asset_3d_tile(
    request: Request,
    project_id: int = 0,
    asset_id: int = 0,
    file: str = "tileset.json",
):
    store = get_store()
    from .asset_uploads import asset_output_available

    ext = store.get_external_asset(project_id, asset_id)
    if not ext or not asset_output_available(ext):
        store.refresh_processing_state()
        ext = store.get_external_asset(project_id, asset_id)
    if not ext:
        raise HTTPException(status_code=404, detail="External asset not found")
    pid = int(ext.get("PROJECT_ID") or project_id)
    proj_row = store.get_project(pid) or {}
    clean_file = file.lstrip("/")
    if ".." in clean_file.split("/"):
        raise HTTPException(status_code=400, detail="Invalid tile path")
    extracted_share_token = ""
    f_segs = [s for s in clean_file.split("/") if s]
    if len(f_segs) >= 3 and f_segs[0].lower() in ("map", "share"):
        if f_segs[0].lower() == "share" and len(f_segs) >= 5:
            extracted_share_token = f_segs[3]
            clean_file = "/".join(f_segs[4:])
        else:
            clean_file = "/".join(f_segs[2:])
    if "source" in [s.lower() for s in clean_file.split("/") if s]:
        raise HTTPException(status_code=403, detail="Master source files cannot be accessed via 3D Tile streaming")
    _enforce_project_and_asset_acl(
        request,
        store,
        project_row=proj_row,
        ext_asset=ext,
        require_download_rights=False,
        share_token=extracted_share_token,
    )
    if not asset_output_available(ext):
        raise HTTPException(status_code=404, detail="No processed output is available.")
    candidates = [
        f"external/{pid}/{asset_id}/{clean_file}",
        f"external/{pid}/viewer/{clean_file}",
    ]
    disp_prefix = str(ext.get("DISPLAY_BLOB_PREFIX") or "").replace("\\", "/").rstrip("/")
    if disp_prefix and "/source/" not in disp_prefix:
        candidates.append(f"{disp_prefix}/{clean_file}")
    base_rel = str(ext.get("PREVIEW_RELATIVE_PATH") or ext.get("STORAGE_RELATIVE_PATH") or "").replace("\\", "/")
    if base_rel and "/source/" not in base_rel:
        base_dir = base_rel.rsplit("/", 1)[0] if "/" in base_rel else base_rel
        candidates.append(f"{base_dir}/{clean_file}")
    blob_store = get_blob_store()
    is_tileset_json = clean_file.lower().endswith("tileset.json")
    entry_file = base_rel.removeprefix(f"external/{pid}/{asset_id}/")
    if is_tileset_json and clean_file == entry_file:
        from .gis_splat_engine import _build_enu_to_ecef_matrix
        for target_rel in candidates:
            for container in ("results", "assets"):
                raw_ts = blob_store.read_bytes_from_cloud(container, target_rel)
                if not raw_ts:
                    local_ts = blob_store.resolve_blob_path(container, target_rel, allow_remote_fallback=False)
                    if local_ts and local_ts.is_file():
                        raw_ts = local_ts.read_bytes()
                if raw_ts:
                    try:
                        ts_obj = json.loads(raw_ts.decode("utf-8"))
                        if isinstance(ts_obj.get("root"), dict):
                            lat_v = float(ext["LATITUDE"]) if ext.get("LATITUDE") is not None else 41.8205
                            lon_v = float(ext["LONGITUDE"]) if ext.get("LONGITUDE") is not None else 1.8676
                            alt_v = float(ext.get("ALTITUDE") or 0.0)
                            heading_v = float(ext.get("HEADING") or 0.0)
                            pitch_v = float(ext.get("PITCH") or 0.0)
                            roll_v = float(ext.get("ROLL") or 0.0)
                            scale_v = float(ext.get("SCALE") or 1.0)
                            placement = _build_enu_to_ecef_matrix(
                                lat_v,
                                lon_v,
                                alt_v,
                                heading_deg=heading_v,
                                pitch_deg=pitch_v,
                                roll_deg=roll_v,
                                scale=scale_v,
                            )
                            original = ts_obj["root"].get("transform")
                            if ext.get("ASSET_CLASS") == "CESIUM_3D_TILES" and ext.get("HAS_EMBEDDED_GEOREF"):
                                # Preserve the package's rotation, axis convention and
                                # scale. A placement edit is a delta around its origin.
                                if isinstance(original, list) and len(original) == 16:
                                    import numpy as np
                                    from pyproj import Transformer
                                    old_lon, old_lat, old_alt = Transformer.from_crs(4978, 4979, always_xy=True).transform(*original[12:15])
                                    reference = np.array(_build_enu_to_ecef_matrix(old_lat, old_lon, old_alt)).reshape(4, 4, order="F")
                                    native = np.array(original).reshape(4, 4, order="F")
                                    changed = np.array(placement).reshape(4, 4, order="F") @ np.linalg.inv(reference) @ native
                                    ts_obj["root"]["transform"] = changed.flatten(order="F").tolist()
                                # Region-based packages already use global positions.
                            else:
                                ts_obj["root"]["transform"] = placement
                        return Response(
                            content=json.dumps(ts_obj).encode("utf-8"),
                            media_type="application/json",
                            headers={
                                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                                "Access-Control-Allow-Origin": "*",
                            },
                        )
                    except Exception as exc:
                        logger.warning("Failed to inject dynamic transform into tileset %s: %s", target_rel, exc)

    for target_rel in candidates:
        for container in ("results", "assets"):
            resolved = blob_store.resolve_blob_path(container, target_rel, allow_remote_fallback=False)
            if resolved:
                return blob_store.build_range_response(request, resolved)

    import asyncio
    remote_tile_stream = await asyncio.to_thread(
        blob_store.stream_first_available_remote,
        request,
        ("results", "assets"),
        candidates,
    )
    if remote_tile_stream is not None:
        return remote_tile_stream

    def _fetch_tile_remote() -> Optional[Path]:
        for target_rel in candidates:
            for container in ("results", "assets"):
                res = blob_store.resolve_blob_path(container, target_rel, allow_remote_fallback=True)
                if res:
                    return res
        return None

    resolved_remote = await asyncio.to_thread(_fetch_tile_remote)
    if resolved_remote:
        return blob_store.build_range_response(request, resolved_remote)
    raise HTTPException(status_code=404, detail=f"3D Tile file not found: {clean_file}")


@app.get("/ExternalAssetViewer.ashx")
@app.get("/ExternalAssetViewer.aspx")
@app.get("/Client/ExternalAssetViewer.aspx")
async def handler_external_asset_viewer(
    request: Request,
    project_id: int = 0,
    asset_id: int = 0,
    z: Optional[int] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
):
    store = get_store()
    ext = store.get_external_asset(project_id, asset_id) if project_id > 0 else store.get_external_asset_by_id(asset_id)
    if not ext:
        raise HTTPException(status_code=404, detail="External asset not found")
    if ext:
        pid_chk = int(ext.get("PROJECT_ID") or project_id)
        proj_chk = store.get_project(pid_chk) or {}
        _enforce_project_and_asset_acl(
            request,
            store,
            project_row=proj_chk,
            ext_asset=ext,
            require_download_rights=False,
        )
    if z is not None and x is not None and y is not None:
        blob_store = get_blob_store()
        if not 0 <= z <= 22 or not 0 <= x < 2**z or not 0 <= y < 2**z:
            raise HTTPException(status_code=400, detail="Invalid tile coordinates")
        tile_rel = f"external/{int(ext['PROJECT_ID'])}/{int(ext['ID'])}/{z}/{x}/{y}.png"
        for container in ("results", "assets"):
            resolved = blob_store.resolve_blob_path(container, tile_rel, allow_remote_fallback=False)
            if resolved:
                return blob_store.build_range_response(request, resolved)
        import asyncio
        rem_tile = await asyncio.to_thread(
            blob_store.stream_first_available_remote,
            request,
            ("results", "assets"),
            (tile_rel,),
        )
        if rem_tile is not None:
            return rem_tile
        # Dynamic on-demand XYZ tile rendering from the master/normalized GeoTIFF if within bounds
        if ext:
            from .gis_splat_engine import render_dynamic_xyz_tile_for_external_asset
            dyn_png = await asyncio.to_thread(
                render_dynamic_xyz_tile_for_external_asset,
                blob_store,
                int(ext.get("PROJECT_ID") or project_id),
                int(ext.get("ID") or asset_id),
                ext,
                int(z),
                int(x),
                int(y),
            )
            if dyn_png:
                return Response(content=dyn_png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
        return Response(content=EMPTY_TILE_PNG, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
    html_doc = render_external_asset_detail_page(project_id, asset_id, user=get_current_user(request))
    if not html_doc:
        raise HTTPException(status_code=404, detail="External asset not found")
    return HTMLResponse(html_doc)


@app.get("/MosaicAssetDownload.ashx")
@app.get("/ProjectSourceDownload.ashx")
async def handler_mosaic_asset_download(
    request: Request,
    source: str = "processed",
    publishId: int = 0,
    projectId: int = 0,
    assetId: int = 0,
    fileTypeId: int = 2,
):
    store = get_store()
    blob_store = get_blob_store()
    user = get_current_user(request)
    uid = int(user["ID"]) if user else 0

    if source == "external" or assetId > 0:
        ext = store.get_external_asset(projectId, assetId) if projectId > 0 else store.get_external_asset_by_id(assetId)
        if not ext:
            raise HTTPException(status_code=404, detail="External asset not found")
        pid_resolved = int(ext.get("PROJECT_ID") or projectId)
        proj_row = store.get_project(pid_resolved) or {}
        _enforce_project_and_asset_acl(
            request,
            store,
            project_row=proj_row,
            ext_asset=ext,
            require_download_rights=True,
        )
        req_fmt = str(request.query_params.get("format") or "").lower()
        is_multi_view_gs_task = (
            str(ext.get("SOURCE_ORIGIN") or "") == "gaussian"
            and Path(str(ext.get("SOURCE_FILE_NAME") or "")).suffix.lower() in (".jpg", ".jpeg", ".png", ".mp4", ".mov", ".webm")
        )
        if req_fmt in ("bundle", "spz", "ply", "splat", "transform", "geo_alignment") or (is_multi_view_gs_task and not req_fmt):
            return await handler_external_asset_file(
                request,
                project_id=pid_resolved,
                asset_id=int(ext.get("ID") or assetId),
                mode=req_fmt or "bundle",
            )
        rel = str(ext.get("STORAGE_RELATIVE_PATH") or ext.get("SOURCE_BLOB_NAME") or "")
        stored_name_dl = str(ext.get("STORED_FILE_NAME") or ext.get("SOURCE_FILE_NAME") or "")
        dl_candidates = [c for c in (
            rel,
            f"external/{pid_resolved}/{int(ext.get('ID') or assetId)}/source/{stored_name_dl}" if stored_name_dl else "",
            f"external/{pid_resolved}/source/{stored_name_dl}" if stored_name_dl else "",
        ) if c]
        fname = str(ext.get("SOURCE_FILE_NAME") or ext.get("ORIGINAL_FILE_NAME") or stored_name_dl or f"mosaicasset-{assetId}.bin")
        for cand_rel in dl_candidates:
            for container in ("assets", "results"):
                resolved = blob_store.resolve_blob_path(container, cand_rel, allow_remote_fallback=False)
                if resolved:
                    resp = blob_store.build_range_response(request, resolved, download_filename=fname)
                    if ext.get("SOURCE_SHA256"):
                        resp.headers["X-MosaicEarth-Source-SHA256"] = str(ext["SOURCE_SHA256"])
                    return resp
        import asyncio
        rem_dl = await asyncio.to_thread(
            blob_store.stream_first_available_remote,
            request,
            ("assets", "results"),
            dl_candidates,
            fname,
        )
        if rem_dl is not None:
            return rem_dl
    else:
        proj = store.get_project(projectId)
        if not proj and publishId > 0:
            for p_row in store.tables.get("dbo.WEB_PUBLICATIONS", []):
                if int(p_row.get("ID") or p_row.get("ID_PROJECT_PUBLISH") or 0) == publishId:
                    projectId = int(p_row.get("PROJECT_ID") or p_row.get("ID_PROJECT") or 0)
                    fileTypeId = int(p_row.get("FILE_TYPE_ID") or fileTypeId)
                    proj = store.get_project(projectId)
                    break
        if proj:
            _enforce_project_and_asset_acl(
                request,
                store,
                project_row=proj,
                ext_asset=None,
                require_download_rights=True,
            )
            folder = str(proj.get("FOLDER") or "")
            # NEVER fall back to visual derivatives (tilemapresource.xml or tileset.json) when downloading a master!
            candidates = (
                [
                    f"{folder}/procesado/download/orthophoto.tif",
                    f"{folder}/procesado/geotiff/odm_orthophoto.tif",
                ]
                if fileTypeId == 2
                else [
                    f"{folder}/procesado/download/pointcloud.laz",
                    f"{folder}/procesado/download/pointcloud.zip",
                ]
                if fileTypeId == 1
                else [
                    f"{folder}/procesado/download/dsm.tif",
                ]
                if fileTypeId == 4
                else [
                    f"{folder}/procesado/download/dtm.tif",
                ]
                if fileTypeId == 5
                else [
                    f"{folder}/procesado/download/3dmodel.zip",
                    f"{folder}/3d-model.zip",
                ]
            )
            for cand in candidates:
                for container in ("imagenes", "results", "assets"):
                    resolved = blob_store.resolve_blob_path(container, cand, allow_remote_fallback=False)
                    if resolved:
                        return blob_store.build_range_response(request, resolved, download_filename=resolved.name)
            import asyncio
            dl_name = candidates[0].rsplit("/", 1)[-1]
            rem_pkg = await asyncio.to_thread(
                blob_store.stream_first_available_remote,
                request,
                ("imagenes", "results", "assets"),
                candidates,
                dl_name,
            )
            if rem_pkg is not None:
                return rem_pkg
    raise HTTPException(status_code=404, detail="Requested source package not found on disk")


# ============================================================================
# ASSET ANNOTATIONS (WITH HTTP 409 OPTIMISTIC CONCURRENCY)
# ============================================================================
@app.api_route("/AssetAnnotations.ashx", methods=["GET", "POST", "PUT", "DELETE"])
async def handle_asset_annotations(request: Request):
    store = get_store()
    asset_key = (
        request.query_params.get("asset")
        or request.query_params.get("assetKey")
        or request.query_params.get("scopeId")
        or "default"
    )
    user = get_current_user(request)
    can_edit = bool(user)

    if request.method == "GET":
        rows = store.list_annotations(asset_key, is_owner=can_edit)
        return JSONResponse({
            "ok": True,
            "assetKey": asset_key,
            "annotations": rows,
            "Items": rows,
            "CanEdit": can_edit,
            "CanUseLocalAnchors": True,
        })

    try:
        body = await request.json()
    except Exception:
        body = {}

    if body.get("assetKey") or body.get("asset"):
        asset_key = str(body.get("assetKey") or body.get("asset"))
    action = str(body.get("Action") or body.get("action") or request.method).upper()
    is_delete = action == "DELETE" or request.method == "DELETE"
    item_payload = body.get("Item") if isinstance(body.get("Item"), dict) else body
    uid = int(user["ID"]) if user else int(body.get("ownerUserId") or 1)
    proj_id = int(body.get("projectId") or 0)

    status_code, ann_id, err_msg = store.save_annotation(
        asset_key=asset_key,
        project_id=proj_id,
        author_id=uid,
        payload=item_payload,
        delete=is_delete,
    )
    if status_code == 409:
        return JSONResponse(
            {"ok": False, "conflict": True, "Error": err_msg or "Annotation revision conflict (HTTP 409)", "error": err_msg or "Annotation revision conflict (HTTP 409)"},
            status_code=409,
        )
    if status_code != 200:
        return JSONResponse({"ok": False, "Error": err_msg or "Invalid annotation", "error": err_msg or "Invalid annotation"}, status_code=status_code)
    rows = store.list_annotations(asset_key, is_owner=True)
    return JSONResponse({
        "ok": True,
        "id": ann_id,
        "annotations": rows,
        "Items": rows,
        "CanEdit": True,
        "CanUseLocalAnchors": True,
    })


# ============================================================================
# WEBODM DEVICE PAIRING (HTTP 428) & IMPORT ENDPOINTS
# ============================================================================
@app.api_route("/WebOdmDevice.ashx", methods=["GET", "POST"])
async def handle_webodm_device(request: Request):
    store = get_store()
    try:
        body = await request.json() if request.method == "POST" else {}
    except Exception:
        body = {}

    action = (body.get("action") or request.query_params.get("action") or "start").lower()
    if action in ("start", "device_code", "request"):
        url = str(body.get("webodm_url") or request.query_params.get("webodm_url") or "http://localhost:8000")
        info = store.create_webodm_device_code(url)
        info["verification_uri"] = f"{get_public_site_url()}/WebODMConnect.aspx?code={info['user_code']}"
        return JSONResponse(info)

    if action in ("poll", "token"):
        device_code = str(body.get("device_code") or request.query_params.get("device_code") or "")
        result, status_code = store.poll_webodm_device_code(device_code)
        return JSONResponse(result, status_code=status_code)

    return JSONResponse({"ok": True, "service": "MosaicEarth WebODM Device Gateway"})


@app.api_route("/WebOdmImport.ashx", methods=["GET", "POST"])
async def handle_webodm_import(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Unauthorized WebODM bearer token"}, status_code=401)
    if request.method == "GET":
        return JSONResponse(
            {
                "ok": True,
                "user": {"id": user.get("ID"), "username": user.get("DisplayName"), "email": user.get("EMAIL")},
            }
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    new_pid = get_store().create_project(
        user_id=int(user["ID"]),
        name=str(body.get("name") or "WebODM Imported Survey"),
        kind="external",
    )
    proj = get_store().get_project(new_pid) or {"ID": new_pid, "FOLDER": f"{new_pid}-webodm-imported-survey"}
    return JSONResponse({"ok": True, "projectId": proj["ID"], "folder": proj.get("FOLDER")})


# ============================================================================
# MOSAICEARTH DESKTOP HYBRID & CLOUD SYNC ENDPOINTS (LOCAL PC + CLOUD)
# ============================================================================
def _detect_local_lan_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


@app.get("/api/desktop/nodeodm-status", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_nodeodm_status():
    store = get_store()
    info = store.check_nodeodm_engine("")
    return JSONResponse({"ok": True, **info})


@app.post("/api/desktop/start-nodeodm", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_start_nodeodm():
    store = get_store()
    res = store.ensure_local_nodeodm_started()
    return JSONResponse(res)


@app.get("/api/desktop/share-origin", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_share_origin(request: Request):
    lan_ip = _detect_local_lan_ip()
    port = os.environ.get("MOSAICEARTH_DESKTOP_PORT") or str(request.url.port or 8095)
    return JSONResponse({
        "ok": True,
        "lanIp": lan_ip,
        "port": port,
        "shareOrigin": f"http://{lan_ip}:{port}",
    })


@app.post("/api/desktop/cloud-login", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_cloud_login(request: Request):
    store = get_store()
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    if not email or not password:
        return JSONResponse({"ok": False, "message": "Enter both your MosaicEarth Cloud email and password."})

    # Enforce Session-Bound Desktop License if desktop_license.json is present
    for lic_cand in (
        Path(os.environ.get("MOSAICEARTH_DATA_DIR", "")) / "desktop_license.json",
        Path(__file__).resolve().parent.parent / "desktop_license.json",
    ):
        if lic_cand.is_file():
            try:
                lic_obj = json.loads(lic_cand.read_text(encoding="utf-8", errors="ignore"))
                bound_email = str(lic_obj.get("licensed_email") or "").strip().lower()
                if bound_email and email.lower() != bound_email:
                    return JSONResponse({
                        "ok": False,
                        "message": f"This MosaicEarth Desktop installer is cryptographically licensed and locked to '{bound_email}'. It cannot be shared or used with another account.",
                    }, status_code=403)
            except Exception:
                pass
            break

    cloud_origin = os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{cloud_origin}/api/v2/desktop/cloud-auth",
                json={"email": email, "password": password},
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                u_info = data.get("user") or {}
                c_email = str(u_info.get("email") or email)
                c_uname = str(u_info.get("username") or email)
                c_plan = str(u_info.get("plan_code") or "free")
                acc = store.set_desktop_cloud_account(
                    email=c_email,
                    username=c_uname,
                    cloud_uid=int(u_info.get("id") or 0),
                    plan_code=c_plan,
                    token=str(data.get("token") or ""),
                    cloud_url=cloud_origin,
                )
                with store.lock:
                    for u_row in store.tables.get("dbo.USERS", []):
                        if int(u_row.get("ID") or 0) == 1:
                            u_row["EMAIL"] = c_email
                            u_row["NOMBRE"] = c_uname
                            u_row["NAME"] = c_uname
                            u_row["PLAN_KEY"] = c_plan
                            store.persist_table("dbo.USERS")
                            break
                return JSONResponse({"ok": True, "account": acc})
            return JSONResponse({"ok": False, "message": str(data.get("message") or "Invalid MosaicEarth Cloud credentials.")})
    except Exception as exc:
        # Fallback if running on same server
        res_local = store.authenticate_user(email, password)
        u_local = res_local[0] if isinstance(res_local, tuple) else res_local
        if u_local:
            uid_c = int(u_local["ID"])
            st_sum = store.get_user_storage_summary(uid_c)
            acc = store.set_desktop_cloud_account(
                email=str(u_local.get("EMAIL") or email),
                username=str(u_local.get("USERNAME") or u_local.get("NAME") or email),
                cloud_uid=uid_c,
                plan_code=str(st_sum.get("plan_code") or "free"),
                token=f"cloud_uid:{uid_c}",
                cloud_url=cloud_origin,
            )
            return JSONResponse({"ok": True, "account": acc})
        return JSONResponse({"ok": False, "message": f"Could not reach MosaicEarth Cloud ({cloud_origin}): {exc}"})


@app.post("/api/desktop/cloud-logout", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_cloud_logout():
    store = get_store()
    store.clear_desktop_cloud_account()
    with store.lock:
        for u_row in store.tables.get("dbo.USERS", []):
            if int(u_row.get("ID") or 0) == 1:
                u_row["EMAIL"] = "local@mosaicearth.desktop"
                u_row["NOMBRE"] = "Local"
                u_row["NAME"] = "Local"
                store.persist_table("dbo.USERS")
                break
    return JSONResponse({"ok": True})


@app.get("/api/desktop/cloud-workspace", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_cloud_workspace():
    store = get_store()
    acc = store.get_desktop_cloud_account()
    if not acc.get("logged_in"):
        return JSONResponse({"ok": False, "message": "Not signed in to MosaicEarth Cloud Account."})
    cloud_origin = str(acc.get("cloud_url") or os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app")).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{cloud_origin}/api/v2/desktop/cloud-workspace-data",
                json={"email": acc.get("email"), "cloud_uid": acc.get("cloud_uid"), "token": acc.get("token")},
            )
            if resp.status_code == 200:
                return JSONResponse(resp.json())
    except Exception:
        pass
    # Local fallback if same DB
    c_uid = int(acc.get("cloud_uid") or 0)
    if c_uid > 0:
        projs = store.list_myearth_hierarchy(c_uid).get("projects", [])
        mmaps = store.list_user_mymaps(c_uid)
        st_sum = store.get_user_storage_summary(c_uid)
        return JSONResponse({
            "ok": True,
            "account": {"email": acc.get("email"), "plan": str(st_sum.get("plan_code") or "free").lower()},
            "projects": [
                {
                    "id": int(p.get("ID") or 0),
                    "name": str(p.get("NAME") or "Project"),
                    "surveys": len(p.get("Surveys") or []),
                    "tasks": int(p.get("TotalTasksCount") or 0),
                }
                for p in projs
            ],
            "mymosaics": [
                {
                    "id": int(m.get("ID") or 0),
                    "name": str(m.get("NAME") or "MyMosaic"),
                    "visibility": str(m.get("VISIBILITY") or "public"),
                    "asset_count": int(m.get("PublicationCount") or 0),
                }
                for m in mmaps
            ],
        })
    return JSONResponse({"ok": False, "message": "Could not load Cloud workspace from app.mosaicearth.app."})


@app.post("/api/desktop/sync-to-cloud", dependencies=[Depends(_require_desktop_local)])
async def route_desktop_sync_to_cloud(request: Request):
    store = get_store()
    acc = store.get_desktop_cloud_account()
    if not acc.get("logged_in"):
        return JSONResponse({"ok": False, "message": "Sign in to your MosaicEarth Cloud Account first."}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    sync_type = str(body.get("type") or "project").strip().lower()
    item_id = int(body.get("id") or 0)
    cloud_origin = str(acc.get("cloud_url") or os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app")).rstrip("/")

    payload: Dict[str, Any] = {
        "email": acc.get("email"),
        "cloud_uid": acc.get("cloud_uid"),
        "token": acc.get("token"),
        "type": sync_type,
    }
    if sync_type == "mymosaic":
        mm = store.get_mymap(str(item_id))
        if not mm:
            return JSONResponse({"ok": False, "message": "Local MyMosaic not found."}, status_code=404)
        payload["mymosaic"] = {
            "name": str(mm.get("NAME") or f"MyMosaic {item_id}"),
            "visibility": str(mm.get("VISIBILITY") or "private"),
        }
    else:
        with store.lock:
            l1 = next((p for p in store.tables.get("dbo.MOSAICEARTH_PROJECTS", []) if int(p.get("ID") or 0) == item_id), None)
            if not l1:
                return JSONResponse({"ok": False, "message": "Local Project not found."}, status_code=404)
            surveys_out = []
            for s in store.tables.get("dbo.MOSAICEARTH_SURVEYS", []):
                if int(s.get("PARENT_PROJECT_ID") or 0) == item_id and s.get("ACTIVO", True):
                    sid = int(s.get("ID") or 0)
                    tasks_out = []
                    for t in store.tables.get("dbo.WEB_PROJECTS", []):
                        if int(t.get("SURVEY_ID") or 0) == sid and t.get("ACTIVO", True):
                            tasks_out.append({
                                "name": str(t.get("NAME") or "Task"),
                                "description": str(t.get("DESCRIPTION") or ""),
                                "state_id": int(t.get("STATE_ID") or 4),
                            })
                    surveys_out.append({
                        "name": str(s.get("NAME") or "01 Default Survey"),
                        "description": str(s.get("DESCRIPTION") or ""),
                        "tasks": tasks_out,
                    })
            payload["project"] = {
                "name": str(l1.get("NAME") or f"Project {item_id}"),
                "description": str(l1.get("DESCRIPTION") or ""),
                "surveys": surveys_out,
            }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{cloud_origin}/api/v2/desktop/receive-sync", json=payload)
            if resp.status_code == 200:
                return JSONResponse(resp.json())
            return JSONResponse({"ok": False, "message": f"Cloud returned HTTP {resp.status_code}"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"Failed to push item to Cloud: {exc}"})


@app.post("/api/v2/desktop/cloud-auth")
async def api_v2_desktop_cloud_auth(request: Request):
    store = get_store()
    store.refresh_account_state()
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    u, _ = store.authenticate_user(email, password)
    if not u:
        return JSONResponse({"ok": False, "message": "Invalid email or password on MosaicEarth Cloud."}, status_code=401)
    uid = int(u["ID"])
    st_sum = store.get_user_storage_summary(uid)
    return JSONResponse({
        "ok": True,
        "token": store.create_login_session(uid, LOGIN_SESSION_MAX_AGE),
        "user": {
            "id": uid,
            "email": str(u.get("EMAIL") or email),
            "username": str(u.get("USERNAME") or u.get("NAME") or email),
            "plan_code": str(st_sum.get("plan_code") or "free").lower(),
        },
    })


def _desktop_sync_user(store, body):
    if not isinstance(body, dict):
        return None
    uid = store.resolve_login_session(str(body.get("token") or ""))
    if not uid or str(body.get("cloud_uid") or "") != str(uid):
        return None
    user = store.get_user_by_id(uid)
    if user and user.get("ACTIVO", True) and not user.get("SESSION_REVOKED"):
        return user
    return None


@app.post("/api/v2/desktop/cloud-workspace-data")
async def api_v2_desktop_cloud_workspace_data(request: Request):
    store = get_store()
    try:
        body = await request.json()
    except Exception:
        body = {}
    u = _desktop_sync_user(store, body)
    if not u:
        return JSONResponse({"ok": False, "message": "Sign in to MosaicEarth Cloud again."}, status_code=401)
    uid = int(u["ID"])
    st_sum = store.get_user_storage_summary(uid)
    projs = store.list_myearth_hierarchy(uid).get("projects", [])
    mmaps = store.list_user_mymaps(uid)
    return JSONResponse({
        "ok": True,
        "account": {
            "id": uid,
            "email": str(u.get("EMAIL") or ""),
            "plan": str(st_sum.get("plan_code") or "free").lower(),
        },
        "projects": [
            {
                "id": int(p.get("ID") or 0),
                "name": str(p.get("NAME") or "Project"),
                "surveys": len(p.get("Surveys") or []),
                "tasks": int(p.get("TotalTasksCount") or 0),
            }
            for p in projs
        ],
        "mymosaics": [
            {
                "id": int(m.get("ID") or 0),
                "name": str(m.get("NAME") or "MyMosaic"),
                "visibility": str(m.get("VISIBILITY") or "public"),
                "asset_count": int(m.get("PublicationCount") or 0),
            }
            for m in mmaps
        ],
    })


@app.post("/api/v2/desktop/receive-sync")
async def api_v2_desktop_receive_sync(request: Request):
    return JSONResponse({"ok": False, "message": "Update your connector to synchronize files with /api/v2/sync/sessions. No files or projects were copied."}, status_code=410)


@app.get("/api/v2/desktop/public-catalog")
async def api_v2_desktop_public_catalog():
    """Returns live public globe publications, public external assets, and public MyMosaics for Desktop sync."""
    store = get_store()
    with store.lock:
        public_mosaics = [
            m for m in store.tables.get("dbo.WEB_MYMAPS", [])
            if m.get("ACTIVO", True) and str(m.get("VISIBILITY") or "public").lower() == "public"
            and not bool(m.get("IS_CONTAINER_PREVIEW"))
        ]
        public_mosaic_ids = {int(m["ID"]) for m in public_mosaics}
        pub_Ids = {
            int(p.get("PROJECT_ID") or 0)
            for p in store.tables.get("dbo.WEB_PUBLICATIONS", [])
            if int(p.get("STATUS_ID") or p.get("STATE_ID") or 0) == 2
        }
        return JSONResponse({
            "ok": True,
            "dbo.WEB_PUBLICATIONS": [
                p for p in store.tables.get("dbo.WEB_PUBLICATIONS", [])
                if int(p.get("STATUS_ID") or p.get("STATE_ID") or 0) == 2
            ],
            "dbo.WEB_PROJECTS": [
                {key: value for key, value in p.items()
                 if key not in {"CUSTOM_NODEODM_TOKEN", "ENGINE_NODEODM_URL", "LAST_ODM_LOG_LINES"}}
                for p in store.tables.get("dbo.WEB_PROJECTS", [])
                if int(p.get("ID") or 0) in pub_Ids
            ],
            "dbo.WEB_PROJECTS_LOCATION": [
                l for l in store.tables.get("dbo.WEB_PROJECTS_LOCATION", [])
                if int(l.get("PROJECT_ID") or 0) in pub_Ids
            ],
            "dbo.WEB_PROJECTS_VALUES": [
                v for v in store.tables.get("dbo.WEB_PROJECTS_VALUES", [])
                if int(v.get("PROJECT_ID") or 0) in pub_Ids
            ],
            "dbo.WEB_CESIUM_MODELS": [
                m for m in store.tables.get("dbo.WEB_CESIUM_MODELS", [])
                if int(m.get("Project_id") or m.get("PROJECT_ID") or m.get("ID_PROJECT") or 0) in pub_Ids
            ],
            "dbo.MOSAICEARTH_EXTERNAL_ASSETS": [
                a for a in store.tables.get("dbo.MOSAICEARTH_EXTERNAL_ASSETS", [])
                if str(a.get("VISIBILITY") or "").lower() == "public"
            ],
            "dbo.WEB_MYMAPS": public_mosaics,
            "dbo.WEB_MYMAPS_ITEMS": [
                item for item in store.tables.get("dbo.WEB_MYMAPS_ITEMS", [])
                if int(item.get("MYMAP_ID") or 0) in public_mosaic_ids
            ],
        })


@app.api_route("/api/v2/cloud-nodeodm", methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.api_route("/api/v2/cloud-nodeodm/{path_suffix:path}", methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def api_v2_cloud_nodeodm_retired():
    """Retire the provider passthrough. Cloud jobs must enter through the authenticated project workflow."""
    raise HTTPException(
        status_code=410,
        detail="Start cloud processing from your project in MosaicEarth Web.",
    )
