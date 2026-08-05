# Specification: Recipe Search, Categories, and Tags

## Objective

Add server-side recipe search, one extensible category per recipe, and normalized user-managed tags while preserving the existing server-rendered Flask architecture, visual identity, recipe behavior, and manual peer synchronization protocol.

## Scope

- Search by title, summary, ingredients, category, tags, and recipe creator name.
- Case-insensitive, whitespace-normalized search with `/?q=<query>` deep links.
- Category selection and filtering using stable extensible category keys.
- Multiple normalized tags per recipe stored relationally in `tags` and `recipe_tags`.
- Combined query/category/tag filtering without loading every recipe into Python merely to filter.
- Synchronization payloads and checksums include category and tags, with safe defaults for older peers.
- Server-rendered controls remain keyboard accessible and responsive.

## Contracts and limits

- Search query: trim and collapse whitespace; maximum 120 characters; empty means no text filter.
- Category key: lowercase ASCII slug, 1–40 characters, or empty for Uncategorized. Existing recipes migrate to empty.
- Tag display name: trimmed, whitespace-collapsed, 1–40 characters; maximum 12 tags per recipe and 400 characters of submitted tag data.
- Tag normalization: lowercase, whitespace-collapsed, with repeated internal separators normalized to `-`; uniqueness is case-insensitive and normalization-based.
- Unknown remote category/tag values are validated against the same limits before local upsert; malformed values reject the payload without corrupting local data.
- SQL remains parameterized and HTML is rendered through Jinja escaping.

## Data model

- Add nullable/empty `recipes.category_key` for backward-compatible migration.
- Add `categories(category_key PRIMARY KEY, display_name NOT NULL)` with seeded defaults: breakfast, lunch, dinner, dessert, snack, soup, salad, beverage, baking, other.
- Add `tags(id PRIMARY KEY, normalized_name UNIQUE, display_name NOT NULL)`.
- Add `recipe_tags(recipe_id, tag_id, PRIMARY KEY(recipe_id, tag_id))` with cascading foreign keys.
- Existing recipes remain valid and display Uncategorized when `category_key` is empty; no categories are guessed.
- Add indexes for `recipes.category_key`, `recipes.owner_id`, `tags.normalized_name`, and `recipe_tags.tag_id`.

## Search behavior

Use a SQL query that joins the owner/category tables and uses an `EXISTS` tag predicate. Search text is bound as a parameter against normalized lower-case title, summary, ingredients JSON, category label/key, tag names, and creator name. Category and one-tag filters are additional bound predicates. Results retain existing rating/comment decoration after the filtered recipe rows are selected.

## Synchronization behavior

- Canonical recipe payloads add `category` and sorted canonical `tags`.
- Missing category becomes empty and missing/non-list tags become an empty list for compatibility with older peers.
- Tag ordering is sorted by normalized name before checksums and payload comparison.
- New/update/conflict/keep-local/use-remote/keep-both paths persist category and tags.
- No deletion propagation is introduced.
- Protocol compatibility is additive: older peers omit the new fields; newer peers safely default them.

## UI

The main Cards page gains a GET filter form with:

- Search field preserving `q`.
- Category select.
- Tag select.
- Clear Filters action.
- Active-filter summary and accessible no-results state.
- Category badge and tag chips on cards and detail pages.

Recipe create/edit forms gain category selection and a tag input. Validation errors are flashed clearly. Controls use existing semantic HTML, focus styles, spacing, colors, and responsive layout; no frontend framework or unnecessary JavaScript is added.

## Acceptance criteria

1. Existing recipes and legacy databases open without data loss.
2. Search and combined filters return correct results through direct URLs.
3. Tags normalize, deduplicate, enforce limits, and remain relational.
4. Category and tags appear on cards/detail pages and round-trip through create/edit.
5. Checksums are stable regardless of tag order and change when category/tags change.
6. Older synchronization payloads remain readable.
7. Synchronization conflict resolutions preserve category and tags.
8. Existing tests plus focused feature, migration, authorization, and browser checks pass.
9. No production data, Hermes skills/configuration, or unrelated services are modified.
