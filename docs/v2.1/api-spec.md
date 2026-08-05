# EPN Recipe Box 2.1 API Specification

## Contract

Base path: `/api/v1`

Media type: `application/json; charset=utf-8`

Protected endpoints use:

```http
Authorization: Bearer <individual-api-token>
```

The token is an opaque, server-generated value. It is not a Flask session cookie, peer synchronization token, PAT, Tailscale key, or administrator secret.

## Common error shape

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request could not be validated.",
    "details": {}
  }
}
```

`details` is optional and never contains secrets or internal stack traces.

## Endpoints

### `POST /api/v1/auth/login`

Unauthenticated. Request:

```json
{"email":"cook@example.com","password":"..."}
```

Success `200`:

```json
{
  "token": "opaque-token",
  "token_type": "Bearer",
  "expires_at": "2026-08-06T12:00:00+00:00",
  "user": {"id":"...","email":"cook@example.com","nickname":"Cook"}
}
```

Invalid credentials return the same `401 AUTHENTICATION_FAILED` shape for unknown user, wrong password, expired/revoked credential, and malformed login. Rate-limited login returns `429 RATE_LIMITED` with `Retry-After`.

### `POST /api/v1/auth/logout`

Protected. Revokes the presented token. Success `204` with an empty body. Repeating logout is safe and returns `204`.

### `GET /api/v1/me`

Protected. Returns the authenticated user's public profile fields and account identifier; never returns password hashes or token metadata beyond the current token expiry if needed by the client.

### `GET /api/v1/recipes`

Protected. Query parameters:

- `page` — one-based integer, default `1`, maximum `100000`.
- `page_size` — default `20`, maximum `50`.
- `q` — bounded search string, maximum 200 characters.
- `category` — normalized category key.
- `tag` — normalized tag key.

Success `200`:

```json
{
  "data": [{"id":"...","title":"...","summary":"...","ingredients":[],"steps":[],"category":"dinner","tags":[],"image":null,"created_at":"...","updated_at":"...","creator":{"id":"...","nickname":"..."}}],
  "pagination": {"page":1,"page_size":20,"total_items":1,"total_pages":1}
}
```

Archived recipes are excluded from the first mobile list contract, matching the normal web list behavior. Results are ordered by `updated_at DESC, id ASC`.

### `GET /api/v1/recipes/{recipe_id}`

Protected. Returns the same recipe representation. `404 NOT_FOUND` does not reveal whether another account owns an ID.

### `POST /api/v1/recipes`

Protected. Request fields:

```json
{"title":"...","summary":"...","ingredients":["..."],"steps":["..."],"category":"dinner","tags":["quick","family"]}
```

No image binary is accepted in this first slice. Success `201` returns the created recipe. Field limits reuse the existing domain validators. Invalid JSON, unknown fields where prohibited, missing fields, invalid category/tags, or oversize input return `422 VALIDATION_ERROR`.

### `GET /api/v1/categories`

Protected. Returns `{ "data": [{"key":"dinner","name":"Dinner"}] }`.

### `GET /api/v1/tags`

Protected. Returns `{ "data": [{"key":"quick","name":"Quick"}] }`, bounded to a safe maximum.

### `GET /api/v1/health`

Unauthenticated readiness/liveness response. It returns status, database status, schema version, and API version only. It never returns environment values, filesystem paths, hostnames, or secrets.

## Compatibility rules

- Existing `/`, `/signup`, `/recipes/*`, `/health`, and `/api/sync/*` routes remain available.
- Existing browser sessions and CSRF rules remain unchanged.
- API tokens use new tables and new auth middleware; peer synchronization continues to use the installation token hash.
- Stable recipe IDs are the existing database IDs.
- Timestamps are UTC ISO-8601 strings.
- No delete endpoint is introduced.

## OpenAPI

The executable OpenAPI document lives at `docs/v2.1/openapi.yaml` and is generated/maintained alongside this contract.
