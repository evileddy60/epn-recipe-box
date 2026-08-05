from __future__ import annotations

import json
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from flask import current_app, jsonify, request

from sync import MAX_SYNC_BODY_BYTES, canonical_recipe, classify_merge, make_keep_both_copy, recipe_checksum, token_matches, validate_recipe_payload
from .config import *
from .db import *

def sync_installation_id() -> str:
    init_db()
    with db_connect() as conn:
        return conn.execute("SELECT id FROM installations LIMIT 1").fetchone()["id"]


def sync_json_error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def local_session_user():
    data = load_data()
    return current_user(data)


def require_local_json_auth():
    if not local_session_user():
        return sync_json_error("AUTHENTICATION_REQUIRED", "Sign in before using synchronization.", 401)
    return None


def require_peer_auth():
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    init_db()
    with db_connect() as conn:
        row = conn.execute("SELECT token_hash FROM installations LIMIT 1").fetchone()
    if not row or not token_matches(token, row["token_hash"]):
        return sync_json_error("AUTHENTICATION_FAILED", "A valid sync token is required.", 401)
    return None


def sync_recipe_payload(row: sqlite3.Row | dict) -> dict:
    recipe = {
        "id": row["id"], "title": row["title"], "summary": row["summary"],
        "prep_time": row["prep_time"], "servings": row["servings"],
        "ingredients": json.loads(row["ingredients_json"]), "steps": json.loads(row["steps_json"]),
        "category": row["category_key"] if "category_key" in row.keys() else "",
        "tags": [],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    with db_connect() as tag_conn:
        recipe["tags"] = recipe_tag_payload(tag_conn, row["id"])
    recipe = validate_recipe_payload(recipe)
    recipe["checksum"] = recipe_checksum(recipe)
    return recipe


def valid_peer_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username and not parsed.password and not parsed.fragment


def get_sync_peer(peer_id: str):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM sync_peers WHERE id = ?", (peer_id,)).fetchone()


def fetch_peer_manifest(peer: sqlite3.Row) -> dict:
    since = f"?{urlencode({'since': peer['last_sync_at']})}" if peer["last_sync_at"] else ""
    endpoint = urljoin(peer["url"].rstrip("/") + "/", "api/sync/manifest") + since
    if not valid_peer_url(endpoint):
        raise ValueError("Peer URL must be an HTTP or HTTPS URL without credentials.")
    request_obj = Request(endpoint, headers={"Authorization": f"Bearer {peer['token']}", "Accept": "application/json"})
    try:
        with urlopen(request_obj, timeout=current_app.config["SYNC_TIMEOUT_SECONDS"]) as response:
            body = response.read(MAX_SYNC_BODY_BYTES + 1)
    except HTTPError as exc:
        raise RuntimeError(f"Peer returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Peer did not respond before the timeout.") from exc
    if len(body) > MAX_SYNC_BODY_BYTES:
        raise RuntimeError("Peer response exceeded the synchronization size limit.")
    try:
        manifest = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Peer returned malformed JSON.") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("recipes"), list) or len(manifest["recipes"]) > 500:
        raise RuntimeError("Peer returned an invalid recipe manifest.")
    manifest["recipes"] = [validate_recipe_payload(item) for item in manifest["recipes"]]
    return manifest


def preview_peer_changes(peer_id: str) -> dict:
    peer = get_sync_peer(peer_id)
    if not peer or not peer["enabled"]:
        raise ValueError("That peer is missing or disabled.")
    manifest = fetch_peer_manifest(peer)
    items = []
    with db_connect() as conn:
        for remote in manifest["recipes"]:
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (remote["id"],)).fetchone()
            local = sync_recipe_payload(row) if row else None
            baseline = conn.execute("SELECT checksum FROM sync_baselines WHERE peer_id = ? AND recipe_id = ?", (peer_id, remote["id"])).fetchone()
            status = classify_merge(local, remote, baseline["checksum"] if baseline else None)
            items.append({"status": status, "recipe": remote, "local": local})
    counts = {name: sum(item["status"] == name for item in items) for name in ("new", "update", "unchanged", "conflict")}
    counts["failure"] = 0
    return {"peer_id": peer_id, "peer_name": peer["name"], "source_installation_id": manifest.get("installation_id", "unknown"), "items": items, "counts": counts}


