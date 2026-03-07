"""Quality checks for generated meal plans."""

from __future__ import annotations

from collections.abc import Iterable

from vkuswill_bot.agents.meal_plan_hard_constraints import (
    build_applied_preferences_trace,
    validate_hard_constraints_with_ingredients,
    validate_hard_constraints_with_trace,
)
from vkuswill_bot.agents.meal_plan_types import MealPlanDish, MealPlanRequest

SOFT_COVERAGE_TARGET = 0.70


def _normalized_values(raw: object) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def calculate_soft_coverage(
    *,
    request: MealPlanRequest,
    dishes: Iterable[MealPlanDish],
) -> dict[str, float]:
    """Compute soft-preferences coverage by audience group.

    v1 supports coverage for ``soft_preferences.cuisines``.
    """
    by_group: dict[str, list[MealPlanDish]] = {group.id: [] for group in request.groups}
    for dish in dishes:
        for group_id in dish.audience_groups:
            if group_id in by_group:
                by_group[group_id].append(dish)

    coverage: dict[str, float] = {}
    for group in request.groups:
        preferred_cuisines = _normalized_values(group.soft_preferences.get("cuisines"))
        if not preferred_cuisines:
            coverage[group.id] = 1.0
            continue
        candidate = by_group.get(group.id, [])
        if not candidate:
            coverage[group.id] = 0.0
            continue
        matched = 0
        for dish in candidate:
            tags = {str(tag).strip().lower() for tag in dish.cuisine_tags if str(tag).strip()}
            if tags & preferred_cuisines:
                matched += 1
        coverage[group.id] = matched / max(1, len(candidate))
    return coverage


def validate_hard_constraints(
    *,
    request: MealPlanRequest,
    dishes: Iterable[MealPlanDish],
) -> list[str]:
    violations, _trace = validate_hard_constraints_with_trace(
        request=request,
        dishes=list(dishes),
    )
    return violations


def low_soft_coverage_groups(
    *,
    coverage_by_group: dict[str, float],
    target: float = SOFT_COVERAGE_TARGET,
) -> dict[str, float]:
    return {group_id: value for group_id, value in coverage_by_group.items() if value < target}


def format_soft_coverage_error(
    *,
    low_groups: dict[str, float],
    target: float = SOFT_COVERAGE_TARGET,
) -> str:
    details = ", ".join(f"{group_id}={coverage:.2f}" for group_id, coverage in sorted(low_groups.items()))
    return f"soft_preferences coverage < {target:.2f}: {details}"
