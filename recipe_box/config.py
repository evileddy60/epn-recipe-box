import os
import secrets
from pathlib import Path

APP_TITLE = "EPN Recipe Box"
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("EPN_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_FILE = DATA_DIR / "recipe_box.db"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_CONTENT_LENGTH = 8 * 1024 * 1024
SYNC_TIMEOUT_SECONDS = float(os.environ.get("SYNC_TIMEOUT_SECONDS", "5"))
APP_ENV = os.environ.get("EPN_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"production", "prod"}
HTTPS_ENABLED = os.environ.get("EPN_HTTPS", "0").strip().lower() in {"1", "true", "yes", "on"}
SECRET_KEY_MIN_LENGTH = 32
DEVELOPMENT_SECRET_FALLBACK = secrets.token_urlsafe(32)


def https_enabled() -> bool:
    return os.environ.get("EPN_HTTPS", "0").strip().lower() in {"1", "true", "yes", "on"}


def runtime_secret_key() -> str:
    configured = os.environ.get("SECRET_KEY", "").strip()
    production = os.environ.get("EPN_ENV", "development").strip().lower() in {"production", "prod"}
    if production:
        if not configured:
            raise RuntimeError("SECRET_KEY must be set in production mode.")
        if len(configured) < SECRET_KEY_MIN_LENGTH:
            raise RuntimeError(f"SECRET_KEY must be at least {SECRET_KEY_MIN_LENGTH} characters in production mode.")
        return configured
    return configured or DEVELOPMENT_SECRET_FALLBACK


STATIC_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
