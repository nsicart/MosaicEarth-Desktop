"""User-initiated place searches, shared cache and bounded public-provider use."""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter()


def _reserve(query):
    root = Path(os.environ.get('MOSAICEARTH_DATA_DIR', '/data')) / 'map_search'
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(query.casefold().encode()).hexdigest()
    with sqlite3.connect(root / 'cache.sqlite3', timeout=3) as db:
        db.execute('CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, expires REAL, payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS throttle (id INTEGER PRIMARY KEY, requested REAL)')
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        row = db.execute('SELECT payload FROM results WHERE key=? AND expires>?', (key, now)).fetchone()
        if row:
            return key, json.loads(row[0])
        last = db.execute('SELECT requested FROM throttle WHERE id=1').fetchone()
        if last and now - last[0] < 1.05:
            raise HTTPException(429, 'Please wait a moment before searching again.', headers={'Retry-After': '2'})
        db.execute('INSERT OR REPLACE INTO throttle VALUES (1, ?)', (now,))
        db.execute('DELETE FROM results WHERE expires<?', (now,))
        db.execute('DELETE FROM results WHERE key IN (SELECT key FROM results ORDER BY expires DESC LIMIT -1 OFFSET 10000)')
    return key, None


def _save(key, result):
    root = Path(os.environ.get('MOSAICEARTH_DATA_DIR', '/data')) / 'map_search'
    with sqlite3.connect(root / 'cache.sqlite3', timeout=3) as db:
        db.execute('INSERT OR REPLACE INTO results VALUES (?, ?, ?)', (key, time.time() + 7*86400, json.dumps(result)))


@router.get('/api/maps/geocode')
def geocode(q: str = Query(min_length=2, max_length=160)):
    query = ' '.join(q.split())
    if len(query) < 2 or any(ord(c) < 32 for c in query):
        raise HTTPException(400, 'Enter a place name.')
    # Every official Desktop forwards this explicit search through the same
    # cache/throttle. No account, private data or credentials are forwarded.
    desktop = os.environ.get('MOSAICEARTH_DESKTOP_MODE') == '1'
    if desktop:
        endpoint = os.environ.get('MOSAICEARTH_CLOUD_ORIGIN', 'https://app.mosaicearth.app').rstrip('/') + '/api/maps/geocode'
        params = {'q': query}
    else:
        key, cached = _reserve(query)
        if cached is not None:
            return JSONResponse(cached, headers={'Cache-Control': 'private, max-age=86400'})
        endpoint = os.environ.get('MOSAICEARTH_PHOTON_URL', 'https://photon.komoot.io/api/')
        params = {'q': query, 'limit': 5}
    try:
        with httpx.Client(timeout=10, headers={'User-Agent': 'MosaicEarth/1.0 (https://mosaicearth.app)'}) as client:
            response = client.get(endpoint, params=params)
            if response.status_code == 429:
                raise HTTPException(429, 'Please wait a moment before searching again.', headers={'Retry-After': '2'})
            response.raise_for_status()
            data = response.json()
        features = []
        for item in data.get('features', [])[:5]:
            coordinates = item.get('geometry', {}).get('coordinates', [])
            if len(coordinates) < 2 or not all(isinstance(v, (int, float)) for v in coordinates[:2]):
                continue
            if not (-180 <= coordinates[0] <= 180 and -90 <= coordinates[1] <= 90):
                continue
            properties = item.get('properties', {})
            features.append({'geometry': {'type': 'Point', 'coordinates': coordinates[:2]},
                             'properties': {k: str(properties[k])[:250] for k in ('name', 'city', 'state', 'country') if properties.get(k)}})
        result = {'type': 'FeatureCollection', 'features': features}
        if not desktop:
            _save(key, result)
        return JSONResponse(result, headers={'Cache-Control': 'private, max-age=86400'})
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise HTTPException(503, 'Place search is temporarily unavailable. You can still enter longitude, latitude.') from None
