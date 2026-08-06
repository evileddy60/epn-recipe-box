"""Centralized recipe visibility and mutation policies."""

from __future__ import annotations

VISIBILITIES = frozenset({"private", "shared_epn"})


def normalize_visibility(value: str | None) -> str:
    value = (value or "shared_epn").strip().lower()
    if value not in VISIBILITIES:
        raise ValueError("Choose a supported recipe visibility.")
    return value


def can_view_recipe(recipe: dict, user_id: str | None, selected_user_ids: set[str] | None = None) -> bool:
    if not user_id:
        return False
    if recipe.get("owner_id") == user_id:
        return True
    visibility = recipe.get("visibility", "shared_epn")
    if visibility == "shared_epn":
        return True
    if visibility == "selected_users":
        return False
    return False


def can_mutate_recipe(recipe: dict, user_id: str | None) -> bool:
    return bool(user_id and recipe.get("owner_id") == user_id)


def can_comment_or_rate(recipe: dict, user_id: str | None, selected_user_ids: set[str] | None = None) -> bool:
    return can_view_recipe(recipe, user_id, selected_user_ids)
