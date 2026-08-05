# Specification: Security Baseline, Migrations, Backups, and CI

## Objective

Add a production-oriented security and operational baseline to EPN Recipe Box without changing existing recipe, search, category, tag, inventory, rating, comment, avatar, or synchronization behavior.

## Runtime configuration

- `EPN_ENV=development` permits a clearly identified development-only secret fallback.
- Production-like environments (`production`, `prod`) require `SECRET_KEY` of at least 32 characters and fail startup with an actionable configuration error.
- `EPN_HTTPS=1` enables secure session cookies and HSTS.
- LAN/Tailscale HTTP remains supported with `SESSION_COOKIE_SECURE=false` and no HSTS; it must not be described as public-Internet safe.
- Secrets are never logged, rendered, or included in client errors.

## Web security

- All HTML state-changing forms use a small signed-session CSRF token.
- Bearer-authenticated synchronization endpoints remain bearer-authenticated and do not require the browser CSRF token.
- Session cookies are HttpOnly, SameSite=Lax, and Secure only when HTTPS mode is enabled.
- Login rotates the session after successful authentication.
- Response headers include content-type sniffing protection, referrer policy, permissions policy, CSP frame protection, and a compatible CSP. HSTS is HTTPS-only.
- Login failures are generic and locally rate-limited with bounded temporary lockout.
- Security events use structured logging without passwords, tokens, session contents, or recipe contents.

## Validation and uploads

Server-side limits apply to account, profile, recipe, comment, inventory, search, peer, and upload inputs. Avatar uploads:

- use a generated filename;
- are stored below the configured upload directory;
- are checked by Pillow content parsing and verification;
- reject malformed/non-image files and oversized dimensions;
- are saved in a safe image format;
- replace old avatars without following user-supplied paths.

## Migration system

- `schema_version` stores ordered integer migration versions.
- Migrations are explicit numbered functions.
- Startup creates a timestamped backup before applying pending migrations.
- Each migration runs transactionally where SQLite permits.
- A failed migration rolls back and does not record its version.
- Startup is idempotent.
- Historical schemas covered: original legacy schema, synchronization schema, and categories/tags schema.
- No migration runs against production data in tests.

## Backup/restore

A small CLI provides:

- timestamped backup creation;
- `PRAGMA integrity_check` verification;
- restore to a separate target;
- restored schema-version and row-count verification;
- refusal to overwrite an existing target unless `--force` is explicit.

## Health and errors

- `GET /health` returns only application status, database status, and schema version.
- Browser requests receive HTML error pages; API requests receive structured JSON.
- Handlers cover 400, 401, 403, 404, 413, 429, and 500 without exposing internals.
- Existing synchronization JSON error codes remain stable.

## CI

`.github/workflows/ci.yml` runs on pushes and pull requests with pinned major action versions. It installs runtime and development dependencies, runs formatting/lint/compile/tests/migration/backup checks, two-instance sync where deterministic, `pip-audit`, `bandit`, and whitespace validation across a supported Python matrix.

## Non-goals

- No Redis or external rate-limit service.
- No Alembic dependency.
- No public deployment or reverse-proxy configuration.
- No deletion propagation changes.
- No production-data reset or rewrite.
