# Pre-refactor baseline

Captured before modularization from commit `a9b4ea08cfd40adc6458c0b70b694f224f33adb9`.

## Structure

- `app.py`: 1,988-line Flask monolith containing configuration, SQLite persistence/migrations, domain logic, embedded Jinja templates, inline CSS/JavaScript, routes, and synchronization workflows.
- `sync.py`: pure synchronization contracts and merge helpers.
- `tests/test_sync.py`: five synchronization tests.
- `static/recipe-box.png`: existing visual asset.

## Environment variables

- `EPN_DATA_DIR` — data directory; defaults to `data/` beside the entrypoint.
- `SECRET_KEY` — Flask session secret; defaults to the development placeholder.
- `SYNC_TIMEOUT_SECONDS` — outbound peer timeout; defaults to `5`.
- `SYNC_TOKEN` — installation token; otherwise generated/persisted under `.sync-token`.
- `PORT` — development entrypoint port; defaults to `5000`.

## Routes

| Methods | Path | Endpoint |
|---|---|---|
| GET | `/uploads/<path:filename>` | `uploaded_file` |
| GET | `/` | `index` |
| GET, POST | `/signup` | `signup` |
| POST | `/logout` | `logout` |
| GET, POST | `/profiles` | `profile_setup` |
| GET, POST | `/profile/setup` | `profile_setup` |
| GET, POST | `/recipes/new` | `new_recipe` |
| GET | `/recipes/<recipe_id>` | `recipe_detail` |
| GET, POST | `/recipes/<recipe_id>/edit` | `edit_recipe` |
| POST | `/recipes/<recipe_id>/rate` | `rate_recipe` |
| POST | `/recipes/<recipe_id>/comment` | `comment_recipe` |
| GET, POST | `/inventory` | `inventory` |
| POST | `/inventory/generated/save` | `save_generated` |
| GET | `/api/sync/manifest` | `sync_manifest` |
| GET | `/api/sync/recipes/<recipe_id>` | `sync_recipe` |
| GET | `/sync` | `sync_dashboard` |
| POST | `/sync/peers` | `add_sync_peer` |
| POST | `/sync/peers/<peer_id>/toggle` | `toggle_sync_peer` |
| POST | `/sync/peers/<peer_id>/delete` | `delete_sync_peer` |
| POST | `/api/sync/preview` | `api_sync_preview` |
| POST | `/sync/peers/<peer_id>/preview` | `preview_sync_peer` |
| POST | `/api/sync/run` | `api_sync_run` |
| POST | `/sync/peers/<peer_id>/run` | `run_sync_peer` |
| POST | `/api/sync/conflicts/<conflict_id>/resolve` | `api_resolve_sync_conflict` |
| POST | `/sync/conflicts/<conflict_id>/resolve` | `resolve_sync_conflict` |

## SQLite schema and migration behavior

Tables: `users`, `inventory_items`, `recipes`, `ratings`, `comments`, `installations`, `sync_peers`, `sync_baselines`, `sync_conflicts`, and `sync_history`.

Startup is lazy through `load_data()`/sync functions. `init_db()` creates missing tables, checks legacy `users` and `recipes` columns, adds missing nullable/defaulted columns (`email`, `password_hash`, `nickname`, `sync_source_installation_id`), and copies an existing database to `recipe_box.db.pre-sync-<UTC timestamp>.bak` before required schema alteration. Existing rows are preserved.

## Public function compatibility

The refactor retains the root `app.py` re-export surface for existing imports while moving implementations into `recipe_box.db`, `recipe_box.domain`, and `recipe_box.sync_service`.

## Baseline validation

- `python3 -m py_compile app.py sync.py tests/test_sync.py`: passed.
- `python3 -m unittest discover -s tests -v`: 5 tests passed.
- Two-instance synchronization demonstration: passed (new import, one-sided update, conflict, `keep_both`, idempotent repeat).
- `git diff --check`: passed.
- Ports `5000`, `5101`, `5102`: closed after validation.
