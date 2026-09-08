import os
import secrets
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

APP_TITLE = "EPN Recipe Box"
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
# Beta artifacts are an explicit allowlisted directory, never a user-controlled path.
_configured_beta_dir = os.environ.get("EPN_PUBLIC_BETA_DIR", "").strip()
PUBLIC_BETA_DIR = Path(_configured_beta_dir) if _configured_beta_dir else (Path("/var/lib/epn-recipe-box/public-beta") if os.environ.get("EPN_ENV", "development").strip().lower() in {"production", "prod"} else BASE_DIR.parent / "Public-Beta")
DATA_DIR = Path(os.environ.get("EPN_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
RECIPE_IMAGE_DIR = DATA_DIR / "recipe-images"
DB_FILE = DATA_DIR / "recipe_box.db"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_CONTENT_LENGTH = 8 * 1024 * 1024
MAX_RECIPE_IMAGE_BYTES = 4 * 1024 * 1024
MAX_RECIPE_IMAGE_DIMENSION = 4096
MAX_RECIPE_IMAGE_PIXELS = 16_000_000
MAX_RECIPE_IMAGE_DISPLAY_DIMENSION = 1600
MAX_IMPORT_BYTES = 4 * 1024 * 1024
MAX_IMPORT_RECIPES = 200
SYNC_TIMEOUT_SECONDS = float(os.environ.get("SYNC_TIMEOUT_SECONDS", "5"))
APP_ENV = os.environ.get("EPN_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"production", "prod"}
HTTPS_ENABLED = os.environ.get("EPN_HTTPS", "0").strip().lower() in {"1", "true", "yes", "on"}
SECRET_KEY_MIN_LENGTH = 32
API_TOKEN_TTL_DAYS = int(os.environ.get("API_TOKEN_TTL_DAYS", "30"))
API_BASE_URL = os.environ.get("EPN_API_BASE_URL", "").strip().rstrip("/")
PASSWORD_RESET_TTL_MINUTES = int(os.environ.get("EPN_PASSWORD_RESET_TTL_MINUTES", "20"))
PASSWORD_RESET_CODE_TTL_MINUTES = int(os.environ.get("EPN_PASSWORD_RESET_CODE_TTL_MINUTES", "10"))
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


def public_reset_url(token: str) -> str:
    """Build a reset URL from an explicit public origin, never the request Host header."""
    if not isinstance(token, str) or not token:
        raise ValueError("Reset token is required.")
    production = os.environ.get("EPN_ENV", "development").strip().lower() in {"production", "prod"}
    configured = os.environ.get("EPN_PUBLIC_BASE_URL", "").strip()
    if not configured:
        if production:
            raise ValueError("EPN_PUBLIC_BASE_URL is required in production.")
        configured = "http://localhost"
    parsed = urlsplit(configured.rstrip("/"))
    hostname = (parsed.hostname or "").lower().rstrip(".")
    try:
        loopback = ip_address(hostname).is_loopback
    except ValueError:
        loopback = False
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("EPN_PUBLIC_BASE_URL must be a valid HTTP(S) origin.")
    if production and (parsed.scheme != "https" or hostname == "localhost" or hostname.endswith(".localhost") or loopback):
        raise ValueError("Production reset URLs must use a non-localhost HTTPS public URL.")
    path = parsed.path.rstrip("/") + "/reset-password"
    return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode({"token": token}), ""))


STATIC_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
