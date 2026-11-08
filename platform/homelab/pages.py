from .asset_metadata import format_coordinates
import html
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from .blob_store import get_blob_store

# Version the map bundle by content: public CDN browser TTLs can override
# revalidation headers, so an update must also change its URL.
MAP_RUNTIME_VERSION = hashlib.sha256(b"\0".join(
    Path(__file__).with_name(name).read_bytes()
    for name in ("MosaicMapSources.js", "MosaicCesiumRuntime.js")
)).hexdigest()[:16]

from .db_store import (
    DEFAULT_CATEGORIES,
    ICON_3D,
    ICON_CLOUD,
    ICON_ORTHOPHOTO,
    KNOWN_SOURCE_EXTENSIONS,
    _slugify,
    _try_float,
    family_display,
    get_store,
    subtype_display,
    to_slug,
)
from .interoperability import build_4dtwinmaps_world_link
from .seo_engine import (
    SUPPORTED_FAMILIES,
    SUPPORTED_REGIONS,
    build_breadcrumb_json_ld,
    build_dataset_json_ld,
    build_dynamic_og_image_url,
    build_external_asset_path,
    build_head_metadata_html,
    build_processed_asset_path,
    build_web_application_json_ld,
    get_active_categories,
    get_landing_site_url,
    get_public_directory_items,
    get_public_site_url,
    normalize_lang,
    render_server_catalogue_html,
    resolve_category,
    resolve_family,
    resolve_region,
)

CURRENT_LICENSE_TEXT = """MOSAICEARTH STANDARD GEOSPATIAL LICENSE (v1.0)
1. Grant of License: Upon valid acquisition on MosaicEarth, the Creator grants the Licensee a worldwide, non-exclusive, perpetual, royalty-free license to inspect, use, integrate, and create derivative engineering or visualization works from the acquired MosaicAsset.
2. Permitted Uses: GIS analysis, CAD/BIM integration, 3D digital twin simulation, environmental monitoring, academic research, municipal planning, and commercial presentations.
3. Restrictions: Licensee may not resell, redistribute, or sublicense the raw unmodified source files on competing geospatial marketplaces.
4. Attribution: Attribution to Creator and MosaicEarth (https://mosaicearth.app) is appreciated where technically feasible."""

SHORT_LICENSE_SUMMARY = "Perpetual worldwide license for GIS, CAD, 3D visualization & engineering projects. Raw resale prohibited."

# A new engine can reuse the same local port and browser profile. Version the
# script by its bytes so a cached previous Desktop cannot hide account changes.
DESKTOP_UI_REVISION = hashlib.sha256(
    (Path(__file__).resolve().parents[2] / "resources/platform-ui.js").read_bytes()
).hexdigest()[:16]


def render_home_master(
    head_extra: str,
    seo_head_html: str,
    body_content: str,
    scripts_extra: str = "",
    user: Optional[Dict[str, Any]] = None,
    lang: str = "en",
    wrap_form: bool = True,
) -> str:
    head_extra = head_extra.replace("__MOSAIC_MAP_RUNTIME_VERSION__", MAP_RUNTIME_VERSION)
    norm_lang = normalize_lang(lang)
    is_desktop_local = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
    cloud_acc = get_store().get_desktop_cloud_account() if is_desktop_local else {}
    raw_email = str((cloud_acc.get("email") if cloud_acc.get("logged_in") else "") or (user.get("EMAIL") if user else "") or (user.get("USERNAME") if user else "") or "")
    if is_desktop_local and raw_email == "local@mosaicearth.desktop":
        user_label = "Local · Cloud not connected"
    else:
        user_label = html.escape(raw_email) if user else ""
    has_unread = get_store().has_unread_notifications(int(user["ID"])) if user else False
    green_dot_style = ' style="display: inline;"' if has_unread else ' style="display: none;"'
    asset_base = "/ProcessedAssetFile.ashx/"
    is_fullscreen_map = 'class="container-fluid2"' in (body_content or "")
    body_cls = ' class="me-map-viewport"' if is_fullscreen_map else ""
    form_cls = ' class="me-map-form"' if is_fullscreen_map else ""
    layout_tag = "form" if wrap_form else "div"
    form_attrs = ' method="post" action="#"' if wrap_form else ""
    return f"""<!DOCTYPE html>
<html lang="{norm_lang}" xmlns="http://www.w3.org/1999/xhtml">
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
{seo_head_html}
    <link href="/Resources/mosaicearth-site-favicon.png" rel="icon" type="image/png" />
    <link href="/vendor/raleway/fonts.css" rel="stylesheet" />
    <link href="/vendor/fontawesome/css/all.css" rel="stylesheet" />
    <link rel="shortcut icon" href="/Resources/mosaicearth-site-favicon.png" />
    <link href="/assets/css/bootstrap.min.css" rel="stylesheet" />
    <link rel="stylesheet" href="/assets/css/master.css?v=4" />
    <link href="/admin/assets/css/plugins/sweetalert/sweetalert.css" rel="stylesheet" />
    <link href="/assets/css/plugins/toastr/toastr.min.css" rel="stylesheet" />
    <link href="/assets/css/plugins/datapicker/datepicker3.css" rel="stylesheet" />
{head_extra}
    <link rel="stylesheet" href="/assets/css/mosaic-cards.css?v=2" />
    <link rel="stylesheet" href="/assets/css/mosaic-responsive.css?v=20260923-map-r29" />
    <style>
        html, body {{ overflow-x: hidden !important; max-width: 100vw; }}
        #notificationsDiv {{ position: fixed; top: 0; bottom: 0; width: min(400px, 100vw); max-width: 100vw; z-index: 10100 !important; overflow-y: auto; overscroll-behavior: contain; }}
        #notificationsDiv .notification-toggle {{ min-width: 44px; min-height: 44px; }}
        /* Dark text preserves contrast on the established lime action color. */
        body .btn-primary, body .btn-primary:hover, body .btn-primary:focus,
        body .btn-primary:active, body .btn-primary:visited {{ color: #10170c !important; }}
        body .btn-primary:focus-visible {{ outline: 3px solid #f4f7f5; outline-offset: 3px; }}
        .product-box-buy .asset-detail-actions > .btn {{ flex: 0 0 auto !important; min-height: 44px; }}
        .border-nav, #menu-nav {{ position: relative; z-index: 10050 !important; width: 100%; max-width: 100vw; }}
        #menu-nav .navbar {{ padding: 6px 14px !important; position: relative; z-index: 10050 !important; max-width: 100vw; box-sizing: border-box; }}
        #menu-nav .navbar > .container-fluid {{ padding-left: 4px !important; padding-right: 4px !important; max-width: 100% !important; display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: nowrap; }}
        #menu-nav .navbar-brand {{ margin-right: 8px !important; flex-shrink: 0; white-space: nowrap; }}
        #menu-nav .navbar-collapse {{ flex-grow: 1; min-width: 0; }}
        #menu-nav .navbar-nav {{ flex-wrap: nowrap; gap: 2px; align-items: center; }}
        #menu-nav .navbar-nav .nav-item {{ margin-left: 3px !important; margin-right: 3px !important; white-space: nowrap; flex-shrink: 0; }}
        #menu-nav #navbarDropdownMenuLink {{
            padding: 5px 12px !important;
            max-width: 240px !important;
            min-width: 0 !important;
            width: auto !important;
            border-radius: 999px !important;
            background: #17201b !important;
            border: 1px solid rgba(153, 204, 0, 0.38) !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: space-between !important;
            gap: 6px !important;
        }}
        #menu-nav #usernameEmail {{
            max-width: 185px !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            white-space: nowrap !important;
            font-size: 12.5px !important;
            color: #eef5f0 !important;
            display: inline-block !important;
        }}
        #menu-nav .navbar-nav .nav-link.me-public-link {{
            font-size: 13.5px;
            color: #d8e2dc !important;
            padding: 6px 10px !important;
            white-space: nowrap;
            border-bottom: 2px solid transparent;
            transition: color 0.15s ease, border-color 0.15s ease;
        }}
        #menu-nav .navbar-nav .nav-link.me-public-link:hover {{
            color: #ffffff !important;
        }}
        #menu-nav .navbar-nav .nav-link.me-public-link.activeLink,
        #menu-nav .navbar-nav .nav-link.activeLink {{
            color: #99CC00 !important;
            font-weight: 700 !important;
            border-bottom: 2px solid #99CC00;
        }}
        .me-workspace-nav-group {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(153, 204, 0, 0.30);
            border-radius: 8px;
            padding: 4px 6px;
        }}
        .me-workspace-label {{
            font-family: OpenSansBold, Arial, sans-serif;
            font-size: 9.5px;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: #8fa099;
            padding: 0 5px 0 3px;
            user-select: none;
        }}
        .me-workspace-btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 12px !important;
            border-radius: 6px;
            font-family: OpenSansSemiBold, Arial, sans-serif;
            font-size: 13px !important;
            color: #e6efe8 !important;
            background: rgba(23, 33, 27, 0.85);
            border: 1px solid rgba(255, 255, 255, 0.12);
            text-decoration: none !important;
            white-space: nowrap;
            transition: all 0.15s ease;
        }}
        .me-workspace-btn svg {{
            width: 14px;
            height: 14px;
            flex-shrink: 0;
            color: #99CC00;
            transition: color 0.15s ease;
        }}
        .me-workspace-btn:hover {{
            background: rgba(153, 204, 0, 0.14);
            border-color: #99CC00;
            color: #ffffff !important;
        }}
        .me-workspace-btn.activeLink {{
            background: #99CC00 !important;
            color: #0b110d !important;
            border-color: #99CC00 !important;
            font-family: OpenSansBold, Arial, sans-serif;
            font-weight: 700 !important;
            box-shadow: 0 0 12px rgba(153, 204, 0, 0.25);
        }}
        .me-workspace-btn.activeLink svg {{
            color: #0b110d !important;
        }}
        .me-brand-mark {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 34px;
            height: 34px;
            background: #020302;
            border: 1px solid #303831;
            border-radius: 4px;
            font-family: OpenSansBold, Arial, sans-serif;
            font-size: 15px;
            font-weight: 800;
            letter-spacing: -1.5px;
            padding-right: 2px;
            color: #ffffff;
            flex-shrink: 0;
            margin-right: 9px;
            vertical-align: middle;
        }}
        .me-brand-mark .me-brand-mark-e {{
            color: #99CC00;
        }}
        #menu-nav .nav-item.dropdown {{ position: relative; display: flex; align-items: center; }}
        #menu-nav .dropdown-menu {{ right: 0 !important; left: auto !important; min-width: 240px; max-width: 92vw; background: #111111; border: 1px solid rgba(153,204,0,0.35); box-shadow: 0 14px 34px rgba(0,0,0,0.75); z-index: 10060 !important; }}
        #menu-nav .dropdown-item {{ color: #e5e7eb; font-size: 13px; padding: 8px 16px; white-space: normal; }}
        #menu-nav .dropdown-item:hover {{ background: rgba(153,204,0,0.16); color: #99CC00; }}
        @media (max-width: 1399.98px) {{
            #menu-nav .me-workspace-label {{ display: none; }}
            #menu-nav #navbarDropdownMenuLink {{ max-width: 180px !important; }}
            #menu-nav .navbar-nav .nav-link.me-public-link {{ padding-left: 6px !important; padding-right: 6px !important; }}
        }}
        @media (max-width: 1199.98px) {{
            #menu-nav .navbar > .container-fluid {{ flex-wrap: wrap; }}
            #navbarNavDropdown {{
                background: #0b110d;
                border: 1px solid rgba(153, 204, 0, 0.32);
                border-radius: 10px;
                padding: 12px 14px;
                margin-top: 8px;
                box-shadow: 0 18px 40px rgba(0, 0, 0, 0.9);
            }}
            #menu-nav .navbar-nav {{
                flex-direction: column !important;
                align-items: stretch !important;
                gap: 6px !important;
                width: 100%;
            }}
            #menu-nav .navbar-nav .nav-item {{
                margin-left: 0 !important;
                margin-right: 0 !important;
                width: 100%;
            }}
            .me-workspace-nav-group {{
                width: 100% !important;
                display: flex !important;
                flex-wrap: nowrap !important;
                align-items: center !important;
                justify-content: space-between !important;
                gap: 6px !important;
                margin: 4px 0 !important;
                padding: 6px 8px !important;
            }}
            .me-workspace-label {{
                font-size: 9px !important;
                padding: 0 2px !important;
                flex-shrink: 0 !important;
            }}
            .me-workspace-btn {{
                flex: 1 1 0% !important;
                justify-content: center !important;
                padding: 6px 8px !important;
                font-size: 12px !important;
                min-height: 36px !important;
            }}
            #menu-nav .nav-item.dropdown {{
                flex-direction: column;
                align-items: flex-start;
            }}
            #menu-nav .dropdown-menu {{
                position: static !important;
                width: 100%;
                max-width: 100%;
                margin-top: 6px;
                box-shadow: none;
            }}
        }}
        /* Platform-Integrated Dark Custom Selectors (replaces native OS white radio popups) */
        .me-custom-select-wrap {{
            position: relative;
            display: inline-block;
            width: 100%;
            min-width: 145px;
            vertical-align: middle;
        }}
        .me-custom-select-btn {{
            width: 100%;
            min-height: 36px;
            padding: 6px 12px;
            background: #121915;
            border: 1px solid rgba(153, 204, 0, 0.38);
            border-radius: 7px;
            color: #eef5f0;
            font-family: OpenSansSemiBold, Arial, sans-serif;
            font-size: 12.5px;
            line-height: 1.3;
            text-align: left;
            display: inline-flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            cursor: pointer;
            transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
        }}
        .me-custom-select-btn:hover,
        .me-custom-select-wrap.is-open .me-custom-select-btn {{
            background: #17221b;
            border-color: #99CC00;
            box-shadow: 0 0 0 2px rgba(153, 204, 0, 0.18);
            color: #ffffff;
        }}
        .me-custom-select-label {{
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            flex: 1 1 auto;
        }}
        .me-custom-select-caret {{
            flex: 0 0 auto;
            color: #99CC00;
            font-size: 11px;
            transition: transform 0.15s ease;
        }}
        .me-custom-select-wrap.is-open .me-custom-select-caret {{
            transform: rotate(180deg);
        }}
        .me-custom-select-menu {{
            position: absolute;
            top: calc(100% + 5px);
            left: 0;
            min-width: 100%;
            width: max-content;
            max-width: min(320px, 90vw);
            max-height: 260px;
            overflow-y: auto;
            background: #0d130f;
            border: 1px solid #99CC00;
            border-radius: 9px;
            padding: 5px;
            box-shadow: 0 16px 38px rgba(0, 0, 0, 0.9);
            z-index: 10090;
            display: none;
        }}
        .me-custom-select-wrap.is-open .me-custom-select-menu {{
            display: block;
        }}
        .me-custom-select-opt {{
            width: 100%;
            border: 0;
            background: transparent;
            color: #d8e2dc;
            font-family: OpenSansRegular, Arial, sans-serif;
            font-size: 12.5px;
            line-height: 1.35;
            padding: 8px 10px;
            border-radius: 6px;
            text-align: left;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            cursor: pointer;
            white-space: normal;
        }}
        .me-custom-select-opt:hover {{
            background: rgba(153, 204, 0, 0.15);
            color: #ffffff;
        }}
        .me-custom-select-opt.is-selected {{
            background: rgba(153, 204, 0, 0.22);
            color: #99CC00;
            font-family: OpenSansBold, Arial, sans-serif;
            font-weight: 700;
        }}
        .me-custom-select-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            border: 1.5px solid #6b7f73;
            flex-shrink: 0;
        }}
        .me-custom-select-opt.is-selected .me-custom-select-dot {{
            background: #99CC00;
            border-color: #99CC00;
            box-shadow: 0 0 6px rgba(153, 204, 0, 0.6);
        }}
        /* Integrated Map Viewport & Footer (zero vertical page scroll on 3D Globe / MyMosaics) */
        body.me-map-viewport {{
            height: 100dvh !important;
            max-height: 100dvh !important;
            overflow: hidden !important;
            margin: 0 !important;
        }}
        form.me-map-form {{
            height: 100dvh !important;
            max-height: 100dvh !important;
            min-height: 100dvh !important;
            overflow: hidden !important;
            display: flex !important;
            flex-direction: column !important;
        }}
        form.me-map-form > .border-nav,
        form.me-map-form > #toolbar {{
            flex: 0 0 auto !important;
        }}
        body.me-map-viewport form.me-map-form > .container-fluid2 {{
            flex: 1 1 0% !important;
            height: calc(100dvh - 88px) !important;
            max-height: calc(100dvh - 88px) !important;
            min-height: 0 !important;
            position: relative !important;
            overflow: hidden !important;
        }}
        body.me-map-viewport form.me-map-form > #toolbar ~ .container-fluid2 {{
            height: calc(100dvh - 136px) !important;
            max-height: calc(100dvh - 136px) !important;
        }}
        body.me-map-viewport form.me-map-form > .container-fluid2 > #itemsContainer,
        body.me-map-viewport form.me-map-form > .container-fluid2 > #cesiumContainer {{
            height: 100% !important;
            max-height: 100% !important;
        }}
        body.me-map-viewport form.me-map-form > #toolbar ~ .container-fluid2 .cesium-viewer-toolbar {{
            top: 14px !important;
            right: 14px !important;
        }}
        form.me-map-form > footer.me-site-footer {{
            flex: 0 0 auto !important;
            height: 28px !important;
            min-height: 28px !important;
            max-height: 28px !important;
            margin-top: 0 !important;
            padding: 0 12px !important;
            font-size: 10.5px !important;
            background: #070b09 !important;
            border-top: 1px solid rgba(153, 204, 0, 0.22) !important;
            display: flex !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
            justify-content: space-between !important;
            gap: 12px !important;
            white-space: nowrap !important;
            overflow-x: auto !important;
            scrollbar-width: none !important;
            z-index: 1015 !important;
        }}
        form.me-map-form > footer.me-site-footer::-webkit-scrollbar {{
            display: none !important;
        }}
        form.me-map-form > footer.me-site-footer > div {{
            display: inline-flex !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
            gap: 12px !important;
        }}
        /* Unified Global Map & MyMosaic Map Filter Stack, Category Buttons & Base Layer Controls */
        html body .map-filter-stack .menu-map,
        html body .mymosaic-filter-stack .menu-map {{
            position: relative !important;
            inset: auto !important;
            margin: 0 !important;
            width: 570px !important;
            max-width: 100% !important;
            display: grid !important;
            grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
            border-radius: 6px !important;
            border: 1px solid rgba(153, 204, 0, 0.35) !important;
            background: rgba(13, 19, 15, 0.95) !important;
            overflow: hidden !important;
            pointer-events: auto !important;
        }}
        html body .map-filter-stack .menu-map .menu-item,
        html body .mymosaic-filter-stack .menu-map .menu-item {{
            float: none !important;
            width: auto !important;
            min-height: 40px !important;
            padding: 9px 10px !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            gap: 7px !important;
            font-size: 12px !important;
            font-family: OpenSansSemiBold, Arial, sans-serif !important;
            color: #e2ebe5 !important;
            background: transparent !important;
            border: 0 !important;
            border-right: 1px solid rgba(255, 255, 255, 0.10) !important;
            white-space: nowrap !important;
        }}
        html body .map-filter-stack .menu-map .menu-item:last-child,
        html body .mymosaic-filter-stack .menu-map .menu-item:last-child {{
            border-right: 0 !important;
        }}
        html body .map-filter-stack .menu-map .menu-item.active-icon-menu,
        html body .mymosaic-filter-stack .menu-map .menu-item.active-icon-menu {{
            background: rgba(153, 204, 0, 0.18) !important;
            color: #99CC00 !important;
        }}
        html body .map-filter-stack .menu-map .menu-item .icon-menu,
        html body .mymosaic-filter-stack .menu-map .menu-item .icon-menu {{
            margin-right: 0 !important;
            flex-shrink: 0 !important;
            display: inline-block !important;
            fill: currentColor !important;
        }}
        html body .mymosaic-filter-stack {{
            position: absolute !important;
            z-index: 1010 !important;
            top: 14px !important;
            left: 304px !important;
            display: flex !important;
            flex-direction: column !important;
            align-items: flex-start !important;
            gap: 10px !important;
            pointer-events: none !important;
        }}
        html body .mymosaic-filter-stack > * {{
            pointer-events: auto !important;
        }}
        html body .base-layer-switch,
        html body .mymosaic-map-controls {{
            position: fixed !important;
            right: 20px !important;
            bottom: calc(var(--mosaic-credit-bottom, 28px) + var(--mosaic-credit-height, 24px) + 10px) !important;
            left: auto !important;
            z-index: 1015 !important;
            display: flex !important;
            align-items: center !important;
            gap: 6px !important;
            padding: 4px !important;
            background: rgba(10, 14, 12, 0.94) !important;
            border: 1px solid rgba(153, 204, 0, 0.35) !important;
            border-radius: 7px !important;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5) !important;
        }}
        html body .mymosaic-map-controls .base-row {{
            display: flex !important;
            align-items: center !important;
            gap: 6px !important;
            padding: 0 !important;
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
        }}
        html body .base-layer-switch button,
        html body .mymosaic-map-controls button {{
            min-width: 78px !important;
            min-height: 32px !important;
            padding: 6px 12px !important;
            border: 0 !important;
            border-radius: 5px !important;
            background: transparent !important;
            color: #d4dbd7 !important;
            font-family: OpenSansSemiBold, Arial, sans-serif !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            cursor: pointer !important;
        }}
        html body .base-layer-switch button.active,
        html body .mymosaic-map-controls button.active {{
            background: #99CC00 !important;
            color: #070b09 !important;
            font-weight: 700 !important;
        }}
        html body .base-layer-switch #btn-fly-global-map,
        html body .mymosaic-map-controls button[onclick*="launchMyMosaicFly"] {{
            background: #99CC00 !important;
            color: #070b09 !important;
            font-weight: 700 !important;
        }}
        /* Mobile 3D Map: Top 4 Filter Buttons, Horizontal Scroll Asset Cards Drawer & Bottom Controls */
        @media (max-width: 1024px) {{
            html body .map-filter-stack {{
                position: fixed !important;
                top: 64px !important;
                left: 8px !important;
                right: 8px !important;
                width: calc(100vw - 16px) !important;
                max-width: calc(100vw - 16px) !important;
                gap: 6px !important;
                z-index: 1010 !important;
            }}
            html body .mymosaic-filter-stack {{
                position: absolute !important;
                top: 10px !important;
                left: 8px !important;
                right: 8px !important;
                width: calc(100vw - 16px) !important;
                max-width: calc(100vw - 16px) !important;
                gap: 6px !important;
                z-index: 1010 !important;
            }}
            html body .map-filter-stack .menu-map,
            html body .mymosaic-filter-stack .menu-map {{
                width: 100% !important;
                display: grid !important;
                grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
                border-radius: 8px !important;
                border: 1px solid rgba(153, 204, 0, 0.38) !important;
                background: rgba(13, 19, 15, 0.96) !important;
                overflow: hidden !important;
                box-shadow: 0 6px 18px rgba(0, 0, 0, 0.55) !important;
            }}
            html body .menu-map .menu-item {{
                min-height: 36px !important;
                height: 36px !important;
                padding: 4px 3px !important;
                font-size: 10px !important;
                line-height: 1.15 !important;
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
                gap: 3px !important;
                border-bottom: none !important;
                border-right: 1px solid rgba(255, 255, 255, 0.09) !important;
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
            }}
            html body .menu-map .menu-item:last-child {{
                border-right: none !important;
            }}
            html body .menu-map .menu-item .icon-menu {{
                width: 11px !important;
                height: 11px !important;
                margin-right: 2px !important;
                flex-shrink: 0 !important;
                display: inline-block !important;
            }}
            html body .map-secondary-filters {{
                width: 100% !important;
                display: flex !important;
                flex-wrap: nowrap !important;
                align-items: stretch !important;
                gap: 6px !important;
            }}
            html body .map-secondary-filters .mosaic-extension-filter {{
                flex: 1 1 50% !important;
                min-width: 0 !important;
                padding: 3px 8px !important;
                border-radius: 8px !important;
                background: rgba(13, 19, 15, 0.94) !important;
                border: 1px solid rgba(153, 204, 0, 0.30) !important;
            }}
            html body .map-secondary-filters .mosaic-extension-picker summary {{
                min-height: 26px !important;
                padding: 2px 4px !important;
                font-size: 11.5px !important;
            }}
            html body .map-secondary-filters .mosaic-temporal-host {{
                flex: 1 1 50% !important;
                min-width: 0 !important;
                position: relative !important;
            }}
            html body .map-secondary-filters .mosaic-temporal-trigger {{
                width: 100% !important;
                min-height: 36px !important;
                padding: 4px 8px !important;
                border-radius: 8px !important;
            }}
            html body .map-filter-stack .mosaic-temporal-panel,
            html body .mymosaic-filter-stack .mosaic-temporal-panel,
            html body .mosaic-temporal-host .mosaic-temporal-panel {{
                right: 0 !important;
                left: auto !important;
                width: min(300px, calc(100vw - 16px)) !important;
                max-width: calc(100vw - 16px) !important;
                z-index: 1035 !important;
            }}
            html body .container-fluid2 #itemsContainer {{
                position: absolute !important;
                z-index: 1012 !important;
                left: 8px !important;
                right: 8px !important;
                bottom: 46px !important;
                width: calc(100% - 16px) !important;
                height: auto !important;
                max-height: 194px !important;
                background: rgba(11, 16, 13, 0.96) !important;
                border: 1px solid rgba(153, 204, 0, 0.45) !important;
                border-radius: 11px !important;
                padding: 7px 9px !important;
                overflow-y: hidden !important;
                overflow-x: hidden !important;
                box-shadow: 0 12px 32px rgba(0, 0, 0, 0.85) !important;
                backdrop-filter: blur(8px);
            }}
            /* Match the integrated viewport specificity: the mobile drawer
               must not inherit the desktop catalogue's full height. */
            body.me-map-viewport form.me-map-form > .container-fluid2 > #itemsContainer {{
                height: auto !important;
                max-height: 194px !important;
            }}
            html body .container-fluid2 #itemsContainer.is-mobile-collapsed {{
                display: none !important;
            }}
            html body .container-fluid2 #itemsContainer .map-catalogue-search {{
                padding: 0 0 6px 0 !important;
                background: transparent !important;
                flex-direction: row !important;
                align-items: center !important;
                gap: 6px !important;
            }}
            html body .container-fluid2 #itemsContainer .map-catalogue-search label,
            html body .container-fluid2 #itemsContainer .map-catalogue-search small,
            html body .container-fluid2 #itemsContainer .map-catalogue-search #mapCatalogueStatus,
            html body .container-fluid2 #itemsContainer .map-results-summary {{
                display: none !important;
            }}
            html body .container-fluid2 #itemsContainer .map-catalogue-search input {{
                min-height: 30px !important;
                height: 30px !important;
                padding: 3px 10px !important;
                font-size: 12px !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace {{
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                align-items: stretch !important;
                gap: 10px !important;
                overflow-x: auto !important;
                overflow-y: hidden !important;
                scroll-snap-type: x mandatory !important;
                -webkit-overflow-scrolling: touch !important;
                padding: 2px 2px 4px 2px !important;
                scrollbar-width: thin !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .global-asset-card,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-asset-card,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .box-product {{
                flex: 0 0 282px !important;
                width: 282px !important;
                max-width: 82vw !important;
                height: 126px !important;
                min-height: 126px !important;
                max-height: 126px !important;
                margin: 0 !important;
                float: none !important;
                scroll-snap-align: start !important;
                display: flex !important;
                flex-direction: row !important;
                overflow: hidden !important;
                border-radius: 9px !important;
                border: 1px solid rgba(153, 204, 0, 0.35) !important;
                background: #141b17 !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-preview,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-asset-preview,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .box-product-image {{
                width: 104px !important;
                min-width: 104px !important;
                max-width: 104px !important;
                height: 126px !important;
                min-height: 126px !important;
                max-height: 126px !important;
                flex: 0 0 104px !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-origin {{
                display: none !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-type-icon {{
                top: 6px !important;
                left: 6px !important;
                width: 22px !important;
                height: 22px !important;
                border-radius: 5px !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-type-icon svg {{
                width: 13px !important;
                height: 13px !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-visibility {{
                top: 6px !important;
                right: 6px !important;
                padding: 3px 6px !important;
                font-size: 8.5px !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-visibility span {{
                font-size: 10px !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-details,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .asset-detail-action {{
                left: 6px !important;
                right: 6px !important;
                bottom: 6px !important;
                width: auto !important;
                justify-content: center !important;
                text-align: center !important;
                padding: 3px 6px !important;
                min-height: 22px !important;
                font-size: 9.5px !important;
                border-radius: 5px !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-body,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-asset-body,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .product-box-info {{
                flex: 1 1 auto !important;
                min-width: 0 !important;
                height: 126px !important;
                min-height: 126px !important;
                max-height: 126px !important;
                padding: 8px 10px !important;
                display: flex !important;
                flex-direction: column !important;
                justify-content: flex-start !important;
                gap: 3px !important;
                overflow: hidden !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-family,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-asset-facts,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-annotation-trigger,
            html body .container-fluid2 #itemsContainer #items.section-marketplace button[data-annotation-asset],
            html body .container-fluid2 #itemsContainer #items.section-marketplace [class*="annotation"] {{
                display: none !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-title,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .product-title,
            html body .container-fluid2 #itemsContainer #items.section-marketplace h3 {{
                flex-shrink: 0 !important;
                min-height: 0 !important;
                max-height: 31px !important;
                display: block !important;
                font-size: 12.5px !important;
                line-height: 1.22 !important;
                margin: 0 0 1px 0 !important;
                padding: 0 !important;
                overflow: hidden !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-locate {{
                min-height: 0 !important;
                height: auto !important;
                max-height: 31px !important;
                padding: 0 !important;
                margin: 0 !important;
                border: 0 !important;
                background: transparent !important;
                color: #ffffff !important;
                font-size: 12.5px !important;
                font-weight: 700 !important;
                line-height: 1.22 !important;
                text-align: left !important;
                width: 100% !important;
                display: -webkit-box !important;
                -webkit-line-clamp: 2 !important;
                -webkit-box-orient: vertical !important;
                white-space: normal !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-creator,
            html body .container-fluid2 #itemsContainer #items.section-marketplace .product-author {{
                flex-shrink: 0 !important;
                font-size: 10.5px !important;
                line-height: 1.25 !important;
                color: #a3b3aa !important;
                margin: 0 !important;
                padding: 0 !important;
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-date {{
                flex-shrink: 0 !important;
                font-size: 10px !important;
                line-height: 1.25 !important;
                margin: 0 !important;
                padding: 0 !important;
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
            }}
            html body .container-fluid2 #itemsContainer #items.section-marketplace .mosaic-card-footer {{
                flex-shrink: 0 !important;
                margin-top: auto !important;
                padding-top: 5px !important;
                border-top: 1px solid rgba(255, 255, 255, 0.12) !important;
                font-size: 10px !important;
                line-height: 1.2 !important;
            }}
            html body .map-mobile-toggle {{
                z-index: 1014 !important;
                right: 8px !important;
                bottom: 246px !important;
                min-height: 34px !important;
                padding: 5px 14px !important;
                font-size: 11.5px !important;
                background: #99CC00 !important;
                color: #070b09 !important;
                border-color: #99CC00 !important;
                box-shadow: 0 4px 14px rgba(0, 0, 0, 0.65) !important;
            }}
            html body .map-mobile-toggle.is-collapsed {{
                bottom: 46px !important;
                background: #18271e !important;
                color: #e9f6db !important;
                border-color: #a6cc54 !important;
            }}
            html body .base-layer-switch,
            html body .mymosaic-map-controls {{
                position: fixed !important;
                bottom: calc(var(--mosaic-credit-height, 24px) + 10px) !important;
                right: 8px !important;
                left: 8px !important;
                width: calc(100vw - 16px) !important;
                display: flex !important;
                flex-wrap: nowrap !important;
                align-items: center !important;
                justify-content: space-between !important;
                gap: 4px !important;
                padding: 3px 4px !important;
                background: rgba(10, 14, 11, 0.95) !important;
                border: 1px solid rgba(153, 204, 0, 0.35) !important;
                border-radius: 8px !important;
                z-index: 1016 !important;
            }}
            html body .mymosaic-map-controls .base-row {{
                width: 100% !important;
                display: flex !important;
                align-items: center !important;
                justify-content: space-between !important;
                gap: 4px !important;
            }}
            html body .base-layer-switch button,
            html body .mymosaic-map-controls button {{
                min-height: 30px !important;
                height: 30px !important;
                padding: 4px 8px !important;
                font-size: 11px !important;
                flex: 1 1 auto !important;
                text-align: center !important;
                white-space: nowrap !important;
            }}
            html body .container-fluid2 .cesium-viewer-toolbar {{
                top: auto !important;
                bottom: calc(var(--mosaic-credit-height, 24px) + 66px) !important;
                right: 8px !important;
                z-index: 1004 !important;
            }}

        }}
    </style>
    <style>
        html body .cesium-viewer-bottom {{ left: 8px !important; right: auto !important; bottom: 4px !important; width: calc(100% - 16px) !important; max-width: calc(100% - 16px) !important; }}
        html body .cesium-widget-credits {{ position: static !important; display: block !important; max-width: none !important; width: auto !important; white-space: normal !important; }}
        .cesium-widget-credits, .cesium-credit-textContainer, .cesium-credit-expand-link {{ font: 11px/1.4 sans-serif !important; }}
        html body .sim-hud {{ bottom: calc(var(--mosaic-credit-height, 24px) + 12px) !important; }}
        @media (max-width: 767px) {{
            html body .container-fluid2 #itemsContainer {{ position: fixed !important; bottom: calc(var(--mosaic-credit-bottom, 28px) + var(--mosaic-credit-height, 24px) + 58px) !important; }}
            html body .map-mobile-toggle {{ position: fixed !important; bottom: calc(var(--mosaic-credit-bottom, 28px) + var(--mosaic-credit-height, 24px) + 260px) !important; }}
            html body .map-mobile-toggle.is-collapsed {{ bottom: calc(var(--mosaic-credit-bottom, 28px) + var(--mosaic-credit-height, 24px) + 58px) !important; }}
            html body .container-fluid2 .cesium-viewer-toolbar {{ top: 120px !important; bottom: auto !important; }}
            html body #simMiniMapWrap {{ bottom: calc(var(--mosaic-credit-height, 24px) + 190px) !important; }}
        }}
        html body .base-layer-switch, html body .mymosaic-map-controls {{ bottom: calc(var(--mosaic-credit-bottom, 28px) + var(--mosaic-credit-height, 24px) + 10px) !important; }}
    </style>
    <script>window.MOSAICEARTH_ASSET_BASE={json.dumps(asset_base)};</script>
    <script type="text/javascript">
        function showModalByIdOnClick(e, id) {{
            if (e) e.preventDefault();
            showModalById(id);
        }}
        function showModalById(id) {{
            $(`#${{id}}`).modal('show');
        }}
        function hideModalById(id) {{
            $(`#${{id}}`).modal('hide');
        }}
        function CargarActiveMenu(id) {{
            var el = document.getElementById(id);
            if (el) el.classList.add('activeLink');
        }}
        function initMosaicCustomSelects(root) {{
            var scope = root || document;
            var selects = scope.querySelectorAll ? scope.querySelectorAll('select:not([multiple]):not([data-me-select-done])') : [];
            for (var i = 0; i < selects.length; i++) {{
                (function(sel) {{
                    if (sel.closest('.mosaic-extension-filter') || sel.classList.contains('select2-hidden-accessible')) return;
                    sel.setAttribute('data-me-select-done', '1');
                    var wrap = document.createElement('div');
                    wrap.className = 'me-custom-select-wrap';
                    if (sel.style && sel.style.width) wrap.style.width = sel.style.width;
                    var btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'me-custom-select-btn';
                    var lbl = document.createElement('span');
                    lbl.className = 'me-custom-select-label';
                    var caret = document.createElement('i');
                    caret.className = 'fas fa-chevron-down me-custom-select-caret';
                    btn.appendChild(lbl);
                    btn.appendChild(caret);
                    var menu = document.createElement('div');
                    menu.className = 'me-custom-select-menu';
                    btn.setAttribute('aria-haspopup', 'listbox');
                    btn.setAttribute('aria-expanded', 'false');
                    menu.setAttribute('role', 'listbox');

                    function syncMenu() {{
                        btn.disabled = sel.disabled;
                        if (sel.disabled) wrap.classList.remove('is-open');
                        btn.setAttribute('aria-expanded', String(wrap.classList.contains('is-open')));
                        menu.innerHTML = '';
                        var activeOpt = sel.options[sel.selectedIndex] || sel.options[0];
                        lbl.textContent = activeOpt ? activeOpt.textContent : '';
                        for (var j = 0; j < sel.options.length; j++) {{
                            (function(opt, idx) {{
                                var item = document.createElement('button');
                                item.type = 'button';
                                item.className = 'me-custom-select-opt' + (idx === sel.selectedIndex ? ' is-selected' : '');
                                item.disabled = sel.disabled || opt.disabled;
                                item.setAttribute('role', 'option');
                                item.setAttribute('aria-selected', String(idx === sel.selectedIndex));
                                var txt = document.createElement('span');
                                txt.textContent = opt.textContent;
                                var dot = document.createElement('span');
                                dot.className = 'me-custom-select-dot';
                                item.appendChild(txt);
                                item.appendChild(dot);
                                item.addEventListener('click', function(ev) {{
                                    ev.stopPropagation();
                                    if (sel.disabled || opt.disabled) return;
                                    sel.selectedIndex = idx;
                                    sel.value = opt.value;
                                    syncMenu();
                                    wrap.classList.remove('is-open');
                                    btn.setAttribute('aria-expanded', 'false');
                                    btn.focus();
                                    sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                }});
                                menu.appendChild(item);
                            }})(sel.options[j], j);
                        }}
                    }}
                    syncMenu();
                    btn.addEventListener('click', function(ev) {{
                        ev.stopPropagation();
                        if (sel.disabled) return;
                        var wasOpen = wrap.classList.contains('is-open');
                        var allOpen = document.querySelectorAll('.me-custom-select-wrap.is-open');
                        for (var k = 0; k < allOpen.length; k++) {{ allOpen[k].classList.remove('is-open'); var trigger = allOpen[k].querySelector('.me-custom-select-btn'); if (trigger) trigger.setAttribute('aria-expanded', 'false'); }}
                        if (!wasOpen) {{
                            syncMenu();
                            wrap.classList.add('is-open');
                        }}
                        btn.setAttribute('aria-expanded', String(!wasOpen));
                    }});
                    wrap.addEventListener('keydown', function(ev) {{
                        if (ev.key === 'Escape') {{ wrap.classList.remove('is-open'); btn.setAttribute('aria-expanded', 'false'); btn.focus(); }}
                        if ((ev.key === 'ArrowDown' || ev.key === 'ArrowUp') && !sel.disabled) {{
                            ev.preventDefault();
                            wrap.classList.add('is-open'); btn.setAttribute('aria-expanded', 'true');
                            var options = Array.from(menu.querySelectorAll('button:not(:disabled)'));
                            var current = options.indexOf(document.activeElement);
                            if (options.length) options[current < 0 ? (ev.key === 'ArrowDown' ? 0 : options.length - 1) : (current + (ev.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length].focus();
                        }}
                    }});
                    sel.addEventListener('change', syncMenu);
                    if (window.MutationObserver) new MutationObserver(syncMenu).observe(sel, {{attributes:true,childList:true,subtree:true}});
                    sel.parentNode.insertBefore(wrap, sel);
                    wrap.appendChild(btn);
                    wrap.appendChild(menu);
                    sel.style.setProperty('display', 'none', 'important');
                }})(selects[i]);
            }}
        }}
        document.addEventListener('DOMContentLoaded', function() {{
            initMosaicCustomSelects(document);
            document.addEventListener('click', function() {{
                var allOpen = document.querySelectorAll('.me-custom-select-wrap.is-open');
                for (var k = 0; k < allOpen.length; k++) {{ allOpen[k].classList.remove('is-open'); var trigger = allOpen[k].querySelector('.me-custom-select-btn'); if (trigger) trigger.setAttribute('aria-expanded', 'false'); }}
            }});
            if (window.MutationObserver && document.body) {{
                new MutationObserver(function() {{ initMosaicCustomSelects(document); }}).observe(document.body, {{ childList: true, subtree: true }});
            }}
        }});
    </script>
</head>
<body{body_cls}>
    <{layout_tag} id="form1"{form_attrs}{form_cls} style="min-height:100vh;display:flex;flex-direction:column;">
        <div id="notificationsDiv" role="region" aria-label="Notifications" inert>
            <div class="row">
                <div class="col-12 text-end" style="padding-bottom: 19px; padding-left: 30px; padding-right: 30px; padding-top: 10px;">
                    <button type="button" class="notification-toggle" aria-label="Close notifications" onclick="closeNotifications();"><img alt="" src="/Resources/closeNotis.svg" /></button>
                </div>
                <div class="row notishead">
                    <div class="col-6 headerNotis">Notifications</div>
                    <div class="col-6 text-end markall">
                        <button type="button" class="notification-toggle" onclick="setLeidoall()">Mark all as read</button>
                    </div>
                </div>
                <div id="notificacionesLoad"></div>
            </div>
        </div>
        <div class="border-nav">
            <section class="header" id="menu-nav">
                <nav class="navbar navbar-expand-xl navbar-dark bg-dark">
                    <div class="container-fluid">
                        <a class="navbar-brand" href="/{norm_lang}/Map" title="MosaicEarth Global 3D Map" style="display:inline-flex;align-items:center;">
                            <span class="me-brand-mark" aria-hidden="true"><span>M</span><span class="me-brand-mark-e">E</span></span><span class="navbar-wordmark">Mosaic<strong>Earth</strong></span>{'<small class="me-beta-badge" style="background:rgba(153,204,0,0.20);border-color:#99CC00;color:#99CC00;">DESKTOP LOCAL</small>' if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1" else '<small class="me-beta-badge">BETA</small>'}
                        </a>
                        <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNavDropdown" aria-controls="navbarNavDropdown" aria-expanded="false" aria-label="Toggle navigation">
                            <span class="navbar-toggler-icon"></span>
                        </button>
                        <div class="collapse navbar-collapse justify-content-between" id="navbarNavDropdown">
                            <!-- Left Zone: Public Exploration & Resources (Flat text navigation) -->
                            <ul class="navbar-nav align-items-lg-center me-auto">
                                <li class="nav-item">
                                    <a class="nav-link me-public-link" id="li_public_mymaps" href="/{norm_lang}/PublicMosaics">Public Mosaics</a>
                                </li>
                                <li class="nav-item">
                                    <a class="nav-link me-public-link" id="li_desktop" href="/{norm_lang}/Desktop">Desktop &amp; Connectors</a>
                                </li>
                            </ul>

                            <!-- Right Zone: Workspace Tools (Grouped pill container) + User/Auth -->
                            <ul class="navbar-nav align-items-lg-center ms-auto">
                                <li class="nav-item">
                                    <div class="me-workspace-nav-group" role="group" aria-label="User Workspace Tools">
                                        <span class="me-workspace-label">Workspace</span>
                                        <a class="me-workspace-btn" id="li_projects" href="{f'/{norm_lang}/Projects' if user else f'/Default.aspx?ReturnUrl=%2F{norm_lang}%2FProjects'}" title="MyEarth — Projects, Surveys, Tasks &amp; Assets">
                                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
                                            <span>MyEarth</span>
                                        </a>
                                        <a class="me-workspace-btn" id="li_mymaps" href="{f'/{norm_lang}/MyMaps' if user else f'/Default.aspx?ReturnUrl=%2F{norm_lang}%2FMyMaps'}" title="MyMosaics — Curated Multi-Asset 3D Scenes">
                                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                                            <span>MyMosaics</span>
                                        </a>
                                    </div>
                                </li>
                                {f'''
                                <li class="nav-item">
                                    <div style="position: relative;">
                                        <button type="button" class="notification-toggle" aria-label="Notifications" aria-controls="notificationsDiv" aria-expanded="false" onclick="mostrarNotificaciones();"><img id="notificationsIcon" alt="" src="/Resources/notifications.svg" /></button>
                                        <div id="greenDot"{green_dot_style}></div>
                                    </div>
                                </li>
                                ''' if user else f'''
                                <li class="nav-item ms-lg-1">
                                    <a class="btn btn-sm" href="/Default.aspx" style="background:#99CC00;color:#070b09;font-weight:700;padding:6px 14px;border-radius:6px;">SIGN IN / REGISTER</a>
                                </li>
                                '''}
                                <li class="nav-item dropdown">
                                    <a class="nav-link dropdown-toggle dropdown-menu-dark" href="#" style="display: inline-flex; align-items: center; gap: 6px;" id="navbarDropdownMenuLink" role="button" data-bs-toggle="dropdown" aria-expanded="false">
                                        <label id="usernameEmail" style="cursor:pointer;margin:0;">{user_label if user else ("Desktop activation required" if is_desktop_local else "Guest (Public View)")}</label>
                                        <img src="/Resources/UserMenu.svg" alt="" style="width:16px;height:16px;" />
                                    </a>
                                    <ul class="dropdown-menu dropdown-menu-end text-start" aria-labelledby="navbarDropdownMenuLink">
                                        {f'<li><a class="dropdown-item" href="/en/Projects#desktopCloudProjectsHost" style="color:#38bdf8;font-weight:700;"><i class="fas fa-cloud me-1"></i> Cloud Account &amp; Synchronization</a></li><li><hr class="dropdown-divider" style="border-color:rgba(255,255,255,0.12);" /></li>' if is_desktop_local else ''}
                                        {f'<li><a class="dropdown-item" href="/{norm_lang}/Projects" style="color:#99CC00;font-weight:700;">MyEarth (Projects &amp; Surveys)</a></li><li><a class="dropdown-item" href="/{norm_lang}/MyMaps" style="color:#99CC00;font-weight:700;">MyMosaics (3D Scenes)</a></li><li><a class="dropdown-item" href="/{norm_lang}/Profile">Account, Storage Quota &amp; NodeODM</a></li><li><a class="dropdown-item" href="/{norm_lang}/CreditPacks">Storage Plans &amp; Cloud Processing (€)</a></li>' if user else f'<li><a class="dropdown-item" href="/Default.aspx?ReturnUrl=%2F{norm_lang}%2FProjects" style="color:#99CC00;font-weight:700;">Sign In to MyEarth / MyMosaics</a></li>'}
                                        <li><a class="dropdown-item" href="/{norm_lang}/Desktop">MosaicEarth Desktop &amp; Connectors</a></li>
                                        <li><a class="dropdown-item" href="/{norm_lang}/Faqs">Help &amp; FAQs</a></li>
                                        <li><a class="dropdown-item" href="/Terms.aspx" target="_blank" rel="noopener">Terms of Service</a></li>
                                        <li><a class="dropdown-item" href="/License.aspx" target="_blank" rel="noopener">Standard License</a></li>
                                        <li><hr class="dropdown-divider" style="border-color:rgba(255,255,255,0.12);" /></li>
                                        <li>{('<a class="dropdown-item" href="/Logout.ashx">Log out</a>' if user else '<a class="dropdown-item" href="/Default.aspx">Sign in / Register</a>')}</li>
                                    </ul>
                                </li>
                            </ul>
                        </div>
                    </div>
                </nav>
            </section>
        </div>

{body_content}

        <footer class="me-site-footer" aria-label="Global Geospatial Directory &amp; Policies" style="margin-top:auto;padding:14px 24px;border-top:1px solid rgba(255,255,255,0.08);background:#070b09;color:#9ca3af;font-size:12px;display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;">
            <div>MosaicEarth — Global 3D Geospatial Platform</div>
            <div style="display:flex;flex-wrap:wrap;gap:14px;">
                <a href="/en/directory" style="color:#99CC00;text-decoration:none;font-weight:600;">Geospatial Directory (EN)</a>
                <a href="/es/directory" style="color:#99CC00;text-decoration:none;">Directorio (ES)</a>
                <a href="/en/Map" style="color:#d1d5db;text-decoration:none;">3D Globe</a>
                <a href="/en/explore/maps" style="color:#d1d5db;text-decoration:none;">Maps &amp; Layers</a>
                <a href="/en/explore/3d-models" style="color:#d1d5db;text-decoration:none;">3D Models</a>
                <a href="/en/explore/point-clouds" style="color:#d1d5db;text-decoration:none;">Point Clouds</a>
                <a href="/en/Faqs" style="color:#d1d5db;text-decoration:none;">Help &amp; FAQs</a>
                <a href="https://mosaicearth.app/directory" style="color:#d1d5db;text-decoration:none;">Formats &amp; Guides</a>
                <a href="/License.aspx" style="color:#d1d5db;text-decoration:none;">Standard License</a>
                <a href="/Terms.aspx" style="color:#d1d5db;text-decoration:none;">Terms</a>
                <a href="/open-source" style="color:#d1d5db;text-decoration:none;">Engine Source &amp; Notices</a>
            </div>
        </footer>

        <script src="/assets/js/jquery-3.6.1.min.js"></script>
        <script src="/admin/assets/js/plugins/select2/select2.full.min.js"></script>
        <script src="/js/front/MosaicExtensionFilter.js?v=20260923-map-r28"></script>
        <script src="/js/init.js?v=20260916-ux"></script>
        <script src="/js/front/Notifications.js?v=20261106"></script>
        <script src="/assets/js/popper.min.js"></script>
        <script src="/assets/js/bootstrap.bundle.min.js"></script>
        <script src="/assets/js/plugins/slimscroll/jquery.slimscroll.min.js"></script>
        <script src="/Admin/assets/JqueryUI/jquery-ui.min.js"></script>
        <script src="/assets/js/plugins/datapicker/bootstrap-datepicker.js"></script>
        <script src="/assets/js/plugins/datapicker/locales/bootstrap-datepicker.es.js"></script>
        <script src="/admin/assets/js/plugins/sweetalert/sweetalert.min.js"></script>
        <script src="/assets/js/plugins/toastr/toastr.min.js"></script>
        <script>
            var __funciones_url = "/funciones.aspx/";
        </script>
{scripts_extra}
    <script src="/desktop-platform-ui.js?v={DESKTOP_UI_REVISION}"></script>
    </{layout_tag}>
</body>
</html>"""


