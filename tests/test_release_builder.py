from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_release import build_release, is_forbidden


class ReleaseBuilderTests(unittest.TestCase):
    def make_repo(self):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-b", "fix/comment-owner-hide"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (root / "requirements.txt").write_text("Flask==3.1.3\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)
        return root, subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

    def test_dirty_tree_is_rejected(self):
        repo, commit = self.make_repo()
        (repo / "app.py").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "dirty"):
            build_release(repo, commit, repo / "staging", validation_command=":")

    def test_invalid_commit_is_rejected(self):
        repo, _ = self.make_repo()
        with self.assertRaises(ValueError):
            build_release(repo, "0" * 39, repo / "staging", validation_command=":")

    def test_unapproved_branch_is_rejected(self):
        repo, commit = self.make_repo()
        subprocess.run(["git", "checkout", "-b", "other"], cwd=repo, check=True, capture_output=True)
        with self.assertRaisesRegex(RuntimeError, "unapproved branch"):
            build_release(repo, commit, repo / "staging", validation_command=":")

    def test_forbidden_paths_are_excluded_or_rejected(self):
        self.assertTrue(is_forbidden(".env"))
        self.assertTrue(is_forbidden(".env.example"))
        self.assertTrue(is_forbidden("uploads/avatar.png"))
        self.assertTrue(is_forbidden("keys/signing-key.pem"))
        self.assertFalse(is_forbidden("recipe_box/application.py"))

    def test_manifest_and_archive_checksum_are_correct(self):
        repo, commit = self.make_repo()
        result = build_release(repo, commit, repo / "staging", validation_command=":")
        release = Path(result["release_dir"])
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["git_commit"], commit)
        self.assertEqual(manifest["branch"], "fix/comment-owner-hide")
        self.assertTrue((release / "checksums.sha256").read_text(encoding="utf-8").strip())
        archive = Path(result["archive"])
        expected = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(expected, (Path(result["checksum"]).read_text()).split()[0])
        self.assertNotIn(".git", " ".join(p.as_posix() for p in release.rglob("*")))


if __name__ == "__main__":
    unittest.main()