def run_peer_sync(peer_id: str) -> dict:
    preview = preview_peer_changes(peer_id)
    peer = get_sync_peer(peer_id)
    started = now_iso(); history_id = f"sync-{uuid.uuid4().hex}"
    imported = 0; conflicts = 0
    try:
        with db_connect() as conn:
            owner = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
            if not owner: raise ValueError("Create a local account before importing recipes.")
            for item in preview["items"]:
                remote = item["recipe"]; status = item["status"]
                if status == "new":
                    conn.execute("INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, category_key, created_at, updated_at, sync_source_installation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (remote["id"], owner["id"], remote["title"], remote["summary"], remote["prep_time"], remote["servings"], json.dumps(remote["ingredients"]), json.dumps(remote["steps"]), remote.get("category", ""), remote["created_at"], remote["updated_at"], preview["source_installation_id"])); replace_recipe_tags(conn, remote["id"], remote.get("tags", [])); imported += 1
                elif status == "update":
                    conn.execute("UPDATE recipes SET title=?, summary=?, prep_time=?, servings=?, ingredients_json=?, steps_json=?, category_key=?, updated_at=?, sync_source_installation_id=? WHERE id=?", (remote["title"], remote["summary"], remote["prep_time"], remote["servings"], json.dumps(remote["ingredients"]), json.dumps(remote["steps"]), remote.get("category", ""), remote["updated_at"], preview["source_installation_id"], remote["id"])); replace_recipe_tags(conn, remote["id"], remote.get("tags", [])); imported += 1
                elif status == "conflict":
                    existing_conflict = conn.execute("SELECT id FROM sync_conflicts WHERE peer_id = ? AND recipe_id = ? AND status = 'open'", (peer_id, remote["id"])).fetchone()
                    if not existing_conflict:
                        conn.execute("INSERT INTO sync_conflicts (id, peer_id, recipe_id, local_json, remote_json, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)", (f"conflict-{uuid.uuid4().hex}", peer_id, remote["id"], json.dumps(item["local"]), json.dumps(remote), now_iso()))
                    conflicts += 1
                conn.execute("INSERT INTO sync_baselines (peer_id, recipe_id, checksum, remote_updated_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(peer_id, recipe_id) DO UPDATE SET checksum=excluded.checksum, remote_updated_at=excluded.remote_updated_at, updated_at=excluded.updated_at", (peer_id, remote["id"], recipe_checksum(remote), remote["updated_at"], now_iso()))
            summary = dict(preview["counts"]); summary["imported"] = imported
            conn.execute("UPDATE sync_peers SET last_sync_at = ? WHERE id = ?", (now_iso(), peer_id))
            conn.execute("INSERT INTO sync_history (id, peer_id, started_at, finished_at, status, summary_json) VALUES (?, ?, ?, ?, ?, ?)", (history_id, peer_id, started, now_iso(), "conflict" if conflicts else "success", json.dumps(summary, sort_keys=True)))
        return {"status": "conflict" if conflicts else "success", "summary": summary}
    except Exception:
        with db_connect() as conn: conn.execute("INSERT INTO sync_history (id, peer_id, started_at, finished_at, status, summary_json) VALUES (?, ?, ?, ?, 'failure', ?)", (history_id, peer_id, started, now_iso(), json.dumps({"error": "sync failed"})))
        raise

def resolve_sync_conflict_action(conflict_id: str, resolution: str) -> dict:
    if resolution not in {"keep_local", "use_remote", "keep_both"}: raise ValueError("resolution must be keep_local, use_remote, or keep_both")
    with db_connect() as conn:
        conflict = conn.execute("SELECT * FROM sync_conflicts WHERE id = ? AND status = 'open'", (conflict_id,)).fetchone()
        if not conflict: raise ValueError("Conflict not found or already resolved.")
        local, remote = json.loads(conflict["local_json"]), json.loads(conflict["remote_json"])
        if resolution == "use_remote":
            conn.execute("UPDATE recipes SET title=?, summary=?, prep_time=?, servings=?, ingredients_json=?, steps_json=?, category_key=?, updated_at=? WHERE id=?", (remote["title"], remote["summary"], remote["prep_time"], remote["servings"], json.dumps(remote["ingredients"]), json.dumps(remote["steps"]), remote.get("category", ""), remote["updated_at"], remote["id"]))
            replace_recipe_tags(conn, remote["id"], remote.get("tags", []))
        elif resolution == "keep_both":
            copy = make_keep_both_copy(remote)
            owner = conn.execute("SELECT owner_id FROM recipes WHERE id = ?", (local["id"],)).fetchone()["owner_id"]
            conn.execute("INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, category_key, created_at, updated_at, sync_source_installation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'remote')", (copy["id"], owner, copy["title"], copy["summary"], copy["prep_time"], copy["servings"], json.dumps(copy["ingredients"]), json.dumps(copy["steps"]), copy.get("category", ""), copy["created_at"], copy["updated_at"]))
            replace_recipe_tags(conn, copy["id"], copy.get("tags", []))
        conn.execute("UPDATE sync_conflicts SET status = 'resolved', resolved_at = ? WHERE id = ?", (now_iso(), conflict_id))
    return {"status": "resolved", "resolution": resolution}
