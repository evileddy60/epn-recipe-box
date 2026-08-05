from __future__ import annotations

import argparse
import hashlib
import json
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


def _image_asset_directory(database: Path) -> Path:
    return database.parent / "recipe-images"


def _asset_manifest(assets: Path) -> list[dict[str, object]]:
    manifest = []
    for path in sorted(item for item in assets.rglob("*") if item.is_file()):
        relative = path.relative_to(assets).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.append({"path": relative, "size": path.stat().st_size, "sha256": digest})
    return manifest


def _backup_asset_directory(source: Path, target: Path) -> None:
    assets = _image_asset_directory(source)
    if assets.exists():
        sidecar = target.parent / f"{target.stem}.recipe-images"
        shutil.copytree(assets, sidecar)
        (sidecar / "manifest.json").write_text(json.dumps(_asset_manifest(assets), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _verify_asset_sidecar(sidecar: Path) -> None:
    manifest_path = sidecar / "manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Recipe image sidecar manifest is unreadable.") from exc
    if not isinstance(manifest, list):
        raise RuntimeError("Recipe image sidecar manifest is invalid.")
    for item in manifest:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RuntimeError("Recipe image sidecar manifest is invalid.")
        path = (sidecar / item["path"]).resolve()
        if path.parent != sidecar.resolve() or not path.is_file():
            raise RuntimeError(f"Recipe image sidecar is missing: {item['path']}")
        if path.stat().st_size != item.get("size") or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
            raise RuntimeError(f"Recipe image sidecar failed integrity verification: {item['path']}")


def _missing_recipe_sidecars(database: Path, assets: Path) -> list[str]:
    with sqlite3.connect(database) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(recipes)")}
        if "image_filename" not in columns:
            return []
        names = [row[0] for row in conn.execute("SELECT image_filename FROM recipes WHERE image_filename <> ''")]
    return sorted(name for name in names if Path(name).name != name or not (assets / name).is_file())


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
    _backup_asset_directory(source, target)
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
    backed_up_assets = backup.parent / f"{backup.stem}.recipe-images"
    target_assets = _image_asset_directory(target)
    if backed_up_assets.exists():
        _verify_asset_sidecar(backed_up_assets)
        if target_assets.exists() and force:
            shutil.rmtree(target_assets)
        if not target_assets.exists():
            shutil.copytree(backed_up_assets, target_assets)
    return {
        "schema_version": schema_version(target),
        "row_counts": _row_counts(target),
        "missing_image_sidecars": _missing_recipe_sidecars(target, target_assets),
    }


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
