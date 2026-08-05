from __future__ import annotations

import json
import os
import sqlite3
import uuid

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from . import config as _config
from .config import *
from .db import *
from .domain import *
from .sync_service import *
from .security import login_allowed, login_retry_after, record_login_failure, record_login_success, rotate_session

bp = Blueprint("main", __name__)


@bp.route("/health")
def health():
    init_db()
    with db_connect() as conn:
        conn.execute("SELECT 1").fetchone()
    return jsonify({"status": "ok", "database": "ok", "schema_version": schema_version(DB_FILE)})


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(_config.UPLOAD_DIR, filename)


@bp.route("/")
def index():
    search = request.args.get("q", "")
    category = request.args.get("category", "")
    tag = request.args.get("tag", "")
    data = load_data(search=search, category=category, tag=tag)
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    inventory = user.get("inventory", []) if user else []
    recipes = [decorate_recipe(data, recipe, inventory) for recipe in data["recipes"]]
    suggestions = sorted(recipes, key=lambda item: (item["match_count"], item["average_rating"]), reverse=True)[:3]
    return render_template(
        "index.html",
        title=APP_TITLE,
        user=user,
        recipes=recipes,
        suggestions=suggestions,
        categories=data["categories"],
        available_tags=data["tags"],
        filters=data["filters"],
        active="cards",
    )


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    data = load_data()
    user = current_user(data)
    if profile_ready(user):
        return redirect(url_for("index"))
    if user:
        return redirect(url_for("profile_setup"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        mode = request.form.get("mode", "signup")
        if mode == "login":
            if not login_allowed(email):
                from flask import abort
                response = abort(429, description="Too many unsuccessful login attempts. Try again shortly.")
            user_id = authenticate_user(email, password)
            if not user_id:
                record_login_failure(email)
                flash("Email or password did not match.", "error")
                return redirect(url_for("signup"))
            record_login_success(email)
            rotate_session(user_id)
            data = load_data()
            user = current_user(data)
            return redirect(url_for("index") if profile_ready(user) else url_for("profile_setup"))
        if len(password) < 8 or len(password) > 128:
            flash("Password must be between 8 and 128 characters.", "error")
            return redirect(url_for("signup"))
        try:
            user_id = create_account(email, password)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("signup"))
        except sqlite3.IntegrityError:
            flash("An account already exists for that email. Sign in instead.", "error")
            return redirect(url_for("signup"))
        session.clear()
        session["user_id"] = user_id
        session["csrf_token"] = __import__("secrets").token_urlsafe(32)
        session.permanent = True
        flash("Account created. Set up your profile next.")
        return redirect(url_for("profile_setup"))
    return render_template("signup.html", title=APP_TITLE, user=user, active="profile")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("signup"))


@bp.route("/profiles", methods=["GET", "POST"])
@bp.route("/profile/setup", methods=["GET", "POST"])
def profile_setup():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        if not nickname:
            flash("Choose a profile nickname.", "error")
            return redirect(url_for("profile_setup"))
        try:
            avatar = save_avatar(request.files.get("avatar"))
            update_profile(user["id"], nickname, request.form.get("bio", "").strip(), avatar)
        except ValueError as exc:
            return render_template("profile_setup.html", title=APP_TITLE, user=user, active="profile", error=str(exc)), 400
        flash("Profile saved.")
        return redirect(url_for("index"))
    return render_template("profile_setup.html", title=APP_TITLE, user=user, active="profile")


