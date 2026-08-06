import os
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


class DeploymentWrapperTests(unittest.TestCase):
    wrapper = Path(__file__).parents[1] / "deploy" / "epn-recipe-box-deploy"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.data = self.root / "data"
        self.backups = self.root / "backups"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.data.mkdir()
        self.backup = self.backups / "verified-backup"
        (self.backup / "uploads").mkdir(parents=True)
        (self.backup / "recipe-images").mkdir()
        self._database(self.backup / "recipe_box.db", 7, "backup-user", "backup recipe")
        (self.backup / "uploads" / "upload.txt").write_text("backup upload", encoding="utf-8")
        (self.backup / "recipe-images" / "image.jpg").write_bytes(b"backup image")
        self._manifest(self.backup)
        self._database(self.data / "recipe_box.db", 14, "live-user", "live recipe")
        (self.data / "uploads").mkdir()
        (self.data / "recipe-images").mkdir()
        (self.data / "uploads" / "upload.txt").write_text("live upload", encoding="utf-8")
        (self.data / "recipe-images" / "image.jpg").write_bytes(b"live image")
        (self.fake_bin / "systemctl").write_text(
            '#!/bin/sh\nprintf \'systemctl %s\\n\' "$*" >> "$TEST_LOG"\nif [ "$1" = is-active ]; then exit 1; fi\nexit 0\n',
            encoding="utf-8",
        )
        (self.fake_bin / "systemctl").chmod(0o755)
        self.log = self.root / "commands.log"
        (self.fake_bin / "git").write_text('#!/bin/sh\nprintf \'git %s\\n\' "$*" >> "$TEST_LOG"\nexit 0\n', encoding="utf-8")
        (self.fake_bin / "curl").write_text('#!/bin/sh\nprintf \'curl %s\\n\' "$*" >> "$TEST_LOG"\nexit 0\n', encoding="utf-8")
        (self.fake_bin / "git").chmod(0o755)
        (self.fake_bin / "curl").chmod(0o755)

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def _database(path, version, user, recipe):
        with sqlite3.connect(path) as conn:
            conn.executescript(
                "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);"
                "CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT);"
                "CREATE TABLE recipes(id TEXT PRIMARY KEY, title TEXT);"
            )
            conn.execute("INSERT INTO schema_version VALUES (?, 'now')", (version,))
            conn.execute("INSERT INTO users VALUES (?, ?)", (user, user))
            conn.execute("INSERT INTO recipes VALUES (?, ?)", (f"recipe-{version}", recipe))

    def _run(self, *args):
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "EPN_RECIPE_BOX_DATA_DIR": str(self.data),
                "EPN_RECIPE_BOX_BACKUP_ROOT": str(self.backups),
                "EPN_RECIPE_BOX_SERVICE": "test.service",
                "EPN_RECIPE_BOX_TAILSCALE_HEALTH_URL": "https://tailnet.example/health",
                "TEST_LOG": str(self.log),
            }
        )
        return subprocess.run([str(self.wrapper), *args], env=env, text=True, capture_output=True)

    @staticmethod
    def _manifest(backup):
        files = []
        for relative in ("recipe_box.db", "uploads/upload.txt", "recipe-images/image.jpg"):
            path = backup / relative
            files.append({"path": relative, "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        (backup / "manifest.json").write_text(json.dumps(files), encoding="utf-8")

    def test_restore_replaces_database_uploads_and_images_after_verifying_staging(self):
        result = self._run("restore", str(self.backup))
        self.assertEqual(result.returncode, 0, result.stderr)
        with sqlite3.connect(self.data / "recipe_box.db") as conn:
            self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0], 7)
            self.assertEqual(conn.execute("SELECT title FROM recipes").fetchone()[0], "backup recipe")
        self.assertEqual((self.data / "uploads" / "upload.txt").read_text(), "backup upload")
        self.assertEqual((self.data / "recipe-images" / "image.jpg").read_bytes(), b"backup image")

    def test_rollback_restores_then_deploys_starts_and_checks_both_health_endpoints(self):
        commit = "a" * 40
        result = self._run("rollback", commit, str(self.backup))
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertLess(log.index("systemctl stop"), log.index("git -C"))
        self.assertLess(log.index("checkout"), log.index("systemctl start"))
        self.assertIn("curl", log)
        with sqlite3.connect(self.data / "recipe_box.db") as conn:
            self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0], 7)

    def test_restore_refuses_backup_outside_approved_root(self):
        outside = self.root / "outside"
        outside.mkdir()
        result = self._run("restore", str(outside))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the approved backup root", result.stderr)
        with sqlite3.connect(self.data / "recipe_box.db") as conn:
            self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0], 14)

    def test_verify_backup_rejects_tampered_database_and_upload(self):
        for relative, replacement in (("recipe_box.db", b"not sqlite"), ("uploads/upload.txt", b"tampered upload")):
            with self.subTest(relative=relative):
                original = (self.backup / relative).read_bytes()
                (self.backup / relative).write_bytes(replacement)
                result = self._run("verify-backup", str(self.backup))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("checksum", result.stderr)
                (self.backup / relative).write_bytes(original)

    def test_verify_backup_rejects_symlinks_in_asset_trees(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        for directory in ("uploads", "recipe-images"):
            link = self.backup / directory / "link"
            link.symlink_to(outside)
            result = self._run("verify-backup", str(self.backup))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)
            link.unlink()


if __name__ == "__main__":
    unittest.main()
