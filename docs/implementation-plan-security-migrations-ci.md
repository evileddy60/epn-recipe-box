# Implementation plan: Security, migrations, backups, and CI

## Milestone 1 — contracts and baseline tests

- Add configuration, CSRF, header, auth-abuse, upload, error, health, migration, backup, and CI contract tests.
- Preserve existing regression tests and run them before each vertical slice.

## Milestone 2 — runtime security

- Add environment-aware startup validation and session configuration.
- Add signed-session CSRF helpers and inject tokens into existing forms.
- Add security headers and request-size/error handlers.
- Add bounded authentication failures and generic login responses.
- Add validation limits and content-based avatar handling.
- Enable SQLite foreign keys on every connection.

## Milestone 3 — explicit migrations

- Extract schema creation into numbered migration functions.
- Add schema-version tracking and pre-migration backups.
- Keep legacy columns/data and synchronization/category/tag relationships intact.
- Add failure rollback and idempotence fixtures for each historical schema.

## Milestone 4 — backup and health operations

- Add `tools/recipe_box_backup.py` with backup, verify, and restore commands.
- Add temporary-database tests for integrity, overwrite refusal, row counts, and schema version.
- Add `/health` with a deliberately minimal response.

## Milestone 5 — CI and documentation

- Add development dependencies in `pyproject.toml` while leaving runtime requirements separate.
- Add `.github/workflows/ci.yml` with pinned actions and meaningful checks.
- Update README, `.env.example`, deployment guidance, migration/backup docs, and ADR.

## Validation gates

1. Focused RED/GREEN tests for each slice.
2. Full unittest suite, compilation, Ruff, Bandit, pip-audit, diff check.
3. Historical migration and backup/restore tests.
4. Isolated two-instance synchronization demonstration.
5. Gunicorn browser validation with console/network checks.
6. Clean working tree, no listeners, and no Hermes changes.
