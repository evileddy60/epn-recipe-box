# Specification: Recipe Images, Import/Export, Favorites, and Archive

## Scope

Add one primary image per recipe, a versioned JSON exchange format, user-local favorites, and non-destructive local archive state without weakening the existing security, migration, backup, search, category/tag, or peer-synchronization contracts.

## Data contracts

### Recipe image

- Store recipe image binaries below `EPN_DATA_DIR/recipe-images/` only.
- Store generated filename, media type, width, height, byte size, and SHA-256 metadata on the recipe row.
- Never store or render a client-supplied path.
- Validate actual content with Pillow, enforce the existing request limit plus a 4 MiB image limit, a 4096-pixel dimension limit, and a 16-million-pixel decompression-bomb limit.
- Normalize accepted images to an optimized JPEG display asset no larger than 1600x1600 while preserving aspect ratio.
- Replacing an image removes only the previous generated file after the new image is successfully stored.
- Existing recipes with no image render a CSS fallback and accessible alt text.

### Exchange format v1

```json
{
  "format": "epn-recipe-box.recipe-exchange",
  "version": 1,
  "source_installation_id": "box-...",
  "exported_at": "2026-01-01T00:00:00+00:00",
  "recipes": [
    {
      "id": "recipe-...",
      "title": "...",
      "summary": "...",
      "prep_time": "...",
      "servings": "...",
      "ingredients": ["..."],
      "steps": ["..."],
      "category": "dinner",
      "tags": [{"normalized_name": "quick", "display_name": "Quick"}],
      "created_at": "...",
      "updated_at": "...",
      "image": {"available": true, "media_type": "image/jpeg", "width": 1200, "height": 800, "size": 12345, "sha256": "..."}
    }
  ]
}
```

The envelope is deterministic in field names and sorted recipe/tag ordering. It never includes passwords, tokens, sessions, peer secrets, comments, local paths, or image bytes. Remote image URLs and arbitrary file references are invalid input.

### Import result

Preview and apply responses report integer counts for `new`, `updated`, `unchanged`, `conflicts`, and `rejected`, plus per-item statuses and safe messages. A divergent duplicate ID is a conflict. A matching checksum is unchanged. An update is accepted only when the source installation matches and the incoming timestamp is newer. Keep-both creates a new server-generated recipe ID and retains the local recipe.

## Favorites

Favorites use a composite `(user_id, recipe_id)` primary key. Favorite/unfavorite operations are idempotent. Favorites are visible to the current user only and are excluded from synchronization and exports.

## Archive

Archive is a local `archived_at` timestamp owned by the recipe owner. Normal lists and search exclude archived recipes; an explicit Archived view includes them. Archive and restore never delete rows, images, ratings, comments, tags, or sync records. Archive state is deliberately local-only and excluded from synchronization to preserve the no-deletion protocol.

## UI

Use the existing server-rendered Flask templates and recipe-card visual language: paper cards, warm palette, serif headings, rounded controls, semantic labels, keyboard-reachable native controls, visible focus states, and mobile-first grids. Add fallback/image presentation, favorite controls/filter, archive/restore controls, export links, and import preview/apply/conflict states without a frontend framework.

## Non-goals

- No remote image fetching.
- No image binary synchronization.
- No physical recipe deletion.
- No favorite synchronization.
- No archive synchronization.
- No new frontend framework or external service.
