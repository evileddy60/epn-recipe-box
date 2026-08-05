# Implementation plan: Search, categories, and tags

## Milestone 1 — contracts and tests

- Extend pure synchronization canonicalization/validation for category and tags with compatibility defaults.
- Add failing unit tests for normalization, limits, checksum ordering, and old payloads.
- Add failing application tests for migration, create/edit, search/filtering, authorization, and no-results behavior.

## Milestone 2 — additive persistence

- Add `category_key` to `recipes` with an empty default.
- Add categories, tags, and recipe_tags tables plus indexes and seeded category labels.
- Add normalized tag parsing and relational replacement helpers.
- Preserve the existing backup-before-migration behavior and verify copied legacy databases.

## Milestone 3 — application and synchronization

- Add SQL-backed filtered recipe loading and owner/category/tag decoration.
- Update recipe create/edit and generated-recipe paths with category/tag validation.
- Extend sync payload, validation, merge checksums, import, and conflict resolution.

## Milestone 4 — interface

- Add the filter form and state summary to the Cards page.
- Add category select and tag input to recipe forms.
- Display category badges and tag chips on cards and detail pages.
- Preserve existing visual tokens, focus states, responsive behavior, and escaped rendering.

## Milestone 5 — validation and delivery

- Run compile checks, focused tests, complete regression suite, and isolated two-instance synchronization.
- Run Gunicorn browser validation with desktop and narrow viewports if supported.
- Verify SQL/auth/security boundaries, no production-data writes, no unexpected listeners, and clean Git state.
- Commit only after all validation passes as `feat: add recipe search categories and tags`.

## Expected files

- `sync.py`
- `recipe_box/db.py`
- `recipe_box/domain.py`
- `recipe_box/routes.py`
- `recipe_box/sync_service.py`
- `recipe_box/templates/index.html`
- `recipe_box/templates/recipe_form.html`
- `recipe_box/templates/detail.html`
- `static/style.css` if required for existing design tokens
- `tests/test_sync.py`
- `tests/test_application.py`
- `docs/SPEC-search-categories-tags.md`
- `docs/implementation-plan-search-categories-tags.md`
- `docs/adr/0003-search-categories-tags-sync.md`

## Migration and rollback

- All tests use temporary `EPN_DATA_DIR` directories.
- Existing databases receive a timestamped backup before schema alteration.
- Migration is additive and leaves existing recipe rows with an empty category and no tags.
- Code rollback is a branch checkout; schema rollback uses the pre-migration SQLite backup after stopping the application.
- No production data is used in tests or demonstrations.

## Non-goals

- No external search service, frontend framework, automatic categorization, deletion propagation, multi-tag filtering, or redesign of the existing visual language.
