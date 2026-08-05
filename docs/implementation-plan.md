# Implementation plan: Recipe Box sync

## Milestone 1 — contract and safe persistence
- Add `sync.py` pure functions for canonical recipe payloads, checksums, validation, token comparison, and merge classification.
- Add additive schema for installation identity, trusted peers, per-peer baselines, conflicts, and sync history.
- Back up an existing database before adding missing schema objects.

## Milestone 2 — authenticated API and pull client
- Add token-authenticated manifest and recipe endpoints.
- Add local peer CRUD and an outbound client with URL validation, request-size limits, timeout handling, and redacted errors.
- Add preview/run/resolution routes.

## Milestone 3 — server-rendered sync UI
- Add a `/sync` page using existing embedded-template conventions.
- Provide peer form, status, preview/results, and conflict actions.
- Add style tokens, visible focus states, semantic headings, responsive layout, and reduced-motion support without replacing the existing styling system.

## Milestone 4 — tests and demonstration
- Add `unittest` coverage for IDs/checksums, validation, auth, merge modes, duplicate/loop protection, malformed/oversized responses, timeout, and no deletion propagation.
- Run an isolated two-instance local demonstration with two temporary SQLite data directories.

## Expected files
- `app.py`
- `sync.py`
- `tests/test_sync.py`
- `README.md`
- `.env.example`
- `docs/SPEC-recipe-box-sync.md`
- `docs/adr/0001-peer-sync.md`
- `docs/implementation-plan.md`
- `docs/visual-style.md`

## Migration and rollback
1. Never point this branch at production data during tests.
2. For an existing DB, copy `recipe_box.db` to `recipe_box.db.pre-sync-<UTC timestamp>.bak` before schema alteration.
3. The migration only creates tables and adds nullable/defaulted recipe columns; existing rows remain intact.
4. Roll back code by checking out the prior branch. To roll back schema, stop the service and restore the pre-sync backup using the existing README backup procedure.
5. No data migration runs in the demonstration; each instance uses a temporary directory.
