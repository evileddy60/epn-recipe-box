import getpass
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from werkzeug.security import check_password_hash


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        import app as recipe_app

        self.recipe_app = recipe_app
        self.tempdir = TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.original = {name: getattr(recipe_app, name) for name in ("DATA_DIR", "UPLOAD_DIR", "DB_FILE", "STATIC_DIR")}
        recipe_app.DATA_DIR = root
        recipe_app.UPLOAD_DIR = root / "uploads"
        recipe_app.DB_FILE = root / "recipe_box.db"
        recipe_app.UPLOAD_DIR.mkdir()
        recipe_app.init_db()
        with recipe_app.db_connect() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                ("reset-user", "User@Example.com", "", "now"),
            )

    def tearDown(self):
        for name, value in self.original.items():
            setattr(self.recipe_app, name, value)
        self.tempdir.cleanup()

    def read_hash(self):
        with self.recipe_app.db_connect() as conn:
            return conn.execute("SELECT password_hash FROM users WHERE id = 'reset-user'").fetchone()[0]

    def test_reset_uses_application_hasher_and_updates_case_insensitively(self):
        self.assertTrue(self.recipe_app.reset_account_password(" user@example.COM ", "new-secret"))
        password_hash = self.read_hash()
        self.assertTrue(check_password_hash(password_hash, "new-secret"))
        self.assertFalse(check_password_hash(password_hash, "old-secret"))

    def test_reset_does_not_update_unknown_user(self):
        self.assertFalse(self.recipe_app.reset_account_password("missing@example.com", "new-secret"))
        self.assertEqual(self.read_hash(), "")

    def test_reset_rejects_short_password(self):
        with self.assertRaises(ValueError):
            self.recipe_app.reset_account_password("user@example.com", "short")

    def test_cli_prompts_twice_and_does_not_print_hash(self):
        import manage

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(getpass, "getpass", side_effect=["new-secret", "new-secret"]), redirect_stdout(stdout), redirect_stderr(stderr):
            result = manage.main(["reset-password", "USER@EXAMPLE.COM"])

        self.assertEqual(result, 0)
        self.assertIn("Password reset for user@example.com.", stdout.getvalue())
        self.assertNotIn(self.read_hash(), stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_rejects_mismatched_passwords_without_writing(self):
        import manage

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(getpass, "getpass", side_effect=["new-secret", "different"]), redirect_stdout(stdout), redirect_stderr(stderr):
            result = manage.main(["reset-password", "user@example.com"])

        self.assertEqual(result, 2)
        self.assertIn("Passwords do not match.", stderr.getvalue())
        self.assertEqual(self.read_hash(), "")


if __name__ == "__main__":
    unittest.main()
