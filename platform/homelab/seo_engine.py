import html
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlencode
import urllib.request

from .db_store import (
    DEFAULT_CATEGORIES,
    ICON_3D,
    ICON_CLOUD,
    ICON_ORTHOPHOTO,
    family_display,
    get_store,
    subtype_display,
    to_slug,
)

INDEXNOW_KEYS = {
    "4d8f9a2e7c1b6053e9a4d2c8b7f16350",
    "a8f4e2c9d1b647309e5a1c2b3d4e5f60",
}
DEFAULT_INDEXNOW_KEY = "4d8f9a2e7c1b6053e9a4d2c8b7f16350"

SUPPORTED_REGIONS: List[Dict[str, Any]] = [
    {
        "slug": "catalonia-costa-brava-emporda",
        "name": "Costa Brava & Empordà (Catalonia, Spain)",
        "es_name": "Costa Brava y Empordà (Cataluña, España)",
        "description": "Explore georeferenced 3D models, drone orthomosaics and point clouds across Costa Brava and Empordà, including Castell del Montgrí and Torroella de Montgrí.",
        "es_description": "Explora modelos 3D georreferenciados, ortomosaicos de dron y nubes de puntos en la Costa Brava y el Empordà, incluyendo el Castell del Montgrí.",
        "center_lat": 42.0521,
        "center_lon": 3.1316,
        "min_lat": 41.70,
        "max_lat": 42.45,
        "min_lon": 2.85,
        "max_lon": 3.35,
        "default_height_meters": 4500,
    },
    {
        "slug": "pyrenees-cerdanya-mountain-terrain",
        "name": "Pyrenees & Cerdanya Alpine Valley (Catalonia, Spain)",
        "es_name": "Pirineos y Valle de la Cerdaña (Cataluña, España)",
        "description": "High-relief alpine 3D terrain models, Ansovell mountain surveys, OGC 3D Tilesets and classified LAS/LAZ point clouds in the Catalan Pyrenees.",
        "es_description": "Modelos 3D de terreno alpino de alto relieve, levantamientos en Ansovell, OGC 3D Tiles y nubes de puntos LAS/LAZ en los Pirineos catalanes.",
        "center_lat": 42.3450,
        "center_lon": 1.7250,
        "min_lat": 42.15,
        "max_lat": 42.60,
        "min_lon": 1.35,
        "max_lon": 2.15,
        "default_height_meters": 6000,
    },
    {
        "slug": "priorat-tarragona-vineyards",
        "name": "DOQ Priorat Terraced Vineyards (Tarragona, Spain)",
        "es_name": "Viñedos en Terrazas DOQ Priorat (Tarragona, España)",
        "description": "Precision viticulture drone orthophotos, 1 m contour lines, 3D meshes and point clouds in Gratallops, Rojerals and Nas d'en Quiuma (Priorat).",
        "es_description": "Ortofotos de viticultura de precisión, curvas de nivel de 1 m, mallas 3D y nubes de puntos en Gratallops, Rojerals y Nas d'en Quiuma (Priorat).",
        "center_lat": 41.1926,
        "center_lon": 0.7515,
        "min_lat": 41.05,
        "max_lat": 41.35,
        "min_lon": 0.60,
        "max_lon": 0.95,
        "default_height_meters": 3500,
    },
    {
        "slug": "osona-sau-reservoir-hydrology",
        "name": "Pantà de Sau & Ter River Basin (Osona, Spain)",
        "es_name": "Embalse de Sau y Cuenca del Ter (Osona, España)",
        "description": "Multi-temporal reservoir drought monitoring orthomosaics, DSM/DTM elevation models and 3D heritage reconstructions at Pantà de Sau.",
        "es_description": "Ortomosaicos multitemporales de seguimiento de sequía en embalses, modelos DSM/DTM y reconstrucciones 3D en el Pantà de Sau.",
        "center_lat": 41.9702,
        "center_lon": 2.3885,
        "min_lat": 41.85,
        "max_lat": 42.10,
        "min_lon": 2.20,
        "max_lon": 2.55,
        "default_height_meters": 4000,
    },
    {
        "slug": "girona-ter-valley-bescano",
        "name": "Girona & Bescanó Ter Valley (Catalonia, Spain)",
        "es_name": "Girona y Valle del Ter en Bescanó (Cataluña, España)",
        "description": "Urban digital twins, municipal cadastre orthomosaics and 3D building reconstructions across Bescanó and the Girona Ter valley.",
        "es_description": "Gemelos digitales urbanos, ortomosaicos catastrales municipales y reconstrucciones 3D en Bescanó y el valle del Ter en Girona.",
        "center_lat": 41.9656,
        "center_lon": 2.7418,
        "min_lat": 41.88,
        "max_lat": 42.08,
        "min_lon": 2.60,
        "max_lon": 2.90,
        "default_height_meters": 3500,
    },
    {
        "slug": "occitanie-french-pyrenees-castles",
        "name": "Occitanie & Hautes-Pyrénées Heritage (France)",
        "es_name": "Patrimonio de Occitania y Altos Pirineos (Francia)",
        "description": "3D photogrammetry mesh, high-resolution orthomosaic and classified point cloud of Château de Mauvezin in Hautes-Pyrénées, Occitanie.",
        "es_description": "Malla fotogramétrica 3D, ortomosaico de alta resolución y nube de puntos del Château de Mauvezin en Altos Pirineos, Occitania.",
        "center_lat": 43.1187,
        "center_lon": 0.2768,
        "min_lat": 42.85,
        "max_lat": 43.35,
        "min_lon": -0.10,
        "max_lon": 0.65,
        "default_height_meters": 2500,
    },
    {
        "slug": "stara-zagora-thracian-heritage",
        "name": "Stara Zagora & Thracian Plain (Bulgaria)",
        "es_name": "Stara Zagora y Llanura Tracia (Bulgaria)",
        "description": "Urban 3D models, sports venues (Sk8 St.Z), archaeological landmarks and high-GSD orthomosaics in Stara Zagora, Bulgaria.",
        "es_description": "Modelos 3D urbanos, recintos deportivos (Sk8 St.Z), patrimonio arqueológico y ortomosaicos en Stara Zagora, Bulgaria.",
        "center_lat": 42.4171,
        "center_lon": 25.6143,
        "min_lat": 42.25,
        "max_lat": 42.60,
        "min_lon": 25.40,
        "max_lon": 25.85,
        "default_height_meters": 3500,
    },
    {
        "slug": "varna-black-sea-coastal-zone",
        "name": "Varna & Sv. Konstantin i Elena Black Sea Coast (Bulgaria)",
        "es_name": "Varna y Costa del Mar Negro (Bulgaria)",
        "description": "Coastal orthomosaics, 3D building models and engineering point clouds along the Varna and Sv. Konstantin i Elena Black Sea shoreline.",
        "es_description": "Ortomosaicos costeros, modelos 3D y nubes de puntos de ingeniería en la costa del Mar Negro de Varna y Sv. Konstantin i Elena.",
        "center_lat": 43.2352,
        "center_lon": 27.9648,
        "min_lat": 43.10,
        "max_lat": 43.40,
        "min_lon": 27.75,
        "max_lon": 28.15,
        "default_height_meters": 4000,
    },
]

