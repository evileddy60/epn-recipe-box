import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template_string, request, session, url_for
from jinja2 import DictLoader
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


APP_TITLE = "EPN Recipe Box"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = STATIC_DIR / "uploads"
DB_FILE = BASE_DIR / "recipe_box.db"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

STATIC_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "replace-this-with-a-random-recipe-box-secret"
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_ingredients(value: str) -> list[str]:
    chunks = re.split(r"[\n,]+", value)
    return [chunk.strip().lower() for chunk in chunks if chunk.strip()]


def ingredient_key(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\b(cups?|tbsp|tablespoons?|tsp|teaspoons?|oz|ounces?|lbs?|pounds?|grams?|g|kg|ml|l)\b", "", value)
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    value = re.sub(r"\b\d+([./]\d+)?\b", "", value)
    words = [word for word in value.split() if len(word) > 2]
    return " ".join(words[-2:]) if words else value.strip()


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                nickname TEXT NOT NULL DEFAULT '',
                bio TEXT NOT NULL DEFAULT '',
                avatar TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory_items (
                user_id TEXT NOT NULL,
                item TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (user_id, item),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                prep_time TEXT NOT NULL,
                servings TEXT NOT NULL,
                ingredients_json TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ratings (
                recipe_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (recipe_id, user_id),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                recipe_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "email" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        if "password_hash" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
        if "nickname" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
            if "name" in user_columns:
                conn.execute("UPDATE users SET nickname = name WHERE nickname = ''")


def row_to_user(row: sqlite3.Row, inventory: list[str] | None = None) -> dict:
    nickname = row["nickname"] or row["email"]
    return {
        "id": row["id"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "nickname": row["nickname"],
        "name": nickname,
        "bio": row["bio"],
        "avatar": row["avatar"],
        "inventory": inventory or [],
        "created_at": row["created_at"],
    }


def row_to_recipe(row: sqlite3.Row, ratings: list[dict] | None = None, comments: list[dict] | None = None) -> dict:
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "title": row["title"],
        "summary": row["summary"],
        "prep_time": row["prep_time"],
        "servings": row["servings"],
        "ingredients": json.loads(row["ingredients_json"]),
        "steps": json.loads(row["steps_json"]),
        "ratings": ratings or [],
        "comments": comments or [],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def load_data() -> dict:
    init_db()
    with db_connect() as conn:
        inventory_by_user = {}
        for row in conn.execute("SELECT user_id, item FROM inventory_items ORDER BY position, item"):
            inventory_by_user.setdefault(row["user_id"], []).append(row["item"])

        users = [
            row_to_user(row, inventory_by_user.get(row["id"], []))
            for row in conn.execute("SELECT * FROM users ORDER BY created_at")
        ]

        ratings_by_recipe = {}
        for row in conn.execute("SELECT recipe_id, user_id, score FROM ratings"):
            ratings_by_recipe.setdefault(row["recipe_id"], []).append(
                {"user_id": row["user_id"], "score": row["score"]}
            )

        comments_by_recipe = {}
        for row in conn.execute("SELECT id, recipe_id, user_id, body, created_at FROM comments ORDER BY created_at"):
            comments_by_recipe.setdefault(row["recipe_id"], []).append(
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "body": row["body"],
                    "created_at": row["created_at"],
                }
            )

        recipes = [
            row_to_recipe(row, ratings_by_recipe.get(row["id"], []), comments_by_recipe.get(row["id"], []))
            for row in conn.execute("SELECT * FROM recipes ORDER BY created_at DESC")
        ]

    return {"users": users, "recipes": recipes}


def create_account(email: str, password: str) -> str:
    user_id = f"u-{uuid.uuid4().hex[:10]}"
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, email.lower(), generate_password_hash(password), now_iso()),
        )
    return user_id


def authenticate_user(email: str, password: str) -> str | None:
    init_db()
    with db_connect() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE lower(email) = lower(?)",
            (email.strip(),),
        ).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        return row["id"]
    return None


