# ADR: Release-based deployment from `epn-hermes-01`

- Status: proposed; implementation prepared, production installation not performed
- Date: 2026-08-06
- Decision owners: EPN Recipe Box maintainers

## Context

The Pi currently runs a Git checkout directly from `/opt/epn-recipe-box`. That couples production to Git history and makes an update partially mutable. `epn-hermes-01` is the authoritative development, test, GitHub, and release host. `epn-hermes-worker-01` is a runtime-only host.

## Decision

Build a Git-pinned, checksum-verified release on `epn-hermes-01`, retain archives under `/srv/samba/HERMES-01/Projects/EPN-Recipe-Box-Releases/`, transfer the archive and detached checksum to a restricted incoming directory over SSH/rsync, and stage it into a never-overwritten release directory. Production uses `/opt/epn-recipe-box/current` as an atomic symlink.

Persistent state remains in `/var/lib/epn-recipe-box`; backups are complete state boundaries under `/var/backups/epn-recipe-box`. The production host does not need Git for normal deployment.

## Alternatives

- **Mutable checkout:** rejected; not atomic and couples runtime to Git.
- **SMB mount on the Pi:** rejected as a requirement; SSH/rsync has a smaller failure and credential surface.
- **Per-release virtual environments:** rejected for the Pi's limited storage and slower deployment. Use a shared `/opt/epn-recipe-box/shared/venv`, versioned by requirements checksum and snapshotted before dependency changes.

## Release identity and integrity

Release IDs are `YYYYMMDD-HHMMSS-<short-git-sha>`. A manifest records the full SHA, branch, UTC build time, Python version, dependency checksum, builder version, expected schema, source URL, release notes, and every tracked file's SHA-256 and size. `checksums.sha256` covers release files and the archive has a detached `.sha256` file.

No production data, environment files, caches, secrets, or Git metadata enter a bundle.

## Rollback and retention

Activation requires a fresh verified online backup, integrity checks, migration, local health, schema, and independent Tailscale health checks. A critical failure triggers one rollback attempt: stop, restore database and assets from the verified backup, repoint `current`, start, and revalidate. Failed release directories are retained for audit. Keep the active release plus the last five verified releases and all referenced rollback backups; cleanup refuses the active release and never deletes failed releases automatically.

## Consequences

Deployments become auditable and atomic, while the first migration requires a carefully reviewed systemd override and a service-user permission change. The shared venv is a dependency rollback boundary and must be backed up before mutation.
