"""Compatibility entrypoint for EPN Recipe Box.

The public ``app`` object and legacy helper imports remain available while
implementation is organized under the ``recipe_box`` package.
"""

from recipe_box.application import app, create_app
from recipe_box.config import *
from recipe_box.db import *
from recipe_box.domain import *
from recipe_box.sync_service import *
from recipe_box.routes import *
import recipe_box.config as _config
import recipe_box.db as _db
import recipe_box.domain as _domain


def _sync_legacy_globals() -> None:
    """Keep legacy app-module path overrides working for callers and tests."""
    for name in ("DATA_DIR", "UPLOAD_DIR", "DB_FILE", "STATIC_DIR"):
        value = globals()[name]
        setattr(_config, name, value)
        setattr(_db, name, value)
        setattr(_domain, name, value)


def db_connect():
    _sync_legacy_globals()
    return _db.db_connect()


def init_db() -> None:
    _sync_legacy_globals()
    return _db.init_db()


def sync_token() -> str:
    _sync_legacy_globals()
    return _db.sync_token()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)  # nosec B104 - development entrypoint; deployment uses Gunicorn/systemd
