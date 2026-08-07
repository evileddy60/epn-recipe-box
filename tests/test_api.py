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
        from recipe_box import config as recipe_config

        self.recipe_config = recipe_config
        self.original_config_image_dir = recipe_config.RECIPE_IMAGE_DIR
        root = Path(self.tempdir.name)
        recipe_app.DATA_DIR = root
        recipe_app.UPLOAD_DIR = root / "uploads"
        recipe_app.RECIPE_IMAGE_DIR = root / "recipe-images"
        recipe_app.DB_FILE = root / "recipe_box.db"
        recipe_config.RECIPE_IMAGE_DIR = recipe_app.RECIPE_IMAGE_DIR
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
        from recipe_box import security

        security._FAILED_LOGINS.clear()
        self.client = recipe_app.app.test_client()

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in self.original.items():
            setattr(self.recipe_app, name, value)
        self.recipe_config.RECIPE_IMAGE_DIR = self.original_config_image_dir
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

    def test_signup_never_logs_password_or_hash(self):
        from recipe_box import security

        security._FAILED_LOGINS.clear()
        with self.assertLogs("recipe_box.security", level="WARNING") as captured:
            response = self.client.post(
                "/api/v1/auth/signup",
                json={"email": "log-check@example.com", "password": "short", "nickname": "Cook"},
            )
        self.assertEqual(response.status_code, 422)
        logs = "\n".join(captured.output)
        self.assertNotIn("short", logs)
        self.assertNotIn("password_hash", logs)

    def test_native_signup_issues_token_and_persists_profile_without_exposing_hash(self):
        response = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "new-cook@example.com", "password": "correct-horse", "nickname": "New Cook"},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload["token"])
        self.assertEqual(payload["user"]["email"], "new-cook@example.com")
        self.assertEqual(payload["user"]["nickname"], "New Cook")
        serialized = json.dumps(payload)
        self.assertNotIn("password", serialized.lower())
        self.assertNotIn("password_hash", serialized.lower())
        me = self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {payload['token']}"})
        self.assertEqual(me.status_code, 200)
        with self.recipe_app.db_connect() as conn:
            row = conn.execute("SELECT password_hash, nickname FROM users WHERE email = ?", ("new-cook@example.com",)).fetchone()
        self.assertNotEqual(row["password_hash"], "correct-horse")
        self.assertEqual(row["nickname"], "New Cook")

    def test_native_signup_validation_duplicate_and_structured_errors(self):
        cases = [
            ({}, "Email, password, and nickname are required."),
            ({"email": "bad", "password": "correct-horse", "nickname": "Cook"}, "Enter a valid email address."),
            ({"email": "new@example.com", "password": "short", "nickname": "Cook"}, "Password must be between 8 and 128 characters."),
            ({"email": "new@example.com", "password": "x" * 129, "nickname": "Cook"}, "Password must be between 8 and 128 characters."),
            ({"email": "new@example.com", "password": "correct-horse", "nickname": ""}, "Nickname must be between 1 and 80 characters."),
            (
                {"email": "new@example.com", "password": "correct-horse", "nickname": "x" * 81},
                "Nickname must be between 1 and 80 characters.",
            ),
        ]
        for body, message in cases:
            response = self.client.post("/api/v1/auth/signup", json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.get_json()["error"]["code"], "VALIDATION_ERROR")
            self.assertEqual(response.get_json()["error"]["message"], message)
        duplicate = self.client.post(
            "/api/v1/auth/signup",
            json={"email": " API@example.com ", "password": "correct-horse", "nickname": "Another"},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.get_json()["error"]["code"], "ACCOUNT_EXISTS")

    def test_native_signup_reuses_rate_limit_and_browser_signup_contract(self):
        from recipe_box import security

        security._FAILED_LOGINS.clear()
        for _ in range(5):
            response = self.client.post(
                "/api/v1/auth/signup",
                json={"email": "limited@example.com", "password": "short", "nickname": "Cook"},
            )
            self.assertEqual(response.status_code, 422)
        limited = self.client.post(
            "/api/v1/auth/signup",
            json={"email": "limited@example.com", "password": "correct-horse", "nickname": "Cook"},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()["error"]["code"], "RATE_LIMITED")
        browser = self.client.get("/signup")
        self.assertEqual(browser.status_code, 200)
        self.assertIn("Create account", browser.get_data(as_text=True))

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

    def test_recipe_owner_can_update_all_editable_fields_without_changing_owner_or_local_state(self):
        token = self.login()
        created = self.create_recipe(token, title="Original")
        recipe_id = created["id"]
        with self.recipe_app.db_connect() as conn:
            conn.execute(
                "INSERT INTO favorites(user_id, recipe_id, created_at) VALUES(?, ?, ?)",
                (self.user_id, recipe_id, "2026-01-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO recipe_user_state(user_id, recipe_id, is_favorite, is_archived, archived_at, created_at, updated_at) VALUES(?, ?, 0, 1, ?, ?, ?)",
                (self.user_id, recipe_id, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
        response = self.client.patch(
            f"/api/v1/recipes/{recipe_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Updated title",
                "summary": "Updated summary",
                "prep_time": "25 minutes",
                "servings": "4",
                "ingredients": ["beans", "rice"],
                "steps": ["Soak beans.", "Cook dinner."],
                "category": "lunch",
                "tags": ["updated", "family"],
                "visibility": "private",
            },
        )
        self.assertEqual(response.status_code, 200)
        updated = response.get_json()
        self.assertEqual(updated["title"], "Updated title")
        self.assertEqual(updated["ingredients"], ["beans", "rice"])
        self.assertEqual(updated["steps"], ["Soak beans.", "Cook dinner."])
        self.assertEqual(updated["category"], "lunch")
        self.assertEqual([tag["normalized_name"] for tag in updated["tags"]], ["family", "updated"])
        self.assertEqual(updated["visibility"], "private")
        self.assertEqual(updated["creator"]["id"], self.user_id)
        with self.recipe_app.db_connect() as conn:
            row = conn.execute("SELECT owner_id FROM recipes WHERE id=?", (recipe_id,)).fetchone()
            state = conn.execute(
                "SELECT is_archived FROM recipe_user_state WHERE user_id=? AND recipe_id=?", (self.user_id, recipe_id)
            ).fetchone()
            favorite = conn.execute("SELECT 1 FROM favorites WHERE user_id=? AND recipe_id=?", (self.user_id, recipe_id)).fetchone()
        self.assertEqual(row["owner_id"], self.user_id)
        self.assertEqual(state["is_archived"], 1)
        self.assertIsNotNone(favorite)

    def test_recipe_update_requires_owner_and_rejects_malformed_fields(self):
        owner_token = self.login()
        created = self.create_recipe(owner_token)
        other_id = self.recipe_app.create_account("other-edit@example.com", "correct-horse")
        self.recipe_app.update_profile(other_id, "Other", "", "")
        other_token = self.client.post(
            "/api/v1/auth/login", json={"email": "other-edit@example.com", "password": "correct-horse"}
        ).get_json()["token"]
        for token, status in ((None, 401), (other_token, 403)):
            headers = {} if token is None else {"Authorization": f"Bearer {token}"}
            response = self.client.patch(f"/api/v1/recipes/{created['id']}", headers=headers, json={"title": "Nope"})
            self.assertEqual(response.status_code, status)
        for body in (
            {"ingredients": "not-an-array"},
            {"tags": ["x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13"]},
            {"category": "bad category!"},
            {"visibility": "public"},
            {"owner_id": "attacker"},
        ):
            response = self.client.patch(f"/api/v1/recipes/{created['id']}", headers={"Authorization": f"Bearer {owner_token}"}, json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.get_json()["error"]["code"], "VALIDATION_ERROR")

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
        archived = self.client.post(f"/api/v1/recipes/{recipe_id}/archive", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(archived.status_code, 204)
        # Application initialization must not replay the legacy global archive column over user-local state.
        self.recipe_app.init_db()
        self.assertEqual(self.client.get(f"/api/v1/recipes/{recipe_id}", headers={"Authorization": f"Bearer {token}"}).status_code, 404)
        other_id = self.recipe_app.create_account("archive-other@example.com", "correct-horse")
        self.recipe_app.update_profile(other_id, "Archive Other", "", "")
        other_token = self.client.post(
            "/api/v1/auth/login", json={"email": "archive-other@example.com", "password": "correct-horse"}
        ).get_json()["token"]
        self.assertEqual(
            self.client.get(f"/api/v1/recipes/{recipe_id}", headers={"Authorization": f"Bearer {other_token}"}).status_code, 200
        )
        restored = self.client.delete(f"/api/v1/recipes/{recipe_id}/archive", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(restored.status_code, 204)
        self.assertEqual(self.client.get(f"/api/v1/recipes/{recipe_id}", headers={"Authorization": f"Bearer {token}"}).status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/v1/recipes/{recipe_id}", headers={"Authorization": f"Bearer {other_token}"}).status_code, 200
        )
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
        self.assertGreaterEqual(len(feed.get_json()["data"]), 3)
        self.assertNotIn("comment_added", {item["event_type"] for item in feed.get_json()["data"]})

    def test_profile_patch_is_authenticated_and_limited_to_editable_fields(self):
        token = self.login()
        response = self.client.patch(
            "/api/v1/me", headers={"Authorization": f"Bearer {token}"}, json={"nickname": "  New   Name ", "bio": "About me"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["nickname"], "New Name")
        self.assertEqual(response.get_json()["bio"], "About me")
        self.assertEqual(self.client.patch("/api/v1/me", json={"nickname": "Nope"}).status_code, 401)
        rejected = self.client.patch("/api/v1/me", headers={"Authorization": f"Bearer {token}"}, json={"email": "attacker@example.com"})
        self.assertEqual(rejected.status_code, 422)

    def test_recipe_image_upload_replacement_validation_authorization_and_private_delivery(self):
        from io import BytesIO
        from PIL import Image

        token = self.login()
        created = self.create_recipe(token, title="Image Card")
        recipe_id = created["id"]

        def jpeg(color):
            stream = BytesIO()
            Image.new("RGB", (32, 24), color).save(stream, format="JPEG")
            stream.seek(0)
            return stream

        uploaded = self.client.put(
            f"/api/v1/recipes/{recipe_id}/image",
            headers={"Authorization": f"Bearer {token}"},
            data={"image": (jpeg("red"), "first.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 200)
        first = uploaded.get_json()["image"]
        self.assertEqual(first["media_type"], "image/jpeg")
        with self.recipe_app.db_connect() as conn:
            first_filename = conn.execute("SELECT image_filename FROM recipes WHERE id=?", (recipe_id,)).fetchone()["image_filename"]
        old_path = self.recipe_app.RECIPE_IMAGE_DIR / first_filename
        self.assertTrue(old_path.is_file())

        replaced = self.client.put(
            f"/api/v1/recipes/{recipe_id}/image",
            headers={"Authorization": f"Bearer {token}"},
            data={"image": (jpeg("blue"), "second.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(replaced.status_code, 200)
        second = replaced.get_json()["image"]
        with self.recipe_app.db_connect() as conn:
            second_filename = conn.execute("SELECT image_filename FROM recipes WHERE id=?", (recipe_id,)).fetchone()["image_filename"]
        self.assertNotEqual(first_filename, second_filename)
        self.assertFalse(old_path.exists())
        new_path = self.recipe_app.RECIPE_IMAGE_DIR / second_filename
        self.assertTrue(new_path.is_file())

        failed = self.client.put(
            f"/api/v1/recipes/{recipe_id}/image",
            headers={"Authorization": f"Bearer {token}"},
            data={"image": (BytesIO(b"not an image"), "bad.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(failed.status_code, 422)
        with self.recipe_app.db_connect() as conn:
            self.assertEqual(
                conn.execute("SELECT image_filename FROM recipes WHERE id=?", (recipe_id,)).fetchone()["image_filename"], second_filename
            )

        other_id = self.recipe_app.create_account("image-other@example.com", "correct-horse")
        self.recipe_app.update_profile(other_id, "Other", "", "")
        other_token = self.client.post(
            "/api/v1/auth/login", json={"email": "image-other@example.com", "password": "correct-horse"}
        ).get_json()["token"]
        self.assertEqual(
            self.client.put(
                f"/api/v1/recipes/{recipe_id}/image",
                headers={"Authorization": f"Bearer {other_token}"},
                data={"image": (jpeg("green"), "other.jpg")},
                content_type="multipart/form-data",
            ).status_code,
            403,
        )
        self.client.patch(f"/api/v1/recipes/{recipe_id}", headers={"Authorization": f"Bearer {token}"}, json={"visibility": "private"})
        self.assertEqual(
            self.client.get(f"/api/v1/recipes/{recipe_id}/image", headers={"Authorization": f"Bearer {other_token}"}).status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/api/v1/recipes/{recipe_id}/image", headers={"Authorization": f"Bearer {token}"}).status_code, 200
        )


if __name__ == "__main__":
    unittest.main()
