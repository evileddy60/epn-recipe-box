# EPN Recipe Box 2.1 Private-Beta Onboarding

This document is for invited EPN members and operators. Do not publish it publicly.

## Server

- Browser URL: `https://epn-hermes-worker-01.tail510dca.ts.net/`
- Android API base URL: `https://epn-hermes-worker-01.tail510dca.ts.net/api/v1`
- Access requires an authorized Tailscale device/user. Tailscale Funnel is disabled.

## Joining the tailnet

1. Receive an invitation through the normal EPN/Tailscale administration process.
2. Install Tailscale on the device.
3. Sign in with the invited identity and confirm the device appears online.
4. Do not use a public IP, router port-forward, or cleartext HTTP URL.
5. If access should end, remove the device/user from the tailnet and separately revoke Recipe Box API tokens/account access.

## Browser client

1. Open the browser URL while connected to the tailnet.
2. Create an account with an email and a password of at least 8 characters.
3. Complete profile setup.
4. The current deployed application supports shared browsing of non-archived recipes, while ownership remains attached to each recipe. Only the owner can edit or archive a recipe.
5. Favorites are user-local. Archive behavior is not yet fully private per user; see the visibility limitation below.

## Android client

1. Install the private-beta APK supplied by the EPN operator. Do not download an APK from an untrusted public location.
2. Connect the device to the tailnet first.
3. Enter the HTTPS server URL shown above, without `/api/v1` unless the app specifically requests an API base.
4. Validate the server, sign in with the Recipe Box account, and keep the issued token in the app's secure storage.
5. Release builds must use HTTPS; public cleartext HTTP is intentionally rejected.
6. The current Android API slice supports login/logout, account information, recipe listing/search/filtering, recipe detail, authenticated images, categories/tags, and recipe creation. Image upload, edit/delete, and background synchronization are not included in this slice.

## Removing a user

The current private-beta application has no administrator UI for account deletion. An operator must perform this as a controlled maintenance operation:

1. Confirm the account identity and obtain explicit operator approval.
2. Stop `epn-recipe-box.service` on the server.
3. Back up and verify the current database and image sidecar first.
4. Delete the user through an approved maintenance script using the service account and a parameterized user ID/email. Do not run ad-hoc SQL copied into chat.
5. Verify the account and its cascaded records are absent, restart the service, and run the health/API smoke checks.
6. Revoke the user's tailnet/device access separately.

This procedure should become an authenticated admin workflow before broader membership growth.

## Revoking API tokens

- A client logout revokes the current token through `POST /api/v1/auth/logout`.
- For emergency revocation, stop the service and use the approved operator maintenance procedure to set `revoked_at` for the account's token rows, then restart and verify a 401 response.
- Tokens are opaque, individually issued, hashed in the database, expiring, and independent of the peer `SYNC_TOKEN`.

## Removing devices

Remove the device from the Tailscale tailnet using the normal Tailscale administration process. Removing a device from Tailscale does not revoke an already-issued application token, so revoke the Recipe Box token/account separately.

## Backups

The authoritative data is on `epn-hermes-worker-01`:

- Database: `/var/lib/epn-recipe-box/recipe_box.db`
- Recipe images: `/var/lib/epn-recipe-box/recipe-images/`
- Backups: `/var/backups/epn-recipe-box/`

Use `tools/recipe_box_backup.py` from the deployed repository with `PYTHONPATH=/opt/epn-recipe-box`, as the `epn-recipe-box` service account. Verify every backup and include the image sidecar when it exists. Do not copy environment files or secrets into backups or Git.

## Restores

1. Stop the service.
2. Verify the selected database backup and image sidecar in an isolated restore directory.
3. Confirm schema version, row counts, and image integrity.
4. Restore only after operator approval; never overwrite a live database casually.
5. Start the service and verify `/api/v1/health`, browser access, authenticated API access, and an authenticated image.
6. Preserve the pre-restore database and logs until the restore is accepted.

## Updating the server

1. Confirm the target commit and review the diff.
2. Back up and verify the database plus recipe images.
3. Stage a new application checkout beside the current checkout; do not overwrite persistent data.
4. Install declared dependencies into the matching virtual environment.
5. Run read-only compile checks and the isolated test suite.
6. Restart `epn-recipe-box.service` and verify health, listeners, logs, and HTTPS through Serve.
7. Roll back the application checkout if readiness checks fail; do not downgrade the database without a documented migration plan.

## Updating the Android app

1. Obtain the APK from the EPN operator and verify its release notes/checksum through the normal private channel.
2. Keep the server HTTPS hostname unchanged unless the operator announces a coordinated migration.
3. Sign in again if the app reports an expired or revoked token.
4. Do not enable cleartext HTTP or use a public server URL.

## Current visibility limitation

Source inspection confirmed that the current release exposes all non-archived recipes to authenticated browser/API users, preserves `owner_id`, and restricts editing/archive/export to the owner. Favorites are per-user, but archive state is stored on the recipe and is not yet consistently user-local across all list/read paths. The desired model of explicit `Private`, `Shared with EPN`, and future selected-user visibility requires the design in `ADR-0005-centralized-private-beta.md`; it was intentionally not implemented during this deployment.
