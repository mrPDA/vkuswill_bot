"""Search-related preprocessing helpers for MCP tool arguments."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.services.search_processor import SearchProcessor


def apply_preferences_to_query(query: str, user_prefs: dict[str, str]) -> str:
    """Append user preferences to product search query."""
    if not user_prefs or not query:
        return query
    query_lower = query.strip().lower()
    preference = user_prefs.get(query_lower)
    if preference is None:
        return query
    if query_lower in preference.lower():
        return preference
    return f"{query} {preference}"


def preprocess_products_search_args(
    tool_args: dict[str, Any],
    *,
    user_preferences: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize `vkusvill_products_search` arguments."""
    normalized_search_args = dict(tool_args)
    if "page" in normalized_search_args:
        normalized_search_args.pop("page", None)
    prefs = user_preferences or {}
    if not prefs:
        return normalized_search_args
    query_key = None
    if isinstance(normalized_search_args.get("q"), str):
        query_key = "q"
    elif isinstance(normalized_search_args.get("query"), str):
        query_key = "query"
    if query_key is None:
        return normalized_search_args
    original_query = str(normalized_search_args.get(query_key, "")).strip()
    if not original_query:
        return normalized_search_args
    enhanced_query = apply_preferences_to_query(original_query, prefs)
    if enhanced_query == original_query:
        return normalized_search_args
    return {**normalized_search_args, query_key: enhanced_query}


def normalize_recipe_search_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize `recipe_search` ingredient rows."""
    ingredients = tool_args.get("ingredients")
    if not isinstance(ingredients, list):
        return tool_args

    normalized_rows: list[Any] = []
    changed = False
    for row in ingredients:
        if not isinstance(row, dict):
            normalized_rows.append(row)
            continue

        normalized = dict(row)
        raw_query = normalized.get("search_query", "")
        query = str(raw_query).strip() if raw_query is not None else ""
        if query:
            cleaned_query = SearchProcessor.clean_search_query(query)
            if cleaned_query and cleaned_query != query:
                normalized["search_query"] = cleaned_query
                changed = True
        else:
            name = str(normalized.get("name", "")).strip()
            if name:
                normalized["search_query"] = SearchProcessor.clean_search_query(name)
                changed = True

        normalized_rows.append(normalized)

    if not changed:
        return tool_args
    return {**tool_args, "ingredients": normalized_rows}
