import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class CommentModerationTests(unittest.TestCase):
    def setUp(self):
        import app as recipe_app

        self.recipe_app = recipe_app
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.original = {
            name: getattr(recipe_app, name) for name in ("DATA_DIR", "UPLOAD_DIR", "RECIPE_IMAGE_DIR", "DB_FILE", "STATIC_DIR")
        }
        recipe_app.DATA_DIR = root
        recipe_app.UPLOAD_DIR = root / "uploads"
        recipe_app.RECIPE_IMAGE_DIR = root / "recipe-images"
        recipe_app.DB_FILE = root / "recipe_box.db"
        recipe_app.UPLOAD_DIR.mkdir()
        recipe_app.RECIPE_IMAGE_DIR.mkdir()
        self.previous = {name: os.environ.get(name) for name in ("SYNC_TOKEN", "SECRET_KEY", "EPN_ENV")}
        os.environ["SYNC_TOKEN"] = "comment-test-token"
        os.environ["SECRET_KEY"] = "comment-test-secret"
        os.environ["EPN_ENV"] = "development"
        recipe_app.init_db()
        recipe_app.app.config.update(TESTING=True, ENFORCE_CSRF=True)
        self.client = recipe_app.app.test_client()
        self.owner = recipe_app.create_account("owner@example.com", "correct-horse")
        self.author = recipe_app.create_account("author@example.com", "correct-horse")
        self.other = recipe_app.create_account("other@example.com", "correct-horse")
        for user_id, nickname in ((self.owner, "Owner"), (self.author, "Author"), (self.other, "Other")):
            recipe_app.update_profile(user_id, nickname, "", "")
        with recipe_app.db_connect() as conn:
            conn.execute(
                "INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, created_at, updated_at, visibility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("recipe-1", self.owner, "Shared recipe", "", "", "", "[]", "[]", "now", "now", "shared_epn"),
            )
            conn.execute(
                "INSERT INTO comments (id, recipe_id, user_id, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("comment-1", "recipe-1", self.author, "Visible comment", "now", "now"),
            )

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in self.original.items():
            setattr(self.recipe_app, name, value)
        self.tempdir.cleanup()

    def api_token(self, email):
        response = self.client.post("/api/v1/auth/login", json={"email": email, "password": "correct-horse"})
        self.assertEqual(response.status_code, 200)
        return response.get_json()["token"]

    def browser_as(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf-test-token"
            session.permanent = True

    def test_schema_adds_moderation_actor_and_preserves_deleted_state_from_schema_7(self):
        from recipe_box import migrations

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            with sqlite3.connect(path) as conn:
                conn.executescript(migrations.HISTORICAL_SCHEMAS["legacy"])
                conn.execute("INSERT INTO recipes VALUES ('r','legacy-user','Recipe','','','','[]','[]','now','now')")
                conn.execute("INSERT INTO comments VALUES ('c','r','legacy-user','old','then')")
            self.assertEqual(migrations.migrate_database(path), 16)
            self.assertEqual(migrations.migrate_database(path), 16)
            with sqlite3.connect(path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(comments)")}
                self.assertTrue({"hidden_at", "hidden_by_user_id"} <= columns)
                self.assertEqual(
                    conn.execute("SELECT body, deleted_at, hidden_at FROM comments WHERE id='c'").fetchone(), ("old", None, None)
                )

    def test_backup_restore_preserves_moderation_audit_fields(self):
        from tools.recipe_box_backup import backup_database, restore_database, verify_database
        from recipe_box import migrations

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.db"
            restored = root / "restored.db"
            migrations.migrate_database(source)
            with sqlite3.connect(source) as conn:
                conn.execute("INSERT INTO users (id,email,password_hash,created_at) VALUES ('owner','owner@example.com','hash','now')")
                conn.execute("INSERT INTO users (id,email,password_hash,created_at) VALUES ('author','author@example.com','hash','now')")
                conn.execute(
                    "INSERT INTO recipes (id,owner_id,title,summary,prep_time,servings,ingredients_json,steps_json,created_at,updated_at,visibility) VALUES ('r','owner','R','','','','[]','[]','now','now','shared_epn')"
                )
                conn.execute(
                    "INSERT INTO comments (id,recipe_id,user_id,body,created_at,hidden_at,hidden_by_user_id) VALUES ('c','r','author','body','now','hidden','owner')"
                )
            backup = backup_database(source, root / "backups")
            self.assertEqual(verify_database(backup), "ok")
            restore_database(backup, restored)
            with sqlite3.connect(restored) as conn:
                self.assertEqual(
                    conn.execute("SELECT hidden_at, hidden_by_user_id FROM comments WHERE id='c'").fetchone(), ("hidden", "owner")
                )

    def test_policy_distinguishes_author_deletion_and_recipe_owner_moderation(self):
        from recipe_box.policies import can_delete_comment, can_edit_comment, can_hide_comment, can_unhide_comment, can_view_comment

        recipe = {"id": "recipe-1", "owner_id": self.owner, "visibility": "shared_epn"}
        active = {"user_id": self.author, "deleted_at": None, "hidden_at": None}
        hidden = {**active, "hidden_at": "now", "hidden_by_user_id": self.owner}
        deleted = {**active, "deleted_at": "now"}
        self.assertTrue(can_edit_comment(active, recipe, self.author))
        self.assertTrue(can_delete_comment(active, recipe, self.author))
        self.assertFalse(can_delete_comment(active, recipe, self.owner))
        self.assertTrue(can_hide_comment(active, recipe, self.owner))
        self.assertFalse(can_hide_comment(active, recipe, self.author))
        self.assertTrue(can_unhide_comment(hidden, recipe, self.owner))
        self.assertFalse(can_unhide_comment(hidden, recipe, self.author))
        self.assertFalse(can_view_comment(hidden, recipe, self.other))
        self.assertFalse(can_view_comment(deleted, recipe, self.author))

    def test_owner_can_hide_and_unhide_but_author_and_other_cannot(self):
        owner_token = self.api_token("owner@example.com")
        author_token = self.api_token("author@example.com")
        other_token = self.api_token("other@example.com")
        endpoint = "/api/v1/comments/comment-1/hide"
        self.assertEqual(self.client.post(endpoint, headers={"Authorization": f"Bearer {owner_token}"}).status_code, 204)
        self.assertEqual(self.client.post(endpoint, headers={"Authorization": f"Bearer {owner_token}"}).status_code, 204)
        for token in (author_token, other_token):
            response = self.client.post(endpoint, headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.get_json()["error"]["code"], "FORBIDDEN")
        listed = self.client.get("/api/v1/recipes/recipe-1/comments", headers={"Authorization": f"Bearer {author_token}"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["data"], [])
        self.assertEqual(
            self.client.post("/api/v1/comments/comment-1/unhide", headers={"Authorization": f"Bearer {owner_token}"}).status_code, 204
        )
        listed = self.client.get("/api/v1/recipes/recipe-1/comments", headers={"Authorization": f"Bearer {author_token}"})
        self.assertEqual([row["id"] for row in listed.get_json()["data"]], ["comment-1"])
        with self.recipe_app.db_connect() as conn:
            row = conn.execute("SELECT deleted_at, hidden_at, hidden_by_user_id FROM comments WHERE id='comment-1'").fetchone()
            self.assertEqual(tuple(row), (None, None, None))

    def test_api_authentication_csrf_and_private_recipe_denials_are_safe(self):
        response = self.client.post("/api/v1/comments/comment-1/hide")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "AUTHENTICATION_REQUIRED")
        owner_token = self.api_token("owner@example.com")
        with self.client.session_transaction() as session:
            session["user_id"] = self.owner
            session["csrf_token"] = "expected"
        response = self.client.post("/comments/comment-1/hide")
        self.assertEqual(response.status_code, 400)
        with self.recipe_app.db_connect() as conn:
            conn.execute("UPDATE recipes SET visibility='private' WHERE id='recipe-1'")
        other_token = self.api_token("other@example.com")
        response = self.client.post("/api/v1/comments/comment-1/hide", headers={"Authorization": f"Bearer {other_token}"})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("comment-1", response.get_data(as_text=True))
        self.assertEqual(
            self.client.post("/api/v1/comments/comment-1/hide", headers={"Authorization": f"Bearer {owner_token}"}).status_code, 204
        )

    def test_browser_owner_controls_and_hidden_comment_is_not_in_normal_view(self):
        self.browser_as(self.author)
        author_page = self.client.get("/recipes/recipe-1")
        self.assertIn("Edit comment", author_page.get_data(as_text=True))
        self.assertIn("Delete comment", author_page.get_data(as_text=True))
        self.assertNotIn("Hide comment", author_page.get_data(as_text=True))
        self.browser_as(self.owner)
        owner_page = self.client.get("/recipes/recipe-1")
        self.assertIn("Hide comment", owner_page.get_data(as_text=True))
        self.assertNotIn("Edit comment", owner_page.get_data(as_text=True))
        response = self.client.post("/comments/comment-1/hide", data={"csrf_token": "csrf-test-token"})
        self.assertEqual(response.status_code, 302)
        self.browser_as(self.author)
        self.assertNotIn("Visible comment", self.client.get("/recipes/recipe-1").get_data(as_text=True))
        self.browser_as(self.owner)
        owner_page = self.client.get("/recipes/recipe-1")
        self.assertIn("Visible comment", owner_page.get_data(as_text=True))
        self.assertIn("Unhide comment", owner_page.get_data(as_text=True))

    def test_hidden_comment_activity_is_filtered_for_browser_and_api(self):
        owner_token = self.api_token("owner@example.com")
        with self.recipe_app.db_connect() as conn:
            conn.execute(
                "INSERT INTO activity_events(id,user_id,event_type,recipe_id,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                ("event-comment", self.author, "comment_added", "recipe-1", '{"comment_id":"comment-1"}', "now"),
            )
        activity = self.client.get("/api/v1/activity", headers={"Authorization": f"Bearer {owner_token}"})
        self.assertIn("event-comment", {item["id"] for item in activity.get_json()["data"]})
        self.assertEqual(
            self.client.post("/api/v1/comments/comment-1/hide", headers={"Authorization": f"Bearer {owner_token}"}).status_code, 204
        )
        activity = self.client.get("/api/v1/activity", headers={"Authorization": f"Bearer {owner_token}"})
        self.assertNotIn("event-comment", {item["id"] for item in activity.get_json()["data"]})
        self.browser_as(self.owner)
        self.assertNotIn("Comment added", self.client.get("/activity").get_data(as_text=True))

    def test_deleted_comment_is_not_hidden_and_author_only_deletes(self):
        author_token = self.api_token("author@example.com")
        owner_token = self.api_token("owner@example.com")
        forbidden = self.client.delete("/api/v1/comments/comment-1", headers={"Authorization": f"Bearer {owner_token}"})
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(
            self.client.delete("/api/v1/comments/comment-1", headers={"Authorization": f"Bearer {author_token}"}).status_code, 204
        )
        with self.recipe_app.db_connect() as conn:
            row = conn.execute("SELECT deleted_at, hidden_at FROM comments WHERE id='comment-1'").fetchone()
            self.assertIsNotNone(row["deleted_at"])
            self.assertIsNone(row["hidden_at"])


if __name__ == "__main__":
    unittest.main()
