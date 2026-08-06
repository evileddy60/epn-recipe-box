# Migrations and backup operations

## Migration runner

`recipe_box.migrations.migrate_database()` applies numbered migrations in order and records each successful version in `schema_version`. SQLite DDL is transactional in the supported operations, and the runner rolls back the current migration on failure. A failed version is not recorded.

Before applying pending migrations, the runner creates:

```text
recipe_box.db.pre-sync-YYYYMMDDTHHMMSSZ.bak
```

The original legacy schema (including `users.name`), synchronization schema, and categories/tags schema are covered by tests. Existing rows and relationships are retained; missing category values remain uncategorized and missing tags remain empty.

The current schema version adds recipe image metadata and local archive state, followed by the user-local favorites table and explicit comment moderation audit fields. Existing recipes and comments receive empty image metadata, null archive timestamps, and null moderation fields; no row is deleted. Recipe-owner hiding is recorded separately from author deletion.

Startup calls the runner on every initialization. If no pending version exists, no migration is repeated.
## Backup tool

Use a stopped application or a quiescent database for scheduled backups:

```bash
python tools/recipe_box_backup.py backup /srv/epn-recipe-box/data/recipe_box.db --directory /srv/epn-recipe-box/backups
python tools/recipe_box_backup.py verify /srv/epn-recipe-box/backups/recipe_box-20260805T120000Z.db
python tools/recipe_box_backup.py restore /srv/epn-recipe-box/backups/recipe_box-20260805T120000Z.db /tmp/recipe-box-restore.db
```

The backup command uses SQLite's online backup API and verifies `PRAGMA integrity_check` before and after copying. When `data/recipe-images/` exists beside the source database, it is copied to a `<backup-stem>.recipe-images` sidecar with a SHA-256 manifest. Restore verifies that manifest, recreates the image directory beside the target database, and reports referenced image filenames that are missing from the restored sidecar. Restore reports schema version and counts for users, recipes, inventory, ratings, comments, tags, and recipe/tag relationships. It refuses to overwrite an existing target unless `--force` is explicit.

Never point restore tests or experiments at production data. Test targets should live under a temporary directory.
