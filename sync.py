"""Pure synchronization contracts for EPN Recipe Box.

The Flask app owns persistence and transport; this module keeps untrusted-data
validation, checksums, and merge decisions deterministic and easy to test.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


MAX_SYNC_BODY_BYTES = 256 * 1024
RECIPE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
REQUIRED_RECIPE_FIELDS = {
    "id", "title", "summary", "prep_time", "servings", "ingredients",
    "steps", "created_at", "updated_at",
}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, expected_hash: str) -> bool:
    if not token or not expected_hash:
        return False
    return hmac.compare_digest(token_hash(token), expected_hash)


def canonical_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    """Return only the stable, synchronizable recipe fields."""
    return {
        "id": str(recipe["id"]),
        "title": str(recipe["title"]).strip(),
        "summary": str(recipe["summary"]).strip(),
        "prep_time": str(recipe["prep_time"]).strip(),
        "servings": str(recipe["servings"]).strip(),
        "ingredients": [str(item).strip() for item in recipe["ingredients"]],
        "steps": [str(item).strip() for item in recipe["steps"]],
        "created_at": str(recipe["created_at"]),
        "updated_at": str(recipe["updated_at"]),
    }


def recipe_checksum(recipe: dict[str, Any]) -> str:
    payload = json.dumps(canonical_recipe(recipe), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_recipe_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("recipe must be an object")
    missing = REQUIRED_RECIPE_FIELDS - payload.keys()
    if missing:
        raise ValueError("recipe is missing required fields")
    if not isinstance(payload["id"], str) or not RECIPE_ID_PATTERN.fullmatch(payload["id"]):
        raise ValueError("recipe id is invalid")
    for field in ("title", "summary", "prep_time", "servings", "created_at", "updated_at"):
        if not isinstance(payload[field], str) or not payload[field].strip() or len(payload[field]) > 20000:
            raise ValueError(f"recipe {field} is invalid")
    for field in ("ingredients", "steps"):
        if not isinstance(payload[field], list) or len(payload[field]) > 500:
            raise ValueError(f"recipe {field} is invalid")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 2000 for item in payload[field]):
            raise ValueError(f"recipe {field} contains invalid items")
    return canonical_recipe(payload)


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def classify_merge(local: dict[str, Any] | None, remote: dict[str, Any], baseline_checksum: str | None) -> str:
    """Classify remote data as new, unchanged, update, or conflict."""
    remote = validate_recipe_payload(remote)
    if local is None:
        return "new"
    local = validate_recipe_payload(local)
    local_checksum = recipe_checksum(local)
    remote_checksum = recipe_checksum(remote)
    if local_checksum == remote_checksum:
        return "unchanged"
    if baseline_checksum:
        local_changed = local_checksum != baseline_checksum
        remote_changed = remote_checksum != baseline_checksum
        if remote_changed and not local_changed:
            return "update"
        if not remote_changed:
            return "unchanged"
        if local_changed and remote_changed:
            return "conflict"
    return "update" if parse_iso(remote["updated_at"]) > parse_iso(local["updated_at"]) else "unchanged"


def make_keep_both_copy(remote: dict[str, Any]) -> dict[str, Any]:
    copy = validate_recipe_payload(remote)
    copy["id"] = f"{copy['id']}-copy-{uuid.uuid4().hex[:10]}"
    copy["title"] = f"{copy['title']} (remote copy)"
    return copy
