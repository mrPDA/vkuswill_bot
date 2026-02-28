"""Runtime helpers for recipe tool outputs, fallback data and follow-up detection."""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.agents.recipe_pantry import (
    detect_pantry_tag_for_ingredient,
    normalize_text,
)
from vkuswill_bot.agents.recipe_parsing import normalize_recipe_ingredient_row
from vkuswill_bot.agents.tool_result_compactor import _safe_float
from vkuswill_bot.services.prompts import detect_prompt_profile

logger = logging.getLogger(__name__)


def filter_recipe_ingredients_list(
    *,
    ingredients: list[Any],
    explicit_pantry_requests: set[str],
) -> tuple[list[Any], list[str]]:
    """Filter pantry ingredients unless explicitly requested by user."""
    filtered: list[Any] = []
    removed: list[str] = []
    for row in ingredients:
        if not isinstance(row, dict):
            filtered.append(row)
            continue
        pantry_tag = detect_pantry_tag_for_ingredient(row)
        if pantry_tag and pantry_tag not in explicit_pantry_requests:
            removed.append(str(row.get("name", "")).strip())
            continue
        filtered.append(row)
    return filtered, removed


def enrich_recipe_equivalents(ingredient: dict[str, Any]) -> None:
    """Add equivalent quantities in kg/l/pack for downstream processing."""
    unit = str(ingredient.get("unit", "")).lower().strip()
    quantity = _safe_float(ingredient.get("quantity"), default=0.0)
    name = str(ingredient.get("name", "")).lower()
    if quantity <= 0:
        return
    if unit == "г":
        ingredient["kg_equivalent"] = round(quantity / 1000, 3)
        return
    if unit == "мл":
        ingredient["l_equivalent"] = round(quantity / 1000, 3)
        return
    if "яйц" in name and unit in {"шт", "штука", "штук"}:
        ingredient["pack_equivalent"] = max(1, math.ceil(quantity / 10))


def fallback_borscht_ingredients(servings: int) -> list[dict[str, Any]]:
    """Hardcoded borscht fallback recipe."""
    base_servings = 2
    factor = servings / base_servings if servings > 0 else 1.0
    base = [
        {"name": "свёкла", "quantity": 0.67, "unit": "шт", "search_query": "свекла"},
        {
            "name": "капуста белокочанная",
            "quantity": 0.17,
            "unit": "кг",
            "search_query": "капуста белокочанная",
        },
        {"name": "картофель", "quantity": 0.2, "unit": "кг", "search_query": "картофель"},
        {"name": "морковь", "quantity": 0.05, "unit": "кг", "search_query": "морковь"},
        {
            "name": "лук репчатый",
            "quantity": 0.03,
            "unit": "кг",
            "search_query": "лук репчатый",
        },
        {"name": "помидоры", "quantity": 0.1, "unit": "кг", "search_query": "помидоры свежие"},
        {"name": "говядина", "quantity": 0.4, "unit": "кг", "search_query": "говядина"},
        {"name": "чеснок", "quantity": 10, "unit": "г", "search_query": "чеснок"},
        {
            "name": "масло растительное",
            "quantity": 30,
            "unit": "мл",
            "search_query": "масло растительное",
        },
        {
            "name": "томатная паста",
            "quantity": 60,
            "unit": "г",
            "search_query": "томатная паста",
        },
    ]
    result: list[dict[str, Any]] = []
    for row in base:
        item = dict(row)
        base_quantity = _safe_float(row.get("quantity"), default=1.0)
        item["quantity"] = round(base_quantity * factor, 3)
        enrich_recipe_equivalents(item)
        result.append(item)
    return result


def is_recipe_followup(*, text: str, history: list[dict[str, Any]] | None) -> bool:
    """Detect whether user input is a follow-up in recipe flow."""
    if not history:
        return False
    normalized = normalize_text(text)
    if not normalized or len(normalized) > 120:
        return False
    if any(marker in normalized for marker in ("привяз", "алис", "код", "статус", "отвяз")):
        return False

    recent_user_messages = [
        str(msg.get("content", "")).strip()
        for msg in reversed(history)
        if msg.get("role") == "user" and isinstance(msg.get("content"), str)
    ]
    for prev_text in recent_user_messages[:3]:
        if detect_prompt_profile(prev_text) == "recipe":
            return True

    for msg in reversed(history[-8:]):
        if msg.get("role") == "tool" and msg.get("name") in {
            "recipe_ingredients",
            "recipe_search",
        }:
            return True
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function_data = call.get("function")
            if not isinstance(function_data, dict):
                continue
            name = str(function_data.get("name", "")).strip()
            if name in {"recipe_ingredients", "recipe_search"}:
                return True
    return False


def enrich_requested_ingredients_from_recipe(state: Any, tool_result: str) -> None:
    """Populate state.requested_ingredients from recipe_ingredients response.

    Without this, the deterministic quantity calculator in
    apply_requested_ingredient_overrides never fires for recipe flows
    because requested_ingredients (parsed from user text) is empty.
    """
    try:
        payload = json.loads(tool_result)
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    ingredients: list[Any] | None = None
    if isinstance(payload.get("ingredients"), list):
        ingredients = payload["ingredients"]
    elif isinstance(payload.get("data"), dict):
        data = payload["data"]
        if isinstance(data.get("ingredients"), list):
            ingredients = data["ingredients"]
    if not ingredients:
        return
    seen_queries: set[str] = {
        str(row.get("search_query", "")).strip().lower()
        for row in state.requested_ingredients
        if isinstance(row, dict)
    }
    for raw_row in ingredients:
        normalized = normalize_recipe_ingredient_row(raw_row)
        if not normalized or not normalized.get("name"):
            continue
        query_key = str(normalized.get("search_query", "")).strip().lower()
        if query_key and query_key in seen_queries:
            continue
        seen_queries.add(query_key)
        state.requested_ingredients.append(normalized)


def sanitize_recipe_ingredients_tool_result(
    *,
    tool_result: str,
    explicit_pantry_requests: set[str],
) -> str:
    """Remove pantry ingredients from recipe_ingredients tool payload."""
    payload = parse_json_payload(tool_result)
    if not isinstance(payload, dict) or not payload:
        return tool_result

    removed_names: list[str] = []
    changed = False

    ingredients = payload.get("ingredients")
    if isinstance(ingredients, list):
        filtered, removed = filter_recipe_ingredients_list(
            ingredients=ingredients,
            explicit_pantry_requests=explicit_pantry_requests,
        )
        if len(filtered) != len(ingredients):
            payload["ingredients"] = filtered
            removed_names.extend(removed)
            changed = True

    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("ingredients")
        if isinstance(nested, list):
            filtered, removed = filter_recipe_ingredients_list(
                ingredients=nested,
                explicit_pantry_requests=explicit_pantry_requests,
            )
            if len(filtered) != len(nested):
                data["ingredients"] = filtered
                removed_names.extend(removed)
                changed = True

    if not changed:
        return tool_result

    unique_removed = sorted({name for name in removed_names if name})
    if unique_removed:
        payload["pantry_filtered"] = unique_removed
        logger.info("Filtered pantry ingredients from recipe_ingredients: %s", unique_removed)

    return json.dumps(payload, ensure_ascii=False)
