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
from .policies import can_delete_comment, can_edit_comment, can_hide_comment, can_unhide_comment


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
        "visibility": row["visibility"] if "visibility" in row.keys() else "shared_epn",
        "favorite": bool(row["favorite"]) if "favorite" in row.keys() else False,
        "archived": bool(row["archived"]) if "archived" in row.keys() else False,
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


def _validate_signup(payload: dict | None) -> tuple[str, str, str] | None:
    if not payload or not all(isinstance(payload.get(field), str) for field in ("email", "password", "nickname")):
        return None
    return payload["email"].strip().lower(), payload["password"], payload["nickname"]


def _issue_api_token(user_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    expires_at = (_now() + timedelta(days=API_TOKEN_TTL_DAYS)).isoformat(timespec="seconds")
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO api_tokens (id, user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"api-{uuid.uuid4().hex}", user_id, token_hash(token), expires_at, _now_iso()),
        )
    return token, expires_at


@bp.route("/auth/signup", methods=["POST"])
def signup():
    payload = _json_body()
    candidate_email = payload.get("email", "") if isinstance(payload, dict) and isinstance(payload.get("email"), str) else ""
    candidate_email = candidate_email.strip().lower()
    if candidate_email and not login_allowed(candidate_email):
        response = _error("RATE_LIMITED", "Too many signup attempts. Try again shortly.", 429)
        response[0].headers["Retry-After"] = str(login_retry_after(candidate_email))
        return response
    credentials = _validate_signup(payload)
    if not credentials:
        if candidate_email:
            record_login_failure(candidate_email)
        return _error("VALIDATION_ERROR", "Email, password, and nickname are required.", 422)
    email, password, nickname = credentials
    try:
        init_db()
        user_id = create_account(email, password, nickname)
    except ValueError as exc:
        record_login_failure(email)
        return _error("VALIDATION_ERROR", str(exc), 422)
    except sqlite3.IntegrityError:
        return _error("ACCOUNT_EXISTS", "An account with that email already exists.", 409)
    token, expires_at = _issue_api_token(user_id)
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    record_login_success(email)
    return jsonify({"token": token, "token_type": "Bearer", "expires_at": expires_at, "user": _user_payload(row)}), 201


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
    where = [
        "(r.owner_id = ? OR r.visibility = 'shared_epn')",
        "COALESCE((SELECT is_archived FROM recipe_user_state s WHERE s.user_id = ? AND s.recipe_id = r.id), 0) = 0",
    ]
    params: list[str] = [g.api_user["id"], g.api_user["id"]]
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
            SELECT r.*, owner.nickname AS owner_nickname,
                   EXISTS (SELECT 1 FROM favorites f WHERE f.recipe_id=r.id AND f.user_id=?) AS favorite,
                   COALESCE((SELECT is_archived FROM recipe_user_state s WHERE s.recipe_id=r.id AND s.user_id=?),0) AS archived
            FROM recipes r JOIN users owner ON owner.id = r.owner_id
            WHERE {where}
            ORDER BY r.updated_at DESC, r.id ASC
            LIMIT ? OFFSET ?
            """,  # nosec B608 - fixed SQL with bound filter values
            [g.api_user["id"], g.api_user["id"], *params, page_size, (page - 1) * page_size],
        ).fetchall()
        data = [_recipe_payload(row, conn) for row in rows]
    total_pages = max(1, (total + page_size - 1) // page_size)
    return jsonify({"data": data, "pagination": {"page": page, "page_size": page_size, "total_items": total, "total_pages": total_pages}})


@bp.route("/recipes/<recipe_id>")
@_require_token
def recipe(recipe_id: str):
    with db_connect() as conn:
        archived_state = conn.execute(
            "SELECT is_archived FROM recipe_user_state WHERE recipe_id=? AND user_id=?",
            (recipe_id, g.api_user["id"]),
        ).fetchone()
        if archived_state and archived_state["is_archived"]:
            return _error("NOT_FOUND", "Recipe not found.", 404)
        row = conn.execute(
            "SELECT r.*, owner.nickname AS owner_nickname, EXISTS (SELECT 1 FROM favorites f WHERE f.recipe_id=r.id AND f.user_id=?) AS favorite, COALESCE(state.is_archived,0) AS archived FROM recipes r JOIN users owner ON owner.id = r.owner_id LEFT JOIN recipe_user_state state ON state.recipe_id=r.id AND state.user_id=? WHERE r.id = ? AND (r.owner_id = ? OR r.visibility = 'shared_epn') AND COALESCE(state.is_archived,0)=0",
            (g.api_user["id"], g.api_user["id"], recipe_id, g.api_user["id"]),
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
        "visibility": payload.get("visibility", "shared_epn"),
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
        if row["visibility"] == "shared_epn":
            _event(conn, "shared_recipe_created", recipe_id)
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
    with db_connect() as conn:
        data = [
            {"key": row["normalized_name"], "name": row["display_name"]}
            for row in conn.execute("SELECT normalized_name, display_name FROM tags ORDER BY normalized_name LIMIT 500")
        ]
    return jsonify({"data": data})


# Community foundation endpoints. All authorization is evaluated at the API boundary.
def _event(conn, kind, recipe_id=None, payload=None):
    conn.execute(
        "INSERT INTO activity_events(id,user_id,event_type,recipe_id,payload_json,created_at) VALUES(?,?,?,?,?,?)",
        (f"event-{uuid.uuid4().hex}", g.api_user["id"], kind, recipe_id, json.dumps(payload or {}), _now_iso()),
    )


@bp.route("/recipes/<recipe_id>/favorite", methods=["POST", "DELETE"])
@_require_token
def api_favorite(recipe_id):
    with db_connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM recipes WHERE id=? AND (owner_id=? OR visibility='shared_epn')", (recipe_id, g.api_user["id"])
        ).fetchone():
            return _error("NOT_FOUND", "Recipe not found.", 404)
        if request.method == "POST":
            conn.execute(
                "INSERT OR IGNORE INTO favorites(user_id,recipe_id,created_at) VALUES(?,?,?)", (g.api_user["id"], recipe_id, _now_iso())
            )
        else:
            conn.execute("DELETE FROM favorites WHERE user_id=? AND recipe_id=?", (g.api_user["id"], recipe_id))
    return ("", 204)


@bp.route("/recipes/<recipe_id>/archive", methods=["POST", "DELETE"])
@_require_token
def api_archive(recipe_id):
    set_recipe_archived(recipe_id, request.method == "POST", g.api_user["id"])
    return ("", 204)


@bp.route("/recipes/<recipe_id>", methods=["PATCH"])
@_require_token
def api_update_recipe(recipe_id):
    payload = _json_body()
    if payload is None or not payload:
        return _error("VALIDATION_ERROR", "A JSON recipe update object is required.", 422)
    editable_fields = {"title", "summary", "prep_time", "servings", "ingredients", "steps", "category", "tags", "visibility"}
    unknown_fields = sorted(set(payload) - editable_fields)
    if unknown_fields:
        return _error("VALIDATION_ERROR", "Unsupported recipe fields.", 422, {"fields": unknown_fields})
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
        if not row:
            return _error("NOT_FOUND", "Recipe not found.", 404)
        if row["owner_id"] != g.api_user["id"]:
            return _error("FORBIDDEN", "Only the recipe owner can update it.", 403)
        current_tags = recipe_tag_payload(conn, recipe_id)
        current = {
            "title": row["title"],
            "summary": row["summary"],
            "prep_time": row["prep_time"],
            "servings": row["servings"],
            "ingredients": "\n".join(json.loads(row["ingredients_json"])),
            "steps": "\n".join(json.loads(row["steps_json"])),
            "category": row["category_key"] or "",
            "tags": ", ".join(tag["display_name"] for tag in current_tags),
            "visibility": row["visibility"] if "visibility" in row.keys() else "shared_epn",
        }
        for field in editable_fields.intersection(payload):
            value = payload[field]
            if field in {"ingredients", "steps", "tags"}:
                if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                    return _error("VALIDATION_ERROR", f"{field} must be an array of non-empty strings.", 422)
                current[field] = "\n".join(value) if field != "tags" else ", ".join(value)
            elif not isinstance(value, str):
                return _error("VALIDATION_ERROR", f"{field} must be a string.", 422)
            else:
                current[field] = value
        try:
            update_recipe(recipe_id, current)
        except (KeyError, TypeError, ValueError) as exc:
            return _error("VALIDATION_ERROR", str(exc), 422)
        row = conn.execute(
            "SELECT r.*, owner.nickname AS owner_nickname, EXISTS (SELECT 1 FROM favorites f WHERE f.recipe_id=r.id AND f.user_id=?) AS favorite, COALESCE(state.is_archived,0) AS archived FROM recipes r JOIN users owner ON owner.id=r.owner_id LEFT JOIN recipe_user_state state ON state.recipe_id=r.id AND state.user_id=? WHERE r.id=?",
            (g.api_user["id"], g.api_user["id"], recipe_id),
        ).fetchone()
        return jsonify(_recipe_payload(row, conn))


@bp.route("/collections", methods=["GET", "POST"])
@_require_token
def api_collections():
    with db_connect() as conn:
        if request.method == "GET":
            rows = conn.execute(
                "SELECT * FROM collections WHERE owner_id=? OR visibility='shared_epn' ORDER BY name", (g.api_user["id"],)
            ).fetchall()
            return jsonify({"data": [dict(r) for r in rows]})
        body = _json_body() or {}
        name = str(body.get("name", "")).strip()
        visibility = body.get("visibility", "private")
        if not name or len(name) > 120 or visibility not in {"private", "shared_epn"}:
            return _error("VALIDATION_ERROR", "Collection name or visibility is invalid.", 422)
        cid = f"col-{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO collections(id,owner_id,name,created_at,updated_at,visibility) VALUES(?,?,?,?,?,?)",
            (cid, g.api_user["id"], name, _now_iso(), _now_iso(), visibility),
        )
        if visibility == "shared_epn":
            _event(conn, "shared_collection_created", payload={"collection_id": cid})
        return jsonify(dict(conn.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone())), 201


@bp.route("/collections/<collection_id>", methods=["GET", "PATCH", "DELETE"])
@_require_token
def api_collection(collection_id):
    with db_connect() as conn:
        collection = conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
        if not collection or (collection["owner_id"] != g.api_user["id"] and collection["visibility"] != "shared_epn"):
            return _error("NOT_FOUND", "Collection not found.", 404)
        if request.method == "PATCH":
            if collection["owner_id"] != g.api_user["id"]:
                return _error("FORBIDDEN", "Only the collection owner can update it.", 403)
            body = _json_body() or {}
            name = str(body.get("name", collection["name"])).strip()
            visibility = body.get("visibility", collection["visibility"])
            if not name or len(name) > 120 or visibility not in {"private", "shared_epn"}:
                return _error("VALIDATION_ERROR", "Collection name or visibility is invalid.", 422)
            conn.execute(
                "UPDATE collections SET name=?, visibility=?, updated_at=? WHERE id=?", (name, visibility, _now_iso(), collection_id)
            )
            collection = conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
        elif request.method == "DELETE":
            if collection["owner_id"] != g.api_user["id"]:
                return _error("FORBIDDEN", "Only the collection owner can delete it.", 403)
            if conn.execute("SELECT 1 FROM collection_recipes WHERE collection_id=? LIMIT 1", (collection_id,)).fetchone():
                return _error("CONFLICT", "Only empty collections can be deleted.", 409)
            conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
            return ("", 204)
        recipes = conn.execute(
            "SELECT r.* FROM recipes r JOIN collection_recipes cr ON cr.recipe_id=r.id WHERE cr.collection_id=? ORDER BY cr.position, cr.created_at",
            (collection_id,),
        ).fetchall()
        payload = dict(collection)
        payload["recipes"] = [_recipe_payload(row, conn) for row in recipes]
        return jsonify(payload)


@bp.route("/collections/<collection_id>/recipes", methods=["POST"])
@bp.route("/collections/<collection_id>/recipes/<recipe_id>", methods=["POST", "DELETE"])
@_require_token
def api_collection_recipe(collection_id, recipe_id=None):
    if recipe_id is None:
        recipe_id = (_json_body() or {}).get("recipe_id")
        if not isinstance(recipe_id, str) or not recipe_id:
            return _error("VALIDATION_ERROR", "recipe_id is required.", 422)
    with db_connect() as conn:
        collection = conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
        recipe = conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
        if not collection or not recipe:
            return _error("NOT_FOUND", "Collection or recipe not found.", 404)
        if collection["owner_id"] != g.api_user["id"]:
            return _error("FORBIDDEN", "Only the collection owner can manage it.", 403)
        if request.method == "POST":
            if collection["visibility"] == "shared_epn" and recipe["visibility"] != "shared_epn":
                return _error("FORBIDDEN", "Private recipes cannot enter shared collections.", 403)
            conn.execute(
                "INSERT OR IGNORE INTO collection_recipes(collection_id,recipe_id,created_at) VALUES(?,?,?)",
                (collection_id, recipe_id, _now_iso()),
            )
            if collection["visibility"] == "shared_epn":
                _event(conn, "recipe_added_to_shared_collection", recipe_id, {"collection_id": collection_id})
        else:
            conn.execute("DELETE FROM collection_recipes WHERE collection_id=? AND recipe_id=?", (collection_id, recipe_id))
    return ("", 204)


@bp.route("/recipes/<recipe_id>/comments", methods=["GET", "POST"])
@_require_token
def api_comments(recipe_id):
    with db_connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM recipes WHERE id=? AND (owner_id=? OR visibility='shared_epn')", (recipe_id, g.api_user["id"])
        ).fetchone():
            return _error("NOT_FOUND", "Recipe not found.", 404)
        if request.method == "GET":
            rows = conn.execute(
                "SELECT * FROM comments WHERE recipe_id=? AND deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at", (recipe_id,)
            ).fetchall()
            return jsonify({"data": [dict(r) for r in rows]})
        try:
            comment_id = create_comment(recipe_id, g.api_user["id"], (_json_body() or {}).get("body", ""))
        except (ValueError, TypeError) as exc:
            return _error("VALIDATION_ERROR", str(exc), 422)
        row = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        _event(conn, "comment_added", recipe_id, {"comment_id": comment_id})
        return jsonify(dict(row)), 201


@bp.route("/comments/<comment_id>", methods=["PATCH", "DELETE"])
@_require_token
def api_comment(comment_id):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT c.*, r.owner_id AS recipe_owner, r.visibility FROM comments c JOIN recipes r ON r.id=c.recipe_id WHERE c.id=?",
            (comment_id,),
        ).fetchone()
        if not row:
            return _error("NOT_FOUND", "Comment not found.", 404)
        recipe = {"owner_id": row["recipe_owner"], "visibility": row["visibility"]}
        comment = dict(row)
        if request.method == "PATCH":
            if not can_edit_comment(comment, recipe, g.api_user["id"]):
                return _error("FORBIDDEN", "Only the author can edit this comment.", 403)
            body = (_json_body() or {}).get("body", "")
            if not isinstance(body, str) or not body.strip() or len(body.strip()) > MAX_COMMENT_LENGTH:
                return _error("VALIDATION_ERROR", "Comment length is invalid.", 422)
            conn.execute("UPDATE comments SET body=?, updated_at=? WHERE id=?", (body.strip(), _now_iso(), comment_id))
            return jsonify(dict(conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()))
        if not can_delete_comment(comment, recipe, g.api_user["id"]):
            return _error("FORBIDDEN", "Only the comment author can delete this comment.", 403)
        conn.execute("UPDATE comments SET deleted_at=?, updated_at=? WHERE id=?", (_now_iso(), _now_iso(), comment_id))
    return ("", 204)


def _comment_moderation_row(conn, comment_id: str):
    return conn.execute(
        "SELECT c.*, r.owner_id AS recipe_owner, r.visibility FROM comments c JOIN recipes r ON r.id=c.recipe_id WHERE c.id=? AND (r.owner_id=? OR r.visibility='shared_epn')",
        (comment_id, g.api_user["id"]),
    ).fetchone()


@bp.route("/comments/<comment_id>/hide", methods=["POST"])
@_require_token
def api_hide_comment(comment_id: str):
    with db_connect() as conn:
        row = _comment_moderation_row(conn, comment_id)
        if not row:
            return _error("NOT_FOUND", "Comment not found.", 404)
        recipe = {"owner_id": row["recipe_owner"], "visibility": row["visibility"]}
        if row["hidden_at"]:
            if row["recipe_owner"] == g.api_user["id"]:
                return ("", 204)
            return _error("FORBIDDEN", "Only the recipe owner can hide this comment.", 403)
        if not can_hide_comment(dict(row), recipe, g.api_user["id"]):
            return _error("FORBIDDEN", "Only the recipe owner can hide this comment.", 403)
        stamp = _now_iso()
        conn.execute(
            "UPDATE comments SET hidden_at=?, hidden_by_user_id=?, updated_at=? WHERE id=?", (stamp, g.api_user["id"], stamp, comment_id)
        )
    return ("", 204)


@bp.route("/comments/<comment_id>/unhide", methods=["POST"])
@_require_token
def api_unhide_comment(comment_id: str):
    with db_connect() as conn:
        row = _comment_moderation_row(conn, comment_id)
        if not row:
            return _error("NOT_FOUND", "Comment not found.", 404)
        recipe = {"owner_id": row["recipe_owner"], "visibility": row["visibility"]}
        if not row["hidden_at"]:
            if row["recipe_owner"] == g.api_user["id"]:
                return ("", 204)
            return _error("FORBIDDEN", "Only the recipe owner can unhide this comment.", 403)
        if not can_unhide_comment(dict(row), recipe, g.api_user["id"]):
            return _error("FORBIDDEN", "Only the recipe owner can unhide this comment.", 403)
        conn.execute("UPDATE comments SET hidden_at=NULL, hidden_by_user_id=NULL, updated_at=? WHERE id=?", (_now_iso(), comment_id))
    return ("", 204)


@bp.route("/activity")
@_require_token
def api_activity():
    page = max(1, request.args.get("page", 1, type=int))
    page_size = min(50, max(1, request.args.get("page_size", 20, type=int)))
    offset = (page - 1) * page_size
    with db_connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM activity_events e LEFT JOIN recipes r ON r.id=e.recipe_id WHERE (e.recipe_id IS NULL OR r.visibility='shared_epn' OR r.owner_id=?) AND NOT (e.event_type='comment_added' AND EXISTS (SELECT 1 FROM comments c WHERE c.id=json_extract(e.payload_json, '$.comment_id') AND (c.deleted_at IS NOT NULL OR c.hidden_at IS NOT NULL)))",
            (g.api_user["id"],),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT e.* FROM activity_events e LEFT JOIN recipes r ON r.id=e.recipe_id WHERE (e.recipe_id IS NULL OR r.visibility='shared_epn' OR r.owner_id=?) AND NOT (e.event_type='comment_added' AND EXISTS (SELECT 1 FROM comments c WHERE c.id=json_extract(e.payload_json, '$.comment_id') AND (c.deleted_at IS NOT NULL OR c.hidden_at IS NOT NULL))) ORDER BY e.created_at DESC LIMIT ? OFFSET ?",
            (g.api_user["id"], page_size, offset),
        ).fetchall()
        return jsonify(
            {
                "data": [dict(r) for r in rows],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_items": total,
                    "total_pages": (total + page_size - 1) // page_size,
                },
            }
        )