@bp.route("/recipes/new", methods=["GET", "POST"])
def new_recipe():
    data = load_data()
    user = current_user(data)
    if not user:
        flash("Create a profile before adding recipes.", "error")
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    if request.method == "POST":
        try:
            recipe_id = create_recipe(user["id"], request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("recipe_form.html", title=APP_TITLE, user=user, recipe=None, categories=data["categories"], active="new"), 422
        flash("Recipe card added to the box.")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    return render_template("recipe_form.html", title=APP_TITLE, user=user, recipe=None, categories=data["categories"], active="new")


@bp.route("/recipes/<recipe_id>")
def recipe_detail(recipe_id):
    data = load_data()
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not recipe:
        flash("Recipe not found.", "error")
        return redirect(url_for("index"))
    decorated = decorate_recipe(data, recipe, user.get("inventory", []) if user else [])
    comments = []
    for comment in decorated.get("comments", []):
        enriched = dict(comment)
        enriched["user"] = next((profile for profile in data["users"] if profile["id"] == comment["user_id"]), {"name": "Guest"})
        comments.append(enriched)
    decorated["comments"] = comments
    return render_template("detail.html", title=APP_TITLE, user=user, recipe=decorated, active="cards")


@bp.route("/recipes/<recipe_id>/edit", methods=["GET", "POST"])
def edit_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not recipe:
        flash("Recipe not found.", "error")
        return redirect(url_for("index"))
    if not user or recipe["owner_id"] != user["id"]:
        flash("Only the recipe owner can edit this card.", "error")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    if request.method == "POST":
        try:
            update_recipe(recipe_id, request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("recipe_form.html", title=APP_TITLE, user=user, recipe=recipe, categories=data["categories"], active="cards"), 422
        flash("Recipe card updated.")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    return render_template("recipe_form.html", title=APP_TITLE, user=user, recipe=recipe, categories=data["categories"], active="cards")


@bp.route("/recipes/<recipe_id>/rate", methods=["POST"])
def rate_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if recipe:
        score = max(1, min(5, int(request.form["score"])))
        save_rating(recipe_id, user["id"], score)
        flash("Rating saved.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))


@bp.route("/recipes/<recipe_id>/comment", methods=["POST"])
def comment_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    body = request.form.get("body", "").strip()
    if recipe and body:
        try:
            create_comment(recipe_id, user["id"], body)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("recipe_detail", recipe_id=recipe_id))
        flash("Comment added.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))


@bp.route("/inventory", methods=["GET", "POST"])
def inventory():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    if request.method == "POST":
        try:
            update_inventory(user["id"], split_ingredients(request.form.get("inventory", "")))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("inventory"))
        flash("Food stock updated.")
        return redirect(url_for("inventory"))
    inventory_items = user.get("inventory", [])
    generated_now, generated_one = generated_recipe_ideas(inventory_items)
    suggestions = [
        decorate_recipe(data, recipe, inventory_items)
        for recipe in data["recipes"]
    ]
    suggestions = sorted(suggestions, key=lambda item: (item["match_count"], item["average_rating"]), reverse=True)
    return render_template(
        "inventory.html",
        title=APP_TITLE,
        user=user,
        suggestions=suggestions,
        generated_now=generated_now,
        generated_one=generated_one,
        active="inventory",
    )


@bp.route("/inventory/generated/save", methods=["POST"])
def save_generated():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))

    generated_now, generated_one = generated_recipe_ideas(user.get("inventory", []))
    candidates = generated_now + generated_one
    idea_id = request.form.get("idea_id", "")
    kind = request.form.get("kind", "")
    idea = next((item for item in candidates if item["id"] == idea_id and item["kind"] == kind), None)
    if not idea:
        flash("That generated recipe is no longer available. Update your stock and try again.", "error")
        return redirect(url_for("inventory"))

    recipe_id = save_generated_recipe(user["id"], idea)
    flash("Generated recipe saved to your recipe box.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))

@bp.route("/api/sync/manifest")
def sync_manifest():
    auth_error = require_peer_auth()
    if auth_error:
        return auth_error
    if request.content_length and request.content_length > MAX_SYNC_BODY_BYTES:
        return sync_json_error("PAYLOAD_TOO_LARGE", "The request is too large.", 413)
    since = request.args.get("since", "").strip()
    init_db()
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM recipes ORDER BY updated_at DESC LIMIT 500").fetchall()
        recipes = []
        for row in rows:
            if since and row["updated_at"] <= since:
                continue
            recipes.append(sync_recipe_payload(row))
    return jsonify({"installation_id": sync_installation_id(), "recipes": recipes})


@bp.route("/api/sync/recipes/<recipe_id>")
def sync_recipe(recipe_id):
    auth_error = require_peer_auth()
    if auth_error:
        return auth_error
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        return sync_json_error("NOT_FOUND", "Recipe not found.", 404)
    return jsonify(sync_recipe_payload(row))


@bp.route("/sync")
def sync_dashboard():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    with db_connect() as conn:
        peers = [dict(row) for row in conn.execute("SELECT id, name, url, enabled, last_sync_at FROM sync_peers ORDER BY name")]
        history = []
        for row in conn.execute("SELECT status, started_at, summary_json FROM sync_history ORDER BY started_at DESC LIMIT 6"):
            summary = json.loads(row["summary_json"])
            history.append({**dict(row), "summary_label": f"{summary.get('new', 0)} new · {summary.get('update', 0)} updated · {summary.get('unchanged', 0)} unchanged · {summary.get('conflict', 0)} conflicts · {summary.get('imported', 0)} imported"})
        conflicts = [dict(row) for row in conn.execute("SELECT id, recipe_id FROM sync_conflicts WHERE status = 'open' ORDER BY created_at")]
    return render_template("sync.html", title=APP_TITLE, user=user, peers=peers, history=history, conflicts=conflicts, preview=session.pop("sync_preview", None), active="sync")


@bp.route("/sync/peers", methods=["POST"])
def add_sync_peer():
    data = load_data(); user = current_user(data)
    if not user: return redirect(url_for("signup"))
    name, url, token = request.form.get("name", "").strip(), request.form.get("url", "").strip().rstrip("/"), request.form.get("token", "").strip()
    if not name or len(name) > 80 or not valid_peer_url(url) or not token or len(token) > 500:
        flash("Enter a peer name, a valid HTTP(S) URL, and its shared token.", "error")
        return redirect(url_for("sync_dashboard"))
    with db_connect() as conn:
        conn.execute("INSERT INTO sync_peers (id, name, url, token, created_at) VALUES (?, ?, ?, ?, ?)", (f"peer-{uuid.uuid4().hex}", name, url, token, now_iso()))
    flash("Trusted peer added.")
    return redirect(url_for("sync_dashboard"))


@bp.route("/sync/peers/<peer_id>/toggle", methods=["POST"])
def toggle_sync_peer(peer_id):
    if not local_session_user(): return redirect(url_for("signup"))
    with db_connect() as conn: conn.execute("UPDATE sync_peers SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id = ?", (peer_id,))
    return redirect(url_for("sync_dashboard"))


@bp.route("/sync/peers/<peer_id>/delete", methods=["POST"])
def delete_sync_peer(peer_id):
    if not local_session_user(): return redirect(url_for("signup"))
    with db_connect() as conn: conn.execute("DELETE FROM sync_peers WHERE id = ?", (peer_id,))
    flash("Peer removed.")
    return redirect(url_for("sync_dashboard"))


@bp.route("/api/sync/preview", methods=["POST"])
def api_sync_preview():
    auth_error = require_local_json_auth()
    if auth_error: return auth_error
    data = request.get_json(silent=True) or {}
    try: return jsonify(preview_peer_changes(str(data["peer_id"])))
    except KeyError: return sync_json_error("VALIDATION_ERROR", "peer_id is required.", 422)
    except (ValueError, RuntimeError) as exc: return sync_json_error("SYNC_FAILED", str(exc), 502)


@bp.route("/sync/peers/<peer_id>/preview", methods=["POST"])
def preview_sync_peer(peer_id):
    if not local_session_user(): return redirect(url_for("signup"))
    try: session["sync_preview"] = preview_peer_changes(peer_id); flash("Preview ready. Review the counts before syncing.")
    except (ValueError, RuntimeError) as exc: flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))


