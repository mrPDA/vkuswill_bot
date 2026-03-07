"""Hard-constraint validation and applied trace for meal plans."""

from __future__ import annotations

import re
from typing import Any

from vkuswill_bot.agents.meal_plan_types import MealPlanDish, MealPlanRequest

_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-Я0-9_]+")
_DIET_MARKERS: dict[str, tuple[str, ...]] = {
    "vegan": ("vegan", "веган", "plant based", "plant-based", "plant_based"),
    "vegetarian": ("vegetarian", "вегетариан", "без мяса"),
    "halal": ("halal", "халяль", "халал"),
    "default": ("default", "обыч", "стандарт", "omnivore", "омнивор"),
}
_DIET_FORBIDDEN = {
    "vegan": {
        "говяд",
        "свинин",
        "куриц",
        "курин",
        "индейк",
        "рыб",
        "лосос",
        "тунец",
        "яйц",
        "сыр",
        "творог",
        "йогурт",
        "молок",
        "слив",
    },
    "vegetarian": {"говяд", "свинин", "куриц", "курин", "индейк", "рыб", "лосос", "тунец"},
}
_PORK_TERMS = {"свинин", "бекон", "ветчин", "pork", "ham", "bacon"}
_ALLERGEN_ALIASES = {
    "nuts": {"орех", "миндаль", "фундук", "арахис", "кешью", "грецк"},
    "орехи": {"орех", "миндаль", "фундук", "арахис", "кешью", "грецк"},
    "lactose": {"молок", "слив", "сыр", "творог", "йогурт"},
}
_PHASE1 = "phase1_generation"
_PHASE2 = "phase2_ingredients"


def _normalized_values(raw: object) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def _dish_terms(dish: MealPlanDish) -> set[str]:
    merged = " ".join([dish.name, *dish.cuisine_tags]).lower()
    return {token for token in _TOKEN_RE.findall(merged) if token}


def _dish_key(name: str) -> str:
    return str(name).strip().lower()


def _contains_any(terms: set[str], markers: set[str]) -> bool:
    return any(any(marker in term for marker in markers) for term in terms)


def _is_truthy(value: Any) -> bool:
    if value is True:
        return True
    return str(value).strip().lower() in {"1", "true", "yes", "да"}


def _ingredient_terms(rows: list[dict[str, Any]]) -> set[str]:
    terms: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        merged = " ".join(
            [str(row.get("name", "")).strip(), str(row.get("search_query", "")).strip()]
        ).lower()
        terms.update(token for token in _TOKEN_RE.findall(merged) if token)
    return terms


def _canonicalize_diet(value: Any) -> str:
    low = " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())
    if not low:
        return ""
    for canonical, markers in _DIET_MARKERS.items():
        if any(marker in low for marker in markers):
            return canonical
    return low


def _sources_for_group_field(*, request: MealPlanRequest, group_id: str, field: str) -> list[str]:
    sources: set[str] = set()
    global_suffix = field
    group_suffix = f"groups.{group_id}.{field}"
    for row in request.preferences_trace:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source", "")).strip().lower()
        row_field = str(row.get("field", "")).strip()
        if not source or not row_field:
            continue
        if row_field == group_suffix or row_field.endswith(f".{group_suffix}"):
            sources.add(source)
            continue
        if row_field == global_suffix or row_field.endswith(f".{global_suffix}"):
            sources.add(source)
    return sorted(sources)


def _append_trace(
    *,
    trace: list[dict[str, Any]],
    stage: str,
    group_id: str,
    field: str,
    value: Any,
    applied: bool,
    reason: str,
    dish_name: str | None,
) -> None:
    row: dict[str, Any] = {
        "type": "applied_preference",
        "stage": stage,
        "scope": "dish_group" if dish_name else "group",
        "group_id": group_id,
        "field": field,
        "value": value,
        "applied": applied,
        "reason": reason,
    }
    if dish_name:
        row["dish"] = dish_name
    trace.append(row)


def validate_hard_constraints_with_trace(
    *,
    request: MealPlanRequest,
    dishes: list[MealPlanDish],
) -> tuple[list[str], list[dict[str, Any]]]:
    by_group = {group.id: group for group in request.groups}
    violations: list[str] = []
    trace: list[dict[str, Any]] = []

    for dish in dishes:
        terms = _dish_terms(dish)
        for group_id in dish.audience_groups:
            group = by_group.get(group_id)
            if group is None:
                continue
            hard = group.hard_constraints if isinstance(group.hard_constraints, dict) else {}

            diet = _canonicalize_diet(hard.get("diet"))
            if diet:
                forbidden = _DIET_FORBIDDEN.get(diet, set())
                applied = not (forbidden and _contains_any(terms, forbidden))
                if not applied:
                    violations.append(f"{dish.name}: diet={diet} нарушен для {group_id}")
                _append_trace(
                    trace=trace,
                    stage=_PHASE1,
                    group_id=group_id,
                    field="hard_constraints.diet",
                    value=diet,
                    applied=applied,
                    reason=("matched dish tokens" if applied else "forbidden token matched"),
                    dish_name=dish.name,
                )

            if _is_truthy(hard.get("no_pork")):
                applied = not _contains_any(terms, _PORK_TERMS)
                if not applied:
                    violations.append(f"{dish.name}: no_pork нарушен для {group_id}")
                _append_trace(
                    trace=trace,
                    stage=_PHASE1,
                    group_id=group_id,
                    field="hard_constraints.no_pork",
                    value=True,
                    applied=applied,
                    reason=("matched dish tokens" if applied else "pork token matched"),
                    dish_name=dish.name,
                )

            allergens = hard.get("allergens_excluded")
            if isinstance(allergens, list):
                for allergen in allergens:
                    low = str(allergen).strip().lower()
                    if not low:
                        continue
                    markers = _ALLERGEN_ALIASES.get(low, {low})
                    applied = not _contains_any(terms, markers)
                    if not applied:
                        violations.append(f"{dish.name}: аллерген {low} нарушен для {group_id}")
                    _append_trace(
                        trace=trace,
                        stage=_PHASE1,
                        group_id=group_id,
                        field="hard_constraints.allergens_excluded",
                        value=low,
                        applied=applied,
                        reason=("matched dish tokens" if applied else "allergen token matched"),
                        dish_name=dish.name,
                    )

    return violations, trace


