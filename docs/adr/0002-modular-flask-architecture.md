# ADR-0002: Modular Flask package with compatibility entrypoint

## Status

Accepted

## Context

The synchronization implementation was recovered as a working Flask application in a single 1,988-line `app.py`. It combined configuration, SQLite persistence and migrations, recipe/inventory domain logic, synchronization services, embedded templates, inline CSS/JavaScript, and all HTTP routes. The application already has public route names, form actions, importable helper functions, a Gunicorn entrypoint, and a SQLite database format that must remain compatible.

A direct conversion to an application package would risk breaking `app:app`, `python3 app.py`, existing tests that import `app`, and callers that override module-level data paths during isolated tests.

## Decision

Use a `recipe_box/` package with focused modules:

- `config.py` — paths, environment variables, and application constants.
- `db.py` — SQLite connection, schema creation, migrations, persistence, and row conversion.
- `domain.py` — ingredient matching, recipe suggestions, recipe decoration, and uploads.
- `sync_service.py` — peer manifest fetching, validation, preview, synchronization, and conflict actions.
- `routes.py` — Flask blueprint containing the existing routes and form behavior.
- `application.py` — application factory and blueprint registration.
- `templates/` — filesystem Jinja templates.
- root `app.py` — compatibility entrypoint that exposes `app`, `create_app`, and legacy helper imports.
- root `static/` — existing image plus extracted CSS and JavaScript.

The root entrypoint retains the existing Gunicorn target (`app:app`) and executable development behavior (`python3 app.py`). Blueprint endpoint aliases preserve the original endpoint names used by templates and callers. The compatibility entrypoint synchronizes legacy mutable path globals into the new modules for existing tests and integrations.

## Alternatives considered

### Move directly to `app/` and remove `app.py`

Rejected because it would change the existing Gunicorn/import entrypoint and break `python3 app.py` without a compatibility shim.

### Keep the monolith and only extract templates

Rejected because it would leave database, domain, synchronization, and route responsibilities coupled and would not provide a maintainable growth boundary.

### Introduce an ORM or new database layer

Rejected because this refactor must preserve SQLite schema and existing data compatibility without adding dependencies or changing persistence behavior.

## Consequences

- The application has clear module boundaries without changing public URLs or database tables.
- Existing `app:app` deployment remains valid.
- Legacy mutable module-level path overrides remain supported through a deliberate compatibility bridge.
- Blueprint-prefixed endpoint names exist internally, while unprefixed aliases preserve the prior public endpoint contract.
- The migration path now correctly avoids duplicate `ALTER TABLE` operations after a legacy schema upgrade; this fixes an uncovered latent migration defect while preserving the intended documented behavior.
- Future feature work can add service/repository boundaries incrementally without another monolithic extraction.
