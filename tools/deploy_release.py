#!/usr/bin/env python3
"""Development-host release orchestrator; production mutations occur only via the installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess  # nosec B404 - required for the explicit SSH/rsync transport
from pathlib import Path

from tools.build_release import build_release

HOST = "epn-hermes-worker-01"
INCOMING = "/var/tmp/epn-recipe-box-incoming"  # nosec B108 - approved, mode-restricted incoming directory
INSTALLER = "/usr/local/sbin/epn-recipe-box-release-deploy"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def remote(command: str) -> str:
    return subprocess.check_output(["ssh", "-o", "BatchMode=yes", HOST, command], text=True).strip()  # nosec - fixed host and approved command construction


def deploy(repo: Path, commit: str, staging: Path, dry_run: bool = False) -> dict[str, object]:
    artifact = build_release(repo, commit, staging)
    archive = Path(str(artifact["archive"]))
    checksum = Path(str(artifact["checksum"]))
    if dry_run:
        return {
            "mode": "dry-run",
            **artifact,
            "archive_sha256": sha256(archive),
            "actions": ["rsync", "remote checksum verification", "installer invocation"],
        }
    subprocess.run(["ssh", HOST, f"mkdir -p {INCOMING}"], check=True)  # nosec - fixed host and path
    subprocess.run(["rsync", "--archive", "--checksum", str(archive), str(checksum), f"{HOST}:{INCOMING}/"], check=True)  # nosec - local generated artifacts only
    remote_archive = f"{INCOMING}/{archive.name}"
    remote_checksum = f"{INCOMING}/{checksum.name}"
    remote_digest = remote(f"sha256sum {remote_archive} | cut -d' ' -f1")
    if remote_digest != sha256(archive):
        raise RuntimeError("destination archive checksum differs")
    release_id = str(artifact["release_id"])
    backup = remote(f"sudo {INSTALLER} backup")
    remote(f"sudo {INSTALLER} verify-backup {backup}")
    remote(f"sudo {INSTALLER} stage-release {remote_archive} {remote_checksum}")
    # Rehearsal is a separate operator-approved fixture invocation; never use live data as a rehearsal target.
    remote(f"sudo {INSTALLER} activate-release {release_id} {backup}")
    remote(f"sudo {INSTALLER} health")
    post_backup = remote(f"sudo {INSTALLER} backup")
    remote(f"sudo {INSTALLER} verify-backup {post_backup}")
    return {"mode": "deployed", **artifact, "backup": backup, "post_backup": post_backup}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--staging", type=Path, default=Path("/srv/samba/HERMES-01/Projects/EPN-Recipe-Box-Releases"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(deploy(args.repo, args.commit, args.staging, args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
