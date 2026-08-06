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
from .policies import can_view_recipe, normalize_visibility

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
    row: sqlite3.Row,
    ratings: list[dict] | None = None,
    comments: list[dict] | None = None,
    tags: list[dict] | None = None,
    favorite: bool = False,
    archived: bool = False,
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
        "image_filename": row["image_filename"] if "image_filename" in row.keys() else "",
        "image_media_type": row["image_media_type"] if "image_media_type" in row.keys() else "",
        "image_width": row["image_width"] if "image_width" in row.keys() else 0,
        "image_height": row["image_height"] if "image_height" in row.keys() else 0,
        "image_size": row["image_size"] if "image_size" in row.keys() else 0,
        "image_sha256": row["image_sha256"] if "image_sha256" in row.keys() else "",
        "archived_at": row["archived_at"] if "archived_at" in row.keys() else None,
        "visibility": row["visibility"] if "visibility" in row.keys() else "private",
        "favorite": favorite,
        "archived": archived,
    }


def load_data(
    search: str = "", category: str = "", tag: str = "", favorites: bool = False, archived: bool | None = None, user_id: str = ""
) -> dict:
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
            WHERE (r.owner_id = ? OR r.visibility = 'shared_epn')
        """
        params = [user_id]
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
        if favorites:
            query += " AND (EXISTS (SELECT 1 FROM favorites f_filter WHERE f_filter.recipe_id = r.id AND f_filter.user_id = ?) OR EXISTS (SELECT 1 FROM recipe_user_state sf WHERE sf.recipe_id = r.id AND sf.user_id = ? AND sf.is_favorite = 1))"
            params.extend([user_id, user_id])
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
        for row in conn.execute(
            "SELECT id, recipe_id, user_id, body, created_at, updated_at, deleted_at, hidden_at, hidden_by_user_id FROM comments WHERE deleted_at IS NULL AND hidden_at IS NULL ORDER BY created_at"
        ):
            comments_by_recipe.setdefault(row["recipe_id"], []).append(
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "body": row["body"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "deleted_at": row["deleted_at"],
                    "hidden_at": row["hidden_at"],
                    "hidden_by_user_id": row["hidden_by_user_id"],
                }
            )
        favorite_ids = set()
        archived_ids = set()
        if user_id:
            favorite_ids = {row["recipe_id"] for row in conn.execute("SELECT recipe_id FROM favorites WHERE user_id = ?", (user_id,))}
            state_rows = conn.execute(
                "SELECT recipe_id, is_favorite, is_archived FROM recipe_user_state WHERE user_id = ?", (user_id,)
            ).fetchall()
            favorite_ids.update(row["recipe_id"] for row in state_rows if row["is_favorite"])
            archived_ids = {row["recipe_id"] for row in state_rows if row["is_archived"]}
        recipes = [
            row_to_recipe(
                row,
                ratings_by_recipe.get(row["id"], []),
                comments_by_recipe.get(row["id"], []),
                tags_by_recipe.get(row["id"], []),
                row["id"] in favorite_ids,
                row["id"] in archived_ids,
            )
            for row in recipe_rows
        ]
        if user_id:
            recipes = [recipe for recipe in recipes if recipe["archived"] is (archived is True)]
        categories = [dict(row) for row in conn.execute("SELECT category_key, display_name FROM categories ORDER BY display_name")]
        tags = [dict(row) for row in conn.execute("SELECT normalized_name, display_name FROM tags ORDER BY normalized_name")]
    return {
        "users": users,
        "recipes": recipes,
        "categories": categories,
        "tags": tags,
        "filters": {"q": search, "category": category, "tag": tag, "favorites": favorites, "archived": archived},
    }


def community_home_data(limit: int = 6) -> dict:
    """Return bounded, aggregate-backed data for the community homepage."""
    init_db()
    limit = max(1, min(int(limit), 12))
    with db_connect() as conn:
        stats = dict(
            conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM recipes WHERE visibility = 'shared_epn') AS recipes,
                    (SELECT COUNT(*) FROM users) AS members,
                    (SELECT COUNT(*) FROM collections WHERE visibility = 'shared_epn') AS collections,
                    (SELECT COUNT(*) FROM comments c JOIN recipes r ON r.id = c.recipe_id WHERE r.visibility = 'shared_epn') AS comments
                """
            ).fetchone()
        )
        recipe_fields = """
            SELECT r.id, r.title, r.summary, r.category_key, r.created_at, r.updated_at,
                   COALESCE(NULLIF(owner.nickname, ''), 'EPN cook') AS owner_name,
                   COUNT(DISTINCT rating.user_id) AS rating_count,
                   ROUND(AVG(rating.score), 1) AS average_rating
            FROM recipes r
            JOIN users owner ON owner.id = r.owner_id
            LEFT JOIN ratings rating ON rating.recipe_id = r.id
            WHERE r.visibility = 'shared_epn'
            GROUP BY r.id
        """

        def recipes(order_by: str) -> list[dict]:
            return [
                dict(row)
                for row in conn.execute(f"{recipe_fields} ORDER BY {order_by} LIMIT ?", (limit,))  # nosec B608 - fixed internal order clauses
            ]

        recent = recipes("r.updated_at DESC")
        highest_rated = recipes("CASE WHEN rating_count = 0 THEN 1 ELSE 0 END, average_rating DESC, rating_count DESC, r.created_at DESC")
        newest = recipes("r.created_at DESC")
        active_members = [
            dict(row)
            for row in conn.execute(
                """
                SELECT u.id, COALESCE(NULLIF(u.nickname, ''), 'EPN cook') AS name,
                       MAX(e.created_at) AS last_active
                FROM activity_events e JOIN users u ON u.id = e.user_id
                GROUP BY u.id ORDER BY last_active DESC LIMIT ?
                """,
                (limit,),
            )
        ]
        categories = [
            dict(row)
            for row in conn.execute(
                """
                SELECT COALESCE(NULLIF(r.category_key, ''), 'other') AS category_key,
                       COUNT(*) AS recipe_count
                FROM recipes r WHERE r.visibility = 'shared_epn'
                GROUP BY COALESCE(NULLIF(r.category_key, ''), 'other')
                ORDER BY recipe_count DESC, category_key LIMIT ?
                """,
                (limit,),
            )
        ]
        tags = [
            dict(row)
            for row in conn.execute(
                """
                SELECT t.display_name, COUNT(*) AS recipe_count
                FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id
                JOIN recipes r ON r.id = rt.recipe_id
                WHERE r.visibility = 'shared_epn'
                GROUP BY t.id ORDER BY recipe_count DESC, t.normalized_name LIMIT ?
                """,
                (limit,),
            )
        ]
    return {
        "stats": stats,
        "recent": recent,
        "highest_rated": highest_rated,
        "newest": newest,
        "active_members": active_members,
        "categories": categories,
        "tags": tags,
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


def create_recipe(owner_id: str, form, image_meta: dict | None = None) -> str:
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
    visibility = normalize_visibility(form.get("visibility"))
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO recipes (
                id, owner_id, title, summary, prep_time, servings,
                ingredients_json, steps_json, category_key, created_at, updated_at,
                image_filename, image_media_type, image_width, image_height, image_size, image_sha256, visibility
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                (image_meta or {}).get("image_filename", ""),
                (image_meta or {}).get("image_media_type", ""),
                (image_meta or {}).get("image_width", 0),
                (image_meta or {}).get("image_height", 0),
                (image_meta or {}).get("image_size", 0),
                (image_meta or {}).get("image_sha256", ""),
                visibility,
            ),
        )
        replace_recipe_tags(conn, recipe_id, form.get("tags", ""))
    return recipe_id


