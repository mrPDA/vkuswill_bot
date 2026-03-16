"""Day-by-day product selection for meal-plan executor."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from vkuswill_bot.agents.meal_plan_recipe_search_ops import (
    create_global_mcp_search_semaphore,
    search_products,
)
from vkuswill_bot.agents.meal_plan_runtime_ops import merge_products
from vkuswill_bot.agents.meal_plan_trace_ops import finish_search_span, start_span

FilterPantryFn = Callable[..., tuple[list[dict[str, Any]], list[str]]]
AggregateIngredientsFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
PrioritizeIngredientsFn = Callable[..., tuple[list[dict[str, Any]], list[str]]]

_DAY_SEARCH_CONCURRENCY = 2


@dataclass(slots=True)
class MealPlanDaySearchStats:
    day_count: int = 0
    searched_day_count: int = 0
    aggregated_ingredients_count: int = 0
    prioritized_ingredients_count: int = 0
    deferred_ingredients_count: int = 0
    pantry_filtered_count: int = 0
    primary_attempted: bool = False
    primary_products_count: int = 0
    primary_not_found_count: int = 0
    final_products_count: int = 0
    final_not_found_count: int = 0
    used_chunk_fallback: bool = False
    chunk_count: int = 0
    chunk_products_count: int = 0
    chunk_not_found_count: int = 0
    fallback_reason: str = "day_by_day"
    fallback_reasons: list[str] = field(default_factory=list)
    primary_error_type: str | None = None
    primary_error_message: str | None = None
    chunk_failure_count: int = 0
    chunk_sample_failures: list[dict[str, Any]] = field(default_factory=list)
    local_fallback_chunk_count: int = 0
    local_fallback_products_count: int = 0
    local_fallback_not_found_count: int = 0
    days: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "search_mode": "day_by_day",
            "day_count": self.day_count,
            "searched_day_count": self.searched_day_count,
            "aggregated_ingredients_count": self.aggregated_ingredients_count,
            "prioritized_ingredients_count": self.prioritized_ingredients_count,
            "deferred_ingredients_count": self.deferred_ingredients_count,
            "pantry_filtered_count": self.pantry_filtered_count,
            "primary_attempted": self.primary_attempted,
            "primary_products_count": self.primary_products_count,
            "primary_not_found_count": self.primary_not_found_count,
            "final_products_count": self.final_products_count,
            "final_not_found_count": self.final_not_found_count,
            "used_chunk_fallback": self.used_chunk_fallback,
            "chunk_count": self.chunk_count,
            "chunk_products_count": self.chunk_products_count,
            "chunk_not_found_count": self.chunk_not_found_count,
            "fallback_reason": self.fallback_reason,
            "fallback_reasons": list(self.fallback_reasons),
            "primary_error_type": self.primary_error_type,
            "primary_error_message": self.primary_error_message,
            "chunk_failure_count": self.chunk_failure_count,
            "chunk_sample_failures": list(self.chunk_sample_failures),
            "local_fallback_chunk_count": self.local_fallback_chunk_count,
            "local_fallback_products_count": self.local_fallback_products_count,
            "local_fallback_not_found_count": self.local_fallback_not_found_count,
            "days": list(self.days),
        }


def _dish_key(dish_name: Any) -> str:
    return str(dish_name or "").strip().lower()


def _group_dishes_by_day(dishes_payload: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for dish in dishes_payload:
        try:
            day = int(dish.get("day", 0))
        except (TypeError, ValueError):
            continue
        if day <= 0:
            continue
        grouped.setdefault(day, []).append(dish)
    return grouped


def _ingredients_for_day(
    *,
    day_dishes: list[dict[str, Any]],
    ingredients_by_dish: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dish in day_dishes:
        rows.extend(ingredients_by_dish.get(_dish_key(dish.get("name")), []))
    return rows


async def search_products_day_by_day(
    *,
    agent: Any,
    state: Any,
    user_id: int,
    llm_provider: str,
    dishes_payload: list[dict[str, Any]],
    ingredients_by_dish: dict[str, list[dict[str, Any]]],
    phase2_deadline_at: float,
    trace: Any | None,
    filter_pantry_fn: FilterPantryFn,
    aggregate_ingredients_fn: AggregateIngredientsFn,
    prioritize_ingredients_fn: PrioritizeIngredientsFn,
) -> tuple[
    list[dict[str, Any]],
    list[str],
    bool,
    MealPlanDaySearchStats,
    list[str],
    list[str],
    dict[int, list[dict[str, Any]]],
]:
    grouped_days = _group_dishes_by_day(dishes_payload)
    stats = MealPlanDaySearchStats(day_count=len(grouped_days))
    all_products: list[dict[str, Any]] = []
    all_not_found: list[str] = []
    pantry_filtered_all: set[str] = set()
    deferred_all: set[str] = set()
    global_mcp_semaphore = create_global_mcp_search_semaphore()

    day_items = sorted(grouped_days.items())
    _recipe_search_disabled = False
    _PROBE_BUDGET_SECONDS = 15.0

    async def _run_day(
        day: int,
        day_dishes: list[dict[str, Any]],
        semaphore: asyncio.Semaphore,
        *,
        budget_seconds: float | None = None,
    ) -> dict[str, Any]:
        async with semaphore:
            if budget_seconds is not None:
                day_deadline_at = min(
                    phase2_deadline_at,
                    time.monotonic() + budget_seconds,
                )
            else:
                day_deadline_at = phase2_deadline_at
            day_flat_ingredients = _ingredients_for_day(
                day_dishes=day_dishes,
                ingredients_by_dish=ingredients_by_dish,
            )
            searchable_ingredients, pantry_filtered = filter_pantry_fn(
                items=day_flat_ingredients,
                explicit_pantry_requests=state.explicit_pantry_requests,
            )
            aggregated = aggregate_ingredients_fn(searchable_ingredients)
            prioritized, deferred = prioritize_ingredients_fn(items=aggregated)

            day_span = start_span(
                trace=trace,
                name=f"meal-plan.search-day-{day}",
                input={
                    "day": day,
                    "dish_names": [str(dish.get("name", "")).strip() for dish in day_dishes],
                    "aggregated_ingredients_count": len(aggregated),
                    "prioritized_ingredients_count": len(prioritized),
                    "deferred_ingredients_count": len(deferred),
                    "pantry_filtered_count": len(pantry_filtered),
                },
            )
            if not prioritized:
                skipped_output = {
                    "products_count": 0,
                    "not_found_count": 0,
                    "used_chunk_fallback": False,
                    "stats": {"search_skipped": True},
                }
                if day_span is not None:
                    day_span.end(
                        output=skipped_output,
                        level="WARNING",
                        status_message="meal_plan_day_search_skipped",
                    )
                return {
                    "day": day,
                    "dishes": [str(dish.get("name", "")).strip() for dish in day_dishes],
                    "aggregated_count": len(aggregated),
                    "prioritized_count": 0,
                    "deferred_count": len(deferred),
                    "pantry_filtered": pantry_filtered,
                    "deferred": deferred,
                    "products": [],
                    "not_found": [],
                    "used_chunk_fallback": False,
                    "search_stats": None,
                    "day_record": {
                        "day": day,
                        "dishes": [str(dish.get("name", "")).strip() for dish in day_dishes],
                        "aggregated_ingredients_count": len(aggregated),
                        "prioritized_ingredients_count": 0,
                        "deferred_ingredients_count": len(deferred),
                        "products_count": 0,
                        "not_found_count": 0,
                        "used_chunk_fallback": False,
                    },
                }

            (
                day_products,
                day_not_found,
                used_chunk_fallback,
                day_search_stats,
            ) = await search_products(
                agent=agent,
                state=state,
                user_id=user_id,
                llm_provider=llm_provider,
                aggregated_ingredients=prioritized,
                phase2_deadline_at=day_deadline_at,
                prefer_local_only=_recipe_search_disabled,
                global_mcp_semaphore=global_mcp_semaphore,
            )
            finish_search_span(
                span=day_span,
                products=day_products,
                not_found=day_not_found,
                used_chunk_fallback=used_chunk_fallback,
                stats=day_search_stats,
            )
            return {
                "day": day,
                "dishes": [str(dish.get("name", "")).strip() for dish in day_dishes],
                "aggregated_count": len(aggregated),
                "prioritized_count": len(prioritized),
                "deferred_count": len(deferred),
                "pantry_filtered": pantry_filtered,
                "deferred": deferred,
                "products": day_products,
                "not_found": day_not_found,
                "used_chunk_fallback": used_chunk_fallback,
                "search_stats": day_search_stats,
                "day_record": {
                    "day": day,
                    "dishes": [str(dish.get("name", "")).strip() for dish in day_dishes],
                    "aggregated_ingredients_count": len(aggregated),
                    "prioritized_ingredients_count": len(prioritized),
                    "deferred_ingredients_count": len(deferred),
                    "products_count": len(day_products),
                    "not_found_count": len(day_not_found),
                    "used_chunk_fallback": used_chunk_fallback,
                    "search_stats": day_search_stats.as_dict(),
                },
            }

    semaphore = asyncio.Semaphore(_DAY_SEARCH_CONCURRENCY)

    probe_day, probe_dishes = day_items[0]
    probe_start = time.monotonic()
    probe_result = await _run_day(
        probe_day, probe_dishes, semaphore, budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    probe_latency = time.monotonic() - probe_start
    probe_stats = probe_result.get("search_stats")
    if probe_stats is not None and getattr(probe_stats, "primary_error_type", None) in (
        "TimeoutError",
        "asyncio.TimeoutError",
    ):
        _recipe_search_disabled = True

    remaining_count = len(day_items) - 1
    remaining_budget = max(0.0, phase2_deadline_at - time.monotonic())
    per_day_budget = (
        remaining_budget / max(1, (remaining_count + _DAY_SEARCH_CONCURRENCY - 1) // _DAY_SEARCH_CONCURRENCY)
        if remaining_count > 0
        else remaining_budget
    )

    if not _recipe_search_disabled and per_day_budget > 0 and probe_latency > per_day_budget * 0.6:
        _recipe_search_disabled = True

    remaining_results = await asyncio.gather(
        *[
            _run_day(day, day_dishes, semaphore, budget_seconds=per_day_budget)
            for day, day_dishes in day_items[1:]
        ]
    )
    day_results = [probe_result, *remaining_results]

    products_by_day: dict[int, list[dict[str, Any]]] = {}
    for day_result in sorted(day_results, key=lambda row: int(row["day"])):
        products_by_day[int(day_result["day"])] = list(day_result["products"])
        pantry_filtered_all.update(day_result["pantry_filtered"])
        deferred_all.update(day_result["deferred"])
        stats.aggregated_ingredients_count += int(day_result["aggregated_count"])
        stats.prioritized_ingredients_count += int(day_result["prioritized_count"])
        stats.deferred_ingredients_count += int(day_result["deferred_count"])
        stats.pantry_filtered_count += len(day_result["pantry_filtered"])

        day_search_stats = day_result["search_stats"]
        if day_search_stats is None:
            stats.days.append(day_result["day_record"])
            continue

        day_products = list(day_result["products"])
        day_not_found = list(day_result["not_found"])
        used_chunk_fallback = bool(day_result["used_chunk_fallback"])
        stats.searched_day_count += 1
        stats.primary_attempted = stats.primary_attempted or bool(
            getattr(day_search_stats, "primary_attempted", False)
        )
        stats.primary_products_count += int(getattr(day_search_stats, "primary_products_count", 0))
        stats.primary_not_found_count += int(
            getattr(day_search_stats, "primary_not_found_count", 0)
        )
        stats.used_chunk_fallback = stats.used_chunk_fallback or used_chunk_fallback
        day_fallback_reason = str(getattr(day_search_stats, "fallback_reason", "")).strip()
        if day_fallback_reason and day_fallback_reason not in stats.fallback_reasons:
            stats.fallback_reasons.append(day_fallback_reason)
        stats.chunk_count += int(getattr(day_search_stats, "chunk_count", 0))
        stats.chunk_products_count += int(getattr(day_search_stats, "chunk_products_count", 0))
        stats.chunk_not_found_count += int(getattr(day_search_stats, "chunk_not_found_count", 0))
        stats.chunk_failure_count += int(getattr(day_search_stats, "chunk_failure_count", 0))
        stats.local_fallback_chunk_count += int(
            getattr(day_search_stats, "local_fallback_chunk_count", 0)
        )
        stats.local_fallback_products_count += int(
            getattr(day_search_stats, "local_fallback_products_count", 0)
        )
        stats.local_fallback_not_found_count += int(
            getattr(day_search_stats, "local_fallback_not_found_count", 0)
        )
        if not stats.primary_error_type:
            stats.primary_error_type = getattr(day_search_stats, "primary_error_type", None)
        if not stats.primary_error_message:
            stats.primary_error_message = getattr(day_search_stats, "primary_error_message", None)
        for row in list(getattr(day_search_stats, "chunk_sample_failures", []) or []):
            if len(stats.chunk_sample_failures) >= 5:
                break
            stats.chunk_sample_failures.append(row)
        all_products.extend(day_products)
        for item in day_not_found:
            if item not in all_not_found:
                all_not_found.append(item)
        stats.days.append(day_result["day_record"])

    if len(stats.fallback_reasons) == 1:
        stats.fallback_reason = stats.fallback_reasons[0]
    elif len(stats.fallback_reasons) > 1:
        stats.fallback_reason = "day_by_day_mixed"

    merged_products = merge_products(all_products)

    found_names_lower = {
        str(p.get("name", "")).strip().lower()
        for p in merged_products
        if str(p.get("name", "")).strip()
    }
    deduplicated_not_found = [
        item for item in all_not_found if item.strip().lower() not in found_names_lower
    ]

    stats.final_products_count = len(merged_products)
    stats.final_not_found_count = len(deduplicated_not_found)
    return (
        merged_products,
        deduplicated_not_found,
        stats.used_chunk_fallback,
        stats,
        sorted(pantry_filtered_all),
        sorted(deferred_all),
        products_by_day,
    )
