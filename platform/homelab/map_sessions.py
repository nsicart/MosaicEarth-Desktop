"""Issue individual Esri basemap sessions without distributing app credentials.

The SQLite ledger reserves budget BEFORE a chargeable request. Unknown outcomes
are never refunded: a timeout or process crash may still have created a session.
Imagery goes directly to Esri; this service does not cache or redistribute tiles.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()
SESSION_PATH = "/api/v2/maps/session"
TOKEN_URL = "https://www.arcgis.com/sharing/rest/oauth2/token"
START_URL = "https://basemapstyles-api.arcgis.com/arcgis/rest/services/styles/v2/sessions/start"
NONCE = re.compile(r"^[a-f0-9]{64}$")


class SessionUnavailable(Exception):
    def __init__(self, code="unavailable", status=503, retry=60):
        self.code, self.status, self.retry = code, status, retry
        super().__init__(code)


def billing_period(now, day):
    """UTC billing anniversary, restricted to 1..28 to avoid short months."""
    dt = datetime.fromtimestamp(now, timezone.utc)
    year, month = dt.year, dt.month
    if dt.day < day:
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return f"{year:04}-{month:02}-{day:02}"


class SessionBroker:
    def __init__(self, path, client_id, secret, *, maximum=1000, billing_day=1,
                 per_hour=100, per_minute=30, transport=None, clock=time.time):
        if not 1 <= billing_day <= 28 or maximum < 0:
            raise ValueError("Invalid basemap billing configuration")
        self.path = Path(path)
        self.client_id, self.secret = client_id, secret
        self.maximum, self.billing_day = maximum, billing_day
        self.per_hour, self.per_minute = per_hour, per_minute
        self.transport, self.clock = transport, clock
        self.pending = {}
        self.token, self.token_until = None, 0
        self.auth_lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(4)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Set permissions before SQLite can write credentials, including after a restart.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS usage (period TEXT PRIMARY KEY, reserved INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    client TEXT PRIMARY KEY, token TEXT, expires REAL NOT NULL,
                    attempted REAL NOT NULL, peer TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (created REAL NOT NULL, peer TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_time ON attempts(created);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def peer_hash(self, peer):
        # No raw IP addresses in the ledger. Rotates daily; retained for rate limits only.
        value = str(int(self.clock() // 86400)) + ":" + peer
        return hmac.new(self.secret.encode(), value.encode(), hashlib.sha256).hexdigest()

    def reserve(self, client, peer):
        now = self.clock()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE client=?", (client,)).fetchone()
            if row and row["token"] and row["expires"] > now + 60:
                return {"sessionToken": row["token"], "expiresAt": int(row["expires"] * 1000)}
            if row and now - row["attempted"] < 60:
                raise SessionUnavailable("retry", retry=60)
            db.execute("DELETE FROM attempts WHERE created < ?", (now - 3600,))
            db.execute("DELETE FROM sessions WHERE expires < ? AND attempted < ?", (now - 86400, now - 86400))
            count = db.execute("SELECT COUNT(*) FROM attempts WHERE peer=?", (peer,)).fetchone()[0]
            minute = db.execute("SELECT COUNT(*) FROM attempts WHERE created>?", (now - 60,)).fetchone()[0]
            if count >= self.per_hour or minute >= self.per_minute:
                raise SessionUnavailable("rate", 429, 60)
            # Also bound calendar months. This remains conservative if a provider
            # accounts its free allowance on a different anniversary.
            periods = {billing_period(now, self.billing_day), billing_period(now, 1)}
            for period in periods:
                row = db.execute("SELECT reserved FROM usage WHERE period=?", (period,)).fetchone()
                if row and row[0] >= self.maximum:
                    raise SessionUnavailable("budget", 429, 3600)
            for period in periods:
                db.execute("INSERT INTO usage VALUES (?,1) ON CONFLICT(period) DO UPDATE SET reserved=reserved+1", (period,))
            db.execute("INSERT INTO attempts VALUES (?,?)", (now, peer))
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?,NULL,0,?,?, 'pending')", (client, now, peer))
        return None

    async def json_request(self, http, method, url, **kwargs):
        # Neither upstream exceptions nor response bodies (which may echo tokens)
        # enter public responses or application logs.
        try:
            async with http.stream(method, url, **kwargs) as response:
                if response.status_code != 200:
                    raise SessionUnavailable()
                body = bytearray()
                async for block in response.aiter_bytes():
                    body.extend(block)
                    if len(body) > 65536:
                        raise SessionUnavailable()
                data = json.loads(body)
                if not isinstance(data, dict) or data.get("error"):
                    raise SessionUnavailable()
                return data
        except (httpx.HTTPError, ValueError, TypeError):
            raise SessionUnavailable() from None

    async def access_token(self, http):
        async with self.auth_lock:
            if self.token and self.token_until > self.clock() + 60:
                return self.token
            data = await self.json_request(http, "POST", TOKEN_URL, data={
                "f": "json", "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.secret, "expiration": "60"})
            token = data.get("access_token")
            if not isinstance(token, str) or not token or len(token) > 16000:
                raise SessionUnavailable()
            self.token, self.token_until = token, self.clock() + min(float(data.get("expires_in", 0)), 3600)
            return token

    async def issue(self, client, peer):
        async with self.slots:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20), transport=self.transport, follow_redirects=False) as http:
                # Authentication itself does not issue a chargeable basemap session.
                # Do it first so a credential outage cannot drain reservations.
                token = await self.access_token(http)
                cached = await asyncio.to_thread(self.reserve, client, peer)
                if cached:
                    return cached
                data = await self.json_request(http, "GET", START_URL,
                    params={"f": "json", "styleFamily": "arcgis", "durationSeconds": 43200},
                    headers={"Authorization": "Bearer " + token})
                session = data.get("sessionToken")
                try:
                    expires = min(float(data["endTime"]) / 1000, self.clock() + 43200)
                except (KeyError, TypeError, ValueError):
                    raise SessionUnavailable() from None
                if not isinstance(session, str) or not session or len(session) > 16000 or not math.isfinite(expires) or expires <= self.clock() + 60:
                    raise SessionUnavailable()
                def save():
                    with self.db() as db:
                        db.execute("UPDATE sessions SET token=?, expires=?, state='ready' WHERE client=?", (session, expires, client))
                await asyncio.to_thread(save)
                return {"sessionToken": session, "expiresAt": int(expires * 1000)}

    async def session(self, nonce, peer):
        if not self.client_id or not self.secret:
            raise SessionUnavailable("unconfigured", retry=3600)
        client = hashlib.sha256(nonce.encode()).hexdigest()
        # Reuse an existing session even if authentication is temporarily down or
        # this month's quota has been exhausted.
        with self.db() as db:
            row = db.execute("SELECT token,expires FROM sessions WHERE client=?", (client,)).fetchone()
        if row and row["token"] and row["expires"] > self.clock() + 60:
            return {"sessionToken": row["token"], "expiresAt": int(row["expires"] * 1000)}
        if client not in self.pending:
            if len(self.pending) >= 64:
                raise SessionUnavailable("rate", 429)
            task = asyncio.create_task(self.issue(client, self.peer_hash(peer)))
            self.pending[client] = task
            def complete(done):
                self.pending.pop(client, None)
                # Consume exceptions if the HTTP caller disconnected.
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(complete)
        return await asyncio.shield(self.pending[client])


_broker = None


def get_broker():
    global _broker
    if _broker is None:
        path = Path(os.environ.get("MOSAICEARTH_DATA_DIR", "/data")) / "runtime" / "maps" / "sessions.sqlite3"
        _broker = SessionBroker(path, os.environ.get("MOSAICEARTH_ESRI_CLIENT_ID", ""),
            os.environ.get("MOSAICEARTH_ESRI_CLIENT_SECRET", ""),
            maximum=int(os.environ.get("MOSAICEARTH_ESRI_MAX_SESSIONS", "1000")),
            billing_day=int(os.environ.get("MOSAICEARTH_ESRI_BILLING_DAY", "1")))
    return _broker


def allowed_origin(origin):
    if not origin or len(origin) > 300:
        return False
    try:
        url = urlsplit(origin)
        if url.username or url.password or url.path or url.query or url.fragment:
            return False
        if url.scheme in {"http", "https"} and url.hostname in {"localhost", "127.0.0.1", "::1"}:
            return bool(url.port is None or 1 <= url.port <= 65535)
        allowed = {"https://app.mosaicearth.app"}
        allowed.update(filter(None, os.environ.get("MOSAICEARTH_MAP_ALLOWED_ORIGINS", "").split(",")))
        return origin in allowed
    except ValueError:
        return False


def response_headers(origin):
    return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.options(SESSION_PATH)
async def map_session_options(request: Request):
    origin = request.headers.get("origin", "")
    if not allowed_origin(origin):
        return Response(status_code=403)
    return Response(status_code=204, headers={**response_headers(origin),
        "Access-Control-Allow-Methods": "POST", "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600"})


@router.post(SESSION_PATH)
async def map_session(request: Request):
    origin = request.headers.get("origin", "")
    if not allowed_origin(origin):
        return JSONResponse({"error": "origin"}, status_code=403, headers={"Cache-Control": "no-store"})
    headers = response_headers(origin)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        return JSONResponse({"error": "format"}, status_code=415, headers=headers)
    body = bytearray()
    async for block in request.stream():
        body.extend(block)
        if len(body) > 1024:
            return JSONResponse({"error": "size"}, status_code=413, headers=headers)
    try:
        data = json.loads(body)
        nonce = data.get("client") if isinstance(data, dict) else None
        if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
            raise ValueError()
    except (ValueError, TypeError):
        return JSONResponse({"error": "client"}, status_code=400, headers=headers)
    # Never trust caller-supplied forwarding headers for rate-limit identity.
    peer = request.client.host if request.client else "unknown"
    try:
        result = await get_broker().session(nonce, peer)
        return JSONResponse(result, headers=headers)
    except SessionUnavailable as error:
        return JSONResponse({"error": error.code, "retryAfter": error.retry}, status_code=error.status,
                            headers={**headers, "Retry-After": str(error.retry)})
    except (OSError, sqlite3.Error, ValueError):
        # A full/read-only ledger fails closed; it must never bypass the budget.
        return JSONResponse({"error": "unavailable", "retryAfter": 60}, status_code=503, headers=headers)


def public_map_config():
    """Only public routing preferences, never account credentials, enter JS."""
    return "window.MosaicMapConfig=" + json.dumps({
        "sessionUrl": os.environ.get("MOSAICEARTH_MAP_SESSION_URL", "https://app.mosaicearth.app" + SESSION_PATH),
        "openOnly": os.environ.get("MOSAICEARTH_MAP_OPEN_ONLY", "0") == "1",
    }).replace("<", "\\u003c") + ";\n"