def update_profile(user_id: str, nickname: str, bio: str, avatar: str) -> None:
    with db_connect() as conn:
        if avatar:
            conn.execute(
                "UPDATE users SET nickname = ?, bio = ?, avatar = ? WHERE id = ?",
                (nickname, bio, avatar, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET nickname = ?, bio = ? WHERE id = ?",
                (nickname, bio, user_id),
            )


def update_inventory(user_id: str, items: list[str]) -> None:
    unique_items = list(dict.fromkeys(items))
    with db_connect() as conn:
        conn.execute("DELETE FROM inventory_items WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO inventory_items (user_id, item, position) VALUES (?, ?, ?)",
            [(user_id, item, position) for position, item in enumerate(unique_items)],
        )


def create_recipe(owner_id: str, form) -> str:
    recipe_id = f"r-{uuid.uuid4().hex[:10]}"
    timestamp = now_iso()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO recipes (
                id, owner_id, title, summary, prep_time, servings,
                ingredients_json, steps_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recipe_id,
                owner_id,
                form["title"].strip(),
                form["summary"].strip(),
                form["prep_time"].strip(),
                form["servings"].strip(),
                json.dumps(split_ingredients(form["ingredients"])),
                json.dumps(split_lines(form["steps"])),
                timestamp,
                timestamp,
            ),
        )
    return recipe_id


def update_recipe(recipe_id: str, form) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE recipes
            SET title = ?, summary = ?, prep_time = ?, servings = ?,
                ingredients_json = ?, steps_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                form["title"].strip(),
                form["summary"].strip(),
                form["prep_time"].strip(),
                form["servings"].strip(),
                json.dumps(split_ingredients(form["ingredients"])),
                json.dumps(split_lines(form["steps"])),
                now_iso(),
                recipe_id,
            ),
        )