def render_map2_page(
    lang: str = "en",
    route_family: Optional[str] = None,
    route_category: Optional[str] = None,
    route_region: Optional[str] = None,
    search_query: Optional[str] = None,
    current_page: int = 1,
    lat_param: float = 0.0,
    lon_param: float = 0.0,
    type_param: int = 0,
    user: Optional[Dict[str, Any]] = None,
) -> str:
    norm_lang = normalize_lang(lang)
    is_es = norm_lang == "es"
    active_family = resolve_family(route_family or (str(type_param) if type_param else None))
    active_category = resolve_category(route_category)
    active_region = resolve_region(route_region)

    catalogue_html, page_items, total_count = render_server_catalogue_html(
        norm_lang,
        active_family=active_family,
        active_category=active_category,
        search_query=search_query,
        current_page=current_page,
        page_size=24,
        active_region=active_region,
    )

    page_suffix = f" (Page {current_page})" if current_page > 1 else ""
    if active_family and active_category:
        fam_name = family_display(active_family["file_type_id"])
        canonical_path = f"/{norm_lang}/explore/{active_family['route_slug']}/{active_category['slug']}"
        title = (
            f"{active_category['name']} · {fam_name} Georreferenciados en 3D{page_suffix} · MosaicEarth"
            if is_es
            else f"{active_category['name']} {fam_name} — Georeferenced 3D Globe & Datasets{page_suffix} · MosaicEarth"
        )
        description = f"{active_category['description']} {active_family['description']}"
        badge = f"{active_category['slug']} · {active_family['route_slug']}".upper()
    elif active_region:
        canonical_path = f"/{norm_lang}/locations/{active_region['slug']}"
        title = f"{active_region['es_name'] if is_es else active_region['name']} — 3D Models, Orthophotos & Point Clouds{page_suffix} · MosaicEarth"
        description = active_region["es_description"] if is_es else active_region["description"]
        badge = f"REGIONAL 3D HUB · {active_region['center_lat']:.2f}N {active_region['center_lon']:.2f}E"
    elif active_family:
        canonical_path = f"/{norm_lang}/explore/{active_family['route_slug']}"
        title = active_family["title"].replace(" · MosaicEarth", "") + page_suffix + " · MosaicEarth"
        description = active_family["description"]
        badge = f"{active_family['route_slug'].upper()} DIRECTORY"
    elif active_category:
        canonical_path = f"/{norm_lang}/category/{active_category['slug']}"
        title = f"{active_category['name']} — 3D Geospatial Models, Orthophotos & Point Clouds{page_suffix} · MosaicEarth"
        description = active_category["description"]
        badge = f"{active_category['name'].upper()} CATEGORY"
    else:
        canonical_path = f"/{norm_lang}/Map"
        title = f"Explore 3D Geospatial Globe, Orthophotos, 3D Models & Point Clouds{page_suffix} · MosaicEarth"
        description = f"Interactive 3D geospatial globe and public directory with {total_count} georeferenced drone orthophotos (GeoTIFF), 3D photogrammetry models, Gaussian Splats and LiDAR point clouds."
        badge = "PUBLIC 3D GLOBE & CATALOGUE"

    init_lat = (
        active_region["center_lat"]
        if active_region
        else (page_items[0]["Latitude"] if page_items and (active_family or active_category) else None)
    )
    init_lon = (
        active_region["center_lon"]
        if active_region
        else (page_items[0]["Longitude"] if page_items and (active_family or active_category) else None)
    )
    init_height = (
        active_region["default_height_meters"]
        if active_region
        else (2200.0 if (active_family or active_category) else None)
    )
    globe_preconfig = {
        "hasFilter": bool(active_family or active_category or active_region),
        "fileTypeId": active_family["file_type_id"] if active_family else None,
        "categoryId": active_category["id"] if active_category else None,
        "categorySlug": active_category["slug"] if active_category else None,
        "regionSlug": active_region["slug"] if active_region else None,
        "lat": init_lat,
        "lon": init_lon,
        "height": init_height,
    }

    item_list_elements = []
    idx_start = (current_page - 1) * 24 + 1
    for i, item in enumerate(page_items, start=idx_start):
        item_list_elements.append(
            {
                "@type": "ListItem",
                "position": i,
                "url": item["CanonicalUrl"],
                "name": item["Name"],
                "item": {
                    "@type": "Dataset",
                    "name": item["Name"],
                    "description": item["Description"] or f"{item['Name']} — georeferenced {item['FamilyDisplay']} on MosaicEarth.",
                    "url": item["CanonicalUrl"],
                    "isAccessibleForFree": item["PriceCredits"] <= 0,
                    "license": f"{get_public_site_url()}/License.aspx",
                    "creator": {"@type": "Person", "name": item["Creator"]},
                    "spatialCoverage": {
                        "@type": "Place",
                        "name": item["Name"],
                        "geo": {
                            "@type": "GeoCoordinates",
                            "latitude": round(item["Latitude"], 6),
                            "longitude": round(item["Longitude"], 6),
                        },
                    },
                },
            }
        )

    collection_json_ld = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "description": description,
        "url": f"{get_public_site_url()}{canonical_path}",
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": total_count,
            "itemListElement": item_list_elements,
        },
    }

    crumbs = [
        ("MosaicEarth", get_landing_site_url()),
        ("3D Globe & Catalogue", f"/{norm_lang}/Map"),
    ]
    if active_family:
        crumbs.append((family_display(active_family["file_type_id"]), f"/{norm_lang}/explore/{active_family['route_slug']}"))
    if active_category:
        crumbs.append((active_category["name"], canonical_path))

    seo_head = build_head_metadata_html(
        title=title,
        description=description,
        canonical_path=canonical_path,
        og_image_url=build_dynamic_og_image_url(
            title.replace(" · MosaicEarth", ""),
            description,
            badge,
            f"{total_count} Georeferenced Datasets",
        ),
        json_ld_graphs=[
            build_web_application_json_ld(),
            collection_json_ld,
            build_breadcrumb_json_ld(crumbs),
        ],
    )

    store = get_store()
    cesium_key = store.get_cesium_browser_token()
    effective_type = active_family["file_type_id"] if active_family else type_param

    ext_options = "".join(f'<option value="{ext}">.{ext}</option>' for ext in KNOWN_SOURCE_EXTENSIONS)

    head_extra = """
    <link href="/vendor/cesium/Widgets/widgets.css" rel="stylesheet" />
    <script src="/vendor/cesium/Cesium.js"></script>
    <script src="/js/front/MosaicCesiumRuntime.js?v=__MOSAIC_MAP_RUNTIME_VERSION__"></script>
    <script src="/js/front/MosaicAnnotations.js?v=20261016-r45"></script>
    <link rel="stylesheet" href="/css/MosaicAnnotations.css?v=20261016-r45" />
    <link rel="stylesheet" href="/assets/css/external-asset-import.css?v=7" />
    <link rel="stylesheet" href="/assets/css/map2.css?v=12" />
    <link rel="stylesheet" href="/assets/css/mosaic-temporal-filter.css?v=2" />
    <style>
        .map-filter-stack { z-index: 1010; position: absolute; top: calc(var(--mosaic-map-header-height, 60px) + 16px); left: 300px; display: flex; flex-direction: column; align-items: flex-start; gap: 10px; max-width: calc(100vw - 320px); }
        .menu-map { position: relative !important; inset: auto !important; margin: 0 !important; background-color: white; width: 570px; max-width: 100%; border-radius: 3px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); overflow: hidden; }
        .menu-item { width: auto; text-align: center; font-size: 12px; font-family: OpenSansSemiBold; padding: 12px 5px; }
        .icon-menu { margin-right: 8px; fill: currentColor !important; }
        .icon-menu path, .icon-menu circle, .icon-menu rect { fill: currentColor !important; }
        .active-icon-menu { color: #99CC00; }
        .active-icon-menu path, .active-icon-menu circle { fill: #99CC00; }
        .container-fluid2 { display: flex; height: calc(100vh - 60px); }
        #itemsContainer { width: 280px; background-color: #111111; overflow-y: auto; scrollbar-width: none; }
        #itemsContainer::-webkit-scrollbar { display: none; }
        #cesiumContainer { flex: 1; background-color: #111111; }
        @media (max-width: 1199.98px) {
            .general-map-intro { display: none !important; }
        }
        @media (max-width: 991.98px) {
            .map-filter-stack {
                left: 10px !important;
                right: 10px !important;
                top: 66px !important;
                max-width: calc(100vw - 20px) !important;
                width: calc(100vw - 20px) !important;
                z-index: 1005 !important;
            }
            .menu-map {
                width: 100% !important;
                grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
            }
            .menu-item {
                padding: 8px 6px !important;
                font-size: 11.5px !important;
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            }
            .map-secondary-filters {
                width: 100%;
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
            }
            .base-layer-switch {
                bottom: 58px !important;
                right: 10px !important;
                left: auto !important;
                z-index: 1005 !important;
                flex-wrap: wrap;
                justify-content: flex-end;
            }
        }
    </style>"""

    body_content = f"""
    <div class="general-map-intro">
        <strong>MosaicEarth</strong>
        <span>Explore independent geospatial assets from many capture and creation workflows. Discover maps and layers, 3D models and point clouds, then view, acquire or reuse them.</span>
        <span class="general-map-intro-quality">Models displayed here are optimized for smooth web viewing. The downloadable original files provide higher quality.</span>
    </div>
    <div class="map-filter-stack">
        <div class="menu-map" style="cursor: pointer;">
            <button type="button" aria-pressed="false" id="all-assets-menu" class="menu-item" style="border-right: 1px solid rgba(255,255,255,0.10);" onclick="loadCesium(0, null, null)">
                <svg class="icon-menu" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="3" cy="3" r="2"/><path d="M7 1h6v4H7z"/><path d="M1 7h4v6H1z"/><circle cx="10" cy="10" r="3"/></svg>
                All Assets
            </button>
            <button type="button" aria-pressed="false" id="orthophoto-menu" class="menu-item" style="border-right: 1px solid rgba(255,255,255,0.10);" onclick="loadCesium(2, null, null)">
                {ICON_ORTHOPHOTO.replace('<svg ', '<svg class="icon-menu" ').replace('width="20" height="20"', 'width="13" height="13"')}
                Maps &amp; Layers
            </button>
            <button type="button" aria-pressed="false" id="3d-menu" class="menu-item" style="border-right: 1px solid rgba(255,255,255,0.10);" onclick="loadCesium(3, null, null)">
                {ICON_3D.replace('<svg ', '<svg class="icon-menu" ').replace('width="20" height="20"', 'width="12.068" height="13"')}
                3D Models
            </button>
            <button type="button" aria-pressed="false" id="pointcloud-menu" class="menu-item" onclick="loadCesium(1, null, null)">
                {ICON_CLOUD.replace('<svg ', '<svg class="icon-menu" ').replace('width="20" height="20"', 'width="12.527" height="13"')}
                Point Clouds
            </button>
        </div>
        <div class="map-secondary-filters">
            <div class="mosaic-extension-filter">
                <span>File extensions</span>
                <select multiple aria-label="File extensions" id="mapExtension">
                    <option value="">All extensions</option>
                    {ext_options}
                </select>
            </div>
            <div id="globalTemporalFilter" class="mosaic-temporal-host global-time"></div>
        </div>
    </div>
    <div class="base-layer-switch" aria-label="Base map" style="display:flex;align-items:center;gap:6px;">
        <button id="base-satellite" type="button" class="active" onclick="setMosaicBaseLayer('satellite')">Satellite</button>
        <button id="base-topographic" type="button" onclick="setMosaicBaseLayer('topographic')">Topographic</button>
        <button id="btn-fly-global-map" type="button" onclick="launchGlobalFirstPersonFly()" style="background:#99CC00;color:#070b09;font-weight:700;border:none;padding:6px 12px;border-radius:3px;cursor:pointer;" title="Fly in 1st Person across the Global 3D World with all live MosaicAssets">
            <i class="fas fa-plane-departure me-1"></i> Fly 1st Person
        </button>
    </div>
    <div class="container-fluid2">
        <button type="button" id="mapMobileToggle" class="map-mobile-toggle is-collapsed" aria-controls="itemsContainer" aria-expanded="false">Explore assets</button>
        <div id="itemsContainer" class="is-mobile-collapsed">
            <div class="map-catalogue-search">
                <label for="mapAssetSearch">Find an asset or creator</label>
                <input id="mapAssetSearch" type="search" maxlength="100" placeholder="Asset or creator name" aria-describedby="mapSearchScope" />
                <small id="mapSearchScope">Browsing assets in this view. Use the globe's place search to go to a location.</small>
                <div id="mapCatalogueStatus" role="status" aria-live="polite"></div>
            </div>
            <div id="items" class="section-marketplace">
                {catalogue_html}
            </div>
        </div>
        <div id="cesiumContainer"></div>
    </div>"""

    scripts_extra = f"""
    <script>
        window.__MOSAIC_INITIAL_GLOBE_CONFIG = {json.dumps(globe_preconfig)};
        MYLIBRARY.setUrl_page('/Map2.aspx');
        const URL_CESIUM = '/ProcessedAssetFile.ashx/procesado/3d_tiles/model/tileset.json';
        const HEIGHT1 = 0;
        const HEIGHT2 = 0;
        const HEIGHT3 = 0;
        const HEIGHT4 = 0;
        const tipo = '{effective_type}';
        const latMarket = '{lat_param}';
        const lonMarket = '{lon_param}';
        const key = {json.dumps(cesium_key)};
        if (typeof CargarActiveMenu === 'function') CargarActiveMenu("li_map");
        window.launchGlobalFirstPersonFly = function() {{
            var lat = 41.9702, lon = 2.3885;
            try {{
                if (typeof viewer !== 'undefined' && viewer && viewer.camera && viewer.camera.positionCartographic) {{
                    var c = viewer.camera.positionCartographic;
                    var dLat = Cesium.Math.toDegrees(c.latitude);
                    var dLon = Cesium.Math.toDegrees(c.longitude);
                    if (isFinite(dLat) && isFinite(dLon) && (Math.abs(dLat) > 0.01 || Math.abs(dLon) > 0.01)) {{
                        lat = dLat;
                        lon = dLon;
                    }}
                }}
            }} catch(e) {{}}
            window.location.href = '/Fly.aspx?asset_id=global&lat=' + lat.toFixed(5) + '&lon=' + lon.toFixed(5) + '&name=' + encodeURIComponent('Global 3D World (All Live MosaicAssets)');
        }};
        function placeMapControlsBelowHeader() {{
            var header = document.querySelector("#menu-nav nav.navbar") || document.getElementById("menu-nav");
            var bottom = header ? header.getBoundingClientRect().bottom : 60;
            var height = Math.max(60, Math.ceil(bottom || 0));
            document.documentElement.style.setProperty("--mosaic-map-header-height", height + "px");
        }}
        placeMapControlsBelowHeader();
        window.addEventListener("load", placeMapControlsBelowHeader);
        window.addEventListener("resize", placeMapControlsBelowHeader);
        window.setTimeout(function () {{
            var introduction = document.querySelector(".general-map-intro");
            if (!introduction) return;
            introduction.classList.add("is-hiding");
            window.setTimeout(function () {{ introduction.remove(); }}, 350);
        }}, 4650);
    </script>
    <script src="/js/front/MosaicTemporalFilter.js?v=20260916-ux"></script>
    <script src="/js/front/Map2.js?v=20261015-expansion"></script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_directory_page(lang: str = "en") -> str:
    norm_lang = normalize_lang(lang)
    is_es = norm_lang == "es"
    alt_url = "/en/directory" if is_es else "/es/directory"
    all_assets = get_public_directory_items(norm_lang)
    canonical_path = f"/{norm_lang}/directory"
    title = (
        "Directorio Geoespacial 3D — Modelos 3D, Ortofotos, Nubes de Puntos y Regiones · MosaicEarth"
        if is_es
        else "3D Geospatial Directory — Public 3D Models, Orthophotos, Point Clouds & Regions · MosaicEarth"
    )
    description = (
        f"Índice completo de MosaicEarth con {len(all_assets)} activos georreferenciados públicos, 30 combinaciones de Familia × Categoría, 8 regiones geográficas y especificaciones técnicas."
        if is_es
        else f"Complete MosaicEarth public index with {len(all_assets)} georeferenced 3D assets, 30 combinatorial Family × Category hubs, 8 geographic regions and technical specifications."
    )

    seo_head = build_head_metadata_html(
        title=title,
        description=description,
        canonical_path=canonical_path,
        alternate_es_path="/es/directory",
        og_image_url=build_dynamic_og_image_url(
            title.replace(" · MosaicEarth", ""),
            description,
            "GEOSPATIAL DIRECTORY",
            f"{len(all_assets)} Public 3D Assets",
        ),
        json_ld_graphs=[
            build_web_application_json_ld(),
            build_breadcrumb_json_ld(
                [
                    ("MosaicEarth", get_landing_site_url()),
                    ("3D Globe", f"/{norm_lang}/Map"),
                    ("Directorio" if is_es else "Directory", canonical_path),
                ]
            ),
        ],
    )

    sb: List[str] = []
    sb.append(f'<span class="dir-badge">{"DIRECTORIO HUB & SPOKE · MÁX 3 CLICS" if is_es else "HUB & SPOKE GEOSPATIAL DIRECTORY · MAX 3 CLICS"}</span>')
    sb.append(f'<h1>{html.escape(title.replace(" · MosaicEarth", ""))}</h1>')
    sb.append(f'<p class="dir-lead">{html.escape(description)}</p>')

    sb.append('<section class="dir-card" style="margin-bottom:28px;border-color:rgba(153,204,0,0.35);">')
    sb.append(f'  <h3>{"Acceso Directo al Visor 3D Preconfigurado por Familia" if is_es else "Pre-Configured 3D Globe Launchers & Technical Architecture"}</h3>')
    sb.append('  <table class="dir-table" style="margin-top:10px;">')
    sb.append(
        f'    <thead><tr><th>{"Familia de Activos" if is_es else "Asset Family"}</th><th>{"Formatos de Entrada" if is_es else "Supported Source Formats"}</th><th>{"Motor de Streaming WebGL" if is_es else "WebGL Streaming Derivative"}</th><th>{"Ruta Preconfigurada" if is_es else "Pre-Configured Route"}</th></tr></thead>'
    )
    sb.append("    <tbody>")
    sb.append(f'      <tr><td><strong>Maps &amp; Layers (type=2)</strong></td><td>GeoTIFF/COG (.tif), DSM, DTM, Slope, .gpkg, .geojson, .kml/.kmz, .shp (.zip), .dxf, .gpx</td><td>CesiumJS XYZ Imagery Pyramid &amp; Clamped Vectors</td><td><a href="/{norm_lang}/explore/maps">/{norm_lang}/explore/maps &rarr;</a></td></tr>')
    sb.append(f'      <tr><td><strong>3D Models (type=3)</strong></td><td>GLB/glTF (.glb), Wavefront OBJ (.zip), OGC 3D Tiles, 3D Gaussian Splatting (.ply)</td><td>OGC 3D Tiles 1.1 (KTX2 BasisU GPU Textures) &amp; Splat Viewer</td><td><a href="/{norm_lang}/explore/3d-models">/{norm_lang}/explore/3d-models &rarr;</a></td></tr>')
    sb.append(f'      <tr><td><strong>Point Clouds (type=1)</strong></td><td>ASPRS LiDAR (.las, .laz), Stanford .ply, PCL .pcd, Leica .pts, ASCII .xyz</td><td>OGC 3D Tiles (.pnts Octree) + Isolated Potree Viewer</td><td><a href="/{norm_lang}/explore/point-clouds">/{norm_lang}/explore/point-clouds &rarr;</a></td></tr>')
    sb.append("    </tbody></table></section>")

    sb.append(f'<h2>1. {"Matriz Combinatoria: Categoría × Familia de Activo 3D" if is_es else "Combinatorial Matrix: Category × 3D Asset Family"} (30 Routes)</h2>')
    sb.append('<div class="dir-grid">')
    for cat in get_active_categories():
        cnt = sum(1 for x in all_assets if x["CategoryId"] == cat["id"])
        sb.append('  <article class="dir-card">')
        sb.append(f'    <h3><a href="/{norm_lang}/category/{cat["slug"]}">{html.escape(cat["name"])}</a> <small style="color:#9ca3af;font-weight:400;">({cnt})</small></h3>')
        sb.append(f'    <p>{html.escape(cat["description"])}</p>')
        sb.append('    <div class="dir-spokes">')
        for fam in SUPPORTED_FAMILIES:
            sb.append(
                f'      <a href="/{norm_lang}/explore/{fam["route_slug"]}/{cat["slug"]}">{html.escape(cat["name"])} · {html.escape(family_display(fam["file_type_id"]))}</a>'
            )
        sb.append("    </div></article>")
    sb.append("</div>")

    sb.append(f'<h2>2. {"Regiones Geográficas con Coordenadas Preconfiguradas en el Globo 3D" if is_es else "Geographic Region Hubs (Pre-Centered 3D Globe Coordinates)"} ({len(SUPPORTED_REGIONS)} Regions)</h2>')
    sb.append('<div class="dir-grid">')
    for reg in SUPPORTED_REGIONS:
        rcnt = sum(
            1
            for x in all_assets
            if reg["min_lat"] <= x["Latitude"] <= reg["max_lat"]
            and reg["min_lon"] <= x["Longitude"] <= reg["max_lon"]
        )
        sb.append('  <article class="dir-card">')
        sb.append(f'    <h3><a href="/{norm_lang}/locations/{reg["slug"]}">{html.escape(reg["es_name"] if is_es else reg["name"])}</a></h3>')
        sb.append(f'    <p>{html.escape(reg["es_description"] if is_es else reg["description"])}</p>')
        sb.append(
            f'    <div class="dir-spokes"><a href="/{norm_lang}/locations/{reg["slug"]}">WGS84: {format_coordinates(reg["center_lat"], reg["center_lon"])} ({rcnt} assets) &rarr;</a></div>'
        )
        sb.append("  </article>")
    sb.append("</div>")

    landing_prefix = "https://mosaicearth.app/es" if is_es else "https://mosaicearth.app"
    sb.append(f'<h2>3. {"Especificaciones de Formatos, Casos de Uso y Guías de Ingeniería (mosaicearth.app)" if is_es else "Format Specifications, Industry Workflows & Engineering Guides (mosaicearth.app)"}</h2>')
    sb.append('<div class="dir-spokes" style="margin-bottom:24px;">')
    sb.append(f'  <a href="{landing_prefix}/directory">Full pSEO Hub</a>')
    sb.append('  <a href="https://mosaicearth.app/formats">All 14 Formats</a>')
    sb.append(f'  <a href="{landing_prefix}/formats/geotiff-cog">GeoTIFF / COG (.tif)</a>')
    sb.append(f'  <a href="{landing_prefix}/formats/glb-gltf-3d-models">GLB / glTF 2.0 (.glb)</a>')
    sb.append(f'  <a href="{landing_prefix}/formats/obj-photogrammetry-mesh">Textured OBJ (.zip)</a>')
    sb.append(f'  <a href="{landing_prefix}/formats/3d-tiles-fileset">OGC 3D Tiles</a>')
    sb.append(f'  <a href="{landing_prefix}/formats/gaussian-splatting-ply">3D Gaussian Splatting (.ply)</a>')
    sb.append(f'  <a href="{landing_prefix}/formats/las-laz-lidar-point-cloud">LAS / LAZ LiDAR</a>')
    sb.append('  <a href="https://mosaicearth.app/use-cases">12 Industry Use Cases</a>')
    sb.append('  <a href="https://mosaicearth.app/guides">12 Troubleshooting Guides</a>')
    sb.append('  <a href="https://mosaicearth.app/solutions">30 Combinatorial Solutions</a>')
    sb.append("</div>")

    sb.append(f'<h2>4. {"Catálogo Completo de Activos Geoespaciales Publicados" if is_es else "Complete Index of Published Georeferenced MosaicAssets"} ({len(all_assets)} Published MosaicAssets)</h2>')
    sb.append('<table class="dir-table">')
    sb.append(
        f'  <thead><tr><th>{"Activo Geoespacial (Slug Semántico)" if is_es else "MosaicAsset (Semantic Route)"}</th><th>{"Familia" if is_es else "Family"}</th><th>{"Categoría" if is_es else "Category"}</th><th>{"Coordenadas WGS84" if is_es else "WGS84 Coordinates"}</th><th>{"Formato Descargable" if is_es else "Download Format"}</th></tr></thead><tbody>'
    )
    for item in all_assets:
        sb.append(
            f'    <tr><td><a href="{html.escape(item["CanonicalPath"], quote=True)}"><strong>{html.escape(item["Name"])}</strong></a></td><td>{html.escape(item["FamilyDisplay"])}</td><td>{html.escape(item["CategoryName"] or "Geospatial")}</td><td>{format_coordinates(item["Latitude"], item["Longitude"])}</td><td>{html.escape(item["DownloadFormat"])}</td></tr>'
        )
    sb.append("  </tbody></table>")

    content_html = "\n".join(sb)
    return f"""<!DOCTYPE html>
