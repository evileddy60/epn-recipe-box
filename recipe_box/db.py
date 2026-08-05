from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

from sync import token_hash
from .config import *
from .migrations import migrate_database, schema_version

CATEGORY_DEFAULTS = (
    ("breakfast", "Breakfast"),
    ("lunch", "Lunch"),
    ("dinner", "Dinner"),
    ("dessert", "Dessert"),
    ("snack", "Snack"),
    ("soup", "Soup"),
    ("salad", "Salad"),
    ("beverage", "Beverage"),
    ("baking", "Baking"),
    ("other", "Other"),
)
MAX_TAGS = 12
MAX_TAG_LENGTH = 40
MAX_TAG_INPUT = 400
MAX_NICKNAME_LENGTH = 80
MAX_BIO_LENGTH = 1000
MAX_RECIPE_TITLE = 200
MAX_RECIPE_SUMMARY = 1000
MAX_RECIPE_INGREDIENTS = 6000
MAX_RECIPE_STEPS = 10000
MAX_COMMENT_LENGTH = 2000
MAX_INVENTORY_LENGTH = 4000
MAX_EMAIL_LENGTH = 254


def normalize_category(value: str) -> str:
    value = "-".join(str(value or "").strip().lower().split())
    if value and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", value):
        raise ValueError("Choose a valid recipe category.")
    return value


def normalize_tag(value: str) -> tuple[str, str]:
    display = " ".join(str(value or "").strip().split())
    if not display or len(display) > MAX_TAG_LENGTH:
        raise ValueError("Tags must be between 1 and 40 characters.")
    normalized = re.sub(r"[^a-z0-9]+", "-", display.lower()).strip("-")
    if not normalized or len(normalized) > MAX_TAG_LENGTH:
        raise ValueError("Tags may contain letters, numbers, spaces, and hyphens.")
    return normalized, display


def parse_tags(value: str) -> list[tuple[str, str]]:
    if len(value or "") > MAX_TAG_INPUT:
        raise ValueError("Tags are too long.")
    parsed = []
    seen = set()
    for raw in re.split(r"[,\n]+", value or ""):
        if not raw.strip():
            continue
        normalized, display = normalize_tag(raw)
        if normalized not in seen:
            parsed.append((normalized, display))
            seen.add(normalized)
    if len(parsed) > MAX_TAGS:
        raise ValueError(f"Use no more than {MAX_TAGS} tags.")
    return parsed


def category_label(category_key: str) -> str:
    return category_key.replace("-", " ").title() if category_key else "Uncategorized"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def now_iso_legacy() -> str:
    return datetime.now().isoformat(timespec="seconds")


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_ingredients(value: str) -> list[str]:
    chunks = re.split(r"[\n,]+", value)
    return [chunk.strip().lower() for chunk in chunks if chunk.strip()]


def normalized_ingredient_words(value: str) -> list[str]:
    value = value.lower()
    value = re.sub(r"\b(cups?|tbsp|tablespoons?|tsp|teaspoons?|oz|ounces?|lbs?|pounds?|grams?|g|kg|ml|l)\b", "", value)
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    value = re.sub(r"\b\d+([./]\d+)?\b", "", value)
    words = []
    for word in value.split():
        if len(word) <= 2:
            continue
        if word.endswith("ies") and len(word) > 4:
            word = f"{word[:-3]}y"
        elif word.endswith("es") and len(word) > 4:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 3:
            word = word[:-1]
        words.append(word)
    return words


def ingredient_key(value: str) -> str:
    words = normalized_ingredient_words(value)
    return " ".join(words[-2:]) if words else value.strip().lower()


def ingredient_matches(needed: str, stocked: str) -> bool:
    needed_words = normalized_ingredient_words(needed)
    stocked_words = normalized_ingredient_words(stocked)
    if not needed_words or not stocked_words:
        return needed.strip().lower() == stocked.strip().lower()
    needed_set = set(needed_words)
    stocked_set = set(stocked_words)
    return needed_set.issubset(stocked_set) or bool(needed_set & stocked_set)


def stock_has(needed: str, inventory: list[str]) -> bool:
    return any(ingredient_matches(needed, stocked) for stocked in inventory)


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _backup_database_before_migration() -> None:
    if not DB_FILE.exists():
        return
    backup = DB_FILE.with_name(f"{DB_FILE.name}.pre-sync-{utc_stamp()}.bak")
    shutil.copy2(DB_FILE, backup)


