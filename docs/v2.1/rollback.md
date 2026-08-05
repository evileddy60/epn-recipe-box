# EPN Recipe Box 2.1 Rollback Plan

## Principles

The first slice is additive. Existing browser routes, peer synchronization, and schema compatibility remain the rollback anchor. No database downgrade is planned.

## Before deployment

- Keep the current server commit and a verified database/image backup.
- Build/test the API with an isolated `EPN_DATA_DIR`.
- Do not change firewall, router, Tailscale ACL, or production systemd state automatically.

## API rollback

1. Stop accepting Android traffic at the Tailscale Serve layer or stop the new API service if separately deployed.
2. Keep the existing web application available.
3. Revert the server branch commit only after preserving logs and the database backup.
4. If the API token table migration has already run, leave the additive table in place; the existing web/sync code does not depend on it. Do not delete data as part of rollback.
5. Re-run existing migration, browser, backup, and synchronization validation in an isolated copy.

## Android rollback

- Remove or revoke the private APK distribution artifact.
- Revoke affected user API tokens server-side.
- Users can uninstall the app; no server database rollback is required.
- Do not distribute a replacement signed artifact until the defect is reproduced, fixed, and validated.

## Data recovery

Use `tools/recipe_box_backup.py verify` before restore. Restore only to an explicitly chosen isolated or approved target. Never overwrite production data automatically.

## Exit criteria

- Existing web routes respond.
- Peer synchronization remains authenticated and functional.
- No public listener or firewall rule was introduced.
- API credentials are revoked if Android access was withdrawn.
- Git state and backups are recorded for human review.
