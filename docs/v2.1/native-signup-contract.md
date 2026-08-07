# Native account creation contract

## Existing browser contract (verified before implementation)

`POST /signup` accepts `email`, `password`, and `mode=signup` in a CSRF-protected browser form. Email is trimmed/lowercased and must match the existing email pattern and `MAX_EMAIL_LENGTH`; passwords must be 8–128 characters and are hashed with Werkzeug's existing password hasher. Duplicate email is handled as a user-facing signup error. A successful browser signup creates the user with an empty nickname, regenerates the session, and redirects to `/profile/setup`. The profile step requires a nickname (1–80 characters after whitespace normalization), accepts optional bio (bounded by `MAX_BIO_LENGTH`) and avatar upload, and then redirects to the recipe index. Browser CSRF enforcement remains unchanged.

API login uses opaque random bearer tokens stored only as hashes in `api_tokens`, with the configured expiry. Login rate limiting is the existing in-memory five-failure window keyed by a normalized identifier; failures log only a short identifier hash.

## Native API contract

`POST /api/v1/auth/signup` is bearerless JSON and is exempt from browser CSRF because it is under `/api/v1/`. The request is exactly:

```json
{"email":"cook@example.com","password":"...","nickname":"Cook"}
```

The endpoint reuses the existing email/password validation, hashing, account database write, and nickname validation. Nickname is created in the same user write, so the native flow does not require a second profile mutation endpoint. Bio and avatar remain optional web-only profile fields; native signup does not pretend to support avatar upload.

Success returns HTTP 201 with the same token shape as login (`token`, `token_type`, `expires_at`, `user`). The token is stored hashed server-side. Validation returns structured 422 errors, duplicate email returns structured 409 `ACCOUNT_EXISTS`, and rate limiting returns structured 429 with `Retry-After`. Passwords, password hashes, stack traces, filesystem paths, and internal exception details are never returned or logged.

## Android behavior

Create Account is a native Compose screen. It collects only the server-supported fields, performs matching client-side validation, calls the endpoint, stores the returned token through the existing secure token store, calls the authenticated app flow, and never launches a browser.
