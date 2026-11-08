import base64
import datetime
import hashlib
import hmac
import html
import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple


def _parse_acs_connection_string(conn_str: str) -> Tuple[str, str]:
    endpoint = ""
    access_key = ""
    for part in (conn_str or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k_low = k.strip().lower()
        if k_low == "endpoint":
            endpoint = v.strip().rstrip("/")
        elif k_low == "accesskey":
            access_key = v.strip()
    return endpoint, access_key


def _wrap_mosaic_email_html(title: str, body_html: str, cta_label: str = "", cta_url: str = "") -> str:
    cta_block = ""
    if cta_label and cta_url:
        safe_url = html.escape(cta_url, quote=True)
        safe_label = html.escape(cta_label)
        cta_block = f"""
        <div style="margin: 28px 0;">
            <a href="{safe_url}" style="display:inline-block;padding:12px 26px;background:#99CC00;color:#111612;font-weight:700;text-decoration:none;border-radius:4px;font-family:Arial,sans-serif;font-size:14px;">
                {safe_label}
            </a>
        </div>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body style="margin:0;padding:24px;background:#121614;color:#eef3ee;font-family:Arial,Helvetica,sans-serif;">
    <div style="max-width:580px;margin:0 auto;background:#1b211d;border:1px solid rgba(153,204,0,0.28);border-radius:8px;padding:32px;">
        <div style="margin-bottom:20px;font-size:22px;font-weight:700;letter-spacing:-0.02em;color:#f4f7f2;">
            Mosaic<span style="color:#99CC00;">Earth</span>
        </div>
        <h2 style="margin:0 0 16px 0;font-size:18px;color:#ffffff;">{html.escape(title)}</h2>
        <div style="font-size:14px;line-height:1.6;color:#d0d8d2;">
            {body_html}
        </div>
        {cta_block}
        <hr style="border:none;border-top:1px solid rgba(255,255,255,0.1);margin:24px 0 14px 0;" />
        <div style="font-size:12px;color:#8c968e;">
            MosaicEarth · Georeferenced 3D Models, Orthophotos, Point Clouds &amp; Gaussian Splats
        </div>
    </div>
</body>
</html>"""


class MosaicEarthEmailService:
    """Transactional email dispatcher supporting Azure Communication Services (HMAC REST)
    and standard SMTP, with idempotency and persistent audit logging in dbo.MOSAICEARTH_EMAIL_AUDIT."""

    def __init__(self, store: Any):
        self.store = store

    def _get_config(self) -> Dict[str, str]:
        acs_conn = os.environ.get("AZURE_EMAIL_CONNECTION_STRING") or self.store.get_setting("Email.AzureConnectionString", "")
        sender = os.environ.get("AZURE_EMAIL_SENDER") or self.store.get_setting("Email.AzureSenderAddress", "DoNotReply@mosaicearth.app")
        smtp_host = os.environ.get("SMTP_HOST") or self.store.get_setting("Email.SmtpHost", "")
        smtp_port = os.environ.get("SMTP_PORT") or self.store.get_setting("Email.SmtpPort", "587")
        smtp_user = os.environ.get("SMTP_USER") or self.store.get_setting("Email.SmtpUser", "")
        smtp_pass = os.environ.get("SMTP_PASS") or self.store.get_setting("Email.SmtpPassword", "")
        return {
            "acs_conn": acs_conn,
            "sender": sender or "DoNotReply@mosaicearth.app",
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user,
            "smtp_pass": smtp_pass,
        }

    def send_email(
        self,
        recipient: str,
        subject: str,
        html_content: str,
        plain_text: str = "",
        template_kind: str = "transactional",
        idempotency_key: Optional[str] = None,
    ) -> Tuple[bool, str]:
        recipient = (recipient or "").strip()
        if not recipient or "@" not in recipient:
            return False, "INVALID_RECIPIENT"

        with self.store.lock:
            audit_rows = self.store.tables.setdefault("dbo.MOSAICEARTH_EMAIL_AUDIT", [])
            if idempotency_key:
                for row in audit_rows:
                    corr = str(row.get("CORRELATION_KEY") or row.get("IDEMPOTENCY_KEY") or "")
                    st_ok = bool(row.get("ACCEPTED")) or str(row.get("STATUS") or "").lower() == "accepted"
                    if corr == idempotency_key and st_ok:
                        return True, str(row.get("PROVIDER_MESSAGE_ID") or row.get("MESSAGE_ID") or "IDEMPOTENT_SKIP")

        cfg = self._get_config()
        accepted = False
        provider = "local_spool"
        message_id = ""
        detail = ""

        # 1. Try SMTP if explicitly configured
        if cfg["smtp_host"]:
            provider = "smtp"
            try:
                msg = EmailMessage()
                msg["Subject"] = subject
                msg["From"] = cfg["sender"]
                msg["To"] = recipient
                msg.set_content(plain_text or subject)
                msg.add_alternative(html_content, subtype="html")
                with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"] or 587), timeout=15) as server:
                    server.ehlo()
                    if int(cfg["smtp_port"] or 587) in (587, 25):
                        try:
                            server.starttls()
                        except Exception:
                            pass
                    if cfg["smtp_user"] and cfg["smtp_pass"]:
                        server.login(cfg["smtp_user"], cfg["smtp_pass"])
                    server.send_message(msg)
                accepted = True
                message_id = f"smtp-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}"
                detail = "Accepted by SMTP relay"
            except Exception as ex:
                detail = f"SMTP error: {ex}"

        # 2. Try Azure Communication Services Email REST API if configured
        if not accepted and cfg["acs_conn"]:
            endpoint, access_key = _parse_acs_connection_string(cfg["acs_conn"])
            if endpoint and access_key:
                provider = "azure_acs"
                try:
                    path_and_query = "/emails:send?api-version=2023-03-31"
                    url = f"{endpoint}{path_and_query}"
                    payload = {
                        "senderAddress": cfg["sender"],
                        "content": {
                            "subject": subject,
                            "plainText": plain_text or subject,
                            "html": html_content,
                        },
                        "recipients": {
                            "to": [{"address": recipient}],
                        },
                    }
                    body_bytes = json.dumps(payload).encode("utf-8")
                    date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
                    host = urllib.parse.urlparse(endpoint).netloc
                    content_hash = base64.b64encode(hashlib.sha256(body_bytes).digest()).decode("ascii")
                    string_to_sign = f"POST\n{path_and_query}\n{date_str};{host};{content_hash}"
                    key_bytes = base64.b64decode(access_key)
                    sig = base64.b64encode(hmac.new(key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()).decode("ascii")
                    req = urllib.request.Request(
                        url,
                        data=body_bytes,
                        method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "x-ms-date": date_str,
                            "x-ms-content-sha256": content_hash,
                            "Authorization": f"HMAC-SHA256 SignedHeaders=x-ms-date;host;x-ms-content-sha256&Signature={sig}",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        resp_body = resp.read().decode("utf-8", errors="replace")
                        resp_json = json.loads(resp_body) if resp_body.strip().startswith("{") else {}
                        accepted = resp.status in (200, 201, 202)
                        message_id = str(resp_json.get("id") or resp.headers.get("mise-correlation-id") or f"acs-{resp.status}")
                        detail = f"HTTP {resp.status} ({resp_json.get('status', 'Queued')})"
                except Exception as ex:
                    detail = f"ACS error: {ex}"

        # 3. Always write a copy to local spool (/data/db/email_outbox) for full auditability
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            outbox_dir = self.store.db_dir / "email_outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)
            if not message_id:
                message_id = f"local-{int(now_utc.timestamp() * 1000)}"
            spool_file = outbox_dir / f"{now_utc.strftime('%Y%m%d_%H%M%S')}_{message_id}.json"
            spool_file.write_text(
                json.dumps(
                    {
                        "timestamp_utc": now_iso,
                        "recipient": recipient,
                        "subject": subject,
                        "template_kind": template_kind,
                        "idempotency_key": idempotency_key,
                        "provider": provider,
                        "accepted": accepted,
                        "message_id": message_id,
                        "detail": detail,
                        "html": html_content,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        with self.store.lock:
            audit_rows = self.store.tables.setdefault("dbo.MOSAICEARTH_EMAIL_AUDIT", [])
            next_id = max([int(r.get("ID") or 0) for r in audit_rows] + [20060]) + 1
            audit_rows.append(
                {
                    "ID": next_id,
                    "CREATED_AT_UTC": now_iso,
                    "EMAIL_TYPE": template_kind,
                    "TEMPLATE_KIND": template_kind,
                    "RECIPIENT": recipient,
                    "SUBJECT": subject,
                    "CORRELATION_KEY": idempotency_key,
                    "IDEMPOTENCY_KEY": idempotency_key,
                    "TRANSPORT": "AzureCommunicationServices" if provider == "azure_acs" else provider,
                    "PROVIDER": provider,
                    "STATUS": "accepted" if accepted else "spool_only",
                    "ACCEPTED": accepted,
                    "PROVIDER_MESSAGE_ID": message_id,
                    "MESSAGE_ID": message_id,
                    "DETAIL": detail[:1000],
                    "DELIVERY_STATUS": "Accepted" if accepted else "SpoolOnly",
                    "DELIVERY_UPDATED_AT_UTC": now_iso,
                    "DELIVERY_DETAIL": detail[:1000],
                }
            )
            self.store.persist_table("dbo.MOSAICEARTH_EMAIL_AUDIT")

        return accepted, message_id

    def send_account_created_email(self, email: str, username: str, verify_url: str) -> bool:
        subject = "Welcome to MosaicEarth — Confirm your account"
        body = (
            f"<p>Hello <strong>{html.escape(username or email)}</strong>,</p>"
            f"<p>Your MosaicEarth account has been created. You can sign in and start exploring or publishing 3D models, orthophotos, point clouds, and Gaussian splats.</p>"
        )
        html_doc = _wrap_mosaic_email_html("Welcome to MosaicEarth", body, "Open MosaicEarth", verify_url)
        ok, _ = self.send_email(email, subject, html_doc, plain_text=f"Welcome to MosaicEarth! Open: {verify_url}", template_kind="account_created")
        return ok

    def send_password_reset_email(self, email: str, username: str, reset_url: str) -> bool:
        subject = "MosaicEarth — Reset your password"
        body = (
            f"<p>Hello <strong>{html.escape(username or email)}</strong>,</p>"
            f"<p>We received a request to reset the password for your MosaicEarth account (<code>{html.escape(email)}</code>).</p>"
            f"<p>Click the button below to choose a new password:</p>"
        )
        html_doc = _wrap_mosaic_email_html("Reset your password", body, "Reset Password", reset_url)
        ok, _ = self.send_email(email, subject, html_doc, plain_text=f"Reset your MosaicEarth password: {reset_url}", template_kind="password_reset")
        return ok

    def send_email_change_verification(self, new_email: str, username: str, verify_url: str) -> bool:
        subject = "MosaicEarth — Verify your new sign-in email"
        body = (
            f"<p>Hello <strong>{html.escape(username or new_email)}</strong>,</p>"
            f"<p>Please confirm that you want to use <code>{html.escape(new_email)}</code> as your new sign-in email on MosaicEarth.</p>"
        )
        html_doc = _wrap_mosaic_email_html("Verify your new email address", body, "Confirm Email Change", verify_url)
        ok, _ = self.send_email(new_email, subject, html_doc, plain_text=f"Confirm your email change: {verify_url}", template_kind="email_change")
        return ok

    def send_project_completed_email(
        self,
        recipient: str,
        project_name: str,
        project_url: str,
        is_imported: bool = False,
        idempotency_key: Optional[str] = None,
        result_kind_label: Optional[str] = None,
    ) -> bool:
        kind_text = result_kind_label or ("imported asset" if is_imported else "photogrammetry project")
        subject = f"MosaicEarth — Your {kind_text} '{project_name}' is ready"
        body = (
            f"<p>Good news! Your {html.escape(kind_text)} <strong>{html.escape(project_name)}</strong> has finished processing and is ready for review in your workspace.</p>"
            f"<p>You can inspect its 3D placement, configure metadata, or publish it to the global map.</p>"
        )
        html_doc = _wrap_mosaic_email_html(f"'{project_name}' is ready", body, "Open in Workspace", project_url)
        ok, _ = self.send_email(
            recipient,
            subject,
            html_doc,
            plain_text=f"Your MosaicEarth {kind_text} '{project_name}' is ready: {project_url}",
            template_kind="project_completed",
            idempotency_key=idempotency_key,
        )
        return ok


_EMAIL_SERVICE_INSTANCE: Optional[MosaicEarthEmailService] = None


def get_email_service() -> MosaicEarthEmailService:
    global _EMAIL_SERVICE_INSTANCE
    if _EMAIL_SERVICE_INSTANCE is None:
        from .db_store import get_store
        _EMAIL_SERVICE_INSTANCE = MosaicEarthEmailService(get_store())
    return _EMAIL_SERVICE_INSTANCE

