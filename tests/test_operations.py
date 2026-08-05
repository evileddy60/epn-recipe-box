import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from recipe_box import migrations
from tools.recipe_box_backup import backup_database, restore_database, verify_database


class MigrationTests(unittest.TestCase):
    def make_db(self, schema="legacy"):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "recipe_box.db"
        with sqlite3.connect(path) as conn:
            conn.executescript(migrations.HISTORICAL_SCHEMAS[schema])
        return temp, path

    def test_migration_from_all_supported_historical_schemas_is_idempotent(self):
        for schema in ("legacy", "sync", "categories_tags"):
            with self.subTest(schema=schema):
                temp, path = self.make_db(schema)
                try:
                    migrations.migrate_database(path)
                    first_version = migrations.schema_version(path)
                    migrations.migrate_database(path)
                    self.assertEqual(migrations.schema_version(path), first_version)
                    with sqlite3.connect(path) as conn:
                        self.assertGreater(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
                        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                finally:
                    temp.cleanup()

    def test_failed_migration_rolls_back_and_does_not_record_version(self):
        temp, path = self.make_db("legacy")
        try:
            with self.assertRaises(RuntimeError):
                migrations.migrate_database(path, migrations_override=[(1, lambda conn: (_ for _ in ()).throw(RuntimeError("boom")))])
            self.assertEqual(migrations.schema_version(path), 0)
        finally:
            temp.cleanup()


class BackupTests(unittest.TestCase):
    def test_backup_verify_restore_and_overwrite_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "restored.db"
            migrations.migrate_database(source)
            with sqlite3.connect(source) as conn:
                conn.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES ('u1', 'backup@example.com', 'hash', 'now')")
            backup = backup_database(source, root / "backups")
            self.assertEqual(verify_database(backup), "ok")
            result = restore_database(backup, target)
            self.assertEqual(result["schema_version"], migrations.schema_version(source))
            self.assertEqual(result["row_counts"]["users"], 1)
            with self.assertRaises(FileExistsError):
                restore_database(backup, target)


if __name__ == "__main__":
    unittest.main()
