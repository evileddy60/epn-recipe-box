# ADR-0005: Local Recipe Assets and Versioned Exchange

## Status

Accepted

## Date

2026-08-05

## Context

Recipe Box needs primary recipe images, portable recipe exchange, favorites, and archive behavior. The application is a small server-rendered Flask/SQLite deployment with existing security controls and a peer synchronization protocol that intentionally does not propagate deletions or arbitrary local files.

## Decision

- Store normalized recipe display images under the server-owned `EPN_DATA_DIR/recipe-images` directory and persist only generated filenames and safe metadata.
- Keep recipe images, favorites, and archive timestamps out of the peer synchronization payload. Synchronization remains metadata-only for recipes and cannot expose local filesystem paths or fetch remote image URLs.
- Use a versioned `epn-recipe-box.recipe-exchange` JSON envelope for one or many recipes. Validate the complete envelope and enforce count/size limits before applying it.
- Treat divergent duplicate IDs as conflicts. Permit explicit keep-both import by assigning a new server-generated ID; never silently overwrite a divergent local recipe.
- Keep favorites user-local with a composite primary key. Keep archive local-only and non-destructive so the existing no-deletion synchronization contract remains unchanged.

## Alternatives considered

### Synchronize image binaries

Rejected: it would enlarge the peer protocol, complicate authentication and size limits, and risk exposing local paths or consuming significant Pi storage. Metadata-only exchange is sufficient for this version.

### Store uploads in the web/static tree

Rejected: application data should not be mixed with deployable assets, and the server-owned data directory provides clearer backup and path-boundary guarantees.

### Hard-delete archived recipes

Rejected: it would violate the requested restore/export behavior and the existing synchronization no-deletion model.

### Synchronize favorites or archive state

Rejected: favorites are inherently user-specific, while archive propagation could hide data on another installation. Both remain explicitly local-only.

## Consequences

- Backups include recipe image files because they live below the configured data directory; restore must preserve that directory alongside SQLite.
- Existing recipes migrate with empty image metadata and unarchived state.
- Older peers continue receiving the existing recipe payload shape and ignore the new local-only features.
- Users must transfer image binaries separately if they exchange JSON with another installation; the exchange format records image metadata but intentionally does not embed or fetch image data.