<html lang="{norm_lang}">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
{seo_head}
    <link rel="icon" type="image/png" href="/Resources/mosaicearth-site-favicon.png" />
    <style>
        :root {{ --bg-dark: #070b09; --bg-card: #0c1410; --border-subtle: rgba(255, 255, 255, 0.09); --accent-green: #99CC00; --accent-emerald: #4ade80; --text-main: #f3f4f6; --text-muted: #9ca3af; }}
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; background-color: var(--bg-dark); color: var(--text-main); font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.55; }}
        a {{ color: var(--accent-green); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .dir-header {{ position: sticky; top: 0; z-index: 30; background: rgba(7, 11, 9, 0.92); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border-subtle); padding: 14px 24px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 14px; }}
        .dir-brand {{ font-size: 18px; font-weight: 700; color: #fff; text-decoration: none; }}
        .dir-brand span {{ color: var(--accent-green); }}
        .dir-nav {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; align-items: center; }}
        .dir-nav a {{ color: #d1d5db; }}
        .dir-btn {{ background: var(--accent-green); color: #070b09 !important; font-weight: 700; padding: 7px 14px; border-radius: 6px; font-size: 12px; }}
        .dir-container {{ max-width: 1220px; margin: 0 auto; padding: 36px 24px 64px; }}
        .dir-badge {{ display: inline-block; font-family: monospace; font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent-emerald); background: rgba(74, 222, 128, 0.1); border: 1px solid rgba(74, 222, 128, 0.3); padding: 4px 10px; border-radius: 999px; }}
        h1 {{ font-size: clamp(26px, 3.5vw, 40px); margin: 14px 0 10px; color: #ffffff; line-height: 1.2; }}
        h2 {{ font-size: 21px; color: #ffffff; margin: 36px 0 14px; padding-bottom: 8px; border-bottom: 1px solid var(--border-subtle); }}
        .dir-lead {{ color: var(--text-muted); max-width: 820px; font-size: 15px; margin: 0 0 24px; }}
        .dir-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }}
        .dir-card {{ background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: 10px; padding: 16px; }}
        .dir-card h3 {{ margin: 0 0 6px; font-size: 15px; color: #fff; }}
        .dir-card p {{ margin: 0 0 10px; font-size: 12px; color: var(--text-muted); }}
        .dir-spokes {{ display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; }}
        .dir-spokes a {{ background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.1); padding: 3px 8px; border-radius: 5px; color: #e5e7eb; }}
        .dir-table {{ width: 100%; border-collapse: collapse; font-size: 13px; background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: 10px; overflow: hidden; }}
        .dir-table th, .dir-table td {{ padding: 10px 14px; border-bottom: 1px solid rgba(255, 255, 255, 0.06); text-align: left; }}
        .dir-table th {{ background: rgba(255, 255, 255, 0.03); color: var(--text-muted); font-family: monospace; font-size: 11px; text-transform: uppercase; }}
        .dir-footer {{ margin-top: 56px; padding: 24px; border-top: 1px solid var(--border-subtle); font-size: 12px; color: var(--text-muted); display: flex; flex-wrap: wrap; justify-content: space-between; gap: 14px; }}
    </style>
</head>
<body>
    <header class="dir-header">
        <a class="dir-brand" href="/{norm_lang}/Map" style="display:inline-flex;align-items:center;gap:9px;"><span style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;background:#020302;border:1px solid #303831;border-radius:4px;font-size:14px;font-weight:800;letter-spacing:-1.5px;padding-right:2px;color:#fff;"><span>M</span><span style="color:#99CC00;">E</span></span><span>Mosaic<span>Earth</span></span></a>
        <nav class="dir-nav" aria-label="Main Geospatial Navigation">
            <a href="/{norm_lang}/Map">3D Globe</a>
            <a href="/{norm_lang}/explore/maps">Maps &amp; Layers</a>
            <a href="/{norm_lang}/explore/3d-models">3D Models</a>
            <a href="/{norm_lang}/explore/point-clouds">Point Clouds</a>
            <a href="/{norm_lang}/Faqs">Help &amp; FAQs</a>
            <a href="https://mosaicearth.app/directory">Formats &amp; Guides</a>
            <a href="{alt_url}">{"EN · English" if is_es else "ES · Español"}</a>
            <a class="dir-btn" href="/{norm_lang}/Map">{"Abrir Globo 3D" if is_es else "Launch 3D Globe"}</a>
        </nav>
    </header>
    <main class="dir-container">
        {content_html}
    </main>
    <footer class="dir-footer">
        <div>MosaicEarth — Global 3D Geospatial Digital Twin Platform (app.mosaicearth.app)</div>
        <div style="display:flex;flex-wrap:wrap;gap:14px;">
            <a href="/en/directory">Directory (EN)</a>
            <a href="/es/directory">Directorio (ES)</a>
            <a href="/en/Map">3D Globe</a>
            <a href="/en/Faqs">Help &amp; FAQs</a>
            <a href="/License.aspx">Standard License</a>
            <a href="/Terms.aspx">Terms</a>
            <a href="https://mosaicearth.app/directory">Landing pSEO Directory</a>
        </div>
    </footer>
</body>
</html>"""


def render_product_detail_page(
    publish_id: Optional[int] = None,
    project_id: Optional[int] = None,
    file_type_id: Optional[int] = None,
    lang: str = "en",
    user: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    norm_lang = normalize_lang(lang)
    store = get_store()

    pub_row = None
    proj_row = None
    is_published = False

    if publish_id and publish_id > 0:
        for p in store.tables.get("dbo.WEB_PUBLICATIONS", []):
            if int(p.get("ID") or 0) == publish_id:
                pub_row = p
                is_published = int(p.get("STATUS_ID") or p.get("STATE_ID") or 0) == 2
                project_id = int(p.get("PROJECT_ID") or 0)
                file_type_id = int(p.get("FILE_TYPE_ID") or 2)
                break
        if not pub_row and not project_id:
            # Fallback if trailing int in slug was a project_id
            for p in store.tables.get("dbo.WEB_PUBLICATIONS", []):
                if int(p.get("PROJECT_ID") or 0) == publish_id:
                    pub_row = p
                    publish_id = int(p.get("ID") or 0)
                    is_published = int(p.get("STATUS_ID") or p.get("STATE_ID") or 0) == 2
                    project_id = int(p.get("PROJECT_ID") or 0)
                    file_type_id = int(p.get("FILE_TYPE_ID") or 2)
                    break

    if project_id:
        proj_row = store.get_project(project_id)

    if not proj_row and not pub_row:
        return None

    ftype = int(file_type_id or (pub_row.get("FILE_TYPE_ID") if pub_row else 2) or 2)
    pid = int(project_id or (proj_row.get("ID") if proj_row else 0))
    name = str(
        (pub_row.get("NAME") if pub_row and pub_row.get("NAME") else None)
        or (proj_row.get("NAME") if proj_row else None)
        or f"MosaicAsset #{publish_id or pid}"
    )
    description_raw = str(
        (pub_row.get("DESCRIPTION") if pub_row and pub_row.get("DESCRIPTION") else None)
        or (proj_row.get("DESCRIPTION") if proj_row else None)
        or ""
    )
    folder = str(proj_row.get("FOLDER") or to_slug(f"{pid}-{name}")) if proj_row else ""
    loc_row = store.get_project_location(pid) if pid else None
    lat_str = str((loc_row.get("LATITUD") if loc_row else None) or (proj_row.get("LATITUD") if proj_row else None) or "41.9702").replace(",", ".")
    lon_str = str((loc_row.get("LONGITUD") if loc_row else None) or (proj_row.get("LONGITUD") if proj_row else None) or "2.3885").replace(",", ".")
    try:
        lat_val = float(lat_str)
        lon_val = float(lon_str)
    except Exception:
        lat_val, lon_val = 41.9702, 2.3885

    cat_id = int(
        (pub_row.get("CATEGORY_ID") if pub_row and pub_row.get("CATEGORY_ID") else 0)
        or (proj_row.get("CATEGORY_ID") if proj_row and proj_row.get("CATEGORY_ID") else 0)
        or 14
    )
    cat_info = DEFAULT_CATEGORIES.get(cat_id, {"name": "Monuments", "slug": "monuments"})
    cat_name = cat_info["name"]
    cat_slug = cat_info["slug"]

    credits_val = int(round(float((pub_row.get("CREDITS") if pub_row else 0) or 0)))
    asset_type_display = "Point cloud" if ftype == 1 else "3D model" if ftype == 3 else "Orthophoto"
    asset_icon = ICON_CLOUD if ftype == 1 else ICON_3D if ftype == 3 else ICON_ORTHOPHOTO
    download_fmt = "LAS · ZIP" if ftype == 1 else "OBJ · ZIP" if ftype == 3 else "GeoTIFF · TIF"

    creator_display = "MosaicEarth creator"
    if proj_row and (proj_row.get("CREATE_ID") or proj_row.get("USER_ID")):
        u = store.get_user_by_id(int(proj_row.get("CREATE_ID") or proj_row.get("USER_ID")))
        if u:
            creator_display = str(u.get("USERNAME") or u.get("NAME") or "MosaicEarth creator")

    pub_date_raw = str((pub_row.get("PUBLISH_DATE") or pub_row.get("CREATE_DATE") or pub_row.get("DATE") if pub_row else None) or (proj_row.get("CREATE_DATE") if proj_row else None) or "2026-09-15")
    pub_date_display = pub_date_raw[:10]
    try:
        parts_d = pub_date_display.split("-")
        if len(parts_d) == 3:
            pub_date_display = f"{parts_d[2]}/{parts_d[1]}/{parts_d[0]}"
    except Exception:
        pass

    # Look up real processing metrics from dbo.WEB_PROJECTS_VALUES (Area, GPSAverage, GSDAverage)
    pvals = store.get_project_values(pid) if hasattr(store, "get_project_values") else None
    if not pvals:
        for pv in store.tables.get("dbo.WEB_PROJECTS_VALUES", []):
            if int(pv.get("PROJECT_ID") or 0) == pid:
                pvals = pv
                break

    coverage_display = "Not provided"
    gps_display = "Not provided"
    gsd_display = "Not provided"
    pvals = pvals or {}
    contract = (proj_row or {}).get("CANONICAL_RESULT_CONTRACT") or {}
    # Current ingestion preserves units in AREA/GSD; older tables used numeric
    # square metres and centimetres. Never parse a hectare value as square metres.
    area_f = None
    area_ha = _try_float(contract.get("area_ha"))
    if area_ha is not None:
        area_f = area_ha * 10000.0
    else:
        area_raw = str(pvals.get("AREA") or pvals.get("Area") or "").strip()
        match = re.fullmatch(r"([0-9]+(?:[.,][0-9]+)?)\s*(ha|m²|m2)?", area_raw, re.IGNORECASE)
        if match:
            area_f = float(match.group(1).replace(",", ".")) * (10000 if (match.group(2) or "").lower() == "ha" else 1)
    if area_f is not None and area_f > 0:
        if area_f < 0.01:
            coverage_display = "Less than 0.01 m²"
        elif area_f < 10000:
            coverage_display = f"{area_f:.2f}".rstrip("0").rstrip(".") + " m²"
        else:
            coverage_display = f"{(area_f / 10000.0):.2f}".rstrip("0").rstrip(".") + " ha"
    gps_raw = str(pvals.get("GPS_AVERAGE") or pvals.get("GPSAverage") or pvals.get("AVERAGE_GPS") or contract.get("gps_str") or "").strip()
    if gps_raw and gps_raw != "-":
        gps_f = _try_float(gps_raw)
        gps_display = f"{gps_f:.2f}".rstrip("0").rstrip(".") + " m" if gps_f is not None else gps_raw
    gsd_raw = str(pvals.get("GSD_AVERAGE") or pvals.get("GSDAverage") or pvals.get("GSD") or contract.get("gsd_str") or "").strip()
    if gsd_raw and gsd_raw != "-":
        gsd_f = _try_float(gsd_raw)
        gsd_display = f"{gsd_f:.2f}".rstrip("0").rstrip(".") + " cm/px" if gsd_f is not None else gsd_raw

    asset_key = f"processed-{ftype}-{pid}"
    capture_display = "Unknown"
    capture_source_display = ""
    for trow in store.tables.get("dbo.MOSAICEARTH_ASSET_TEMPORAL", []):
        if str(trow.get("ASSET_KEY") or "").strip().lower() == asset_key.lower():
            cs = str(trow.get("CAPTURE_START_UTC") or "")[:10]
            if cs and len(cs.split("-")) == 3:
                sp = cs.split("-")
                capture_display = f"{sp[2]}/{sp[1]}/{sp[0]}"
            src = str(trow.get("CAPTURE_SOURCE") or "").strip()
            if src and src.lower() != "unknown":
                capture_source_display = f"Captured date from {src}"
            break
    if capture_display == "Unknown":
        for pf in store.tables.get("dbo.WEB_PROJECTS_FILES", []):
            if int(pf.get("PROJECT_ID") or 0) == pid and pf.get("CAPTURE_DATE"):
                cs = str(pf.get("CAPTURE_DATE"))[:10]
                if len(cs.split("-")) == 3:
                    sp = cs.split("-")
                    capture_display = f"{sp[2]}/{sp[1]}/{sp[0]}"
                    capture_source_display = "Captured date from image EXIF"
                    break

    tags_raw = str((pub_row.get("TAGS") if pub_row and pub_row.get("TAGS") else None) or (proj_row.get("TAGS") if proj_row else None) or "")
    tags_html = "".join(
        f'<span class="tag">{html.escape(t.strip())}</span>'
        for t in tags_raw.split(",")
        if t.strip()
    )

    canonical_path = (
        build_processed_asset_path(publish_id, name, norm_lang)
        if publish_id
        else f"/Client/ProductDetail.aspx?project_id={pid}&type={ftype}"
    )
    canonical_url = f"{get_public_site_url()}{canonical_path}"

    world_link = build_4dtwinmaps_world_link(
        {
            "Key": asset_key,
            "PublishId": publish_id or 0,
            "ExternalAssetId": 0,
            "FileTypeId": ftype,
            "Latitude": lat_val,
            "Longitude": lon_val,
            "Name": name,
        }
    )

    cesium_token = store.get_cesium_browser_token()
    height_val = 0.0
    for cm in store.tables.get("dbo.WEB_CESIUM_MODELS", []):
        if int(cm.get("Project_id") or cm.get("ID_PROJECT") or 0) == pid and int(cm.get("ModelType") or cm.get("TYPE") or 0) == ftype:
            try:
                height_val = float(cm.get("Height") or 0.0)
            except Exception:
                height_val = 0.0
            break

    tiles_url = (
        f"/ProcessedAssetFile.ashx/{folder}/procesado/3d_tiles/pointcloud/tileset.json"
        if ftype == 1
        else f"/ProcessedAssetFile.ashx/{folder}/procesado/output/tileset.json"
    )
    ortho_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/orthophoto_tiles/"

    title = f"{name} — {asset_type_display} ({cat_name}) · MosaicEarth"
    coords_str = f"{lat_val:.4f}, {lon_val:.4f}"
    description_meta = (
        f"{description_raw[:150]} ({asset_type_display}, {download_fmt} at {coords_str})."
        if description_raw
        else f"Interactive 3D {asset_type_display.lower()} of {name} ({cat_name}) at WGS84 coordinates {coords_str}, downloadable as {download_fmt} on MosaicEarth."
    )
    og_image = build_dynamic_og_image_url(
        name,
        f"{asset_type_display} · Category: {cat_name} · Format: {download_fmt}",
        f"{asset_type_display.upper()} DATASET",
        f"{cat_name} · {coords_str}",
    )

    fam_info = resolve_family(str(ftype))
    crumbs = [
        ("MosaicEarth", get_landing_site_url()),
        ("3D Globe & Catalogue", f"/{norm_lang}/Map"),
    ]
    if fam_info:
        crumbs.append((family_display(ftype), f"/{norm_lang}/explore/{fam_info['route_slug']}"))
    crumbs.append((name, canonical_path))

    seo_head = build_head_metadata_html(
        title=title,
        description=description_meta,
        canonical_path=canonical_path,
        og_type="article",
        og_image_url=og_image,
        robots="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" if is_published else "noindex, nofollow",
        json_ld_graphs=[
            build_dataset_json_ld(
                name=name,
                description=description_meta,
                canonical_url=canonical_url,
                creator_name=creator_display,
                family_name=asset_type_display,
                download_format=download_fmt,
                latitude=lat_val,
                longitude=lon_val,
                date_published=pub_date_display,
                price_credits=credits_val,
                preview_image_url=og_image,
                keywords=[name, asset_type_display, download_fmt, cat_name, "CesiumJS", "3D Tiles", "Geospatial"],
            ),
            build_breadcrumb_json_ld(crumbs),
        ],
    )

    head_extra = """
    <link rel="stylesheet" href="/assets/css/marketplace.css?v=20261104" />
    <link rel="stylesheet" href="/assets/css/external-asset-import.css?v=9" />
    <link rel="stylesheet" href="/assets/css/external-asset-detail.css?v=8" />
    <link rel="stylesheet" href="/assets/css/external-asset-detail-unified.css?v=7" />
    <link rel="stylesheet" href="/assets/css/mosaic-map-controls.css?v=7" />
    <style>
        .processed-asset-facts > div {
            display: grid !important;
            grid-template-columns: 1fr !important;
            align-items: flex-start !important;
            gap: 4px !important;
            padding: 12px 16px !important;
        }
        .processed-asset-facts dt {
            color: #8d9992 !important;
            font-size: 10px !important;
            font-weight: 700 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.04em !important;
        }
        .processed-asset-facts dd {
            margin: 0 !important;
            color: #eef3ef !important;
            font-size: 13px !important;
            font-weight: 700 !important;
            text-align: left !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
            line-height: 1.4 !important;
        }
        .asset-summary-lines p {
            grid-template-columns: 1fr !important;
            gap: 4px !important;
        }
    </style>
    <script src="/vendor/cesium/Cesium.js"></script>
    <script src="/js/front/MosaicCesiumRuntime.js?v=__MOSAIC_MAP_RUNTIME_VERSION__"></script>
    <script src="/js/front/MosaicAnnotations.js?v=20261016-r45"></script>
    <link rel="stylesheet" href="/css/MosaicAnnotations.css?v=20261016-r45" />
    <link href="/vendor/cesium/Widgets/widgets.css" rel="stylesheet" />"""

    if ftype == 2:
        viewer_block = f"""
        <div id="processedAssetViewer" class="processed-asset-viewer"></div>
        <div class="processed-map-switch mosaic-map-style-switch"><button type="button" class="active" data-layer="satellite">Satellite</button><button type="button" data-layer="topographic">Topographic</button></div>
        <button type="button" class="processed-locate mosaic-map-locate" title="Locate MosaicAsset" aria-label="Locate MosaicAsset"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3"></path></svg></button>
        <script>
            (function(){{
                function createBaseProvider(layer){{return MosaicCesiumRuntime.createBaseProvider(layer);}}
                var cesiumBrowserToken={json.dumps(cesium_token)};
                Cesium.Ion.defaultAccessToken=cesiumBrowserToken;
                var processedViewer=MosaicCesiumRuntime.createViewer('processedAssetViewer',{{imageryProvider:createBaseProvider('satellite'),timeline:false,animation:false,fullscreenButton:false,homeButton:false,sceneModePicker:false,baseLayerPicker:false,navigationHelpButton:false,geocoder:false,infoBox:false,selectionIndicator:false}},cesiumBrowserToken,{{requestVertexNormals:true,requestWaterMask:true}});
                var processedBaseLayer=processedViewer.imageryLayers.length?processedViewer.imageryLayers.get(0):null;
                processedViewer.scene.globe.enableLighting=false; processedViewer.scene.globe.depthTestAgainstTerrain=!!cesiumBrowserToken; processedViewer.shadows=false; processedViewer.clock.shouldAnimate=false;
                MosaicCesiumRuntime.loadTmsLayer(processedViewer,{json.dumps(ortho_url)},{{maximumLevel:20}});
                function locate(){{processedViewer.camera.flyTo({{destination:Cesium.Cartesian3.fromDegrees({lon_val},{lat_val},1800),orientation:{{heading:0,pitch:MosaicCesiumRuntime.topDownOffset(0).pitch,roll:0}}}});}} locate();
                document.querySelector('.processed-locate').addEventListener('click',locate);
                document.querySelectorAll('.processed-map-switch button').forEach(function(btn){{
                    btn.addEventListener('click',function(){{
                        document.querySelectorAll('.processed-map-switch button').forEach(function(b){{b.classList.remove('active');}});
                        btn.classList.add('active');
                        var mode=btn.getAttribute('data-layer')||'satellite';
                        if(processedBaseLayer)processedViewer.imageryLayers.remove(processedBaseLayer,true);
                        processedBaseLayer=processedViewer.imageryLayers.addImageryProvider(createBaseProvider(mode),0);
                    }});
                }});
            }})();
        </script>"""
    else:
        sse = "8" if ftype == 1 else "18"
        pc_extra = "if(tile.pointCloudShading){tile.pointCloudShading.attenuation=true;tile.pointCloudShading.maximumAttenuation=3.0;tile.pointCloudShading.geometricErrorScale=2.2;tile.pointCloudShading.baseResolution=0.22;}"
        viewer_block = f"""
        <div id="cesiumContainer" class="processed-asset-viewer"></div>
        <div class="processed-map-switch mosaic-map-style-switch"><button type="button" class="active" data-layer="satellite">Satellite</button><button type="button" data-layer="topographic">Topographic</button></div>
        <button type="button" class="processed-locate mosaic-map-locate" title="Locate MosaicAsset" aria-label="Locate MosaicAsset"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3"></path></svg></button>
        <script>
            (function(){{
                function createBaseProvider(layer){{return MosaicCesiumRuntime.createBaseProvider(layer);}}
                var cesiumBrowserToken={json.dumps(cesium_token)};
                Cesium.Ion.defaultAccessToken=cesiumBrowserToken;
                var assetViewer = MosaicCesiumRuntime.createViewer("cesiumContainer", {{
                    imageryProvider: createBaseProvider('satellite'),
                    timeline: false, animation: false, fullscreenButton: false, homeButton: false,
                    sceneModePicker: false, baseLayerPicker: false, projectionPicker: false,
                    scene3DOnly: true, geocoder: false, infoBox: false, selectionIndicator: false
                }}, cesiumBrowserToken, {{requestVertexNormals:true,requestWaterMask:true}});
                var assetBaseLayer=assetViewer.imageryLayers.length?assetViewer.imageryLayers.get(0):null;
                MosaicCesiumRuntime.enableFeatureMetadataInspector(assetViewer, 'cesiumContainer');
                assetViewer.scene.globe.enableLighting=false;
                assetViewer.scene.globe.depthTestAgainstTerrain=!!cesiumBrowserToken;
                assetViewer.shadows=false;
                assetViewer.clock.shouldAnimate=false;
                var assetTileset=null;
                function locateAsset(){{if(!assetTileset)return;var range=Math.max(assetTileset.boundingSphere.radius*2.6,120);MosaicCesiumRuntime.flyToTopDown(assetViewer,assetTileset,range,.8);}}
                MosaicCesiumRuntime.loadTileset(assetViewer,{json.dumps(tiles_url)},{{maximumScreenSpaceError:{sse},enableCollision:false}}).then(function(tile){{assetTileset=tile;MosaicCesiumRuntime.applyProcessedTilesetHeight(tile,{height_val});{pc_extra}locateAsset();}});
                document.querySelector('.processed-locate').addEventListener('click',locateAsset);
                document.querySelectorAll('.processed-map-switch button').forEach(function(btn){{
                    btn.addEventListener('click',function(){{
                        document.querySelectorAll('.processed-map-switch button').forEach(function(b){{b.classList.remove('active');}});
                        btn.classList.add('active');
                        var mode=btn.getAttribute('data-layer')||'satellite';
                        if(assetBaseLayer)assetViewer.imageryLayers.remove(assetBaseLayer,true);
                        assetBaseLayer=assetViewer.imageryLayers.addImageryProvider(createBaseProvider(mode),0);
                    }});
                }});
            }})();
        </script>"""

    capture_small_html = f"<small>{html.escape(capture_source_display)}</small>" if capture_source_display else ""

    body_content = f"""
    <section class="pt-5">
        <div class="container">
            <div class="row" style="margin-bottom: 15px;">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="row">
                                <div class="col-md-12">
                                    <a class="subpage-nav" href="/Map2.aspx?lon={lon_val}&lat={lat_val}&type={ftype}">MosaicEarth</a>
                                </div>
                            </div>
                        </div>
                        <div class="content-box">
                            <div class="row product-detail-layout">
                                <div class="col-md-7 product-detail-visual">
                                    <div class="product-detail-img mosaic-map-shell">
                                        <div class="icon icon-top icon-map">
                                            <a href="/Map2.aspx?lon={lon_val}&lat={lat_val}&type={ftype}">
                                                <img src="/assets/resources/icon-map.svg" alt="Open on 3D Globe" />
                                            </a>
                                        </div>
                                        <span class="asset-viewer-type" title="{html.escape(asset_type_display)}" aria-label="{html.escape(asset_type_display)}">
                                            {asset_icon}
                                        </span>
                                        {viewer_block}
                                    </div>
                                    <p class="image-info"><i class="fas fa-info-circle me-2"></i>The actual 2D or 3D object when downloading has a higher resolution than the preview above.</p>
                                </div>
                                <div class="col-md-5 product-box-buy">
                                    <div class="processed-publication-state {'is-published' if is_published else 'is-private'}">{('PUBLISHED · FREE (0 €)' if credits_val <= 0 else f'PUBLISHED · MARKETPLACE ({credits_val} €)') if is_published else 'NOT PUBLISHED · PRIVATE (OWNER ONLY)'}</div>
                                    <h1 class="product-title">{html.escape(name)}</h1>
                                    <div class="asset-detail-actions" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
                                        <a id="btnView" class="btn btn-primary" href="/Potree.aspx?id={pid}&type={ftype}&pub={publish_id or 0}" target="_blank" rel="noopener">OPEN VIEWER FOR SHARING</a>
                                        <a id="btnFlyFirstPersonAsset" class="btn" style="background:#99CC00;color:#070b09;font-weight:700;" href="/Fly.aspx?asset_id={html.escape(asset_key, quote=True)}&amp;lat={lat_val}&amp;lon={lon_val}" title="Fly in 1st Person over this MosaicAsset">
                                            <i class="fas fa-plane-departure me-1"></i> Fly in 1st Person
                                        </a>
                                    </div>
                                    <div class="asset-summary-lines">
                                        <p><span>{"Published" if pub_row else "Created"}</span><b>{html.escape(pub_date_display)}</b></p>
                                        <p><span>Visibility &amp; Storage</span><b>{'Permanent Public & Marketplace Hosting' if is_published else 'Private Asset (Visible only to Creator & in Creator MyMosaics as View-Only)'}</b></p>
                                        <p><span>Creator</span><b>{html.escape(creator_display)}</b></p>
                                    </div>
                                    <dl class="processed-asset-facts">
                                        <div><dt>Coverage</dt><dd>{html.escape(coverage_display)}</dd></div>
                                        <div><dt>Average GPS accuracy <small>Mean positional accuracy reported by processing</small></dt><dd>{html.escape(gps_display)}</dd></div>
                                        <div><dt>Ground resolution <small>Ground sampling distance (GSD)</small></dt><dd>{html.escape(gsd_display)}</dd></div>
                                        <div><dt>Captured{capture_small_html}</dt><dd>{html.escape(capture_display)}</dd></div>
                                        <div><dt>Download</dt><dd>{html.escape(download_fmt)}</dd></div>
                                    </dl>
                                    <p>License &amp; Download Access</p>
                                    <div class="row mb-4 asset-buy-row">
                                        <div class="col-6 content-center asset-price-cell">
                                            <p><span class="price me-2">{'PRIVATE' if not is_published else ('FREE (0 €)' if credits_val <= 0 else f'{credits_val} €')}</span> {'' if (not is_published or credits_val <= 0) else '<span class="credits">Commercial License (85% Creator)</span>'}</p>
                                        </div>
                                        <div class="col-6 content-center asset-license-cell">
                                            <p>MosaicEarth Standard License<br /><small>{html.escape(SHORT_LICENSE_SUMMARY)}</small> <i class="fas fa-info-circle ms-2 cursor-pointer" data-bs-toggle="modal" data-bs-target="#modal-info"></i></p>
                                        </div>
                                        <div class="col-12 content-center asset-action-cell">
                                            <button type="button" id="btnAddCollection" class="btn btn-primary asset-primary-action" data-bs-toggle="modal" data-bs-target="#modal-purchase">
                                                {'DOWNLOAD SOURCE (OWNER)' if not is_published else ('ACQUIRE FREE MOSAICASSET (0 €) & DOWNLOAD' if credits_val <= 0 else f'BUY COMMERCIAL LICENSE ({credits_val} €) TO DOWNLOAD')}
                                            </button>
                                        </div>
                                    </div>
                                    <p class="mb-3">Description</p>
                                    <p class="description">{html.escape(description_raw).replace(chr(10), '<br />')}</p>
                                    <nav aria-label="Geospatial directory navigation" style="margin-bottom:10px;font-size:12px;display:flex;flex-wrap:wrap;gap:10px;">
                                        <a href="/en/directory" style="color:#99CC00;text-decoration:none;font-weight:600;">Full Geospatial Directory</a>
                                        <a href="/en/explore/maps" style="color:#9ca3af;text-decoration:none;">Maps &amp; Layers</a>
                                        <a href="/en/explore/3d-models" style="color:#9ca3af;text-decoration:none;">3D Models</a>
                                        <a href="/en/explore/point-clouds" style="color:#9ca3af;text-decoration:none;">Point Clouds</a>
                                        <a href="https://mosaicearth.app/directory" style="color:#9ca3af;text-decoration:none;">Formats &amp; Guides Hub</a>
                                    </nav>
                                    <div class="asset-tags-panel">
                                        <a class="category" href="/en/category/{html.escape(cat_slug)}" style="text-decoration:none;">{html.escape(cat_name)}</a>
                                        {tags_html}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>
    <div class="modal" tabindex="-1" id="modal-info">
        <div class="modal-dialog modal-dialog-centered modal-dark">
            <div class="modal-content">
                <div class="modal-header ps-4 pe-4 pt-4"><h5 class="modal-title">MosaicEarth Standard License</h5></div>
                <div class="modal-body ps-4 pe-4">
                    <p class="license-short-summary">{html.escape(SHORT_LICENSE_SUMMARY)}</p>
                    <pre class="mosaicearth-license-text">{html.escape(CURRENT_LICENSE_TEXT)}</pre>
                </div>
                <div class="modal-footer text-start ps-4 pe-4 pb-4">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    </div>
    <div class="modal" tabindex="-1" id="modal-purchase">
        <div class="modal-dialog modal-dialog-centered modal-dark">
            <div class="modal-content">
                <div class="modal-header ps-4 pe-4 pt-4"><h5 class="modal-title">{'Acquire Free MosaicAsset (0 €)' if credits_val <= 0 else f'Purchase Commercial License ({credits_val} €)'}</h5></div>
                <div class="modal-body ps-4 pe-4">
                    <p>You are about to acquire: <b>{html.escape(name)}</b></p>
                    <p><span class="price me-3">{'FREE (0 €)' if credits_val <= 0 else f'{credits_val} €'}</span> <span class="credits">{'Includes original high-res download & MyMosaics reuse' if credits_val <= 0 else 'Stripe Checkout · 85% Direct to Creator / 15% Platform'}</span></p>
                </div>
                <div class="modal-footer text-start ps-4 pe-4 pb-4">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                    <button type="button" id="btnPurchaseNow" class="btn btn-primary">{'Acquire & Download (0 €)' if credits_val <= 0 else f'Confirm License ({credits_val} €)'}</button>
                </div>
            </div>
        </div>
    </div>"""

    scripts_extra = f"""
    <script>
        MYLIBRARY.setUrl_page('/Client/ProductDetail.aspx');
        var purchaseNowButton = document.getElementById('btnPurchaseNow');
        if (purchaseNowButton) purchaseNowButton.addEventListener('click', function (event) {{
            event.preventDefault();
            $.ajax({{
                type: "POST",
                dataType: 'json',
                contentType: "application/json; charset=utf-8",
                url: "/Client/ProductDetail.aspx/PurchaseNow",
                data: JSON.stringify({{ publishId: {publish_id or 0} }}),
                success: function (response) {{
                    var json = typeof response.d === 'string' ? JSON.parse(response.d) : response.d;
                    if (json && json.Error) {{
                        alert(json.Message || "Sign in to acquire this MosaicAsset.");
                    }} else if (json && json.RequiresStripe && json.CheckoutUrl) {{
                        window.location.href = json.CheckoutUrl;
                    }} else {{
                        window.location.href = "/MosaicAssetDownload.ashx?source=processed&publishId={publish_id or 0}&projectId={pid}&fileTypeId={ftype}";
                    }}
                }}
            }});
        }});
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_external_asset_detail_page(
    project_id: int,
    asset_id: int,
    lang: str = "en",
    user: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    norm_lang = normalize_lang(lang)
    store = get_store()
    store.refresh_processing_state()
    ext = store.get_external_asset(project_id, asset_id)
    from .asset_uploads import asset_output_available, gaussian_training_input
    if not ext or not asset_output_available(ext):
        return None

    name = str(ext.get("TITLE") or ext.get("DISPLAY_NAME") or f"MosaicAsset #{asset_id}")
    asset_type = str(ext.get("ASSET_TYPE") or "model3d").lower()
    ftype = 1 if asset_type == "pointcloud" else 3 if asset_type == "model3d" else 2
    original_fmt = Path(str(ext.get("SOURCE_FILE_NAME") or "")).suffix.lstrip(".").upper()
    fmt = str(ext.get("OUTPUT_FORMAT") or ext.get("SOURCE_FORMAT") or ext.get("FORMAT") or "GLB").upper()
    quality = (ext.get("INSPECTION_META") or {}).get("result_quality") or {}
    quality_label = " · ".join(str(quality[k]).replace("_", " ") for k in ("quality_state", "georeferencing_state") if quality.get(k))
    metrics = (ext.get("INSPECTION_META") or {}).get("training_metrics") or {}
    if metrics.get("preset_id"):
        preset_label = str(metrics["preset_id"]).replace("_", " ").title()
        validation_label = "Quality check passed" if quality.get("passed") else str(quality.get("quality_state") or "Quality not measured")
        placement_label = str(quality.get("georeferencing_state") or "").replace("_", " ")
        quality_label = " · ".join(x for x in (f"{preset_label} preset", validation_label, placement_label) if x)
    measured = []
    for key, label, unit in (("psnr_val_db", "PSNR", " dB"), ("ssim_val", "SSIM", ""), ("lpips_val", "LPIPS", "")):
        value = metrics.get(key)
        if isinstance(value, (float, int)):
            measured.append(f"{label}: {value:.4f}{unit}")
    metrics_html = ""
    if metrics:
        metric_text = html.escape(" · ".join(measured) or "Validation metrics not measured")
        metrics_html = f'<p style="color:#d1d5db;margin-top:10px;">Held-out validation view · {metric_text}</p><a href="/ExternalAssetFile.ashx?project_id={project_id}&amp;asset_id={asset_id}&amp;mode=quality_report" download="quality.json">Download quality report</a>'
    lat_val = float(ext["LATITUDE"]) if ext.get("LATITUDE") is not None else 41.9702
    lon_val = float(ext["LONGITUDE"]) if ext.get("LONGITUDE") is not None else 2.3885
    coordinate_label = f"{abs(lat_val):.4f}°{'N' if lat_val >= 0 else 'S'}, {abs(lon_val):.4f}°{'E' if lon_val >= 0 else 'W'}"
    alt_val = float(ext.get("ALTITUDE") or 0.0)
    heading_val = float(ext.get("HEADING") or 0.0)
    scale_val = float(ext.get("SCALE") or 1.0)
    price_credits = int(ext.get("PRICE_CREDITS") or 0)
    is_pub = bool(ext.get("IS_PUBLISHED"))
    viewer_uid = int((user or {}).get("ID") or 0)
    owner_uid = int(ext.get("USER_ID") or (store.get_project(project_id) or {}).get("CREATE_ID") or 0)
    can_download = viewer_uid > 0 and (viewer_uid == owner_uid or store.user_has_purchased_asset(viewer_uid, external_asset_id=asset_id))
    needs_purchase = is_pub and price_credits > 0 and not can_download
    desc_raw = str(ext.get("DESCRIPTION") or "")
    viewer_kind = str(ext.get("VIEWER_KIND") or "").lower()

    canonical_path = build_external_asset_path(project_id, asset_id, name, norm_lang)
    canonical_url = f"{get_public_site_url()}{canonical_path}"
    asset_key = f"external-{ftype}-{asset_id}"

    world_link = build_4dtwinmaps_world_link(
        {
            "Key": asset_key,
            "PublishId": 0,
            "ExternalAssetId": asset_id,
            "FileTypeId": ftype,
            "Latitude": lat_val,
            "Longitude": lon_val,
            "Name": name,
        }
    )

    title = f"{name} — {family_display(ftype)} ({fmt}) · MosaicEarth"
    desc_meta = desc_raw or f"{family_display(ftype)} ({fmt}) placed at WGS84 {coordinate_label} on MosaicEarth."
    seo_head = build_head_metadata_html(
        title=title,
        description=desc_meta,
        canonical_path=canonical_path,
        og_type="article",
        json_ld_graphs=[
            build_dataset_json_ld(
                name=name,
                description=desc_meta,
                canonical_url=canonical_url,
                creator_name="MosaicEarth creator",
                family_name=family_display(ftype),
                download_format=fmt,
                latitude=lat_val,
                longitude=lon_val,
                date_published=str(ext.get("PUBLISHED_AT_UTC") or "2026-10-01")[:10],
                price_credits=price_credits,
            ),
            build_breadcrumb_json_ld(
                [
                    ("MosaicEarth", get_landing_site_url()),
                    ("3D Globe", f"/{norm_lang}/Map"),
                    (name, canonical_path),
                ]
            ),
        ],
    )

    cesium_token = store.get_cesium_browser_token()
    head_extra = """
    <link rel="stylesheet" href="/assets/css/marketplace.css?v=20261104" />
    <link rel="stylesheet" href="/assets/css/external-asset-import.css?v=9" />
    <link rel="stylesheet" href="/assets/css/external-asset-detail.css?v=8" />
    <link rel="stylesheet" href="/assets/css/external-asset-detail-unified.css?v=7" />
    <link rel="stylesheet" href="/assets/css/mosaic-map-controls.css?v=7" />
    <style>
        .processed-asset-facts > div {
            display: grid !important;
            grid-template-columns: 1fr !important;
            align-items: flex-start !important;
            gap: 4px !important;
            padding: 12px 16px !important;
        }
        .processed-asset-facts dt {
            color: #8d9992 !important;
            font-size: 10px !important;
            font-weight: 700 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.04em !important;
        }
        .processed-asset-facts dd {
            margin: 0 !important;
            color: #eef3ef !important;
            font-size: 13px !important;
            font-weight: 700 !important;
            text-align: left !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
            line-height: 1.4 !important;
        }
        .asset-summary-lines p {
            grid-template-columns: 1fr !important;
            gap: 4px !important;
        }
    </style>
    <script src="/vendor/cesium/Cesium.js"></script>
    <script src="/js/front/MosaicCesiumRuntime.js?v=__MOSAIC_MAP_RUNTIME_VERSION__"></script>
    <script src="/js/front/MosaicAnnotations.js?v=20261103"></script>
    <link rel="stylesheet" href="/css/MosaicAnnotations.css?v=20261103" />
    <link href="/vendor/cesium/Widgets/widgets.css" rel="stylesheet" />"""

    from .asset_metadata import derivative_version
    derivative_v = derivative_version(ext)
    tiles_url = f"/ExternalAsset3DTile.ashx/{project_id}/{asset_id}/tileset.json"
    file_url = f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=preview"
    raster_url = f"/ExternalAssetViewer.ashx?project_id={project_id}&asset_id={asset_id}&z={{z}}&x={{x}}&y={{y}}&v={derivative_v}"

    is_gs_ext = (
        str(ext.get("SOURCE_ORIGIN") or "") == "gaussian"
        or str(ext.get("ASSET_SUBTYPE") or "") == "gaussian_splat"
    )
    if is_gs_ext and not needs_purchase:
        gs_dl_buttons = f"""
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;">
            <a class="btn btn-sm" style="background:rgba(153,204,0,0.14);border:1px solid #99CC00;color:#99CC00;font-weight:700;font-size:11px;" href="/ExternalAssetFile.ashx?project_id={project_id}&amp;asset_id={asset_id}&amp;mode=ply" download="model.ply"><i class="fas fa-download me-1"></i> model.ply (Raw 3DGS)</a>
            <a class="btn btn-sm" style="background:rgba(153,204,0,0.14);border:1px solid #99CC00;color:#99CC00;font-weight:700;font-size:11px;" href="/ExternalAssetFile.ashx?project_id={project_id}&amp;asset_id={asset_id}&amp;mode=spz" download="model.spz"><i class="fas fa-compress-arrows-alt me-1"></i> model.spz (Compressed SPZ v2)</a>
            <a class="btn btn-sm" style="background:rgba(153,204,0,0.14);border:1px solid #99CC00;color:#99CC00;font-weight:700;font-size:11px;" href="/ExternalAssetFile.ashx?project_id={project_id}&amp;asset_id={asset_id}&amp;mode=transform" download="transform.json"><i class="fas fa-compass me-1"></i> transform.json (ECEF Matrix)</a>
        </div>"""
    else:
        gs_dl_buttons = ""

    asset_class_val = str(ext.get("ASSET_CLASS") or "").upper()
    subtype_low = str(ext.get("ASSET_SUBTYPE") or "").lower()
    is_elevation_asset = asset_class_val == "RASTER_ELEVATION" or subtype_low in ("dsm", "dtm", "dem")
    is_generic_raster = asset_class_val == "RASTER_GENERIC"
    elev_meta = ext.get("ELEVATION_META") if isinstance(ext.get("ELEVATION_META"), dict) else {}
    elev_min_m = float(elev_meta.get("min_elevation_m") if elev_meta.get("min_elevation_m") is not None else 0.0)
    elev_max_m = float(elev_meta.get("max_elevation_m") if elev_meta.get("max_elevation_m") is not None else 120.0)
    elev_palette = str(elev_meta.get("palette") or "hypsometric").lower()
    vec_layers = ext.get("VECTOR_LAYERS") if isinstance(ext.get("VECTOR_LAYERS"), list) else []
    src_sha256 = str(ext.get("SOURCE_SHA256") or "")
    canon_status = str(ext.get("CANONICAL_STATUS") or "READY")

    elevation_panel_html = ""
    if is_elevation_asset:
        from .geospatial_pipeline import PALETTE_STOPS
        elevation_legend = ','.join(f'rgb({r},{g},{b}) {level*100:g}%' for level,(r,g,b) in PALETTE_STOPS.get(elev_palette,PALETTE_STOPS['hypsometric']))
        elevation_panel_html = f"""
        <div id="meElevationTerrainPanel" class="mt-3 p-3 rounded" style="background:#141b17;border:1px solid rgba(153,204,0,0.35);">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <strong style="color:#99CC00;font-size:12px;text-transform:uppercase;letter-spacing:0.05em;">Elevation Layer &amp; 3D Terrain Controls</strong>
                <span class="badge" style="background:rgba(153,204,0,0.18);color:#99CC00;font-size:10px;">DEM / {html.escape(subtype_low.upper() or 'ELEVATION')}</span>
            </div>
            <div class="form-check mb-2">
                <input class="form-check-input" type="checkbox" id="chkShowElevationLayer" checked onchange="window.updateElevationLayerState && window.updateElevationLayerState()">
                <label class="form-check-label" for="chkShowElevationLayer" style="font-size:12px;color:#eef3ef;">Show elevation layer (Color Relief)</label>
            </div>
            <div class="row g-2 align-items-center mb-2">
                <div class="col-5"><label style="font-size:11px;color:#9ca3af;">Palette</label></div>
                <div class="col-7">
                    <select id="selElevationPalette" class="form-select form-select-sm bg-dark text-light" style="font-size:11px;" onchange="window.changeElevationPalette && window.changeElevationPalette(this.value)">
                        <option value="hypsometric" {'selected' if elev_palette == 'hypsometric' else ''}>Hypsometric</option>
                        <option value="viridis" {'selected' if elev_palette == 'viridis' else ''}>Viridis</option>
                        <option value="terrain" {'selected' if elev_palette == 'terrain' else ''}>Terrain</option>
                        <option value="grayscale" {'selected' if elev_palette == 'grayscale' else ''}>Grayscale</option>
                    </select>
                </div>
            </div>
            <div class="row g-2 align-items-center mb-2">
                <div class="col-5"><label style="font-size:11px;color:#9ca3af;">Opacity (<span id="lblElevOpacity">100%</span>)</label></div>
                <div class="col-7">
                    <input type="range" class="form-range" id="rngElevationOpacity" min="0" max="100" value="100" oninput="window.updateElevationLayerState && window.updateElevationLayerState()">
                </div>
            </div>
            <div class="mb-2">
                <div style="display:flex;justify-content:space-between;font-size:11px;color:#d1d5db;margin-bottom:3px;">
                    <span>Range: <b id="lblElevMin">{elev_min_m:.1f} m</b></span>
                    <span><b id="lblElevMax">{elev_max_m:.1f} m</b></span>
                </div>
                <div style="height:10px;border-radius:5px;background:linear-gradient(90deg,{elevation_legend});border:1px solid rgba(255,255,255,0.18);"></div>
            </div>
            <div class="form-check mb-3">
                <input class="form-check-input" type="checkbox" id="chkHillshadeOverlay" checked onchange="window.updateElevationLayerState && window.updateElevationLayerState()">
                <label class="form-check-label" for="chkHillshadeOverlay" style="font-size:12px;color:#eef3ef;">Hillshade overlay</label>
            </div>
            <hr style="border-color:rgba(255,255,255,0.12);margin:8px 0;" />
            <div style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;margin-bottom:6px;">Terrain Surface (OFF by default)</div>
            <div class="form-check">
                <input class="form-check-input" type="radio" name="meTerrainMode" id="radTerrainDefault" value="default" checked onchange="window.switchLocalDemTerrain && window.switchLocalDemTerrain('default')">
                <label class="form-check-label" for="radTerrainDefault" style="font-size:12px;color:#eef3ef;">Default terrain (Global)</label>
            </div>
            <div class="form-check">
                <input class="form-check-input" type="radio" name="meTerrainMode" id="radTerrainDtm" value="dtm" {'disabled' if subtype_low != 'dtm' else ''} onchange="window.switchLocalDemTerrain && window.switchLocalDemTerrain('dtm')">
                <label class="form-check-label" for="radTerrainDtm" style="font-size:12px;color:#eef3ef;">Local DTM (Bare-Earth Terrain)</label>
            </div>
            <div class="form-check">
                <input class="form-check-input" type="radio" name="meTerrainMode" id="radTerrainDsm" value="dsm" {'disabled' if subtype_low == 'dtm' else ''} onchange="window.switchLocalDemTerrain && window.switchLocalDemTerrain('dsm')">
                <label class="form-check-label" for="radTerrainDsm" style="font-size:12px;color:#eef3ef;">Local DSM (Surface Elevation)</label>
            </div>
            <div id="dsmMeshWarningBanner" style="display:none;margin-top:6px;padding:6px 8px;border-radius:6px;background:rgba(245,158,11,0.15);border:1px solid rgba(245,158,11,0.45);color:#fbbf24;font-size:11px;">
                <i class="fas fa-exclamation-triangle me-1"></i> Note: Local DSM includes surface vegetation/buildings. If a 3D Textured Mesh is also active, use Local DTM or Default terrain to avoid geometry intersection.
            </div>
            <div id="terrainControlStatus" role="status" style="margin-top:6px;font-size:11px;color:#dce6dc;">Local terrain uses this asset. Open the other DSM/DTM asset to use its surface.</div>
        </div>"""

    vector_layers_panel_html = ""
    if vec_layers:
        layer_rows = []
        for l_info in vec_layers:
            l_slug = html.escape(str(l_info.get("slug") or l_info.get("name") or "layer_0"), quote=True)
            l_name = html.escape(str(l_info.get("name") or l_slug))
            l_geom = html.escape(str(l_info.get("geometry_type") or "GEOMETRY"))
            l_cnt = int(l_info.get("feature_count") or 0)
            layer_rows.append(
                f'<div class="form-check mb-1">'
                f'<input class="form-check-input me-gpkg-layer-chk" type="checkbox" id="chkVecLayer_{l_slug}" data-layer-slug="{l_slug}" checked onchange="window.toggleGpkgVectorLayer && window.toggleGpkgVectorLayer(\'{l_slug}\', this.checked)">'
                f'<label class="form-check-label" for="chkVecLayer_{l_slug}" style="font-size:12px;color:#eef3ef;">{l_name} <small style="color:#9ca3af;">({l_geom} · {l_cnt} features)</small></label>'
                f'</div>'
            )
        vector_layers_panel_html = f"""
        <div id="meMultiVectorLayersPanel" class="mt-3 p-3 rounded" style="background:#141b17;border:1px solid rgba(153,204,0,0.35);">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <strong style="color:#99CC00;font-size:12px;text-transform:uppercase;letter-spacing:0.05em;">GeoPackage Multi-Layer Control ({len(vec_layers)} Layers)</strong>
            </div>
            {''.join(layer_rows)}
        </div>"""

    canonical_master_panel_html = f"""
    <div class="mt-3 p-3 rounded" style="background:#111714;border:1px solid rgba(255,255,255,0.12);font-size:11px;">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;">
            <span style="color:#9ca3af;">Master Status: <b style="color:#99CC00;">IMMUTABLE SOURCE ({html.escape(canon_status)})</b></span>
            <button type="button" id="btnRegenerateAsset" {"hidden" if gaussian_training_input(ext) or viewer_uid != owner_uid else ""} class="btn btn-sm" style="background:rgba(153,204,0,0.14);border:1px solid #99CC00;color:#99CC00;font-weight:700;font-size:10px;padding:3px 8px;" onclick="window.regenerateAssetDerivatives && window.regenerateAssetDerivatives({project_id}, {asset_id})">
                <i class="fas fa-sync-alt me-1"></i> Regenerate Derivatives
            </button>
        </div>
        {f'<div style="margin-top:4px;color:#d1d5db;">{html.escape(quality_label)}</div>' if quality_label else ''}
        {metrics_html}
        <div id="assetRegenerationStatus" role="status" aria-live="polite" style="margin-top:8px;color:#dce6dc;"></div>
        {f'<div style="margin-top:4px;color:#a8b4ad;word-break:break-all;">SHA-256: <code>{html.escape(src_sha256[:24])}…</code> · Class: <code>{html.escape(asset_class_val or "GEOSPATIAL")}</code></div>' if src_sha256 else ''}
    </div>"""

    body_content = f"""
    <section class="pt-5">
        <div class="container">
            <div class="box-4d">
                <div class="content-box">
                    <div class="row product-detail-layout">
                        <div class="col-md-7 product-detail-visual">
                            <div class="product-detail-img mosaic-map-shell">
                                <div id="externalAssetCesium" class="processed-asset-viewer" style="min-height:480px;"></div>
                                <div class="processed-map-switch mosaic-map-style-switch"><button type="button" class="active" data-layer="satellite">Satellite</button><button type="button" data-layer="topographic">Topographic</button></div>
                                <button type="button" class="processed-locate mosaic-map-locate" onclick="window.locateExternalAsset && window.locateExternalAsset()" title="Locate MosaicAsset" aria-label="Locate MosaicAsset"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3"></path></svg></button>
                            </div>
                        </div>
                        <div class="col-md-5 product-box-buy">
                            <div class="processed-publication-state {'is-published' if is_pub else 'is-private'}">{('PUBLISHED · FREE (0 €)' if price_credits <= 0 else f'PUBLISHED · MARKETPLACE ({price_credits} €)') if is_pub else 'PRIVATE · OWNER ONLY'}</div>
                            <h1 class="product-title">{html.escape(name)}</h1>
                            <div class="asset-detail-actions" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
                                <a class="btn btn-primary" href="/MapView.aspx?id={project_id}&amp;asset_id={asset_id}" target="_blank" rel="noopener">OPEN FULL VIEWER</a>
                                <a id="btnFlyFirstPersonExt" class="btn" style="background:#99CC00;color:#070b09;font-weight:700;" href="/Fly.aspx?asset_id={html.escape(asset_key, quote=True)}&amp;lat={lat_val}&amp;lon={lon_val}">
                                    <i class="fas fa-plane-departure me-1"></i> Fly in 1st Person
                                </a>
                            </div>
                            <dl class="processed-asset-facts">
                                <div><dt>Family &amp; Format</dt><dd>{html.escape(family_display(ftype))} · {html.escape(fmt)}</dd></div>
                                <div><dt>Coordinates (WGS84)</dt><dd id="extCoordsLabel">{coordinate_label}</dd></div>
                                <div><dt>{'Surface elevation range' if is_elevation_asset else 'Altitude'}</dt><dd id="extAltLabel">{f'{elev_min_m:.1f} – {elev_max_m:.1f} m' if is_elevation_asset else f'{alt_val:.1f} m'}</dd></div>
                                <div><dt>Marketplace Price</dt><dd>{'PRIVATE (Not in Marketplace)' if not is_pub else ('FREE (0 €)' if price_credits <= 0 else f'{price_credits} € · Commercial License')}</dd></div>
                            </dl>
                            <div class="mt-3 mb-3">
                                {f'<button type="button" class="btn btn-primary" onclick="buyExternalMarketplaceAsset({project_id}, {asset_id}, {price_credits})">Buy License ({price_credits} €) &amp; Download ({html.escape(fmt)})</button>' if needs_purchase else f'<a class="btn btn-primary" href="/MosaicAssetDownload.ashx?source=external&projectId={project_id}&assetId={asset_id}&amp;format=original">Download Original ({html.escape(original_fmt or fmt)})</a>'}
                                {gs_dl_buttons}
                                {elevation_panel_html}
                                {vector_layers_panel_html}
                                {canonical_master_panel_html}
                            </div>
                            <p class="mb-3">Description</p>
                            <p class="description">{html.escape(desc_meta)}</p>
                            <nav aria-label="Geospatial directory navigation" style="margin-bottom:10px;font-size:12px;display:flex;flex-wrap:wrap;gap:10px;">
                                <a href="/en/directory" style="color:#99CC00;text-decoration:none;font-weight:600;">Full Geospatial Directory</a>
                                <a href="/en/explore/maps" style="color:#9ca3af;text-decoration:none;">Maps &amp; Layers</a>
                                <a href="/en/explore/3d-models" style="color:#9ca3af;text-decoration:none;">3D Models</a>
                                <a href="/en/explore/point-clouds" style="color:#9ca3af;text-decoration:none;">Point Clouds</a>
                                <a href="https://mosaicearth.app/directory" style="color:#9ca3af;text-decoration:none;">Formats &amp; Guides Hub</a>
                            </nav>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>"""

    scripts_extra = f"""
    <script>
        function buyExternalMarketplaceAsset(projectId, assetId, priceEur) {{
            if (!confirm('Confirm direct purchase of this Marketplace asset for ' + priceEur + ' €? (80%–90% Creator Payout)')) return;
            fetch('/api/v2/checkout/asset', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{externalAssetId: assetId, projectId: projectId}})
            }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
                if (data && data.requiresStripe && data.checkoutUrl) {{
                    window.location.href = data.checkoutUrl;
                }} else if (data && data.ok) {{
                    window.location.href = '/MosaicAssetDownload.ashx?source=external&projectId=' + projectId + '&assetId=' + assetId;
                }} else {{
                    alert((data && data.message) || 'Please sign in to purchase this Marketplace asset.');
                }}
            }});
        }}
        (function(){{
            var token = {json.dumps(cesium_token)};
            Cesium.Ion.defaultAccessToken = token;
            var viewer = MosaicCesiumRuntime.createViewer('externalAssetCesium', {{
                imageryProvider: MosaicCesiumRuntime.createBaseProvider('satellite'),
                timeline: false, animation: false, fullscreenButton: false, homeButton: false,
                sceneModePicker: false, baseLayerPicker: false, geocoder: false, infoBox: false, selectionIndicator: false
            }}, token, {{requestVertexNormals: true, requestWaterMask: true}});
            var viewerKind = {json.dumps(viewer_kind)};
            var geojsonUrl = {json.dumps(f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=geojson")};
            var previewPngUrl = {json.dumps(f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=preview_png")};
            var glbUrl = {json.dumps(f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=glb")};
            var activeTileset = null;
            var activeDataSource = null;
            var activeEntity = null;
            var activeBaseLayer = viewer.imageryLayers.length ? viewer.imageryLayers.get(0) : null;
            var activeElevationLayer = null;
            var activeRasterRectangle = null;
            var activeHillshadeLayer = null;
            var gpkgLayerSources = {{}};
            var terrainGridUrl = {json.dumps(f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=terrain_grid&v={derivative_v}")};
            var hillshadePngUrl = {json.dumps(f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=elevation_hillshade&v={derivative_v}")};
            var colorReliefPngUrl = {json.dumps(f"/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=elevation_color_relief")};

            window.updateElevationLayerState = function() {{
                var showChk = document.getElementById('chkShowElevationLayer');
                var hsChk = document.getElementById('chkHillshadeOverlay');
                var opRng = document.getElementById('rngElevationOpacity');
                var opLbl = document.getElementById('lblElevOpacity');
                var alpha = opRng ? (Number(opRng.value) / 100.0) : 1.0;
                if (opLbl && opRng) opLbl.textContent = opRng.value + '%';
                if (activeElevationLayer) {{
                    activeElevationLayer.show = showChk ? !!showChk.checked : true;
                    activeElevationLayer.alpha = alpha;
                }}
                if (activeHillshadeLayer) {{
                    activeHillshadeLayer.show = (showChk && showChk.checked && hsChk && hsChk.checked);
                    activeHillshadeLayer.alpha = alpha * 0.45;
                }}
                if (viewer && viewer.scene) viewer.scene.requestRender();
            }};

            var selectedTerrainMode = 'default';
            window.switchLocalDemTerrain = function(mode) {{
                selectedTerrainMode = mode;
                var warnEl = document.getElementById('dsmMeshWarningBanner');
                if (warnEl) warnEl.style.display = (mode === 'dsm') ? 'block' : 'none';
                if (!MosaicCesiumRuntime.applyLocalDemTerrain) return;
                if (!mode || mode === 'default') {{
                    MosaicCesiumRuntime.applyLocalDemTerrain(viewer, 'default');
                    document.getElementById('terrainControlStatus').textContent = 'Default terrain active.';
                }} else {{
                    document.getElementById('terrainControlStatus').textContent = 'Loading local terrain...';
                    MosaicCesiumRuntime.applyLocalDemTerrain(viewer, terrainGridUrl).then(function(ok) {{
                        if (selectedTerrainMode !== mode) return;
                        document.getElementById('terrainControlStatus').textContent = ok ? 'Local terrain active.' : 'Local terrain unavailable. Default terrain retained.';
                        if (!ok) {{ document.getElementById('radTerrainDefault').checked = true; if (warnEl) warnEl.style.display = 'none'; }}
                    }});
                }}
            }};

            window.changeElevationPalette = function(newPalette) {{
                startAssetRegeneration('/api/v2/assets/reclassify', {{project_id: {project_id}, asset_id: {asset_id}, asset_class: 'RASTER_ELEVATION', palette: newPalette}});
            }};

            window.toggleGpkgVectorLayer = function(slug, visible) {{
                var ds = gpkgLayerSources[slug];
                if (ds) {{
                    ds.show = !!visible;
                    if (viewer && viewer.scene) viewer.scene.requestRender();
                    return;
                }}
                if (visible) {{
                    var lUrl = '/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=vector_layer&layer=' + encodeURIComponent(slug);
                    Cesium.GeoJsonDataSource.load(lUrl, {{
                        stroke: Cesium.Color.fromCssColorString('#99CC00'),
                        fill: Cesium.Color.fromCssColorString('#99CC00').withAlpha(0.35),
                        strokeWidth: 3,
                        clampToGround: true
                    }}).then(async function(loadedDs) {{
                        await MosaicCesiumRuntime.prepareVectorDataSource(viewer, loadedDs);
                        gpkgLayerSources[slug] = loadedDs;
                        viewer.dataSources.add(loadedDs);
                    }});
                }}
            }};

            var regenerationKey = 'mosaic-regenerate-{project_id}-{asset_id}';
            var regenerationPolling = false;
            function regenerationBusy(busy) {{
                var button = document.getElementById('btnRegenerateAsset');
                if (button) button.disabled = busy;
                var palette = document.getElementById('selElevationPalette');
                if (palette) palette.disabled = busy;
            }}
            function watchAssetRegeneration(jid) {{
                if (regenerationPolling) return;
                regenerationPolling = true;
                regenerationBusy(true);
                var status = document.getElementById('assetRegenerationStatus');
                function poll() {{
                    var controller = new AbortController();
                    var timer = setTimeout(function() {{ controller.abort(); }}, 12000);
                    fetch('/api/v2/jobs/status?job_id=' + jid, {{signal:controller.signal}}).then(function(r) {{
                        if (!r.ok) throw new Error('Status unavailable');
                        return r.json();
                    }}).then(function(data) {{
                        var job = data.job || {{}};
                        status.textContent = 'Job #' + jid + ' · ' + (job.PROGRESS || 0) + '% · ' + (job.STAGE || job.STATUS);
                        if (job.STATUS === 'COMPLETED' || job.STATUS === 'FAILED') {{
                            sessionStorage.removeItem(regenerationKey);
                            regenerationPolling = false;
                            regenerationBusy(false);
                            if (job.STATUS === 'FAILED') status.textContent = job.ERROR || 'Regeneration failed. Your original is preserved.';
                            else {{ status.textContent = 'Viewer updated. Original SHA-256 preserved.'; setTimeout(function() {{ window.location.reload(); }}, 700); }}
                        }} else setTimeout(poll, 2000);
                    }}).catch(function() {{
                        status.textContent = 'Connection interrupted. Job #' + jid + ' continues in the background; reconnecting...';
                        setTimeout(poll, 5000);
                    }}).finally(function() {{ clearTimeout(timer); }});
                }}
                poll();
            }}
            function startAssetRegeneration(url, payload) {{
                if (regenerationPolling) return;
                regenerationBusy(true);
                var status = document.getElementById('assetRegenerationStatus');
                status.textContent = 'Submitting viewer regeneration...';
                fetch(url, {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}})
                    .then(function(r) {{return r.json();}}).then(function(data) {{
                        if (!data.ok || !data.job_id) throw new Error(data.error || 'Regeneration could not start.');
                        sessionStorage.setItem(regenerationKey, String(data.job_id));
                        watchAssetRegeneration(data.job_id);
                    }}).catch(function(error) {{ regenerationBusy(false); status.textContent = error.message; }});
            }}
            window.regenerateAssetDerivatives = function(pid, aid) {{
                startAssetRegeneration('/api/v2/assets/regenerate', {{project_id:pid,asset_id:aid}});
            }};
            var savedRegeneration = parseInt(sessionStorage.getItem(regenerationKey) || '0', 10);
            if (savedRegeneration) watchAssetRegeneration(savedRegeneration);
            window.locateExternalAsset = function() {{
                if (activeTileset) {{
                    MosaicCesiumRuntime.flyToTopDown(viewer, activeTileset, Math.max(activeTileset.boundingSphere.radius * 2.5, 3), 0.8);
                }} else if (activeDataSource) {{
                    MosaicCesiumRuntime.focusVectorDataSource(viewer, activeDataSource, 0.8);
                }} else if (activeRasterRectangle) {{
                    MosaicCesiumRuntime.flyToRectangleTopDown(viewer, activeRasterRectangle, 0.8);
                }} else if (activeEntity) {{
                    viewer.flyTo(activeEntity);
                }} else {{
                    viewer.camera.flyTo({{
                        destination: Cesium.Cartesian3.fromDegrees({lon_val}, {lat_val}, 650),
                        orientation: {{heading: 0, pitch: MosaicCesiumRuntime.topDownOffset(0).pitch, roll: 0}},
                        duration: 0.8
                    }});
                }}
            }};
            document.querySelectorAll('.processed-map-switch button').forEach(function(btn) {{
                btn.addEventListener('click', function() {{
                    document.querySelectorAll('.processed-map-switch button').forEach(function(b) {{ b.classList.remove('active'); }});
                    btn.classList.add('active');
                    var mode = btn.getAttribute('data-layer') || 'satellite';
                    if (activeBaseLayer) viewer.imageryLayers.remove(activeBaseLayer, true);
                    activeBaseLayer = viewer.imageryLayers.addImageryProvider(MosaicCesiumRuntime.createBaseProvider(mode), 0);
                }});
            }});
            if (viewerKind.indexOf('tiles') >= 0 || viewerKind.indexOf('splat') >= 0 || viewerKind.indexOf('gaussian') >= 0 || viewerKind.indexOf('pointcloud') >= 0) {{
                MosaicCesiumRuntime.loadTileset(viewer, {json.dumps(tiles_url)}, {{maximumScreenSpaceError: 12}}).then(function(t){{
                    activeTileset = t;
                    window.locateExternalAsset();
                }}).catch(function(){{
                    window.locateExternalAsset();
                }});
            }} else if (viewerKind.indexOf('geojson') >= 0 || viewerKind.indexOf('kml') >= 0 || viewerKind.indexOf('gpkg') >= 0 || viewerKind.indexOf('dxf') >= 0 || viewerKind.indexOf('shp') >= 0 || viewerKind.indexOf('gpx') >= 0 || viewerKind.indexOf('csv') >= 0) {{
                Cesium.GeoJsonDataSource.load(geojsonUrl, {{
                    stroke: Cesium.Color.fromCssColorString('#99CC00'),
                    fill: Cesium.Color.fromCssColorString('#99CC00').withAlpha(0.35),
                    strokeWidth: 3,
                    clampToGround: true
                }}).then(async function(ds) {{
                    await MosaicCesiumRuntime.prepareVectorDataSource(viewer, ds);
                    activeDataSource = ds;
                    viewer.dataSources.add(ds);
                    window.locateExternalAsset();
                }}).catch(function(error) {{
                    MosaicCesiumRuntime.showViewerError(viewer, error.message);
                }});
            }} else if (viewerKind.indexOf('raster') >= 0 || viewerKind.indexOf('tms') >= 0 || viewerKind.indexOf('orthophoto') >= 0 || viewerKind.indexOf('tif') >= 0) {{
                MosaicCesiumRuntime.loadXyzRaster(viewer, {json.dumps(raster_url)}, '/ExternalAssetFile.ashx?project_id={project_id}&asset_id={asset_id}&mode=raster_bounds&v={derivative_v}').then(function(layer) {{
                    activeElevationLayer = layer;
                    activeRasterRectangle = layer.imageryProvider.rectangle;
                    window.updateElevationLayerState();
                    if ({'true' if is_elevation_asset else 'false'}) {{
                        Cesium.SingleTileImageryProvider.fromUrl(hillshadePngUrl, {{rectangle: activeRasterRectangle}}).then(function(hsProv) {{
                            activeHillshadeLayer = viewer.imageryLayers.addImageryProvider(hsProv);
                            window.updateElevationLayerState();
                        }}).catch(function(){{}});
                    }}
                }}).catch(function(error) {{
                    MosaicCesiumRuntime.showViewerError(viewer, error.message);
                }});
            }} else if (viewerKind.indexOf('glb') >= 0 || viewerKind.indexOf('gltf') >= 0 || viewerKind.indexOf('obj') >= 0) {{
                var pos = Cesium.Cartesian3.fromDegrees({lon_val}, {lat_val}, {alt_val});
                var hpr = new Cesium.HeadingPitchRoll(Cesium.Math.toRadians({heading_val}), 0, 0);
                var entity = viewer.entities.add({{
                    name: {json.dumps(name)},
                    position: pos,
                    orientation: Cesium.Transforms.headingPitchRollQuaternion(pos, hpr),
                    model: {{ uri: glbUrl, scale: {scale_val}, minimumPixelSize: 64 }}
                }});
                activeEntity = entity;
                window.locateExternalAsset();
            }} else {{
                window.locateExternalAsset();
            }}
        }})();
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_login_page(error_msg: Optional[str] = None, return_url: str = "/en/Map") -> str:
    err_html = f'<div class="alert alert-danger" style="font-size:13px;">{html.escape(error_msg)}</div>' if error_msg else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Sign In · MosaicEarth Geospatial Platform</title>
    <link href="/assets/css/bootstrap.min.css" rel="stylesheet" />
    <link rel="stylesheet" href="/assets/css/master.css?v=4" />
    <style>
        body {{ background: #070b09; color: #f3f4f6; min-height: 100vh; display: flex; align-items: center; justify-content: center; font-family: system-ui, sans-serif; }}
        .login-card {{ background: #0c1410; border: 1px solid rgba(153,204,0,0.3); border-radius: 12px; padding: 36px; width: 100%; max-width: 430px; box-shadow: 0 20px 50px rgba(0,0,0,0.6); }}
        .brand {{ font-size: 26px; font-weight: 700; color: #fff; text-decoration: none; display: block; margin-bottom: 8px; }}
        .brand strong {{ color: #99CC00; }}
        .form-control {{ background: #111b15; border: 1px solid rgba(255,255,255,0.15); color: #fff; }}
        .form-control:focus {{ background: #14221a; border-color: #99CC00; color: #fff; box-shadow: none; }}
        .btn-login {{ background: #99CC00; color: #070b09; font-weight: 700; border: none; width: 100%; padding: 11px; border-radius: 6px; }}
    </style>
</head>
<body>
    <div class="login-card">
        <a class="brand" href="/en/Map" style="display:inline-flex;align-items:center;gap:10px;"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#020302;border:1px solid #303831;border-radius:4px;font-size:16px;font-weight:800;letter-spacing:-1.5px;padding-right:2px;color:#fff;"><span>M</span><span style="color:#99CC00;">E</span></span><span>Mosaic<strong>Earth</strong> <small style="font-size:11px;color:#99CC00;">BETA</small></span></a>
        <p style="color:#9ca3af;font-size:13px;margin-bottom:20px;">Sign in with your MosaicEarth account or explore the public 3D globe directly.</p>
        {err_html}
        <form method="post" action="/Default.aspx">
            <input type="hidden" name="returnUrl" value="{html.escape(return_url, quote=True)}" />
            <div class="mb-3">
                <label class="form-label" style="font-size:12px;color:#d1d5db;">Email or Username</label>
                <input type="text" name="email" class="form-control" required autocomplete="username" placeholder="you@example.com" />
            </div>
            <div class="mb-4">
                <label class="form-label" style="font-size:12px;color:#d1d5db;">Password</label>
                <input type="password" name="password" class="form-control" required autocomplete="current-password" placeholder="••••••••" />
            </div>
            <button type="submit" class="btn-login">SIGN IN</button>
        </form>
        <div style="margin-top:20px;padding-top:16px;border-top:1px solid rgba(255,255,255,0.08);display:flex;justify-content:space-between;font-size:12px;">
            <a href="/en/Map" style="color:#99CC00;text-decoration:none;font-weight:600;">&larr; Explore Public 3D Globe</a>
            <a href="/en/directory" style="color:#9ca3af;text-decoration:none;">Geospatial Directory</a>
        </div>
    </div>
</body>
</html>"""


def render_workspace_page(
    title: str,
    heading: str,
    subtitle: str,
    inner_html: str,
    user: Optional[Dict[str, Any]] = None,
    lang: str = "en",
    canonical_path: str = "/en/Projects",
    scripts_extra: str = "",
) -> str:
    seo_head = build_head_metadata_html(
        title=f"{title} · MosaicEarth",
        description=subtitle,
        canonical_path=canonical_path,
        robots="noindex, nofollow",
    )
    body = f"""
    <section class="pt-4 pb-5">
        <div class="container">
            <div class="box-4d" style="background:#0c1410;border:1px solid rgba(255,255,255,0.09);border-radius:10px;padding:24px;">
                <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px;border-bottom:1px solid rgba(255,255,255,0.08);padding-bottom:14px;">
                    <div>
                        <h1 style="font-size:22px;color:#fff;margin:0 0 4px;">{html.escape(heading)}</h1>
                        <p style="font-size:13px;color:#9ca3af;margin:0;">{html.escape(subtitle)}</p>
                    </div>
                    <div style="display:flex;wrap:wrap;gap:8px;">
                        <a class="btn btn-sm btn-outline-light" href="/Client/Project.aspx">+ Photogrammetry Project</a>
                        <a class="btn btn-sm btn-outline-light" href="/Client/ExternalAssetImport.aspx">+ Import Geospatial Asset</a>
                        <a class="btn btn-sm" style="background:#99CC00;color:#070b09;font-weight:700;" href="/Client/GaussianProject.aspx">+ 3D Gaussian Splatting (12 Cr)</a>
                    </div>
                </div>
                {inner_html}
            </div>
        </div>
    </section>"""
    return render_home_master("", seo_head, body, scripts_extra, user=user, lang=lang)


def _ecef_to_wgs84_height(x: float, y: float, z: float) -> Optional[float]:
    try:
        a = 6378137.0
        f = 1.0 / 298.257223563
        b = a * (1.0 - f)
        e2 = (a * a - b * b) / (a * a)
        ep2 = (a * a - b * b) / (b * b)
        p = math.hypot(x, y)
        if p < 1.0:
            return None
        th = math.atan2(a * z, b * p)
        lat = math.atan2(z + ep2 * b * (math.sin(th) ** 3), p - e2 * a * (math.cos(th) ** 3))
        cos_lat = math.cos(lat)
        if abs(cos_lat) < 1e-6:
            return None
        n_val = a / math.sqrt(1.0 - e2 * (math.sin(lat) ** 2))
        h = (p / cos_lat) - n_val
        if -450.0 <= h <= 9000.0:
            return round(h, 2)
    except Exception:
        pass
    return None


def _extract_tileset_wgs84_height(tileset_input: Any) -> Optional[float]:
    try:
        if isinstance(tileset_input, (bytes, bytearray)):
            data = json.loads(tileset_input.decode("utf-8", errors="ignore"))
        elif isinstance(tileset_input, dict):
            data = tileset_input
        else:
            with open(tileset_input, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        root = data.get("root") or {}
        bv = root.get("boundingVolume") or {}
        if isinstance(bv.get("region"), list) and len(bv["region"]) >= 6:
            min_h = float(bv["region"][4])
            max_h = float(bv["region"][5])
            return round((min_h + max_h) * 0.5, 2)
        local_pt = None
        if isinstance(bv.get("box"), list) and len(bv["box"]) >= 3:
            local_pt = (float(bv["box"][0]), float(bv["box"][1]), float(bv["box"][2]))
        elif isinstance(bv.get("sphere"), list) and len(bv["sphere"]) >= 3:
            local_pt = (float(bv["sphere"][0]), float(bv["sphere"][1]), float(bv["sphere"][2]))
        m = root.get("transform")
        if isinstance(m, list) and len(m) == 16:
            bx, by, bz = local_pt if local_pt is not None else (0.0, 0.0, 0.0)
            x = float(m[0]) * bx + float(m[4]) * by + float(m[8]) * bz + float(m[12])
            y = float(m[1]) * bx + float(m[5]) * by + float(m[9]) * bz + float(m[13])
            z = float(m[2]) * bx + float(m[6]) * by + float(m[10]) * bz + float(m[14])
            return _ecef_to_wgs84_height(x, y, z)
        elif local_pt is not None:
            return _ecef_to_wgs84_height(local_pt[0], local_pt[1], local_pt[2])
    except Exception:
        pass
    return None


def _resolve_asset_ground_elevation_asl(folder: str, pid: int, ext_id: int, height_offset: float) -> float:
    blob_store = get_blob_store()
    candidates: List[Tuple[str, str]] = []
    if folder:
        candidates.append(("imagenes", f"{folder}/procesado/output/tileset.json"))
        candidates.append(("imagenes", f"{folder}/procesado/3d_tiles/model/tileset.json"))
        candidates.append(("imagenes", f"{folder}/procesado/3d_tiles/pointcloud/tileset.json"))
        candidates.append(("results", f"{folder}/procesado/output/tileset.json"))
    if pid > 0 and ext_id > 0:
        candidates.append(("results", f"external/{pid}/{ext_id}/tileset.json"))
        candidates.append(("assets", f"external/{pid}/{ext_id}/tileset.json"))
        for container in ("assets", "results"):
            base_dir = blob_store.blobs_root / container / "external" / str(pid) / str(ext_id)
            if base_dir.is_dir():
                for ts_file in base_dir.rglob("tileset.json"):
                    h = _extract_tileset_wgs84_height(ts_file)
                    if h is not None:
                        return max(0.0, round(h + height_offset, 2))
    for container, rel in candidates:
        raw_ts = blob_store.read_bytes_from_cloud(container, rel)
        if raw_ts:
            h = _extract_tileset_wgs84_height(raw_ts)
            if h is not None:
                return max(0.0, round(h + height_offset, 2))
    # Fallback: check project files for GPS altitude if available
    if pid > 0:
        store = get_store()
        alts = []
        for pf in store.tables.get("dbo.WEB_PROJECTS_FILES", []):
            if int(pf.get("PROJECT_ID") or 0) == pid:
                a_val = _try_float(pf.get("ALTITUD") or pf.get("ALTITUDE"))
                if a_val is not None and 0.0 < a_val < 8500.0:
                    alts.append(a_val)
        if alts:
            alts.sort()
            return max(0.0, round(alts[len(alts) // 2], 2))
    return max(0.0, round(height_offset, 2))


def render_world_simulator_page(
    asset_id: str = "",
    name: str = "",
    lat: float = 41.9702,
    lon: float = 2.3885,
    alt: float = 0.0,
    kind: str = "",
    user: Optional[Dict[str, Any]] = None,
    lang: str = "en",
) -> str:
    norm_lang = normalize_lang(lang)
    store = get_store()
    cesium_token = store.get_cesium_browser_token()

    asset_title = (name or "").strip() or "MosaicEarth Geospatial Asset"
    asset_ftype = 2
    tiles_url = ""
    ortho_url = ""
    glb_url = ""
    height_offset = float(alt or 0.0)
    folder = ""
    pid = 0
    ext_id = 0
    detail_back_url = f"/{norm_lang}/Map?lon={lon}&lat={lat}"

    parts = (asset_id or "").strip().lower().split("-")
    scene_layers: List[Dict[str, Any]] = []
    radar_pins: List[Dict[str, Any]] = []
    has_explicit_coords = abs(float(lat or 0.0) - 41.9702) > 0.0005 or abs(float(lon or 0.0) - 2.3885) > 0.0005
    if len(parts) == 2 and parts[0] in ("mymap", "mymosaic") and parts[1].isdigit():
        mymap_id = int(parts[1])
        uid_val = int(user["ID"]) if user else 0
        map_row = next((m for m in store.tables.get("dbo.WEB_MYMAPS", []) if int(m.get("ID") or 0) == mymap_id), None)
        map_owner_id = int((map_row or {}).get("USER_ID") or (map_row or {}).get("CREATE_ID") or uid_val)
        asset_title = f"MyMosaic: {map_row.get('NAME')}" if (map_row and map_row.get("NAME")) else f"MyMosaic #{mymap_id}"
        detail_back_url = f"/MyMap.aspx?id={mymap_id}"
        for m3 in store.get_mymap_models(mymap_id, map_owner_id, 3):
            u_rel = str(m3.get("URL") or "").lstrip("/")
            m_lat = _try_float(m3.get("Lat")) or _try_float(m3.get("LATITUD"))
            m_lon = _try_float(m3.get("Lon")) or _try_float(m3.get("LONGITUD"))
            if u_rel and m_lat and m_lon:
                scene_layers.append({"kind": "tileset", "ftype": 3, "url": f"/ProcessedAssetFile.ashx/{u_rel}", "height": float(m3.get("Height") or 0.0), "lat": m_lat, "lon": m_lon, "name": str(m3.get("NAME") or "3D Model")})
                radar_pins.append({"name": str(m3.get("NAME") or "3D Model"), "lat": m_lat, "lon": m_lon, "ftype": 3})
        for m1 in store.get_mymap_models(mymap_id, map_owner_id, 1):
            u_rel = str(m1.get("URL") or "").lstrip("/")
            m_lat = _try_float(m1.get("Lat")) or _try_float(m1.get("LATITUD"))
            m_lon = _try_float(m1.get("Lon")) or _try_float(m1.get("LONGITUD"))
            if u_rel and m_lat and m_lon:
                scene_layers.append({"kind": "tileset", "ftype": 1, "url": f"/ProcessedAssetFile.ashx/{u_rel}", "height": float(m1.get("Height") or 0.0), "lat": m_lat, "lon": m_lon, "name": str(m1.get("NAME") or "Point Cloud")})
                radar_pins.append({"name": str(m1.get("NAME") or "Point Cloud"), "lat": m_lat, "lon": m_lon, "ftype": 1})
        for mo in store.get_mymap_orthophotos(mymap_id, map_owner_id):
            fld = str(mo.get("FOLDER") or "").strip("/")
            m_lat = _try_float(mo.get("LATITUD")) or _try_float(mo.get("Lat"))
            m_lon = _try_float(mo.get("LONGITUD")) or _try_float(mo.get("Lon"))
            if fld and m_lat and m_lon:
                scene_layers.append({"kind": "tms", "ftype": 2, "url": f"/ProcessedAssetFile.ashx/{fld}/procesado/orthophoto_tiles/", "lat": m_lat, "lon": m_lon, "name": str(mo.get("NAME") or "Orthophoto")})
                radar_pins.append({"name": str(mo.get("NAME") or "Orthophoto"), "lat": m_lat, "lon": m_lon, "ftype": 2})
        for me in store.get_mymap_external_assets(mymap_id, map_owner_id):
            vk = str(me.get("ViewerKind") or "").lower()
            e_pid = int(me.get("ProjectId") or 0)
            e_aid = int(me.get("Id") or 0)
            e_lat = _try_float(me.get("Latitude"))
            e_lon = _try_float(me.get("Longitude"))
            e_name = str(me.get("Title") or me.get("DisplayName") or "Imported Asset")
            if e_lat and e_lon:
                radar_pins.append({"name": e_name, "lat": e_lat, "lon": e_lon, "ftype": int(me.get("FileTypeId") or 2)})
                if "tiles" in vk or "splat" in vk:
                    scene_layers.append({"kind": "tileset", "ftype": 3, "url": f"/ExternalAsset3DTile.ashx/{e_pid}/{e_aid}/tileset.json", "height": 0.0, "lat": e_lat, "lon": e_lon, "name": e_name})
                elif "raster" in vk:
                    scene_layers.append({"kind": "xyz", "ftype": 2, "url": f"/ExternalAssetViewer.ashx?project_id={e_pid}&asset_id={e_aid}&z={{z}}&x={{x}}&y={{y}}", "lat": e_lat, "lon": e_lon, "name": e_name})
                elif "glb" in vk:
                    scene_layers.append({"kind": "glb", "ftype": 3, "url": f"/ExternalAssetFile.ashx?project_id={e_pid}&asset_id={e_aid}&mode=preview", "lat": e_lat, "lon": e_lon, "name": e_name})
        if scene_layers and not has_explicit_coords:
            valid_lats = [float(x["lat"]) for x in scene_layers if x.get("lat")]
            valid_lons = [float(x["lon"]) for x in scene_layers if x.get("lon")]
            if valid_lats and valid_lons:
                lat = sum(valid_lats) / len(valid_lats)
                lon = sum(valid_lons) / len(valid_lons)
    elif len(parts) == 3 and parts[0] == "processed" and parts[1].isdigit() and parts[2].isdigit():
        asset_ftype = int(parts[1])
        pid = int(parts[2])
        proj = store.get_project(pid)
        if proj:
            asset_title = str(proj.get("NAME") or asset_title)
            folder = str(proj.get("FOLDER") or _slugify(f"{pid}-{asset_title}"))
            loc = store.get_project_location(pid)
            lat = _try_float(proj.get("LATITUD")) or (_try_float(loc.get("LATITUD")) if loc else None) or lat
            lon = _try_float(proj.get("LONGITUD")) or (_try_float(loc.get("LONGITUD")) if loc else None) or lon
            radar_pins.append({"name": asset_title, "lat": lat, "lon": lon, "ftype": asset_ftype})
            for cm in store.tables.get("dbo.WEB_CESIUM_MODELS", []):
                if int(cm.get("Project_id") or cm.get("ID_PROJECT") or 0) == pid and int(cm.get("ModelType") or cm.get("TYPE") or 0) == asset_ftype:
                    height_offset = _try_float(cm.get("Height")) or 0.0
                    break
            pubs = store.get_publications_by_project(pid)
            pub_match = next((p for p in pubs if int(p.get("FILE_TYPE_ID") or 0) == asset_ftype and int(p.get("STATUS_ID") or 0) == 2), None)
            if pub_match:
                pub_id_val = int(pub_match.get("ID") or pub_match.get("ID_PROJECT_PUBLISH") or 0)
                if pub_id_val > 0:
                    detail_back_url = build_processed_asset_path(pub_id_val, asset_title, norm_lang)
            if asset_ftype == 2:
                ortho_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/orthophoto_tiles/"
            elif asset_ftype == 1:
                tiles_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/3d_tiles/pointcloud/tileset.json"
            else:
                tiles_url = f"/ProcessedAssetFile.ashx/{folder}/procesado/output/tileset.json"
    elif len(parts) in (2, 3) and parts[0] == "external" and all(part.isdigit() for part in parts[1:]):
        ext_id = int(parts[-1])
        ext = store.get_external_asset_by_id(ext_id)
        if ext:
            asset_ftype = int(parts[1]) if len(parts) == 3 else int(ext.get("FILE_TYPE_ID") or 3)
            pid = int(ext.get("PROJECT_ID") or 0)
            asset_title = str(ext.get("TITLE") or ext.get("DISPLAY_NAME") or asset_title)
            lat = _try_float(ext.get("LATITUDE")) or lat
            lon = _try_float(ext.get("LONGITUDE")) or lon
            radar_pins.append({"name": asset_title, "lat": lat, "lon": lon, "ftype": asset_ftype})
            # External tiles already contain their native placement transform.
            # ALTITUDE is an absolute elevation, not a second display offset.
            height_offset = 0.0
            detail_back_url = build_external_asset_path(pid, ext_id, asset_title, norm_lang)
            vk = str(ext.get("VIEWER_KIND") or kind or "").lower()
            if "tiles" in vk or "splat" in vk:
                tiles_url = f"/ExternalAsset3DTile.ashx/{pid}/{ext_id}/tileset.json"
            elif "raster" in vk:
                ortho_url = f"/ExternalAssetViewer.ashx?project_id={pid}&asset_id={ext_id}&z={{z}}&x={{x}}&y={{y}}"
            elif "glb" in vk:
                glb_url = f"/ExternalAssetFile.ashx?project_id={pid}&asset_id={ext_id}&mode=preview"
    else:
        # Global Map 1st-Person Flight Mode: Load all public geolocated assets live onto the globe!
        asset_title = (name or "").strip() or "Global 3D World · All Live MosaicAssets"
        detail_back_url = f"/{norm_lang}/Map?lon={lon}&lat={lat}"
        for pub_item in store.build_public_asset_index()[:80]:
            orig = str(pub_item.get("Origin") or "processed")
            ft_i = int(pub_item.get("FileTypeId") or 2)
            fld = str(pub_item.get("Folder") or "").strip("/")
            p_lat = _try_float(pub_item.get("Latitude"))
            p_lon = _try_float(pub_item.get("Longitude"))
            p_name = str(pub_item.get("Name") or "MosaicAsset")
            if not p_lat or not p_lon:
                continue
            radar_pins.append({"name": p_name, "lat": p_lat, "lon": p_lon, "ftype": ft_i})
            if len(scene_layers) < 48:
                if orig == "processed" and fld:
                    if ft_i == 3:
                        scene_layers.append({"kind": "tileset", "ftype": 3, "url": f"/ProcessedAssetFile.ashx/{fld}/procesado/3d_tiles/model/tileset.json", "height": float(pub_item.get("Height") or 0.0), "lat": p_lat, "lon": p_lon, "name": p_name})
                    elif ft_i == 1:
                        scene_layers.append({"kind": "tileset", "ftype": 1, "url": f"/ProcessedAssetFile.ashx/{fld}/procesado/3d_tiles/pointcloud/tileset.json", "height": float(pub_item.get("Height") or 0.0), "lat": p_lat, "lon": p_lon, "name": p_name})
                    elif ft_i == 2:
                        scene_layers.append({"kind": "tms", "ftype": 2, "url": f"/ProcessedAssetFile.ashx/{fld}/procesado/orthophoto_tiles/", "lat": p_lat, "lon": p_lon, "name": p_name})
                elif orig == "external":
                    vk = str(pub_item.get("ViewerKind") or "").lower()
                    e_pid = int(pub_item.get("ProjectId") or 0)
                    e_aid = int(pub_item.get("ExternalAssetId") or 0)
                    if "tiles" in vk or "splat" in vk:
                        scene_layers.append({"kind": "tileset", "ftype": ft_i, "url": f"/ExternalAsset3DTile.ashx/{e_pid}/{e_aid}/tileset.json", "height": 0.0, "lat": p_lat, "lon": p_lon, "name": p_name})
                    elif "glb" in vk:
                        scene_layers.append({"kind": "glb", "ftype": 3, "url": f"/ExternalAssetFile.ashx?project_id={e_pid}&asset_id={e_aid}&mode=preview", "lat": p_lat, "lon": p_lon, "name": p_name})

    # Also ensure global nearby assets appear on the Mini-Map Radar if viewing a single asset
    if len(radar_pins) <= 1:
        for pub_item in store.build_public_asset_index()[:60]:
            p_lat = _try_float(pub_item.get("Latitude"))
            p_lon = _try_float(pub_item.get("Longitude"))
            if p_lat and p_lon:
                radar_pins.append({"name": str(pub_item.get("Name") or "MosaicAsset"), "lat": p_lat, "lon": p_lon, "ftype": int(pub_item.get("FileTypeId") or 2)})

    initial_ground_asl = _resolve_asset_ground_elevation_asl(folder, pid, ext_id, height_offset)

    seo_head = build_head_metadata_html(
        title=f"Fly Over {asset_title} · 4DTwinMaps AirEngine Flight Simulator · MosaicEarth",
        description=f"Choose your aircraft and fly directly over {asset_title} ({format_coordinates(lat, lon)}) using the authentic 4DTwinMaps AirEngine flight physics and 3D aircraft fleet.",
        canonical_path=f"/{norm_lang}/fly",
        robots="noindex, follow",
    )

    global_4dtm_url = f"https://4dtwinmaps.com/fly?lat={lat:.6f}&lon={lon:.6f}&heading=0&model=standard-aircraft"

    head_extra = """
    <link href="/vendor/cesium/Widgets/widgets.css" rel="stylesheet" />
    <script src="/vendor/cesium/Cesium.js"></script>
    <script src="/js/front/MosaicCesiumRuntime.js?v=__MOSAIC_MAP_RUNTIME_VERSION__"></script>
    <style>
        .sim-shell { position: relative; width: 100%; height: calc(100vh - 64px); min-height: 640px; background: #050806; overflow: hidden; font-family: 'Inter', system-ui, -apple-system, sans-serif; user-select: none; }
        #simCesiumContainer { width: 100%; height: 100%; }
        .sim-topbar {
            position: absolute; top: 12px; left: 14px; right: 14px; z-index: 40;
            display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px;
            background: rgba(9, 15, 12, 0.88); backdrop-filter: blur(12px);
            border: 1px solid rgba(153, 204, 0, 0.35); border-radius: 10px; padding: 9px 14px; color: #f3f4f6;
        }
        .sim-badge { background: rgba(153, 204, 0, 0.16); color: #99CC00; border: 1px solid rgba(153, 204, 0, 0.45); font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 999px; letter-spacing: 0.06em; text-transform: uppercase; }
        .sim-hangar-overlay {
            position: absolute; inset: 0; z-index: 80;
            background: radial-gradient(circle at center, rgba(7, 14, 10, 0.76) 0%, rgba(4, 8, 6, 0.94) 100%);
            backdrop-filter: blur(9px);
            display: flex; align-items: center; justify-content: center; padding: 18px;
            transition: opacity 0.25s ease; overflow-y: auto;
        }
        .sim-hangar-overlay.is-hidden { opacity: 0; visibility: hidden; pointer-events: none; }
        .sim-hangar-card {
            width: 100%; max-width: 1060px; background: rgba(11, 19, 15, 0.97);
            border: 1px solid rgba(153, 204, 0, 0.42); border-radius: 16px;
            padding: 24px 26px; box-shadow: 0 28px 70px rgba(0, 0, 0, 0.8); color: #fff;
            max-height: 90vh; overflow-y: auto;
        }
        .aircraft-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; margin: 16px 0 20px;
        }
        .aircraft-option {
            background: rgba(18, 29, 23, 0.92); border: 1.5px solid rgba(255, 255, 255, 0.12);
            border-radius: 12px; padding: 12px; cursor: pointer; transition: all 0.18s ease;
            display: flex; flex-direction: column; justify-content: space-between;
        }
        .aircraft-option:hover { border-color: rgba(153, 204, 0, 0.65); transform: translateY(-2px); }
        .aircraft-option.selected {
            background: rgba(153, 204, 0, 0.13); border-color: #99CC00;
            box-shadow: 0 0 24px rgba(153, 204, 0, 0.24);
        }
        .aircraft-thumb {
            width: 100%; height: 108px; border-radius: 8px; overflow: hidden;
            background: radial-gradient(circle at 50% 40%, rgba(153,204,0,0.18), rgba(6,10,8,0.95));
            margin-bottom: 10px; display: flex; align-items: center; justify-content: center;
        }
        .aircraft-thumb img { width: 100%; height: 100%; object-fit: cover; }
        .aircraft-name { font-size: 14px; font-weight: 800; color: #fff; margin-bottom: 2px; letter-spacing: 0.02em; }
        .aircraft-role { font-size: 10px; color: #99CC00; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700; margin-bottom: 6px; }
        .aircraft-specs { font-size: 11.5px; color: #9ca3af; line-height: 1.4; margin: 0; padding: 0; list-style: none; }
        .sim-hud {
            position: absolute; bottom: 16px; left: 16px; right: 16px; z-index: 35;
            display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px;
            pointer-events: none;
        }
        .sim-hud-panel {
            pointer-events: auto;
            background: rgba(8, 14, 11, 0.9); backdrop-filter: blur(10px);
            border: 1px solid rgba(153, 204, 0, 0.34); border-radius: 12px; padding: 12px 16px; color: #e5e7eb;
        }
        .hud-metrics { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
        .hud-metric-val { font-size: 20px; font-weight: 800; color: #99CC00; font-variant-numeric: tabular-nums; line-height: 1.1; }
        .hud-metric-lbl { font-size: 10px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.06em; }
        .sim-pad-btn {
            background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.18);
            color: #fff; border-radius: 6px; padding: 6px 11px; font-size: 12px; font-weight: 600; cursor: pointer;
        }
        .sim-pad-btn:hover, .sim-pad-btn.active { background: #99CC00; color: #070b09 !important; border-color: #99CC00; }
        .btn-4dtm-global {
            background: linear-gradient(135deg, rgba(153,204,0,0.22), rgba(56,189,248,0.18));
            border: 1px solid #99CC00; color: #f4f7f2; font-weight: 700; font-size: 12px;
            padding: 6px 12px; border-radius: 8px; text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
        }
        .btn-4dtm-global:hover { background: #99CC00; color: #070b09; }
        @media (max-width: 767.98px) {
            .sim-topbar { top: 8px; left: 8px; right: 8px; padding: 8px 10px; gap: 6px; }
            #simMiniMapWrap { top: auto !important; bottom: 170px !important; right: 8px !important; width: 164px !important; padding: 6px !important; }
            #simMiniMapCanvas { width: 150px !important; height: 112px !important; }
            .sim-hud { bottom: 8px; left: 8px; right: 8px; gap: 6px; }
            .sim-hud-panel { padding: 8px 10px; width: 100%; }
            .hud-metric-val { font-size: 15px; }
        }
    </style>"""

    body_content = f"""
    <div class="sim-shell">
        <div class="sim-topbar">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <span class="sim-badge"><i class="fas fa-fighter-jet me-1"></i> 4DTwinMaps AirEngine</span>
                <div>
                    <strong style="font-size:14px;color:#fff;">{html.escape(asset_title)}</strong>
                    <span style="font-size:12px;color:#9ca3af;margin-left:6px;">WGS84 {format_coordinates(lat, lon)}</span>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <button type="button" id="btnOpenHangar" class="btn btn-sm" style="background:#99CC00;color:#070b09;font-weight:700;">
                    <i class="fas fa-helicopter me-1"></i> HANGAR / AIRCRAFT
                </button>
                <button type="button" id="btnToggleCam" class="btn btn-sm btn-outline-light">
                    <i class="fas fa-video me-1"></i> Camera: <span id="lblCamMode">1st Person HUD</span>
                </button>
                <button type="button" id="btnResetApproach" class="btn btn-sm btn-outline-light" title="Reset approach over MosaicAsset (R)">
                    <i class="fas fa-crosshairs me-1"></i> Reset Over Asset
                </button>
                <a id="lnkTop4DTwinMaps" href="{html.escape(global_4dtm_url, quote=True)}" target="_blank" rel="noopener" class="btn-4dtm-global" title="Open 4DTwinMaps.com to fly anywhere in the world with multiplayer, air races &amp; global 3D terrain">
                    <i class="fas fa-globe-americas"></i> Fly Worldwide on 4DTwinMaps.com &nearr;
                </a>
                <a href="{html.escape(detail_back_url, quote=True)}" class="btn btn-sm btn-outline-secondary" style="color:#d1d5db;border-color:rgba(255,255,255,0.2);">
                    &larr; Back to Asset
                </a>
            </div>
        </div>

        <div id="simHangarOverlay" class="sim-hangar-overlay">
            <div class="sim-hangar-card">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;">
                    <div>
                        <span class="sim-badge">MosaicEarth &times; 4DTwinMaps AirEngine</span>
                        <h1 style="font-size:22px;font-weight:800;margin:8px 0 4px;color:#fff;">Select Your Exploration Mode</h1>
                        <p style="font-size:13px;color:#9ca3af;margin:0;">
                            Flying over <strong style="color:#fff;">{html.escape(asset_title)}</strong> ({format_coordinates(lat, lon)}) in 1st-Person HUD or 3rd-Person Chase Camera.
                        </p>
                    </div>
                    <a id="lnkHangar4DTwinMaps" href="{html.escape(global_4dtm_url, quote=True)}" target="_blank" rel="noopener" class="btn-4dtm-global">
                        <i class="fas fa-external-link-alt"></i> Explore &amp; Fly Anywhere in the World on 4DTwinMaps.com &nearr;
                    </a>
                </div>

                <div class="aircraft-grid" id="aircraftGrid">
                    <div class="aircraft-option selected" data-aircraft="drone">
                        <div>
                            <div class="aircraft-thumb"><img src="/assets/4dtwinmaps/previews/drone.webp" alt="Camera Drone" /></div>
                            <div class="aircraft-role">Precision Hover + Close Inspection</div>
                            <div class="aircraft-name">CAMERA DRONE</div>
                        </div>
                        <ul class="aircraft-specs">
                            <li>Speed: <strong>0 (Hover) to 100 km/h</strong></li>
                            <li>Controls: <strong>Zero-Drift Hover, Yaw &amp; Roll, Down to 1m AGL</strong></li>
                        </ul>
                    </div>

                    <div class="aircraft-option" data-aircraft="helicopter">
                        <div>
                            <div class="aircraft-thumb"><img src="/assets/4dtwinmaps/previews/helicopter.webp" alt="Helicopter" /></div>
                            <div class="aircraft-role">Hover + Fast Survey · Rotorcraft</div>
                            <div class="aircraft-name">HELICOPTER</div>
                        </div>
                        <ul class="aircraft-specs">
                            <li>Speed: <strong>0 (Hover) to 295 km/h</strong></li>
                            <li>Controls: <strong>Stationary Hover, Pure Yaw &amp; Banked Roll</strong></li>
                        </ul>
                    </div>

                    <div class="aircraft-option" data-aircraft="paramotor">
                        <div>
                            <div class="aircraft-thumb"><img src="/assets/4dtwinmaps/previews/paramotor.webp" alt="Paramotor" /></div>
                            <div class="aircraft-role">Low &amp; Slow Scenic · 4DTwinMaps</div>
                            <div class="aircraft-name">PARAMOTOR</div>
                        </div>
                        <ul class="aircraft-specs">
                            <li>Cruise / Max: <strong>45 / 78 km/h</strong></li>
                            <li>Controls: <strong>Powered Canopy Climb, Low-Altitude Glide &amp; Roll</strong></li>
                        </ul>
                    </div>

                    <div class="aircraft-option" data-aircraft="standard-aircraft">
                        <div>
                            <div class="aircraft-thumb"><img src="/assets/4dtwinmaps/previews/standard-aircraft.webp" alt="Standard Aircraft" /></div>
                            <div class="aircraft-role">Regional Tourer · Fixed-Wing</div>
                            <div class="aircraft-name">AIRCRAFT</div>
                        </div>
                        <ul class="aircraft-specs">
                            <li>Cruise / Max: <strong>280 / 600 km/h</strong></li>
                            <li>Controls: <strong>Bank, Pitch, Throttle &amp; Regional Transit</strong></li>
                        </ul>
                    </div>
                </div>

                <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:14px;border-top:1px solid rgba(255,255,255,0.1);padding-top:16px;">
                    <div style="font-size:12px;color:#9ca3af;line-height:1.5;">
                        <strong style="color:#d1d5db;">Flight Controls:</strong>
                        <code>W / S</code> Climb / Descend (Throttle) &middot;
                        <code>&uarr; / &darr;</code> Forward / Backward (Pitch) &middot;
                        <code>A / D</code> Yaw + Bank Roll &middot;
                        <code>Q / E</code> or <code>&larr; / &rarr;</code> Roll + Strafe &middot;
                        <code>C</code> Toggle 1st Person / 3rd Person
                    </div>
                    <button type="button" id="btnStartFlying" class="btn btn-lg" style="background:#99CC00;color:#070b09;font-weight:800;padding:11px 30px;border-radius:10px;box-shadow:0 10px 25px rgba(153,204,0,0.35);">
                        <i class="fas fa-play me-2"></i> LAUNCH 1ST-PERSON FLIGHT
                    </button>
                </div>
            </div>
        </div>

        <div id="simCesiumContainer"></div>

        <!-- 2D Mini-Map Radar with Scale & Click-to-Fly -->
        <div id="simMiniMapWrap" style="position:absolute;top:70px;right:16px;z-index:38;width:236px;background:rgba(8,14,11,0.92);backdrop-filter:blur(10px);border:1px solid rgba(153,204,0,0.42);border-radius:12px;padding:10px;color:#e5e7eb;box-shadow:0 14px 32px rgba(0,0,0,0.65);">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-size:11px;">
                <strong style="color:#99CC00;letter-spacing:0.04em;text-transform:uppercase;"><i class="fas fa-compass me-1"></i> Asset Radar</strong>
                <div style="display:flex;gap:4px;">
                    <button type="button" id="btnRadarZoomIn" style="background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.2);color:#fff;border-radius:4px;padding:0 6px;font-size:11px;cursor:pointer;" title="Zoom In Radar">+</button>
                    <button type="button" id="btnRadarZoomOut" style="background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.2);color:#fff;border-radius:4px;padding:0 6px;font-size:11px;cursor:pointer;" title="Zoom Out Radar">&minus;</button>
                </div>
            </div>
            <canvas id="simMiniMapCanvas" width="216" height="164" style="width:216px;height:164px;border-radius:8px;background:#060c09;border:1px solid rgba(255,255,255,0.12);cursor:crosshair;display:block;"></canvas>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;font-size:10.5px;color:#9ca3af;">
                <span>Click pin to fly there</span>
                <span id="lblRadarScale" style="color:#99CC00;font-weight:700;">Scale: 2.0 km</span>
            </div>
        </div>

        <div class="sim-hud">
            <div class="sim-hud-panel">
                <div style="font-size:11px;color:#99CC00;font-weight:700;text-transform:uppercase;margin-bottom:6px;">
                    <span id="hudAircraftName">AIR ENGINE / STANDARD AIRCRAFT</span> &middot; <span id="hudStatusText">IN FLIGHT</span>
                </div>
                <div class="hud-metrics">
                    <div>
                        <div class="hud-metric-val"><span id="hudSpeed">280</span> <small style="font-size:11px;font-weight:600;">KM/H</small></div>
                        <div class="hud-metric-lbl">3D Airspeed</div>
                    </div>
                    <div>
                        <div class="hud-metric-val"><span id="hudAltitude">260</span> <small style="font-size:11px;font-weight:600;">M AGL</small></div>
                        <div class="hud-metric-lbl">Above Asset (<span id="hudAltitudeAsl">300</span>m ASL)</div>
                    </div>
                    <div>
                        <div class="hud-metric-val"><span id="hudHeading">000</span>&deg;</div>
                        <div class="hud-metric-lbl">Heading</div>
                    </div>
                    <div>
                        <div class="hud-metric-val"><span id="hudEnergy">65</span><small style="font-size:11px;font-weight:600;">%</small></div>
                        <div class="hud-metric-lbl">Power / Thrust</div>
                    </div>
                    <div>
                        <div class="hud-metric-val"><span id="hudDist">0.35</span> <small style="font-size:11px;font-weight:600;">KM</small></div>
                        <div class="hud-metric-lbl">To MosaicAsset</div>
                    </div>
                </div>
            </div>

            <div class="sim-hud-panel" style="display:flex;flex-wrap:wrap;align-items:center;gap:6px;">
                <button type="button" class="sim-pad-btn" data-touch="steer:-1" title="Steer / Bank Left (A / Left)">&larr; Bank L</button>
                <button type="button" class="sim-pad-btn" data-touch="pitch:1" title="Pitch Up / Forward (Up Arrow)">&uarr; Pitch Up</button>
                <button type="button" class="sim-pad-btn" data-touch="pitch:-1" title="Pitch Down / Back (Down Arrow)">&darr; Pitch Dn</button>
                <button type="button" class="sim-pad-btn" data-touch="steer:1" title="Steer / Bank Right (D / Right)">Bank R &rarr;</button>
                <button type="button" class="sim-pad-btn" data-touch="throttle:1" title="Increase Throttle / Climb (W)">+ Power (W)</button>
                <button type="button" class="sim-pad-btn" data-touch="brake:1" title="Air Brake / Descend (S)">- Brake (S)</button>
                <button type="button" class="sim-pad-btn" data-touch="boost:1" title="Boost / Afterburner (Shift / Space)">BOOST</button>
                <button type="button" class="sim-pad-btn" id="btnHudPause" title="Pause Flight (P)">Pause</button>
            </div>
        </div>
    </div>"""

    scripts_extra = f"""
    <script>
        (function() {{
            var token = {json.dumps(cesium_token)};
            Cesium.Ion.defaultAccessToken = token;

            var targetLat = {lat};
            var targetLon = {lon};
            var targetHeightOffset = {height_offset};
            var initialGroundAsl = {initial_ground_asl};
            var baseGroundAltitude = Math.max(initialGroundAsl, 0);
            var tilesUrl = {json.dumps(tiles_url)};
            var orthoUrl = {json.dumps(ortho_url)};
            var glbUrl = {json.dumps(glb_url)};
            var assetFtype = {asset_ftype};
            var sceneLayers = {json.dumps(scene_layers)};
            var radarPins = {json.dumps(radar_pins)};

            var viewer = MosaicCesiumRuntime.createViewer('simCesiumContainer', {{
                imageryProvider: MosaicCesiumRuntime.createBaseProvider('satellite'),
                timeline: false, animation: false, fullscreenButton: false, homeButton: false,
                sceneModePicker: false, baseLayerPicker: false, navigationHelpButton: false,
                geocoder: false, infoBox: false, selectionIndicator: false, scene3DOnly: true,
                shouldAnimate: true
            }}, token, {{requestVertexNormals: true, requestWaterMask: true}});

            viewer.scene.globe.enableLighting = false;
            viewer.scene.globe.depthTestAgainstTerrain = false;

            // Target beacon over the MosaicAsset
            var beaconEntity = viewer.entities.add({{
                name: {json.dumps(asset_title)},
                position: Cesium.Cartesian3.fromDegrees(targetLon, targetLat, baseGroundAltitude + 26),
                point: {{ pixelSize: 11, color: Cesium.Color.fromCssColorString('#99CC00'), outlineColor: Cesium.Color.BLACK, outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY }},
                label: {{
                    text: {json.dumps(asset_title)},
                    font: '700 13px Inter, sans-serif',
                    fillColor: Cesium.Color.WHITE,
                    outlineColor: Cesium.Color.BLACK,
                    outlineWidth: 3,
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    pixelOffset: new Cesium.Cartesian2(0, -14),
                    disableDepthTestDistance: Number.POSITIVE_INFINITY
                }}
            }});
            var glbEntity = null;

            function applyDiscoveredGroundElevation(elevMeters) {{
                if (typeof elevMeters !== 'number' || !isFinite(elevMeters) || elevMeters < -400 || elevMeters > 8900) return;
                // An asset can sit above the coarse global terrain surface.
                // Keep its spawn camera above its actual georeferenced bounds.
                if (tilesUrl || glbUrl || orthoUrl) elevMeters = Math.max(initialGroundAsl, elevMeters);
                var prevBase = baseGroundAltitude;
                baseGroundAltitude = elevMeters;
                if (beaconEntity) {{
                    beaconEntity.position = Cesium.Cartesian3.fromDegrees(targetLon, targetLat, baseGroundAltitude + 26);
                }}
                if (glbEntity) {{
                    glbEntity.position = Cesium.Cartesian3.fromDegrees(targetLon, targetLat, baseGroundAltitude + 4);
                }}
                var desiredSpawnAsl = baseGroundAltitude + (currentDef ? currentDef.spawnAlt : 90);
                if (!simControl.flying) {{
                    pose.altitude = desiredSpawnAsl;
                    rigState.initialized = false;
                    updateModelMatrix();
                    applyCamera(0.016, false);
                }} else if (pose.altitude < baseGroundAltitude + 4) {{
                    pose.altitude = baseGroundAltitude + 12;
                }}
            }}

            // Load the target MosaicAsset onto the 3D globe
            function reportAssetLoadError() {{
                if (document.getElementById('flyAssetLoadError')) return;
                var warning = document.createElement('div');
                warning.id = 'flyAssetLoadError';
                warning.setAttribute('role', 'alert');
                warning.style.cssText = 'position:absolute;top:112px;left:20px;right:20px;z-index:30;background:#482516;color:#fff;padding:14px;border-radius:8px';
                warning.textContent = 'The selected model could not load. Reopen Fly View or use Back to Asset to check its files.';
                document.getElementById('simCesiumContainer').appendChild(warning);
            }}
            if (tilesUrl) {{
                var sse = assetFtype === 1 ? 8 : 16;
                MosaicCesiumRuntime.loadTileset(viewer, tilesUrl, {{maximumScreenSpaceError: sse, enableCollision: false}}).then(function(tile) {{
                    if (tile.tileFailed) tile.tileFailed.addEventListener(reportAssetLoadError);
                    MosaicCesiumRuntime.applyProcessedTilesetHeight(tile, targetHeightOffset);
                    if (tile.pointCloudShading) {{
                        tile.pointCloudShading.attenuation = true;
                        tile.pointCloudShading.maximumAttenuation = 3.0;
                        tile.pointCloudShading.geometricErrorScale = 2.2;
                        tile.pointCloudShading.baseResolution = 0.22;
                    }}
                }}).catch(reportAssetLoadError);
            }} else if (orthoUrl) {{
                if (orthoUrl.indexOf('{{z}}') >= 0) {{
                    viewer.imageryLayers.addImageryProvider(new Cesium.UrlTemplateImageryProvider({{url: orthoUrl, maximumLevel: 20}}));
                }} else {{
                    MosaicCesiumRuntime.loadTmsLayer(viewer, orthoUrl, {{maximumLevel: 20}});
                }}
            }} else if (glbUrl) {{
                var mPos = Cesium.Cartesian3.fromDegrees(targetLon, targetLat, baseGroundAltitude + 4);
                glbEntity = viewer.entities.add({{ position: mPos, model: {{ uri: glbUrl, scale: 1.0 }} }});
            }}
            // Also load all multi-layer scene assets (for Global World Fly Mode or MyMosaic Fly Mode)
            if (Array.isArray(sceneLayers) && sceneLayers.length > 0) {{
                sceneLayers.forEach(function(layer) {{
                    if (!layer || !layer.url) return;
                    if (layer.kind === 'tileset') {{
                        MosaicCesiumRuntime.loadTileset(viewer, layer.url, {{maximumScreenSpaceError: layer.ftype === 1 ? 10 : 18, enableCollision: false}}).then(function(tile) {{
                            MosaicCesiumRuntime.applyProcessedTilesetHeight(tile, Number(layer.height || 0));
                        }}).catch(function() {{}});
                    }} else if (layer.kind === 'tms') {{
                        MosaicCesiumRuntime.loadTmsLayer(viewer, layer.url, {{maximumLevel: 20}});
                    }} else if (layer.kind === 'xyz') {{
                        viewer.imageryLayers.addImageryProvider(new Cesium.UrlTemplateImageryProvider({{url: layer.url, maximumLevel: 20}}));
                    }} else if (layer.kind === 'glb') {{
                        var lPos = Cesium.Cartesian3.fromDegrees(layer.lon || targetLon, layer.lat || targetLat, baseGroundAltitude + 4);
                        viewer.entities.add({{ position: lPos, model: {{ uri: layer.url, scale: 1.0 }} }});
                    }}
                }});
            }}

            var EARTH_RADIUS_METERS = 6371008.8;
            function clamp(v, min, max) {{ return Math.min(max, Math.max(min, v)); }}
            function normalizeHeading(h) {{ return ((h % 360) + 360) % 360; }}
            function signedAngle(v) {{ return ((v + 540) % 360) - 180; }}
            function approach(cur, target, rate, dt) {{
                var d = target - cur;
                var step = rate * dt;
                return Math.abs(d) <= step ? target : cur + Math.sign(d) * step;
            }}
            function advancePosition(pos, dt) {{
                var headRad = pos.heading * Math.PI / 180;
                var pitchRad = (pos.pitch || 0) * Math.PI / 180;
                var groundDist = pos.velocity * Math.cos(pitchRad) * dt;
                var north = Math.cos(headRad) * groundDist;
                var east = Math.sin(headRad) * groundDist;
                var dLat = (north / EARTH_RADIUS_METERS) * (180 / Math.PI);
                var latCos = Math.max(0.05, Math.cos(pos.latitude * Math.PI / 180));
                var dLon = (east / (EARTH_RADIUS_METERS * latCos)) * (180 / Math.PI);
                return {{
                    latitude: clamp(pos.latitude + dLat, -89.9, 89.9),
                    longitude: ((((pos.longitude + dLon + 180) % 360) + 360) % 360) - 180,
                    altitude: pos.altitude + pos.velocity * Math.sin(pitchRad) * dt
                }};
            }}

            var BASE_PHYSICS = {{
                massKg: 1100, cruiseSpeed: 78, maximumSpeed: 168, acceleration: 31, braking: 48,
                turnRate: 31, pitchRate: 27, rollLimit: 62, stallSpeed: 31, drag: 0.003,
                lift: 1, inertia: 1, climbRate: 32, descentRate: 24, glideRatio: 9.5,
                boostMultiplier: 1.35, minimumClearance: 1.5, maximumAltitude: 30000,
                motorResponse: 0.24, lateralAcceleration: 18, minimumSinkRate: 1.1,
                brakeAuthority: 1, translationalLift: 0, airDensityEffect: 0.35, stallSeverity: 1
            }};

            var CAMERA_PRESETS = {{
                aircraft: {{ chaseDistance: 40, chaseHeight: 12, lookAhead: 10, lookHeight: -1, speedLookAhead: 0.014, minFov: 52, maxFov: 67 }},
                agile:    {{ chaseDistance: 29, chaseHeight: 8.5, lookAhead: 7, lookHeight: -0.7, speedLookAhead: 0.012, minFov: 55, maxFov: 74 }},
                hover:    {{ chaseDistance: 20, chaseHeight: 6.2, lookAhead: 3, lookHeight: -0.4, speedLookAhead: 0.015, minFov: 51, maxFov: 64 }},
                paramotor:{{ chaseDistance: 22, chaseHeight: 6.8, lookAhead: 4, lookHeight: -0.5, speedLookAhead: 0.012, minFov: 54, maxFov: 68 }}
            }};

            function defModel(cfg) {{
                var p = Object.assign({{}}, BASE_PHYSICS, cfg.physics || {{}});
                return Object.assign({{}}, cfg, {{ physics: p }});
            }}

            // 4 Distinct Exploration Modes: Drone, Helicopter, Paramotor (4DTwinMaps), Aircraft
            var FLIGHT_MODELS = {{
                'drone': defModel({{
                    id: 'drone', name: 'CAMERA DRONE', physicsType: 'multirotor', assisted: true,
                    modelUrl: '/assets/4dtwinmaps/models/drone-4dtm.glb', scale: 0.45, minPx: 22, maxScale: 3.2, headingOffset: -90, heightOffset: 0,
                    propellerNodes: [], rotorNodes: ['RotorFL', 'RotorFR', 'RotorRL', 'RotorRR'],
                    camera: CAMERA_PRESETS.hover, spawnAlt: 65, spawnDistDeg: 0.0012, initialSpeed: 0,
                    physics: {{ massKg: 2.1, cruiseSpeed: 14, maximumSpeed: 28, acceleration: 14, braking: 22, turnRate: 72, pitchRate: 32, rollLimit: 28, stallSpeed: 0, inertia: 0.45, climbRate: 10, descentRate: 8, motorResponse: 0.16, lateralAcceleration: 15, translationalLift: 0.05, airDensityEffect: 0.4, minimumClearance: 1.0 }}
                }}),
                'helicopter': defModel({{
                    id: 'helicopter', name: 'HELICOPTER', physicsType: 'rotor', assisted: true,
                    modelUrl: '/assets/4dtwinmaps/models/helicopter-zsky-flight.glb', scale: 0.22, minPx: 32, maxScale: 4.2, headingOffset: -90, heightOffset: 0,
                    propellerNodes: ['TailRotor'], rotorNodes: ['MainRotor'],
                    camera: {{ chaseDistance: 29, chaseHeight: 10, lookAhead: 3, lookHeight: -0.55, speedLookAhead: 0.015, minFov: 51, maxFov: 64 }},
                    spawnAlt: 110, spawnDistDeg: 0.0022, initialSpeed: 0,
                    physics: {{ massKg: 2700, cruiseSpeed: 54, maximumSpeed: 82, acceleration: 19, braking: 28, turnRate: 54, pitchRate: 24, rollLimit: 28, stallSpeed: 0, inertia: 0.95, climbRate: 16, descentRate: 13, minimumClearance: 2.2 }}
                }}),
                'paramotor': defModel({{
                    id: 'paramotor', name: 'PARAMOTOR', physicsType: 'paramotor', assisted: true,
                    modelUrl: '/assets/4dtwinmaps/models/adventurer-paramotor.glb', scale: 0.9, minPx: 26, maxScale: 3.6, headingOffset: -90, heightOffset: 0,
                    propellerNodes: ['Propeller'], rotorNodes: [],
                    camera: CAMERA_PRESETS.paramotor, spawnAlt: 85, spawnDistDeg: 0.0018, initialSpeed: 12.5,
                    physics: {{ massKg: 115, cruiseSpeed: 12.5, maximumSpeed: 22, acceleration: 8, braking: 12, turnRate: 42, pitchRate: 18, rollLimit: 34, stallSpeed: 6.5, inertia: 0.72, climbRate: 6.5, descentRate: 5.5, minimumSinkRate: 1.2, minimumClearance: 1.4 }}
                }}),
                'standard-aircraft': defModel({{
                    id: 'standard-aircraft', name: 'STANDARD AIRCRAFT', physicsType: 'fixed-wing', assisted: true,
                    modelUrl: '/assets/4dtwinmaps/models/aircraft-4dtm.glb', scale: 1.0, minPx: 30, maxScale: 4.5, headingOffset: -90, heightOffset: 0,
                    propellerNodes: ['Propeller', 'PropellerLeft', 'PropellerRight'], rotorNodes: [],
                    camera: CAMERA_PRESETS.aircraft, spawnAlt: 220, spawnDistDeg: 0.0045, initialSpeed: 78,
                    physics: {{ massKg: 1150, cruiseSpeed: 78, maximumSpeed: 168, acceleration: 31, turnRate: 31, pitchRate: 27, stallSpeed: 31, glideRatio: 9.5, boostMultiplier: 1.18, minimumClearance: 6.0 }}
                }})
            }};

            var currentDef = FLIGHT_MODELS['drone'];
            var pose = {{
                latitude: targetLat - 0.0012,
                longitude: targetLon,
                altitude: baseGroundAltitude + 65,
                heading: 0,
                pitch: 0,
                roll: 0,
                velocity: 0,
                verticalSpeed: 0
            }};
            var engineState = {{
                throttle: 0.5,
                motorPower: 0.5,
                bodyForwardVelocity: 0,
                bodyLateralVelocity: 0,
                yawRate: 0,
                aerobaticPitch: 0,
                aerobaticRoll: 0
            }};
            var rigState = {{
                initialized: false,
                heading: 0,
                pitch: -14,
                roll: 0,
                fov: 55,
                range: 24,
                orbitHeading: 0,
                orbitPitch: 0,
                zoom: 1.0
            }};
            // Default to 3rd-Person Chase Camera ('chase') so the aircraft is always visible on launch
            var simControl = {{
                flying: false,
                paused: false,
                camMode: 'cockpit' // 'cockpit' | 'chase' | 'free'
            }};

            var activePrimitive = null;
            var partNodes = new Map();
            var modelLoadGen = 0;
            var scratchOrigin = new Cesium.Cartesian3();
            var scratchHpr = new Cesium.HeadingPitchRoll();
            var scratchTarget = new Cesium.Cartesian3();
            var scratchRange = new Cesium.HeadingPitchRange();
            var scratchCarto = new Cesium.Cartographic();
            var propellerPhase = 0;

            var trailPositions = [];
            var lastTrailAt = 0;
            var trailEntity = viewer.entities.add({{
                polyline: {{
                    positions: new Cesium.CallbackProperty(function() {{ return trailPositions; }}, false),
                    width: 2.2,
                    material: Cesium.Color.WHITE.withAlpha(0.32),
                    arcType: Cesium.ArcType.NONE
                }}
            }});

            function sampleLocalGround(latDeg, lonDeg) {{
                var terrainHeight = null;
                try {{
                    Cesium.Cartographic.fromDegrees(lonDeg, latDeg, 0, scratchCarto);
                    // The ellipsoid's zero is a fallback surface, not measured
                    // terrain. Keep the asset elevation when terrain is absent.
                    if (viewer.scene && viewer.scene.globe && viewer.terrainProvider &&
                        !(viewer.terrainProvider instanceof Cesium.EllipsoidTerrainProvider)) {{
                        var gh = viewer.scene.globe.getHeight(scratchCarto);
                        if (typeof gh === 'number' && isFinite(gh) && gh > -400 && gh < 8900) {{
                            terrainHeight = gh;
                        }}
                    }}
                    if (viewer.scene && viewer.scene.sampleHeightSupported) {{
                        var excl = [trailEntity, beaconEntity];
                        if (activePrimitive) excl.push(activePrimitive);
                        if (glbEntity) excl.push(glbEntity);
                        var sh = viewer.scene.sampleHeight(scratchCarto, excl, 0.5);
                        if (typeof sh === 'number' && isFinite(sh) && sh > -400 && sh < 8900) {{
                            if (terrainHeight === null || (sh >= terrainHeight - 5 && sh <= terrainHeight + 45)) {{
                                terrainHeight = terrainHeight === null ? sh : Math.max(terrainHeight, sh);
                            }}
                        }}
                    }}
                }} catch (ignore) {{}}
                return terrainHeight !== null ? terrainHeight : baseGroundAltitude;
            }}

            var terrainProbedDetailed = false;
            setInterval(function() {{
                if (!simControl.flying) {{
                    var gTarget = sampleLocalGround(targetLat, targetLon);
                    if (typeof gTarget === 'number' && isFinite(gTarget) && Math.abs(gTarget - baseGroundAltitude) > 1.5) {{
                        applyDiscoveredGroundElevation(gTarget);
                    }}
                }}
                if (!terrainProbedDetailed && viewer.terrainProvider && viewer.terrainProvider.availability && typeof Cesium.sampleTerrainMostDetailed === 'function') {{
                    terrainProbedDetailed = true;
                    var pts = [
                        Cesium.Cartographic.fromDegrees(targetLon, targetLat)
                    ];
                    Cesium.sampleTerrainMostDetailed(viewer.terrainProvider, pts).then(function(updated) {{
                        var c = updated && updated[0];
                        if (c && typeof c.height === 'number' && isFinite(c.height) && c.height > -400) {{
                            applyDiscoveredGroundElevation(c.height);
                        }}
                    }}).catch(function() {{ terrainProbedDetailed = false; }});
                }}
            }}, 350);

            function updateModelMatrix() {{
                if (!activePrimitive || activePrimitive.isDestroyed()) return;
                Cesium.Cartesian3.fromDegrees(pose.longitude, pose.latitude, pose.altitude + currentDef.heightOffset, undefined, scratchOrigin);
                scratchHpr.heading = Cesium.Math.toRadians(pose.heading + currentDef.headingOffset);
                scratchHpr.pitch = Cesium.Math.toRadians(pose.pitch);
                scratchHpr.roll = Cesium.Math.toRadians(pose.roll);
                activePrimitive.modelMatrix = Cesium.Transforms.headingPitchRollToFixedFrame(scratchOrigin, scratchHpr);
                activePrimitive.show = (simControl.camMode !== 'cockpit');
            }}

            function spinAircraftParts(now, dt) {{
                if (!activePrimitive || !activePrimitive.ready) return;
                propellerPhase = (propellerPhase + dt * (18 + pose.velocity * 0.25)) % (Math.PI * 2);
                (currentDef.propellerNodes || []).forEach(function(name) {{
                    if (!partNodes.has(name)) {{
                        try {{ partNodes.set(name, activePrimitive.getNode(name) || null); }} catch(e) {{ partNodes.set(name, null); }}
                    }}
                    var node = partNodes.get(name);
                    if (!node) return;
                    var angle = name === 'TailRotor' ? now * 0.028 : propellerPhase;
                    var rot = Cesium.Matrix4.fromRotationTranslation(Cesium.Matrix3.fromRotationX(angle));
                    node.matrix = Cesium.Matrix4.multiply(node.originalMatrix, rot, new Cesium.Matrix4());
                }});
                (currentDef.rotorNodes || []).forEach(function(name) {{
                    if (!partNodes.has(name)) {{
                        try {{ partNodes.set(name, activePrimitive.getNode(name) || null); }} catch(e) {{ partNodes.set(name, null); }}
                    }}
                    var node = partNodes.get(name);
                    if (!node) return;
                    var angle = now * (name === 'MainRotor' ? 0.035 : 0.095);
                    var rot = Cesium.Matrix4.fromRotationTranslation(Cesium.Matrix3.fromRotationY(angle));
                    node.matrix = Cesium.Matrix4.multiply(node.originalMatrix, rot, new Cesium.Matrix4());
                }});
            }}

            function loadAircraftGlb(def) {{
                var gen = ++modelLoadGen;
                partNodes.clear();
                if (activePrimitive && !activePrimitive.isDestroyed()) {{
                    viewer.scene.primitives.remove(activePrimitive);
                    activePrimitive = null;
                }}
                Cesium.Model.fromGltfAsync({{
                    url: def.modelUrl,
                    scale: def.scale,
                    minimumPixelSize: def.minPx,
                    maximumScale: def.maxScale,
                    incrementallyLoadTextures: false,
                    clampAnimations: true,
                    allowPicking: false
                }}).then(function(model) {{
                    if (gen !== modelLoadGen || viewer.isDestroyed()) {{
                        if (!model.isDestroyed()) model.destroy();
                        return;
                    }}
                    activePrimitive = viewer.scene.primitives.add(model);
                    updateModelMatrix();
                }}).catch(function(err) {{
                    console.warn('Could not load aircraft GLB:', err);
                }});
            }}

            function update4DTwinMapsLinks() {{
                var url = 'https://4dtwinmaps.com/fly?lat=' + pose.latitude.toFixed(6) + '&lon=' + pose.longitude.toFixed(6) + '&heading=' + Math.round(pose.heading) + '&model=' + encodeURIComponent(currentDef.id);
                var l1 = document.getElementById('lnkTop4DTwinMaps');
                var l2 = document.getElementById('lnkHangar4DTwinMaps');
                if (l1) l1.href = url;
                if (l2) l2.href = url;
            }}

            function resetAircraftToApproach(loadModelNow) {{
                var p = currentDef.physics;
                pose.latitude = targetLat - currentDef.spawnDistDeg;
                pose.longitude = targetLon;
                var resolvedGround = Math.max(
                    baseGroundAltitude,
                    sampleLocalGround(targetLat, targetLon),
                    sampleLocalGround(pose.latitude, pose.longitude)
                );
                if (resolvedGround > baseGroundAltitude) {{
                    baseGroundAltitude = resolvedGround;
                    if (beaconEntity) beaconEntity.position = Cesium.Cartesian3.fromDegrees(targetLon, targetLat, baseGroundAltitude + 26);
                }}
                pose.altitude = resolvedGround + currentDef.spawnAlt;
                pose.heading = 0;
                pose.pitch = currentDef.physicsType === 'fixed-wing' ? -2 : 0;
                pose.roll = 0;
                pose.velocity = currentDef.initialSpeed;
                pose.verticalSpeed = 0;

                engineState.throttle = currentDef.initialSpeed > 0
                    ? clamp((currentDef.initialSpeed - p.stallSpeed * 0.55) / Math.max(1, (p.maximumSpeed - p.stallSpeed * 0.55) * 0.76), 0.2, 0.85)
                    : 0.5;
                engineState.motorPower = 0.5;
                engineState.bodyForwardVelocity = currentDef.initialSpeed;
                engineState.bodyLateralVelocity = 0;
                engineState.yawRate = 0;
                engineState.aerobaticPitch = pose.pitch;
                engineState.aerobaticRoll = 0;

                rigState.initialized = false;
                rigState.orbitHeading = 0;
                rigState.orbitPitch = -Math.atan2(currentDef.spawnAlt,
                    currentDef.spawnDistDeg * Math.PI / 180 * EARTH_RADIUS_METERS) * 180 / Math.PI - pose.pitch + 3;
                rigState.zoom = 1.0;
                trailPositions = [];

                document.getElementById('hudAircraftName').textContent = 'AIR ENGINE / ' + currentDef.name;
                document.getElementById('hudStatusText').textContent = simControl.flying ? (simControl.paused ? 'PAUSED' : 'IN FLIGHT') : 'READY FOR LAUNCH';
                update4DTwinMapsLinks();
                if (loadModelNow) loadAircraftGlb(currentDef);
                updateModelMatrix();
                applyCamera(0.016, false);
            }}

            function teleportToAssetPin(pin) {{
                if (!pin || !isFinite(pin.lat) || !isFinite(pin.lon)) return;
                targetLat = Number(pin.lat);
                targetLon = Number(pin.lon);
                var gHere = sampleLocalGround(targetLat, targetLon);
                baseGroundAltitude = Math.max(0, gHere);
                if (beaconEntity) {{
                    beaconEntity.position = Cesium.Cartesian3.fromDegrees(targetLon, targetLat, baseGroundAltitude + 26);
                    if (beaconEntity.label) beaconEntity.label.text = String(pin.name || 'MosaicAsset');
                }}
                resetAircraftToApproach(false);
            }}

            // 2D Mini-Map Radar Drawing + Click-to-Fly
            var radarCanvas = document.getElementById('simMiniMapCanvas');
            var radarCtx = radarCanvas ? radarCanvas.getContext('2d') : null;
            var radarHalfSpanKm = 2.0; // Default 2.0 km radius
            var renderedPinScreenCoords = [];

            var btnZoomIn = document.getElementById('btnRadarZoomIn');
            var btnZoomOut = document.getElementById('btnRadarZoomOut');
            if (btnZoomIn) btnZoomIn.addEventListener('click', function() {{
                radarHalfSpanKm = Math.max(0.25, radarHalfSpanKm * 0.5);
            }});
            if (btnZoomOut) btnZoomOut.addEventListener('click', function() {{
                radarHalfSpanKm = Math.min(2500, radarHalfSpanKm * 2.0);
            }});
            if (radarCanvas) {{
                radarCanvas.addEventListener('click', function(ev) {{
                    var rect = radarCanvas.getBoundingClientRect();
                    var cx = ev.clientX - rect.left;
                    var cy = ev.clientY - rect.top;
                    var bestPin = null;
                    var bestDist = 16;
                    for (var i = 0; i < renderedPinScreenCoords.length; i++) {{
                        var item = renderedPinScreenCoords[i];
                        var d = Math.hypot(cx - item.x, cy - item.y);
                        if (d < bestDist) {{
                            bestDist = d;
                            bestPin = item.pin;
                        }}
                    }}
                    if (bestPin) {{
                        teleportToAssetPin(bestPin);
                    }} else {{
                        // Click anywhere on radar to fly to that coordinate
                        var w = radarCanvas.width, h = radarCanvas.height;
                        var dxKm = ((cx - w / 2) / (w / 2)) * radarHalfSpanKm;
                        var dyKm = ((h / 2 - cy) / (h / 2)) * radarHalfSpanKm;
                        var latCos = Math.max(0.05, Math.cos(pose.latitude * Math.PI / 180));
                        var clickLat = pose.latitude + (dyKm / 111.32);
                        var clickLon = pose.longitude + (dxKm / (111.32 * latCos));
                        teleportToAssetPin({{ name: 'Selected Coordinate', lat: clickLat, lon: clickLon }});
                    }}
                }});
            }}

            function drawMiniMapRadar() {{
                if (!radarCtx || !radarCanvas) return;
                var w = radarCanvas.width, h = radarCanvas.height;
                var midX = w / 2, midY = h / 2;
                radarCtx.clearRect(0, 0, w, h);

                // Radar grid rings
                radarCtx.strokeStyle = 'rgba(153, 204, 0, 0.18)';
                radarCtx.lineWidth = 1;
                [0.35, 0.7].forEach(function(r) {{
                    radarCtx.beginPath();
                    radarCtx.arc(midX, midY, Math.min(midX, midY) * r, 0, Math.PI * 2);
                    radarCtx.stroke();
                }});
                radarCtx.beginPath();
                radarCtx.moveTo(midX, 0); radarCtx.lineTo(midX, h);
                radarCtx.moveTo(0, midY); radarCtx.lineTo(w, midY);
                radarCtx.stroke();

                var latCos = Math.max(0.05, Math.cos(pose.latitude * Math.PI / 180));
                renderedPinScreenCoords = [];

                // Draw asset pins
                (radarPins || []).forEach(function(pin) {{
                    if (!pin || !isFinite(pin.lat) || !isFinite(pin.lon)) return;
                    var dNorthKm = (pin.lat - pose.latitude) * 111.32;
                    var dEastKm = (pin.lon - pose.longitude) * 111.32 * latCos;
                    var nx = dEastKm / radarHalfSpanKm;
                    var ny = dNorthKm / radarHalfSpanKm;
                    // Clamp edge pins so distant assets still show direction on the perimeter
                    var mag = Math.hypot(nx, ny);
                    var clamped = false;
                    if (mag > 0.92) {{
                        nx = (nx / mag) * 0.92;
                        ny = (ny / mag) * 0.92;
                        clamped = true;
                    }}
                    var sx = midX + nx * (w / 2);
                    var sy = midY - ny * (h / 2);
                    renderedPinScreenCoords.push({{ x: sx, y: sy, pin: pin }});

                    radarCtx.beginPath();
                    radarCtx.arc(sx, sy, clamped ? 3.5 : 5, 0, Math.PI * 2);
                    radarCtx.fillStyle = clamped ? 'rgba(56, 189, 248, 0.75)' : '#99CC00';
                    radarCtx.fill();
                    radarCtx.strokeStyle = '#050806';
                    radarCtx.lineWidth = 1.5;
                    radarCtx.stroke();
                }});

                // Draw aircraft heading arrow at center
                radarCtx.save();
                radarCtx.translate(midX, midY);
                radarCtx.rotate(pose.heading * Math.PI / 180);
                radarCtx.beginPath();
                radarCtx.moveTo(0, -8);
                radarCtx.lineTo(6, 7);
                radarCtx.lineTo(0, 4);
                radarCtx.lineTo(-6, 7);
                radarCtx.closePath();
                radarCtx.fillStyle = '#ffffff';
                radarCtx.fill();
                radarCtx.restore();

                // Update scale bar text
                var lblScale = document.getElementById('lblRadarScale');
                if (lblScale) {{
                    lblScale.textContent = radarHalfSpanKm < 1
                        ? ('Scale: ' + Math.round(radarHalfSpanKm * 1000) + ' m')
                        : ('Scale: ' + radarHalfSpanKm.toFixed(1) + ' km');
                }}
            }}

            // Input handling
            var pressed = new Set();
            var touchInput = {{ throttle: 0, brake: 0, steer: 0, pitch: 0, strafe: 0, boost: false }};

            window.addEventListener('keydown', function(e) {{
                var tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
                if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
                if (['KeyW','KeyS','KeyA','KeyD','KeyQ','KeyE','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','ShiftLeft','ShiftRight','Space'].indexOf(e.code) >= 0) {{
                    e.preventDefault();
                }}
                pressed.add(e.code);
                if (e.code === 'KeyP' && simControl.flying) {{
                    document.getElementById('btnHudPause').click();
                }} else if (e.code === 'KeyR' && simControl.flying) {{
                    resetAircraftToApproach(false);
                }} else if (e.code === 'KeyC') {{
                    document.getElementById('btnToggleCam').click();
                }}
            }});
            window.addEventListener('keyup', function(e) {{ pressed.delete(e.code); }});
            window.addEventListener('blur', function() {{ pressed.clear(); }});

            document.querySelectorAll('.sim-pad-btn[data-touch]').forEach(function(btn) {{
                var spec = btn.getAttribute('data-touch').split(':');
                var key = spec[0];
                var val = parseFloat(spec[1]);
                var start = function(e) {{
                    if (e && e.cancelable) e.preventDefault();
                    if (key === 'boost') touchInput.boost = true;
                    else touchInput[key] = val;
                }};
                var end = function() {{
                    if (key === 'boost') touchInput.boost = false;
                    else touchInput[key] = 0;
                }};
                btn.addEventListener('mousedown', start);
                btn.addEventListener('touchstart', start);
                btn.addEventListener('mouseup', end);
                btn.addEventListener('mouseleave', end);
                btn.addEventListener('touchend', end);
            }});

            var draggingCam = false;
            var lastMouseX = 0, lastMouseY = 0;
            var canvasEl = viewer.scene.canvas;
            canvasEl.addEventListener('mousedown', function(e) {{
                if (simControl.camMode === 'free') return;
                draggingCam = true;
                lastMouseX = e.clientX;
                lastMouseY = e.clientY;
            }});
            window.addEventListener('mousemove', function(e) {{
                if (!draggingCam || simControl.camMode === 'free') return;
                var dx = e.clientX - lastMouseX;
                var dy = e.clientY - lastMouseY;
                lastMouseX = e.clientX;
                lastMouseY = e.clientY;
                rigState.orbitHeading = signedAngle(rigState.orbitHeading + dx * 0.28);
                rigState.orbitPitch = clamp(rigState.orbitPitch - dy * 0.22, -28, 32);
            }});
            window.addEventListener('mouseup', function() {{ draggingCam = false; }});
            canvasEl.addEventListener('dblclick', function() {{
                rigState.orbitHeading = 0;
                rigState.orbitPitch = 0;
                rigState.zoom = 1.0;
            }});
            canvasEl.addEventListener('wheel', function(e) {{
                if (simControl.camMode === 'free') return;
                e.preventDefault();
                rigState.zoom = clamp(rigState.zoom + (e.deltaY > 0 ? 0.04 : -0.04), 0.55, 2.4);
            }}, {{ passive: false }});

            function readInput() {{
                var has = function(c) {{ return pressed.has(c); }};
                var throttle = Math.max(has('KeyW') ? 1 : 0, touchInput.throttle);
                var brake = Math.max(has('KeyS') ? 1 : 0, touchInput.brake);
                var steer = touchInput.steer !== 0 ? touchInput.steer : ((has('KeyD') ? 1 : 0) - (has('KeyA') ? 1 : 0));
                var pitch = touchInput.pitch !== 0 ? touchInput.pitch : ((has('ArrowUp') ? 1 : 0) - (has('ArrowDown') ? 1 : 0));
                var strafe = touchInput.strafe !== 0 ? touchInput.strafe : ((has('KeyE') || has('ArrowRight') ? 1 : 0) - (has('KeyQ') || has('ArrowLeft') ? 1 : 0));
                var boost = has('ShiftLeft') || has('ShiftRight') || has('Space') || touchInput.boost;
                return {{ throttle: throttle, brake: brake, steer: steer, pitch: pitch, strafe: strafe, boost: boost }};
            }}

            function airDensityRatio() {{
                return clamp(Math.exp(-Math.max(0, pose.altitude) / 9200), 0.14, 1);
            }}

            function stepFixedWing(input, dt) {{
                var p = currentDef.physics;
                engineState.throttle = clamp(engineState.throttle + (input.throttle - input.brake) * dt * 0.52, 0.06, 1);
                var density = airDensityRatio();
                var densityAuthority = 1 - p.airDensityEffect + p.airDensityEffect * density;
                var boost = input.boost ? p.boostMultiplier : 1;
                var minimum = Math.max(7, p.stallSpeed * 0.55);
                var targetSpeed = clamp(minimum + engineState.throttle * (p.maximumSpeed - minimum) * (input.boost ? 1 : 0.76), minimum, p.maximumSpeed);
                var thrustAcc = p.acceleration * (0.28 + engineState.throttle * 0.72) * boost * (0.72 + density * 0.28);
                var dragAcc = p.drag * pose.velocity * pose.velocity * (0.68 + density * 0.32);
                var acc = thrustAcc - dragAcc - input.brake * p.braking;
                if (pose.velocity > targetSpeed) acc -= p.braking * 0.42;
                if (pose.velocity < targetSpeed && acc < p.acceleration * 0.08) acc = p.acceleration * 0.08;
                pose.velocity = clamp(pose.velocity + acc * dt, minimum, p.maximumSpeed);

                var authority = clamp(pose.velocity / Math.max(1, p.cruiseSpeed), 0.18, 1.55) * densityAuthority;
                var rollCmd = clamp(input.steer + input.strafe * 0.65, -1, 1);
                var targetPitch = clamp(input.pitch * p.pitchRate, -p.pitchRate, p.pitchRate);
                var targetRoll = clamp(rollCmd * p.rollLimit, -p.rollLimit, p.rollLimit);
                pose.pitch = approach(pose.pitch, targetPitch, p.pitchRate * 1.8 * authority / p.inertia, dt);
                pose.roll = approach(pose.roll, targetRoll, p.turnRate * 2.5 * authority / p.inertia, dt);

                var bankTurn = Math.sin(pose.roll * Math.PI / 180) * p.turnRate * authority;
                var rudderTurn = input.steer * p.turnRate * 0.32 * authority;
                pose.heading = normalizeHeading(pose.heading + (bankTurn + rudderTurn) * dt);

                var stallFraction = clamp((p.stallSpeed * 1.04 - pose.velocity) / Math.max(1, p.stallSpeed * 0.48), 0, 1);
                var commandedVertical = pose.velocity * Math.sin(pose.pitch * Math.PI / 180);
                var bankSink = (1 - Math.cos(pose.roll * Math.PI / 180)) * p.descentRate * 0.22;
                var targetVertical = commandedVertical - stallFraction * p.descentRate * p.stallSeverity - bankSink;
                pose.verticalSpeed = approach(pose.verticalSpeed, targetVertical, p.climbRate * 2.2 / p.inertia, dt);
                engineState.bodyForwardVelocity = pose.velocity;
                engineState.bodyLateralVelocity = 0;
                advanceBody(dt, pose.velocity, 0);
            }}

            // Zero-drift Hover + Yaw + Banked Roll for Camera Drone & Helicopter
            function stepHover(input, dt) {{
                var p = currentDef.physics;
                var assisted = currentDef.assisted;
                var sport = input.boost ? 1.0 : 0.68;
                // Stay 100% stationary when no pitch/strafe keys are pressed!
                var fwdCmd = input.pitch;
                var targetForward = fwdCmd * p.maximumSpeed * sport;
                var targetLateral = input.strafe * p.maximumSpeed * sport;
                var forwardRate = targetForward === 0 ? p.braking * 1.4 : p.acceleration;
                var lateralRate = targetLateral === 0 ? p.braking * 1.4 : p.lateralAcceleration;
                engineState.bodyForwardVelocity = approach(engineState.bodyForwardVelocity, targetForward, forwardRate / p.inertia, dt);
                engineState.bodyLateralVelocity = approach(engineState.bodyLateralVelocity, targetLateral, lateralRate / p.inertia, dt);

                var motorTarget = clamp(0.5 + (input.throttle - input.brake) * 0.5, 0, 1);
                var motorBlend = 1 - Math.exp(-dt / Math.max(0.025, p.motorResponse));
                engineState.motorPower += (motorTarget - engineState.motorPower) * motorBlend;
                var density = airDensityRatio();
                var densityAuthority = 1 - p.airDensityEffect + p.airDensityEffect * density;
                var horizSpeed = Math.hypot(engineState.bodyForwardVelocity, engineState.bodyLateralVelocity);
                var vertCmd = (input.throttle - input.brake) === 0
                    ? 0
                    : (engineState.motorPower >= 0.5
                        ? (engineState.motorPower - 0.5) * 2 * p.climbRate
                        : (engineState.motorPower - 0.5) * 2 * p.descentRate);
                pose.verticalSpeed = approach(pose.verticalSpeed, vertCmd * densityAuthority, p.climbRate * 3.0 / p.inertia, dt);

                // Pure Yaw on A/D + coordinated Yaw when banking with Q/E at forward speed
                var speedFactor = clamp(horizSpeed / Math.max(1, p.cruiseSpeed), 0, 1);
                var desiredYawRate = (input.steer + input.strafe * 0.35 * speedFactor) * p.turnRate;
                engineState.yawRate = approach(engineState.yawRate, desiredYawRate, p.turnRate * 4.2, dt);
                pose.heading = normalizeHeading(pose.heading + engineState.yawRate * dt);

                // Both Yaw AND Roll: banking on Q/E (strafe) AND coordinated roll bank on A/D (steer)
                var yawBankFactor = currentDef.id === 'helicopter' ? 0.52 : 0.42;
                var rollCmd = clamp(input.strafe + input.steer * yawBankFactor, -1, 1);
                pose.roll = approach(pose.roll, rollCmd * p.rollLimit, p.turnRate * 2.8, dt);
                var forwardLean = -fwdCmd * Math.min(18, p.pitchRate * 0.55);
                pose.pitch = approach(pose.pitch, forwardLean, p.pitchRate * 2.5, dt);
                pose.velocity = horizSpeed;
                advanceBody(dt, engineState.bodyForwardVelocity, engineState.bodyLateralVelocity);
            }}

            // 4DTwinMaps Paramotor Physics (low & slow powered canopy with climb/glide & roll)
            function stepParamotor(input, dt) {{
                var p = currentDef.physics;
                var speedMult = 1 + input.pitch * 0.38 + (input.boost ? 0.35 : 0);
                var targetSpeed = clamp(p.cruiseSpeed * speedMult, p.stallSpeed * 0.7, p.maximumSpeed);
                pose.velocity = approach(pose.velocity, targetSpeed, p.acceleration / p.inertia, dt);

                var steerCmd = clamp(input.steer + input.strafe * 0.75, -1, 1);
                var rollTarget = steerCmd * p.rollLimit;
                pose.roll = approach(pose.roll, rollTarget, p.turnRate * 2.2 / p.inertia, dt);
                var bankTurn = Math.sin(pose.roll * Math.PI / 180) * p.turnRate;
                var yawTurn = steerCmd * p.turnRate * 0.45;
                pose.heading = normalizeHeading(pose.heading + (bankTurn + yawTurn) * dt);

                engineState.throttle = clamp(0.5 + (input.throttle - input.brake) * 0.5, 0, 1);
                var climbCmd = (input.throttle - input.brake) * p.climbRate;
                var naturalSink = (input.throttle === 0 && input.brake === 0) ? -0.35 : 0;
                pose.verticalSpeed = approach(pose.verticalSpeed, climbCmd + naturalSink, p.climbRate * 2.2, dt);
                pose.pitch = approach(pose.pitch, clamp(-input.pitch * 10 + (input.throttle - input.brake) * 6, -16, 16), p.pitchRate * 2.0, dt);
                engineState.bodyForwardVelocity = pose.velocity;
                engineState.bodyLateralVelocity = 0;
                advanceBody(dt, pose.velocity, 0);
            }}

            function advanceBody(dt, fwdMs, latMs) {{
                var headRad = pose.heading * Math.PI / 180;
                var rightRad = headRad + Math.PI / 2;
                var north = Math.cos(headRad) * fwdMs + Math.cos(rightRad) * latMs;
                var east = Math.sin(headRad) * fwdMs + Math.sin(rightRad) * latMs;
                var gSpeed = Math.hypot(north, east);
                var gHeading = gSpeed > 0.01 ? normalizeHeading(Math.atan2(east, north) * 180 / Math.PI) : pose.heading;
                var next = advancePosition({{ latitude: pose.latitude, longitude: pose.longitude, altitude: pose.altitude, heading: gHeading, pitch: 0, velocity: gSpeed }}, dt);
                pose.latitude = next.latitude;
                pose.longitude = next.longitude;
                var localGround = sampleLocalGround(pose.latitude, pose.longitude);
                // Allow Drone (1.0m) and Paramotor (1.4m) to get right down close to the ground/model!
                var minAlt = localGround + (currentDef.physics.minimumClearance || 1.2);
                var nextAlt = pose.altitude + pose.verticalSpeed * dt;
                if (nextAlt < minAlt) {{
                    nextAlt = minAlt;
                    if (pose.verticalSpeed < 0) pose.verticalSpeed = 0;
                }}
                pose.altitude = clamp(nextAlt, minAlt, currentDef.physics.maximumAltitude);
            }}

            function applyCamera(dt, boost) {{
                if (simControl.camMode === 'free') {{
                    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
                    viewer.scene.screenSpaceCameraController.enableInputs = true;
                    return;
                }}
                viewer.trackedEntity = undefined;
                viewer.scene.screenSpaceCameraController.enableInputs = false;

                if (simControl.camMode === 'cockpit') {{
                    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
                    var cockPos = Cesium.Cartesian3.fromDegrees(pose.longitude, pose.latitude, pose.altitude + 0.8);
                    viewer.camera.setView({{
                        destination: cockPos,
                        orientation: {{
                            heading: Cesium.Math.toRadians(pose.heading + rigState.orbitHeading),
                            pitch: Cesium.Math.toRadians(pose.pitch - 3.0 + rigState.orbitPitch),
                            roll: Cesium.Math.toRadians(pose.roll * 0.55)
                        }}
                    }});
                    return;
                }}

                var cam = currentDef.camera;
                var speedRatio = clamp(Math.abs(pose.velocity) / 120, 0, 1);
                var lookAhead = cam.lookAhead + Math.abs(pose.velocity) * cam.speedLookAhead + (boost ? 3.5 : 0);
                var targetGeo = advancePosition({{ latitude: pose.latitude, longitude: pose.longitude, altitude: pose.altitude, heading: pose.heading, pitch: 0, velocity: lookAhead }}, 1);
                var desiredPitch = clamp(-Math.atan2(cam.chaseHeight, cam.chaseDistance) * (180 / Math.PI) + pose.pitch * 0.25 + rigState.orbitPitch, -68, -4);
                var desiredRoll = clamp((pose.roll || 0) * 0.22, -16, 16);
                var desiredFov = clamp(cam.minFov + (cam.maxFov - cam.minFov) * speedRatio + (boost ? 5 : 0), cam.minFov, cam.maxFov + 6);
                var desiredRange = Math.hypot(cam.chaseDistance, cam.chaseHeight) * (1 + speedRatio * 0.14 + (boost ? 0.08 : 0)) * clamp(rigState.zoom, 0.55, 2.4);
                var desiredHeading = normalizeHeading(pose.heading + rigState.orbitHeading);

                if (!rigState.initialized) {{
                    rigState.initialized = true;
                    rigState.heading = desiredHeading;
                    rigState.pitch = desiredPitch;
                    rigState.roll = desiredRoll;
                    rigState.fov = desiredFov;
                    rigState.range = desiredRange;
                }} else {{
                    var blend = 1 - Math.exp(-Math.min(dt, 0.1) * 7.5);
                    var hDelta = signedAngle(desiredHeading - rigState.heading);
                    rigState.heading = normalizeHeading(rigState.heading + hDelta * blend);
                    rigState.pitch += (desiredPitch - rigState.pitch) * blend;
                    rigState.roll += (desiredRoll - rigState.roll) * blend;
                    rigState.fov += (desiredFov - rigState.fov) * blend;
                    rigState.range += (desiredRange - rigState.range) * blend;
                }}

                Cesium.Cartesian3.fromDegrees(targetGeo.longitude, targetGeo.latitude, pose.altitude + cam.lookHeight, undefined, scratchTarget);
                scratchRange.heading = Cesium.Math.toRadians(rigState.heading);
                scratchRange.pitch = Cesium.Math.toRadians(rigState.pitch);
                scratchRange.range = rigState.range;
                viewer.camera.lookAt(scratchTarget, scratchRange);
                if (Math.abs(rigState.roll) > 0.05) {{
                    viewer.camera.twistRight(Cesium.Math.toRadians(rigState.roll));
                }}
                if (viewer.camera.frustum instanceof Cesium.PerspectiveFrustum) {{
                    viewer.camera.frustum.fov = Cesium.Math.toRadians(rigState.fov);
                }}
            }}

            // UI Wiring
            document.querySelectorAll('.aircraft-option').forEach(function(card) {{
                card.addEventListener('click', function() {{
                    document.querySelectorAll('.aircraft-option').forEach(function(c) {{ c.classList.remove('selected'); }});
                    card.classList.add('selected');
                    var id = card.getAttribute('data-aircraft') || 'drone';
                    currentDef = FLIGHT_MODELS[id] || FLIGHT_MODELS['drone'];
                    resetAircraftToApproach(true);
                }});
            }});

            document.getElementById('btnStartFlying').addEventListener('click', function() {{
                var gNow = Math.max(baseGroundAltitude, sampleLocalGround(targetLat, targetLon), sampleLocalGround(pose.latitude, pose.longitude));
                if (gNow > baseGroundAltitude) applyDiscoveredGroundElevation(gNow);
                if (pose.altitude < baseGroundAltitude + currentDef.spawnAlt * 0.85) {{
                    pose.altitude = baseGroundAltitude + currentDef.spawnAlt;
                    rigState.initialized = false;
                }}
                document.getElementById('simHangarOverlay').classList.add('is-hidden');
                simControl.flying = true;
                simControl.paused = false;
                document.getElementById('btnHudPause').classList.remove('active');
                document.getElementById('btnHudPause').textContent = 'Pause';
                document.getElementById('hudStatusText').textContent = 'IN FLIGHT';
            }});

            document.getElementById('btnOpenHangar').addEventListener('click', function() {{
                simControl.paused = true;
                update4DTwinMapsLinks();
                document.getElementById('simHangarOverlay').classList.remove('is-hidden');
            }});

            document.getElementById('btnResetApproach').addEventListener('click', function() {{
                resetAircraftToApproach(false);
            }});

            document.getElementById('btnToggleCam').addEventListener('click', function() {{
                simControl.camMode = simControl.camMode === 'cockpit' ? 'chase' : simControl.camMode === 'chase' ? 'free' : 'cockpit';
                var label = simControl.camMode === 'cockpit' ? '1st Person HUD' : simControl.camMode === 'chase' ? 'Rear Chase Rig' : 'Free Globe Orbit';
                document.getElementById('lblCamMode').textContent = label;
                if (simControl.camMode === 'chase') rigState.initialized = false;
            }});

            document.getElementById('btnHudPause').addEventListener('click', function() {{
                simControl.paused = !simControl.paused;
                this.classList.toggle('active', simControl.paused);
                this.textContent = simControl.paused ? 'Resume' : 'Pause';
                document.getElementById('hudStatusText').textContent = simControl.paused ? 'PAUSED' : 'IN FLIGHT';
            }});

            resetAircraftToApproach(true);

            var lastTime = performance.now();
            function tick(now) {{
                var dt = Math.min(0.05, Math.max(0.001, (now - lastTime) / 1000.0));
                lastTime = now;
                var input = readInput();

                if (simControl.flying && !simControl.paused) {{
                    if (currentDef.physicsType === 'fixed-wing') stepFixedWing(input, dt);
                    else if (currentDef.physicsType === 'paramotor') stepParamotor(input, dt);
                    else stepHover(input, dt);

                    if (now - lastTrailAt > 140) {{
                        lastTrailAt = now;
                        trailPositions.push(Cesium.Cartesian3.fromDegrees(pose.longitude, pose.latitude, pose.altitude));
                        if (trailPositions.length > 36) trailPositions.shift();
                    }}
                }}

                updateModelMatrix();
                spinAircraftParts(now, dt);
                applyCamera(dt, input.boost);
                drawMiniMapRadar();

                // HUD Telemetry
                var airspeedMs = Math.hypot(pose.velocity, pose.verticalSpeed);
                var currentGround = sampleLocalGround(pose.latitude, pose.longitude);
                var aglMeters = Math.max(0, Math.round(pose.altitude - currentGround));
                document.getElementById('hudSpeed').textContent = String(Math.round(airspeedMs * 3.6));
                document.getElementById('hudAltitude').textContent = String(aglMeters);
                var aslEl = document.getElementById('hudAltitudeAsl');
                if (aslEl) aslEl.textContent = String(Math.round(pose.altitude));
                document.getElementById('hudHeading').textContent = String(Math.round(pose.heading)).padStart(3, '0');
                var energyPct = (currentDef.physicsType === 'rotor' || currentDef.physicsType === 'multirotor')
                    ? Math.round(engineState.motorPower * 100)
                    : Math.round(engineState.throttle * 100);
                document.getElementById('hudEnergy').textContent = String(energyPct);
                var dLatKm = (pose.latitude - targetLat) * 111.32;
                var dLonKm = (pose.longitude - targetLon) * 111.32 * Math.cos(Cesium.Math.toRadians(targetLat));
                document.getElementById('hudDist').textContent = Math.hypot(dLatKm, dLonKm).toFixed(2);
                if (simControl.flying && !simControl.paused) {{
                    document.getElementById('hudStatusText').textContent = input.boost ? 'BOOST / SPORT' : 'IN FLIGHT';
                }}

                requestAnimationFrame(tick);
            }}
            requestAnimationFrame(tick);
        }})();
    </script>"""

    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_public_mosaics_page(user: Optional[Dict[str, Any]] = None, lang: str = "en") -> str:
    norm_lang = normalize_lang(lang)
    store = get_store()
    all_mymaps = sorted(
        store.tables.get("dbo.WEB_MYMAPS", []),
        key=lambda m: int(m.get("ID") or 0),
        reverse=True,
    )
    seo_head = build_head_metadata_html(
        title="Public Mosaics · Multi-Layer 3D Scenes · MosaicEarth",
        description="Explore public and shared multi-asset 3D scenes (Mosaics) combining orthophotos, 3D meshes, point clouds and Gaussian splats on the global Earth.",
        canonical_path=f"/{norm_lang}/PublicMosaics",
        robots="index, follow",
    )
    head_extra = """
    <link rel="stylesheet" href="/assets/css/Projects.css?v=20261015-gaussian-r35" />
    <style>
        .me-unified-node {
            background: #17211b;
            border: 1px solid #344239;
            border-radius: 6px;
            margin-bottom: 12px;
            padding: 14px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
            transition: border-color 0.16s ease, background 0.16s ease;
        }
        .me-unified-node:hover {
            border-color: #99CC00;
            background: #1a261f;
        }
        .me-unified-icon {
            width: 38px;
            height: 38px;
            border-radius: 6px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: rgba(153, 204, 0, 0.14);
            border: 1px solid rgba(153, 204, 0, 0.45);
            color: #99CC00;
            flex-shrink: 0;
        }
        .me-unified-icon svg { width: 18px; height: 18px; }
        .me-unified-pill {
            display: inline-block;
            padding: 2px 7px;
            border-radius: 4px;
            font-family: OpenSansBold, Arial, sans-serif;
            font-size: 9.5px;
            letter-spacing: 0.07em;
            text-transform: uppercase;
            background: #233316;
            color: #99CC00;
            border: 1px solid #5a7a22;
        }
        .me-unified-chip {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 2px 8px;
            border-radius: 4px;
            background: #121915;
            border: 1px solid #344239;
            color: #b8c6be;
            font-size: 11px;
        }
        .me-unified-btn {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 13px;
            border-radius: 5px;
            font-family: OpenSansSemiBold, Arial, sans-serif;
            font-size: 11.5px;
            color: #dce6dc !important;
            background: #17211b;
            border: 1px solid #45564b;
            cursor: pointer;
            text-decoration: none !important;
            transition: all 0.15s ease;
            white-space: nowrap;
        }
        .me-unified-btn:hover {
            border-color: #99CC00;
            color: #99CC00 !important;
            background: #1d2a22;
        }
        .me-unified-btn--primary {
            background: #99CC00 !important;
            color: #0b110d !important;
            border-color: #99CC00 !important;
            font-family: OpenSansBold, Arial, sans-serif;
        }
        .me-unified-btn--primary:hover {
            background: #aad916 !important;
            color: #0b110d !important;
        }
        .me-unified-btn--accent {
            background: rgba(153, 204, 0, 0.14);
            color: #99CC00 !important;
            border-color: rgba(153, 204, 0, 0.55);
            font-family: OpenSansBold, Arial, sans-serif;
        }
        .me-unified-btn--accent:hover {
            background: #99CC00 !important;
            color: #0b110d !important;
        }
    </style>"""

    cards_html = []
    for m in all_mymaps:
        if not m.get("ACTIVO", True):
            continue
        mid = int(m.get("ID") or 0)
        if mid <= 0:
            continue
        raw_name = str(m.get("NAME") or f"Mosaic #{mid}")
        if bool(m.get("IS_CONTAINER_PREVIEW")) or raw_name.startswith(("Survey: ", "Project: ", "Task: ")):
            continue
        vis_state = str(m.get("VISIBILITY") or "public").strip().lower()
        if vis_state != "public":
            continue
        mname = html.escape(raw_name)
        mdescr = html.escape(str(m.get("DESCRIPTION") or "Multi-layer georeferenced 3D scene combining orthophotos, 3D models and point clouds."))
        slug = str(m.get("SEO_URL_FRIENDLY") or f"{mid}-{to_slug(mname)}")
        owner_id = int(m.get("USER_ID") or m.get("CREATE_ID") or 0)
        owner = store.get_user_by_id(owner_id)
        owner_name = html.escape(str((owner.get("USERNAME") or owner.get("NAME") or "MosaicEarth Creator") if owner else "MosaicEarth Creator"))
        mdate = str(m.get("CREATE_DATE") or m.get("DATE") or "")[:10]
        pub_cnt = int(m.get("PublicationCount") or 0)
        viewer_url = f"/{norm_lang}/MyMaps/{slug}"
        cards_html.append(f"""
        <div class="me-unified-node" data-mosaic-name="{mname.lower()} {owner_name.lower()}">
            <div style="display:flex;align-items:flex-start;gap:12px;flex:1;min-width:260px;">
                <span class="me-unified-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                </span>
                <div style="min-width:0;">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <span class="me-unified-pill">PUBLIC MOSAIC</span>
                        <a href="{viewer_url}" style="font-size:15.5px;color:#ffffff;font-family:OpenSansSemiBold,Arial,sans-serif;text-decoration:none;">{mname}</a>
                    </div>
                    <div style="font-size:12px;color:#b8c6be;margin-top:3px;">{mdescr}</div>
                    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;">
                        <span class="me-unified-chip"><i class="fas fa-user" style="color:#99CC00;"></i> {owner_name}</span>
                        {f'<span class="me-unified-chip"><i class="far fa-calendar-alt"></i> {mdate}</span>' if mdate else ''}
                        {f'<span class="me-unified-chip"><i class="fas fa-layer-group"></i> {pub_cnt} Layers</span>' if pub_cnt > 0 else ''}
                    </div>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                <a href="{viewer_url}" class="me-unified-btn me-unified-btn--accent">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                    Open in 3D Map
                </a>
                <a href="{viewer_url}" class="me-unified-btn me-unified-btn--primary">
                    <i class="fas fa-plane-departure"></i> Explore &amp; Fly
                </a>
            </div>
        </div>""")

    create_cta = (
        f'<a href="/Client/MyMapDetail.aspx" class="me-unified-btn me-unified-btn--primary">+ NEW MYMOSAIC</a>'
        if user
        else '<a href="/Default.aspx?ReturnUrl=%2Fen%2FMyMaps" class="me-unified-btn me-unified-btn--primary">SIGN IN TO CREATE MYMOSAIC</a>'
    )

    body_content = f"""
    <section id="MyMaps" class="workspace-section" style="padding-top: 32px; padding-bottom: 60px;">
        <div class="container">
            <div class="row">
                <div class="col-12">
                    <div class="box-4d">
                        <div class="titulo-box">
                            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                                <div>Public Mosaics &middot; Multi-Asset 3D Scenes</div>
                                <div>{create_cta}</div>
                            </div>
                        </div>
                        <div class="content-box" style="padding: 20px 24px;">
                            <div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px;">
                                <p style="color:#d1d5db;font-size:13px;margin:0;">
                                    Explore multi-asset 3D scenes combining geolocated orthophotos, 3D meshes, point clouds and Gaussian splats. Open any Mosaic to inspect its layers on the 3D Globe or launch <strong>Fly 1st Person</strong>.
                                </p>
                                <div style="position:relative;min-width:260px;">
                                    <i class="fas fa-search" style="position:absolute;left:12px;top:50%;transform:translateY(-50%);color:#8fa099;font-size:12px;"></i>
                                    <input type="search" placeholder="Search Public Mosaics or Creators..." oninput="var q=this.value.toLowerCase();document.querySelectorAll('[data-mosaic-name]').forEach(function(el){{el.style.display=(!q||el.getAttribute('data-mosaic-name').indexOf(q)>=0)?'':'none';}});" style="width:100%;background:#101713;border:1px solid #3b4a40;border-radius:5px;color:#fff;padding:7px 12px 7px 34px;font-size:12.5px;" />
                                </div>
                            </div>
                            <div>
                                {''.join(cards_html) if cards_html else '<p style="color:#9ca3af;">No public Mosaics published yet.</p>'}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </section>"""

    scripts_extra = """
    <script>
        CargarActiveMenu("li_public_mymaps");
    </script>"""
    return render_home_master(head_extra, seo_head, body_content, scripts_extra, user=user, lang=norm_lang)


def render_desktop_connectors_page(user: Optional[Dict[str, Any]] = None, lang: str = "en") -> str:
    from .desktop_distribution import release_manifest
    norm_lang = normalize_lang(lang)
    uid = int(user.get("ID") or 0) if user else 0
    license_info = get_store().get_desktop_license_info(uid)
    _, release = release_manifest()
    artifacts = release.get("artifacts", {})
    version = html.escape(str(release.get("version") or "being prepared"))

    def download(target, label):
        item = artifacts.get(target)
        if not item:
            return '<p class="me-muted">This release artifact is being prepared.</p>'
        sha = html.escape(item["sha256"])
        return f'<a class="me-dl" href="/api/connectors/download?target={target}">{label}</a><details class="me-checksum"><summary>Verify SHA-256</summary><code>{sha}</code></details>'

    if license_info.get("licensed"):
        account = '<p>Installer access for <strong>' + html.escape(str(license_info["email"])) + '</strong>.</p>'
        account += download("desktop-installer-win", "Windows x64 · graphical .exe installer")
        account += download("desktop-installer-linux", "Linux amd64 · .deb package")
        account += '<button type="button" id="desktop-devices">Manage activated computers</button><div id="desktop-device-list"></div>'
    elif user:
        account = '<p>The official installer costs €29 once, or is included with an active Studio plan.</p><button type="button" id="desktop-purchase">Buy official installer · €29</button><a class="me-dl" href="/en/Account">View Studio plan</a>'
    else:
        account = '<p>The official installer costs €29 once, or is included with an active Studio plan.</p><a class="me-dl" href="/Login.aspx?ReturnUrl=%2Fen%2FDesktop">Sign in to buy or download</a>'
    plugins = ''.join(f'<article class="me-card"><h2>{label}</h2><p>{description}</p>{download(target, "Download plugin ZIP")}<a href="https://github.com/nsicart/MosaicEarth-Desktop/blob/main/CONNECTORS.md">Installation instructions</a></article>'
        for target, label, description in (
            ("webodm", "WebODM", "Synchronize completed tasks, original photographs, products and processing metadata from WebODM. Restore cloud files into a new local task."),
            ("webui", "OpenDroneMap WebUI", "A separate package for OpenDroneMap’s WebUI plugin system. Transfer completed tasks and restore cloud projects with verified downloads."),
            ("qgis", "QGIS", "Synchronize project settings, data layers, styles and coordinate systems. Reopen downloaded QGIS projects or add compatible cloud products as layers.")))
    body = f'''<main class="me-distribution"><p class="me-eyebrow">MOSAICEARTH · DESKTOP & CONNECTORS</p><h1>Your projects, locally and in the cloud</h1>
<p class="me-intro">Process on your computer with NodeODX and OpenSplat. Synchronize photographs, completed models and project data with MyEarth when you choose.</p>
<p>Release {version} · canonical cloud service: <a href="https://app.mosaicearth.app">app.mosaicearth.app</a></p>
<div class="me-grid"><article class="me-card me-featured"><h2>Official Desktop installer</h2>{account}
<p>Windows uses a graphical installation wizard. Linux uses its package manager. The official distribution is associated with your account and up to two computers. Activation grants last 30 days for offline use; renew with the same eligible account.</p>
<p>Local processing requires Docker with Linux containers. The application checks the installation and downloads verified NodeODX and OpenSplat CPU engines (about 1.3 GB). Allow at least 20 GB of free disk space. NVIDIA CUDA setup is documented separately. macOS installers are not available.</p>
<p>Processing and synchronized files remain private until you explicitly publish them. Installed data is preserved when uninstalling the application.</p></article>
<article class="me-card"><h2>Open-source Desktop</h2><p>Build and run the local edition without signing in. Cloud synchronization requires a MosaicEarth account because it accesses your private cloud storage.</p>
<a class="me-dl" href="https://github.com/nsicart/MosaicEarth-Desktop">Source code and build instructions</a>{download("desktop-source", "Corresponding release source")}
<p>AGPL-3.0-or-later. The account controls the commercial distribution and services; it does not remove the redistribution rights granted by open-source licenses.</p>
<a href="/open-source">Engine source code and third-party notices</a></article></div>
<h2 class="me-section">Open-source connectors</h2><div class="me-grid me-three">{plugins}</div>
<article class="me-card"><h2>What synchronization transfers</h2><p>Original photographs and other source files, completed products, supporting files and project metadata. Transfers resume from verified chunks and check complete files with SHA-256. Downloading creates a new local copy; it does not overwrite an existing project.</p>
<p>The cloud preserves revisions and prepares supported products for its viewers. File transfer and viewer preparation have separate statuses. A format unsupported by a host application remains available as its original file. Storage is subject to your account’s quota; synchronization does not automatically purchase cloud processing.</p></article>
<p id="desktop-message" role="status" aria-live="polite"></p></main>'''
    css = '''<style>.me-distribution{max-width:1200px;margin:auto;padding:40px 24px;color:#e9f1eb}.me-distribution h1{font-size:clamp(28px,4vw,46px);line-height:1.15}.me-intro{font-size:18px;max-width:850px}.me-eyebrow,.me-muted{color:#b6cbbb}.me-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin:28px 0}.me-three{grid-template-columns:repeat(3,minmax(0,1fr))}.me-card{padding:24px;border:1px solid #416047;border-radius:12px;background:#17251c;min-width:0}.me-featured{border-color:#9bc56d}.me-card h2{font-size:23px;margin-top:0}.me-card p{line-height:1.65}.me-distribution a{color:#b6e893}.me-dl,.me-distribution button{display:block;padding:12px 16px;margin:12px 0;border:1px solid #72915c;border-radius:7px;background:#284625;color:#eff8e9!important;font-weight:600;text-decoration:none}.me-distribution button{cursor:pointer;width:100%;text-align:left}.me-distribution button:disabled{opacity:.55;cursor:wait}.me-distribution :focus-visible{outline:3px solid #c4f381;outline-offset:3px}.me-checksum code{display:block;overflow-wrap:anywhere;color:#d7e5d3}.me-section{margin-top:40px}#desktop-message{padding:12px;background:#203224;border-radius:6px}#desktop-message:empty{display:none}@media(max-width:800px){.me-grid,.me-three{grid-template-columns:1fr}.me-distribution{padding:24px 16px}}</style>'''
    scripts = '''<script src="/mosaicearth-desktop-distribution.js?v=020" defer></script>'''
    seo = build_head_metadata_html(title="MosaicEarth Desktop and open-source connectors", description="Local NodeODX photogrammetry, OpenSplat reconstruction and full private project synchronization for Desktop, WebODM, WebUI and QGIS.", canonical_path=f"/{norm_lang}/Desktop", robots="index, follow")
    return render_home_master(css, seo, body, scripts, user=user, lang=norm_lang)



