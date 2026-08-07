from __future__ import annotations

import json
import os
import sqlite3
import uuid

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for

from . import config as _config
from .config import *
from .db import *
from .domain import *
from .exchange import build_exchange, recipe_fingerprint, validate_exchange_payload
from .sync_service import *
from .security import login_allowed, login_retry_after, record_login_failure, record_login_success, rotate_session
from .policies import can_delete_comment, can_edit_comment, can_hide_comment, can_unhide_comment

bp = Blueprint("main", __name__)


_BETA_FILES = frozenset({"latest.apk", "latest.sha256", "INSTALL.md", "CHANGELOG.md", "release.json"})


def _beta_metadata() -> dict:
    metadata_path = _config.PUBLIC_BETA_DIR / "release.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        abort(404)
    payload: dict = {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        abort(404)
    if payload.get("apk") != "latest.apk" or payload.get("release_notes") != "CHANGELOG.md":
        abort(404)
    return payload


@bp.get("/beta")
def beta_download_page():
    metadata = _beta_metadata()
    changelog = (_config.PUBLIC_BETA_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    highlights = [line[2:].strip() for line in changelog.splitlines() if line.startswith("- ")][:5]
    return render_template("beta.html", title="Download Android Beta · EPN Recipe Box", user=None, metadata=metadata, highlights=highlights, active="beta")


@bp.get("/beta/<path:filename>")
def beta_artifact(filename: str):
    if filename not in _BETA_FILES or "/" in filename or "\\" in filename or filename.startswith("."):
        abort(404)
    root = _config.PUBLIC_BETA_DIR.resolve()
    path = (_config.PUBLIC_BETA_DIR / filename)
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
        abort(404)
    metadata = _beta_metadata()
    if filename == "latest.apk":
        return send_file(path, mimetype="application/vnd.android.package-archive", as_attachment=True, download_name=f"EPN-Recipe-Box-Private-Beta-{metadata['version_name']}.apk", max_age=0)
    mimetypes = {"release.json": "application/json", "latest.sha256": "text/plain", "INSTALL.md": "text/markdown", "CHANGELOG.md": "text/markdown"}
    return send_file(path, mimetype=mimetypes[filename], as_attachment=False, max_age=0)


@bp.route("/health")
def health():
    init_db()
    with db_connect() as conn:
        conn.execute("SELECT 1").fetchone()
    return jsonify({"status": "ok", "database": "ok", "schema_version": schema_version(DB_FILE)})


@bp.route("/activity")
def activity():
    data = load_data(user_id=session.get("user_id", ""))
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    with db_connect() as conn:
        events = [
            dict(row)
            for row in conn.execute(
                "SELECT e.* FROM activity_events e LEFT JOIN recipes r ON r.id=e.recipe_id WHERE (e.recipe_id IS NULL OR r.visibility='shared_epn' OR r.owner_id=?) AND NOT (e.event_type='comment_added' AND EXISTS (SELECT 1 FROM comments c WHERE c.id=json_extract(e.payload_json, '$.comment_id') AND (c.deleted_at IS NOT NULL OR c.hidden_at IS NOT NULL))) ORDER BY e.created_at DESC LIMIT 50",
                (user["id"],),
            )
        ]
    return render_template("activity.html", title=APP_TITLE, user=user, events=events, active="activity")


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(_config.UPLOAD_DIR, filename)


@bp.route("/recipe-images/<path:filename>")
def recipe_image(filename):
    data = load_data(user_id=session.get("user_id", ""))
    if not current_user(data):
        return redirect(url_for("signup"))
    recipe = next((item for item in data["recipes"] if item.get("image_filename") == filename), None)
    if not recipe:
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Recipe image not found."}}), 404
    return send_from_directory(_config.RECIPE_IMAGE_DIR, filename)


@bp.route("/")
def index():
    search = request.args.get("q", "")
    category = request.args.get("category", "")
    tag = request.args.get("tag", "")
    favorites = request.args.get("favorites", "") == "1"
    archived = request.args.get("archived", "") == "1"
    user_id = session.get("user_id", "")
    data = (
        load_data(search=search, category=category, tag=tag, favorites=favorites, archived=archived, user_id=user_id)
        if user_id
        else {
            "users": [],
            "recipes": [],
            "categories": [],
            "tags": [],
            "filters": {"q": search, "category": category, "tag": tag, "favorites": favorites, "archived": archived},
        }
    )
    community = community_home_data()
    user = current_user(data)
    if user and not profile_ready(user):
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
        community=community,
        active="recipes",
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
        image_meta = {}
        try:
            image_meta = save_recipe_image(request.files.get("image"))
            recipe_id = create_recipe(user["id"], request.form, image_meta=image_meta)
        except ValueError as exc:
            if image_meta:
                remove_recipe_image(image_meta["image_filename"])
            flash(str(exc), "error")
            return render_template(
                "recipe_form.html", title=APP_TITLE, user=user, recipe=None, categories=data["categories"], active="new"
            ), 422
        flash("Recipe card added to the box.")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    return render_template("recipe_form.html", title=APP_TITLE, user=user, recipe=None, categories=data["categories"], active="new")


@bp.route("/recipes/<recipe_id>")
def recipe_detail(recipe_id):
    data = load_data(user_id=session.get("user_id", ""))
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not recipe:
        flash("Recipe not found.", "error")
        return redirect(url_for("index"))
    decorated = decorate_recipe(data, recipe, user.get("inventory", []) if user else [])
    comments = decorated.get("comments", [])
    if user and recipe["owner_id"] == user["id"]:
        comments = list_comments(recipe_id, user["id"], include_hidden=True)
    enriched_comments = []
    for comment in comments:
        enriched = dict(comment)
        enriched["user"] = next((profile for profile in data["users"] if profile["id"] == comment["user_id"]), {"name": "Guest"})
        enriched_comments.append(enriched)
    decorated["comments"] = enriched_comments
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
        image_meta = {}
        old_image = recipe.get("image_filename", "")
        try:
            image_meta = save_recipe_image(request.files.get("image"))
            update_recipe(recipe_id, request.form, image_meta=image_meta)
        except ValueError as exc:
            if image_meta:
                remove_recipe_image(image_meta["image_filename"])
            flash(str(exc), "error")
            return render_template(
                "recipe_form.html", title=APP_TITLE, user=user, recipe=recipe, categories=data["categories"], active="cards"
            ), 422
        if image_meta and old_image:
            remove_recipe_image(old_image)
        flash("Recipe card updated.")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    return render_template("recipe_form.html", title=APP_TITLE, user=user, recipe=recipe, categories=data["categories"], active="cards")


@bp.route("/recipes/<recipe_id>/export")
def export_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not user or not recipe or recipe["owner_id"] != user["id"]:
        return jsonify({"error": {"code": "NOT_FOUND", "message": "Recipe not found."}}), 404
    response = jsonify(build_exchange([recipe], sync_installation_id()))
    response.headers["Content-Disposition"] = f'attachment; filename="recipe-{recipe_id}.json"'
    return response


@bp.route("/recipes/export")
def export_all_recipes():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    recipes = [recipe for recipe in data["recipes"] if recipe["owner_id"] == user["id"]]
    response = jsonify(build_exchange(recipes, sync_installation_id()))
    response.headers["Content-Disposition"] = 'attachment; filename="recipe-box-export.json"'
    return response


def _read_exchange_upload():
    upload = request.files.get("exchange")
    if not upload or not upload.filename:
        raise ValueError("Choose a JSON recipe exchange file.")
    content = upload.stream.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError("The import file is too large.")
    try:
        return validate_exchange_payload(json.loads(content.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("The import file must contain valid UTF-8 JSON.") from exc


def _import_preview(payload: dict, user_id: str) -> dict:
    data = load_data()
    local = {recipe["id"]: recipe for recipe in data["recipes"] if recipe["owner_id"] == user_id}
    items = []
    counts = {key: 0 for key in ("new", "updated", "unchanged", "conflicts", "rejected")}
    for incoming in payload["recipes"]:
        existing = local.get(incoming["id"])
        if not existing:
            status = "new"
        elif recipe_fingerprint(existing) == recipe_fingerprint(incoming):
            status = "unchanged"
        elif (
            existing.get("sync_source_installation_id") == payload["source_installation_id"]
            and incoming["updated_at"] > existing["updated_at"]
        ):
            status = "updated"
        else:
            status = "conflicts"
        counts[status] += 1
        items.append({"id": incoming["id"], "title": incoming["title"], "status": status})
    return {"counts": counts, "items": items}


@bp.route("/recipes/import/preview", methods=["GET", "POST"])
def import_preview():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if request.method == "GET":
        return render_template("import.html", title=APP_TITLE, user=user, active="cards")
    try:
        payload = _read_exchange_upload()
        result = _import_preview(payload, user["id"])
    except ValueError as exc:
        return render_template("import.html", title=APP_TITLE, user=user, error=str(exc), active="cards"), 422
    return render_template(
        "import.html", title=APP_TITLE, user=user, payload=json.dumps(payload, sort_keys=True), result=result, active="cards"
    )


@bp.route("/recipes/import/apply", methods=["POST"])
def import_apply():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    try:
        payload = validate_exchange_payload(json.loads(request.form.get("exchange_json", "")))
    except (ValueError, json.JSONDecodeError):
        return render_template(
            "import.html",
            title=APP_TITLE,
            user=user,
            error="The import preview is invalid or expired. Upload the file again.",
            active="cards",
        ), 422
    result = _import_preview(payload, user["id"])
    action = request.form.get("conflict_action", "reject")
    applied = {key: 0 for key in ("new", "updated", "unchanged", "conflicts", "rejected")}
    for incoming, item in zip(payload["recipes"], result["items"]):
        status = item["status"]
        if status == "new":
            insert_imported_recipe(user["id"], incoming, payload["source_installation_id"])
            applied["new"] += 1
        elif status == "updated":
            update_imported_recipe(incoming["id"], incoming, payload["source_installation_id"])
            applied["updated"] += 1
        elif status == "unchanged":
            applied["unchanged"] += 1
        elif status == "conflicts" and action == "keep_both":
            insert_imported_recipe(user["id"], incoming, payload["source_installation_id"], f"r-import-{uuid.uuid4().hex[:10]}")
            applied["conflicts"] += 1
        else:
            applied["rejected"] += 1
    return render_template(
        "import.html", title=APP_TITLE, user=user, result={"counts": applied, "items": result["items"]}, applied=True, active="cards"
    )


@bp.route("/recipes/<recipe_id>/favorite", methods=["POST"])
def favorite_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not user or not recipe:
        return redirect(url_for("signup"))
    with db_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, recipe_id, created_at) VALUES (?, ?, ?)", (user["id"], recipe_id, now_iso())
        )
    return redirect(request.referrer or url_for("recipe_detail", recipe_id=recipe_id))


@bp.route("/recipes/<recipe_id>/unfavorite", methods=["POST"])
def unfavorite_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    if user:
        with db_connect() as conn:
            conn.execute("DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?", (user["id"], recipe_id))
    return redirect(request.referrer or url_for("recipe_detail", recipe_id=recipe_id))


@bp.route("/recipes/<recipe_id>/archive", methods=["POST"])
def archive_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not user or not recipe or recipe["owner_id"] != user["id"]:
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    set_recipe_archived(recipe_id, True, user["id"])
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO recipe_user_state(user_id, recipe_id, is_archived, archived_at, created_at, updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id, recipe_id) DO UPDATE SET is_archived=1, archived_at=excluded.archived_at, updated_at=excluded.updated_at",
            (user["id"], recipe_id, 1, now_iso(), now_iso(), now_iso()),
        )
    flash("Recipe archived. It remains available in Archived recipes.")
    return redirect(url_for("index"))


@bp.route("/recipes/<recipe_id>/restore", methods=["POST"])
def restore_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not user or not recipe or recipe["owner_id"] != user["id"]:
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    set_recipe_archived(recipe_id, False, user["id"])
    with db_connect() as conn:
        conn.execute(
            "UPDATE recipe_user_state SET is_archived=0, archived_at=NULL, updated_at=? WHERE user_id=? AND recipe_id=?",
            (now_iso(), user["id"], recipe_id),
        )
    flash("Recipe restored to the recipe box.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))


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
            comment_id = create_comment(recipe_id, user["id"], body)
            with db_connect() as conn:
                conn.execute(
                    "INSERT INTO activity_events(id,user_id,event_type,recipe_id,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        f"event-{uuid.uuid4().hex}",
                        user["id"],
                        "comment_added",
                        recipe_id,
                        json.dumps({"comment_id": comment_id}),
                        now_iso(),
                    ),
                )
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("recipe_detail", recipe_id=recipe_id))
        flash("Comment added.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))


def _browser_comment(comment_id: str):
    with db_connect() as conn:
        return conn.execute(
            "SELECT c.*, r.owner_id AS recipe_owner, r.visibility AS recipe_visibility FROM comments c JOIN recipes r ON r.id = c.recipe_id WHERE c.id = ?",
            (comment_id,),
        ).fetchone()


@bp.route("/comments/<comment_id>/edit", methods=["GET", "POST"])
def edit_comment(comment_id: str):
    data = load_data(user_id=session.get("user_id", ""))
    user = current_user(data)
    row = _browser_comment(comment_id)
    recipe = {"owner_id": row["recipe_owner"], "visibility": row["recipe_visibility"]} if row else {}
    comment = dict(row) if row else {}
    if not user or not row or not can_edit_comment(comment, recipe, user["id"]):
        return redirect(url_for("index"))
    if request.method == "POST":
        try:
            update_comment(comment_id, request.form.get("body", ""))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("edit_comment", comment_id=comment_id))
        flash("Comment updated.")
        return redirect(url_for("recipe_detail", recipe_id=row["recipe_id"]))
    return render_template("comment_edit.html", title=APP_TITLE, user=user, comment=comment, active="cards")


