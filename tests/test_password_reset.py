import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        import app as recipe_app
        from recipe_box import config, mail, security

        self.app = recipe_app
        self.config = config
        self.mail = mail
        self.security = security
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.original = {name: getattr(recipe_app, name) for name in ("DATA_DIR", "UPLOAD_DIR", "DB_FILE", "STATIC_DIR")}
        recipe_app.DATA_DIR = root
        recipe_app.UPLOAD_DIR = root / "uploads"
        recipe_app.RECIPE_IMAGE_DIR = root / "recipe-images"
        recipe_app.DB_FILE = root / "recipe_box.db"
        recipe_app.UPLOAD_DIR.mkdir()
        recipe_app.RECIPE_IMAGE_DIR.mkdir()
        self.original_config_image_dir = config.RECIPE_IMAGE_DIR
        config.RECIPE_IMAGE_DIR = recipe_app.RECIPE_IMAGE_DIR
        self.previous = {name: os.environ.get(name) for name in ("SYNC_TOKEN", "SECRET_KEY", "EPN_ENV", "EPN_MAIL_BACKEND")}
        os.environ.update(SYNC_TOKEN="password-reset-test-token", SECRET_KEY="password-reset-test-secret", EPN_ENV="development", EPN_MAIL_BACKEND="fake")
        mail.clear_outbox()
        security._FAILED_LOGINS.clear()
        security._PASSWORD_RESET_REQUESTS.clear()
        recipe_app.init_db()
        self.user_id = recipe_app.create_account("cook@example.com", "old-password")
        recipe_app.app.config.update(TESTING=True, ENFORCE_CSRF=False)
        self.client = recipe_app.app.test_client()

    def tearDown(self):
        self.mail.clear_outbox()
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in self.original.items():
            setattr(self.app, name, value)
        self.config.RECIPE_IMAGE_DIR = self.original_config_image_dir
        self.tempdir.cleanup()

    def token_from_outbox(self):
        message = self.mail.outbox[-1]
        return message["reset_url"].split("token=", 1)[1]

    def test_existing_account_gets_generic_response_and_reset_email(self):
        response = self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json(), {"message": "If an account exists for that email, password reset instructions have been sent."})
        self.assertEqual(len(self.mail.outbox), 1)
        self.assertIn("cook@example.com", self.mail.outbox[0]["recipient"])

    def test_nonexistent_account_is_indistinguishable_and_sends_no_email(self):
        response = self.client.post("/api/v1/auth/forgot-password", json={"email": "missing@example.com"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json(), {"message": "If an account exists for that email, password reset instructions have been sent."})
        self.assertEqual(len(self.mail.outbox), 0)

    def test_reset_token_is_hashed_and_valid_token_changes_password(self):
        self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
        token = self.token_from_outbox()
        with self.app.db_connect() as conn:
            row = conn.execute("SELECT token_hash, used_at, expires_at FROM password_reset_tokens").fetchone()
        self.assertNotEqual(row["token_hash"], token)
        self.assertIsNone(row["used_at"])
        response = self.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "new-password"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(self.app.authenticate_user("cook@example.com", "new-password"))
        self.assertIsNone(self.app.authenticate_user("cook@example.com", "old-password"))

    def test_token_is_single_use_and_expired_or_malformed_tokens_fail(self):
        self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
        token = self.token_from_outbox()
        self.assertEqual(self.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "new-password"}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "another-password"}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/reset-password", json={"token": "not-a-real-token", "new_password": "new-password"}).status_code, 400)
        self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
        expired = self.token_from_outbox()
        with self.app.db_connect() as conn:
            conn.execute("UPDATE password_reset_tokens SET expires_at = '2000-01-01T00:00:00+00:00' WHERE token_hash = (SELECT token_hash FROM password_reset_tokens ORDER BY created_at DESC LIMIT 1)")
        self.assertEqual(self.client.post("/api/v1/auth/reset-password", json={"token": expired, "new_password": "new-password"}).status_code, 400)

    def test_reset_revokes_existing_api_tokens_and_invalidates_older_reset_tokens(self):
        login = self.client.post("/api/v1/auth/login", json={"email": "cook@example.com", "password": "old-password"})
        api_token = login.get_json()["token"]
        self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
        first = self.token_from_outbox()
        self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
        second = self.token_from_outbox()
        self.assertEqual(self.client.post("/api/v1/auth/reset-password", json={"token": first, "new_password": "new-password"}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/reset-password", json={"token": second, "new_password": "new-password"}).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {api_token}"}).status_code, 401)

    def test_reset_request_is_rate_limited_without_changing_generic_response(self):
        for _ in range(5):
            response = self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
            self.assertEqual(response.status_code, 202)
        self.assertEqual(len(self.mail.outbox), 5)
        response = self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(self.mail.outbox), 5)

    def test_web_forgot_and_reset_flow(self):
        page = self.client.get("/signup")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Forgot password?", page.data)
        response = self.client.post("/forgot-password", data={"email": "cook@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"If an account exists", self.client.get(response.location).data)
        token = self.token_from_outbox()
        reset_page = self.client.get(f"/reset-password?token={token}")
        self.assertEqual(reset_page.status_code, 200)
        response = self.client.post("/reset-password", data={"token": token, "new_password": "web-password", "confirmation": "web-password"})
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(self.app.authenticate_user("cook@example.com", "web-password"))

    def test_schema_15_migration_is_idempotent(self):
        from recipe_box import migrations
        self.assertEqual(migrations.schema_version(self.app.DB_FILE), 16)
        self.assertEqual(migrations.migrate_database(self.app.DB_FILE), 16)
        with self.app.db_connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='password_reset_tokens'").fetchone())

    def test_mobile_code_is_six_digits_and_only_keyed_digest_is_stored(self):
        from recipe_box.db import create_password_reset_challenge
        challenge = create_password_reset_challenge("cook@example.com", 10)
        self.assertRegex(challenge["code"], r"^\d{6}$")
        with self.app.db_connect() as conn:
            row = conn.execute("SELECT code_hash, web_token_hash FROM password_reset_tokens").fetchone()
        self.assertNotEqual(row["code_hash"], challenge["code"])
        self.assertIsNotNone(row["code_hash"])
        self.assertIsNotNone(row["web_token_hash"])

    def test_correct_code_resets_password_and_invalidates_after_use(self):
        from recipe_box.db import create_password_reset_challenge, reset_password_with_code
        challenge = create_password_reset_challenge("cook@example.com", 10)
        self.assertEqual(reset_password_with_code("cook@example.com", challenge["code"], "new-password"), "ok")
        self.assertEqual(reset_password_with_code("cook@example.com", challenge["code"], "another-password"), "invalid_code")

    def test_mobile_api_accepts_email_and_code(self):
        response = self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"})
        self.assertEqual(response.status_code, 202)
        code = self.mail.outbox[-1]["code"]
        response = self.client.post("/api/v1/auth/reset-password", json={"email": "cook@example.com", "code": code, "new_password": "mobile-password"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(self.app.authenticate_user("cook@example.com", "mobile-password"))

    def test_fifth_wrong_code_invalidates_challenge(self):
        from recipe_box.db import create_password_reset_challenge, reset_password_with_code
        challenge = create_password_reset_challenge("cook@example.com", 10)
        wrong_code = "000000" if challenge["code"] != "000000" else "000001"
        for _ in range(5):
            self.assertEqual(reset_password_with_code("cook@example.com", wrong_code, "new-password"), "invalid_code")
        self.assertEqual(reset_password_with_code("cook@example.com", challenge["code"], "new-password"), "invalid_code")

    def test_gmail_api_backend_builds_plaintext_message_without_network(self):
        class FakeSend:
            def execute(self):
                return {"id": "provider-message-id"}
        class FakeMessages:
            def send(self, **kwargs):
                self.kwargs = kwargs
                return FakeSend()
        class FakeUsers:
            def messages(self):
                return messages
        messages = FakeMessages()
        fake_service = type("Service", (), {"users": lambda self: FakeUsers()})()
        with patch.dict(os.environ, {"EPN_MAIL_BACKEND": "gmail_api", "EPN_GMAIL_TOKEN_FILE": "/secure/token", "EPN_GMAIL_CLIENT_SECRET_FILE": "/secure/client", "EPN_MAIL_FROM": "aaron.doug.projects@gmail.com", "EPN_MAIL_ENABLED": "1"}, clear=False), patch.object(self.mail, "_gmail_service", return_value=fake_service):
            result = self.mail.send_email("cook@example.com", "Subject", "Plain body")
        self.assertEqual(result, "provider-message-id")
        self.assertEqual(messages.kwargs["userId"], "me")
        self.assertNotIn("new-password", str(messages.kwargs))

    def test_gmail_api_backend_fails_closed_when_disabled_or_unconfigured(self):
        with patch.dict(os.environ, {"EPN_MAIL_BACKEND": "gmail_api", "EPN_MAIL_ENABLED": "0"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                self.mail.send_email("cook@example.com", "Subject", "Plain body")
        with patch.dict(os.environ, {"EPN_MAIL_BACKEND": "gmail_api", "EPN_MAIL_ENABLED": "1"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "credential"):
                self.mail.send_email("cook@example.com", "Subject", "Plain body")
    def test_reset_url_uses_configured_public_https_origin_and_encodes_token(self):
        from recipe_box.config import public_reset_url
        with patch.dict(os.environ, {"EPN_ENV": "production", "EPN_PUBLIC_BASE_URL": "https://epn-hermes-worker-01.tail510dca.ts.net/"}, clear=False):
            url = public_reset_url("a/b+c?token")
        self.assertEqual(url, "https://epn-hermes-worker-01.tail510dca.ts.net/reset-password?token=a%2Fb%2Bc%3Ftoken")

    def test_production_rejects_missing_localhost_loopback_and_malformed_public_url(self):
        from recipe_box.config import public_reset_url
        for value in ("", "http://localhost", "http://127.0.0.1:5000", "not-a-url"):
            with patch.dict(os.environ, {"EPN_ENV": "production", "EPN_PUBLIC_BASE_URL": value}, clear=False):
                with self.assertRaises(ValueError):
                    public_reset_url("token")

    def test_test_environment_allows_localhost_without_host_header_influence(self):
        from recipe_box.config import public_reset_url
        with patch.dict(os.environ, {"EPN_ENV": "development", "EPN_PUBLIC_BASE_URL": "http://localhost"}, clear=False):
            self.assertTrue(public_reset_url("token").startswith("http://localhost/reset-password?token="))

    def test_reset_email_host_header_cannot_override_configured_public_host(self):
        with patch.dict(os.environ, {"EPN_PUBLIC_BASE_URL": "https://epn-hermes-worker-01.tail510dca.ts.net"}, clear=False):
            response = self.client.post("/api/v1/auth/forgot-password", json={"email": "cook@example.com"}, headers={"Host": "attacker.example"})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(self.mail.outbox[-1]["reset_url"].startswith("https://epn-hermes-worker-01.tail510dca.ts.net/reset-password?token="))


if __name__ == "__main__":
    unittest.main()
