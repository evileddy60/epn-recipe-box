from __future__ import annotations

import hmac
import logging
import secrets
from datetime import timedelta
from functools import wraps

from flask import abort, current_app, jsonify, make_response, request, session
from markupsafe import Markup, escape

logger = logging.getLogger("recipe_box.security")

_FAILED_LOGINS: dict[str, list[float]] = {}
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 60


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def csrf_input() -> Markup:
    return Markup('<input type="hidden" name="csrf_token" value="{}">').format(escape(csrf_token()))


def _is_bearer_sync_request() -> bool:
    return request.path.startswith("/api/sync/") and request.headers.get("Authorization", "").lower().startswith("bearer ")


def validate_csrf() -> None:
    if (
        not current_app.config.get("ENFORCE_CSRF", True)
        or request.method not in {"POST", "PUT", "PATCH", "DELETE"}
        or request.path.startswith("/api/v1/")
        or _is_bearer_sync_request()
    ):
        return
    provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not provided or not hmac.compare_digest(str(expected), str(provided)):
        abort(400, description="The form security token is missing or invalid. Refresh the page and try again.")


def rotate_session(user_id: str) -> None:
    session.clear()
    session["user_id"] = user_id
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True


def login_allowed(identifier: str) -> bool:
    import time

    now = time.monotonic()
    attempts = [value for value in _FAILED_LOGINS.get(identifier.lower(), []) if now - value < LOGIN_WINDOW_SECONDS]
    _FAILED_LOGINS[identifier.lower()] = attempts
    return len(attempts) < LOGIN_MAX_FAILURES


def record_login_failure(identifier: str) -> None:
    import time

    key = identifier.lower()
    attempts = [value for value in _FAILED_LOGINS.get(key, []) if time.monotonic() - value < LOGIN_WINDOW_SECONDS]
    attempts.append(time.monotonic())
    _FAILED_LOGINS[key] = attempts[-LOGIN_MAX_FAILURES:]
    logger.warning("login_failure identifier_hash=%s attempts=%d", _safe_identifier(identifier), len(_FAILED_LOGINS[key]))


def record_login_success(identifier: str) -> None:
    _FAILED_LOGINS.pop(identifier.lower(), None)


def login_retry_after(identifier: str) -> int:
    import time

    attempts = _FAILED_LOGINS.get(identifier.lower(), [])
    if len(attempts) < LOGIN_MAX_FAILURES:
        return 0
    return max(1, int(LOGIN_LOCKOUT_SECONDS - (time.monotonic() - attempts[0])))


def _safe_identifier(identifier: str) -> str:
    import hashlib

    return hashlib.sha256(identifier.strip().lower().encode()).hexdigest()[:12]


def is_api_request() -> bool:
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def error_response(status: int, message: str):
    if is_api_request():
        return jsonify({"error": {"code": f"HTTP_{status}", "message": message}}), status
    return make_response(
        f'<!doctype html><title>{status}</title><main><h1>{status}</h1><p>{escape(message)}</p><p><a href="/">Return to Recipe Box</a></p></main>',
        status,
    )


def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if current_app.config.get("HTTPS_ENABLED"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
