"""Search-related preprocessing helpers for MCP tool arguments."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.tool_input_normalizers import (
    clean_search_query as _clean_search_query,
)


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

    query_key = None
    if isinstance(normalized_search_args.get("q"), str):
        query_key = "q"
    elif isinstance(normalized_search_args.get("query"), str):
        query_key = "query"
    if query_key is not None:
        original_query = str(normalized_search_args.get(query_key, "")).strip()
        if original_query:
            cleaned = _clean_search_query(original_query)
            if cleaned and cleaned != original_query:
                normalized_search_args[query_key] = cleaned

    prefs = user_preferences or {}
    if not prefs or query_key is None:
        return normalized_search_args
    current_query = str(normalized_search_args.get(query_key, "")).strip()
    if not current_query:
        return normalized_search_args
    enhanced_query = apply_preferences_to_query(current_query, prefs)
    if enhanced_query == current_query:
        return normalized_search_args
    return {**normalized_search_args, query_key: enhanced_query}


def inject_preference_mismatch_hint(
    tool_result: str,
    *,
    user_preferences: dict[str, str] | None = None,
) -> str:
    """Add relevance_warning to search result when top items don't match a preference.

    Helps the LLM detect that the search returned a different product than
    the user's saved preference and decide to inform the user instead of
    silently ordering a wrong item.
    """
    if not user_preferences:
        return tool_result
    import contextlib
    import json

    with contextlib.suppress(Exception):
        parsed = json.loads(tool_result)
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            return tool_result

        data = parsed.get("data") or parsed
        if not isinstance(data, dict):
            return tool_result
        meta = data.get("meta", {})
        query = str(meta.get("q", "")).strip().lower() if isinstance(meta, dict) else ""
        if not query:
            return tool_result

        matched_pref: str | None = None
        for _cat, pref_text in user_preferences.items():
            if pref_text.lower() in query or query in pref_text.lower():
                matched_pref = pref_text
                break
        if matched_pref is None:
            return tool_result

        items = data.get("items", [])
        if not isinstance(items, list) or not items:
            return tool_result

        pref_lower = matched_pref.lower()
        first = items[0]
        top_name = str(first.get("name", "")).strip().lower() if isinstance(first, dict) else ""
        pref_tokens = set(pref_lower.split())
        name_tokens = set(top_name.split())
        overlap = pref_tokens & name_tokens
        if len(overlap) >= len(pref_tokens) * 0.6:
            return tool_result

        warning = (
            f"Внимание: любимый продукт пользователя «{matched_pref}» "
            f"не найден точно. Первый результат — «{items[0].get('name', '')}». "
            "Проверь совпадение перед заказом или уточни у пользователя."
        )
        data["relevance_warning"] = warning
        return json.dumps(parsed, ensure_ascii=False)

    return tool_result


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