@bp.route("/api/sync/run", methods=["POST"])
def api_sync_run():
    auth_error = require_local_json_auth()
    if auth_error: return auth_error
    data = request.get_json(silent=True) or {}
    try: return jsonify(run_peer_sync(str(data["peer_id"])))
    except KeyError: return sync_json_error("VALIDATION_ERROR", "peer_id is required.", 422)
    except (ValueError, RuntimeError) as exc: return sync_json_error("SYNC_FAILED", str(exc), 502)


@bp.route("/sync/peers/<peer_id>/run", methods=["POST"])
def run_sync_peer(peer_id):
    if not local_session_user(): return redirect(url_for("signup"))
    try:
        result = run_peer_sync(peer_id); flash(f"Sync complete: {result['summary']['imported']} imported, {result['summary']['conflict']} conflicts.", "error" if result["status"] == "conflict" else "")
    except (ValueError, RuntimeError) as exc: flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))


@bp.route("/api/sync/conflicts/<conflict_id>/resolve", methods=["POST"])
def api_resolve_sync_conflict(conflict_id):
    auth_error = require_local_json_auth()
    if auth_error: return auth_error
    try: return jsonify(resolve_sync_conflict_action(conflict_id, (request.get_json(silent=True) or {}).get("resolution", "")))
    except ValueError as exc: return sync_json_error("VALIDATION_ERROR", str(exc), 422)


@bp.route("/sync/conflicts/<conflict_id>/resolve", methods=["POST"])
def resolve_sync_conflict(conflict_id):
    if not local_session_user(): return redirect(url_for("signup"))
    try: resolve_sync_conflict_action(conflict_id, request.form.get("resolution", "")); flash("Conflict resolved.")
    except ValueError as exc: flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))
