# ADR-0004: Built-in migrations and operational security baseline

## Status

Accepted

## Date

2026-08-05

## Context

Recipe Box is a small single-instance Flask/SQLite application with an existing legacy schema, synchronization schema, and categories/tags schema. Its startup currently creates tables and conditionally adds columns, which makes migration history implicit and does not provide reliable failure/version semantics. It also has development secret fallback behavior, no CSRF layer, implicit session settings, and no automated backup verification.

The application must remain easy to run on a Raspberry Pi and must not gain a mandatory external service solely for security or operations.

## Decision

Use a small built-in numbered migration runner backed by a `schema_version` table. Each migration is a Python function that executes inside a transaction, records its version only after success, and creates a timestamped SQLite backup before pending migrations. Use SQLite's own backup/integrity mechanisms for operational verification.

Add a small signed-session CSRF implementation rather than Flask-WTF. Synchronization bearer endpoints remain exempt because they authenticate with their own bearer token and are not browser-session state changes. Add environment-aware configuration, secure response headers, bounded local login abuse protection, Pillow-based avatar verification, and minimal health/error endpoints.

Keep runtime dependencies limited to Flask, Gunicorn, and Pillow. Put Ruff, Bandit, pip-audit, and test tooling in development dependencies and CI.

## Alternatives considered

### Alembic

Rejected for now: it adds ORM/migration machinery disproportionate to this small raw-SQLite application and would require introducing a new metadata layer. The built-in runner provides explicit ordering and rollback semantics with fewer moving parts.

### Flask-WTF

Rejected: the application only needs a small token check and already uses plain HTML forms. A local CSRF helper keeps the dependency surface and template changes small.

### Redis-backed rate limiting

Rejected: it would add an external service and operational failure mode for a single-instance application. A bounded in-process limiter is sufficient for the stated threat model, with its multi-worker limitation documented.

## Consequences

- Migration history becomes inspectable and testable.
- Failed migrations can be rolled back without falsely advancing schema state.
- Backups are created before structural changes and can be verified independently.
- A process-local login limiter does not coordinate across multiple workers or hosts; public Internet exposure still requires a reverse proxy/WAF or external rate limiter.
- LAN/Tailscale HTTP remains supported, but secure cookies and HSTS require explicit HTTPS mode.
- Existing synchronization payloads and route contracts remain unchanged except for additive operational endpoints and security behavior.
