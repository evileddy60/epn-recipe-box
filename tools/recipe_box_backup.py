from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from recipe_box.migrations import schema_version


def verify_database(path: Path) -> str:
    path = Path(path)
    with sqlite3.connect(path) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {result}")
    return result


def backup_database(source: Path, directory: Path | None = None) -> Path:
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(source)
    verify_database(source)
    directory = Path(directory) if directory else source.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{source.stem}-{stamp}.db"
    if target.exists():
        raise FileExistsError(target)
    with sqlite3.connect(source) as source_conn, sqlite3.connect(target) as target_conn:
        source_conn.backup(target_conn)
    verify_database(target)
    return target


def _row_counts(path: Path) -> dict[str, int]:
    tables = ("users", "recipes", "inventory_items", "ratings", "comments", "tags", "recipe_tags")
    with sqlite3.connect(path) as conn:
        return {
            table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]  # nosec B608 - table names come from a fixed internal tuple
            for table in tables
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        }


def restore_database(backup: Path, target: Path, force: bool = False) -> dict:
    backup = Path(backup)
    target = Path(target)
    if target.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing database: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    with sqlite3.connect(backup) as source_conn, sqlite3.connect(target) as target_conn:
        source_conn.backup(target_conn)
    verify_database(target)
    return {"schema_version": schema_version(target), "row_counts": _row_counts(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup and verify an EPN Recipe Box SQLite database")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("database", type=Path)
    backup.add_argument("--directory", type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("database", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("target", type=Path)
    restore.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "backup":
        print(backup_database(args.database, args.directory))
    elif args.command == "verify":
        print(verify_database(args.database))
    else:
        print(restore_database(args.backup, args.target, args.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