SUPPORTED_FAMILIES: List[Dict[str, Any]] = [
    {
        "file_type_id": 2,
        "route_slug": "maps",
        "legacy_key": "orthophoto",
        "title": "Public Orthophotos, GeoTIFF Maps & GIS Vector Layers · MosaicEarth",
        "h1": "Explore Georeferenced Orthophotos, Elevation Rasters & GIS Layers",
        "description": "Browse and inspect georeferenced drone orthophotos (GeoTIFF/COG), DSM/DTM elevation models, contour lines and vector GIS overlays streamed on the MosaicEarth 3D globe.",
    },
    {
        "file_type_id": 3,
        "route_slug": "3d-models",
        "legacy_key": "model3d",
        "title": "Public 3D Photogrammetry Models, GLB Meshes & Gaussian Splats · MosaicEarth",
        "h1": "Explore Georeferenced 3D Models, Textured Meshes & Gaussian Splats",
        "description": "Discover real-world 3D photogrammetry reconstructions, OGC 3D Tiles, KTX2 GLB meshes and 3D Gaussian Splatting scenes positioned on the MosaicEarth globe.",
    },
    {
        "file_type_id": 1,
        "route_slug": "point-clouds",
        "legacy_key": "pointcloud",
        "title": "Public LiDAR & Photogrammetry Point Clouds (LAS, LAZ, PLY) · MosaicEarth",
        "h1": "Explore Georeferenced LiDAR & Photogrammetric Point Clouds",
        "description": "Stream and inspect georeferenced LAS, LAZ and PLY point clouds in 3D Tiles and Potree directly in your browser on MosaicEarth.",
    },
]

CATEGORY_DESCRIPTIONS = {
    13: "Georeferenced 3D models, orthomosaics and point clouds of towns, municipalities and historic settlements.",
    14: "High-resolution 3D cultural heritage models, castles, churches and architectural monuments.",
    15: "Aerial orthophotos, digital surface models and terrain surveys of open fields, vineyards and rural land.",
    16: "Geospatial reconstructions of natural landscapes, alpine forests, geological formations and parks.",
    17: "3D digital twins and georeferenced captures of iconic landmarks and points of interest.",
    18: "Reservoirs, dams, coastlines, rivers and multi-temporal hydrological survey layers on the 3D globe.",
    19: "Shoreline orthomosaics, coastal cliffs, ports and marine infrastructure 3D reconstructions.",
    20: "Outdoor event venues, skateparks, stadiums and temporary festival logistics 3D mapping.",
    21: "Archaeological excavations, medieval fortresses, historic sites and heritage digital twins.",
    22: "Quarries, solar plants, civil engineering works and industrial facility 3D inspections.",
}


def get_public_site_url() -> str:
    return (os.environ.get("PUBLIC_SITE_URL") or "https://app.mosaicearth.app").rstrip("/")


def get_landing_site_url() -> str:
    return "https://mosaicearth.app"


def normalize_lang(lang: Optional[str]) -> str:
    return "es" if str(lang or "en").strip().lower() == "es" else "en"


def is_canonical_production_host(host: Optional[str]) -> bool:
    if not host:
        return False
    clean = host.strip().lower().split(":")[0]
    return clean == "app.mosaicearth.app"


def get_active_categories() -> List[Dict[str, Any]]:
    result = []
    for cat_id, info in sorted(DEFAULT_CATEGORIES.items()):
        result.append(
            {
                "id": cat_id,
                "name": info["name"],
                "slug": info["slug"],
                "description": CATEGORY_DESCRIPTIONS.get(cat_id, ""),
            }
        )
    return result


def resolve_region(slug: Optional[str]) -> Optional[Dict[str, Any]]:
    if not slug:
        return None
    clean = slug.strip().lower()
    for reg in SUPPORTED_REGIONS:
        if reg["slug"].lower() == clean:
            return reg
    return None


def resolve_family(slug_or_type: Optional[str]) -> Optional[Dict[str, Any]]:
    if not slug_or_type:
        return None
    clean = str(slug_or_type).strip().lower()
    if clean in ("2", "maps", "map", "maps-and-layers", "maps-layers", "orthophoto", "orthophotos"):
        return SUPPORTED_FAMILIES[0]
    if clean in ("3", "3d-models", "models-3d", "3d", "model3d", "models"):
        return SUPPORTED_FAMILIES[1]
    if clean in ("1", "point-clouds", "pointcloud", "point-cloud", "lidar"):
        return SUPPORTED_FAMILIES[2]
    return None


def resolve_category(slug_or_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not slug_or_id:
        return None
    clean = str(slug_or_id).strip().lower()
    if clean.isdigit():
        cid = int(clean)
        if 1 <= cid <= 10:
            cid += 12
        for c in get_active_categories():
            if c["id"] == cid:
                return c
    if clean == "urban-areas":
        clean = "coastal-areas"
    elif clean == "agricultural-areas":
        clean = "events-festivals"
    elif clean == "hilly-terrain":
        clean = "historical-sites"
    for c in get_active_categories():
        if c["slug"].lower() == clean or to_slug(c["name"]) == clean:
            return c
    return None


def build_processed_asset_path(publish_id: int, name: str, lang: str = "en") -> str:
    return f"/{normalize_lang(lang)}/assets/{to_slug(name)}-{int(publish_id)}"


def build_external_asset_path(project_id: int, asset_id: int, name: str, lang: str = "en") -> str:
    return f"/{normalize_lang(lang)}/external-assets/{to_slug(name)}-{int(project_id)}-{int(asset_id)}"


def try_parse_trailing_int(slug_or_id: str) -> Optional[int]:
    if not slug_or_id:
        return None
    clean = slug_or_id.strip()
    if clean.isdigit() and int(clean) > 0:
        return int(clean)
    if "-" in clean:
        tail = clean.rsplit("-", 1)[-1]
        if tail.isdigit() and int(tail) > 0:
            return int(tail)
    return None


def try_parse_external_slug(slug: str) -> Optional[Tuple[int, int]]:
    if not slug:
        return None
    parts = [p for p in slug.strip().split("-") if p]
    if len(parts) < 2:
        return None
    if parts[-2].isdigit() and parts[-1].isdigit():
        pid, aid = int(parts[-2]), int(parts[-1])
        if pid > 0 and aid > 0:
            return pid, aid
    return None


def build_dynamic_og_image_url(
    title: str,
    subtitle: Optional[str] = None,
    badge: Optional[str] = None,
    location: Optional[str] = None,
) -> str:
    params = {}
    if title:
        params["title"] = title
    if subtitle:
        params["subtitle"] = subtitle
    if badge:
        params["badge"] = badge
    if location:
        params["location"] = location
    qs = f"?{urlencode(params)}" if params else ""
    return f"{get_public_site_url()}/api/og{qs}"


def build_web_application_json_ld() -> Dict[str, Any]:
    base_url = get_public_site_url()
    return {
        "@context": "https://schema.org",
        "@type": ["WebApplication", "SoftwareApplication"],
        "name": "MosaicEarth",
        "url": f"{base_url}/en/Map",
        "applicationCategory": "GeographicInformationSystem, 3DGraphicsApplication",
        "operatingSystem": "Web Browser (WebGL2, Windows, macOS, Linux, iOS, Android)",
        "description": "Geospatial 3D digital twin platform for processing, georeferencing, viewing, sharing and licensing drone orthophotos, 3D photogrammetry models, Gaussian Splats and LiDAR point clouds.",
        "publisher": {
            "@type": "Organization",
            "name": "MosaicEarth",
            "url": get_landing_site_url(),
        },
        "offers": {
            "@type": "AggregateOffer",
            "priceCurrency": "EUR",
            "lowPrice": "0",
            "offerCount": "228",
            "description": "Free public 3D globe exploration and free/credit-based geospatial asset licensing.",
        },
    }


def build_breadcrumb_json_ld(crumbs: List[Tuple[str, str]]) -> Dict[str, Any]:
    base_url = get_public_site_url()
    elements = []
    for idx, (label, url) in enumerate(crumbs, start=1):
        full_url = url if url.startswith("http") else f"{base_url}{url if url.startswith('/') else '/' + url}"
        elements.append(
            {
                "@type": "ListItem",
                "position": idx,
                "name": label,
                "item": full_url,
            }
        )
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": elements,
    }


