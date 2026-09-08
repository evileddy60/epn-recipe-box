from __future__ import annotations

import base64
import hashlib
import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger("recipe_box.mail")
outbox: list[dict[str, str]] = []
DEFAULT_GMAIL_SENDER = "aaron.doug.projects@gmail.com"
SEND_TIMEOUT_SECONDS = 10


def clear_outbox() -> None:
    outbox.clear()


def _backend() -> str:
    return os.environ.get("EPN_MAIL_BACKEND", "smtp" if os.environ.get("EPN_ENV", "").lower() in {"prod", "production"} else "fake").strip().lower()


def _mail_enabled() -> bool:
    return os.environ.get("EPN_MAIL_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}


def _gmail_service():
    token_path = Path(os.environ.get("EPN_GMAIL_TOKEN_FILE", "").strip())
    client_secret_path = Path(os.environ.get("EPN_GMAIL_CLIENT_SECRET_FILE", "").strip())
    if not token_path.is_file() or not client_secret_path.is_file():
        raise RuntimeError("Gmail API credential files are missing.")
    for path in (token_path, client_secret_path):
        if path.stat().st_mode & 0o077:
            raise RuntimeError("Gmail API credential files must not be group/world-readable.")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Gmail API dependencies are not installed.") from exc
    credentials = Credentials.from_authorized_user_file(str(token_path), ["https://www.googleapis.com/auth/gmail.send"])
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise RuntimeError("Gmail API credentials are invalid.")
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _gmail_message(recipient: str, subject: str, plain_body: str, html_body: str | None, sender: str) -> dict[str, str]:
    if html_body:
        message = EmailMessage()
        message.set_content(plain_body)
        message.add_alternative(html_body, subtype="html")
    else:
        message = EmailMessage()
        message.set_content(plain_body)
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return {"raw": raw}


def _send_gmail_api(recipient: str, subject: str, plain_body: str, html_body: str | None) -> str:
    sender = os.environ.get("EPN_MAIL_FROM", DEFAULT_GMAIL_SENDER).strip().lower()
    allowed_sender = os.environ.get("EPN_GMAIL_ACCOUNT", DEFAULT_GMAIL_SENDER).strip().lower()
    if sender != allowed_sender:
        raise RuntimeError("Configured Gmail From identity is not authorized.")
    service = _gmail_service()
    result = service.users().messages().send(userId="me", body=_gmail_message(recipient, subject, plain_body, html_body, sender)).execute()
    message_id = result.get("id")
    if not message_id:
        raise RuntimeError("Gmail API did not return a message identifier.")
    return str(message_id)


def send_email(recipient: str, subject: str, plain_body: str, html_body: str | None = None) -> str | None:
    if not _mail_enabled():
        raise RuntimeError("Outbound mail is disabled.")
    backend = _backend()
    if backend == "fake":
        if os.environ.get("EPN_ENV", "").strip().lower() in {"prod", "production"}:
            raise RuntimeError("The fake password-reset mail backend is not allowed in production.")
        outbox.append({"recipient": recipient, "subject": subject, "body": plain_body, "html_body": html_body or ""})
        return None
    if backend == "gmail_api":
        return _send_gmail_api(recipient, subject, plain_body, html_body)
    if backend != "smtp":
        raise RuntimeError("Unsupported password-reset mail backend.")
    host = os.environ.get("EPN_MAIL_HOST", os.environ.get("EMAIL_SMTP_HOST", "")).strip()
    sender = os.environ.get("EPN_MAIL_FROM", os.environ.get("EMAIL_ADDRESS", "")).strip()
    if not host or not sender:
        raise RuntimeError("Password-reset email is not configured.")
    port = int(os.environ.get("EPN_MAIL_PORT", "587"))
    message = EmailMessage()
    message.set_content(plain_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    with smtplib.SMTP(host, port, timeout=SEND_TIMEOUT_SECONDS) as client:
        if os.environ.get("EPN_MAIL_USE_TLS", "1").strip().lower() in {"1", "true", "yes", "on"}:
            client.starttls()
        username = os.environ.get("EPN_MAIL_USERNAME", os.environ.get("EMAIL_ADDRESS", "")).strip()
        if username:
            client.login(username, os.environ.get("EPN_MAIL_PASSWORD", os.environ.get("EMAIL_PASSWORD", "")))
        client.send_message(message)
    logger.info("password_reset_email_sent recipient_hash=%s", hashlib.sha256(recipient.lower().encode()).hexdigest()[:12])
    return None


def send_password_reset_email(recipient: str, reset_url: str, code: str, expiry_minutes: int = 10) -> None:
    subject = "EPN Recipe Box password reset"
    plain_body = (
        "Someone requested a password reset for your EPN Recipe Box account.\n\n"
        f"Your Recipe Box verification code: {code}\n"
        f"This code expires in {expiry_minutes} minutes.\n\n"
        f"Use this web link within {expiry_minutes} minutes to choose a new password:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this message."
    )
    html_body = (
        "<p>Someone requested a password reset for your EPN Recipe Box account.</p>"
        f"<p><strong>Your Recipe Box verification code: {code}</strong><br>"
        f"This code expires in {expiry_minutes} minutes.</p>"
        f'<p><a href="{reset_url}">Reset your password</a></p>'
        "<p>If you did not request this, you can ignore this message.</p>"
    )
    send_email(recipient, subject, plain_body, html_body)
    if _backend() == "fake" and outbox:
        outbox[-1].update({"reset_url": reset_url, "code": code})
