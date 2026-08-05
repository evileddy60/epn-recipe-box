# EPN Recipe Box 2.1 Implementation Milestones

## M1 — Specifications and contracts

- Architecture, threat model, API contract, OpenAPI, deployment, Android design, rollback, and context docs.
- Gate: documents present and internally consistent.

## M2 — Server API foundation

- API token migration/table and service.
- Versioned auth, identity, recipe, taxonomy, and health endpoints.
- Structured errors, pagination/filter validation, size limits, rate limiting.
- OpenAPI and server tests.
- Gate: existing suite plus API suite passes.

## M3 — Pi/Tailscale preparation

- Canonical API base URL configuration.
- Readiness response and deployment documentation/systemd template updates.
- No deployment or network changes.
- Gate: isolated Gunicorn startup and health check.

## M4 — Android project and setup/auth

- Kotlin/Compose/Material 3 project, URL validation, health check, login, Keystore token abstraction.
- Gate: Gradle build/unit tests when toolchain exists.

## M5 — Recipe vertical slice

- Room cache, list/search/filter, detail, create, logout/cache clear.
- Gate: repository/ViewModel/Compose tests and API integration demonstration.

## M6 — Security and distribution readiness

- Network security, release/debug separation, screenshot/backups review, signing/distribution docs.
- Gate: no production key, no secret, release configuration validation.

## M7 — End-to-end and review

- Isolated server + Android test build demonstration; web and sync regression suite; cleanup and git review.
- Gate: human review before any push/deploy/private beta.
