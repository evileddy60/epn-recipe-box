# Specification: Recipe Box Peer Synchronization

## Objective
Allow two EPN Recipe Box installations to exchange recipe cards over a manually configured LAN/Tailscale URL without a paid service, automatic deletion, silent conflict overwrites, or new listening services.

## Current stack and commands
- Python 3.11-compatible Flask 3.1.3 application.
- SQLite database selected by `EPN_DATA_DIR`.
- Existing commands: `python3 app.py`, Gunicorn service command.
- New validation: `python3 -m unittest discover -s tests -v`, `python3 -m py_compile app.py sync.py tests/test_sync.py`.

## Scope and acceptance criteria
1. Existing recipes remain readable after startup and migration.
2. Every recipe has a stable ID, `created_at`, `updated_at`, and deterministic SHA-256 checksum.
3. Each installation has a persistent ID and a sync token supplied by environment configuration.
4. Users can add, disable, and remove trusted peers; tokens are not rendered or logged.
5. Authenticated peers can list changed recipes and retrieve a single recipe.
6. Manual sync previews changes before import and reports new, updated, unchanged, conflict, and failure counts.
7. One-sided changes import the valid newer version; two-sided changes preserve both versions as a conflict.
8. Imports are idempotent, do not propagate deletions, and cannot loop through the same peer baseline.
9. Request size, payload shape, URL scheme, timeout, and authentication are constrained.
10. Sync UI is responsive, keyboard usable, semantically structured, and retains the existing warm recipe-card language.

## Architecture
- Keep Flask, SQLite, server-rendered templates, and the existing host/port.
- Put pure synchronization validation, checksums, and merge decisions in `sync.py`.
- Add additive SQLite tables/columns with an automatic timestamped database backup before schema changes.
- Use an installation token from `SYNC_TOKEN`; store only a SHA-256 token hash for inbound authentication. Peer bearer tokens are stored in local SQLite because they are operator configuration and never committed.
- Use Python standard-library `urllib.request` for outbound peer calls, with a short configurable timeout.

## API contract
- `GET /api/sync/manifest?since=<ISO timestamp>` — authenticated peer metadata and changed recipe summaries.
- `GET /api/sync/recipes/<recipe_id>` — authenticated full recipe payload.
- `POST /api/sync/preview` — authenticated local session; body `{peer_id}`; returns a preview and no writes.
- `POST /api/sync/run` — authenticated local session; body `{peer_id}`; imports only approved non-conflicting changes and records history.
- `POST /api/sync/conflicts/<conflict_id>/resolve` — authenticated local session; body `{resolution: keep_local|use_remote|keep_both}`.

All JSON errors use `{ "error": { "code": string, "message": string } }`. Incoming recipes are validated before database use.

## Testing strategy
Use Python's built-in `unittest` because the repository has no test framework and the feature can be tested without adding a runtime dependency. Unit tests cover pure merge/checksum/validation behavior; Flask test-client tests cover sync endpoints and auth; a two-instance temporary-directory demonstration covers the network path.

## Boundaries
- Always: preserve existing recipe fields, validate peer data, use parameterized SQL, redact secrets, run tests and compile checks.
- Ask first: changing the public API contract, adding a paid/proprietary service, or changing the existing host/port model.
- Never: automatic deletes, arbitrary file access, shell execution, credential logging, production-data demonstrations, pushing, merging, deploying, releasing, or opening a PR.

## Open limitations
The first version synchronizes recipe cards only. Ratings, comments, avatars, and user accounts remain local. Conflicts preserve a copy and require explicit resolution. Peer discovery and automatic background sync are intentionally excluded.
