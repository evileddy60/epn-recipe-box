from __future__ import annotations

import os
from flask import Flask, render_template
from werkzeug.exceptions import HTTPException

from .config import APP_TITLE, STATIC_DIR, SYNC_TIMEOUT_SECONDS, https_enabled, runtime_secret_key
from .routes import bp
from .security import apply_security_headers, csrf_input, csrf_token, error_response, validate_csrf


def create_app() -> Flask:
    application = Flask(__name__, template_folder="templates", static_folder=str(STATIC_DIR), static_url_path="/static")
    application.secret_key = runtime_secret_key()
    application.config.update(
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        SYNC_TIMEOUT_SECONDS=SYNC_TIMEOUT_SECONDS,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=https_enabled(),
        PERMANENT_SESSION_LIFETIME=__import__("datetime").timedelta(days=14),
        HTTPS_ENABLED=https_enabled(),
        ENFORCE_CSRF=True,
    )
    application.context_processor(lambda: {"csrf_token": csrf_token, "csrf_input": csrf_input, "app_title": APP_TITLE})
    application.before_request(validate_csrf)
    application.after_request(apply_security_headers)

    @application.errorhandler(HTTPException)
    def handle_http_error(error):
        return error_response(error.code or 500, error.description if error.code in {400, 401, 403, 404, 413, 429} else "The request could not be completed.")

    @application.errorhandler(Exception)
    def handle_unexpected_error(error):
        application.logger.exception("unhandled_request_error")
        return error_response(500, "The server could not complete the request.")

    application.register_blueprint(bp)
    # Preserve the pre-refactor endpoint names used by templates, callers, and
    # existing integrations while routes live in a blueprint.
    aliases = set()
    for rule in list(application.url_map.iter_rules()):
        if not rule.endpoint.startswith("main."):
            continue
        alias = rule.endpoint.removeprefix("main.")
        if alias in aliases or alias in application.view_functions:
            continue
        methods = sorted(rule.methods.difference({"HEAD", "OPTIONS"}))
        application.add_url_rule(
            rule.rule,
            endpoint=alias,
            view_func=application.view_functions[rule.endpoint],
            defaults=rule.defaults,
            methods=methods or ["GET"],
            strict_slashes=rule.strict_slashes,
        )
        aliases.add(alias)
    return application


app = create_app()
