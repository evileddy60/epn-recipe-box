import json
import os
import tempfile
import unittest
from pathlib import Path


class ApiFoundationTests(unittest.TestCase):
    def setUp(self):
        import app as recipe_app

        self.recipe_app = recipe_app
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.original = {name: getattr(recipe_app, name) for name in ("DATA_DIR", "UPLOAD_DIR", "DB_FILE", "STATIC_DIR")}
        recipe_app.DATA_DIR = root
        recipe_app.UPLOAD_DIR = root / "uploads"
        recipe_app.RECIPE_IMAGE_DIR = root / "recipe-images"
        recipe_app.DB_FILE = root / "recipe_box.db"
        recipe_app.UPLOAD_DIR.mkdir()
        recipe_app.RECIPE_IMAGE_DIR.mkdir()
        self.previous = {name: os.environ.get(name) for name in ("SYNC_TOKEN", "SECRET_KEY", "EPN_ENV")}
        os.environ["SYNC_TOKEN"] = "api-test-peer-token"
        os.environ["SECRET_KEY"] = "api-test-secret"
        os.environ["EPN_ENV"] = "development"
        recipe_app.init_db()
        self.user_id = recipe_app.create_account("api@example.com", "correct-horse")
        recipe_app.update_profile(self.user_id, "API Cook", "Private test account", "")
        recipe_app.app.config.update(TESTING=True, ENFORCE_CSRF=True)
        self.client = recipe_app.app.test_client()

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in self.original.items():
            setattr(self.recipe_app, name, value)
        self.tempdir.cleanup()

    def login(self):
        response = self.client.post("/api/v1/auth/login", json={"email": "api@example.com", "password": "correct-horse"})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["token"])
        return payload["token"]

    def create_recipe(self, token, title="API Recipe", category="dinner", tags=None):
        response = self.client.post(
            "/api/v1/recipes",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": title,
                "summary": "A recipe created through the API.",
                "ingredients": ["rice", "egg"],
                "steps": ["Cook rice.", "Add egg."],
                "category": category,
                "tags": tags if tags is not None else ["quick", "family"],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def test_health_is_public_and_does_not_expose_secrets(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["api_version"], "v1")
        self.assertNotIn("SECRET_KEY", json.dumps(payload))
        self.assertNotIn("api-test-peer-token", json.dumps(payload))

    def test_login_hashes_token_and_me_logout_revokes_it(self):
        response = self.client.post("/api/v1/auth/login", json={"email": "api@example.com", "password": "correct-horse"})
        self.assertEqual(response.status_code, 200)
        token = response.get_json()["token"]
        with self.recipe_app.db_connect() as conn:
            row = conn.execute("SELECT token_hash, expires_at, revoked_at FROM api_tokens").fetchone()
        self.assertNotEqual(row["token_hash"], token)
        self.assertIsNotNone(row["expires_at"])
        self.assertIsNone(row["revoked_at"])
        me = self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["email"], "api@example.com")
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)

    def test_login_failures_are_generic_and_protected_routes_require_bearer_token(self):
        response = self.client.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "AUTHENTICATION_FAILED")
        self.assertNotIn("missing@example.com", json.dumps(response.get_json()))
        response = self.client.get("/api/v1/recipes")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "AUTHENTICATION_REQUIRED")

    def test_expired_token_is_rejected(self):
        token = self.login()
        with self.recipe_app.db_connect() as conn:
            conn.execute("UPDATE api_tokens SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
        response = self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)

    def test_recipe_create_list_search_filters_pagination_and_detail(self):
        token = self.login()
        created = self.create_recipe(token)
        self.create_recipe(token, "Other Recipe", category="lunch", tags=["slow"])
        response = self.client.get(
            "/api/v1/recipes?page=1&page_size=1&q=API&category=dinner&tag=quick",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["pagination"]["total_items"], 1)
        self.assertEqual(payload["data"][0]["id"], created["id"])
        self.assertNotIn("/home/", json.dumps(payload))
        detail = self.client.get(f"/api/v1/recipes/{created['id']}", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["title"], "API Recipe")

    def test_taxonomy_and_validation_errors(self):
        token = self.login()
        categories = self.client.get("/api/v1/categories", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(categories.status_code, 200)
        self.assertIn({"key": "dinner", "name": "Dinner"}, categories.get_json()["data"])
        tags = self.client.get("/api/v1/tags", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(tags.status_code, 200)
        invalid = self.client.post("/api/v1/recipes", headers={"Authorization": f"Bearer {token}"}, json={"title": ""})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["error"]["code"], "VALIDATION_ERROR")

    def test_recipe_visibility_is_shared_by_default_and_private_is_owner_only(self):
        token = self.login()
        created = self.create_recipe(token, title="Community Card")
        self.assertEqual(created["visibility"], "shared_epn")
        other_id = self.recipe_app.create_account("other@example.com", "correct-horse")
        self.recipe_app.update_profile(other_id, "Other Cook", "", "")
        other_login = self.client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "correct-horse"})
        other_token = other_login.get_json()["token"]
        hidden = self.client.get("/api/v1/recipes", headers={"Authorization": f"Bearer {other_token}"})
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.get_json()["pagination"]["total_items"], 1)
        changed = self.client.patch(
            f"/api/v1/recipes/{created['id']}",
            headers={"Authorization": f"Bearer {token}"},
            json={"visibility": "shared_epn"},
        )
        self.assertEqual(changed.status_code, 200)
        private = self.client.patch(
            f"/api/v1/recipes/{created['id']}", headers={"Authorization": f"Bearer {token}"}, json={"visibility": "private"}
        )
        self.assertEqual(private.status_code, 200)
        hidden = self.client.get("/api/v1/recipes", headers={"Authorization": f"Bearer {other_token}"})
        self.assertEqual(hidden.get_json()["pagination"]["total_items"], 0)
        for forbidden in ("public", "selected_users"):
            rejected = self.client.patch(
                f"/api/v1/recipes/{created['id']}",
                headers={"Authorization": f"Bearer {token}"},
                json={"visibility": forbidden},
            )
            self.assertEqual(rejected.status_code, 422)

    def test_archive_is_per_user_and_collections_comments_activity_have_lifecycle(self):
        token = self.login()
        created = self.create_recipe(token, title="Stateful Card")
        recipe_id = created["id"]
        self.assertEqual(
            self.client.post(f"/api/v1/recipes/{recipe_id}/archive", headers={"Authorization": f"Bearer {token}"}).status_code, 204
        )
        self.assertEqual(self.client.get("/api/v1/recipes", headers={"Authorization": f"Bearer {token}"}).status_code, 200)
        collection = self.client.post(
            "/api/v1/collections", headers={"Authorization": f"Bearer {token}"}, json={"name": "Weeknight", "visibility": "shared_epn"}
        )
        self.assertEqual(collection.status_code, 201)
        collection_id = collection.get_json()["id"]
        added = self.client.post(
            f"/api/v1/collections/{collection_id}/recipes", headers={"Authorization": f"Bearer {token}"}, json={"recipe_id": recipe_id}
        )
        self.assertEqual(added.status_code, 204)
        comment = self.client.post(
            f"/api/v1/recipes/{recipe_id}/comments", headers={"Authorization": f"Bearer {token}"}, json={"body": "Nice"}
        )
        self.assertEqual(comment.status_code, 201)
        comment_id = comment.get_json()["id"]
        edited = self.client.patch(f"/api/v1/comments/{comment_id}", headers={"Authorization": f"Bearer {token}"}, json={"body": "Updated"})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(
            self.client.delete(f"/api/v1/comments/{comment_id}", headers={"Authorization": f"Bearer {token}"}).status_code, 204
        )
        feed = self.client.get("/api/v1/activity", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(feed.status_code, 200)
        self.assertGreaterEqual(len(feed.get_json()["data"]), 4)


if __name__ == "__main__":
    unittest.main()
