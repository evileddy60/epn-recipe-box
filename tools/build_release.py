#!/usr/bin/env python3
"""Build a verified, Git-pinned EPN Recipe Box release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess  # nosec B404 - required for fixed Git and validation commands
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

APPROVED_BRANCH = "fix/comment-owner-hide"
REPOSITORY_URL = "https://github.com/evileddy60/epn-recipe-box.git"
BUILDER_VERSION = "1.0.0"
SECRET_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
SECRET_PARTS = ("secret", "token", "credential", "password", "signing-key")
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "data", "uploads", "recipe-images", "backups"}


def run(*args: str, cwd: Path, capture: bool = True) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=capture, check=False)  # nosec B603 - callers pass fixed Git commands
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "command failed").strip())
    return result.stdout.strip() if capture else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_release_id(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{7,40}", value))


def tracked_paths(repo: Path, commit: str) -> list[str]:
    return [line for line in run("git", "ls-tree", "-r", "--name-only", commit, cwd=repo).splitlines() if line]


def is_forbidden(path: str) -> bool:
    parts = Path(path).parts
    lowered = path.lower()
    if any(part in EXCLUDED_PARTS for part in parts):
        return True
    basename = Path(path).name.lower()
    if basename in SECRET_NAMES or basename.startswith(".env"):
        return True
    return any(part.lower() in SECRET_PARTS or any(marker in part.lower() for marker in SECRET_PARTS) for part in parts)


def run_validation(repo: Path, command: str) -> None:
    if command:
        result = subprocess.run(command, cwd=repo, shell=True, text=True, capture_output=True, check=False)  # nosec B602 - validation command is local operator configuration
        if result.returncode:
            raise RuntimeError(f"validation failed ({result.returncode}): {(result.stderr or result.stdout).strip()}")


def build_release(repo: Path, commit: str, staging: Path, validation_command: str | None = None) -> dict[str, object]:
    repo = repo.resolve()
    staging = staging.resolve()
    if not (len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)):
        raise ValueError("an exact 40-character lowercase commit SHA is required")
    if run("git", "status", "--porcelain", cwd=repo):
        raise RuntimeError("Git working tree is dirty")
    current_branch = run("git", "branch", "--show-current", cwd=repo)
    if current_branch != APPROVED_BRANCH:
        raise RuntimeError(f"unapproved branch: {current_branch or 'detached'}")
    run("git", "cat-file", "-e", f"{commit}^{{commit}}", cwd=repo)
    approved_ref = f"refs/heads/{APPROVED_BRANCH}"
    run("git", "merge-base", "--is-ancestor", commit, approved_ref, cwd=repo)
    if run("git", "rev-parse", "HEAD", cwd=repo) != commit:
        raise RuntimeError("requested commit is not the current HEAD")
    run_validation(repo, validation_command or f"{shlex.quote(sys.executable)} -m unittest discover -s tests -v")

    short_sha = commit[:12]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    release_id = f"{timestamp}-{short_sha}"
    if not validate_release_id(release_id):
        raise RuntimeError("generated release ID is invalid")
    release_dir = staging / release_id
    if release_dir.exists():
        raise FileExistsError(release_dir)
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="epn-release-") as temp_name:
        export_dir = Path(temp_name) / "source"
        export_dir.mkdir()
        archive_bytes = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repo, capture_output=True, check=True).stdout  # nosec - fixed executable and validated SHA
        tar_path = Path(temp_name) / "source.tar"
        tar_path.write_bytes(archive_bytes)
        with tarfile.open(tar_path) as archive:
            for member in archive.getmembers():
                member_path = (export_dir / member.name).resolve()
                if member_path != export_dir and export_dir not in member_path.parents:
                    raise RuntimeError(f"unsafe Git archive member: {member.name}")
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise RuntimeError(f"unsafe Git archive member: {member.name}")
            archive.extractall(export_dir)  # nosec B202 - every member is validated immediately above
        # git archive may create a top-level directory only when prefix is supplied; here it does not.
        for path in export_dir.rglob("*"):
            if path.is_file() and is_forbidden(path.relative_to(export_dir).as_posix()):
                path.unlink()
        release_dir.mkdir()
        for path in export_dir.iterdir():
            destination = release_dir / path.name
            if path.is_dir():
                shutil.copytree(path, destination)
            else:
                shutil.copy2(path, destination)

    files = []
    for path in sorted(p for p in release_dir.rglob("*") if p.is_file()):
        relative = path.relative_to(release_dir).as_posix()
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256(path)})
    requirements = release_dir / "requirements.txt"
    requirements_sha = sha256(requirements) if requirements.exists() else None
    manifest = {
        "format": "epn-recipe-box-release/v1",
        "release_id": release_id,
        "git_commit": commit,
        "branch": APPROVED_BRANCH,
        "build_timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "requirements_sha256": requirements_sha,
        "builder_version": BUILDER_VERSION,
        "expected_schema_version": 14,
        "release_notes_summary": run("git", "log", "-1", "--format=%s", commit, cwd=repo),
        "source_repository_url": REPOSITORY_URL,
        "files": files,
    }
    (release_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # The manifest is intentionally excluded from its own file list.
    checksums = "".join(f"{item['sha256']}  {item['path']}\n" for item in files)
    (release_dir / "checksums.sha256").write_text(checksums, encoding="utf-8")
    archive_path = staging / f"{release_id}.tar.gz"
    checksum_path = staging / f"{release_id}.tar.gz.sha256"
    with tarfile.open(archive_path, "w:gz", compresslevel=9) as archive:
        for path in sorted(release_dir.rglob("*")):
            info = archive.gettarinfo(str(path), arcname=path.relative_to(staging).as_posix())
            info.mtime = 0
            if path.is_file():
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
    checksum_path.write_text(f"{sha256(archive_path)}  {archive_path.name}\n", encoding="utf-8")
    index_entry = {
        "release_id": release_id,
        "commit": commit,
        "branch": APPROVED_BRANCH,
        "archive": archive_path.name,
        "archive_sha256": sha256(archive_path),
        "status": "built",
    }
    with (staging / "release-index.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(index_entry, sort_keys=True) + "\n")
    return {
        "release_id": release_id,
        "release_dir": str(release_dir),
        "archive": str(archive_path),
        "checksum": str(checksum_path),
        "commit": commit,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--staging", type=Path, default=Path("/srv/samba/HERMES-01/Projects/EPN-Recipe-Box-Releases"))
    parser.add_argument("--validation-command", default=None)
    args = parser.parse_args()
    print(json.dumps(build_release(args.repo, args.commit, args.staging, args.validation_command), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