def save_rating(recipe_id: str, user_id: str, score: int) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO ratings (recipe_id, user_id, score, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(recipe_id, user_id)
            DO UPDATE SET score = excluded.score, created_at = excluded.created_at
            """,
            (recipe_id, user_id, score, now_iso()),
        )


def create_comment(recipe_id: str, user_id: str, body: str) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO comments (id, recipe_id, user_id, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"c-{uuid.uuid4().hex[:10]}", recipe_id, user_id, body, now_iso()),
        )


def current_user(data: dict) -> dict | None:
    user_id = session.get("user_id")
    user = next((user for user in data["users"] if user["id"] == user_id), None)
    if user_id and not user:
        session.pop("user_id", None)
    return user


def profile_ready(user: dict | None) -> bool:
    return bool(user and user.get("nickname"))


def recipe_owner(data: dict, recipe: dict) -> dict:
    return next((user for user in data["users"] if user["id"] == recipe["owner_id"]), {"name": "Unknown cook", "avatar": ""})


def average_rating(recipe: dict) -> float:
    ratings = recipe.get("ratings", [])
    if not ratings:
        return 0
    return round(sum(item["score"] for item in ratings) / len(ratings), 1)


def suggestion_score(recipe: dict, inventory: list[str]) -> tuple[int, int, list[str], list[str]]:
    inventory_keys = {ingredient_key(item) for item in inventory}
    recipe_keys = {ingredient_key(item) for item in recipe["ingredients"]}
    matched = sorted(item for item in recipe["ingredients"] if ingredient_key(item) in inventory_keys)
    missing = sorted(item for item in recipe["ingredients"] if ingredient_key(item) not in inventory_keys)
    return len(matched), len(recipe_keys), matched, missing


def decorate_recipe(data: dict, recipe: dict, inventory: list[str] | None = None) -> dict:
    match_count, ingredient_count, matched, missing = suggestion_score(recipe, inventory or [])
    decorated = dict(recipe)
    decorated["owner"] = recipe_owner(data, recipe)
    decorated["average_rating"] = average_rating(recipe)
    decorated["rating_count"] = len(recipe.get("ratings", []))
    decorated["match_count"] = match_count
    decorated["ingredient_count"] = ingredient_count
    decorated["matched"] = matched
    decorated["missing"] = missing
    decorated["share_url"] = url_for("recipe_detail", recipe_id=recipe["id"], _external=True)
    return decorated


def save_avatar(upload) -> str:
    if not upload or not upload.filename or not allowed_image(upload.filename):
        return ""
    filename = secure_filename(upload.filename)
    suffix = filename.rsplit(".", 1)[1].lower()
    stored_name = f"avatar-{uuid.uuid4().hex[:10]}.{suffix}"
    upload.save(UPLOAD_DIR / stored_name)
    return f"uploads/{stored_name}"


TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #2d241d;
      --muted: #74665a;
      --paper: #fff8e8;
      --paper-deep: #f5dfb9;
      --line: #e5c991;
      --accent: #b13f2c;
      --accent-dark: #7e2d24;
      --green: #416f42;
      --blue: #355c7d;
      --box: #9b5a2e;
      --shadow: 0 16px 38px rgba(70, 39, 17, .18);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(111, 74, 38, .05) 1px, transparent 1px),
        linear-gradient(#f8ead0, #f4dcb7 48%, #ead0a5);
      background-size: 18px 18px, auto;
    }
    a { color: inherit; }
    .shell {
      width: min(100%, 980px);
      margin: 0 auto;
      padding: 14px 14px 92px;
    }
    .hero {
      min-height: 250px;
      border: 1px solid rgba(77, 46, 22, .18);
      border-radius: 8px;
      background-image: linear-gradient(180deg, rgba(34, 19, 8, .08), rgba(34, 19, 8, .5)), url("{{ url_for('static', filename='recipe-box.png') }}");
      background-size: cover;
      background-position: center;
      color: white;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      padding: 18px;
      box-shadow: var(--shadow);
    }
    .hero h1 {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(2rem, 12vw, 4.5rem);
      line-height: .9;
      letter-spacing: 0;
      text-shadow: 0 2px 16px rgba(0,0,0,.45);
    }
    .hero p {
      max-width: 620px;
      margin: 8px 0 0;
      font-size: 1rem;
      text-shadow: 0 2px 12px rgba(0,0,0,.55);
    }
    .topbar {
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      margin: 14px 0;
    }
    .profile-chip, .nav-chip {
      border: 1px solid rgba(91, 55, 27, .2);
      background: rgba(255, 248, 232, .88);
      border-radius: 999px;
      padding: 8px 12px;
      text-decoration: none;
      color: var(--ink);
      white-space: nowrap;
      box-shadow: 0 8px 18px rgba(70,39,17,.08);
    }
    .profile-chip {
      display: flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      min-width: max-content;
    }
    .avatar {
      width: 34px;
      height: 34px;
      border-radius: 50%;
      border: 2px solid #fff3d8;
      object-fit: cover;
      background: #c46c45;
      display: grid;
      place-items: center;
      color: white;
      font-weight: 800;
      flex: 0 0 auto;
    }
    .nav {
      display: none;
      gap: 8px;
      min-width: max-content;
    }
    .flash {
      margin: 12px 0;
      padding: 12px 14px;
      border-radius: 8px;
      background: #ecf5df;
      border: 1px solid #bad19c;
      color: #2f4d24;
    }
    .flash.error {
      background: #fff0ec;
      border-color: #e3a493;
      color: #722d22;
    }
    .workspace {
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
    }
    .recipe-box {
      position: relative;
      padding: 16px 12px 18px;
      border: 1px solid rgba(74, 38, 13, .22);
      border-radius: 8px;
      background:
        linear-gradient(90deg, rgba(255,255,255,.14), transparent 18%, rgba(0,0,0,.08) 100%),
        linear-gradient(#b47442, #8d4f2a);
      box-shadow: inset 0 0 0 3px rgba(255,255,255,.12), var(--shadow);
    }
    .box-label {
      color: #fff1ce;
      font-family: Georgia, "Times New Roman", serif;
      font-size: 1.3rem;
      margin: 0 0 12px;
    }
    .cards {
      display: grid;
      gap: 12px;
    }
    .recipe-card, .panel {
      position: relative;
      border: 1px solid #dfbd7a;
      border-radius: 8px;
      background:
        repeating-linear-gradient(0deg, transparent 0 30px, rgba(207, 165, 85, .34) 31px 32px),
        linear-gradient(96deg, rgba(177,63,44,.14) 0 3px, transparent 3px),
        var(--paper);
      box-shadow: 0 10px 24px rgba(73, 42, 15, .14);
    }
    .recipe-card { padding: 18px 16px 14px; }
    .recipe-card::before {
      content: "";
      position: absolute;
      top: -1px;
      right: 18px;
      width: 92px;
      height: 20px;
      border-radius: 0 0 7px 7px;
      background: var(--paper-deep);
      border: 1px solid #d1ac66;
      border-top: 0;
    }
    .recipe-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    h2, h3 {
      font-family: Georgia, "Times New Roman", serif;
      letter-spacing: 0;
    }
    h2 { margin: 0 0 8px; font-size: 1.45rem; }
    h3 { margin: 0 0 8px; font-size: 1.2rem; }
    .meta, .small {
      color: var(--muted);
      font-size: .9rem;
    }
    .summary { margin: 9px 0 12px; }
    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin: 10px 0;
    }
    .tag {
      border-radius: 999px;
      padding: 5px 8px;
      background: rgba(65,111,66,.12);
      color: #315434;
      border: 1px solid rgba(65,111,66,.22);
      font-size: .8rem;
    }
    .tag.missing {
      background: rgba(177,63,44,.09);
      color: var(--accent-dark);
      border-color: rgba(177,63,44,.2);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    button, .button {
      appearance: none;
      border: 1px solid rgba(91, 55, 27, .25);
      border-radius: 8px;
      background: var(--accent);
      color: white;
      padding: 10px 12px;
      font: inherit;
      font-weight: 750;
      text-decoration: none;
      cursor: pointer;
      min-height: 42px;
    }
    .button.secondary, button.secondary {
      background: #fff5df;
      color: var(--ink);
    }
    .button.green, button.green { background: var(--green); }
    .panel {
      padding: 16px;
    }
    .panel + .panel { margin-top: 14px; }
    form { display: grid; gap: 10px; }
    label { display: grid; gap: 6px; font-weight: 760; color: #4b392a; }
    input, textarea, select {
      width: 100%;
      border: 1px solid #d4b274;
      border-radius: 8px;
      background: rgba(255,255,255,.68);
      color: var(--ink);
      padding: 11px 12px;
      font: inherit;
      min-height: 42px;
    }
    textarea { min-height: 96px; resize: vertical; }
    .two { display: grid; grid-template-columns: 1fr; gap: 10px; }
    .rating-row {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
    }
    .star-button {
      width: 40px;
      height: 40px;
      padding: 0;
      display: grid;
      place-items: center;
      background: #fff5df;
      color: #8a5b18;
    }
    .comment {
      border-top: 1px dashed #cfa85f;
      padding-top: 10px;
      margin-top: 10px;
    }
    .empty {
      padding: 18px;
      border: 1px dashed rgba(91,55,27,.32);
      border-radius: 8px;
      background: rgba(255,248,232,.5);
      color: var(--muted);
    }
    .bottom-nav {
      position: fixed;
      left: 50%;
      bottom: 12px;
      transform: translateX(-50%);
      width: min(calc(100% - 22px), 520px);
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 6px;
      padding: 7px;
      border-radius: 18px;
      background: rgba(61, 36, 18, .88);
      backdrop-filter: blur(14px);
      box-shadow: 0 16px 36px rgba(40,21,8,.32);
      z-index: 10;
    }
    .bottom-nav a {
      color: #fff7e6;
      text-decoration: none;
      text-align: center;
      padding: 8px 5px;
      border-radius: 13px;
      font-size: .8rem;
      font-weight: 760;
    }
    .bottom-nav a.active {
      background: #fff4d8;
      color: var(--accent-dark);
    }
    @media (min-width: 740px) {
      .shell { padding: 24px 22px 36px; }
      .hero { min-height: 330px; padding: 30px; }
      .workspace { grid-template-columns: minmax(0, 1.5fr) minmax(270px, .8fr); align-items: start; }
      .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .two { grid-template-columns: repeat(2, 1fr); }
      .nav { display: flex; }
      .bottom-nav { display: none; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <h1>EPN Recipe Box</h1>
      <p>Create, share, rate, and remix recipes from the food you already have.</p>
    </section>

    <div class="topbar">
      <a class="profile-chip" href="{{ url_for('profile_setup') if user else url_for('signup') }}">
        {% if user and user.avatar %}
          <img class="avatar" src="{{ url_for('static', filename=user.avatar) }}" alt="">
        {% else %}
          <span class="avatar">{{ (user.name[:1] if user else "R") }}</span>
        {% endif %}
        <span>{{ user.name if user and user.nickname else "Sign up" }}</span>
      </a>
      <nav class="nav" aria-label="Primary">
        <a class="nav-chip" href="{{ url_for('index') }}">Cards</a>
        <a class="nav-chip" href="{{ url_for('new_recipe') }}">New recipe</a>
        <a class="nav-chip" href="{{ url_for('inventory') }}">Inventory</a>
      </nav>
    </div>

    {% with messages = get_flashed_messages(with_categories=true) %}
      {% for category, message in messages %}
        <div class="flash {% if category == 'error' %}error{% endif %}">{{ message }}</div>
      {% endfor %}
    {% endwith %}

    {% block content %}{% endblock %}
  </main>

  <nav class="bottom-nav" aria-label="Mobile primary">
    <a class="{% if active == 'cards' %}active{% endif %}" href="{{ url_for('index') }}">Cards</a>
    <a class="{% if active == 'new' %}active{% endif %}" href="{{ url_for('new_recipe') }}">Create</a>
    <a class="{% if active == 'inventory' %}active{% endif %}" href="{{ url_for('inventory') }}">Stock</a>
    <a class="{% if active == 'profile' %}active{% endif %}" href="{{ url_for('profile_setup') if user else url_for('signup') }}">{{ "Profile" if user and user.nickname else "Sign up" }}</a>
  </nav>
</body>
</html>
"""


INDEX_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <div class="recipe-box">
    <p class="box-label">Shared cards</p>
    <div class="cards">
      {% for recipe in recipes %}
        <article class="recipe-card">
          <div class="recipe-head">
            <div>
              <h2>{{ recipe.title }}</h2>
              <div class="meta">by {{ recipe.owner.name }} · {{ recipe.prep_time }} · {{ recipe.servings }} servings</div>
            </div>
            <div class="meta">★ {{ recipe.average_rating or "New" }}{% if recipe.rating_count %} ({{ recipe.rating_count }}){% endif %}</div>
          </div>
          <p class="summary">{{ recipe.summary }}</p>
          <div class="tags">
            {% for item in recipe.ingredients[:5] %}
              <span class="tag">{{ item }}</span>
            {% endfor %}
          </div>
          {% if recipe.match_count %}
            <div class="small">{{ recipe.match_count }} of {{ recipe.ingredient_count }} ingredients in stock</div>
          {% endif %}
          <div class="actions">
            <a class="button" href="{{ url_for('recipe_detail', recipe_id=recipe.id) }}">Open card</a>
            {% if user and recipe.owner_id == user.id %}
              <a class="button secondary" href="{{ url_for('edit_recipe', recipe_id=recipe.id) }}">Edit</a>
            {% endif %}
          </div>
        </article>
      {% else %}
        <div class="empty">{% if user %}No recipes yet. Add the first card to the box.{% else %}Sign up to start building your recipe box.{% endif %}</div>
      {% endfor %}
    </div>
  </div>

  <aside>
    <section class="panel">
      <h3>Your food stock</h3>
      {% if user.inventory %}
        <div class="tags">
          {% for item in user.inventory %}
            <span class="tag">{{ item }}</span>
          {% endfor %}
        </div>
      {% else %}
        <p class="small">Add ingredients to unlock recipe suggestions.</p>
      {% endif %}
      <div class="actions">
        <a class="button green" href="{{ url_for('inventory') }}">Update stock</a>
      </div>
    </section>

    <section class="panel">
      <h3>Best matches</h3>
      {% for recipe in suggestions %}
        <p><strong>{{ recipe.title }}</strong><br><span class="small">{{ recipe.match_count }} of {{ recipe.ingredient_count }} matched</span></p>
      {% else %}
        <p class="small">Add stock items and recipes will sort themselves into place.</p>
      {% endfor %}
    </section>
  </aside>
</section>
{% endblock %}
"""


RECIPE_FORM_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="panel">
  <h2>{{ "Edit recipe" if recipe else "Create a recipe card" }}</h2>
  <form method="post">
    <label>Recipe name
      <input name="title" value="{{ recipe.title if recipe else '' }}" required>
    </label>
    <label>Short note
      <textarea name="summary" required>{{ recipe.summary if recipe else '' }}</textarea>
    </label>
    <div class="two">
      <label>Prep time
        <input name="prep_time" value="{{ recipe.prep_time if recipe else '' }}" placeholder="35 min" required>
      </label>
      <label>Servings
        <input name="servings" value="{{ recipe.servings if recipe else '' }}" placeholder="4" required>
      </label>
    </div>
    <label>Ingredients
      <textarea name="ingredients" placeholder="One per line or comma separated" required>{{ recipe.ingredients|join('\\n') if recipe else '' }}</textarea>
    </label>
    <label>Steps
      <textarea name="steps" placeholder="One step per line" required>{{ recipe.steps|join('\\n') if recipe else '' }}</textarea>
    </label>
    <div class="actions">
      <button type="submit">{{ "Save changes" if recipe else "Add to box" }}</button>
      <a class="button secondary" href="{{ url_for('index') }}">Cancel</a>
    </div>
  </form>
</section>
{% endblock %}
"""


DETAIL_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <article class="recipe-card">
    <div class="recipe-head">
      <div>
        <h2>{{ recipe.title }}</h2>
        <div class="meta">by {{ recipe.owner.name }} · {{ recipe.prep_time }} · {{ recipe.servings }} servings</div>
      </div>
      <div class="meta">★ {{ recipe.average_rating or "New" }}</div>
    </div>
    <p class="summary">{{ recipe.summary }}</p>

    <h3>Ingredients</h3>
    <div class="tags">
      {% for item in recipe.ingredients %}
        <span class="tag {% if item in recipe.missing %}missing{% endif %}">{{ item }}</span>
      {% endfor %}
    </div>

    <h3>Steps</h3>
    <ol>
      {% for step in recipe.steps %}
        <li>{{ step }}</li>
      {% endfor %}
    </ol>

    <div class="actions">
      {% if user and recipe.owner_id == user.id %}
        <a class="button secondary" href="{{ url_for('edit_recipe', recipe_id=recipe.id) }}">Edit recipe</a>
      {% endif %}
      <button class="secondary" type="button" onclick="navigator.clipboard && navigator.clipboard.writeText('{{ recipe.share_url }}')">Copy share link</button>
    </div>
    <p class="small">{{ recipe.share_url }}</p>
  </article>

  <aside>
    <section class="panel">
      <h3>Rate this recipe</h3>
      <form method="post" action="{{ url_for('rate_recipe', recipe_id=recipe.id) }}">
        <div class="rating-row">
          {% for score in range(1, 6) %}
            <button class="star-button" name="score" value="{{ score }}" title="{{ score }} stars">★</button>
          {% endfor %}
        </div>
      </form>
    </section>

    <section class="panel">
      <h3>Comments</h3>
      {% for comment in recipe.comments %}
        <div class="comment">
          <strong>{{ comment.user.name }}</strong>
          <p>{{ comment.body }}</p>
          <div class="small">{{ comment.created_at[:10] }}</div>
        </div>
      {% else %}
        <p class="small">No comments yet.</p>
      {% endfor %}
      <form method="post" action="{{ url_for('comment_recipe', recipe_id=recipe.id) }}">
        <label>Add a comment
          <textarea name="body" required></textarea>
        </label>
        <button type="submit">Post comment</button>
      </form>
    </section>
  </aside>
</section>
{% endblock %}
"""


SIGNUP_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <div class="panel">
    <h2>Sign up</h2>
    <form method="post">
      <input type="hidden" name="mode" value="signup">
      <label>Email
        <input type="email" name="email" autocomplete="email" required>
      </label>
      <label>Password
        <input type="password" name="password" autocomplete="new-password" minlength="8" required>
      </label>
      <button type="submit">Create account</button>
    </form>
  </div>
  <div class="panel">
    <h2>Sign in</h2>
    <form method="post">
      <input type="hidden" name="mode" value="login">
      <label>Email
        <input type="email" name="email" autocomplete="email" required>
      </label>
      <label>Password
        <input type="password" name="password" autocomplete="current-password" required>
      </label>
      <button class="secondary" type="submit">Sign in</button>
    </form>
  </div>
</section>
{% endblock %}
"""


PROFILE_SETUP_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <div class="panel">
    <h2>Set up profile</h2>
    <form method="post" enctype="multipart/form-data">
      <label>Profile nickname
        <input name="nickname" value="{{ user.nickname if user else '' }}" autocomplete="nickname" required>
      </label>
      <label>About
        <textarea name="bio" placeholder="Favorite cuisines, dietary notes, cooking style">{{ user.bio if user else '' }}</textarea>
      </label>
      <label>Avatar
        <input type="file" name="avatar" accept="image/*">
      </label>
      <button type="submit">Save profile</button>
    </form>
  </div>
  <div class="panel">
    <h2>Account</h2>
    <p class="small">{{ user.email }}</p>
  </div>
</section>
{% endblock %}
"""


INVENTORY_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <div class="panel">
    <h2>Food stock inventory</h2>
    <form method="post">
      <label>Ingredients you have
        <textarea name="inventory" placeholder="eggs, rice, spinach">{{ user.inventory|join('\\n') }}</textarea>
      </label>
      <button class="green" type="submit">Update stock</button>
    </form>
  </div>
  <div class="recipe-box">
    <p class="box-label">Suggested cards</p>
    <div class="cards">
      {% for recipe in suggestions %}
        <article class="recipe-card">
          <h2>{{ recipe.title }}</h2>
          <div class="meta">{{ recipe.match_count }} of {{ recipe.ingredient_count }} ingredients ready</div>
          <div class="tags">
            {% for item in recipe.matched %}
              <span class="tag">{{ item }}</span>
            {% endfor %}
            {% for item in recipe.missing[:4] %}
              <span class="tag missing">{{ item }}</span>
            {% endfor %}
          </div>
          <a class="button" href="{{ url_for('recipe_detail', recipe_id=recipe.id) }}">Cook this</a>
        </article>
      {% else %}
        <div class="empty">Add ingredients and recipes to get suggestions.</div>
      {% endfor %}
    </div>
  </div>
</section>
{% endblock %}
"""


app.jinja_loader = DictLoader({
    "base": TEMPLATE,
    "index": INDEX_TEMPLATE,
    "recipe_form": RECIPE_FORM_TEMPLATE,
    "detail": DETAIL_TEMPLATE,
    "signup": SIGNUP_TEMPLATE,
    "profile_setup": PROFILE_SETUP_TEMPLATE,
    "inventory": INVENTORY_TEMPLATE,
})


@app.route("/")
def index():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    inventory = user.get("inventory", []) if user else []
    recipes = [decorate_recipe(data, recipe, inventory) for recipe in data["recipes"]]
    suggestions = sorted(recipes, key=lambda item: (item["match_count"], item["average_rating"]), reverse=True)[:3]
    return render_template_string(
        INDEX_TEMPLATE,
        title=APP_TITLE,
        user=user,
        recipes=recipes,
        suggestions=suggestions,
        active="cards",
    )


@app.route("/signup", methods=["GET", "POST"])
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
            user_id = authenticate_user(email, password)
            if not user_id:
                flash("Email or password did not match.", "error")
                return redirect(url_for("signup"))
            session["user_id"] = user_id
            data = load_data()
            user = current_user(data)
            return redirect(url_for("index") if profile_ready(user) else url_for("profile_setup"))
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("signup"))
        try:
            user_id = create_account(email, password)
        except sqlite3.IntegrityError:
            flash("An account already exists for that email. Sign in instead.", "error")
            return redirect(url_for("signup"))
        session["user_id"] = user_id
        flash("Account created. Set up your profile next.")
        return redirect(url_for("profile_setup"))
    return render_template_string(SIGNUP_TEMPLATE, title=APP_TITLE, user=user, active="profile")


