"""Account/device grants for the commercial installer and provisioning service."""
import base64
import hashlib
import json
import re
import sqlite3
import secrets
import subprocess
import time
from fastapi import APIRouter, HTTPException, Request
from .connector_sync import context
from .db_store import get_store
from .connector_sync import stream_download
from types import SimpleNamespace
from urllib.parse import urlsplit


def account_context(request):
    get_store().refresh_account_state()
    if request.headers.get("Authorization"):
        return context(request)
    from .app import get_current_user
    user = get_current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to your MosaicEarth account")
    if request.method != "GET" and urlsplit(request.headers.get("Origin", "")).netloc != request.headers.get("Host"):
        raise HTTPException(403, "Use the account page to manage devices")
    return SimpleNamespace(store=get_store(), uid=int(user["ID"]))

router = APIRouter(prefix="/api/v2/desktop", tags=["Desktop activation"])


async def activation_body(request):
    raw = bytearray()
    async for block in request.stream():
        raw.extend(block)
        if len(raw) > 16384:
            raise HTTPException(413, "Activation request too large")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError
        return body
    except ValueError:
        raise HTTPException(400, "Invalid activation request") from None


@router.post("/activate")
async def activate(request: Request):
    sync = context(request)
    body = await activation_body(request)
    device = str(body.get("device") or "")
    if not re.fullmatch("[0-9a-f]{64}", device):
        raise HTTPException(400, "Invalid device identity")
    info = sync.store.get_desktop_license_info(sync.uid)
    if not info.get("licensed"):
        raise HTTPException(403, "This account has no Desktop purchase or active Studio plan")
    key = sync.store.data_root / "runtime" / "desktop-license-private.pem"
    if not key.is_file():
        raise HTTPException(503, "Desktop activation is temporarily unavailable")
    now = int(time.time())
    with sqlite3.connect(sync.store.sqlite_path, timeout=30) as db:
        db.execute("CREATE TABLE IF NOT EXISTS desktop_activations(user_id INTEGER, device TEXT, name TEXT, updated_at INTEGER, revoked INTEGER DEFAULT 0, PRIMARY KEY(user_id,device))")
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT revoked FROM desktop_activations WHERE user_id=? AND device=?", (sync.uid, device)).fetchone()
        count = db.execute("SELECT COUNT(*) FROM desktop_activations WHERE user_id=? AND revoked=0", (sync.uid,)).fetchone()[0]
        if (not current or current[0]) and count >= 2:
            raise HTTPException(409, "Two devices are already activated. Remove an old device from your Desktop account page first.")
        db.execute("INSERT OR REPLACE INTO desktop_activations VALUES(?,?,?,?,0)",
                   (sync.uid, device, str(body.get("name") or "Desktop")[:100], now))
    payload = {"schema": 1, "edition": "commercial", "user_id": sync.uid, "email": info["email"],
               "device": device, "issued_at": now, "expires_at": now + 30 * 86400}
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key)], input=encoded,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=10).stdout
    return {"ok": True, "grant": {"payload": base64.b64encode(encoded).decode(), "signature": base64.b64encode(signed).decode()}}


@router.get("/activations")
def activations(request: Request):
    sync = account_context(request)
    with sqlite3.connect(sync.store.sqlite_path, timeout=30) as db:
        table = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='desktop_activations'").fetchone()
        rows = db.execute("SELECT device,name,updated_at FROM desktop_activations WHERE user_id=? AND revoked=0", (sync.uid,)).fetchall() if table else []
    return {"ok": True, "devices": [{"device": r[0], "name": r[1], "last_activation": r[2]} for r in rows]}


@router.delete("/activations/{device}")
def revoke(device: str, request: Request):
    sync = account_context(request)
    with sqlite3.connect(sync.store.sqlite_path, timeout=30) as db:
        try:
            db.execute("UPDATE desktop_activations SET revoked=1 WHERE user_id=? AND device=?", (sync.uid, device))
        except sqlite3.OperationalError:
            pass
    return {"ok": True, "message": "Device removed. Its existing offline grant expires within 30 days."}


@router.post("/provision-session")
async def provision_session(request: Request):
    store = get_store()
    store.refresh_account_state()
    body = await activation_body(request)
    grant = body.get("grant", {})
    try:
        payload = base64.b64decode(grant["payload"], validate=True)
        signature = base64.b64decode(grant["signature"], validate=True)
        key = store.data_root / "runtime" / "desktop-license-private.pem"
        modulus = subprocess.check_output(["openssl", "rsa", "-in", str(key), "-noout", "-modulus"], stderr=subprocess.DEVNULL, timeout=10).decode().strip().split("=")[1]
        n, e = int(modulus, 16), 65537
        length = (n.bit_length() + 7) // 8
        if len(payload) > 8192 or len(signature) != length or int.from_bytes(signature, "big") >= n:
            raise ValueError
        digest = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(payload).digest()
        expected = b"\x00\x01" + b"\xff" * (length - len(digest) - 3) + b"\x00" + digest
        if pow(int.from_bytes(signature, "big"), e, n).to_bytes(length, "big") != expected:
            raise ValueError
        data = json.loads(payload)
        if data["edition"] != "commercial" or not data["issued_at"] - 300 <= time.time() < data["expires_at"]:
            raise ValueError
        uid, device = int(data["user_id"]), data["device"]
        if not store.get_desktop_license_info(uid).get("licensed"):
            raise ValueError
        with sqlite3.connect(store.sqlite_path, timeout=30) as db:
            row = db.execute("SELECT revoked FROM desktop_activations WHERE user_id=? AND device=?", (uid, device)).fetchone()
            if not row or row[0]:
                raise ValueError
            token = secrets.token_urlsafe(32)
            db.execute("CREATE TABLE IF NOT EXISTS desktop_provision_tokens(hash TEXT PRIMARY KEY,user_id INTEGER,device TEXT,expires_at INTEGER)")
            db.execute("DELETE FROM desktop_provision_tokens WHERE expires_at < ?", (int(time.time()),))
            db.execute("INSERT INTO desktop_provision_tokens VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), uid, device, int(time.time()) + 3600))
        return {"ok": True, "token": token}
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, sqlite3.Error):
        raise HTTPException(403, "Activate this device with an eligible account before installing engines") from None


@router.get("/engine-bundle")
def engine_bundle(request: Request):
    store = get_store()
    store.refresh_account_state()
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise HTTPException(401, "A provisioning session is required")
    with sqlite3.connect(store.sqlite_path, timeout=30) as db:
        try:
            row = db.execute("SELECT t.user_id FROM desktop_provision_tokens t JOIN desktop_activations a ON a.user_id=t.user_id AND a.device=t.device WHERE t.hash=? AND t.expires_at>? AND a.revoked=0", (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        except sqlite3.Error:
            row = None
    if not row or not store.get_desktop_license_info(row[0]).get("licensed"):
        raise HTTPException(403, "Provisioning session expired or device revoked")
    directory = store.data_root / "desktop_distribution"
    try:
        metadata = json.loads((directory / "engines.json").read_text())
        path = directory / metadata["filename"]
        if not path.is_file() or path.stat().st_size != metadata["bytes"]:
            raise ValueError
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, "Engine download is temporarily unavailable") from None
    return stream_download(request, path, path.name, metadata["sha256"])
