# EPN Recipe Box 2.1 Android Design Specification

## Product

Application name: **EPN Recipe Box**

Package: `ca.evilpeoplenetwork.recipebox`

Distribution: private invited-member distribution; no public store release in this phase.

## Minimum platform

Choose `minSdk 26` (Android 8.0) to provide a practical modern baseline for invited private devices while retaining current Keystore, TLS, Room, and Compose support. The final Gradle catalog records compile/target versions explicitly. Release builds require no production signing key in the repository.

## Navigation and screens

1. **Server setup** — URL entry, validation through `/api/v1/health`, explicit HTTPS/private-tailnet status, and no silent insecure public HTTP.
2. **Login** — email/password, generic errors, loading state, and no credential logging.
3. **Recipe list** — paginated data, search, category/tag filters, refresh, loading, empty, offline-cache, and error states.
4. **Recipe detail** — title, summary, ingredients, steps, category, tags, image metadata/image URL, creator, and timestamps.
5. **Create recipe** — validated title, summary, ingredients, steps, category, tags; server validation errors; no image upload in this slice.

## UI system

Use Material 3 with a warm EPN recipe-card palette: parchment/light surfaces, deep ink text, herb/terracotta accents, and a dark theme with equivalent contrast. Cards should feel like recipe cards rather than an admin dashboard.

- Minimum interactive touch target: 48dp.
- Visible focus/pressed/selected states.
- Accessible content descriptions for images and controls.
- Strong contrast in both themes.
- No decorative animation required for routine navigation; use only restrained state transitions.
- Offline and errors are first-class content states, not toasts alone.

## Architecture

```text
Compose screens -> ViewModels -> repositories -> Retrofit API
                                      |-> Room cache
                                      |-> Keystore-backed token store
```

Single-activity Compose app. State is exposed as immutable UI state from ViewModels. Repositories own mapping between wire DTOs and domain/cache models. Room stores only scoped non-secret cache data. The token store stores the token ciphertext/key material through Android Keystore and exposes no raw persistence details to UI code.

## Network and privacy

- Retrofit + kotlinx.serialization.
- HTTPS required for configured server URLs in release builds.
- Android Network Security Configuration disables cleartext by default.
- Debug-only localhost HTTP may be enabled through a build-type-specific resource/configuration and cannot be enabled in release.
- Connect/read/write timeouts are bounded.
- No Authorization header appears in logs.
- `FLAG_SECURE` is applied to login and account-sensitive surfaces where practical.
- Logout clears token, Room private cache, and in-memory session state.

## Offline behavior

The list and detail screens display the last successful cache when the server is unavailable, with an explicit offline indicator and stale-data timestamp. Creation is disabled offline because this slice has no durable write queue. No WorkManager job is added until background synchronization is a real requirement.

## Compose testing

JVM tests cover URL validation, DTO mapping, repository state transitions, and token-store behavior through a fake abstraction. Instrumented Compose tests cover login, list, detail, create, logout, loading, error, and offline states when an Android emulator/toolchain is available.

## Sources

- Compose: https://developer.android.com/develop/ui/compose/documentation
- Android architecture: https://developer.android.com/topic/architecture
- Room: https://developer.android.com/training/data-storage/room
- Keystore: https://developer.android.com/privacy-and-security/keystore
- Network security configuration: https://developer.android.com/privacy-and-security/security-config
- Material 3 Compose: https://developer.android.com/develop/ui/compose/designsystems/material3