def sync_token() -> str:
    configured = os.environ.get("SYNC_TOKEN", "").strip()
    if configured:
        return configured
    token_file = DATA_DIR / ".sync-token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    token_file.write_text(token + "\n", encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    return token


def init_db() -> None:
    migrate_database(DB_FILE)
    with db_connect() as conn:
        installation = conn.execute("SELECT id FROM installations LIMIT 1").fetchone()
        if not installation:
            conn.execute(
                "INSERT INTO installations (id, token_hash, created_at) VALUES (?, ?, ?)",
                (f"box-{uuid.uuid4().hex}", token_hash(sync_token()), now_iso()),
            )
        else:
            conn.execute("UPDATE installations SET token_hash = ?", (token_hash(sync_token()),))


def _legacy_init_db() -> None:
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
            CREATE TABLE IF NOT EXISTS categories (
                category_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                normalized_name TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recipe_tags (
                recipe_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                PRIMARY KEY (recipe_id, tag_id),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_recipes_owner ON recipes(owner_id);
            CREATE INDEX IF NOT EXISTS idx_recipe_tags_tag ON recipe_tags(tag_id);
            """
        )
        conn.executemany("INSERT OR IGNORE INTO categories (category_key, display_name) VALUES (?, ?)", CATEGORY_DEFAULTS)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS installations (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_peers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, last_sync_at TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_baselines (
                peer_id TEXT NOT NULL, recipe_id TEXT NOT NULL, checksum TEXT NOT NULL,
                remote_updated_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (peer_id, recipe_id), FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sync_conflicts (
                id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, recipe_id TEXT NOT NULL,
                local_json TEXT NOT NULL, remote_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, resolved_at TEXT,
                FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sync_history (
                id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, summary_json TEXT NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE
            );
            """
        )
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        recipe_columns = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)")}
        required_alters = []
        if "sync_source_installation_id" not in recipe_columns:
            required_alters.append("ALTER TABLE recipes ADD COLUMN sync_source_installation_id TEXT NOT NULL DEFAULT ''")
        if "category_key" not in recipe_columns:
            required_alters.append("ALTER TABLE recipes ADD COLUMN category_key TEXT NOT NULL DEFAULT ''")
        if "email" not in user_columns:
            required_alters.append("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        if "password_hash" not in user_columns:
            required_alters.append("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
        if "nickname" not in user_columns:
            required_alters.append("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        if required_alters:
            _backup_database_before_migration()
            for statement in required_alters:
                conn.execute(statement)
            if "name" in user_columns and "nickname" not in user_columns:
                conn.execute("UPDATE users SET nickname = name WHERE nickname = ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recipes_category ON recipes(category_key)")
        installation = conn.execute("SELECT id FROM installations LIMIT 1").fetchone()
        if not installation:
            conn.execute(
                "INSERT INTO installations (id, token_hash, created_at) VALUES (?, ?, ?)",
                (f"box-{uuid.uuid4().hex}", token_hash(sync_token()), now_iso()),
            )
        else:
            conn.execute("UPDATE installations SET token_hash = ?", (token_hash(sync_token()),))


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


def row_to_recipe(
    row: sqlite3.Row, ratings: list[dict] | None = None, comments: list[dict] | None = None, tags: list[dict] | None = None
) -> dict:
    category_key = row["category_key"] if "category_key" in row.keys() else ""
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "title": row["title"],
        "summary": row["summary"],
        "prep_time": row["prep_time"],
        "servings": row["servings"],
        "ingredients": json.loads(row["ingredients_json"]),
        "steps": json.loads(row["steps_json"]),
        "category": category_key or "",
        "category_label": category_label(category_key or ""),
        "tags": tags or [],
        "ratings": ratings or [],
        "comments": comments or [],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "sync_source_installation_id": row["sync_source_installation_id"] if "sync_source_installation_id" in row.keys() else "",
    }


def load_data(search: str = "", category: str = "", tag: str = "") -> dict:
    init_db()
    search = " ".join((search or "").strip().split())[:120]
    category = normalize_category(category) if category else ""
    tag = normalize_tag(tag)[0] if tag else ""
    with db_connect() as conn:
        inventory_by_user = {}
        for row in conn.execute("SELECT user_id, item FROM inventory_items ORDER BY position, item"):
            inventory_by_user.setdefault(row["user_id"], []).append(row["item"])
        users = [row_to_user(row, inventory_by_user.get(row["id"], [])) for row in conn.execute("SELECT * FROM users ORDER BY created_at")]
        query = """
            SELECT r.* FROM recipes r JOIN users owner ON owner.id = r.owner_id
            WHERE 1 = 1
        """
        params = []
        if search:
            needle = f"%{search.lower()}%"
            query += """ AND (
                lower(r.title) LIKE ? OR lower(r.summary) LIKE ? OR lower(r.ingredients_json) LIKE ?
                OR lower(COALESCE(r.category_key, '')) LIKE ? OR lower(owner.nickname) LIKE ?
                OR EXISTS (SELECT 1 FROM recipe_tags sr_rt JOIN tags sr_t ON sr_t.id = sr_rt.tag_id WHERE sr_rt.recipe_id = r.id AND lower(sr_t.normalized_name) LIKE ?)
            )"""
            params.extend([needle] * 6)
        if category:
            query += " AND COALESCE(r.category_key, '') = ?"
            params.append(category)
        if tag:
            query += " AND EXISTS (SELECT 1 FROM recipe_tags fr_rt JOIN tags fr_t ON fr_t.id = fr_rt.tag_id WHERE fr_rt.recipe_id = r.id AND fr_t.normalized_name = ?)"
            params.append(tag)
        query += " ORDER BY r.created_at DESC"
        recipe_rows = conn.execute(query, params).fetchall()
        recipe_ids = [row["id"] for row in recipe_rows]
        tags_by_recipe = {}
        if recipe_ids:
            placeholders = ",".join("?" for _ in recipe_ids)
            for row in conn.execute(
                f"SELECT rt.recipe_id, t.normalized_name, t.display_name FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id WHERE rt.recipe_id IN ({placeholders}) ORDER BY t.normalized_name",  # nosec B608 - placeholders are generated only for bound integer recipe IDs; SQL structure is fixed
                recipe_ids,
            ):
                tags_by_recipe.setdefault(row["recipe_id"], []).append(
                    {"normalized_name": row["normalized_name"], "display_name": row["display_name"]}
                )
        ratings_by_recipe = {}
        for row in conn.execute("SELECT recipe_id, user_id, score FROM ratings"):
            ratings_by_recipe.setdefault(row["recipe_id"], []).append({"user_id": row["user_id"], "score": row["score"]})
        comments_by_recipe = {}
        for row in conn.execute("SELECT id, recipe_id, user_id, body, created_at FROM comments ORDER BY created_at"):
            comments_by_recipe.setdefault(row["recipe_id"], []).append(
                {"id": row["id"], "user_id": row["user_id"], "body": row["body"], "created_at": row["created_at"]}
            )
        recipes = [
            row_to_recipe(
                row, ratings_by_recipe.get(row["id"], []), comments_by_recipe.get(row["id"], []), tags_by_recipe.get(row["id"], [])
            )
            for row in recipe_rows
        ]
        categories = [dict(row) for row in conn.execute("SELECT category_key, display_name FROM categories ORDER BY display_name")]
        tags = [dict(row) for row in conn.execute("SELECT normalized_name, display_name FROM tags ORDER BY normalized_name")]
    return {
        "users": users,
        "recipes": recipes,
        "categories": categories,
        "tags": tags,
        "filters": {"q": search, "category": category, "tag": tag},
    }


def create_account(email: str, password: str) -> str:
    email = email.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid email address.")
    if not 8 <= len(password) <= 128:
        raise ValueError("Password must be between 8 and 128 characters.")
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
    nickname = " ".join(nickname.strip().split())
    bio = bio.strip()
    if not nickname or len(nickname) > MAX_NICKNAME_LENGTH:
        raise ValueError("Nickname must be between 1 and 80 characters.")
    if len(bio) > MAX_BIO_LENGTH:
        raise ValueError("Biography is too long.")
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
    if sum(len(item) + 1 for item in items) > MAX_INVENTORY_LENGTH or len(items) > 200:
        raise ValueError("Inventory is too large.")
    unique_items = list(dict.fromkeys(item.strip()[:100] for item in items if item.strip()))
    with db_connect() as conn:
        conn.execute("DELETE FROM inventory_items WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO inventory_items (user_id, item, position) VALUES (?, ?, ?)",
            [(user_id, item, position) for position, item in enumerate(unique_items)],
        )


def replace_recipe_tags(conn: sqlite3.Connection, recipe_id: str, raw_value: str | list[dict] | list[tuple[str, str]]) -> None:
    if isinstance(raw_value, str):
        parsed = parse_tags(raw_value)
    else:
        parsed = []
        seen = set()
        for item in raw_value or []:
            if isinstance(item, dict):
                pair = normalize_tag(str(item.get("display_name") or item.get("normalized_name") or ""))
            else:
                pair = normalize_tag(item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else item)
            if pair[0] not in seen:
                parsed.append(pair)
                seen.add(pair[0])
        if len(parsed) > MAX_TAGS:
            raise ValueError(f"Use no more than {MAX_TAGS} tags.")
    conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
    for normalized, display in parsed:
        tag_id = f"tag-{normalized}"
        conn.execute("INSERT OR IGNORE INTO tags (id, normalized_name, display_name) VALUES (?, ?, ?)", (tag_id, normalized, display))
        conn.execute("UPDATE tags SET display_name = ? WHERE normalized_name = ?", (display, normalized))
        conn.execute("INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)", (recipe_id, tag_id))


def recipe_tag_payload(conn: sqlite3.Connection, recipe_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT t.normalized_name, t.display_name FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id WHERE rt.recipe_id = ? ORDER BY t.normalized_name",
            (recipe_id,),
        )
    ]


def create_recipe(owner_id: str, form) -> str:
    title = form.get("title", "").strip()
    summary = form.get("summary", "").strip()
    ingredients = form.get("ingredients", "")
    steps = form.get("steps", "")
    if not title or len(title) > MAX_RECIPE_TITLE:
        raise ValueError("Recipe title must be between 1 and 200 characters.")
    if len(summary) > MAX_RECIPE_SUMMARY or len(ingredients) > MAX_RECIPE_INGREDIENTS or len(steps) > MAX_RECIPE_STEPS:
        raise ValueError("Recipe text is too long.")
    recipe_id = f"r-{uuid.uuid4().hex[:10]}"
    timestamp = now_iso()
    category = normalize_category(form.get("category", ""))
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO recipes (
                id, owner_id, title, summary, prep_time, servings,
                ingredients_json, steps_json, category_key, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                category,
                timestamp,
                timestamp,
            ),
        )
        replace_recipe_tags(conn, recipe_id, form.get("tags", ""))
    return recipe_id