@bp.route("/comments/<comment_id>/delete", methods=["POST"])
def delete_comment_browser(comment_id: str):
    data = load_data(user_id=session.get("user_id", ""))
    user = current_user(data)
    row = _browser_comment(comment_id)
    recipe = {"owner_id": row["recipe_owner"], "visibility": row["recipe_visibility"]} if row else {}
    if user and row and can_delete_comment(dict(row), recipe, user["id"]):
        delete_comment(comment_id)
        flash("Comment deleted.")
        return redirect(url_for("recipe_detail", recipe_id=row["recipe_id"]))
    return redirect(url_for("index"))


@bp.route("/comments/<comment_id>/hide", methods=["POST"])
def hide_comment_browser(comment_id: str):
    data = load_data(user_id=session.get("user_id", ""))
    user = current_user(data)
    row = _browser_comment(comment_id)
    recipe = {"owner_id": row["recipe_owner"], "visibility": row["recipe_visibility"]} if row else {}
    if user and row and can_hide_comment(dict(row), recipe, user["id"]):
        hide_comment(comment_id, user["id"])
        flash("Comment hidden from the recipe.")
        return redirect(url_for("recipe_detail", recipe_id=row["recipe_id"]))
    flash("You are not allowed to hide that comment.", "error")
    return redirect(url_for("recipe_detail", recipe_id=row["recipe_id"]) if row else url_for("index"))


