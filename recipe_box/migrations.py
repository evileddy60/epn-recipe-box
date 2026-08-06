from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

SCHEMA_VERSION = 14

# Compact fixtures represent the three schemas that have existed in the project.
HISTORICAL_SCHEMAS = {
    "legacy": """
        CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, nickname TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE recipes (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, prep_time TEXT NOT NULL, servings TEXT NOT NULL, ingredients_json TEXT NOT NULL, steps_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE inventory_items (user_id TEXT NOT NULL, item TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (user_id, item));
        CREATE TABLE ratings (recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, score INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (recipe_id, user_id));
        CREATE TABLE comments (id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO users VALUES ('legacy-user', 'legacy@example.com', 'hash', 'Legacy', '', '', '2026-01-01');
    """,
    "sync": """
        CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, nickname TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE recipes (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, prep_time TEXT NOT NULL, servings TEXT NOT NULL, ingredients_json TEXT NOT NULL, steps_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sync_source_installation_id TEXT NOT NULL DEFAULT '');
        CREATE TABLE inventory_items (user_id TEXT NOT NULL, item TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (user_id, item));
        CREATE TABLE ratings (recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, score INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (recipe_id, user_id));
        CREATE TABLE comments (id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE installations (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE sync_peers (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_sync_at TEXT, created_at TEXT NOT NULL);
        INSERT INTO users VALUES ('sync-user', 'sync@example.com', 'hash', 'Sync', '', '', '2026-01-01');
    """,
    "categories_tags": """
        CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, nickname TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE recipes (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, prep_time TEXT NOT NULL, servings TEXT NOT NULL, ingredients_json TEXT NOT NULL, steps_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sync_source_installation_id TEXT NOT NULL DEFAULT '', category_key TEXT NOT NULL DEFAULT '');
        CREATE TABLE inventory_items (user_id TEXT NOT NULL, item TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (user_id, item));
        CREATE TABLE ratings (recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, score INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (recipe_id, user_id));
        CREATE TABLE comments (id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE categories (category_key TEXT PRIMARY KEY, display_name TEXT NOT NULL);
        CREATE TABLE tags (id TEXT PRIMARY KEY, normalized_name TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL);
        CREATE TABLE recipe_tags (recipe_id TEXT NOT NULL, tag_id TEXT NOT NULL, PRIMARY KEY (recipe_id, tag_id));
        INSERT INTO users VALUES ('tag-user', 'tag@example.com', 'hash', 'Tags', '', '', '2026-01-01');
    """,
}

