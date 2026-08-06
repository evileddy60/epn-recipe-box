import sqlite3
import tempfile
import unittest
from pathlib import Path


class CommunityMigrationTests(unittest.TestCase):
    def test_schema_8_creates_state_visibility_collections_comments_activity_and_migrates_legacy(self):
        from recipe_box import migrations

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "box.db"
            conn = sqlite3.connect(path)
            conn.executescript(migrations.HISTORICAL_SCHEMAS["legacy"])
            conn.execute("INSERT INTO recipes VALUES ('r1','legacy-user','Legacy Recipe','','','','[]','[]','now','now')")
            conn.commit()
            conn.close()
            migrations.migrate_database(path)
            with sqlite3.connect(path) as seeded:
                seeded.execute("INSERT INTO favorites VALUES ('legacy-user','r1','now')")
                seeded.commit()
            self.assertEqual(migrations.migrate_database(path), migrations.SCHEMA_VERSION)
            with sqlite3.connect(path) as check:
                tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"recipe_user_state", "collections", "collection_recipes", "activity_events"} <= tables)
                cols = {r[1] for r in check.execute("PRAGMA table_info(recipes)")}
                self.assertIn("visibility", cols)
                comment_cols = {r[1] for r in check.execute("PRAGMA table_info(comments)")}
                self.assertTrue({"updated_at", "deleted_at", "hidden_at", "hidden_by_user_id"} <= comment_cols)
                state = check.execute("SELECT is_favorite, is_archived FROM recipe_user_state").fetchone()
                self.assertEqual(tuple(state), (1, 0))

    def test_schema_7_migration_preserves_owner_archive_and_legacy_comments(self):
        from recipe_box import migrations

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "box.db"
            with sqlite3.connect(path) as conn:
                conn.executescript(migrations.HISTORICAL_SCHEMAS["legacy"])
                conn.execute("INSERT INTO recipes VALUES ('r1','legacy-user','Legacy Recipe','','','','[]','[]','now','now')")
                conn.execute("INSERT INTO comments VALUES ('c1','r1','legacy-user','old comment','then')")
            migrations.migrate_database(path)
            with sqlite3.connect(path) as conn:
                conn.execute("UPDATE recipes SET archived_at = 'archived' WHERE id = 'r1'")
                conn.execute("INSERT INTO favorites VALUES ('legacy-user','r1','now')")
            migrations.migrate_database(path)
            with sqlite3.connect(path) as conn:
                state = conn.execute(
                    "SELECT is_favorite, is_archived, archived_at FROM recipe_user_state WHERE user_id = ? AND recipe_id = ?",
                    ("legacy-user", "r1"),
                ).fetchone()
                # Legacy archive state is imported during the migration that creates recipe_user_state;
                # later startup must not overwrite user-local state from the legacy global column.
                self.assertEqual(state, (1, 0, None))
                self.assertEqual(conn.execute("SELECT body, created_at FROM comments WHERE id = 'c1'").fetchone(), ("old comment", "then"))

    def test_community_migrations_are_idempotent_and_have_expected_columns(self):
        from recipe_box import migrations

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "box.db"
            migrations.migrate_database(path)
            with sqlite3.connect(path) as conn:
                expected = {
                    "recipe_user_state": {"user_id", "recipe_id", "is_favorite", "is_archived", "archived_at", "created_at", "updated_at"},
                    "collections": {"id", "owner_id", "name", "created_at", "updated_at"},
                    "collection_recipes": {"collection_id", "recipe_id", "position", "created_at"},
                    "activity_events": {"id", "user_id", "event_type", "recipe_id", "payload_json", "created_at"},
                }
                for table, columns in expected.items():
                    self.assertTrue(columns <= {row[1] for row in conn.execute(f"PRAGMA table_info({table})")})
                self.assertEqual(
                    {"updated_at", "deleted_at", "hidden_at", "hidden_by_user_id"}
                    <= {row[1] for row in conn.execute("PRAGMA table_info(comments)")},
                    True,
                )
                self.assertEqual(conn.execute("SELECT visibility FROM recipes WHERE id = 'r1'").fetchone(), None)
            self.assertEqual(migrations.migrate_database(path), migrations.SCHEMA_VERSION)

    def test_community_migration_failure_rolls_back_schema_and_data(self):
        from recipe_box import migrations

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "box.db"
            migrations.migrate_database(path)
            with sqlite3.connect(path) as conn:
                conn.execute("INSERT INTO users VALUES ('u','u@example.com','hash','','','','now')")

            def broken(conn):
                conn.execute("CREATE TABLE rollback_probe (id TEXT)")
                conn.execute("UPDATE users SET nickname = 'changed'")
                raise RuntimeError("boom")

            with self.assertRaises(RuntimeError):
                migrations.migrate_database(path, migrations_override=[(8, broken)])
            with sqlite3.connect(path) as conn:
                self.assertEqual(migrations.schema_version(path), 7)
                self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'rollback_probe'").fetchone())
                self.assertEqual(conn.execute("SELECT nickname FROM users WHERE id = 'u'").fetchone()[0], "")


class CommunityPolicyTests(unittest.TestCase):
    def test_visibility_policy_is_owner_or_shared_epn_only(self):
        from recipe_box.policies import can_view_recipe

        recipe = {"owner_id": "owner", "visibility": "private"}
        self.assertTrue(can_view_recipe(recipe, "owner"))
        self.assertFalse(can_view_recipe(recipe, "other"))
        recipe["visibility"] = "shared_epn"
        self.assertTrue(can_view_recipe(recipe, "other"))
        recipe["visibility"] = "selected_users"
        self.assertFalse(can_view_recipe(recipe, "other", {"other"}))

    def test_public_visibility_is_disabled_for_private_beta(self):
        from recipe_box.policies import normalize_visibility

        with self.assertRaises(ValueError):
            normalize_visibility("public")

        with self.assertRaises(ValueError):
            normalize_visibility("selected_users")
