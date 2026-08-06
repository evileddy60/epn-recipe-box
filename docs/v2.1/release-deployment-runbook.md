# Release deployment runbook

This runbook is operator-facing. It does not authorize a live deployment by itself.

## Preflight on `epn-hermes-01`

1. Confirm the exact branch and commit: `git status --short --branch`, `git rev-parse HEAD`.
2. Confirm the worktree is clean and the commit is an ancestor of `fix/comment-owner-hide`.
3. Run the full CI-equivalent validation suite.
4. Build with `tools/build_release.py <full-sha>`; inspect the manifest and detached checksum.
5. Confirm the archive is stored in `/srv/samba/HERMES-01/Projects/EPN-Recipe-Box-Releases/`.

## Transfer and staging

The orchestrator copies only the archive and detached checksum to `/var/tmp/epn-recipe-box-incoming/` using rsync over SSH. It verifies both local and remote SHA-256 values, then invokes the approved root-owned installer. `stage-release` extracts to a new release directory, applies ownership/mode policy, and verifies the manifest without changing `current`.

## Activation gate

Before activation, verify a fresh online backup and its manifest. Run isolated restore rehearsal using a temporary root, then run `activate-release <release-id>`. Activation stops the service safely, rechecks backup and release integrity, atomically replaces `current`, runs the documented migration command, starts the service, checks `http://127.0.0.1:5055/api/v1/health`, checks the Tailscale URL independently, and checks the expected schema.

If any critical post-switch check fails, the orchestrator performs exactly one rollback using the verified backup and previous release ID. Do not retry rollback automatically.

## Post-deployment

Verify persistence with a read-only health request and a service restart followed by health/schema checks. Create and verify a post-deployment backup. Append deployment status and timestamps to the release index. Retain failed releases for audit.

## Never do during normal deployment

Do not run Git on the Pi, copy into `current` or an active release, modify production data without a verified backup, mount SMB, enable Funnel, change ACLs, open ports, expose secrets, or delete the old checkout. The old checkout is retained through migration validation.
