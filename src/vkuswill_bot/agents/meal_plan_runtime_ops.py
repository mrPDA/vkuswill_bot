"""Helper ops for meal-plan executor runtime."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.agents.meal_plan_quality import calculate_soft_coverage
from vkuswill_bot.agents.meal_plan_types import MealPlanDish, MealPlanRequest
from vkuswill_bot.agents.recipe_pantry import detect_pantry_tag_for_ingredient

_MEAL_PLAN_SEARCH_MAX_INGREDIENTS = 30
_LOW_PRIORITY_SEARCH_MARKERS = (
    "масло",
    "соус",
    "уксус",
    "трав",
    "розмарин",
    "базилик",
    "укроп",
    "петруш",
    "кориц",
    "харисс",
    "лавров",
    "орех",
    "семен",
    "чиа",
    "кокос",
    "мед",
    "мёд",
    "кунжут",
)


def extract_ingredients(tool_result: str) -> list[dict[str, Any]]:
    payload = parse_json_payload(tool_result)
    if not isinstance(payload, dict):
        return []
    ingredients = payload.get("ingredients")
    if not isinstance(ingredients, list):
        data = payload.get("data")
        if isinstance(data, dict):
            ingredients = data.get("ingredients")
    if not isinstance(ingredients, list):
        return []
    result: list[dict[str, Any]] = []
    for row in ingredients:
        if not isinstance(row, dict):
            continue
        query = str(row.get("search_query", "")).strip()
        if not query:
            query = str(row.get("name", "")).strip()
        if not query:
            continue
        quantity_raw = row.get("quantity")
        if isinstance(quantity_raw, bool):
            continue
        try:
            quantity = float(quantity_raw)
        except (TypeError, ValueError):
            quantity = 1.0
        if quantity <= 0:
            quantity = 1.0
        unit = str(row.get("unit", "шт")).strip() or "шт"
        result.append(
            {
                "name": str(row.get("name", query)).strip() or query,
                "search_query": query,
                "quantity": quantity,
                "unit": unit,
            }
        )
    return result


def aggregate_ingredients(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        query = str(item.get("search_query", "")).strip().lower()
        unit = str(item.get("unit", "шт")).strip().lower()
        if not query:
            continue
        key = (query, unit)
        quantity = item.get("quantity", 1.0)
        try:
            amount = float(quantity)
        except (TypeError, ValueError):
            amount = 1.0
        if amount <= 0:
            amount = 1.0
        if key not in bucket:
            bucket[key] = {
                "name": str(item.get("name", query)).strip() or query,
                "search_query": query,
                "quantity": 0.0,
                "unit": unit or "шт",
            }
        bucket[key]["quantity"] += amount
    return list(bucket.values())


def filter_pantry_ingredients_for_search(
    *,
    items: list[dict[str, Any]],
    explicit_pantry_requests: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    filtered: list[dict[str, Any]] = []
    removed: list[str] = []
    for item in items:
        pantry_tag = detect_pantry_tag_for_ingredient(item)
        if pantry_tag and pantry_tag not in explicit_pantry_requests:
            name = str(item.get("name", "")).strip() or str(item.get("search_query", "")).strip()
            if name:
                removed.append(name)
            continue
        filtered.append(item)
    return filtered, sorted({name for name in removed if name})


def prioritize_ingredients_for_search(
    *,
    items: list[dict[str, Any]],
    max_items: int = _MEAL_PLAN_SEARCH_MAX_INGREDIENTS,
) -> tuple[list[dict[str, Any]], list[str]]:
    if len(items) <= max(1, max_items):
        return list(items), []

    def _text(item: dict[str, Any]) -> str:
        name = str(item.get("name", "")).strip().lower().replace("ё", "е")
        query = str(item.get("search_query", "")).strip().lower().replace("ё", "е")
        return f"{name} {query}".strip()

    ranked = []
    for index, item in enumerate(items):
        text = _text(item)
        unit = str(item.get("unit", "")).strip().lower()
        low_priority = "по вкусу" in unit or any(
            marker in text for marker in _LOW_PRIORITY_SEARCH_MARKERS
        )
        try:
            quantity = float(item.get("quantity", 1.0))
        except (TypeError, ValueError):
            quantity = 1.0
        if quantity <= 0:
            quantity = 1.0
        ranked.append((1 if low_priority else 0, -quantity, index, item))

    ranked.sort()
    selected = [item for _, _, _, item in ranked[:max_items]]
    deferred = sorted(
        {
            str(item.get("name", "")).strip() or str(item.get("search_query", "")).strip()
            for _, _, _, item in ranked[max_items:]
            if str(item.get("name", "")).strip() or str(item.get("search_query", "")).strip()
        }
    )
    return selected, deferred


def extract_products_from_recipe_search(tool_result: str) -> tuple[list[dict[str, Any]], list[str]]:
    payload = parse_json_payload(tool_result)
    if not isinstance(payload, dict):
        return [], []
    found: list[dict[str, Any]] = []
    data = payload.get("data")
    data_found = data.get("found") if isinstance(data, dict) else None
    if isinstance(data_found, list):
        for row in data_found:
            if not isinstance(row, dict):
                continue
            item = row.get("item")
            if isinstance(item, dict) and item.get("xml_id") is not None:
                found.append(
                    {
                        "xml_id": item.get("xml_id"),
                        "suggested_q": row.get("suggested_q"),
                        "name": item.get("name"),
                        "category": item.get("category"),
                    }
                )
                continue
            found.append(row)
    raw_found = payload.get("found")
    if isinstance(raw_found, list):
        found.extend(row for row in raw_found if isinstance(row, dict))
    if not found:
        results = payload.get("results")
        if isinstance(results, list):
            for row in results:
                if not isinstance(row, dict):
                    continue
                best_match = row.get("best_match")
                if isinstance(best_match, dict):
                    found.append(
                        {
                            "xml_id": best_match.get("xml_id"),
                            "suggested_q": best_match.get("suggested_q"),
                            "name": best_match.get("name"),
                            "category": best_match.get("category"),
                        }
                    )
    products: list[dict[str, Any]] = []
    for row in found:
        if not isinstance(row, dict):
            continue
        xml_id_raw = row.get("xml_id")
        if isinstance(xml_id_raw, bool):
            continue
        try:
            xml_id = int(xml_id_raw)
        except (TypeError, ValueError):
            continue
        suggested = row.get("suggested_q", 1.0)
        try:
            quantity = float(suggested)
        except (TypeError, ValueError):
            quantity = 1.0
        if quantity <= 0:
            quantity = 1.0
        name = str(row.get("name", "")).strip()
        category = str(row.get("category", "")).strip()
        products.append(
            {
                "xml_id": xml_id,
                "q": quantity,
                "name": name if name else "",
                "category": category if category else "",
            }
        )

    not_found_raw = payload.get("not_found")
    if not isinstance(not_found_raw, list) and isinstance(data, dict):
        not_found_raw = data.get("not_found")
    not_found = (
        [str(item).strip() for item in not_found_raw if str(item).strip()]
        if isinstance(not_found_raw, list)
        else []
    )
    return products, not_found


def merge_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for row in products:
        xml_id_raw = row.get("xml_id")
        if isinstance(xml_id_raw, bool):
            continue
        try:
            xml_id = int(xml_id_raw)
        except (TypeError, ValueError):
            continue
        q_raw = row.get("q", 1.0)
        try:
            quantity = float(q_raw)
        except (TypeError, ValueError):
            quantity = 1.0
        if quantity <= 0:
            quantity = 1.0
        entry = merged.setdefault(
            xml_id,
            {
                "xml_id": xml_id,
                "q": 0.0,
                "name": str(row.get("name", "")).strip(),
                "category": str(row.get("category", "")).strip(),
            },
        )
        entry["q"] += quantity
        if not entry.get("name"):
            entry["name"] = str(row.get("name", "")).strip()
        if not entry.get("category"):
            entry["category"] = str(row.get("category", "")).strip()
    return list(merged.values())


def request_payload_for_renderer(
    request: MealPlanRequest,
    *,
    hard_constraints_passed: bool | None = None,
) -> dict[str, Any]:
    allergens: list[str] = []
    for group in request.groups:
        raw = group.hard_constraints.get("allergens_excluded")
        if isinstance(raw, list):
            for item in raw:
                value = str(item).strip().lower()
                if value and value not in allergens:
                    allergens.append(value)
    groups_payload: list[dict[str, Any]] = []
    for group in request.groups:
        row: dict[str, Any] = {
            "id": group.id,
            "count": group.count,
            "hard_constraints": dict(group.hard_constraints),
            "soft_preferences": dict(group.soft_preferences),
        }
        if group.id.startswith("child_") and group.id.endswith("y"):
            age_text = group.id.removeprefix("child_").removesuffix("y")
            if age_text.isdigit():
                row["age_years"] = int(age_text)
        groups_payload.append(row)
    source_counts: dict[str, int] = {}
    for item in request.preferences_trace:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "")).strip().lower()
        if not source:
            continue
        source_counts[source] = source_counts.get(source, 0) + 1
    applied_trace = [row for row in request.applied_preferences_trace if isinstance(row, dict)]
    applied_summary = {
        "total": len(applied_trace),
        "applied": sum(1 for row in applied_trace if row.get("applied") is True),
        "not_applied": sum(1 for row in applied_trace if row.get("applied") is False),
    }
    payload = {
        "days": request.days,
        "people_total": request.people_total,
        "groups": groups_payload,
        "allergens": allergens,
        "operational_preferences": dict(request.operational_preferences),
        "preferences_trace": list(request.preferences_trace),
        "applied_preferences_trace": applied_trace,
        "applied_preferences_summary": applied_summary,
        "preference_sources": source_counts,
    }
    if isinstance(hard_constraints_passed, bool):
        payload["hard_constraints_passed"] = hard_constraints_passed
    return payload


def soft_coverage_for_renderer(
    *,
    request: MealPlanRequest,
    dishes: list[MealPlanDish],
) -> dict[str, float]:
    """Compute per-group soft-preference coverage for deterministic renderer."""
    return calculate_soft_coverage(request=request, dishes=dishes)