@bp.route("/comments/<comment_id>/unhide", methods=["POST"])
def unhide_comment_browser(comment_id: str):
    data = load_data(user_id=session.get("user_id", ""))
    user = current_user(data)
    row = _browser_comment(comment_id)
    recipe = {"owner_id": row["recipe_owner"], "visibility": row["recipe_visibility"]} if row else {}
    if user and row and can_unhide_comment(dict(row), recipe, user["id"]):
        unhide_comment(comment_id)
        flash("Comment is visible again.")
        return redirect(url_for("recipe_detail", recipe_id=row["recipe_id"]))
    flash("You are not allowed to unhide that comment.", "error")
    return redirect(url_for("recipe_detail", recipe_id=row["recipe_id"]) if row else url_for("index"))


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
    suggestions = [decorate_recipe(data, recipe, inventory_items) for recipe in data["recipes"]]
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
            history.append(
                {
                    **dict(row),
                    "summary_label": f"{summary.get('new', 0)} new · {summary.get('update', 0)} updated · {summary.get('unchanged', 0)} unchanged · {summary.get('conflict', 0)} conflicts · {summary.get('imported', 0)} imported",
                }
            )
        conflicts = [
            dict(row) for row in conn.execute("SELECT id, recipe_id FROM sync_conflicts WHERE status = 'open' ORDER BY created_at")
        ]
    return render_template(
        "sync.html",
        title=APP_TITLE,
        user=user,
        peers=peers,
        history=history,
        conflicts=conflicts,
        preview=session.pop("sync_preview", None),
        active="sync",
    )


