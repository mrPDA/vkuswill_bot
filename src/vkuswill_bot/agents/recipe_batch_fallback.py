from __future__ import annotations

import asyncio
import json
from typing import Any

from vkuswill_bot.agents.llm_helpers import (
    estimate_usage_details,
    extract_message,
    extract_text,
)
from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.services.llm_adapters import extract_usage_details

_BATCH_MAX_TOKENS = 2600
_BATCH_TEMPERATURE = 0.1


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_batch_prompt(dishes: list[dict[str, Any]]) -> str:
    payload = [
        {"dish": str(row.get("dish", "")).strip(), "servings": int(row.get("servings", 1) or 1)}
        for row in dishes
        if str(row.get("dish", "")).strip()
    ]
    return (
        "Для каждого блюда из списка верни ингредиенты в JSON без markdown.\n"
        "Формат ответа:\n"
        '{"dishes":[{"dish":"...","ingredients":[{"name":"...","quantity":1,"unit":"шт","search_query":"..."}]}]}\n'
        "Используй search_query как название продукта для поиска в магазине.\n"
        "Список блюд:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _normalize_batch_payload(payload: Any) -> dict[str, list[dict[str, Any]]]:
    dishes_raw = payload.get("dishes") if isinstance(payload, dict) else None
    if not isinstance(dishes_raw, list):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for row in dishes_raw[:8]:
        if not isinstance(row, dict):
            continue
        dish_name = str(row.get("dish", "")).strip()
        if not dish_name:
            continue
        ingredients_raw = row.get("ingredients")
        if not isinstance(ingredients_raw, list):
            continue
        normalized: list[dict[str, Any]] = []
        for ingredient_row in ingredients_raw[:30]:
            if not isinstance(ingredient_row, dict):
                continue
            name = str(ingredient_row.get("name", "")).strip()
            if not name:
                continue
            query = str(ingredient_row.get("search_query", "")).strip() or name
            unit = str(ingredient_row.get("unit", "шт")).strip() or "шт"
            quantity = _safe_float(ingredient_row.get("quantity"), default=1.0)
            if quantity <= 0:
                quantity = 1.0
            normalized.append(
                {
                    "name": name,
                    "search_query": query,
                    "quantity": round(quantity, 3),
                    "unit": unit,
                }
            )
        if normalized:
            result[dish_name.strip().lower()] = normalized
    return result


async def extract_recipe_ingredients_batch_with_llm(
    *,
    dishes: list[dict[str, Any]],
    adapter: Any,
    model: str,
    timeout_seconds: float,
    trace: Any | None = None,
    llm_provider: str | None = None,
    chunk_index: int | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    if adapter is None:
        return {}, {"status": "adapter_missing"}
    prompt = _build_batch_prompt(dishes)
    messages = [{"role": "user", "content": prompt}]
    generation = (
        trace.generation(
            name="meal-plan-ingredient-batch-fallback",
            model=model,
            input=messages,
            model_parameters={
                "provider": llm_provider or "",
                "chunk_index": chunk_index,
                "dishes": len(dishes),
                "temperature": _BATCH_TEMPERATURE,
                "max_tokens": _BATCH_MAX_TOKENS,
            },
            metadata={"chunk_index": chunk_index, "dishes": len(dishes)},
        )
        if trace is not None
        else None
    )
    try:
        response = await asyncio.wait_for(
            adapter.create_completion(
                model=model,
                messages=messages,
                tools=[],
                tool_choice="none",
                max_tokens=_BATCH_MAX_TOKENS,
                temperature=_BATCH_TEMPERATURE,
            ),
            timeout=timeout_seconds,
        )
    except Exception as exc:
        if generation is not None:
            generation.end(
                output=str(exc),
                metadata={"chunk_index": chunk_index, "dishes": len(dishes)},
                level="ERROR",
                status_message="ingredient_batch_fallback_error",
            )
        return {}, {
            "status": "exception",
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:240],
        }
    text = extract_text(extract_message(response))
    payload = parse_json_payload(text)
    normalized = _normalize_batch_payload(payload)
    usage_details = extract_usage_details(response) or estimate_usage_details(
        messages=messages,
        message=extract_message(response),
    )
    if generation is not None:
        generation.end(
            output=text[:5000],
            usage_details=usage_details,
            metadata={
                "chunk_index": chunk_index,
                "dishes": len(dishes),
                "returned_dishes": len(normalized),
                "status": "success" if normalized else "empty",
            },
            level="DEFAULT" if normalized else "WARNING",
            status_message=None if normalized else "ingredient_batch_fallback_empty",
        )
    return normalized, {
        "status": "success" if normalized else "empty",
        "raw_preview": text[:400],
        "returned_dishes": len(normalized),
    }
