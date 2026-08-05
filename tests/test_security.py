import io
import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


class SecurityContractTests(unittest.TestCase):
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
        self.previous = {name: os.environ.get(name) for name in ("SECRET_KEY", "EPN_ENV", "EPN_HTTPS", "SYNC_TOKEN")}
        os.environ.update(
            {
                "SECRET_KEY": "security-test-secret-that-is-long-enough-123",
                "EPN_ENV": "development",
                "EPN_HTTPS": "0",
                "SYNC_TOKEN": "security-sync-token",
            }
        )
        recipe_app.init_db()
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

    def test_production_secret_is_required_and_weak_secret_rejected(self):
        from recipe_box.application import create_app

        with patch.dict(os.environ, {"EPN_ENV": "production", "SECRET_KEY": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
                create_app()
        with patch.dict(os.environ, {"EPN_ENV": "production", "SECRET_KEY": "too-short"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "32"):
                create_app()

    def test_development_fallback_is_explicit_and_nonempty(self):
        from recipe_box.application import create_app

        with patch.dict(os.environ, {"EPN_ENV": "development", "SECRET_KEY": ""}, clear=False):
            application = create_app()
        self.assertTrue(application.secret_key)
        self.assertNotIn("dev-only-change-me", str(application.secret_key))

    def test_session_settings_and_https_mode(self):
        from recipe_box.application import create_app

        with patch.dict(os.environ, {"EPN_ENV": "production", "SECRET_KEY": "x" * 40, "EPN_HTTPS": "1"}, clear=False):
            application = create_app()
        self.assertTrue(application.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(application.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(application.config["SESSION_COOKIE_SECURE"])

    def test_csrf_rejects_missing_and_accepts_session_token(self):
        response = self.client.get("/signup")
        self.assertIn(b"csrf_token", response.data)
        response = self.client.post("/signup", data={"mode": "signup", "email": "csrf@example.com", "password": "correct-horse"})
        self.assertEqual(response.status_code, 400)
        with self.client.session_transaction() as session:
            token = session["csrf_token"]
        response = self.client.post(
            "/signup", data={"mode": "signup", "email": "csrf@example.com", "password": "correct-horse", "csrf_token": token}
        )
        self.assertEqual(response.status_code, 302)

    def test_bearer_sync_endpoint_is_not_csrf_protected(self):
        response = self.client.get("/api/sync/manifest", headers={"Authorization": "Bearer security-sync-token"})
        self.assertIn(response.status_code, (200, 401))
        self.assertNotEqual(response.status_code, 400)

    def test_security_headers_and_health_contract(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(set(body), {"status", "database", "schema_version"})
        self.assertEqual(body["status"], "ok")
        response = self.client.get("/signup")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("Strict-Transport-Security", response.headers)

    def test_foreign_keys_are_enabled(self):
        with self.recipe_app.db_connect() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO inventory_items (user_id, item, position) VALUES ('missing', 'rice', 0)")

    def test_avatar_content_validation_rejects_invalid_and_accepts_image(self):
        self.client.get("/signup")
        with self.client.session_transaction() as session:
            token = session["csrf_token"]
        self.client.post(
            "/signup", data={"mode": "signup", "email": "avatar@example.com", "password": "correct-horse", "csrf_token": token}
        )
        with self.client.session_transaction() as session:
            token = session["csrf_token"]
        response = self.client.post(
            "/profile/setup",
            data={"nickname": "Avatar User", "bio": "", "csrf_token": token, "avatar": (io.BytesIO(b"not an image"), "avatar.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        image = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(image, format="PNG")
        image.seek(0)
        response = self.client.post(
            "/profile/setup",
            data={"nickname": "Avatar User", "bio": "", "csrf_token": token, "avatar": (image, "avatar.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
