"""Ingredient collection helpers for meal-plan phase 2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.agents.meal_plan_runtime_ops import extract_ingredients
from vkuswill_bot.agents.meal_plan_runtime_policy import call_with_timeout_retry
from vkuswill_bot.agents.recipe_fallback import extract_recipe_ingredients_with_llm_debug

_MCP_RECIPE_INGREDIENTS_TIMEOUT_SECONDS = 2.0
_LLM_RECIPE_EXTRACTION_TIMEOUT_SECONDS = 4.0


class MealPlanIngredientCollectionAgentProtocol(Protocol):
    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str: ...


@dataclass(slots=True)
class IngredientCollectionStats:
    total_dishes: int
    mcp_success_dishes: int
    fallback_attempted_dishes: int
    fallback_success_dishes: int
    empty_dishes: list[str]
    mcp_rows_total: int
    fallback_rows_total: int
    mcp_sample_failures: list[dict[str, Any]]
    fallback_sample_failures: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_dishes": self.total_dishes,
            "mcp_success_dishes": self.mcp_success_dishes,
            "fallback_attempted_dishes": self.fallback_attempted_dishes,
            "fallback_success_dishes": self.fallback_success_dishes,
            "empty_dishes": list(self.empty_dishes),
            "mcp_rows_total": self.mcp_rows_total,
            "fallback_rows_total": self.fallback_rows_total,
            "mcp_sample_failures": list(self.mcp_sample_failures),
            "fallback_sample_failures": list(self.fallback_sample_failures),
        }


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _group_sizes_from_request(request: Any) -> dict[str, int]:
    groups = getattr(request, "groups", None)
    if not isinstance(groups, list):
        return {}
    sizes: dict[str, int] = {}
    for group in groups:
        group_id = str(getattr(group, "id", "")).strip()
        count = _positive_int(getattr(group, "count", None))
        if group_id and count is not None:
            sizes[group_id] = count
    return sizes


def _effective_servings_for_dish(*, dish: dict[str, Any], group_sizes: dict[str, int]) -> int:
    hint = _positive_int(dish.get("servings_total"))
    audience_raw = dish.get("audience_groups")
    audience = []
    if isinstance(audience_raw, list):
        audience = [str(group_id).strip() for group_id in audience_raw if str(group_id).strip()]
    if group_sizes and audience:
        computed = sum(
            group_sizes[group_id] for group_id in dict.fromkeys(audience) if group_id in group_sizes
        )
        if computed > 0:
            return computed
    if hint is not None:
        return hint
    return 1


async def _fallback_rows_for_dish(
    *,
    agent: Any,
    dish_name: str,
    servings: int,
    llm_provider: str,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    adapters = getattr(agent, "_llm_adapters", None)
    if not isinstance(adapters, dict):
        return [], {"status": "adapter_registry_missing"}
    adapter = adapters.get(llm_provider)
    if adapter is None:
        return [], {"status": "adapter_missing", "llm_provider": llm_provider}
    resolve_model = getattr(agent, "_resolve_model_for_provider", None)
    if not callable(resolve_model):
        return [], {"status": "model_resolver_missing"}
    try:
        model = str(resolve_model(llm_provider)).strip()
    except Exception as exc:
        return [], {"status": "model_resolve_error", "error_type": type(exc).__name__}
    if not model:
        return [], {"status": "model_missing"}
    llm_timeout = getattr(agent, "_llm_timeout_seconds", timeout_seconds)
    if not isinstance(llm_timeout, int | float) or llm_timeout <= 0:
        llm_timeout = timeout_seconds
    try:
        debug = await extract_recipe_ingredients_with_llm_debug(
            dish=dish_name,
            servings=servings,
            adapter=adapter,
            model=model,
            timeout_seconds=min(
                float(llm_timeout),
                timeout_seconds,
                _LLM_RECIPE_EXTRACTION_TIMEOUT_SECONDS,
            ),
        )
    except Exception as exc:
        return [], {
            "status": "exception",
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:240],
        }
    status = "success" if debug.rows else "empty"
    payload = debug.as_dict()
    payload["status"] = status
    payload["model"] = model
    return debug.rows, payload


async def collect_ingredients_for_dishes(
    *,
    agent: MealPlanIngredientCollectionAgentProtocol,
    request: Any,
    state: Any,
    user_id: int,
    llm_provider: str,
    dishes_payload: list[dict[str, Any]],
    phase2_deadline_at: float,
    timeout_seconds: float,
    semaphore_limit: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], IngredientCollectionStats]:
    semaphore = asyncio.Semaphore(max(1, semaphore_limit))
    fallback_semaphore = asyncio.Semaphore(2)
    group_sizes = _group_sizes_from_request(request)

    async def _load_ingredients(
        dish: dict[str, Any],
    ) -> tuple[str, int, list[dict[str, Any]], dict[str, Any]]:
        dish_name = str(dish["name"])
        servings = _effective_servings_for_dish(dish=dish, group_sizes=group_sizes)
        async with semaphore:
            try:
                result = await call_with_timeout_retry(
                    operation=lambda: agent._call_mcp_tool(
                        name="recipe_ingredients",
                        arguments={"dish": dish_name, "servings": servings},
                        llm_provider=llm_provider,
                        call_cache=state.mcp_call_cache,
                        user_id=user_id,
                    ),
                    timeout_seconds=min(timeout_seconds, _MCP_RECIPE_INGREDIENTS_TIMEOUT_SECONDS),
                    hard_deadline_at=phase2_deadline_at,
                    retries=0,
                )
            except Exception as exc:
                return dish_name, servings, [], {
                    "status": "exception",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:240],
                }
            rows = extract_ingredients(result)
            parsed = parse_json_payload(result)
            if rows:
                return dish_name, servings, rows, {"status": "success", "rows": len(rows)}
            error = (
                str(parsed.get("error", "")).strip()
                if isinstance(parsed, dict)
                else ""
            )
            message = (
                str(parsed.get("message", "")).strip()
                if isinstance(parsed, dict)
                else ""
            )
            return dish_name, servings, [], {
                "status": "empty",
                "error": error[:120],
                "message": message[:240],
            }

    tasks = [_load_ingredients(dish) for dish in dishes_payload]
    chunks = await asyncio.gather(*tasks, return_exceptions=True)
    flat_ingredients: list[dict[str, Any]] = []
    by_dish: dict[str, list[dict[str, Any]]] = {}
    missing_fallback: list[tuple[str, int]] = []
    mcp_success_dishes = 0
    mcp_rows_total = 0
    mcp_sample_failures: list[dict[str, Any]] = []
    for chunk in chunks:
        if isinstance(chunk, Exception):
            continue
        dish_name, servings, rows, mcp_debug = chunk
        dish_key = str(dish_name).strip().lower()
        if dish_key and rows:
            by_dish.setdefault(dish_key, []).extend(rows)
            flat_ingredients.extend(rows)
            mcp_success_dishes += 1
            mcp_rows_total += len(rows)
            continue
        if dish_key:
            missing_fallback.append((dish_name, servings))
            if len(mcp_sample_failures) < 5:
                mcp_sample_failures.append({"dish": dish_name, **mcp_debug})

    async def _load_fallback(
        dish_name: str,
        servings: int,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        async with fallback_semaphore:
            rows, debug = await _fallback_rows_for_dish(
                agent=agent,
                dish_name=dish_name,
                servings=servings,
                llm_provider=llm_provider,
                timeout_seconds=timeout_seconds,
            )
            return dish_name, rows, debug

    if missing_fallback:
        fallback_chunks = await asyncio.gather(
            *[_load_fallback(dish_name, servings) for dish_name, servings in missing_fallback],
            return_exceptions=True,
        )
        fallback_success_dishes = 0
        fallback_rows_total = 0
        fallback_sample_failures: list[dict[str, Any]] = []
        for chunk in fallback_chunks:
            if isinstance(chunk, Exception):
                continue
            dish_name, rows, fallback_debug = chunk
            dish_key = str(dish_name).strip().lower()
            if dish_key and rows:
                by_dish.setdefault(dish_key, []).extend(rows)
                flat_ingredients.extend(rows)
                fallback_success_dishes += 1
                fallback_rows_total += len(rows)
                continue
            if len(fallback_sample_failures) < 5:
                fallback_sample_failures.append({"dish": dish_name, **fallback_debug})
    else:
        fallback_success_dishes = 0
        fallback_rows_total = 0
        fallback_sample_failures = []

    empty_dishes = [
        str(dish_name).strip()
        for dish_name, _servings in missing_fallback
        if str(dish_name).strip().lower() not in by_dish
    ]
    stats = IngredientCollectionStats(
        total_dishes=len(dishes_payload),
        mcp_success_dishes=mcp_success_dishes,
        fallback_attempted_dishes=len(missing_fallback),
        fallback_success_dishes=fallback_success_dishes,
        empty_dishes=empty_dishes[:10],
        mcp_rows_total=mcp_rows_total,
        fallback_rows_total=fallback_rows_total,
        mcp_sample_failures=mcp_sample_failures,
        fallback_sample_failures=fallback_sample_failures,
    )
    return flat_ingredients, by_dish, stats
