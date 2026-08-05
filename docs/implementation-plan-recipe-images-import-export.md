# Implementation Plan: Recipe Images, Import/Export, Favorites, and Archive

1. **Characterization and contracts**
   - Add focused tests for image validation/storage, exchange envelopes and import classification, favorites, archive authorization, and migration compatibility.
   - Preserve existing security test fixtures and synchronization payload assertions.

2. **Migration and persistence**
   - Add ordered migrations after schema version 4 for recipe image metadata, archive timestamp, and favorites.
   - Extend row conversion and filtered queries without changing legacy defaults.
   - Keep archive/favorites/image metadata out of peer payloads.

3. **Image pipeline**
   - Generalize the existing Pillow validation boundary for recipe images.
   - Normalize images to bounded display assets under the server-owned recipe-image directory.
   - Add safe serving, replacement cleanup, fallback markup, alt text, and form preview support.

4. **Exchange service and routes**
   - Add a pure exchange serializer/validator and an authenticated preview/apply route pair.
   - Add one-recipe and all-owned export responses.
   - Enforce envelope, item-count, field, and total-payload limits before persistence.
   - Add explicit conflict/keep-both behavior and session-backed HTML preview state.

5. **Favorites and archive UI**
   - Add owner archive/restore actions, user-local favorite toggles, favorites/archived filters, and status summaries.
   - Keep every state-changing browser route under the existing CSRF protection.

6. **Compatibility and operations**
   - Update README and migration/backup documentation.
   - Add an ADR documenting local-only archive/favorites and metadata-only image synchronization.
   - Extend backup compatibility tests and the two-instance demonstration assertions.

7. **Validation**
   - Run focused RED/GREEN tests during implementation, then the complete suite, compilation, Ruff, Bandit, pip-audit, migration/backup tests, sync demonstration, Gunicorn, browser checks, cleanup, and final Git verification.
