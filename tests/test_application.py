import io
import json
import os
import sqlite3
import unittest
from pathlib import Path

from PIL import Image


class ApplicationFlowTests(unittest.TestCase):
    def setUp(self):
        import app as recipe_app

        self.recipe_app = recipe_app
        self.tempdir = __import__("tempfile").TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.original = {name: getattr(recipe_app, name) for name in ("DATA_DIR", "UPLOAD_DIR", "DB_FILE", "STATIC_DIR")}
        recipe_app.DATA_DIR = root
        recipe_app.UPLOAD_DIR = root / "uploads"
        recipe_app.DB_FILE = root / "recipe_box.db"
        recipe_app.UPLOAD_DIR.mkdir()
        self.previous_token = os.environ.get("SYNC_TOKEN")
        self.previous_secret = os.environ.get("SECRET_KEY")
        os.environ["SYNC_TOKEN"] = "application-test-token"
        os.environ["SECRET_KEY"] = "application-test-secret"
        recipe_app.init_db()
        recipe_app.app.config.update(TESTING=True, ENFORCE_CSRF=False)
        self.client = recipe_app.app.test_client()

    def tearDown(self):
        if self.previous_token is None:
            os.environ.pop("SYNC_TOKEN", None)
        else:
            os.environ["SYNC_TOKEN"] = self.previous_token
        if self.previous_secret is None:
            os.environ.pop("SECRET_KEY", None)
        else:
            os.environ["SECRET_KEY"] = self.previous_secret
        for name, value in self.original.items():
            setattr(self.recipe_app, name, value)
        self.tempdir.cleanup()

    def signup_and_profile(self, email="cook@example.com"):
        response = self.client.post(
            "/signup",
            data={"mode": "signup", "email": email, "password": "correct-horse"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            "/profile/setup",
            data={"nickname": "Cook", "bio": "Home cooking"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

    def test_signup_login_logout_and_authorization(self):
        self.signup_and_profile()
        response = self.client.post("/logout")
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            "/signup",
            data={"mode": "login", "email": "cook@example.com", "password": "correct-horse"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.location)

        anonymous = self.recipe_app.app.test_client()
        response = anonymous.get("/inventory")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/signup", response.location)

    def test_recipe_inventory_rating_comment_upload_and_sharing(self):
        self.signup_and_profile()
        response = self.client.post(
            "/recipes/new",
            data={
                "title": "Tomato Rice",
                "summary": "A quick bowl.",
                "prep_time": "20 min",
                "servings": "2",
                "ingredients": "rice\ntomato",
                "steps": "Cook rice.\nAdd tomato.",
            },
        )
        self.assertEqual(response.status_code, 302)
        recipe_url = response.location
        recipe_id = recipe_url.rsplit("/", 1)[-1]
        self.assertEqual(self.client.get(recipe_url).status_code, 200)

        response = self.client.post(
            f"/recipes/{recipe_id}/edit",
            data={
                "title": "Updated Tomato Rice",
                "summary": "Still quick.",
                "prep_time": "25 min",
                "servings": "2",
                "ingredients": "rice\ntomato",
                "steps": "Cook rice.\nAdd tomato.\nServe.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.post(f"/recipes/{recipe_id}/rate", data={"score": "5"}).status_code, 302)
        self.assertEqual(self.client.post(f"/recipes/{recipe_id}/comment", data={"body": "Excellent."}).status_code, 302)

        response = self.client.post("/inventory", data={"inventory": "rice\negg\ngarlic\nsoy sauce"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Stock Pot Fried Rice", response.data)
        response = self.client.post("/inventory/generated/save", data={"idea_id": "fried-rice", "kind": "now"})
        self.assertEqual(response.status_code, 302)

        avatar_image = io.BytesIO()
        Image.new("RGB", (20, 20), "blue").save(avatar_image, format="PNG")
        avatar_image.seek(0)
        response = self.client.post(
            "/profile/setup",
            data={
                "nickname": "Cook",
                "bio": "Home cooking",
                "avatar": (avatar_image, "avatar.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with self.recipe_app.db_connect() as conn:
            avatar = conn.execute("SELECT avatar FROM users LIMIT 1").fetchone()["avatar"]
            rating = conn.execute("SELECT score FROM ratings LIMIT 1").fetchone()["score"]
            comment = conn.execute("SELECT body FROM comments LIMIT 1").fetchone()["body"]
        self.assertTrue(avatar.startswith("uploads/avatar-"))
        avatar_response = self.client.get("/uploads/" + avatar.removeprefix("uploads/"))
        try:
            self.assertEqual(avatar_response.status_code, 200)
        finally:
            avatar_response.close()
        self.assertEqual(rating, 5)
        self.assertEqual(comment, "Excellent.")

        anonymous = self.recipe_app.app.test_client()
        self.assertEqual(anonymous.get(recipe_url).status_code, 200)

    def test_sync_conflict_resolution_modes(self):
        self.signup_and_profile()
        local = {
            "id": "r-conflict",
            "title": "Local title",
            "summary": "Local summary",
            "prep_time": "10 min",
            "servings": "1",
            "ingredients": ["rice"],
            "steps": ["Cook locally."],
            "category": "lunch",
            "tags": [{"normalized_name": "local", "display_name": "local"}],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T01:00:00+00:00",
        }
        remote = dict(
            local,
            title="Remote title",
            steps=["Cook remotely."],
            category="dinner",
            tags=[{"normalized_name": "remote", "display_name": "remote"}],
            updated_at="2026-01-01T02:00:00+00:00",
        )
        with self.recipe_app.db_connect() as conn:
            user_id = conn.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO sync_peers (id, name, url, token, created_at) VALUES ('peer-test', 'Test peer', 'http://peer.test', 'token', ?)",
                (self.recipe_app.now_iso(),),
            )
            conn.execute(
                "INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, category_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    local["id"],
                    user_id,
                    local["title"],
                    local["summary"],
                    local["prep_time"],
                    local["servings"],
                    json.dumps(local["ingredients"]),
                    json.dumps(local["steps"]),
                    local["category"],
                    local["created_at"],
                    local["updated_at"],
                ),
            )
            self.recipe_app.replace_recipe_tags(conn, local["id"], local["tags"])
            for index, resolution in enumerate(("keep_local", "use_remote", "keep_both")):
                conflict_id = f"conflict-{index}"
                conn.execute(
                    "INSERT INTO sync_conflicts (id, peer_id, recipe_id, local_json, remote_json, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                    (conflict_id, "peer-test", local["id"], json.dumps(local), json.dumps(remote), self.recipe_app.now_iso()),
                )
                conn.commit()
                result = self.recipe_app.resolve_sync_conflict_action(conflict_id, resolution)
                self.assertEqual(result, {"status": "resolved", "resolution": resolution})
            titles = [row["title"] for row in conn.execute("SELECT title FROM recipes ORDER BY id")]
            categories = [row["category_key"] for row in conn.execute("SELECT category_key FROM recipes ORDER BY id")]
            all_tags = [row["normalized_name"] for row in conn.execute("SELECT normalized_name FROM tags ORDER BY normalized_name")]
        self.assertIn("Remote title", titles)
        self.assertIn("dinner", categories)
        self.assertIn("remote", all_tags)
        self.assertEqual(len(titles), 2)

    def test_recipe_category_tags_search_filters_and_editing(self):
        self.signup_and_profile()
        response = self.client.post(
            "/recipes/new",
            data={
                "title": "Spicy Chicken Bowl",
                "summary": "A quick weeknight dinner.",
                "prep_time": "25 min",
                "servings": "2",
                "ingredients": "chicken\nrice",
                "steps": "Cook chicken.\nServe.",
                "category": "dinner",
                "tags": "Quick,  high-protein, quick",
            },
        )
        self.assertEqual(response.status_code, 302)
        recipe_id = response.location.rsplit("/", 1)[-1]
        with self.recipe_app.db_connect() as conn:
            recipe = conn.execute("SELECT category_key FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            tags = [
                row["normalized_name"]
                for row in conn.execute(
                    "SELECT t.normalized_name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id WHERE rt.recipe_id = ? ORDER BY t.normalized_name",
                    (recipe_id,),
                )
            ]
        self.assertEqual(recipe["category_key"], "dinner")
        self.assertEqual(tags, ["high-protein", "quick"])

        response = self.client.get("/?q=  CHICKEN  ")
        self.assertIn(b"Spicy Chicken Bowl", response.data)
        response = self.client.get("/?category=dinner&tag=quick")
        self.assertIn(b"Spicy Chicken Bowl", response.data)
        response = self.client.get("/?q=does-not-exist")
        self.assertIn(b"No recipes matched", response.data)
        response = self.client.post(
            f"/recipes/{recipe_id}/edit",
            data={
                "title": "Spicy Chicken Bowl",
                "summary": "Updated dinner.",
                "prep_time": "30 min",
                "servings": "2",
                "ingredients": "chicken\nrice",
                "steps": "Cook chicken.\nServe.",
                "category": "lunch",
                "tags": "Family favorite",
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.recipe_app.db_connect() as conn:
            recipe = conn.execute("SELECT category_key FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
            tags = [
                row["normalized_name"]
                for row in conn.execute(
                    "SELECT t.normalized_name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id WHERE rt.recipe_id = ?", (recipe_id,)
                )
            ]
        self.assertEqual(recipe["category_key"], "lunch")
        self.assertEqual(tags, ["family-favorite"])

    def test_legacy_recipe_gets_uncategorized_and_no_tags(self):
        self.signup_and_profile()
        with self.recipe_app.db_connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)")}
            self.assertIn("category_key", columns)
            user_id = conn.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "r-legacy",
                    user_id,
                    "Legacy Soup",
                    "Old card",
                    "20 min",
                    "2",
                    json.dumps(["broth"]),
                    json.dumps(["Cook."]),
                    self.recipe_app.now_iso(),
                    self.recipe_app.now_iso(),
                ),
            )
        response = self.client.get("/?q=legacy")
        self.assertIn(b"Legacy Soup", response.data)
        self.assertIn(b"Uncategorized", response.data)

    def test_tag_limits_and_invalid_category_are_rejected(self):
        self.signup_and_profile()
        base = {
            "title": "Bounded Recipe",
            "summary": "A bounded card.",
            "prep_time": "10 min",
            "servings": "1",
            "ingredients": "rice",
            "steps": "Cook.",
            "category": "not valid!",
            "tags": "one, two",
        }
        response = self.client.post("/recipes/new", data=base)
        self.assertEqual(response.status_code, 422)
        base["category"] = "dinner"
        base["tags"] = ", ".join(f"tag-{index}" for index in range(13))
        response = self.client.post("/recipes/new", data=base)
        self.assertEqual(response.status_code, 422)
        base["tags"] = "x" * 41
        response = self.client.post("/recipes/new", data=base)
        self.assertEqual(response.status_code, 422)

    def test_existing_database_migration_preserves_legacy_rows(self):
        self.recipe_app.DB_FILE.unlink()
        with sqlite3.connect(self.recipe_app.DB_FILE) as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    bio TEXT NOT NULL DEFAULT '',
                    avatar TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE recipes (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    prep_time TEXT NOT NULL,
                    servings TEXT NOT NULL,
                    ingredients_json TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO users VALUES ('legacy-user', 'Legacy Cook', '', '', '2026-01-01T00:00:00+00:00');
                """
            )
        self.recipe_app.init_db()
        with self.recipe_app.db_connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            user = conn.execute("SELECT nickname FROM users WHERE id = 'legacy-user'").fetchone()
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"email", "password_hash", "nickname"}.issubset(columns))
        self.assertEqual(user["nickname"], "Legacy Cook")
        self.assertTrue({"installations", "sync_peers", "sync_baselines", "sync_conflicts", "sync_history"}.issubset(tables))
        self.assertTrue(list(self.recipe_app.DATA_DIR.glob("recipe_box.db.pre-sync-*.bak")))


if __name__ == "__main__":
    unittest.main()
