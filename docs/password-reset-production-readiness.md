# Password recovery production-readiness plan

This is a review artifact only. It contains no production commands to execute automatically.

## Public reset URL

Production EnvironmentFile addition:

```text
EPN_PUBLIC_BASE_URL=https://epn-hermes-worker-01.tail510dca.ts.net
```

The application now builds reset URLs from this explicit value. In production it rejects missing values, malformed values, HTTP, localhost, loopback addresses, userinfo, query strings, and fragments. It does not use the incoming Host header. Staging/development may use `http://localhost`.

## Migrations

- Migration 15 creates `password_reset_tokens` and is idempotent.
- Migration 16 adds `code_hash`, `web_token_hash`, `code_attempts`, and the web-token index.
- The isolated fixture path was tested from schema 14 through schema 16.
- Both migrations are transactional through the existing migration runner.
- No production database was opened or migrated during this review.

## Gmail credentials

- Current verified source: `/home/epn/.config/ai-work-facilitator/`
- Current source owner: `epn`
- Current files are mode 0600 and remain outside the Recipe Box tree.
- Production persistent path: NOT YET CREATED; it must be supplied through the approved secret-management boundary.
- The adapter needs read access to the token/client files and does not require write access for refresh. The refresh token allows a new access token to be obtained after reboot or access-token expiry.
- Missing files, invalid credentials, missing `gmail.send`, and insecure file modes fail closed.

## Dependency and release handling

Add the pinned Google dependencies through the normal release build from `requirements.txt`/`pyproject.toml`. Build a new immutable release directory, run the complete validation suite, and atomically update `/opt/epn-recipe-box/current`. Keep the prior release and virtual-environment rollback boundary intact.

## Backup

Before production migration, stop or quiesce the service as required by the existing runbook. Use the documented backup tool against `/var/lib/epn-recipe-box/recipe_box.db`, verify the exact resulting backup, and retain the recipe-image sidecar when present. The image sidecar is not required for password-reset data correctness, but the standard backup procedure should preserve it for complete release rollback.

## Deployment sequence

1. Review the exact release diff and checksums.
2. Confirm Gmail credential mount and permissions without copying credentials into the project.
3. Add the non-secret `EPN_PUBLIC_BASE_URL` and Gmail backend settings to the protected EnvironmentFile.
4. Create and verify a production backup.
5. Deploy the immutable server release.
6. Start/reload the service according to the operator runbook.
7. Verify `/health` and `/api/v1/health`; require schema version 16.
8. Exercise one controlled production reset request only after separate approval, using provider metadata and human email inspection.
9. Confirm no localhost reset URL is emitted.

## Historical journald security exception

The production review identified 15 historical reset-link journal entries containing raw query markers. The affected reset challenges are consumed and cannot be reused. No log forwarding or rotated ordinary-file copies were found. Production Gunicorn access logging was corrected to omit query strings, so future reset tokens are not logged. The operator explicitly accepted retaining the historical journal records because selective deletion is not safely available and vacuuming would destroy unrelated operational evidence. No journald data was deleted or vacuumed.

## Rollback

If the application release fails before migration, atomically restore the previous release and its compatible virtual environment. If schema 16 has already been applied, do not point schema-14 code at the migrated database without a reviewed compatibility decision. Prefer forward repair; if rollback is required, stop the service, restore the verified pre-migration database backup and any image sidecar, restore the prior release, then verify both health endpoints. Preserve the failed release and backup.

## Android

The next private beta should be `0.6.0`, version code `8`, reusing the existing release signing configuration. This is a recommendation only; no APK was rebuilt, published, or installed by this review.