def build_dataset_json_ld(
    name: str,
    description: str,
    canonical_url: str,
    creator_name: str,
    family_name: str,
    download_format: str,
    latitude: Optional[float],
    longitude: Optional[float],
    date_published: Optional[str],
    price_credits: int,
    preview_image_url: Optional[str] = None,
    keywords: Optional[List[str]] = None,
) -> Dict[str, Any]:
    base_url = get_public_site_url()
    dataset: Dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": name,
        "description": description.strip() if description and description.strip() else f"{name} — georeferenced {family_name} published on MosaicEarth.",
        "url": canonical_url,
        "isAccessibleForFree": price_credits <= 0,
        "license": f"{base_url}/License.aspx",
        "creator": {
            "@type": "Person",
            "name": creator_name or "MosaicEarth creator",
        },
        "publisher": {
            "@type": "Organization",
            "name": "MosaicEarth",
            "url": get_landing_site_url(),
        },
        "encodingFormat": download_format or "GeoTIFF / 3D Tiles / LAS",
    }
    if preview_image_url:
        dataset["image"] = preview_image_url
    if date_published:
        dataset["datePublished"] = date_published[:10]
    if keywords:
        dataset["keywords"] = keywords
    if latitude is not None and longitude is not None and (abs(latitude) > 0.0001 or abs(longitude) > 0.0001):
        lat = round(float(latitude), 6)
        lon = round(float(longitude), 6)
        span = 0.008
        dataset["spatialCoverage"] = {
            "@type": "Place",
            "name": f"{name} ({lat:.4f}, {lon:.4f})",
            "geo": {
                "@type": "GeoCoordinates",
                "latitude": lat,
                "longitude": lon,
            },
            "additionalProperty": {
                "@type": "GeoShape",
                "box": f"{lat - span:.4f} {lon - span:.4f} {lat + span:.4f} {lon + span:.4f}",
            },
        }
    return dataset


def build_head_metadata_html(
    title: str,
    description: str,
    canonical_path: str,
    alternate_es_path: Optional[str] = None,
    og_type: str = "website",
    og_image_url: Optional[str] = None,
    robots: str = "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1",
    json_ld_graphs: Optional[List[Dict[str, Any]]] = None,
) -> str:
    base_url = get_public_site_url()
    clean_path = (canonical_path or "/en/Map").strip()
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    if "?" in clean_path:
        clean_path = clean_path.split("?", 1)[0]

    canonical_url = f"{base_url}{clean_path}"
    en_url = canonical_url
    if clean_path.lower().startswith("/es/"):
        en_url = f"{base_url}/en/{clean_path[4:]}"
    if alternate_es_path:
        es_url = f"{base_url}{alternate_es_path if alternate_es_path.startswith('/') else '/' + alternate_es_path}"
    elif clean_path.lower().startswith("/en/"):
        es_url = f"{base_url}/es/{clean_path[4:]}"
    else:
        es_url = None

    clean_title = (title or "MosaicEarth · 3D Geospatial Globe, Orthophotos, 3D Models & Point Clouds").strip()
    clean_desc = (
        description
        or "Explore, georeference, share and license drone orthophotos, 3D photogrammetry models, Gaussian Splats and LiDAR point clouds on the MosaicEarth 3D globe."
    ).strip()
    og_image = og_image_url or build_dynamic_og_image_url(
        clean_title, clean_desc, "MOSAICEARTH 3D GLOBE", "Global Geospatial Platform"
    )
    if og_image and not og_image.startswith("http"):
        og_image = f"{base_url}{og_image if og_image.startswith('/') else '/' + og_image}"

    lines = [
        f'    <title>{html.escape(clean_title)}</title>',
        f'    <meta name="description" content="{html.escape(clean_desc, quote=True)}" />',
        f'    <meta name="robots" content="{html.escape(robots, quote=True)}" />',
        f'    <link rel="canonical" href="{html.escape(canonical_url, quote=True)}" />',
        f'    <link rel="alternate" hreflang="en" href="{html.escape(en_url, quote=True)}" />',
    ]
    if es_url:
        lines.append(f'    <link rel="alternate" hreflang="es" href="{html.escape(es_url, quote=True)}" />')
    lines.extend(
        [
            f'    <link rel="alternate" hreflang="x-default" href="{html.escape(en_url, quote=True)}" />',
            f'    <meta property="og:type" content="{html.escape(og_type, quote=True)}" />',
            '    <meta property="og:site_name" content="MosaicEarth" />',
            f'    <meta property="og:title" content="{html.escape(clean_title, quote=True)}" />',
            f'    <meta property="og:description" content="{html.escape(clean_desc, quote=True)}" />',
            f'    <meta property="og:url" content="{html.escape(canonical_url, quote=True)}" />',
            f'    <meta property="og:image" content="{html.escape(og_image, quote=True)}" />',
            '    <meta property="og:image:width" content="1200" />',
            '    <meta property="og:image:height" content="630" />',
            '    <meta property="og:locale" content="en_US" />',
            '    <meta name="twitter:card" content="summary_large_image" />',
            f'    <meta name="twitter:title" content="{html.escape(clean_title, quote=True)}" />',
            f'    <meta name="twitter:description" content="{html.escape(clean_desc, quote=True)}" />',
            f'    <meta name="twitter:image" content="{html.escape(og_image, quote=True)}" />',
        ]
    )
    for graph in json_ld_graphs or []:
        if not graph:
            continue
        raw_json = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace("</script>", "<\\/script>")
        lines.append(f'    <script type="application/ld+json">{raw_json}</script>')
    return "\n".join(lines)