def update_recipe(recipe_id: str, form) -> None:
    title = form.get("title", "").strip()
    summary = form.get("summary", "").strip()
    ingredients = form.get("ingredients", "")
    steps = form.get("steps", "")
    if not title or len(title) > MAX_RECIPE_TITLE:
        raise ValueError("Recipe title must be between 1 and 200 characters.")
    if len(summary) > MAX_RECIPE_SUMMARY or len(ingredients) > MAX_RECIPE_INGREDIENTS or len(steps) > MAX_RECIPE_STEPS:
        raise ValueError("Recipe text is too long.")
    category = normalize_category(form.get("category", ""))
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE recipes
            SET title = ?, summary = ?, prep_time = ?, servings = ?,
                ingredients_json = ?, steps_json = ?, category_key = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                form["title"].strip(),
                form["summary"].strip(),
                form["prep_time"].strip(),
                form["servings"].strip(),
                json.dumps(split_ingredients(form["ingredients"])),
                json.dumps(split_lines(form["steps"])),
                category,
                now_iso(),
                recipe_id,
            ),
        )
        replace_recipe_tags(conn, recipe_id, form.get("tags", ""))


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
    body = body.strip()
    if not body or len(body) > MAX_COMMENT_LENGTH:
        raise ValueError("Comments must be between 1 and 2,000 characters.")
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
    recipe_keys = {ingredient_key(item) for item in recipe["ingredients"]}
    matched = sorted(item for item in recipe["ingredients"] if stock_has(item, inventory))
    missing = sorted(item for item in recipe["ingredients"] if not stock_has(item, inventory))
    return len(matched), len(recipe_keys), matched, missing
