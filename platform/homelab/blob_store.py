import base64
import concurrent.futures
import datetime
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import weakref
import httpx
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
try:
    from fastapi import Request, Response
    from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
except ImportError:
    Request = Response = FileResponse = RedirectResponse = StreamingResponse = Any

CUSTOM_MIMES = {
    ".pnts": "application/octet-stream",
    ".b3dm": "application/octet-stream",
    ".i3dm": "application/octet-stream",
    ".cmpt": "application/octet-stream",
    ".subtree": "application/octet-stream",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".bin": "application/octet-stream",
    ".ktx2": "image/ktx2",
    ".laz": "application/octet-stream",
    ".las": "application/octet-stream",
    ".splat": "application/octet-stream",
    ".ply": "application/octet-stream",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".geojson": "application/geo+json",
    ".json": "application/json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
}


def guess_mime_type(rel_path: str) -> str:
    ext = os.path.splitext(rel_path or "")[1].lower()
    if ext in CUSTOM_MIMES:
        return CUSTOM_MIMES[ext]
    guessed, _ = mimetypes.guess_type(rel_path or "")
    return guessed or "application/octet-stream"


class LocalBlobStore:
    """External Cloud Object Storage (Azure Blob Storage mosaicearthstg260809 + S3-compatible R2/B2)
    with full HTTP Range streaming, zero-local-disk cloud mode, and Desktop local mode."""

    CONTAINERS = ("imagenes", "assets", "results", "tasks")

    def __init__(self, data_root: str = "/data"):
        self.data_root = Path(data_root)
        self.blobs_root = self.data_root / "blobs"
        self.legacy_blobs_root = Path(os.environ.get("MOSAICEARTH_LEGACY_BLOBS_DIR", "/data_legacy/blobs"))
        for c in self.CONTAINERS:
            (self.blobs_root / c).mkdir(parents=True, exist_ok=True)
        self._sas_token: Optional[str] = None
        self._missing_remote: Dict[str, float] = {}
        self._present_remote: Dict[str, int] = {}
        self._upload_client = None
        self._upload_client_lock = threading.Lock()
        self.s3_config_file = self.data_root / "s3_cloud_storage.json"
        self.s3_cfg: Dict[str, Any] = self._load_s3_config()
        self._sas_token = self._ensure_azure_sas_token()

    def _load_s3_config(self) -> Dict[str, Any]:
        is_desktop = os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1"
        cfg = {
            "provider": os.environ.get("MOSAICEARTH_S3_PROVIDER", "cloudflare_r2"),
            "storage_mode": os.environ.get("MOSAICEARTH_STORAGE_MODE", "local_only" if is_desktop else "s3_only"),
            "endpoint_url": os.environ.get("MOSAICEARTH_S3_ENDPOINT", "").strip().rstrip("/"),
            "bucket": os.environ.get("MOSAICEARTH_S3_BUCKET", "mosaicearth").strip(),
            "region": os.environ.get("MOSAICEARTH_S3_REGION", "auto").strip() or "auto",
            "access_key": os.environ.get("MOSAICEARTH_S3_ACCESS_KEY", "").strip(),
            "secret_key": os.environ.get("MOSAICEARTH_S3_SECRET_KEY", "").strip(),
            "public_cdn_url": os.environ.get("MOSAICEARTH_S3_PUBLIC_CDN_URL", "").strip().rstrip("/"),
            "zero_local_disk": False if is_desktop else (os.environ.get("MOSAICEARTH_ZERO_LOCAL_DISK", "1").strip().lower() in ("1", "true", "yes")),
            "presigned_redirect": os.environ.get("MOSAICEARTH_S3_REDIRECT", "0").strip().lower() in ("1", "true", "yes"),
        }
        if not is_desktop and self.s3_config_file.is_file():
            try:
                saved = json.loads(self.s3_config_file.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    for k, v in saved.items():
                        if v not in (None, ""):
                            cfg[k] = v
            except Exception:
                pass
        return cfg

    def _ensure_azure_sas_token(self) -> Optional[str]:
        if str(self.s3_cfg.get("provider") or "").lower() == "azure_blob":
            acc_name = str(self.s3_cfg.get("access_key") or "").strip()
            acc_key_b64 = str(self.s3_cfg.get("secret_key") or "").strip()
            if acc_name and acc_key_b64:
                try:
                    acc_key = base64.b64decode(acc_key_b64)
                    sv = "2020-08-04"
                    ss = "b"
                    srt = "sco"
                    sp = "rwdlacupx"
                    se = "2030-12-31T23:59:59Z"
                    st = "2024-01-01T00:00:00Z"
                    spr = "https"
                    sts = f"{acc_name}\n{sp}\n{ss}\n{srt}\n{st}\n{se}\n\n{spr}\n{sv}\n"
                    sig = base64.b64encode(hmac.new(acc_key, sts.encode("utf-8"), hashlib.sha256).digest()).decode()
                    sas = urllib.parse.urlencode({"sv": sv, "ss": ss, "srt": srt, "sp": sp, "se": se, "st": st, "spr": spr, "sig": sig})
                    sas_file = self.data_root / "azure_backup" / ".sas_token"
                    if sas_file.parent.is_dir():
                        try:
                            sas_file.write_text(sas, encoding="utf-8")
                        except Exception:
                            pass
                    return sas
                except Exception:
                    pass
        sas_file = self.data_root / "azure_backup" / ".sas_token"
        if sas_file.exists():
            try:
                return sas_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return None

    def update_s3_config(self, new_cfg: Dict[str, Any]) -> Dict[str, Any]:
        for k in ("provider", "storage_mode", "endpoint_url", "bucket", "region", "access_key", "secret_key", "public_cdn_url", "zero_local_disk", "presigned_redirect"):
            if k in new_cfg and new_cfg[k] is not None:
                if k == "secret_key" and str(new_cfg[k]).startswith("***"):
                    continue
                self.s3_cfg[k] = new_cfg[k]
        try:
            self.s3_config_file.write_text(json.dumps(self.s3_cfg, indent=2), encoding="utf-8")
        except Exception:
            pass
        self._sas_token = self._ensure_azure_sas_token()
        self._missing_remote.clear()
        self._present_remote.clear()
        return self.get_storage_status()

    def is_s3_active(self) -> bool:
        if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
            return False
        return bool(
            self.s3_cfg.get("endpoint_url")
            and self.s3_cfg.get("bucket")
            and self.s3_cfg.get("access_key")
            and self.s3_cfg.get("secret_key")
            and self.s3_cfg.get("storage_mode") in ("s3_only", "hybrid_cloud", "cloud_only")
        )

    def is_zero_local_disk(self) -> bool:
        if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1":
            return False
        return self.is_s3_active() and bool(self.s3_cfg.get("zero_local_disk"))

    def get_storage_status(self) -> Dict[str, Any]:
        ep = str(self.s3_cfg.get("endpoint_url") or "")
        ak = str(self.s3_cfg.get("access_key") or "")
        return {
            "provider": self.s3_cfg.get("provider", "azure_blob"),
            "storage_mode": self.s3_cfg.get("storage_mode", "s3_only"),
            "s3_active": self.is_s3_active(),
            "endpoint_url": ep,
            "bucket": self.s3_cfg.get("bucket", "imagenes,assets,results,tasks"),
            "region": self.s3_cfg.get("region", "westeurope"),
            "access_key_masked": (ak[:4] + "..." + ak[-4:]) if len(ak) > 8 else ("Configured" if ak else "Not configured"),
            "has_secret_key": bool(self.s3_cfg.get("secret_key")),
            "public_cdn_url": self.s3_cfg.get("public_cdn_url", ""),
            "zero_local_disk": self.is_zero_local_disk(),
            "presigned_redirect": bool(self.s3_cfg.get("presigned_redirect", False)),
        }

    def generate_s3_presigned_url(
        self,
        container: str,
        blob_name: str,
        method: str = "GET",
        expires_in: int = 3600,
        disposition: Optional[str] = None,
        list_prefix: Optional[str] = None,
        continuation_token: str = "",
    ) -> Optional[str]:
        """Generates a signed URL for Azure Blob Storage (SAS) or S3-compatible storage (AWS SigV4)."""
        if not self.is_s3_active():
            return None
        clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
        provider = str(self.s3_cfg.get("provider") or "").lower()
        if provider == "azure_blob":
            sas = self._sas_token or self._ensure_azure_sas_token()
            if not sas:
                return None
            endpoint = str(self.s3_cfg.get("endpoint_url") or "https://mosaicearthstg260809.blob.core.windows.net").rstrip("/")
            encoded_blob = "/".join(urllib.parse.quote(seg, safe="") for seg in clean_rel.split("/"))
            return f"{endpoint}/{container}/{encoded_blob}?{sas}"

        endpoint = str(self.s3_cfg["endpoint_url"]).rstrip("/")
        bucket = str(self.s3_cfg["bucket"]).strip("/")
        region = str(self.s3_cfg.get("region") or "auto").strip()
        access_key = str(self.s3_cfg["access_key"]).strip()
        secret_key = str(self.s3_cfg["secret_key"]).strip()
        object_key = f"{container}/{clean_rel}"

        parsed_ep = urllib.parse.urlparse(endpoint)
        host = parsed_ep.netloc
        scheme = parsed_ep.scheme or "https"
        encoded_key = "/".join(urllib.parse.quote(seg, safe="-_.~") for seg in object_key.split("/"))
        canonical_uri = f"/{bucket}/{encoded_key}"
        if list_prefix is not None:
            canonical_uri = f"/{bucket}"

        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        credential_scope = f"{datestamp}/{region}/s3/aws4_request"

        query_params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{access_key}/{credential_scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires_in),
            "X-Amz-SignedHeaders": "host",
        }
        if disposition:
            query_params["response-content-disposition"] = disposition
        if list_prefix is not None:
            query_params.update({"list-type": "2", "prefix": f"{container}/{list_prefix}", "max-keys": "1000"})
            if continuation_token:
                query_params["continuation-token"] = continuation_token

        canonical_querystring = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='-_.~')}"
            for k, v in sorted(query_params.items())
        )
        canonical_headers = f"host:{host}\n"
        canonical_request = f"{method.upper()}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\nhost\nUNSIGNED-PAYLOAD"

        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
            + hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        )

        def _sign(key_bytes: bytes, msg: str) -> bytes:
            return hmac.new(key_bytes, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = _sign(("AWS4" + secret_key).encode("utf-8"), datestamp)
        k_region = _sign(k_date, region)
        k_service = _sign(k_region, "s3")
        k_signing = _sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        return f"{scheme}://{host}{canonical_uri}?{canonical_querystring}&X-Amz-Signature={signature}"

    def _upload_http_client(self):
        # Keep TLS connections across a tile pyramid. A dead TCP connection must
        # not consume the long timeout reserved for transferring a large master.
        with self._upload_client_lock:
            if self._upload_client is None:
                self._upload_client = httpx.Client(
                    timeout=httpx.Timeout(180, connect=10, pool=30),
                    limits=httpx.Limits(max_connections=12, max_keepalive_connections=12),
                )
                weakref.finalize(self, self._upload_client.close)
            return self._upload_client

    def _upload_bytes_to_s3(self, container: str, blob_name: str, data: bytes) -> bool:
        if not self.is_s3_active():
            return False
        put_url = self.generate_s3_presigned_url(container, blob_name, method="PUT", expires_in=900)
        if not put_url:
            return False
        mime = guess_mime_type(blob_name)
        headers = {"Content-Type": mime, "Content-Length": str(len(data)), "User-Agent": "MosaicEarth/2.0"}
        if str(self.s3_cfg.get("provider") or "").lower() == "azure_blob":
            headers["x-ms-blob-type"] = "BlockBlob"
        for _attempt in range(3):
            try:
                resp = self._upload_http_client().put(put_url, content=data, headers=headers)
                if resp.status_code in (200, 201, 204):
                    clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
                    cache_key = f"{container}:{clean_rel}"
                    self._missing_remote.pop(cache_key, None)
                    self._present_remote[cache_key] = len(data)
                    return True
            except Exception:
                put_url = self.generate_s3_presigned_url(container, blob_name, method="PUT", expires_in=900) or put_url
        return False

    def _upload_file_to_s3(self, container: str, blob_name: str, source_file: Path) -> bool:
        if not self.is_s3_active() or not source_file.is_file():
            return False
        fsize = source_file.stat().st_size
        if fsize <= 64 * 1024 * 1024:
            return self._upload_bytes_to_s3(container, blob_name, source_file.read_bytes())
        provider = str(self.s3_cfg.get("provider") or "").lower()
        if provider != "azure_blob":
            mime = guess_mime_type(blob_name)
            clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
            for _attempt in range(3):
                put_url = self.generate_s3_presigned_url(container, blob_name, method="PUT", expires_in=3600)
                if not put_url:
                    return False
                try:
                    with open(source_file, "rb") as fp:
                        resp = self._upload_http_client().put(
                            put_url, content=iter(lambda: fp.read(1024 * 1024), b""),
                            timeout=httpx.Timeout(900, connect=10, pool=30),
                            headers={
                                "Content-Type": mime,
                                "Content-Length": str(fsize),
                                "User-Agent": "MosaicEarth/2.0",
                            },
                        )
                        if resp.status_code in (200, 201, 204):
                            cache_key = f"{container}:{clean_rel}"
                            self._missing_remote.pop(cache_key, None)
                            self._present_remote[cache_key] = fsize
                            return True
                except Exception:
                    continue
            return False
        if provider == "azure_blob":
            sas = self._sas_token or self._ensure_azure_sas_token()
            if not sas:
                return False
            clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
            encoded_rel = "/".join(urllib.parse.quote(seg, safe="") for seg in clean_rel.split("/"))
            endpoint = str(self.s3_cfg.get("endpoint_url") or "https://mosaicearthstg260809.blob.core.windows.net").rstrip("/")
            base_url = f"{endpoint}/{container}/{encoded_rel}"
            mime = guess_mime_type(blob_name)
            try:
                chunk_size = 16 * 1024 * 1024
                block_ids: List[str] = []
                with open(source_file, "rb") as fp:
                    idx = 0
                    while True:
                        chunk = fp.read(chunk_size)
                        if not chunk:
                            break
                        bid = base64.b64encode(f"block-{idx:06d}".encode("ascii")).decode("ascii")
                        block_ids.append(bid)
                        q_bid = urllib.parse.quote(bid, safe="")
                        req_blk = urllib.request.Request(
                            f"{base_url}?comp=block&blockid={q_bid}&{sas}",
                            data=chunk,
                            method="PUT",
                            headers={"Content-Length": str(len(chunk))},
                        )
                        with urllib.request.urlopen(req_blk, timeout=120) as r_blk:
                            if r_blk.status not in (200, 201):
                                return False
                        idx += 1
                xml_blocks = "".join(f"<Latest>{bid}</Latest>" for bid in block_ids)
                xml_body = f'<?xml version="1.0" encoding="utf-8"?><BlockList>{xml_blocks}</BlockList>'.encode("utf-8")
                req_list = urllib.request.Request(
                    f"{base_url}?comp=blocklist&{sas}",
                    data=xml_body,
                    method="PUT",
                    headers={
                        "Content-Type": "application/xml",
                        "Content-Length": str(len(xml_body)),
                        "x-ms-blob-content-type": mime,
                    },
                )
                with urllib.request.urlopen(req_list, timeout=60) as r_lst:
                    if r_lst.status in (200, 201):
                        cache_key = f"{container}:{clean_rel}"
                        self._missing_remote.pop(cache_key, None)
                        self._present_remote[cache_key] = fsize
                        return True
            except Exception:
                return False
        return False

    def sync_folder_to_cloud(self, container: str, folder_prefix: str, delete_local: bool = True) -> int:
        """Uploads all local files under blobs_root/container/folder_prefix to External Cloud Storage
        and removes local copies when zero_local_disk is enabled."""
        if not self.is_s3_active():
            return 0
        clean_prefix = (folder_prefix or "").replace("\\", "/").strip("/")
        base_dir = self.blobs_root / container / clean_prefix if clean_prefix else (self.blobs_root / container)
        if not base_dir.exists():
            return 0
        files = [
            p for p in base_dir.rglob("*")
            if p.is_file() and not p.name.endswith((".tmp_write", ".tmp_fetch"))
        ]
        if not files:
            return 0

        azcopy_bin = shutil.which("azcopy") or ("/usr/local/bin/azcopy" if os.path.isfile("/usr/local/bin/azcopy") else None)
        if azcopy_bin and str(self.s3_cfg.get("provider") or "").lower() == "azure_blob":
            sas = self._sas_token or self._ensure_azure_sas_token()
            endpoint = str(self.s3_cfg.get("endpoint_url") or "https://mosaicearthstg260809.blob.core.windows.net").rstrip("/")
            if sas:
                try:
                    import subprocess
                    dest_url = f"{endpoint}/{container}?{sas}"
                    proc = subprocess.run(
                        [azcopy_bin, "copy", str(base_dir), dest_url, "--recursive=true", "--overwrite=true"],
                        capture_output=True,
                        text=True,
                        timeout=1800,
                    )
                    if proc.returncode == 0:
                        if delete_local and self.is_zero_local_disk():
                            shutil.rmtree(base_dir, ignore_errors=True)
                        return len(files)
                except Exception:
                    pass

        uploaded = 0

        def _up_one(fp: Path) -> bool:
            rel = fp.relative_to(self.blobs_root / container).as_posix()
            ok = self._upload_file_to_s3(container, rel, fp)
            if ok and delete_local and self.is_zero_local_disk():
                try:
                    fp.unlink()
                except Exception:
                    pass
            return ok

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            for ok in ex.map(_up_one, files):
                if ok:
                    uploaded += 1

        if delete_local and self.is_zero_local_disk() and base_dir.is_dir():
            for d in sorted([p for p in base_dir.rglob("*") if p.is_dir()], key=lambda x: len(x.parts), reverse=True):
                try:
                    d.rmdir()
                except Exception:
                    pass
            if clean_prefix:
                try:
                    base_dir.rmdir()
                except Exception:
                    pass
        return uploaded

    def _resolve_in_root(self, root_blobs: Path, container: str, blob_name: str) -> Optional[Path]:
        if container not in self.CONTAINERS:
            return None
        clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
        parts = [p for p in clean_rel.split("/") if p]
        if not parts or any(p in (".", "..") or "\0" in p for p in parts):
            return None
        base = (root_blobs / container).resolve()
        full = (base / "/".join(parts)).resolve()
        if base not in full.parents and full != base:
            return None
        return full

    def resolve_path(self, container: str, blob_name: str) -> Optional[Path]:
        return self._resolve_in_root(self.blobs_root, container, blob_name)

    def list_stored_files(self, container: str, prefix: str, limit: int = 100000) -> List[str]:
        """Enumerate one authorized directory, including files evicted from local cache.

        S3 ListObjectsV2: https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html
        Caller must check project ownership before supplying its storage prefix.
        """
        import xml.etree.ElementTree as ET
        prefix = prefix.rstrip("/") + "/"
        directory = self.resolve_path(container, prefix)
        if directory is None:
            raise ValueError("Invalid storage prefix")
        files = set()
        for root in (self.blobs_root, self.legacy_blobs_root):
            folder = self._resolve_in_root(root, container, prefix)
            if folder and folder.is_dir():
                files.update(prefix + p.relative_to(folder).as_posix() for p in folder.rglob("*")
                             if p.is_file() and not p.is_symlink())
        if self.is_s3_active():
            token = ""
            azure = str(self.s3_cfg.get("provider") or "").lower() == "azure_blob"
            while True:
                if azure:
                    base = self.generate_s3_presigned_url(container, "")
                    url = base + "&" + urllib.parse.urlencode({"restype": "container", "comp": "list", "prefix": prefix, "marker": token})
                else:
                    url = self.generate_s3_presigned_url(container, "", list_prefix=prefix, continuation_token=token)
                try:
                    response = self._upload_http_client().get(url, timeout=60)
                    if response.status_code != 200:
                        raise ValueError("Cloud file listing is temporarily unavailable")
                    xml = ET.fromstring(response.content)
                except (httpx.HTTPError, ET.ParseError):
                    raise ValueError("Cloud file listing is temporarily unavailable") from None
                if azure:
                    names = [n.text for n in xml.findall("./Blobs/Blob/Name")]
                    token = xml.findtext("NextMarker", "")
                else:
                    names = [n.text[len(container) + 1:] for n in xml.findall("{*}Contents/{*}Key")
                             if n.text and n.text.startswith(container + "/")]
                    token = xml.findtext("{*}NextContinuationToken", "")
                files.update(n for n in names if n and n.startswith(prefix) and not n.endswith("/") and self.resolve_path(container, n))
                if len(files) > limit:
                    raise ValueError("Export tasks separately: too many stored files")
                if not token:
                    break
        return sorted(files)

    def _remote_missing(self, cache_key: str) -> bool:
        # Another worker can publish an object after a request returned 404.
        # Never retain that absence for the lifetime of the API process.
        expires = self._missing_remote.get(cache_key, 0.0)
        if expires > time.monotonic():
            return True
        self._missing_remote.pop(cache_key, None)
        return False

    def _remote_url_for(self, container: str, rel_path: str, method: str = "GET") -> Optional[str]:
        clean_rel = (rel_path or "").replace("\\", "/").lstrip("/")
        if not clean_rel:
            return None
        if self.is_s3_active():
            return self.generate_s3_presigned_url(container, clean_rel, method=method, expires_in=3600)
        return self._azure_fallback_url_for(container, clean_rel)

    def _azure_fallback_url_for(self, container: str, rel_path: str) -> Optional[str]:
        clean_rel = (rel_path or "").replace("\\", "/").lstrip("/")
        if not clean_rel:
            return None
        if not self._sas_token:
            self._sas_token = self._ensure_azure_sas_token()
        if self._sas_token:
            encoded_blob = "/".join(urllib.parse.quote(seg, safe="") for seg in clean_rel.split("/"))
            return f"https://mosaicearthstg260809.blob.core.windows.net/{container}/{encoded_blob}?{self._sas_token}"
        return None

    def remote_exists(self, container: str, blob_name: str, expected_size: Optional[int] = None) -> bool:
        clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
        if not clean_rel:
            return False
        cache_key = f"{container}:{clean_rel}"
        if self._remote_missing(cache_key):
            return False
        if cache_key in self._present_remote:
            if expected_size is not None and expected_size >= 0:
                return self._present_remote[cache_key] == expected_size
            return True
        urls = [self._remote_url_for(container, clean_rel, method="HEAD")]
        if str(self.s3_cfg.get("provider") or "").lower() != "azure_blob":
            fb_url = self._azure_fallback_url_for(container, clean_rel)
            if fb_url and fb_url not in urls:
                urls.append(fb_url)
        for idx, url in enumerate(urls):
            if not url:
                continue
            try:
                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    if resp.status == 200:
                        clen = int(resp.headers.get("Content-Length") or 0)
                        self._present_remote[cache_key] = clen
                        if expected_size is not None and expected_size >= 0:
                            return clen == expected_size
                        return True
            except urllib.error.HTTPError as he:
                if he.code == 404 and idx == len(urls) - 1:
                    self._missing_remote[cache_key] = time.monotonic() + 30.0
            except Exception:
                pass
        return False

    def ensure_local(self, container: str, blob_name: str, force_download: bool = False) -> Optional[Path]:
        p = self.resolve_path(container, blob_name)
        if p is None:
            return None
        if p.is_file():
            return p
        clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
        found = self._find_case_insensitive(self.blobs_root / container, clean_rel)
        if found and found.is_file():
            return found
        if not self.is_zero_local_disk() and self.legacy_blobs_root.is_dir():
            p_leg = self._resolve_in_root(self.legacy_blobs_root, container, blob_name)
            if p_leg and p_leg.is_file():
                return p_leg
            found_leg = self._find_case_insensitive(self.legacy_blobs_root / container, clean_rel)
            if found_leg and found_leg.is_file():
                return found_leg

        # If zero_local_disk is enabled and force_download is False, do not write permanent files to local disk
        if self.is_zero_local_disk() and not force_download:
            return None

        url = self._remote_url_for(container, clean_rel)
        if url:
            cache_key = f"{container}:{clean_rel}"
            if not self._remote_missing(cache_key):
                try:
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        if resp.status == 200:
                            data = resp.read()
                            p.parent.mkdir(parents=True, exist_ok=True)
                            tmp = p.with_suffix(p.suffix + ".tmp_fetch")
                            tmp.write_bytes(data)
                            tmp.replace(p)
                            self._present_remote[cache_key] = len(data)
                            return p
                except urllib.error.HTTPError as he:
                    if he.code == 404:
                        self._missing_remote[cache_key] = time.monotonic() + 30.0
                except Exception:
                    pass

        if os.environ.get("MOSAICEARTH_DESKTOP_LOCAL") == "1" and container == "imagenes":
            cache_key = f"desktop:{container}:{clean_rel}"
            if not self._remote_missing(cache_key):
                try:
                    cloud_origin = os.environ.get("MOSAICEARTH_CLOUD_ORIGIN", "https://app.mosaicearth.app").rstrip("/")
                    encoded_blob = "/".join(urllib.parse.quote(seg, safe="") for seg in clean_rel.split("/"))
                    d_url = f"{cloud_origin}/ProcessedAssetFile.ashx/{encoded_blob}"
                    req = urllib.request.Request(d_url, headers={"User-Agent": "MosaicEarth-Desktop/3.6"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        if resp.status == 200:
                            data = resp.read()
                            p.parent.mkdir(parents=True, exist_ok=True)
                            tmp = p.with_suffix(p.suffix + ".tmp_fetch")
                            tmp.write_bytes(data)
                            tmp.replace(p)
                            return p
                except urllib.error.HTTPError as he:
                    if he.code == 404:
                        self._missing_remote[cache_key] = time.monotonic() + 30.0
                except Exception:
                    pass
        return None

    @staticmethod
    def _find_case_insensitive(root: Path, rel_path: str) -> Optional[Path]:
        parts = [seg for seg in rel_path.split("/") if seg]
        if not parts:
            return None
        curr = root
        for idx, part in enumerate(parts):
            exact = curr / part
            if exact.exists():
                curr = exact
                continue
            if not curr.is_dir():
                return None
            part_lower = part.lower()
            matched = None
            try:
                for child in curr.iterdir():
                    if child.name.lower() == part_lower:
                        matched = child
                        break
                if matched is None and idx == 0:
                    m = re.match(r"^(\d+)-", part)
                    if m:
                        prefix = f"{m.group(1)}-"
                        for child in curr.iterdir():
                            if child.is_dir() and child.name.startswith(prefix):
                                matched = child
                                break
            except Exception:
                return None
            if matched is None:
                return None
            curr = matched
        return curr if curr.exists() else None

    def exists(self, container: str, blob_name: str, expected_size: Optional[int] = None) -> bool:
        p = self.resolve_blob_path(container, blob_name, allow_remote_fallback=False)
        if p and p.is_file():
            if expected_size is not None and expected_size >= 0:
                return p.stat().st_size == expected_size
            return True
        return self.remote_exists(container, blob_name, expected_size=expected_size)

    def read_bytes_from_cloud(self, container: str, blob_name: str) -> Optional[bytes]:
        p = self.resolve_blob_path(container, blob_name, allow_remote_fallback=False)
        if p and p.is_file():
            return p.read_bytes()
        clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
        cache_key = f"{container}:{clean_rel}"
        if self._remote_missing(cache_key):
            return None
        urls = [self._remote_url_for(container, clean_rel)]
        if str(self.s3_cfg.get("provider") or "").lower() != "azure_blob":
            fb_url = self._azure_fallback_url_for(container, clean_rel)
            if fb_url and fb_url not in urls:
                urls.append(fb_url)
        for idx, url in enumerate(urls):
            if not url:
                continue
            try:
                with urllib.request.urlopen(url, timeout=8.0) as resp:
                    if resp.status == 200:
                        data = resp.read()
                        self._present_remote[cache_key] = len(data)
                        if idx > 0 and len(data) <= 32 * 1024 * 1024:
                            try:
                                self._upload_bytes_to_s3(container, clean_rel, data)
                            except Exception:
                                pass
                        return data
            except urllib.error.HTTPError as he:
                if he.code == 404 and idx == len(urls) - 1:
                    self._missing_remote[cache_key] = time.monotonic() + 30.0
            except Exception:
                pass
        return None

    read_bytes = read_bytes_from_cloud

    def save_bytes(self, container: str, blob_name: str, data: bytes) -> Path:
        p = self.resolve_path(container, blob_name)
        if p is None:
            raise ValueError(f"Invalid blob path: {container}/{blob_name}")
        s3_ok = self._upload_bytes_to_s3(container, blob_name, data)
        if s3_ok and self.is_zero_local_disk():
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp_write")
        tmp.write_bytes(data)
        tmp.replace(p)
        return p

    def save_upload(self, container: str, blob_name: str, data: bytes) -> Path:
        return self.save_bytes(container, blob_name, data)

    async def save_upload_stream(
        self,
        container: str,
        blob_name: str,
        upload_file: Any,
        header_capture_bytes: int = 131072,
        keep_local_copy: bool = False,
    ) -> Tuple[Path, int, str, bytes]:
        """Streams an UploadFile in 1 MB chunks to disk, computing SHA-256 and capturing header bytes in O(1) RAM."""
        import asyncio
        p = self.resolve_path(container, blob_name)
        if p is None:
            raise ValueError(f"Invalid blob path: {container}/{blob_name}")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp_write")
        hasher = hashlib.sha256()
        total_size = 0
        header_buf = bytearray()
        with open(tmp, "wb") as fp:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                fp.write(chunk)
                hasher.update(chunk)
                total_size += len(chunk)
                if len(header_buf) < header_capture_bytes:
                    need = header_capture_bytes - len(header_buf)
                    header_buf.extend(chunk[:need])
        tmp.replace(p)
        sha256_hex = hasher.hexdigest()
        if self.is_s3_active():
            s3_ok = await asyncio.to_thread(self._upload_file_to_s3, container, blob_name, p)
            if s3_ok and self.is_zero_local_disk() and not keep_local_copy:
                try:
                    p.unlink()
                except Exception:
                    pass
        return p, total_size, sha256_hex, bytes(header_buf)

    def save_file(self, container: str, blob_name: str, source_file: Path) -> Path:
        p = self.resolve_path(container, blob_name)
        if p is None:
            raise ValueError(f"Invalid blob path: {container}/{blob_name}")
        same_file = False
        try:
            same_file = p.exists() and source_file.resolve() == p.resolve()
        except Exception:
            same_file = False
        if self.is_s3_active():
            s3_ok = self._upload_file_to_s3(container, blob_name, source_file)
            if s3_ok and self.is_zero_local_disk():
                if same_file:
                    try:
                        p.unlink()
                    except Exception:
                        pass
                return p
        if not same_file:
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source_file), str(p))
        return p

    def delete(self, container: str, blob_name: str) -> bool:
        clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
        cache_key = f"{container}:{clean_rel}"
        self._present_remote.pop(cache_key, None)
        self._missing_remote[cache_key] = time.monotonic() + 30.0
        p = self.resolve_path(container, blob_name)
        deleted = False
        if p and p.is_file():
            try:
                p.unlink()
                deleted = True
            except Exception:
                pass
        if self.is_s3_active():
            del_url = self.generate_s3_presigned_url(container, blob_name, method="DELETE", expires_in=300)
            if del_url:
                try:
                    req = urllib.request.Request(del_url, method="DELETE")
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status in (200, 202, 204):
                            deleted = True
                except Exception:
                    pass
        return deleted

    def delete_file(self, container: str, blob_name: str) -> bool:
        return self.delete(container, blob_name)

    def delete_prefix(self, container: str, folder_prefix: str) -> int:
        """Deletes all local files and known remote objects under `container/folder_prefix`."""
        clean_prefix = (folder_prefix or "").replace("\\", "/").strip("/")
        if not clean_prefix:
            return 0
        deleted_count = 0
        rel_keys: set = set()
        local_dir = self.resolve_path(container, clean_prefix)
        if local_dir and local_dir.is_dir():
            base_c = (self.blobs_root / container).resolve()
            for f in local_dir.rglob("*"):
                if f.is_file():
                    try:
                        rel_f = str(f.resolve().relative_to(base_c)).replace("\\", "/")
                        rel_keys.add(rel_f)
                    except Exception:
                        pass
        prefix_match = f"{container}:{clean_prefix}/"
        for ck in list(self._present_remote.keys()):
            if ck.startswith(prefix_match) or ck == f"{container}:{clean_prefix}":
                rel_keys.add(ck.split(":", 1)[1])
        for rk in sorted(rel_keys):
            if self.delete(container, rk):
                deleted_count += 1
        if local_dir and local_dir.is_dir():
            shutil.rmtree(local_dir, ignore_errors=True)
        return deleted_count

    def stream_remote_blob(
        self,
        request: Request,
        container: str,
        blob_name: str,
        content_type: Optional[str] = None,
        disposition: Optional[str] = None,
        cache_control: str = "private, max-age=0, must-revalidate",
        cors_origin: Optional[str] = "*",
    ) -> Optional[Response]:
        """Streams a blob directly from External Cloud Storage (mosaicearthstg260809.blob.core.windows.net or S3)
        with full HTTP Range support and zero local disk usage."""
        clean_rel = (blob_name or "").replace("\\", "/").lstrip("/")
        if not clean_rel:
            return None
        cache_key = f"{container}:{clean_rel}"
        if self._remote_missing(cache_key):
            return None
        mime = content_type or guess_mime_type(clean_rel)
        if clean_rel.lower().endswith(".json"):
            cache_control = "no-store, no-cache, must-revalidate, max-age=0"
        req_headers: Dict[str, str] = {}
        range_hdr = request.headers.get("range") if request and hasattr(request, "headers") else None
        if range_hdr:
            req_headers["Range"] = range_hdr
        method = request.method.upper() if request and hasattr(request, "method") else "GET"
        if method not in ("GET", "HEAD"):
            method = "GET"

        urls = [self._remote_url_for(container, clean_rel, method=method)]
        if str(self.s3_cfg.get("provider") or "").lower() != "azure_blob":
            fb_url = self._azure_fallback_url_for(container, clean_rel)
            if fb_url and fb_url not in urls:
                urls.append(fb_url)

        upstream_resp = None
        used_idx = 0
        for idx, candidate_url in enumerate(urls):
            if not candidate_url:
                continue
            try:
                upstream_req = urllib.request.Request(candidate_url, method=method, headers=req_headers)
                upstream_resp = urllib.request.urlopen(upstream_req, timeout=30.0)
                used_idx = idx
                break
            except urllib.error.HTTPError as he:
                if he.code == 404 and idx == len(urls) - 1:
                    self._missing_remote[cache_key] = time.monotonic() + 30.0
            except Exception:
                pass
        if upstream_resp is None:
            return None

        status_code = upstream_resp.status
        resp_headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": cache_control,
            "X-Content-Type-Options": "nosniff",
            "X-MosaicEarth-Storage": str(self.s3_cfg.get("provider") or "azure_blob"),
        }
        for h_name in ("Content-Length", "Content-Range", "ETag", "Last-Modified"):
            val = upstream_resp.headers.get(h_name)
            if val:
                resp_headers[h_name] = val
        if "Content-Length" in resp_headers and status_code == 200:
            try:
                self._present_remote[cache_key] = int(resp_headers["Content-Length"])
            except Exception:
                pass
        if disposition:
            resp_headers["Content-Disposition"] = disposition
        if cors_origin:
            resp_headers["Access-Control-Allow-Origin"] = cors_origin
            resp_headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            resp_headers["Access-Control-Allow-Headers"] = "Range, Authorization, Content-Type, Accept"
            resp_headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges, ETag"

        if method == "HEAD":
            upstream_resp.close()
            return Response(status_code=status_code, headers=resp_headers, media_type=mime)

        def _iter_remote():
            try:
                while True:
                    chunk = upstream_resp.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    upstream_resp.close()
                except Exception:
                    pass

        return StreamingResponse(_iter_remote(), status_code=status_code, headers=resp_headers, media_type=mime)

    def stream_first_available_remote(
        self,
        request: Request,
        containers: Iterable[str],
        candidates: Iterable[str],
        download_filename: Optional[str] = None,
        cors_origin: Optional[str] = "*",
    ) -> Optional[Response]:
        disp = f'attachment; filename="{download_filename}"' if download_filename else None
        for cand in candidates:
            if not cand:
                continue
            for c in containers:
                resp = self.stream_remote_blob(
                    request=request,
                    container=c,
                    blob_name=cand,
                    disposition=disp,
                    cors_origin=cors_origin,
                )
                if resp is not None:
                    return resp
        return None

    def stream_file_response(
        self,
        request: Request,
        container: str,
        blob_name: str,
        content_type: Optional[str] = None,
        disposition: Optional[str] = None,
        cache_control: str = "private, max-age=0, must-revalidate",
        cors_origin: Optional[str] = None,
    ) -> Response:
        p = self.resolve_blob_path(container, blob_name, allow_remote_fallback=False)
        if not p or not p.is_file():
            remote_resp = self.stream_remote_blob(
                request=request,
                container=container,
                blob_name=blob_name,
                content_type=content_type,
                disposition=disposition,
                cache_control=cache_control,
                cors_origin=cors_origin or "*",
            )
            if remote_resp is not None:
                return remote_resp
            p = self.ensure_local(container, blob_name)
        if not p or not p.is_file():
            return Response(status_code=404, content="Asset file not found.", media_type="text/plain")

        stat = p.stat()
        file_size = stat.st_size
        mime = content_type or guess_mime_type(p.name)
        etag = f'"{int(stat.st_mtime_ns):x}-{file_size:x}"'

        headers = {
            "Accept-Ranges": "bytes",
            "ETag": etag,
            "Cache-Control": cache_control,
            "X-Content-Type-Options": "nosniff",
        }
        if disposition:
            headers["Content-Disposition"] = disposition
        if cors_origin:
            headers["Access-Control-Allow-Origin"] = cors_origin
            headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            headers["Access-Control-Allow-Headers"] = "Range, Authorization, Content-Type, Accept"
            headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges, ETag"

        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)

        range_header = request.headers.get("range")
        if range_header and file_size > 0:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m:
                start_s, end_s = m.group(1), m.group(2)
                if start_s == "" and end_s != "":
                    suffix = int(end_s)
                    start = max(0, file_size - suffix)
                    end = file_size - 1
                else:
                    start = int(start_s) if start_s else 0
                    end = int(end_s) if end_s else file_size - 1
                if start >= file_size or start > end or start < 0:
                    headers["Content-Range"] = f"bytes */{file_size}"
                    return Response(status_code=416, headers=headers)
                end = min(end, file_size - 1)
                chunk_len = end - start + 1
                headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                headers["Content-Length"] = str(chunk_len)

                if request.method.upper() == "HEAD":
                    return Response(status_code=206, headers=headers, media_type=mime)

                def _iter_range(file_path: Path, offset: int, length: int):
                    with open(file_path, "rb") as f:
                        f.seek(offset)
                        remaining = length
                        while remaining > 0:
                            buf = f.read(min(65536, remaining))
                            if not buf:
                                break
                            remaining -= len(buf)
                            yield buf

                return StreamingResponse(_iter_range(p, start, chunk_len), status_code=206, headers=headers, media_type=mime)

        headers["Content-Length"] = str(file_size)
        if request.method.upper() == "HEAD":
            return Response(status_code=200, headers=headers, media_type=mime)
        return FileResponse(str(p), status_code=200, headers=headers, media_type=mime)

    @property
    def blob_root(self) -> Path:
        return self.blobs_root

    def resolve_blob_path(self, container: str, rel_path: str, allow_remote_fallback: bool = True) -> Optional[Path]:
        if allow_remote_fallback:
            return self.ensure_local(container, rel_path)
        clean_rel = (rel_path or "").replace("\\", "/").lstrip("/")
        p = self.resolve_path(container, rel_path)
        if p and p.is_file():
            return p
        found = self._find_case_insensitive(self.blobs_root / container, clean_rel)
        if found and found.is_file():
            return found
        if not self.is_zero_local_disk() and self.legacy_blobs_root.is_dir():
            p_leg = self._resolve_in_root(self.legacy_blobs_root, container, rel_path)
            if p_leg and p_leg.is_file():
                return p_leg
            found_leg = self._find_case_insensitive(self.legacy_blobs_root / container, clean_rel)
            if found_leg and found_leg.is_file():
                return found_leg
        return None

    def build_range_response(
        self,
        request: Request,
        resolved_path: Path,
        download_filename: Optional[str] = None,
        cors_origin: Optional[str] = None,
    ) -> Response:
        for root_dir in (self.blobs_root, self.legacy_blobs_root):
            if not root_dir.is_dir():
                continue
            for c in self.CONTAINERS:
                base = (root_dir / c).resolve()
                if base in resolved_path.resolve().parents:
                    rel = str(resolved_path.resolve().relative_to(base)).replace("\\", "/")
                    disp = f'attachment; filename="{download_filename}"' if download_filename else None
                    return self.stream_file_response(
                        request=request,
                        container=c,
                        blob_name=rel,
                        disposition=disp,
                        cors_origin=cors_origin,
                    )
        return FileResponse(str(resolved_path))


_BLOB_STORE_INSTANCE: Optional[LocalBlobStore] = None


def get_blob_store() -> LocalBlobStore:
    global _BLOB_STORE_INSTANCE
    if _BLOB_STORE_INSTANCE is None:
        data_root = os.environ.get("MOSAICEARTH_DATA_DIR", "/data")
        _BLOB_STORE_INSTANCE = LocalBlobStore(data_root=data_root)
    return _BLOB_STORE_INSTANCE
