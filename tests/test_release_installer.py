from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


class ReleaseInstallerTests(unittest.TestCase):
    installer = Path(__file__).parents[1] / "deploy" / "epn-recipe-box-release-deploy"

    def run_installer(self, operation: str, *args: str, root: Path, incoming: Path):
        env = os.environ.copy()
        env.update(
            {
                "EPN_RECIPE_BOX_ROOT": str(root / "opt"),
                "EPN_RECIPE_BOX_DATA_DIR": str(root / "data"),
                "EPN_RECIPE_BOX_BACKUP_ROOT": str(root / "backups"),
                "EPN_RECIPE_BOX_INCOMING": str(incoming),
            }
        )
        return subprocess.run([str(self.installer), operation, *args], env=env, text=True, capture_output=True)

    def make_bundle(self, root: Path, incoming: Path) -> tuple[Path, Path, str]:
        release = root / "source" / "20260806-120000-abcdef1"
        release.mkdir(parents=True)
        (release / "app.py").write_text("app = object()\n", encoding="utf-8")
        (release / "requirements.txt").write_text("Flask==3.1.3\n", encoding="utf-8")
        import hashlib

        files = []
        for path in sorted(release.iterdir()):
            files.append({"path": path.name, "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        manifest = {
            "format": "epn-recipe-box-release/v1",
            "release_id": release.name,
            "git_commit": "a" * 40,
            "branch": "fix/comment-owner-hide",
            "source_repository_url": "https://github.com/evileddy60/epn-recipe-box.git",
            "expected_schema_version": 14,
            "files": files,
        }
        (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (release / "checksums.sha256").write_text("\n".join(f"{x['sha256']}  {x['path']}" for x in files) + "\n", encoding="utf-8")
        archive = incoming / f"{release.name}.tar.gz"
        incoming.mkdir(parents=True)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(release, arcname=release.name)
        import hashlib

        checksum = incoming / f"{archive.name}.sha256"
        checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
        return archive, checksum, release.name

    def test_stage_verifies_and_never_replaces_current(self):
        with tempfile.TemporaryDirectory() as name:
            root, incoming = Path(name), Path(name) / "incoming"
            archive, checksum, release_id = self.make_bundle(root, incoming)
            result = self.run_installer("stage-release", str(archive), str(checksum), root=root, incoming=incoming)
            self.assertEqual(result.returncode, 0, result.stderr)
            staged = root / "opt" / "releases" / release_id
            self.assertTrue((staged / "manifest.json").is_file())
            self.assertFalse((root / "opt" / "current").exists())

    def test_stage_rejects_corrupt_archive_and_outside_input(self):
        with tempfile.TemporaryDirectory() as name:
            root, incoming = Path(name), Path(name) / "incoming"
            archive, checksum, _ = self.make_bundle(root, incoming)
            archive.write_bytes(archive.read_bytes() + b"corrupt")
            result = self.run_installer("stage-release", str(archive), str(checksum), root=root, incoming=incoming)
            self.assertNotEqual(result.returncode, 0)
            outside = root / "outside.tar.gz"
            outside.write_bytes(b"x")
            result = self.run_installer("stage-release", str(outside), str(checksum), root=root, incoming=incoming)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
