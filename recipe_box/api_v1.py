from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, g, jsonify, request, send_from_directory, url_for

from sync import token_hash
from . import config as _config
from .config import *
from .db import *
from .security import login_allowed, login_retry_after, record_login_failure, record_login_success


bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
MAX_API_BODY_BYTES = 64 * 1024
MAX_API_PAGE_SIZE = 50
MAX_API_SEARCH_LENGTH = 200
MAX_API_TAGS = 12


def _error(code: str, message: str, status: int, details: dict | None = None):
    payload = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return jsonify(payload), status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_body() -> dict | None:
    if request.content_length and request.content_length > MAX_API_BODY_BYTES:
        return None
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def _user_payload(row: sqlite3.Row | dict) -> dict:
    return {"id": row["id"], "email": row["email"], "nickname": row["nickname"], "bio": row["bio"]}


def _recipe_payload(row: sqlite3.Row | dict, conn: sqlite3.Connection) -> dict:
    tags = recipe_tag_payload(conn, row["id"])
    image = None
    if row["image_filename"]:
        image = {
            "url": url_for("api_v1.recipe_image", recipe_id=row["id"]),
            "media_type": row["image_media_type"],
            "width": row["image_width"],
            "height": row["image_height"],
            "size": row["image_size"],
        }
    return {
        "id": row["id"],
        "title": row["title"],
        "summary": row["summary"],
        "prep_time": row["prep_time"],
        "servings": row["servings"],
        "ingredients": json.loads(row["ingredients_json"]),
        "steps": json.loads(row["steps_json"]),
        "category": row["category_key"] or "",
        "tags": tags,
        "image": image,
        "creator": {"id": row["owner_id"], "nickname": row["owner_nickname"]},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _require_token(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        authorization = request.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            return _error("AUTHENTICATION_REQUIRED", "A valid API token is required.", 401)
        presented = authorization[7:].strip()
        if not presented or len(presented) > 512:
            return _error("AUTHENTICATION_FAILED", "A valid API token is required.", 401)
        init_db()
        hashed = token_hash(presented)
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT t.id AS token_id, t.user_id, t.expires_at, t.revoked_at,
                       u.id, u.email, u.nickname, u.bio
                FROM api_tokens t JOIN users u ON u.id = t.user_id
                WHERE t.token_hash = ?
                """,
                (hashed,),
            ).fetchone()
            if not row:
                return _error("AUTHENTICATION_FAILED", "A valid API token is required.", 401)
            if row["revoked_at"] or _parse_iso(row["expires_at"]) <= _now():
                return _error("AUTHENTICATION_FAILED", "A valid API token is required.", 401)
            conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (_now_iso(), row["token_id"]))
        g.api_token_id = row["token_id"]
        g.api_user = row
        return view(*args, **kwargs)

    return wrapped


def _validate_login(payload: dict | None) -> tuple[str, str] | None:
    if not payload or not isinstance(payload.get("email"), str) or not isinstance(payload.get("password"), str):
        return None
    email = payload["email"].strip().lower()
    password = payload["password"]
    if not email or not password or len(email) > MAX_EMAIL_LENGTH or len(password) > 128:
        return None
    return email, password


@bp.route("/health")
def health():
    init_db()
    with db_connect() as conn:
        conn.execute("SELECT 1").fetchone()
    return jsonify({"status": "ok", "database": "ok", "schema_version": schema_version(DB_FILE), "api_version": "v1"})


@bp.route("/auth/login", methods=["POST"])
def login():
    credentials = _validate_login(_json_body())
    if not credentials:
        return _error("VALIDATION_ERROR", "Email and password are required.", 422)
    email, password = credentials
    if not login_allowed(email):
        response = _error("RATE_LIMITED", "Too many unsuccessful login attempts. Try again shortly.", 429)
        response[0].headers["Retry-After"] = str(login_retry_after(email))
        return response
    user_id = authenticate_user(email, password)
    if not user_id:
        record_login_failure(email)
        return _error("AUTHENTICATION_FAILED", "Email or password did not match.", 401)
    record_login_success(email)
    token = secrets.token_urlsafe(32)
    created_at = _now_iso()
    expires_at = (_now() + timedelta(days=API_TOKEN_TTL_DAYS)).isoformat(timespec="seconds")
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO api_tokens (id, user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"api-{uuid.uuid4().hex}", user_id, token_hash(token), expires_at, created_at),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify({"token": token, "token_type": "Bearer", "expires_at": expires_at, "user": _user_payload(row)})


@bp.route("/auth/logout", methods=["POST"])
@_require_token
def logout():
    with db_connect() as conn:
        conn.execute("UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (_now_iso(), g.api_token_id))
    return ("", 204)


@bp.route("/me")
@_require_token
def me():
    return jsonify(_user_payload(g.api_user))


def _recipe_query_filters():
    q = " ".join(request.args.get("q", "").strip().split())
    category = request.args.get("category", "").strip().lower()
    tag = request.args.get("tag", "").strip().lower()
    if len(q) > MAX_API_SEARCH_LENGTH:
        raise ValueError("Search text is too long.")
    if category:
        category = normalize_category(category)
    if tag:
        tag = normalize_tag(tag)[0]
    try:
        page = int(request.args.get("page", "1"))
        page_size = int(request.args.get("page_size", "20"))
    except ValueError as exc:
        raise ValueError("Pagination values must be integers.") from exc
    if page < 1 or page > 100000 or page_size < 1 or page_size > MAX_API_PAGE_SIZE:
        raise ValueError("Pagination values are out of range.")
    return q, category, tag, page, page_size


def _recipe_filter_sql(q: str, category: str, tag: str) -> tuple[str, list[str]]:
    where = ["r.archived_at IS NULL"]
    params: list[str] = []
    if q:
        needle = f"%{q.lower()}%"
        where.append(
            "(lower(r.title) LIKE ? OR lower(r.summary) LIKE ? OR lower(r.ingredients_json) LIKE ? OR lower(r.steps_json) LIKE ? OR lower(owner.nickname) LIKE ?)"
        )
        params.extend([needle] * 5)
    if category:
        where.append("COALESCE(r.category_key, '') = ?")
        params.append(category)
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM recipe_tags rt_filter JOIN tags t_filter ON t_filter.id = rt_filter.tag_id WHERE rt_filter.recipe_id = r.id AND t_filter.normalized_name = ?)"
        )
        params.append(tag)
    return " AND ".join(where), params


@bp.route("/recipes")
@_require_token
def recipes():
    try:
        q, category, tag, page, page_size = _recipe_query_filters()
    except ValueError as exc:
        return _error("VALIDATION_ERROR", str(exc), 422)
    where, params = _recipe_filter_sql(q, category, tag)
    with db_connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS count FROM recipes r JOIN users owner ON owner.id = r.owner_id WHERE {where}",  # nosec B608 - fixed SQL with bound filter values
            params,
        ).fetchone()["count"]
        rows = conn.execute(
            f"""
            SELECT r.*, owner.nickname AS owner_nickname
            FROM recipes r JOIN users owner ON owner.id = r.owner_id
            WHERE {where}
            ORDER BY r.updated_at DESC, r.id ASC
            LIMIT ? OFFSET ?
            """,  # nosec B608 - fixed SQL with bound filter values
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        data = [_recipe_payload(row, conn) for row in rows]
    total_pages = max(1, (total + page_size - 1) // page_size)
    return jsonify({"data": data, "pagination": {"page": page, "page_size": page_size, "total_items": total, "total_pages": total_pages}})


@bp.route("/recipes/<recipe_id>")
@_require_token
def recipe(recipe_id: str):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT r.*, owner.nickname AS owner_nickname FROM recipes r JOIN users owner ON owner.id = r.owner_id WHERE r.id = ? AND r.archived_at IS NULL",
            (recipe_id,),
        ).fetchone()
        if not row:
            return _error("NOT_FOUND", "Recipe not found.", 404)
        return jsonify(_recipe_payload(row, conn))


@bp.route("/recipes/<recipe_id>/image")
@_require_token
def recipe_image(recipe_id: str):
    with db_connect() as conn:
        row = conn.execute("SELECT image_filename FROM recipes WHERE id = ? AND archived_at IS NULL", (recipe_id,)).fetchone()
    if not row or not row["image_filename"]:
        return _error("NOT_FOUND", "Recipe image not found.", 404)
    return send_from_directory(_config.RECIPE_IMAGE_DIR, row["image_filename"])


@bp.route("/recipes", methods=["POST"])
@_require_token
def create_api_recipe():
    payload = _json_body()
    if not payload:
        return _error("VALIDATION_ERROR", "A JSON recipe object is required.", 422)
    ingredients = payload.get("ingredients")
    steps = payload.get("steps")
    tags = payload.get("tags", [])
    if not isinstance(ingredients, list) or not isinstance(steps, list) or not isinstance(tags, list):
        return _error("VALIDATION_ERROR", "Ingredients, steps, and tags must be arrays.", 422)
    if any(not isinstance(item, str) or not item.strip() for item in [*ingredients, *steps, *tags]):
        return _error("VALIDATION_ERROR", "Recipe arrays must contain non-empty strings.", 422)
    if len(tags) > MAX_API_TAGS:
        return _error("VALIDATION_ERROR", "Too many tags.", 422)
    form = {
        "title": payload.get("title", "") if isinstance(payload.get("title", ""), str) else "",
        "summary": payload.get("summary", "") if isinstance(payload.get("summary", ""), str) else "",
        "prep_time": payload.get("prep_time", "") if isinstance(payload.get("prep_time", ""), str) else "",
        "servings": payload.get("servings", "") if isinstance(payload.get("servings", ""), str) else "",
        "ingredients": "\n".join(ingredients),
        "steps": "\n".join(steps),
        "category": payload.get("category", "") if isinstance(payload.get("category", ""), str) else "",
        "tags": ", ".join(tags),
    }
    try:
        recipe_id = create_recipe(g.api_user["id"], form)
    except (KeyError, TypeError, ValueError) as exc:
        return _error("VALIDATION_ERROR", str(exc), 422)
    with db_connect() as conn:
        row = conn.execute(
            "SELECT r.*, owner.nickname AS owner_nickname FROM recipes r JOIN users owner ON owner.id = r.owner_id WHERE r.id = ?",
            (recipe_id,),
        ).fetchone()
        response = _recipe_payload(row, conn)
    return jsonify(response), 201


@bp.route("/categories")
@_require_token
def categories():
    init_db()
    with db_connect() as conn:
        data = [
            {"key": row["category_key"], "name": row["display_name"]}
            for row in conn.execute("SELECT category_key, display_name FROM categories ORDER BY display_name")
        ]
    return jsonify({"data": data})


@bp.route("/tags")
@_require_token
def tags():
    init_db()
    with db_connect() as conn:
        data = [
            {"key": row["normalized_name"], "name": row["display_name"]}
            for row in conn.execute("SELECT normalized_name, display_name FROM tags ORDER BY normalized_name LIMIT 500")
        ]
    return jsonify({"data": data})