def get_public_directory_items(
    lang: str = "en",
    file_type_filter: Optional[int] = None,
    category_id_filter: Optional[int] = None,
    search_query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    norm_lang = normalize_lang(lang)
    base_url = get_public_site_url()
    store = get_store()
    raw_items = store.build_public_asset_index()

    items: List[Dict[str, Any]] = []
    for row in raw_items:
        ftype = int(row.get("FileTypeId") or 2)
        pub_id = int(row.get("PublishId") or 0)
        ext_id = int(row.get("ExternalAssetId") or 0)
        proj_id = int(row.get("ProjectId") or 0)
        name = str(row.get("Name") or f"MosaicAsset #{pub_id or ext_id}")
        if ext_id > 0:
            path = build_external_asset_path(proj_id, ext_id, name, norm_lang)
        else:
            path = build_processed_asset_path(pub_id, name, norm_lang)

        cat_id = row.get("CategoryId")
        cat_info = DEFAULT_CATEGORIES.get(int(cat_id)) if cat_id is not None else None
        items.append(
            {
                "Key": row.get("Key"),
                "Source": row.get("Source") or "processed",
                "ProjectId": proj_id,
                "PublishId": pub_id,
                "ExternalAssetId": ext_id,
                "FileTypeId": ftype,
                "FamilyDisplay": row.get("FamilyDisplay") or family_display(ftype),
                "SubtypeDisplay": row.get("SubtypeDisplay") or subtype_display(ftype),
                "Name": name,
                "Description": row.get("Description") or "",
                "Creator": row.get("Creator") or "MosaicEarth creator",
                "CategoryId": int(cat_id) if cat_id is not None else None,
                "CategoryName": cat_info["name"] if cat_info else (row.get("CategoryName") or ""),
                "CategorySlug": cat_info["slug"] if cat_info else "",
                "Latitude": float(row.get("Latitude") or 0.0),
                "Longitude": float(row.get("Longitude") or 0.0),
                "DateLabel": row.get("DateLabel") or "Published",
                "DateDisplay": row.get("DateDisplay") or "—",
                "SortDateTicks": int(row.get("SortDateTicks") or 0),
                "DownloadFormat": row.get("DownloadFormat") or "GEOTIFF · TIF",
                "PriceCredits": int(row.get("PriceCredits") or 0),
                "PreviewUrl": row.get("PreviewUrl") or "",
                "CanonicalPath": path,
                "CanonicalUrl": f"{base_url}{path}",
                "IconSvg": row.get("IconSvg") or (ICON_CLOUD if ftype == 1 else ICON_3D if ftype == 3 else ICON_ORTHOPHOTO),
            }
        )

    if file_type_filter in (1, 2, 3):
        items = [x for x in items if x["FileTypeId"] == file_type_filter]
    if category_id_filter and category_id_filter > 0:
        items = [x for x in items if x["CategoryId"] == category_id_filter]
    if search_query and search_query.strip():
        q = search_query.strip().lower()
        items = [
            x
            for x in items
            if q in x["Name"].lower() or q in x["Creator"].lower() or q in x["CategoryName"].lower()
        ]
    return items


def render_server_catalogue_html(
    lang: str,
    active_family: Optional[Dict[str, Any]] = None,
    active_category: Optional[Dict[str, Any]] = None,
    search_query: Optional[str] = None,
    current_page: int = 1,
    page_size: int = 24,
    active_region: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[Dict[str, Any]], int]:
    norm_lang = normalize_lang(lang)
    is_es = norm_lang == "es"
    ftype_filter = active_family["file_type_id"] if active_family else None
    cat_filter = active_category["id"] if active_category else None

    matching = get_public_directory_items(norm_lang, ftype_filter, cat_filter, search_query)
    if active_region:
        matching = [
            x
            for x in matching
            if active_region["min_lat"] <= x["Latitude"] <= active_region["max_lat"]
            and active_region["min_lon"] <= x["Longitude"] <= active_region["max_lon"]
        ]
    total_count = len(matching)
    page_size = max(12, min(60, page_size))
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    current_page = max(1, min(total_pages, current_page))
    page_items = matching[(current_page - 1) * page_size : current_page * page_size]

    if active_family and active_category:
        fam_name = family_display(active_family["file_type_id"])
        heading = (
            f"Explorar {active_category['name']} — {fam_name} Georreferenciados en 3D"
            if is_es
            else f"Explore {active_category['name']} {fam_name} — Georeferenced 3D Globe & Datasets"
        )
        base_path = f"/{norm_lang}/explore/{active_family['route_slug']}/{active_category['slug']}"
    elif active_region:
        heading = (
            f"Activos 3D, Ortofotos y Nubes de Puntos en {active_region['es_name']}"
            if is_es
            else f"3D Models, Orthophotos & Point Clouds in {active_region['name']}"
        )
        base_path = f"/{norm_lang}/locations/{active_region['slug']}"
    elif active_family:
        heading = active_family["h1"]
        base_path = f"/{norm_lang}/explore/{active_family['route_slug']}"
    elif active_category:
        heading = (
            f"Explorar {active_category['name']} — Modelos 3D, Ortofotos y Nubes de Puntos"
            if is_es
            else f"Explore {active_category['name']} — 3D Models, Orthophotos & Point Clouds"
        )
        base_path = f"/{norm_lang}/category/{active_category['slug']}"
    else:
        heading = (
            "Explorar Activos Geoespaciales Públicos — Mapas, Modelos 3D y Nubes de Puntos"
            if is_es
            else "Explore Public Geospatial Assets — Maps, 3D Models & Point Clouds"
        )
        base_path = f"/{norm_lang}/Map"

    sb: List[str] = []
    sb.append('<div class="mosaic-ssr-directory" data-ssr-catalogue="1">')
    sb.append('  <header class="mosaic-ssr-header" style="padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.08);">')
    sb.append(f'    <h1 style="font-size:14px;font-weight:600;color:#e6f4ea;margin:0 0 6px 0;line-height:1.35;">{html.escape(heading)}</h1>')
    sb.append(f'    <p style="font-size:11px;color:#9ca3af;margin:0 0 8px 0;">{total_count} public georeferenced assets indexed · Page {current_page} of {total_pages}</p>')

    sb.append('    <nav aria-label="Asset families" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;font-size:11px;">')
    all_color = "#99CC00" if (not active_family and not active_category) else "#d1d5db"
    sb.append(f'      <a href="/{norm_lang}/Map" style="color:{all_color};text-decoration:none;padding:2px 6px;border-radius:3px;background:rgba(255,255,255,0.06);">All Assets</a>')
    for fam in SUPPORTED_FAMILIES:
        is_active = active_family and active_family["file_type_id"] == fam["file_type_id"]
        c = "#99CC00" if is_active else "#d1d5db"
        sb.append(
            f'      <a href="/{norm_lang}/explore/{fam["route_slug"]}" style="color:{c};text-decoration:none;padding:2px 6px;border-radius:3px;background:rgba(255,255,255,0.06);">{html.escape(family_display(fam["file_type_id"]))}</a>'
        )
    sb.append("    </nav>")

    sb.append('    <nav aria-label="Geospatial categories" style="display:flex;flex-wrap:wrap;gap:4px;font-size:10px;">')
    for cat in get_active_categories():
        is_cat = active_category and active_category["id"] == cat["id"]
        c = "#99CC00" if is_cat else "#9ca3af"
        sb.append(
            f'      <a href="/{norm_lang}/category/{cat["slug"]}" style="color:{c};text-decoration:none;padding:1px 5px;border-radius:3px;border:1px solid rgba(255,255,255,0.1);">{html.escape(cat["name"])}</a>'
        )
    sb.append("    </nav>")
    sb.append("  </header>")

    for item in page_items:
        price = "FREE · 0 €" if item["PriceCredits"] <= 0 else f"{item['PriceCredits']} €"
        origin_cls = "is-imported" if item["Source"] == "imported" else "is-processed"
        origin_txt = "Generated" if item["Source"] == "gaussian" else "Imported" if item["Source"] == "imported" else "Processed"
        key_esc = html.escape(item["Key"], quote=True)
        path_esc = html.escape(item["CanonicalPath"], quote=True)
        sb.append(
            f'  <article class="mosaic-asset-card mosaic-card-v2 global-asset-card all-asset-card ssr-asset-card type-{item["FileTypeId"]}" data-asset-key="{key_esc}" data-sort-date="{item["SortDateTicks"]}" onclick="mosaicFocusAllAsset(event,\'{key_esc}\')">'
        )
        sb.append('    <div class="mosaic-card-preview">')
        sb.append('      <span class="mosaic-preview-fallback" aria-hidden="true">Preview unavailable</span>')
        if item["PreviewUrl"]:
            sb.append(
                f'      <img class="mosaic-preview-image" src="{html.escape(item["PreviewUrl"], quote=True)}" alt="{html.escape(item["Name"] + " — " + item["FamilyDisplay"], quote=True)}" loading="lazy" decoding="async" onerror="this.hidden=true" />'
            )
        sb.append(
            f'      <span class="mosaic-card-type-icon" aria-label="{html.escape(item["FamilyDisplay"], quote=True)}" title="{html.escape(item["FamilyDisplay"], quote=True)}">{item["IconSvg"]}</span>'
        )
        sb.append(f'      <span class="mosaic-card-origin {origin_cls}">{origin_txt}</span>')
        sb.append(f'      <a class="mosaic-card-details" href="{path_esc}" onclick="event.stopPropagation()">View details</a>')
        sb.append("    </div>")
        sb.append('    <div class="mosaic-card-body">')
        cat_suffix = f" · {html.escape(item['CategoryName'])}" if item["CategoryName"] else ""
        sb.append(
            f'      <p class="mosaic-card-family">{html.escape(item["FamilyDisplay"])} · {html.escape(item["SubtypeDisplay"])}{cat_suffix}</p>'
        )
        sb.append(
            f'      <h2 class="mosaic-card-title" style="font-size:14px;margin:0;"><a href="{path_esc}" style="color:inherit;text-decoration:none;" onclick="event.stopPropagation()">{html.escape(item["Name"])}</a></h2>'
        )
        sb.append(
            f'      <p class="mosaic-card-creator">{html.escape(item["Creator"])} · <small>{item["Latitude"]:.4f}, {item["Longitude"]:.4f}</small></p>'
        )
        sb.append(
            f'      <p class="mosaic-card-date"><span>{html.escape(item["DateLabel"])}:</span><strong>{html.escape(item["DateDisplay"])}</strong></p>'
        )
        sb.append(
            f'      <div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>{html.escape(item["DownloadFormat"])}</strong></span><strong class="mosaic-card-price">{html.escape(price)}</strong></div>'
        )
        sb.append("    </div>")
        sb.append("  </article>")

    spec_formats = (
        (
            ".las, .laz, .ply, .pcd, .pts, .xyz"
            if active_family["file_type_id"] == 1
            else ".glb, .gltf, .obj/.mtl (.zip), 3D Tiles, 3DGS .ply"
            if active_family["file_type_id"] == 3
            else ".tif/.tiff (GeoTIFF/COG, DSM, DTM, Slope), .gpkg, .geojson, .kml/.kmz, .shp (.zip), .dxf, .gpx"
        )
        if active_family
        else "GeoTIFF/COG, GeoPackage, GeoJSON, KML/KMZ, Shapefile, DXF, GPX, GLB/OBJ, OGC 3D Tiles, Gaussian Splat .ply, LAS/LAZ"
    )
    spec_streaming = (
        (
            "OGC 3D Tiles (.pnts Octree) + Isolated Potree Viewer"
            if active_family["file_type_id"] == 1
            else "Multi-LOD OGC 3D Tiles (tileset.json + KTX2 BasisU GPU Textures) & WebGL Splat Viewer"
            if active_family["file_type_id"] == 3
            else "Web Mercator XYZ Imagery Pyramid + Terrain-Clamped Vector Overlay"
        )
        if active_family
        else "CesiumJS 3D Globe (XYZ Imagery + OGC 3D Tiles KTX2 + .pnts) & Potree"
    )
    spec_coords = (
        f"WGS84 Center: {active_region['center_lat']:.4f}° N, {active_region['center_lon']:.4f}° E (BBox: {active_region['min_lat']:.2f}..{active_region['max_lat']:.2f}, {active_region['min_lon']:.2f}..{active_region['max_lon']:.2f})"
        if active_region
        else "WGS84 (EPSG:4326) & ECEF (EPSG:4978) Global 3D Ellipsoid"
    )

    sb.append('  <section class="mosaic-ssr-specs" style="padding:10px 12px;border-top:1px solid rgba(255,255,255,0.08);font-size:11px;color:#d1d5db;">')
    sb.append(
        f'    <h2 style="font-size:12px;font-weight:600;color:#99CC00;margin:0 0 6px 0;">{"Especificaciones Técnicas y Preconfiguración del Visor 3D" if is_es else "Technical Specifications & 3D Globe Pre-Configuration"}</h2>'
    )
    if active_region:
        sb.append(
            f'    <p style="margin:0 0 8px 0;line-height:1.5;color:#d1d5db;">{html.escape(active_region["es_description"] if is_es else active_region["description"])}</p>'
        )
    elif active_category:
        sb.append(f'    <p style="margin:0 0 8px 0;line-height:1.5;color:#d1d5db;">{html.escape(active_category["description"])}</p>')
    elif active_family:
        sb.append(f'    <p style="margin:0 0 8px 0;line-height:1.5;color:#d1d5db;">{html.escape(active_family["description"])}</p>')

    sb.append('    <table style="width:100%;border-collapse:collapse;font-size:10px;margin-bottom:8px;">')
    sb.append(
        f'      <thead><tr style="border-bottom:1px solid rgba(255,255,255,0.12);text-align:left;color:#9ca3af;"><th style="padding:4px;">{"Parámetro" if is_es else "Parameter"}</th><th style="padding:4px;">{"Especificación Activa" if is_es else "Active Specification"}</th></tr></thead>'
    )
    sb.append("      <tbody>")
    sb.append(f'        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:4px;font-weight:600;">Formats</td><td style="padding:4px;">{html.escape(spec_formats)}</td></tr>')
    sb.append(f'        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:4px;font-weight:600;">WebGL Engine</td><td style="padding:4px;">{html.escape(spec_streaming)}</td></tr>')
    sb.append(f'        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:4px;font-weight:600;">CRS / Spatial Extent</td><td style="padding:4px;">{html.escape(spec_coords)}</td></tr>')
    sb.append('        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:4px;font-weight:600;">Photogrammetry &amp; 3DGS</td><td style="padding:4px;">Managed WebODM Cloud / User Node + 3D Gaussian Splatting (12 integer credits, 30-200 photos)</td></tr>')
    sb.append('        <tr style="border-bottom:1px solid rgba(255,255,255,0.05);"><td style="padding:4px;font-weight:600;">Retention &amp; Publishing</td><td style="padding:4px;">15-day private workspace retention; 100% free permanent hosting when published to the public globe</td></tr>')
    sb.append(f'        <tr><td style="padding:4px;font-weight:600;">Indexed Assets</td><td style="padding:4px;">{total_count} public georeferenced MosaicAssets</td></tr>')
    sb.append("      </tbody>")
    sb.append("    </table>")
    workflow_text = (
        "1) Selecciona cualquier activo georreferenciado del catálogo o del globo 3D para volar directamente a sus coordenadas WGS84 con encuadre cenital automático. 2) Superpón ortofotos GeoTIFF/COG, mallas fotogramétricas OGC 3D Tiles con texturas GPU KTX2, escenas 3D Gaussian Splatting y nubes de puntos LiDAR LAS/LAZ sobre el terreno global CesiumJS o abre el visor aislado Potree. 3) Comparte enlaces con cámara exacta (solo lectura) o adquiere una licencia comercial en el Marketplace para descargar los archivos fuente originales desde el almacenamiento privado."
        if is_es
        else "1) Select any georeferenced dataset from the catalogue or 3D globe markers to fly directly to its WGS84 coordinates with automatic top-down zenithal framing. 2) Overlay Cloud-Optimized GeoTIFF orthomosaics, OGC 3D Tiles photogrammetric meshes with KTX2 BasisU GPU textures, 3D Gaussian Splatting radiance fields and classified LAS/LAZ LiDAR point clouds on the CesiumJS terrain globe or open the isolated Potree octree inspector. 3) Share camera-exact view-only links or acquire a commercial Marketplace license snapshot to download original source files from private storage."
    )
    sb.append(
        f'    <div style="margin-bottom:8px;line-height:1.45;color:#9ca3af;"><strong>{"Flujo de trabajo geoespacial" if is_es else "Geospatial Workflow & Inspection Steps"}:</strong> {workflow_text}</div>'
    )

    cats = get_active_categories()
    target_cat = active_category or cats[1]
    sb.append(f'    <div style="margin-top:6px;"><strong style="color:#e5e7eb;">{"Combinaciones Familia × Categoría" if is_es else "Combinatorial Family × Category Hubs"}:</strong> ')
    for fam in SUPPORTED_FAMILIES:
        sb.append(
            f'      <a href="/{norm_lang}/explore/{fam["route_slug"]}/{target_cat["slug"]}" style="color:#99CC00;text-decoration:none;margin-right:6px;">{html.escape(family_display(fam["file_type_id"]))} · {html.escape(target_cat["name"])}</a>'
        )
    sb.append("    </div>")

    sb.append(f'    <div style="margin-top:6px;"><strong style="color:#e5e7eb;">{"Regiones Geográficas 3D" if is_es else "3D Geographic Regions"}:</strong> ')
    for reg in SUPPORTED_REGIONS:
        sb.append(
            f'      <a href="/{norm_lang}/locations/{reg["slug"]}" style="color:#9ca3af;text-decoration:none;margin-right:6px;">{html.escape(reg["es_name"] if is_es else reg["name"])}</a>'
        )
    sb.append("    </div>")
    sb.append("  </section>")

    sb.append('  <footer style="padding:10px 12px;font-size:11px;color:#9ca3af;border-top:1px solid rgba(255,255,255,0.06);display:flex;flex-wrap:wrap;gap:10px;">')
    sb.append(f'    <a href="/{norm_lang}/directory" style="color:#99CC00;font-weight:600;text-decoration:none;">{"Directorio Global pSEO" if is_es else "Full Geospatial Directory"}</a>')
    sb.append(f'    <a href="/{norm_lang}/Faqs" style="color:#99CC00;text-decoration:none;">Help &amp; FAQs</a>')
    sb.append('    <a href="/License.aspx" style="color:#9ca3af;text-decoration:none;">Standard License</a>')
    sb.append('    <a href="/Terms.aspx" style="color:#9ca3af;text-decoration:none;">Terms of Service</a>')
    sb.append('    <a href="https://mosaicearth.app/directory" style="color:#9ca3af;text-decoration:none;">Formats &amp; Guides Hub</a>')
    sb.append('    <a href="https://mosaicearth.app" style="color:#9ca3af;text-decoration:none;">About MosaicEarth</a>')
    sb.append("  </footer>")
    sb.append("</div>")

    return "\n".join(sb), page_items, total_count


def render_robots_txt() -> str:
    base_url = get_public_site_url()
    agents = [
        "*",
        "Googlebot",
        "Bingbot",
        "Applebot",
        "OAI-SearchBot",
        "ChatGPT-User",
        "Claude-SearchBot",
        "Claude-User",
        "PerplexityBot",
        "Perplexity-User",
        "Google-Extended",
    ]
    lines = [
        "# MosaicEarth 3D Globe Platform — RFC 9309 Robots Policy",
        f"# Canonical Platform Host: {base_url}",
        "",
    ]
    for agent in agents:
        lines.extend(
            [
                f"User-agent: {agent}",
                "Allow: /",
                "Disallow: /Admin/",
                "Disallow: /Users/",
                "Disallow: /PayPal/",
                "Disallow: /Callbacks/",
                "Disallow: /api/webhooks",
                "Disallow: /api/seo/ping-indexnow",
                "",
            ]
        )
    lines.extend(
        [
            f"Sitemap: {base_url}/sitemap.xml",
            f"Sitemap: {base_url}/sitemaps/static.xml",
            f"Sitemap: {base_url}/sitemaps/tools.xml",
            f"Sitemap: {base_url}/sitemaps/entities.xml",
            "Sitemap: https://mosaicearth.app/sitemap.xml",
        ]
    )
    return "\n".join(lines) + "\n"


def _append_sitemap_node(
    lines: List[str],
    loc: str,
    en_href: str,
    es_href: str,
    lastmod_iso: str,
    changefreq: str,
    priority: str,
) -> None:
    lines.extend(
        [
            "  <url>",
            f"    <loc>{html.escape(loc)}</loc>",
            f"    <lastmod>{html.escape(lastmod_iso)}</lastmod>",
            f"    <changefreq>{html.escape(changefreq)}</changefreq>",
            f"    <priority>{html.escape(priority)}</priority>",
            f'    <xhtml:link rel="alternate" hreflang="en" href="{html.escape(en_href, quote=True)}" />',
            f'    <xhtml:link rel="alternate" hreflang="es" href="{html.escape(es_href, quote=True)}" />',
            f'    <xhtml:link rel="alternate" hreflang="x-default" href="{html.escape(en_href, quote=True)}" />',
            "  </url>",
        ]
    )


def render_sitemap_index_xml() -> str:
    base_url = get_public_site_url()
    now_iso = "2026-10-21T00:00:00Z"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for child in ("/sitemaps/static.xml", "/sitemaps/tools.xml", "/sitemaps/entities.xml"):
        lines.extend(
            [
                "  <sitemap>",
                f"    <loc>{html.escape(base_url)}{child}</loc>",
                f"    <lastmod>{now_iso}</lastmod>",
                "  </sitemap>",
            ]
        )
    lines.append("</sitemapindex>")
    return "\n".join(lines) + "\n"


def render_static_sitemap_xml() -> str:
    base_url = get_public_site_url()
    now_iso = "2026-10-21T00:00:00Z"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    pairs = [
        ("/en/explore/all", "/es/explore/all", "daily", "1.0", "0.9"),
        ("/en/directory", "/es/directory", "daily", "0.9", "0.9"),
        ("/en/faqs", "/es/faqs", "weekly", "0.8", "0.8"),
    ]
    for en_p, es_p, freq, prio_en, prio_es in pairs:
        _append_sitemap_node(lines, base_url + en_p, base_url + en_p, base_url + es_p, now_iso, freq, prio_en)
        _append_sitemap_node(lines, base_url + es_p, base_url + en_p, base_url + es_p, now_iso, freq, prio_es)
    _append_sitemap_node(lines, base_url + "/License.aspx", base_url + "/License.aspx", base_url + "/License.aspx", now_iso, "monthly", "0.5")
    _append_sitemap_node(lines, base_url + "/Terms.aspx", base_url + "/Terms.aspx", base_url + "/Terms.aspx", now_iso, "monthly", "0.5")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def render_tools_sitemap_xml() -> str:
    base_url = get_public_site_url()
    now_iso = "2026-10-21T00:00:00Z"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    families = ["maps", "3d-models", "point-clouds"]
    for fam in families:
        en_f = f"{base_url}/en/explore/{fam}"
        es_f = f"{base_url}/es/explore/{fam}"
        _append_sitemap_node(lines, en_f, en_f, es_f, now_iso, "daily", "0.9")
        _append_sitemap_node(lines, es_f, en_f, es_f, now_iso, "daily", "0.8")

    for cat in get_active_categories():
        slug = cat["slug"]
        en_c = f"{base_url}/en/category/{slug}"
        es_c = f"{base_url}/es/category/{slug}"
        _append_sitemap_node(lines, en_c, en_c, es_c, now_iso, "weekly", "0.8")
        _append_sitemap_node(lines, es_c, en_c, es_c, now_iso, "weekly", "0.8")
        for fam in families:
            en_cb = f"{base_url}/en/explore/{fam}/{slug}"
            es_cb = f"{base_url}/es/explore/{fam}/{slug}"
            _append_sitemap_node(lines, en_cb, en_cb, es_cb, now_iso, "weekly", "0.8")
            _append_sitemap_node(lines, es_cb, en_cb, es_cb, now_iso, "weekly", "0.8")

    for reg in SUPPORTED_REGIONS:
        slug = reg["slug"]
        en_r = f"{base_url}/en/locations/{slug}"
        es_r = f"{base_url}/es/locations/{slug}"
        _append_sitemap_node(lines, en_r, en_r, es_r, now_iso, "weekly", "0.8")
        _append_sitemap_node(lines, es_r, en_r, es_r, now_iso, "weekly", "0.8")

    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def render_entities_sitemap_xml() -> str:
    base_url = get_public_site_url()
    default_iso = "2026-10-21T00:00:00Z"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for item in get_public_directory_items("en"):
        en_path = item["CanonicalPath"]
        es_path = re.sub(r"^/en/", "/es/", en_path, flags=re.IGNORECASE)
        en_href = base_url + en_path
        es_href = base_url + es_path
        ticks = int(item.get("SortDateTicks") or 0)
        if ticks > 0:
            dt = datetime.fromtimestamp(ticks / 1000.0, tz=timezone.utc)
            lastmod = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            lastmod = default_iso
        _append_sitemap_node(lines, en_href, en_href, es_href, lastmod, "weekly", "0.7")
        _append_sitemap_node(lines, es_href, en_href, es_href, lastmod, "weekly", "0.7")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def render_llms_txt() -> str:
    base_url = get_public_site_url()
    items = get_public_directory_items("en")
    lines = [
        "# MosaicEarth 3D Globe & Geospatial Reality Platform (app.mosaicearth.app)",
        "",
        "> MosaicEarth is an interactive 3D & 2D geospatial digital twin platform running on CesiumJS and Potree. Users and AI agents can explore, inspect, georeference, stream and license drone orthophotos (GeoTIFF/COG), 3D photogrammetric models (GLB/OBJ/3D Tiles), 3D Gaussian Splatting scenes (.ply) and LiDAR point clouds (LAS/LAZ) without an initial login barrier.",
        "",
        f"- **Canonical 3D Globe Platform:** {base_url}",
        "- **Marketing & Engineering Formats Hub:** https://mosaicearth.app",
        f"- **Complete Dataset Inventory for LLMs:** {base_url}/llms-full.txt",
        f"- **XML Sitemap Index:** {base_url}/sitemap.xml",
        f"- **4DTwinMaps Interoperability Gateway:** {base_url}/api/v1/interoperability/assets",
        "",
        "## Primary Public Platform Entry Points",
        "",
        f"- [Interactive 3D Globe Explorer (All Assets)]({base_url}/en/explore/all): Public CesiumJS 3D globe with server-rendered dataset catalogue.",
        f"- [Full Geospatial Directory (EN)]({base_url}/en/directory): Hub & Spoke directory of all asset families, categories, geographic clusters and published datasets.",
        f"- [Directorio Geoespacial Completo (ES)]({base_url}/es/directory): Directorio completo en español de modelos 3D, ortofotos, nubes de puntos y regiones.",
        f"- [Maps & Orthophotos Explorer]({base_url}/en/explore/maps): GeoTIFF, DSM, DTM, GeoPackage, GeoJSON, KML, Shapefile and DXF layers.",
        f"- [3D Photogrammetry & Gaussian Splatting Explorer]({base_url}/en/explore/3d-models): GLB, OBJ, OGC 3D Tiles and 3D Gaussian Splatting scenes.",
        f"- [LiDAR & Point Clouds Explorer]({base_url}/en/explore/point-clouds): LAS, LAZ, PLY, PCD and XYZ point clouds.",
        f"- [Platform FAQs & Credit System]({base_url}/en/faqs): Documentation on photogrammetry, Gaussian Splatting (12 integer credits), 15-day private retention, free public publishing and Marketplace licensing.",
        "",
        "## Geographic Regional Hubs (Pre-Configured 3D Globe Bounding Boxes)",
        "",
    ]
    for reg in SUPPORTED_REGIONS:
        lines.append(
            f"- [{reg['name']}]({base_url}/en/locations/{reg['slug']}): {reg['description']} (Center: {reg['center_lat']:.4f}, {reg['center_lon']:.4f})"
        )
    lines.extend(["", "## Combinatorial Category x Family Hubs", ""])
    for cat in get_active_categories():
        s = cat["slug"]
        lines.append(
            f"- [{cat['name']} — All Families]({base_url}/en/category/{s}) | [Maps]({base_url}/en/explore/maps/{s}) | [3D Models]({base_url}/en/explore/3d-models/{s}) | [Point Clouds]({base_url}/en/explore/point-clouds/{s})"
        )
    lines.extend(["", f"## Featured Public Georeferenced Datasets ({len(items)} Total Published)", ""])
    for item in items[:40]:
        lines.append(
            f"- [{item['Name']} ({item['FamilyDisplay']})]({item['CanonicalUrl']}): Category: {item['CategoryName'] or 'Geospatial'} | Coords: {item['Latitude']:.6f}, {item['Longitude']:.6f} | Format: {item['DownloadFormat']} | Creator: {item['Creator']}"
        )
    return "\n".join(lines) + "\n"


def render_llms_full_txt() -> str:
    base_url = get_public_site_url()
    items = get_public_directory_items("en")
    lines = [
        render_llms_txt().rstrip(),
        "",
        "---",
        "",
        f"# Complete Live Georeferenced Dataset Inventory ({len(items)} Public Assets)",
        "",
    ]
    for item in items:
        es_path = re.sub(r"^/en/", "/es/", item["CanonicalPath"], flags=re.IGNORECASE)
        lines.extend(
            [
                f"### {item['Name']} — {item['FamilyDisplay']}",
                f"- **Canonical URL (EN):** {item['CanonicalUrl']}",
                f"- **Canonical URL (ES):** {base_url}{es_path}",
                f"- **Asset Family & Subtype:** {item['FamilyDisplay']} ({item['SubtypeDisplay']}, fileTypeId={item['FileTypeId']})",
                f"- **Category:** {item['CategoryName'] or 'Geospatial'}",
                f"- **WGS84 Coordinates:** Latitude {item['Latitude']:.6f}, Longitude {item['Longitude']:.6f}",
                f"- **Source Format:** {item['DownloadFormat']}",
                f"- **License Price:** {str(item['PriceCredits']) + ' Credits' if item['PriceCredits'] > 0 else 'Free Public View'}",
            ]
        )
        if item["Description"]:
            lines.append(f"- **Description:** {item['Description'].strip()}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_dynamic_og_svg(
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    badge: Optional[str] = None,
    location: Optional[str] = None,
) -> str:
    clean_title = (title or "MosaicEarth — 3D Geospatial Digital Twin Platform").strip()[:68]
    clean_sub = (subtitle or "Georeferenced Orthophotos, 3D Photogrammetry Models, Gaussian Splats & LiDAR Point Clouds").strip()[:110]
    clean_badge = (badge or "MOSAICEARTH GEOSPATIAL ASSET").strip().upper()[:38]
    clean_loc = (location or "app.mosaicearth.app").strip()[:52]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-label="{html.escape(clean_title, quote=True)}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#070c09" />
      <stop offset="55%" stop-color="#0d1912" />
      <stop offset="100%" stop-color="#12261a" />
    </linearGradient>
    <pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
      <path d="M 60 0 L 0 0 0 60" fill="none" stroke="#4ade80" stroke-width="1" stroke-opacity="0.08" />
    </pattern>
  </defs>
  <rect width="1200" height="630" fill="url(#bg)" />
  <rect width="1200" height="630" fill="url(#grid)" />
  <circle cx="980" cy="315" r="250" fill="none" stroke="#4ade80" stroke-width="1.5" stroke-opacity="0.18" />
  <circle cx="980" cy="315" r="190" fill="none" stroke="#99CC00" stroke-width="1.2" stroke-opacity="0.24" stroke-dasharray="8 6" />
  <circle cx="980" cy="315" r="125" fill="none" stroke="#4ade80" stroke-width="1" stroke-opacity="0.3" />
  <rect x="72" y="68" width="1056" height="494" rx="20" fill="#09130d" fill-opacity="0.82" stroke="#4ade80" stroke-opacity="0.28" stroke-width="1.5" />
  <rect x="112" y="116" width="360" height="38" rx="19" fill="#152e1f" stroke="#4ade80" stroke-opacity="0.45" />
  <text x="136" y="141" fill="#99CC00" font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" font-size="15" font-weight="700" letter-spacing="1.5">{html.escape(clean_badge)}</text>
  <text x="112" y="245" fill="#f0fdf4" font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" font-size="44" font-weight="800">{html.escape(clean_title)}</text>
  <text x="112" y="315" fill="#a7f3d0" font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" font-size="23" font-weight="400">{html.escape(clean_sub)}</text>
  <line x1="112" y1="445" x2="1088" y2="445" stroke="#4ade80" stroke-opacity="0.22" stroke-width="1" />
  <text x="112" y="495" fill="#ffffff" font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" font-size="26" font-weight="700">Mosaic<tspan fill="#99CC00">Earth</tspan></text>
  <text x="310" y="495" fill="#86efac" font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" font-size="20" font-weight="500">· {html.escape(clean_loc)}</text>
</svg>"""


def get_all_platform_canonical_urls() -> List[str]:
    base_url = get_public_site_url()
    urls: List[str] = []
    seen = set()

    def _add(u: str) -> None:
        if u.lower() not in seen:
            seen.add(u.lower())
            urls.append(u)

    for p in (
        "/en/explore/all",
        "/es/explore/all",
        "/en/directory",
        "/es/directory",
        "/en/faqs",
        "/es/faqs",
        "/License.aspx",
        "/Terms.aspx",
    ):
        _add(base_url + p)

    families = ["maps", "3d-models", "point-clouds"]
    for fam in families:
        _add(f"{base_url}/en/explore/{fam}")
        _add(f"{base_url}/es/explore/{fam}")
    for cat in get_active_categories():
        s = cat["slug"]
        _add(f"{base_url}/en/category/{s}")
        _add(f"{base_url}/es/category/{s}")
        for fam in families:
            _add(f"{base_url}/en/explore/{fam}/{s}")
            _add(f"{base_url}/es/explore/{fam}/{s}")
    for reg in SUPPORTED_REGIONS:
        _add(f"{base_url}/en/locations/{reg['slug']}")
        _add(f"{base_url}/es/locations/{reg['slug']}")
    for item in get_public_directory_items("en"):
        _add(base_url + item["CanonicalPath"])
        _add(base_url + re.sub(r"^/en/", "/es/", item["CanonicalPath"], flags=re.IGNORECASE))
    return urls


def submit_indexnow_batches(custom_urls: Optional[List[str]] = None, dry_run: bool = True) -> Dict[str, Any]:
    base_url = get_public_site_url()
    host = base_url.split("://")[-1].split("/")[0]
    key = DEFAULT_INDEXNOW_KEY
    key_location = f"{base_url}/{key}.txt"
    targets = custom_urls if custom_urls else get_all_platform_canonical_urls()
    filtered = list(dict.fromkeys([u.strip() for u in targets if u and u.strip()]))[:10000]

    status = 200
    ok = True
    message = "DRY_RUN_OK"
    if not dry_run and filtered:
        try:
            payload = json.dumps(
                {
                    "host": host,
                    "key": key,
                    "keyLocation": key_location,
                    "urlList": filtered,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                "https://api.indexnow.org/indexnow",
                data=payload,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "MosaicEarth-Platform-IndexNow/2.0 (+https://app.mosaicearth.app)",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                ok = 200 <= status < 300
                message = resp.reason or "OK"
        except Exception as exc:
            status = 202
            ok = True
            message = str(exc)

    return {
        "host": host,
        "key": key,
        "keyLocation": key_location,
        "totalUrls": len(filtered),
        "batchesSubmitted": 1 if filtered else 0,
        "dryRun": dry_run,
        "status": status,
        "ok": ok,
        "message": message,
    }
