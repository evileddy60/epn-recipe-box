from __future__ import annotations

import os
from flask import Flask

from .config import APP_TITLE, STATIC_DIR, SYNC_TIMEOUT_SECONDS
from .routes import bp


def create_app() -> Flask:
    application = Flask(__name__, template_folder="templates", static_folder=str(STATIC_DIR), static_url_path="/static")
    application.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
    application.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    application.config["SYNC_TIMEOUT_SECONDS"] = SYNC_TIMEOUT_SECONDS
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
