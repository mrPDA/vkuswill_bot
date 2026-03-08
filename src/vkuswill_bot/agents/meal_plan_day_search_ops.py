"""Day-by-day product selection for meal-plan executor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from vkuswill_bot.agents.meal_plan_recipe_search_ops import search_products
from vkuswill_bot.agents.meal_plan_runtime_policy import deadline_after, deadline_remaining
from vkuswill_bot.agents.meal_plan_runtime_ops import merge_products
from vkuswill_bot.agents.meal_plan_trace_ops import finish_search_span, start_span

FilterPantryFn = Callable[..., tuple[list[dict[str, Any]], list[str]]]
AggregateIngredientsFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
PrioritizeIngredientsFn = Callable[..., tuple[list[dict[str, Any]], list[str]]]


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


def _allocate_day_deadline(*, overall_deadline_at: float, remaining_days: int) -> float:
    remaining_seconds = deadline_remaining(overall_deadline_at)
    if remaining_days <= 1:
        return overall_deadline_at
    per_day_budget = max(3.0, remaining_seconds / max(1, remaining_days))
    return min(overall_deadline_at, deadline_after(per_day_budget))


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
) -> tuple[list[dict[str, Any]], list[str], bool, MealPlanDaySearchStats, list[str], list[str]]:
    grouped_days = _group_dishes_by_day(dishes_payload)
    stats = MealPlanDaySearchStats(day_count=len(grouped_days))
    all_products: list[dict[str, Any]] = []
    all_not_found: list[str] = []
    pantry_filtered_all: set[str] = set()
    deferred_all: set[str] = set()

    day_items = sorted(grouped_days.items())
    for index, (day, day_dishes) in enumerate(day_items):
        day_deadline_at = _allocate_day_deadline(
            overall_deadline_at=phase2_deadline_at,
            remaining_days=len(day_items) - index,
        )
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
        pantry_filtered_all.update(pantry_filtered)
        deferred_all.update(deferred)
        stats.aggregated_ingredients_count += len(aggregated)
        stats.prioritized_ingredients_count += len(prioritized)
        stats.deferred_ingredients_count += len(deferred)
        stats.pantry_filtered_count += len(pantry_filtered)

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
            stats.days.append(
                {
                    "day": day,
                    "dishes": [str(dish.get("name", "")).strip() for dish in day_dishes],
                    "aggregated_ingredients_count": len(aggregated),
                    "prioritized_ingredients_count": 0,
                    "deferred_ingredients_count": len(deferred),
                    "products_count": 0,
                    "not_found_count": 0,
                    "used_chunk_fallback": False,
                }
            )
            if day_span is not None:
                day_span.end(
                    output={
                        "products_count": 0,
                        "not_found_count": 0,
                        "used_chunk_fallback": False,
                        "stats": {"search_skipped": True},
                    },
                    level="WARNING",
                    status_message="meal_plan_day_search_skipped",
                )
            continue

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
            prefer_local_only=True,
        )
        finish_search_span(
            span=day_span,
            products=day_products,
            not_found=day_not_found,
            used_chunk_fallback=used_chunk_fallback,
            stats=day_search_stats,
        )
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
        stats.days.append(
            {
                "day": day,
                "dishes": [str(dish.get("name", "")).strip() for dish in day_dishes],
                "aggregated_ingredients_count": len(aggregated),
                "prioritized_ingredients_count": len(prioritized),
                "deferred_ingredients_count": len(deferred),
                "products_count": len(day_products),
                "not_found_count": len(day_not_found),
                "used_chunk_fallback": used_chunk_fallback,
                "search_stats": day_search_stats.as_dict(),
            }
        )

    if len(stats.fallback_reasons) == 1:
        stats.fallback_reason = stats.fallback_reasons[0]
    elif len(stats.fallback_reasons) > 1:
        stats.fallback_reason = "day_by_day_mixed"

    merged_products = merge_products(all_products)
    stats.final_products_count = len(merged_products)
    stats.final_not_found_count = len(all_not_found)
    return (
        merged_products,
        all_not_found,
        stats.used_chunk_fallback,
        stats,
        sorted(pantry_filtered_all),
        sorted(deferred_all),
    )
