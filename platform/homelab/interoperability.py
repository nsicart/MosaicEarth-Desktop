import json
import math
import urllib.parse
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from .blob_store import LocalBlobStore, guess_mime_type
from .db_store import MosaicEarthDataStore, _slugify, _try_float

ALLOWED_INTEROP_ORIGINS = {
    "https://4dtwinmaps.com",
    "https://www.4dtwinmaps.com",
    "https://app.4dtwinmaps.com",
    "https://mosaicearth.app",
    "https://app.mosaicearth.app",
    "http://192.168.0.143:8090",
    "http://localhost:8090",
}


def resolve_cors_origin(request: Request) -> str:
    origin = (request.headers.get("origin") or "").strip()
    if origin in ALLOWED_INTEROP_ORIGINS or origin.endswith(".4dtwinmaps.com") or origin.endswith(".mosaicearth.app") or origin.endswith(".trycloudflare.com"):
        return origin
    return "https://4dtwinmaps.com"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def create_interoperability_router(db: MosaicEarthDataStore, blobs: LocalBlobStore) -> APIRouter:
    router = APIRouter()

    @router.options("/api/v1/interoperability/assets")
    @router.options("/api/v1/interoperability/assets/{asset_key}/stream")
    @router.options("/api/v1/interoperability/assets/{asset_key}/stream/{subpath:path}")
    async def interop_options(request: Request):
        origin = resolve_cors_origin(request)
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "Range, Authorization, Content-Type, Accept",
                "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges, ETag",
                "Access-Control-Max-Age": "86400",
            },
        )

    @router.get("/api/v1/interoperability/assets")
    async def list_interoperability_assets(
        request: Request,
        lat: Optional[float] = Query(None, description="Center latitude (WGS84 / EPSG:4326)"),
        lon: Optional[float] = Query(None, description="Center longitude (WGS84 / EPSG:4326)"),
        radius_km: float = Query(50.0, ge=0.1, le=20000.0, description="Search radius in kilometers when lat/lon provided"),
        bbox: Optional[str] = Query(None, description="Bounding box: west,south,east,north in EPSG:4326"),
        family: Optional[str] = Query(None, description="Filter by family: orthophoto, model3d, pointcloud"),
        q: Optional[str] = Query(None, description="Text search filter"),
        limit: int = Query(200, ge=1, le=1000),
    ):
        origin = resolve_cors_origin(request)
        north = south = east = west = None
        if bbox:
            parts = [ _try_float(x) for x in bbox.split(",") ]
            if len(parts) == 4 and all(p is not None for p in parts):
                west, south, east, north = parts[0], parts[1], parts[2], parts[3]
        elif lat is not None and lon is not None:
            dlat = radius_km / 111.32
            dlon = radius_km / max(1.0, 111.32 * math.cos(math.radians(lat)))
            north = min(90.0, lat + dlat)
            south = max(-90.0, lat - dlat)
            east = min(180.0, lon + dlon)
            west = max(-180.0, lon - dlon)

        families = [f.strip() for f in family.split(",") if f.strip()] if family else None
        raw_items = db.build_public_asset_index(
            north=north,
            south=south,
            east=east,
            west=west,
            families=families,
            limit=5000,
            search=q,
        )

        base_url = str(request.base_url).rstrip("/")
        features: List[Dict[str, Any]] = []
        assets_list: List[Dict[str, Any]] = []

        for item in raw_items:
            ilat = float(item.get("Latitude") or 0.0)
            ilon = float(item.get("Longitude") or 0.0)
            dist_km = None
            if lat is not None and lon is not None:
                dist_km = round(haversine_km(lat, lon, ilat, ilon), 3)
                if dist_km > radius_km:
                    continue

            key = str(item["Key"])
            viewer_kind = str(item.get("ViewerKind") or "")
            entry_subpath = (
                "tileset.json"
                if "tileset" in viewer_kind or "pointcloud" in viewer_kind
                else "orthophoto_tiles/{z}/{x}/{y}.png"
                if "raster" in viewer_kind
                else "model.glb"
            )
            stream_base = f"{base_url}/api/v1/interoperability/assets/{key}/stream"
            stream_entry = f"{stream_base}/{entry_subpath}"
            alt = float(item.get("Altitude") or item.get("Height") or 0.0)
            world_link_url = (
                f"{base_url}/en/fly?world_link=mosaicearth"
                f"&asset_id={urllib.parse.quote(key)}"
                f"&name={urllib.parse.quote(str(item.get('Name') or ''))}"
                f"&lat={ilat:.6f}&lon={ilon:.6f}&alt={alt:.2f}"
                f"&kind={urllib.parse.quote(viewer_kind)}"
                f"&stream={urllib.parse.quote(stream_entry)}"
            )

            asset_obj = {
                "id": key,
                "source": item.get("Source"),
                "project_id": item.get("ProjectId"),
                "publication_id": item.get("PublicationId"),
                "name": item.get("Name"),
                "creator": item.get("Creator"),
                "family": item.get("Family"),
                "subtype": item.get("Subtype"),
                "file_type_id": item.get("FileTypeId"),
                "viewer_kind": viewer_kind,
                "extensions": item.get("Extensions"),
                "download_format": item.get("DownloadFormat"),
                "price_credits": item.get("PriceCredits", 0),
                "coordinates": {
                    "crs": "EPSG:4326",
                    "latitude": ilat,
                    "longitude": ilon,
                    "altitude": alt,
                    "heading": float(item.get("Heading") or 0.0),
                    "scale": float(item.get("Scale") or 1.0),
                },
                "distance_km": dist_km,
                "temporal": {
                    "capture_start_utc": item.get("CaptureStartUtc"),
                    "capture_end_utc": item.get("CaptureEndUtc"),
                    "effective_start_utc": item.get("EffectiveStartUtc"),
                    "effective_end_utc": item.get("EffectiveEndUtc"),
                    "precision": item.get("CapturePrecision"),
                    "date_display": item.get("DateDisplay"),
                },
                "links": {
                    "stream_root": stream_base,
                    "stream_entry": stream_entry,
                    "viewer_url": f"{base_url}{item.get('ViewerUrl')}",
                    "detail_url": f"{base_url}{item.get('DetailUrl')}",
                    "world_link_4dtwinmaps": world_link_url,
                },
                "license": "MosaicEarth Standard License v1.0",
            }
            assets_list.append(asset_obj)
            features.append({
                "type": "Feature",
                "id": key,
                "geometry": {
                    "type": "Point",
                    "coordinates": [ilon, ilat, alt],
                },
                "properties": asset_obj,
            })
            if len(assets_list) >= limit:
                break

        if lat is not None and lon is not None:
            assets_list.sort(key=lambda a: (a.get("distance_km") if a.get("distance_km") is not None else 1e9))
            features.sort(key=lambda f: (f["properties"].get("distance_km") if f["properties"].get("distance_km") is not None else 1e9))

        return JSONResponse(
            content={
                "type": "FeatureCollection",
                "protocol": "MosaicEarth-4DTwinMaps-Interoperability-v1",
                "crs": "EPSG:4326",
                "count": len(assets_list),
                "assets": assets_list,
                "features": features,
            },
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Expose-Headers": "Content-Length, Content-Type",
                "Cache-Control": "public, max-age=60",
            },
        )

    @router.api_route("/api/v1/interoperability/assets/{asset_key}/stream", methods=["GET", "HEAD"])
    @router.api_route("/api/v1/interoperability/assets/{asset_key}/stream/{subpath:path}", methods=["GET", "HEAD"])
    async def stream_interoperability_asset(request: Request, asset_key: str, subpath: str = ""):
        origin = resolve_cors_origin(request)
        parts = (asset_key or "").strip().lower().split("-")
        if len(parts) != 3 or parts[0] not in ("processed", "external") or parts[1] not in ("1", "2", "3") or not parts[2].isdigit():
            raise HTTPException(status_code=400, detail="Invalid asset_key format. Expected processed-{1|2|3}-{id} or external-{1|2|3}-{id}.")

        source_kind, type_s, id_s = parts[0], int(parts[1]), int(parts[2])
        clean_sub = (subpath or "").strip().lstrip("/")

        if source_kind == "processed":
            proj = db.get_project(id_s)
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found.")
            pubs = db.get_publications_by_project(id_s)
            is_public = any(int(p.get("STATUS_ID") or 0) == 2 and int(p.get("FILE_TYPE_ID") or 0) == type_s for p in pubs)
            if not is_public:
                raise HTTPException(status_code=403, detail="This asset is not publicly published.")

            folder = _slugify(f"{id_s}-{proj.get('NAME') or ''}")
            if not clean_sub:
                candidates = (
                    ["procesado/odm_orthophoto/odm_orthophoto.webp", "procesado/geotiff/odm_orthophoto.tif", "orthophoto.tif"]
                    if type_s == 2
                    else ["procesado/3d_tiles/pointcloud/tileset.json", "pointcloud.zip"]
                    if type_s == 1
                    else ["procesado/output/tileset.json", "procesado/3d_tiles/model/tileset.json", "3d-model.zip"]
                )
                clean_sub = candidates[0]
                for cand in candidates:
                    if blobs.exists("imagenes", f"{folder}/{cand}"):
                        clean_sub = cand
                        break
            elif not clean_sub.startswith("procesado/"):
                base_prefix = (
                    "procesado/orthophoto_tiles/"
                    if type_s == 2
                    else "procesado/3d_tiles/pointcloud/"
                    if type_s == 1
                    else "procesado/output/"
                )
                clean_sub = base_prefix + clean_sub
                if type_s == 3 and not blobs.exists("imagenes", f"{folder}/{clean_sub}"):
                    alt_sub = "procesado/3d_tiles/model/" + subpath.strip().lstrip("/")
                    if blobs.exists("imagenes", f"{folder}/{alt_sub}"):
                        clean_sub = alt_sub

            return blobs.stream_file_response(
                request=request,
                container="imagenes",
                blob_name=f"{folder}/{clean_sub}",
                cache_control="public, max-age=3600",
                cors_origin=origin,
            )

        else:
            ext = db.get_external_asset_by_id(id_s)
            if not ext:
                raise HTTPException(status_code=404, detail="External asset not found.")
            if str(ext.get("VISIBILITY") or "").lower() != "public":
                raise HTTPException(status_code=403, detail="External asset is private.")

            pid = int(ext.get("PROJECT_ID") or 0)
            display_kind = str(ext.get("DISPLAY_KIND") or "").strip()
            display_entry = str(ext.get("DISPLAY_ENTRY_PATH") or "tileset.json").strip().lstrip("/")

            if display_kind == "model-glb" and not ext.get("DISPLAY_ENTRY_PATH") and (not clean_sub or clean_sub == "model.glb"):
                stored_name = str(ext.get("STORED_FILE_NAME") or ext.get("SOURCE_FILE_NAME") or "")
                return blobs.stream_file_response(
                    request=request,
                    container="assets",
                    blob_name=f"external/{pid}/source/{stored_name}",
                    cache_control="public, max-age=3600",
                    cors_origin=origin,
                )

            target_rel = clean_sub or display_entry
            blob_key = f"external/{pid}/{id_s}/{target_rel}"
            if blobs.exists("results", blob_key):
                return blobs.stream_file_response(
                    request=request,
                    container="results",
                    blob_name=blob_key,
                    cache_control="public, max-age=3600",
                    cors_origin=origin,
                )
            stored_name = str(ext.get("STORED_FILE_NAME") or ext.get("SOURCE_FILE_NAME") or "")
            return blobs.stream_file_response(
                request=request,
                container="assets",
                blob_name=f"external/{pid}/source/{stored_name}",
                cache_control="public, max-age=3600",
                cors_origin=origin,
            )

    return router


def build_4dtwinmaps_world_link(item: Dict[str, Any]) -> str:
    key = str(item.get("Key") or "")
    name = str(item.get("Name") or "MosaicAsset")
    lat = float(item.get("Latitude") or 0.0)
    lon = float(item.get("Longitude") or 0.0)
    alt = float(item.get("Altitude") or item.get("Height") or 0.0)
    ftype = int(item.get("FileTypeId") or 2)
    viewer_kind = str(item.get("ViewerKind") or ("processed-raster" if ftype == 2 else "processed-tileset"))
    stream_entry = f"/api/v1/interoperability/assets/{key}/stream"
    return (
        f"/en/fly?world_link=mosaicearth"
        f"&asset_id={urllib.parse.quote(key)}"
        f"&name={urllib.parse.quote(name)}"
        f"&lat={lat:.6f}&lon={lon:.6f}&alt={alt:.2f}"
        f"&kind={urllib.parse.quote(viewer_kind)}"
        f"&stream={urllib.parse.quote(stream_entry)}"
    )


from .blob_store import get_blob_store as _get_blob_store
from .db_store import get_store as _get_store

router = create_interoperability_router(_get_store(), _get_blob_store())

