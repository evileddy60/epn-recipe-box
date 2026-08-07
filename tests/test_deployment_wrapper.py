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
        self.repo = self.root / "repo"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.data.mkdir()
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Wrapper Test"], check=True)
        (self.repo / "tracked.txt").write_text("tracked", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "initial"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "branch", "fix/comment-owner-hide"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", str(self.repo)], check=True)
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
            '#!/bin/sh\nprintf \'systemctl %s\\n\' "$*" >> "$TEST_LOG"\nif [ "$1" = is-active ]; then [ "${TEST_SERVICE_ACTIVE:-0}" = 1 ]; exit $?; fi\nexit 0\n',
            encoding="utf-8",
        )
        (self.fake_bin / "systemctl").chmod(0o755)
        self.log = self.root / "commands.log"
        (self.fake_bin / "git").write_text(
            '#!/bin/sh\nprintf \'git %s\\n\' "$*" >> "$TEST_LOG"\nexec /usr/bin/git "$@"\n', encoding="utf-8"
        )
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
                "EPN_RECIPE_BOX_REPO": str(self.repo),
                "EPN_RECIPE_BOX_BACKUP_ROOT": str(self.backups),
                "EPN_RECIPE_BOX_SERVICE": "test.service",
                "EPN_RECIPE_BOX_SERVICE_USER": str(os.getuid()),
                "EPN_RECIPE_BOX_SERVICE_GROUP": str(os.getgid()),
                "EPN_RECIPE_BOX_TAILSCALE_HEALTH_URL": "https://tailnet.example/health",
                "TEST_LOG": str(self.log),
            }
        )
        return subprocess.run([str(self.wrapper), *args], env=env, text=True, capture_output=True)

    def _repo_commit(self, message, content):
        (self.repo / "tracked.txt").write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", message], check=True)
        return subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()

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
        commit = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
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

    def test_backup_succeeds_with_active_service_without_stop_and_verifies(self):
        env = os.environ.copy()
        env["TEST_SERVICE_ACTIVE"] = "1"
        env.update(
            {
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "EPN_RECIPE_BOX_DATA_DIR": str(self.data),
                "EPN_RECIPE_BOX_BACKUP_ROOT": str(self.backups),
                "EPN_RECIPE_BOX_SERVICE": "test.service",
                "TEST_LOG": str(self.log),
            }
        )
        result = subprocess.run([str(self.wrapper), "backup"], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        backup_path = Path(result.stdout.strip())
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        self.assertTrue(backup_path.is_dir())
        self.assertEqual(self._run("verify-backup", str(backup_path)).returncode, 0)
        self.assertNotIn("systemctl stop", self.log.read_text() if self.log.exists() else "")

    def test_restore_refuses_while_service_is_active(self):
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "EPN_RECIPE_BOX_DATA_DIR": str(self.data),
                "EPN_RECIPE_BOX_BACKUP_ROOT": str(self.backups),
                "EPN_RECIPE_BOX_SERVICE": "test.service",
                "TEST_LOG": str(self.log),
                "TEST_SERVICE_ACTIVE": "1",
            }
        )
        result = subprocess.run([str(self.wrapper), "restore", str(self.backup)], env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("service must be stopped before restore", result.stderr)
        self.assertNotIn("recipe_box.db", result.stdout)

    def test_info_reports_non_secret_operational_fields(self):
        result = self._run("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        for field in (
            "wrapper_version",
            "repository_path",
            "data_path",
            "backup_root",
            "service",
            "approved_ref",
            "current_commit",
            "schema_version",
            "service_state",
            "health_url",
        ):
            self.assertIn(f"{field}=", result.stdout)
        self.assertNotIn("SECRET_KEY", result.stdout)

    def test_self_check_validates_isolated_configuration(self):
        result = self._run("self-check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("self-check=ok", result.stdout)

    def test_fetch_approved_refreshes_remote_ref_without_service_control(self):
        old = (
            subprocess.check_output(
                ["git", "-C", str(self.repo), "rev-parse", "refs/remotes/origin/fix/comment-owner-hide"], text=True
            ).strip()
            if subprocess.run(
                ["git", "-C", str(self.repo), "rev-parse", "refs/remotes/origin/fix/comment-owner-hide"], capture_output=True
            ).returncode
            == 0
            else None
        )
        newest = self._repo_commit("approved update", "new approved content")
        subprocess.run(["git", "-C", str(self.repo), "branch", "-f", "fix/comment-owner-hide", newest], check=True)
        result = self._run("fetch-approved")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"remote_approved_commit={newest}", result.stdout)
        self.assertNotIn("systemctl", self.log.read_text() if self.log.exists() else "")
        self.assertNotEqual(old, newest)

    def test_deploy_accepts_approved_ancestor_and_rejects_invalid_or_outside_sha(self):
        approved = self._repo_commit("approved update", "approved")
        subprocess.run(["git", "-C", str(self.repo), "branch", "-f", "fix/comment-owner-hide", approved], check=True)
        accepted = self._run("deploy", approved)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip(), approved)
        invalid = self._run("deploy", "0" * 40)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("does not exist locally", invalid.stderr)
        outside = self._repo_commit("unapproved update", "outside")
        rejected = self._run("deploy", outside)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("outside the approved remote branch", rejected.stderr)

    def test_deploy_rejects_modified_tracked_files_but_allows_untracked_documentation(self):
        (self.repo / "operator-notes.txt").write_text("keep", encoding="utf-8")
        (self.repo / "tracked.txt").write_text("modified", encoding="utf-8")
        commit = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        result = self._run("fetch-approved")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("worktree is dirty", result.stderr)
        (self.repo / "tracked.txt").write_text("tracked", encoding="utf-8")
        result = self._run("fetch-approved")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "operator-notes.txt").exists())
        self.assertEqual(commit, subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip())

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
