"""Preprocessing and normalization of MCP tool arguments."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.tool_preprocessor_cart import (
    collect_requested_products_snapshot,
    preprocess_cart_link_args,
    restore_previous_quantities_for_additive_update,
)
from vkuswill_bot.agents.tool_preprocessor_search import (
    apply_preferences_to_query,
    inject_preference_mismatch_hint,
    normalize_recipe_search_args,
    preprocess_products_search_args,
)


def preprocess_tool_args(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    user_preferences: dict[str, str] | None = None,
    product_index: dict[int, dict[str, Any]] | None = None,
    explicit_egg_pack_request: bool = False,
    requested_ingredients: list[dict[str, Any]] | None = None,
    search_query_by_xml_id: dict[int, str] | None = None,
    requested_quantity_overrides: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Нормализует аргументы MCP-инструмента перед вызовом."""
    if tool_name == "vkusvill_cart_link_create":
        return preprocess_cart_link_args(
            tool_args,
            product_index=product_index,
            explicit_egg_pack_request=explicit_egg_pack_request,
        )
    if tool_name == "vkusvill_products_search":
        return preprocess_products_search_args(tool_args, user_preferences=user_preferences)
    if tool_name == "recipe_search":
        return normalize_recipe_search_args(tool_args)
    return tool_args


__all__ = [
    "apply_preferences_to_query",
    "collect_requested_products_snapshot",
    "normalize_recipe_search_args",
    "preprocess_tool_args",
    "restore_previous_quantities_for_additive_update",
]
