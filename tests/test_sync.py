import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sync import classify_merge, recipe_checksum, token_matches, token_hash, validate_recipe_payload


BASE_RECIPE = {
    "id": "r-demo-1",
    "title": "Tomato Rice",
    "summary": "A quick bowl.",
    "prep_time": "20 min",
    "servings": "2",
    "ingredients": ["rice", "tomato"],
    "steps": ["Cook rice.", "Add tomato."],
    "category": "dinner",
    "tags": [
        {"normalized_name": "quick", "display_name": "Quick"},
        {"normalized_name": "vegetarian", "display_name": "Vegetarian"},
    ],
    "created_at": "2026-08-04T10:00:00+00:00",
    "updated_at": "2026-08-04T10:00:00+00:00",
}


class SyncContractTests(unittest.TestCase):
    def test_checksum_is_stable_and_token_is_compared_without_plaintext(self):
        self.assertEqual(recipe_checksum(dict(BASE_RECIPE)), recipe_checksum(dict(BASE_RECIPE)))
        digest = token_hash("shared-secret")
        self.assertTrue(token_matches("shared-secret", digest))
        self.assertFalse(token_matches("wrong-secret", digest))

    def test_validation_rejects_malformed_recipe(self):
        malformed = dict(BASE_RECIPE, ingredients="not-a-list")
        with self.assertRaises(ValueError):
            validate_recipe_payload(malformed)

    def test_new_update_unchanged_and_conflict_classification(self):
        remote = dict(BASE_RECIPE, updated_at="2026-08-04T11:00:00+00:00")
        self.assertEqual(classify_merge(None, remote, None), "new")
        self.assertEqual(classify_merge(BASE_RECIPE, BASE_RECIPE, recipe_checksum(BASE_RECIPE)), "unchanged")
        self.assertEqual(classify_merge(BASE_RECIPE, remote, recipe_checksum(BASE_RECIPE)), "update")
        local_changed = dict(BASE_RECIPE, title="Local title", updated_at="2026-08-04T11:01:00+00:00")
        remote_changed = dict(BASE_RECIPE, title="Remote title", updated_at="2026-08-04T11:02:00+00:00")
        self.assertEqual(classify_merge(local_changed, remote_changed, recipe_checksum(BASE_RECIPE)), "conflict")

    def test_category_and_tags_are_checksum_fields_but_tag_order_is_ignored(self):
        reversed_tags = dict(BASE_RECIPE, tags=list(reversed(BASE_RECIPE["tags"])))
        self.assertEqual(recipe_checksum(BASE_RECIPE), recipe_checksum(reversed_tags))
        self.assertNotEqual(recipe_checksum(BASE_RECIPE), recipe_checksum(dict(BASE_RECIPE, category="breakfast")))
        self.assertNotEqual(
            recipe_checksum(BASE_RECIPE), recipe_checksum(dict(BASE_RECIPE, tags=[{"normalized_name": "quick", "display_name": "Quick"}]))
        )

    def test_older_payload_defaults_category_and_tags(self):
        older = dict(BASE_RECIPE)
        older.pop("category")
        older.pop("tags")
        normalized = validate_recipe_payload(older)
        self.assertEqual(normalized["category"], "")
        self.assertEqual(normalized["tags"], [])

    def test_invalid_category_and_tags_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_recipe_payload(dict(BASE_RECIPE, category="Not A Valid Category!"))
        with self.assertRaises(ValueError):
            validate_recipe_payload(dict(BASE_RECIPE, tags=[{"normalized_name": "", "display_name": ""}]))


class SyncApiTests(unittest.TestCase):
    def setUp(self):
        import app as recipe_app

        self.recipe_app = recipe_app
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        recipe_app.DATA_DIR = root
        recipe_app.UPLOAD_DIR = root / "uploads"
        recipe_app.DB_FILE = root / "recipe_box.db"
        recipe_app.UPLOAD_DIR.mkdir()
        self.env = patch.dict(os.environ, {"SYNC_TOKEN": "test-sync-token", "SECRET_KEY": "test-secret"})
        self.env.start()
        recipe_app.init_db()
        self.client = recipe_app.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.tempdir.cleanup()

    def test_manifest_requires_authentication_and_returns_recipe(self):
        response = self.client.get("/api/sync/manifest")
        self.assertEqual(response.status_code, 401)
        with self.recipe_app.db_connect() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                ("u-1", "test@example.com", "unused", self.recipe_app.now_iso()),
            )
            conn.execute(
                "INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    BASE_RECIPE["id"],
                    "u-1",
                    BASE_RECIPE["title"],
                    BASE_RECIPE["summary"],
                    BASE_RECIPE["prep_time"],
                    BASE_RECIPE["servings"],
                    json.dumps(BASE_RECIPE["ingredients"]),
                    json.dumps(BASE_RECIPE["steps"]),
                    BASE_RECIPE["created_at"],
                    BASE_RECIPE["updated_at"],
                ),
            )
        response = self.client.get("/api/sync/manifest", headers={"Authorization": "Bearer test-sync-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["recipes"][0]["id"], BASE_RECIPE["id"])

    def test_manifest_includes_category_and_tags(self):
        with self.recipe_app.db_connect() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                ("u-1", "test@example.com", "unused", self.recipe_app.now_iso()),
            )
            conn.execute(
                "INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, category_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    BASE_RECIPE["id"],
                    "u-1",
                    BASE_RECIPE["title"],
                    BASE_RECIPE["summary"],
                    BASE_RECIPE["prep_time"],
                    BASE_RECIPE["servings"],
                    json.dumps(BASE_RECIPE["ingredients"]),
                    json.dumps(BASE_RECIPE["steps"]),
                    "dinner",
                    BASE_RECIPE["created_at"],
                    BASE_RECIPE["updated_at"],
                ),
            )
            self.recipe_app.replace_recipe_tags(conn, BASE_RECIPE["id"], BASE_RECIPE["tags"])
        response = self.client.get("/api/sync/manifest", headers={"Authorization": "Bearer test-sync-token"})
        payload = response.get_json()["recipes"][0]
        self.assertEqual(payload["category"], "dinner")
        self.assertEqual([tag["normalized_name"] for tag in payload["tags"]], ["quick", "vegetarian"])

    def test_sync_dashboard_does_not_render_peer_token(self):
        with self.recipe_app.db_connect() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, nickname, created_at) VALUES (?, ?, ?, ?, ?)",
                ("u-1", "test@example.com", "unused", "Tester", self.recipe_app.now_iso()),
            )
            conn.execute(
                "INSERT INTO sync_peers (id, name, url, token, created_at) VALUES (?, ?, ?, ?, ?)",
                ("p-1", "Peer", "http://localhost:5001", "do-not-render", self.recipe_app.now_iso()),
            )
        with self.client.session_transaction() as session:
            session["user_id"] = "u-1"
        response = self.client.get("/sync")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"do-not-render", response.data)


if __name__ == "__main__":
    unittest.main()
