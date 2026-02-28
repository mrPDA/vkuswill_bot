"""Fallback-реализации recipe_ingredients и recipe_search для ShoppingAgent."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from vkuswill_bot.agents.llm_helpers import extract_message, extract_text
from vkuswill_bot.agents.mcp_response_parser import extract_search_items, parse_json_payload
from vkuswill_bot.agents.recipe_parsing import normalize_recipe_ingredient_row
from vkuswill_bot.agents.recipe_runtime import (
    enrich_recipe_equivalents,
    fallback_borscht_ingredients,
)
from vkuswill_bot.agents.recipe_quantity_calculator import RecipeQuantityCalculator
from vkuswill_bot.services.prompts import get_recipe_extraction_prompt

logger = logging.getLogger(__name__)

# Type alias for an async product-search function.
SearchFn = Callable[[str], Awaitable[str]]
_DEFAULT_FALLBACK_SEARCH_CONCURRENCY = 6


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def extract_recipe_ingredients_with_llm(
    *,
    dish: str,
    servings: int,
    adapter: Any,
    model: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Извлечь ингредиенты блюда через LLM-запрос."""
    if adapter is None:
        return []

    prompt = get_recipe_extraction_prompt().format(dish=dish, servings=servings)
    try:
        response = await asyncio.wait_for(
            adapter.create_completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                tool_choice="none",
            ),
            timeout=timeout_seconds,
        )
    except Exception:
        return []

    message = extract_message(response)
    content = extract_text(message)
    parsed = parse_json_payload(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("ingredients")
    if not isinstance(parsed, list):
        return []

    normalized: list[dict[str, Any]] = []
    for row in parsed[:30]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        unit = str(row.get("unit", "шт")).strip() or "шт"
        quantity = _safe_float(row.get("quantity"), default=1.0)
        if quantity <= 0:
            quantity = 1.0
        ingredient: dict[str, Any] = {
            "name": name,
            "quantity": round(quantity, 3),
            "unit": unit,
            "search_query": str(row.get("search_query", "")).strip() or name,
        }
        if bool(row.get("optional", False)):
            ingredient["optional"] = True
        enrich_recipe_equivalents(ingredient)
        normalized.append(ingredient)

    return normalized


async def fallback_recipe_ingredients(
    arguments: dict[str, Any],
    *,
    adapter: Any,
    model: str,
    timeout_seconds: float,
) -> str:
    """Fallback для recipe_ingredients: извлечь рецепт через LLM."""
    dish = str(arguments.get("dish", "")).strip()
    if not dish:
        return json.dumps(
            {"ok": False, "error": "Не указано название блюда"},
            ensure_ascii=False,
        )

    servings_raw = arguments.get("servings", 2)
    servings = servings_raw if isinstance(servings_raw, int) and servings_raw > 0 else 2

    ingredients = await extract_recipe_ingredients_with_llm(
        dish=dish,
        servings=servings,
        adapter=adapter,
        model=model,
        timeout_seconds=timeout_seconds,
    )
    if not ingredients and "борщ" in dish.lower():
        ingredients = fallback_borscht_ingredients(servings)

    if not ingredients:
        return json.dumps(
            {
                "ok": False,
                "error": "Не удалось получить рецепт",
                "message": "recipe_ingredients unavailable and no fallback recipe",
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "ok": True,
            "dish": dish,
            "servings": servings,
            "ingredients": ingredients,
            "cached": False,
            "hint": (
                "Сначала вызови recipe_search и передай ВЕСЬ массив ingredients. "
                "Если recipe_search недоступен — ищи каждый ингредиент через "
                "vkusvill_products_search (используй search_query). "
                "Для q используй детерминированный расчет по quantity/unit ингредиента "
                "и упаковке найденного товара. "
                "Затем vkusvill_cart_link_create."
            ),
            "source": "shopping_agent_fallback",
        },
        ensure_ascii=False,
    )


async def fallback_recipe_search(
    arguments: dict[str, Any],
    *,
    search_fn: SearchFn,
    max_concurrent: int = _DEFAULT_FALLBACK_SEARCH_CONCURRENCY,
) -> str:
    """Fallback для recipe_search: поиск каждого ингредиента через vkusvill_products_search."""
    ingredients = arguments.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        return json.dumps(
            {"ok": False, "error": "Пустой список ingredients"},
            ensure_ascii=False,
        )

    results: list[dict[str, Any]] = []
    found: list[dict[str, Any]] = []
    not_found: list[str] = []
    search_log: dict[str, list[int]] = {}
    pending: list[tuple[dict[str, Any], str, str]] = []

    for row_raw in ingredients[:40]:
        row = normalize_recipe_ingredient_row(row_raw)
        if not row:
            continue
        query = str(row.get("search_query", "")).strip() or str(row.get("name", "")).strip()
        ingredient_name = str(row.get("name", "")).strip() or query
        if not query:
            continue
        pending.append((row, query, ingredient_name))

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def _search_one(
        row: dict[str, Any],
        query: str,
        ingredient_name: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, list[int], str]:
        async with semaphore:
            raw = await search_fn(query)
        parsed = parse_json_payload(raw)
        items = extract_search_items(parsed)
        if not items:
            result = {
                "ingredient": ingredient_name,
                "search_query": query,
                "best_match": None,
                "alternatives": [],
                "error": "Поиск не вернул items",
            }
            return result, None, [], query

        ids = [item.get("xml_id") for item in items if isinstance(item.get("xml_id"), int)]

        best = items[0]
        suggested_q = RecipeQuantityCalculator.calculate_purchase_q(row, best)
        best_match = {
            "xml_id": best.get("xml_id"),
            "name": best.get("name"),
            "price": best.get("price"),
            "unit": best.get("unit", "шт"),
            "suggested_q": suggested_q,
        }
        alternatives = [
            {
                "xml_id": item.get("xml_id"),
                "name": item.get("name"),
                "price": item.get("price"),
                "unit": item.get("unit", "шт"),
                "suggested_q": RecipeQuantityCalculator.calculate_purchase_q(row, item),
            }
            for item in items[1:4]
        ]
        result = {
            "ingredient": ingredient_name,
            "search_query": query,
            "best_match": best_match,
            "alternatives": alternatives,
        }
        found_row = {
            "ingredient": ingredient_name,
            "quantity": row.get("quantity"),
            "unit": row.get("unit"),
            "search_query": query,
            "item": {
                "xml_id": best_match.get("xml_id"),
                "name": best_match.get("name"),
                "price": best_match.get("price"),
                "unit": best_match.get("unit", "шт"),
            },
            "suggested_q": best_match.get("suggested_q"),
            "alternatives": alternatives,
        }
        normalized_ids = [xml_id for xml_id in ids if isinstance(xml_id, int)]
        return result, found_row, normalized_ids, query

    payloads = await asyncio.gather(
        *[_search_one(row, query, ingredient_name) for row, query, ingredient_name in pending]
    )
    for result_row, found_row, ids, query in payloads:
        if ids:
            search_log[query] = ids
        if found_row is None:
            not_found.append(query)
            results.append(result_row)
            continue
        found.append(found_row)
        results.append(result_row)

    return json.dumps(
        {
            "ok": True,
            "results": results,
            "data": {
                "found": found,
                "not_found": not_found,
                "search_log": search_log,
            },
            "not_found": not_found,
            "search_log": search_log,
            "source": "shopping_agent_fallback",
        },
        ensure_ascii=False,
    )