def insert_imported_recipe(owner_id: str, recipe: dict, source_installation_id: str, recipe_id: str | None = None) -> str:
    imported_id = recipe_id or recipe["id"]
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, category_key, created_at, updated_at, sync_source_installation_id, image_filename, image_media_type, image_width, image_height, image_size, image_sha256, visibility)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', 0, 0, 0, '', 'shared_epn')
            """,
            (
                imported_id,
                owner_id,
                recipe["title"],
                recipe["summary"],
                recipe["prep_time"],
                recipe["servings"],
                json.dumps(recipe["ingredients"]),
                json.dumps(recipe["steps"]),
                recipe.get("category", ""),
                recipe["created_at"],
                recipe["updated_at"],
                source_installation_id,
            ),
        )
        replace_recipe_tags(conn, imported_id, recipe.get("tags", []))
    return imported_id


def update_imported_recipe(recipe_id: str, recipe: dict, source_installation_id: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "UPDATE recipes SET title=?, summary=?, prep_time=?, servings=?, ingredients_json=?, steps_json=?, category_key=?, updated_at=?, sync_source_installation_id=? WHERE id=?",
            (
                recipe["title"],
                recipe["summary"],
                recipe["prep_time"],
                recipe["servings"],
                json.dumps(recipe["ingredients"]),
                json.dumps(recipe["steps"]),
                recipe.get("category", ""),
                recipe["updated_at"],
                source_installation_id,
                recipe_id,
            ),
        )
        replace_recipe_tags(conn, recipe_id, recipe.get("tags", []))


def update_recipe(recipe_id: str, form, image_meta: dict | None = None) -> None:
    title = form.get("title", "").strip()
    summary = form.get("summary", "").strip()
    ingredients = form.get("ingredients", "")
    steps = form.get("steps", "")
    if not title or len(title) > MAX_RECIPE_TITLE:
        raise ValueError("Recipe title must be between 1 and 200 characters.")
    if len(summary) > MAX_RECIPE_SUMMARY or len(ingredients) > MAX_RECIPE_INGREDIENTS or len(steps) > MAX_RECIPE_STEPS:
        raise ValueError("Recipe text is too long.")
    category = normalize_category(form.get("category", ""))
    visibility = normalize_visibility(form.get("visibility"))
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE recipes
            SET title = ?, summary = ?, prep_time = ?, servings = ?,
                ingredients_json = ?, steps_json = ?, category_key = ?, updated_at = ?,
                image_filename = COALESCE(?, image_filename), image_media_type = COALESCE(?, image_media_type),
                image_width = COALESCE(?, image_width), image_height = COALESCE(?, image_height),
                image_size = COALESCE(?, image_size), image_sha256 = COALESCE(?, image_sha256), visibility = ?
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
                (image_meta or {}).get("image_filename"),
                (image_meta or {}).get("image_media_type"),
                (image_meta or {}).get("image_width"),
                (image_meta or {}).get("image_height"),
                (image_meta or {}).get("image_size"),
                (image_meta or {}).get("image_sha256"),
                visibility,
                recipe_id,
            ),
        )
        replace_recipe_tags(conn, recipe_id, form.get("tags", ""))


def toggle_favorite(user_id: str, recipe_id: str) -> bool:
    with db_connect() as conn:
        existing = conn.execute("SELECT 1 FROM favorites WHERE user_id = ? AND recipe_id = ?", (user_id, recipe_id)).fetchone()
        if existing:
            conn.execute("DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?", (user_id, recipe_id))
            return False
        conn.execute("INSERT INTO favorites (user_id, recipe_id, created_at) VALUES (?, ?, ?)", (user_id, recipe_id, now_iso()))
        return True


def set_recipe_archived(recipe_id: str, archived: bool, user_id: str | None = None) -> None:
    with db_connect() as conn:
        stamp = now_iso() if archived else None
        if user_id:
            conn.execute(
                """INSERT INTO recipe_user_state(user_id, recipe_id, is_archived, archived_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, recipe_id) DO UPDATE SET is_archived=excluded.is_archived,
                archived_at=excluded.archived_at, updated_at=excluded.updated_at""",
                (user_id, recipe_id, int(archived), stamp, now_iso(), now_iso()),
            )
        else:
            conn.execute("UPDATE recipes SET archived_at = ?, updated_at = ? WHERE id = ?", (stamp, now_iso(), recipe_id))


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


def create_comment(recipe_id: str, user_id: str, body: str) -> str:
    body = body.strip()
    if not body or len(body) > MAX_COMMENT_LENGTH:
        raise ValueError("Comments must be between 1 and 2,000 characters.")
    comment_id = f"c-{uuid.uuid4().hex[:10]}"
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO comments (id, recipe_id, user_id, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (comment_id, recipe_id, user_id, body, now_iso()),
        )
    return comment_id


def list_comments(recipe_id: str, viewer_id: str | None, include_hidden: bool = False) -> list[dict]:
    with db_connect() as conn:
        recipe = conn.execute("SELECT id, owner_id, visibility FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not recipe or not viewer_id or (recipe["owner_id"] != viewer_id and recipe["visibility"] != "shared_epn"):
            return []
        query = "SELECT * FROM comments WHERE recipe_id = ? AND deleted_at IS NULL"
        if not include_hidden or recipe["owner_id"] != viewer_id:
            query += " AND hidden_at IS NULL"
        query += " ORDER BY created_at"
        return [dict(row) for row in conn.execute(query, (recipe_id,))]


def update_comment(comment_id: str, body: str) -> None:
    body = body.strip()
    if not body or len(body) > MAX_COMMENT_LENGTH:
        raise ValueError("Comments must be between 1 and 2,000 characters.")
    with db_connect() as conn:
        conn.execute("UPDATE comments SET body = ?, updated_at = ? WHERE id = ?", (body, now_iso(), comment_id))


def delete_comment(comment_id: str) -> None:
    with db_connect() as conn:
        stamp = now_iso()
        conn.execute("UPDATE comments SET deleted_at = ?, updated_at = ? WHERE id = ?", (stamp, stamp, comment_id))


def hide_comment(comment_id: str, user_id: str) -> None:
    with db_connect() as conn:
        stamp = now_iso()
        conn.execute(
            "UPDATE comments SET hidden_at = ?, hidden_by_user_id = ?, updated_at = ? WHERE id = ?", (stamp, user_id, stamp, comment_id)
        )


def unhide_comment(comment_id: str) -> None:
    with db_connect() as conn:
        conn.execute("UPDATE comments SET hidden_at = NULL, hidden_by_user_id = NULL, updated_at = ? WHERE id = ?", (now_iso(), comment_id))


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
