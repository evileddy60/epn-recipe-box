# EPN Recipe Box release bundle format v1

## Naming

- Release ID: `YYYYMMDD-HHMMSS-<short-git-sha>` (UTC)
- Archive: `<release-id>.tar.gz`
- Detached checksum: `<release-id>.tar.gz.sha256`

## Archive root

The archive contains one directory named `<release-id>/` with tracked application files plus:

- `manifest.json`
- `checksums.sha256`

`manifest.json` has `format`, `release_id`, `git_commit`, `branch`, `build_timestamp_utc`, `python_version`, `requirements_sha256`, `builder_version`, `expected_schema_version`, `release_notes_summary`, `source_repository_url`, and `files`. Each `files` item has a normalized relative `path`, byte `size`, and lowercase SHA-256 `sha256`.

The manifest and checksum files are metadata and are not included in the file list they describe. The detached checksum uses standard two-space `sha256sum` format.

## Exclusions

`.git`, databases, `data/`, `uploads/`, `recipe-images/`, backups, `.env*`, tokens, credentials, signing keys, caches, logs, temporary files, and symlinks are excluded or rejected. A bundle must contain `app.py`, `requirements.txt`, and the application package.

## Verification rules

The installer accepts only an archive copied into the approved incoming directory. It verifies the detached archive checksum, rejects absolute/parent-traversal paths and symlinks, requires the expected repository URL and branch, checks every file digest, and refuses to overwrite an existing release. The installer never changes `current` during staging.

## Audit record

The Samba-backed `release-index.jsonl` records release ID, commit, branch, archive name, archive digest, and status. Deployment status and activation/rollback timestamps are appended by the orchestrator; no secrets or production data are recorded.