@app.route("/profiles", methods=["GET", "POST"])
@app.route("/profile/setup", methods=["GET", "POST"])
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
        avatar = save_avatar(request.files.get("avatar"))
        update_profile(user["id"], nickname, request.form.get("bio", "").strip(), avatar)
        flash("Profile saved.")
        return redirect(url_for("index"))
    return render_template_string(PROFILE_SETUP_TEMPLATE, title=APP_TITLE, user=user, active="profile")


@app.route("/recipes/new", methods=["GET", "POST"])
def new_recipe():
    data = load_data()
    user = current_user(data)
    if not user:
        flash("Create a profile before adding recipes.", "error")
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    if request.method == "POST":
        recipe_id = create_recipe(user["id"], request.form)
        flash("Recipe card added to the box.")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    return render_template_string(RECIPE_FORM_TEMPLATE, title=APP_TITLE, user=user, recipe=None, active="new")


@app.route("/recipes/<recipe_id>")
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
    return render_template_string(DETAIL_TEMPLATE, title=APP_TITLE, user=user, recipe=decorated, active="cards")


@app.route("/recipes/<recipe_id>/edit", methods=["GET", "POST"])
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
        update_recipe(recipe_id, request.form)
        flash("Recipe card updated.")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    return render_template_string(RECIPE_FORM_TEMPLATE, title=APP_TITLE, user=user, recipe=recipe, active="cards")


@app.route("/recipes/<recipe_id>/rate", methods=["POST"])
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


@app.route("/recipes/<recipe_id>/comment", methods=["POST"])
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
        create_comment(recipe_id, user["id"], body)
        flash("Comment added.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))


@app.route("/inventory", methods=["GET", "POST"])
def inventory():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    if request.method == "POST":
        update_inventory(user["id"], split_ingredients(request.form.get("inventory", "")))
        flash("Food stock updated.")
        return redirect(url_for("inventory"))
    suggestions = [
        decorate_recipe(data, recipe, user.get("inventory", []))
        for recipe in data["recipes"]
    ]
    suggestions = sorted(suggestions, key=lambda item: (item["match_count"], item["average_rating"]), reverse=True)
    return render_template_string(
        INVENTORY_TEMPLATE,
        title=APP_TITLE,
        user=user,
        suggestions=suggestions,
        active="inventory",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
