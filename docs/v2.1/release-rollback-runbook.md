# Release rollback runbook

Rollback is a controlled recovery operation, not a code-only switch. The backup must contain `recipe_box.db`, `uploads/`, `recipe-images/`, and a verified checksum manifest.

1. Record the active and previous release IDs and incident reason.
2. Stop `epn-recipe-box.service`; confirm it is inactive.
3. Run `verify-backup <backup-path>`; reject missing, unsafe, symlinked, corrupt, or out-of-root backups.
4. Restore the database and both asset trees transactionally into `/var/lib/epn-recipe-box`, preserving the prior state until replacement succeeds.
5. Atomically replace `/opt/epn-recipe-box/current` with the previous release. Never overwrite release directories.
6. Start the service and verify local health, schema derived from the restored database, and independent Tailscale HTTPS health.
7. Record the rollback in the release index. Keep the failed release and backup.

If the restore or validation fails, stop and preserve evidence; do not perform a second automatic rollback. A human operator must assess the data boundary and service state.

The shared virtual environment is a separate rollback boundary. If requirements changed, restore the pre-change venv snapshot before starting the previous release, then re-run its health checks.