def validate_hard_constraints_with_ingredients(
    *,
    request: MealPlanRequest,
    dishes: list[MealPlanDish],
    ingredients_by_dish: dict[str, list[dict[str, Any]]],
) -> tuple[list[str], list[dict[str, Any]]]:
    by_group = {group.id: group for group in request.groups}
    violations: list[str] = []
    trace: list[dict[str, Any]] = []

    for dish in dishes:
        key = _dish_key(dish.name)
        terms = _ingredient_terms(ingredients_by_dish.get(key, []))
        for group_id in dish.audience_groups:
            group = by_group.get(group_id)
            if group is None:
                continue
            hard = group.hard_constraints if isinstance(group.hard_constraints, dict) else {}
            if not hard:
                continue

            if not terms:
                violations.append(
                    f"{dish.name}: ingredient-level validation unavailable for {group_id}"
                )
                for field, value in hard.items():
                    _append_trace(
                        trace=trace,
                        stage=_PHASE2,
                        group_id=group_id,
                        field=f"hard_constraints.{field}",
                        value=value,
                        applied=False,
                        reason="missing ingredient data",
                        dish_name=dish.name,
                    )
                continue

            diet = _canonicalize_diet(hard.get("diet"))
            if diet:
                forbidden = _DIET_FORBIDDEN.get(diet, set())
                applied = not (forbidden and _contains_any(terms, forbidden))
                if not applied:
                    violations.append(
                        f"{dish.name}: diet={diet} нарушен для {group_id} (ingredients)"
                    )
                _append_trace(
                    trace=trace,
                    stage=_PHASE2,
                    group_id=group_id,
                    field="hard_constraints.diet",
                    value=diet,
                    applied=applied,
                    reason=(
                        "ingredient-level matched"
                        if applied
                        else "forbidden ingredient token matched"
                    ),
                    dish_name=dish.name,
                )

            if _is_truthy(hard.get("no_pork")):
                applied = not _contains_any(terms, _PORK_TERMS)
                if not applied:
                    violations.append(f"{dish.name}: no_pork нарушен для {group_id} (ingredients)")
                _append_trace(
                    trace=trace,
                    stage=_PHASE2,
                    group_id=group_id,
                    field="hard_constraints.no_pork",
                    value=True,
                    applied=applied,
                    reason=(
                        "ingredient-level matched" if applied else "pork ingredient token matched"
                    ),
                    dish_name=dish.name,
                )

            allergens = hard.get("allergens_excluded")
            if isinstance(allergens, list):
                for allergen in allergens:
                    low = str(allergen).strip().lower()
                    if not low:
                        continue
                    markers = _ALLERGEN_ALIASES.get(low, {low})
                    applied = not _contains_any(terms, markers)
                    if not applied:
                        violations.append(
                            f"{dish.name}: аллерген {low} нарушен для {group_id} (ingredients)"
                        )
                    _append_trace(
                        trace=trace,
                        stage=_PHASE2,
                        group_id=group_id,
                        field="hard_constraints.allergens_excluded",
                        value=low,
                        applied=applied,
                        reason=(
                            "ingredient-level matched"
                            if applied
                            else "allergen ingredient token matched"
                        ),
                        dish_name=dish.name,
                    )

    return violations, trace


def build_applied_preferences_trace(
    *,
    request: MealPlanRequest,
    phase1_applied_trace: list[dict[str, Any]] | None = None,
    phase2_applied_trace: list[dict[str, Any]] | None = None,
    soft_coverage_by_group: dict[str, float] | None = None,
    soft_coverage_target: float = 0.70,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for row in [*(phase1_applied_trace or []), *(phase2_applied_trace or [])]:
        if not isinstance(row, dict):
            continue
        group_id = str(row.get("group_id", "")).strip()
        field = str(row.get("field", "")).strip()
        if group_id and field:
            row = {
                **row,
                "sources": _sources_for_group_field(
                    request=request, group_id=group_id, field=field
                ),
            }
        merged.append(row)

    if isinstance(soft_coverage_by_group, dict):
        by_group = {group.id: group for group in request.groups}
        for group_id, coverage in sorted(soft_coverage_by_group.items()):
            group = by_group.get(group_id)
            if group is None:
                continue
            cuisines = _normalized_values(group.soft_preferences.get("cuisines"))
            if not cuisines:
                continue
            merged.append(
                {
                    "type": "applied_preference",
                    "stage": _PHASE1,
                    "scope": "group",
                    "group_id": group_id,
                    "field": "soft_preferences.cuisines",
                    "value": sorted(cuisines),
                    "applied": coverage >= soft_coverage_target,
                    "coverage": coverage,
                    "coverage_target": soft_coverage_target,
                    "sources": _sources_for_group_field(
                        request=request, group_id=group_id, field="soft_preferences.cuisines"
                    ),
                }
            )
    return merged
