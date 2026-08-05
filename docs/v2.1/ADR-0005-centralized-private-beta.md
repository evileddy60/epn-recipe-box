# ADR-0005: Centralized Private-Beta Deployment on epn-hermes-worker-01

- Status: accepted for private beta
- Date: 2026-08-05
- Scope: deployment only; no community-sharing behavior change
- Server source: `feature/android-api-foundation` at `a86da882da146b669857f550f05bf63e2b9e20e8`

## Decision

Deploy EPN Recipe Box as one authoritative Flask/Gunicorn/SQLite instance on `epn-hermes-worker-01`, reachable only through Tailscale Serve HTTPS. Gunicorn binds to loopback and Tailscale Serve proxies only to that loopback listener. No router ports, public listeners, Funnel, ACL changes, Docker, nginx, Caddy, PostgreSQL, or Android build tooling are part of this deployment.

## Layout

- Deployment user: `epn-recipe-box`
- Application checkout: `/opt/epn-recipe-box`
- Virtual environment: `/opt/epn-recipe-box/.venv`
- Persistent data: `/var/lib/epn-recipe-box`
- SQLite database: `/var/lib/epn-recipe-box/recipe_box.db`
- Avatar uploads: `/var/lib/epn-recipe-box/uploads`
- Recipe images: `/var/lib/epn-recipe-box/recipe-images`
- Environment file: `/etc/epn-recipe-box/epn-recipe-box.env` (root-readable)
- Backups: `/var/backups/epn-recipe-box`
- systemd unit: `epn-recipe-box.service`
- Gunicorn: `127.0.0.1:5055`, two workers
- Logs: journald, bounded by the system journal policy; no application secrets in logs
- Backup method: repository backup tool plus image sidecar, executed only against explicit paths
- Restore method: verified backup restore to an isolated path first, then controlled service stop/copy/start for an approved restore
- Final URL: the target's MagicDNS hostname, `https://<tailscale-magicdns-host>/`
- Android API base: `https://<tailscale-magicdns-host>/api/v1`

## Security and recoverability

The service uses a dedicated unprivileged account, a production environment, a generated secret outside Git, loopback-only Gunicorn binding, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem`, and a narrow writable data path. Tailscale Funnel remains disabled. The beta database starts empty and is not populated from any existing Recipe Box data.

Backups include the SQLite file and recipe-image sidecar. Verification and restore tests use separate temporary paths. Persistent application data and secrets are excluded from Git.

## Current visibility behavior (verified from source)

The existing implementation exposes all non-archived recipes to authenticated browser/API users. `owner_id` is retained and only the owner may edit, archive/unarchive, or export. Favorites are user-local. Archive state is stored on the recipe row and the current list queries do not scope it to the requesting user. Android and browser clients therefore do not yet implement the desired private archive semantics.

This ADR deliberately does not change that behavior during deployment.

## Deferred EPN community-sharing proposal

For the desired model, add an explicit recipe visibility policy rather than overloading ownership:

1. Add `visibility` to `recipes`, defaulting legacy rows to `shared_epn` only after an owner-confirmed migration decision. Recommended enum: `private`, `shared_epn`, `selected_users`, `public`.
2. Add `recipe_visibility_users(recipe_id, user_id, created_at)` for selected-user grants, with foreign keys and indexes.
3. Add user-local `recipe_user_state(user_id, recipe_id, favorite, archived_at, created_at, updated_at)` and migrate favorites/archive state out of shared recipe state. Preserve existing favorites and archived values per user during migration.
4. Centralize an authorization predicate used by browser routes, API routes, image delivery, search, categories/tags, comments/ratings, and synchronization. Owner-only mutation remains mandatory; visibility controls reads.
5. Extend API responses with `owner`, `visibility`, and a client-safe `can_edit`/`can_delete` capability set. Add owner-only mutation endpoints and image upload/delete endpoints deliberately; do not infer permissions in Android.
6. Update browser templates and filters to distinguish `My recipes`, `Shared with EPN`, and private archive/favorites. Enforce the same predicate server-side.
7. Update Android models, API contract, cache keys, list filters, detail capabilities, and offline behavior. Never cache a recipe across accounts without its visibility/account authorization context.
8. Define synchronization as an explicit server export/import protocol. Do not synchronize private favorites, private archives, API tokens, passwords, or selected-user grants through the old peer payload. Legacy peers should be read-only or isolated until upgraded.

Recommendation: implement `private` and `shared_epn` first; keep `selected_users` and `public` schema-ready but disabled by default. Public visibility should require an explicit feature flag and remain unavailable in the private beta. The main risk is authorization drift across browser, API, image, and sync paths; mitigate with shared policy functions and matrix tests for owner/non-owner, each visibility state, archive, favorite, and token/account boundaries.

## Rollback

Stop the service, disable the new unit/Serve route if required, preserve the deployment directory and logs, and remove only the isolated beta paths after evidence is retained. No schema downgrade or production-data restore is needed because the beta database is created empty.
