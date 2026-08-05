from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .config import MAX_IMPORT_RECIPES

EXCHANGE_FORMAT = "epn-recipe-box.recipe-exchange"
EXCHANGE_VERSION = 1
_REQUIRED_RECIPE_FIELDS = {
    "id",
    "title",
    "summary",
    "prep_time",
    "servings",
    "ingredients",
    "steps",
    "category",
    "tags",
    "created_at",
    "updated_at",
    "image",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recipe_export(recipe: dict) -> dict:
    return {
        "id": str(recipe["id"]),
        "title": str(recipe["title"]),
        "summary": str(recipe["summary"]),
        "prep_time": str(recipe["prep_time"]),
        "servings": str(recipe["servings"]),
        "ingredients": [str(item) for item in recipe.get("ingredients", [])],
        "steps": [str(item) for item in recipe.get("steps", [])],
        "category": str(recipe.get("category", "")),
        "tags": sorted(
            (
                {"normalized_name": str(tag.get("normalized_name", "")), "display_name": str(tag.get("display_name", ""))}
                for tag in recipe.get("tags", [])
            ),
            key=lambda item: item["normalized_name"],
        ),
        "created_at": str(recipe.get("created_at", "")),
        "updated_at": str(recipe.get("updated_at", "")),
        "image": {
            "available": bool(recipe.get("image_filename")),
            "media_type": str(recipe.get("image_media_type", "")),
            "width": int(recipe.get("image_width", 0) or 0),
            "height": int(recipe.get("image_height", 0) or 0),
            "size": int(recipe.get("image_size", 0) or 0),
            "sha256": str(recipe.get("image_sha256", "")),
        },
    }


def build_exchange(recipes: list[dict], source_installation_id: str) -> dict:
    payload = {
        "format": EXCHANGE_FORMAT,
        "version": EXCHANGE_VERSION,
        "source_installation_id": str(source_installation_id),
        "exported_at": _utc_now(),
        "recipes": sorted((_recipe_export(recipe) for recipe in recipes), key=lambda item: item["id"]),
    }
    return validate_exchange_payload(payload)


def recipe_fingerprint(recipe: dict) -> str:
    comparable = _recipe_export(recipe) if "owner_id" in recipe else recipe
    encoded = json.dumps(comparable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_exchange_payload(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("The import must be a JSON object.")
    if payload.get("format") != EXCHANGE_FORMAT:
        raise ValueError("Unsupported recipe exchange format.")
    if payload.get("version") != EXCHANGE_VERSION:
        raise ValueError("Unsupported recipe exchange version.")
    source = payload.get("source_installation_id")
    recipes = payload.get("recipes")
    if not isinstance(source, str) or not source.strip() or len(source) > 120:
        raise ValueError("A valid source installation ID is required.")
    if not isinstance(recipes, list) or len(recipes) > MAX_IMPORT_RECIPES:
        raise ValueError(f"Imports may contain no more than {MAX_IMPORT_RECIPES} recipes.")
    normalized = dict(payload)
    normalized["source_installation_id"] = source.strip()
    normalized["recipes"] = []
    seen = set()
    for item in recipes:
        if not isinstance(item, dict) or not _REQUIRED_RECIPE_FIELDS <= set(item):
            raise ValueError("Each imported recipe must contain the complete exchange fields.")
        recipe_id = item["id"]
        if not isinstance(recipe_id, str) or not recipe_id.strip() or len(recipe_id) > 120 or recipe_id in seen:
            raise ValueError("Recipe IDs must be unique, non-empty strings.")
        seen.add(recipe_id)
        for field in ("title", "summary", "prep_time", "servings", "category", "created_at", "updated_at"):
            if not isinstance(item[field], str) or len(item[field]) > 20_000:
                raise ValueError(f"Recipe field {field} is invalid.")
        if (
            not isinstance(item["ingredients"], list)
            or not isinstance(item["steps"], list)
            or not all(isinstance(value, str) for value in item["ingredients"] + item["steps"])
        ):
            raise ValueError("Recipe ingredients and steps must be string lists.")
        if not isinstance(item["tags"], list) or not all(
            isinstance(tag, dict) and isinstance(tag.get("normalized_name"), str) and isinstance(tag.get("display_name"), str)
            for tag in item["tags"]
        ):
            raise ValueError("Recipe tags are invalid.")
        image = item["image"]
        if not isinstance(image, dict) or "url" in image or "path" in image or not isinstance(image.get("available"), bool):
            raise ValueError("Recipe image metadata is invalid; remote images are not supported.")
        allowed_image_keys = {"available", "media_type", "width", "height", "size", "sha256"}
        if set(image) - allowed_image_keys:
            raise ValueError("Recipe image metadata contains unsupported fields.")
        normalized["recipes"].append(
            {**item, "id": recipe_id.strip(), "tags": sorted(item["tags"], key=lambda tag: tag["normalized_name"])}
        )
    return normalized
