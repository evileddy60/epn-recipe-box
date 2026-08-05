# EPN Recipe Box 2.1 Architecture Specification

## Status

Accepted for the first vertical slice. This document is the implementation boundary for the local Phase 2–9 work. It does not authorize deployment, publication, or changes to the existing web contract.

## Objective

Add a private, native Android client for invited EPN members while preserving the existing Flask web application, SQLite database, migrations, and peer synchronization. The Raspberry Pi remains the authoritative server. Android connects over a user-configured Tailscale hostname and uses individual user accounts with revocable opaque API tokens.

## Existing system baseline

- Flask 3.1.3, Gunicorn, Pillow, and SQLite.
- `create_app()` in `recipe_box/application.py`; web routes in `recipe_box/routes.py`.
- Schema migrations 1–6 in `recipe_box/migrations.py`.
- Users have email, password hash, profile fields, and timestamps.
- Recipes have stable string IDs, owner, title, summary, prep time, servings, ingredients, steps, timestamps, category, tags, image metadata, archive state, ratings, and comments.
- Favorites and archive are user-local. Recipe image binaries remain local to the server.
- Peer synchronization uses `/api/sync/*` and a separate installation bearer token. This token is never reused by Android.
- Existing browser authentication uses Flask's signed session cookie plus CSRF protection.

## Selected architecture

```text
Android Compose UI
        |
   ViewModels / state holders
        |
  Repositories (auth, recipes, server setup)
        |
 Retrofit + kotlinx.serialization
        |
 Versioned Flask JSON API (/api/v1)
        |
 API token middleware -> SQLite repositories/services
        |
 Existing web routes and peer sync remain separate contracts
```

### Server boundary

The API is added as a separate blueprint/module. It must not call browser route handlers, depend on Flask session cookies, or accept peer sync tokens. It may reuse validated domain/database helpers, but response serialization and authorization remain API-specific.

### Android boundary

The first client uses Kotlin, Jetpack Compose, Material 3, Retrofit, kotlinx.serialization, Room, and Android Keystore-backed encrypted token storage. WorkManager is intentionally omitted: the first slice has no required background synchronization job.

### Local cache policy

Room caches the last successfully fetched authenticated recipe list/detail data and non-secret server metadata. Cache records are scoped by server URL and account identifier. Logout deletes the account's cached private data and token. Offline mode is read-only in this slice; recipe creation requires a live server response.

## Explicit non-goals

- No WebView.
- No public Internet exposure.
- No automatic Tailscale ACL, firewall, router, or systemd changes.
- No peer-token reuse or administrator credential embedding.
- No deletion endpoint, image upload, background sync, sharing, or Android push notifications in this slice.
- No migration rewrite or database replacement.

## Success criteria

1. Existing web and peer-sync tests continue to pass.
2. API endpoints are versioned, documented in OpenAPI, and return one structured JSON error shape.
3. Login returns a one-time opaque token whose server-side hash is stored and whose expiry/revocation are enforced.
4. Authenticated Android users can list, search/filter, view, and create recipes.
5. Server responses contain image metadata and safe API-relative image URLs, never filesystem paths or secrets.
6. Android stores tokens only through a Keystore-backed abstraction and never logs them.
7. Isolated server tests demonstrate login, expiry, revocation, authorization, pagination, filtering, validation, and compatibility with existing routes/sync.
8. Android JVM/unit tests cover models, URL validation, token storage abstraction, repository mapping, and ViewModel states when the toolchain is available.

## Sources

- Flask application factories: https://flask.palletsprojects.com/en/stable/patterns/appfactories/
- Flask blueprints: https://flask.palletsprojects.com/en/stable/blueprints/
- Android Compose documentation: https://developer.android.com/develop/ui/compose/documentation
- Android app architecture: https://developer.android.com/topic/architecture
- Room: https://developer.android.com/training/data-storage/room
- Android Keystore: https://developer.android.com/privacy-and-security/keystore
- Android network security configuration: https://developer.android.com/privacy-and-security/security-config