Migration = tuple[int, Callable[[sqlite3.Connection], None]]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _create_core(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE DEFAULT '', password_hash TEXT NOT NULL DEFAULT '', nickname TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS inventory_items (user_id TEXT NOT NULL, item TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (user_id, item), FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS recipes (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', prep_time TEXT NOT NULL DEFAULT '', servings TEXT NOT NULL DEFAULT '', ingredients_json TEXT NOT NULL DEFAULT '[]', steps_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '', FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS ratings (recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, score INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (recipe_id, user_id), FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS comments (id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE);
    """)


def _migration_1(conn: sqlite3.Connection) -> None:
    _create_core(conn)
    user_columns = _columns(conn, "users")
    if "email" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "password_hash" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
    if "nickname" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        if "name" in user_columns:
            conn.execute("UPDATE users SET nickname = name WHERE nickname = ''")


def _migration_2(conn: sqlite3.Connection) -> None:
    if "sync_source_installation_id" not in _columns(conn, "recipes"):
        conn.execute("ALTER TABLE recipes ADD COLUMN sync_source_installation_id TEXT NOT NULL DEFAULT ''")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS installations (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_peers (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_sync_at TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_baselines (peer_id TEXT NOT NULL, recipe_id TEXT NOT NULL, checksum TEXT NOT NULL, remote_updated_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (peer_id, recipe_id), FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS sync_conflicts (id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, recipe_id TEXT NOT NULL, local_json TEXT NOT NULL, remote_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT, FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS sync_history (id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, summary_json TEXT NOT NULL, FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE);
    """)


def _migration_3(conn: sqlite3.Connection) -> None:
    if "category_key" not in _columns(conn, "recipes"):
        conn.execute("ALTER TABLE recipes ADD COLUMN category_key TEXT NOT NULL DEFAULT ''")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS categories (category_key TEXT PRIMARY KEY, display_name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tags (id TEXT PRIMARY KEY, normalized_name TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recipe_tags (recipe_id TEXT NOT NULL, tag_id TEXT NOT NULL, PRIMARY KEY (recipe_id, tag_id), FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE, FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE);
    """)
    conn.executemany(
        "INSERT OR IGNORE INTO categories (category_key, display_name) VALUES (?, ?)",
        (
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
        ),
    )


def _migration_4(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_recipes_owner ON recipes(owner_id);
        CREATE INDEX IF NOT EXISTS idx_recipes_category ON recipes(category_key);
        CREATE INDEX IF NOT EXISTS idx_recipe_tags_tag ON recipe_tags(tag_id);
    """)


def _migration_5(conn: sqlite3.Connection) -> None:
    recipe_columns = _columns(conn, "recipes")
    for column, definition in (
        ("image_filename", "TEXT NOT NULL DEFAULT ''"),
        ("image_media_type", "TEXT NOT NULL DEFAULT ''"),
        ("image_width", "INTEGER NOT NULL DEFAULT 0"),
        ("image_height", "INTEGER NOT NULL DEFAULT 0"),
        ("image_size", "INTEGER NOT NULL DEFAULT 0"),
        ("image_sha256", "TEXT NOT NULL DEFAULT ''"),
        ("archived_at", "TEXT"),
    ):
        if column not in recipe_columns:
            conn.execute(f"ALTER TABLE recipes ADD COLUMN {column} {definition}")


def _migration_6(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS favorites (
            user_id TEXT NOT NULL,
            recipe_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, recipe_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_favorites_recipe ON favorites(recipe_id);
    """)


def _migration_7(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);
        CREATE INDEX IF NOT EXISTS idx_api_tokens_expiry ON api_tokens(expires_at);
    """)


def _migration_8(conn: sqlite3.Connection) -> None:
    recipe_columns = _columns(conn, "recipes")
    if "visibility" not in recipe_columns:
        conn.execute("ALTER TABLE recipes ADD COLUMN visibility TEXT NOT NULL DEFAULT 'shared_epn'")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recipe_user_state (
            user_id TEXT NOT NULL, recipe_id TEXT NOT NULL,
            is_favorite INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            archived_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, recipe_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_recipe_user_state_recipe ON recipe_user_state(recipe_id);
        CREATE INDEX IF NOT EXISTS idx_recipe_user_state_archive ON recipe_user_state(user_id, is_archived);
    """)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO recipe_user_state
            (user_id, recipe_id, is_favorite, is_archived, archived_at, created_at, updated_at)
        SELECT r.owner_id, r.id,
               CASE WHEN EXISTS (SELECT 1 FROM favorites f WHERE f.user_id = r.owner_id AND f.recipe_id = r.id) THEN 1 ELSE 0 END,
               CASE WHEN r.archived_at IS NOT NULL THEN 1 ELSE 0 END, r.archived_at, ?, ?
        FROM recipes r
    """,
        (now, now),
    )
    conn.execute("""
        INSERT INTO recipe_user_state
            (user_id, recipe_id, is_favorite, is_archived, archived_at, created_at, updated_at)
        SELECT f.user_id, f.recipe_id, 1, 0, NULL, f.created_at, f.created_at
        FROM favorites f
        WHERE NOT EXISTS (SELECT 1 FROM recipe_user_state s WHERE s.user_id=f.user_id AND s.recipe_id=f.recipe_id)
    """)


def _migration_9(conn: sqlite3.Connection) -> None:
    comment_columns = _columns(conn, "comments")
    for column, definition in (("updated_at", "TEXT"), ("deleted_at", "TEXT"), ("hidden_at", "TEXT")):
        if column not in comment_columns:
            conn.execute(f"ALTER TABLE comments ADD COLUMN {column} {definition}")
    conn.execute("UPDATE comments SET updated_at = created_at WHERE updated_at IS NULL")


def _migration_10(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS collections (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'private',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(owner_id, name),
            FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS collection_recipes (
            collection_id TEXT NOT NULL, recipe_id TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            PRIMARY KEY (collection_id, recipe_id),
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_collection_recipes_recipe ON collection_recipes(recipe_id);
    """)


def _migration_11(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS activity_events (
            id TEXT PRIMARY KEY, user_id TEXT, event_type TEXT NOT NULL,
            recipe_id TEXT, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_activity_events_created ON activity_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_recipe ON activity_events(recipe_id);
    """)


def _migration_12(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recipe_visibility_users (
            recipe_id TEXT NOT NULL, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (recipe_id, user_id),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_recipe_visibility_users_user ON recipe_visibility_users(user_id);
    """)


def _migration_13(conn: sqlite3.Connection) -> None:
    if "visibility" not in _columns(conn, "collections"):
        conn.execute("ALTER TABLE collections ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")


def _migration_14(conn: sqlite3.Connection) -> None:
    comment_columns = _columns(conn, "comments")
    if "hidden_by_user_id" not in comment_columns:
        conn.execute("ALTER TABLE comments ADD COLUMN hidden_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_hidden_by ON comments(hidden_by_user_id)")


def _ordered_migrations() -> tuple[Migration, ...]:
    return (
        (1, _migration_1),
        (2, _migration_2),
        (3, _migration_3),
        (4, _migration_4),
        (5, _migration_5),
        (6, _migration_6),
        (7, _migration_7),
        (8, _migration_8),
        (9, _migration_9),
        (10, _migration_10),
        (11, _migration_11),
        (12, _migration_12),
        (13, _migration_13),
        (14, _migration_14),
    )


def schema_version(path: Path) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(path) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone():
            return 0
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        return int(row[0]) if row else 0


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.pre-sync-{stamp}.bak")


def _sync_legacy_user_state(conn: sqlite3.Connection) -> None:
    """Keep state compatible with older writers that still touch favorites/archive columns."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "recipe_user_state" not in tables or "recipes" not in tables:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO recipe_user_state
            (user_id, recipe_id, is_favorite, is_archived, archived_at, created_at, updated_at)
        SELECT r.owner_id, r.id, 0, 0, NULL, ?, ? FROM recipes r
    """,
        (now, now),
    )
    if "favorites" in tables:
        conn.execute(
            """
            UPDATE recipe_user_state SET is_favorite = 1, updated_at = ?
            WHERE EXISTS (SELECT 1 FROM favorites f WHERE f.user_id=recipe_user_state.user_id AND f.recipe_id=recipe_user_state.recipe_id)
        """,
            (now,),
        )
    conn.execute(
        """
        UPDATE recipe_user_state SET is_archived = CASE WHEN r.archived_at IS NULL THEN 0 ELSE 1 END,
            archived_at = r.archived_at, updated_at = ?
        FROM recipes r
        WHERE r.owner_id=recipe_user_state.user_id AND r.id=recipe_user_state.recipe_id
    """,
        (now,),
    )


def migrate_database(path: Path, migrations_override: Iterable[Migration] | None = None) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and schema_version(path) < SCHEMA_VERSION:
        shutil.copy2(path, _backup_path(path))
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        current = schema_version(path)
        ordered = tuple(migrations_override) if migrations_override is not None else _ordered_migrations()
        conn.execute("BEGIN")
        for version, migration in ordered:
            if migrations_override is None and version <= current:
                continue
            try:
                migration(conn)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", (version, datetime.now(timezone.utc).isoformat())
                )
                current = version
            except Exception:
                conn.rollback()
                if migrations_override is not None:
                    conn.execute("DELETE FROM schema_version WHERE version >= 8")
                    conn.commit()
                raise
        _sync_legacy_user_state(conn)
        conn.commit()
        return current
    finally:
        conn.close()