@bp.route("/sync/peers", methods=["POST"])
def add_sync_peer():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    name, url, token = (
        request.form.get("name", "").strip(),
        request.form.get("url", "").strip().rstrip("/"),
        request.form.get("token", "").strip(),
    )
    if not name or len(name) > 80 or not valid_peer_url(url) or not token or len(token) > 500:
        flash("Enter a peer name, a valid HTTP(S) URL, and its shared token.", "error")
        return redirect(url_for("sync_dashboard"))
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO sync_peers (id, name, url, token, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"peer-{uuid.uuid4().hex}", name, url, token, now_iso()),
        )
    flash("Trusted peer added.")
    return redirect(url_for("sync_dashboard"))


@bp.route("/sync/peers/<peer_id>/toggle", methods=["POST"])
def toggle_sync_peer(peer_id):
    if not local_session_user():
        return redirect(url_for("signup"))
    with db_connect() as conn:
        conn.execute("UPDATE sync_peers SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id = ?", (peer_id,))
    return redirect(url_for("sync_dashboard"))


@bp.route("/sync/peers/<peer_id>/delete", methods=["POST"])
def delete_sync_peer(peer_id):
    if not local_session_user():
        return redirect(url_for("signup"))
    with db_connect() as conn:
        conn.execute("DELETE FROM sync_peers WHERE id = ?", (peer_id,))
    flash("Peer removed.")
    return redirect(url_for("sync_dashboard"))


