from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
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
from vkuswill_bot.services.prompts import get_recipe_extraction_prompt_with_metadata

logger = logging.getLogger(__name__)
SearchFn = Callable[[str], Awaitable[str]]
_DEFAULT_FALLBACK_SEARCH_CONCURRENCY = 6
_RECIPE_FALLBACK_MAX_TOKENS = 900
_RECIPE_FALLBACK_TEMPERATURE = 0.1


@dataclass(slots=True)
class RecipeExtractionDebug:
    rows: list[dict[str, Any]]
    attempts: int
    raw_preview: str
    parsed_type: str
    error_type: str | None = None
    error_message: str | None = None
    prompt_metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "rows": len(self.rows),
            "attempts": self.attempts,
            "parsed_type": self.parsed_type,
            "raw_preview": self.raw_preview,
        }
        if self.error_type:
            payload["error_type"] = self.error_type
        if self.error_message:
            payload["error_message"] = self.error_message
        if self.prompt_metadata:
            payload["prompt"] = self.prompt_metadata
        return payload


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
    debug = await extract_recipe_ingredients_with_llm_debug(
        dish=dish,
        servings=servings,
        adapter=adapter,
        model=model,
        timeout_seconds=timeout_seconds,
    )
    return debug.rows


async def extract_recipe_ingredients_with_llm_debug(
    *,
    dish: str,
    servings: int,
    adapter: Any,
    model: str,
    timeout_seconds: float,
) -> RecipeExtractionDebug:
    if adapter is None:
        return RecipeExtractionDebug(
            rows=[],
            attempts=0,
            raw_preview="",
            parsed_type="adapter_missing",
            error_type="adapter_missing",
        )
    prompt_template, prompt_metadata = get_recipe_extraction_prompt_with_metadata()
    prompt = prompt_template.format(dish=dish, servings=servings)
    started_at = time.monotonic()
    last_raw_preview = ""
    last_parsed_type = "empty"
    last_error_type: str | None = None
    last_error_message: str | None = None
    attempts = 0

    async def _call_and_parse(prompt_text: str) -> list[dict[str, Any]]:
        nonlocal attempts, last_raw_preview, last_parsed_type, last_error_type, last_error_message
        attempts += 1
        remaining = timeout_seconds - (time.monotonic() - started_at)
        if remaining <= 0:
            last_parsed_type = "timeout_before_call"
            last_error_type = "timeout"
            last_error_message = "deadline_exceeded_before_call"
            return []
        try:
            response = await asyncio.wait_for(
                adapter.create_completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt_text}],
                    tools=[],
                    tool_choice="none",
                    max_tokens=_RECIPE_FALLBACK_MAX_TOKENS,
                    temperature=_RECIPE_FALLBACK_TEMPERATURE,
                ),
                timeout=remaining,
            )
        except Exception as exc:
            last_parsed_type = "exception"
            last_error_type = type(exc).__name__
            last_error_message = str(exc)[:240]
            return []
        message = extract_message(response)
        content = extract_text(message)
        last_raw_preview = content[:400]
        parsed = parse_json_payload(content)
        if isinstance(parsed, dict):
            parsed = parsed.get("ingredients")
        last_parsed_type = type(parsed).__name__
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

    rows = await _call_and_parse(prompt)
    if rows:
        return RecipeExtractionDebug(
            rows=rows,
            attempts=attempts,
            raw_preview=last_raw_preview,
            parsed_type=last_parsed_type,
            prompt_metadata=prompt_metadata,
        )
    if last_error_type is not None and not last_raw_preview:
        return RecipeExtractionDebug(
            rows=[],
            attempts=attempts,
            raw_preview=last_raw_preview,
            parsed_type=last_parsed_type,
            error_type=last_error_type,
            error_message=last_error_message,
            prompt_metadata=prompt_metadata,
        )
    retry_prompt = (
        f"{prompt}\n\n"
        "Предыдущий ответ не удалось распарсить.\n"
        "Верни только валидный JSON-массив ингредиентов без markdown и пояснений."
    )
    rows = await _call_and_parse(retry_prompt)
    return RecipeExtractionDebug(
        rows=rows,
        attempts=attempts,
        raw_preview=last_raw_preview,
        parsed_type=last_parsed_type,
        error_type=last_error_type,
        error_message=last_error_message,
        prompt_metadata=prompt_metadata,
    )


async def fallback_recipe_ingredients(
    arguments: dict[str, Any],
    *,
    adapter: Any,
    model: str,
    timeout_seconds: float,
) -> str:
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