@bp.route("/api/sync/preview", methods=["POST"])
def api_sync_preview():
    auth_error = require_local_json_auth()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(preview_peer_changes(str(data["peer_id"])))
    except KeyError:
        return sync_json_error("VALIDATION_ERROR", "peer_id is required.", 422)
    except (ValueError, RuntimeError) as exc:
        return sync_json_error("SYNC_FAILED", str(exc), 502)


@bp.route("/sync/peers/<peer_id>/preview", methods=["POST"])
def preview_sync_peer(peer_id):
    if not local_session_user():
        return redirect(url_for("signup"))
    try:
        session["sync_preview"] = preview_peer_changes(peer_id)
        flash("Preview ready. Review the counts before syncing.")
    except (ValueError, RuntimeError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))


@bp.route("/api/sync/run", methods=["POST"])
def api_sync_run():
    auth_error = require_local_json_auth()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(run_peer_sync(str(data["peer_id"])))
    except KeyError:
        return sync_json_error("VALIDATION_ERROR", "peer_id is required.", 422)
    except (ValueError, RuntimeError) as exc:
        return sync_json_error("SYNC_FAILED", str(exc), 502)


@bp.route("/sync/peers/<peer_id>/run", methods=["POST"])
def run_sync_peer(peer_id):
    if not local_session_user():
        return redirect(url_for("signup"))
    try:
        result = run_peer_sync(peer_id)
        flash(
            f"Sync complete: {result['summary']['imported']} imported, {result['summary']['conflict']} conflicts.",
            "error" if result["status"] == "conflict" else "",
        )
    except (ValueError, RuntimeError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))


@bp.route("/api/sync/conflicts/<conflict_id>/resolve", methods=["POST"])
def api_resolve_sync_conflict(conflict_id):
    auth_error = require_local_json_auth()
    if auth_error:
        return auth_error
    try:
        return jsonify(resolve_sync_conflict_action(conflict_id, (request.get_json(silent=True) or {}).get("resolution", "")))
    except ValueError as exc:
        return sync_json_error("VALIDATION_ERROR", str(exc), 422)


@bp.route("/sync/conflicts/<conflict_id>/resolve", methods=["POST"])
def resolve_sync_conflict(conflict_id):
    if not local_session_user():
        return redirect(url_for("signup"))
    try:
        resolve_sync_conflict_action(conflict_id, request.form.get("resolution", ""))
        flash("Conflict resolved.")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))
