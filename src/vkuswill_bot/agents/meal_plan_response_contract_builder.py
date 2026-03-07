"""Builder for typed Meal Plan Response Contract v1 model."""

from __future__ import annotations
from collections import defaultdict
from typing import Any
from vkuswill_bot.agents.meal_plan_response_contract_model import (
    ContractConstraintsCheck,
    ContractDayPlan,
    ContractDaySlot,
    ContractGroupAdaptation,
    ContractRequestGroup,
    ContractRequestSummary,
    ContractSlotDish,
    MealPlanResponseContractV1,
)
from vkuswill_bot.agents.meal_plan_response_utils import (
    resolve_hard_constraints_passed,
    extract_allergens,
    build_contract_cart_summary,
    extract_child_group,
    extract_days,
    extract_people_total,
    latest_user_text,
)

_SLOT_BASE = ("breakfast", "lunch", "dinner")
_SLOT_CHILD = ("breakfast", "snack_1", "lunch", "snack_2", "dinner")
_SLOT_EXTENDED = ("breakfast", "snack_1", "lunch", "snack_2", "dinner", "snack_3")
_SLOT_LABELS = {
    "breakfast": "Завтрак",
    "snack_1": "Перекус 1",
    "lunch": "Обед",
    "snack_2": "Перекус 2",
    "dinner": "Ужин",
    "snack_3": "Перекус 3",
}


def _parse_request_payload(
    *,
    history: list[dict[str, Any]],
    request_payload: dict[str, Any] | None,
    user_preference_profile: dict[str, Any],
) -> tuple[
    int,
    int,
    list[ContractRequestGroup],
    list[dict[str, Any]],
    list[str],
    dict[str, int],
    dict[str, Any],
    dict[str, int],
    bool,
]:
    def _to_int(raw: Any, default: int, min_value: int, max_value: int) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(min_value, min(max_value, value))

    def _counter(raw: Any) -> dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        result: dict[str, int] = {}
        for key, value in raw.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            result[key_text] = parsed
        return result

    def _fallback_groups(
        text_value: str, total: int
    ) -> tuple[list[ContractRequestGroup], list[dict[str, Any]], bool]:
        child_group_id, child_count, child_age = extract_child_group(text_value, total)
        if child_group_id:
            rows: list[ContractRequestGroup] = []
            adults_count = max(0, total - child_count)
            if adults_count > 0:
                rows.append(ContractRequestGroup(id="adults", count=adults_count))
            rows.append(ContractRequestGroup(id=child_group_id, count=child_count))
            return (
                rows,
                [{"id": row.id, "count": row.count} for row in rows],
                bool(isinstance(child_age, int) and child_age <= 3),
            )
        row = ContractRequestGroup(id="all", count=total)
        return [row], [{"id": row.id, "count": row.count}], False

    text = latest_user_text(history)
    if not isinstance(request_payload, dict):
        days = extract_days(text)
        people_total = extract_people_total(text)
        groups, groups_raw, child_under_three = _fallback_groups(text, people_total)
        return (
            days,
            people_total,
            groups,
            groups_raw,
            extract_allergens(text, user_preference_profile),
            {},
            {},
            {},
            child_under_three,
        )

    days = _to_int(request_payload.get("days", 7), 7, 1, 31)
    people_total = _to_int(request_payload.get("people_total", 2), 2, 1, 100)
    groups_payload = (
        request_payload.get("groups") if isinstance(request_payload.get("groups"), list) else []
    )

    groups: list[ContractRequestGroup] = []
    normalized_groups_payload: list[dict[str, Any]] = []
    child_under_three = False
    for group in groups_payload:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("id", "")).strip()
        if not group_id:
            continue
        count = _to_int(group.get("count", 0), 0, 0, 100)
        if count <= 0:
            continue
        age = group.get("age_years")
        if group_id.startswith("child") and isinstance(age, int) and age <= 3:
            child_under_three = True
        groups.append(ContractRequestGroup(id=group_id, count=count))
        normalized_groups_payload.append(group)

    if not groups:
        groups, normalized_groups_payload, child_under_three = _fallback_groups(text, people_total)

    allergens = request_payload.get("allergens")
    allergens_list = (
        [str(item).strip().lower() for item in allergens if str(item).strip()]
        if isinstance(allergens, list)
        else extract_allergens(text, user_preference_profile)
    )
    preference_sources = _counter(request_payload.get("preference_sources", {}))
    operational = (
        request_payload.get("operational_preferences", {})
        if isinstance(request_payload.get("operational_preferences", {}), dict)
        else {}
    )
    applied_summary = _counter(request_payload.get("applied_preferences_summary", {}))
    return (
        days,
        people_total,
        groups,
        normalized_groups_payload,
        allergens_list,
        preference_sources,
        operational,
        applied_summary,
        child_under_three,
    )


def build_meal_plan_response_contract_v1(
    *,
    history: list[dict[str, Any]],
    request_payload: dict[str, Any] | None,
    structured_dishes: list[dict[str, Any]] | None,
    cart_data: dict[str, Any] | None,
    user_preference_profile: dict[str, Any],
    soft_coverage_by_group: dict[str, float] | None,
    fallback_message: str,
) -> MealPlanResponseContractV1:
    (
        days,
        people_total,
        groups,
        groups_raw,
        allergens,
        preference_sources,
        operational,
        applied_summary,
        child_under_three,
    ) = _parse_request_payload(
        history=history,
        request_payload=request_payload,
        user_preference_profile=user_preference_profile,
    )

    group_constraints: dict[str, dict[str, Any]] = {}
    hard_summary: list[str] = [f"allergens_excluded={','.join(allergens)}"] if allergens else []
    for group in groups_raw:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("id", "")).strip()
        if not group_id:
            continue
        hard = (
            group.get("hard_constraints") if isinstance(group.get("hard_constraints"), dict) else {}
        )
        soft = (
            group.get("soft_preferences") if isinstance(group.get("soft_preferences"), dict) else {}
        )
        group_constraints[group_id] = {"hard": hard, "soft": soft}
        for key, value in hard.items():
            if key != "allergens_excluded" and value:
                hard_summary.append(f"{group_id}.{key}={value}")

    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for dish in structured_dishes or []:
        if not isinstance(dish, dict):
            continue
        try:
            day = int(dish.get("day", 0))
        except (TypeError, ValueError):
            continue
        meal_type = str(dish.get("meal_type", "")).strip().lower()
        if 1 <= day <= days and meal_type in _SLOT_LABELS:
            grouped[(day, meal_type)].append(dish)
    present_snacks = {mt for _day, mt in grouped if mt.startswith("snack_")}
    if child_under_three:
        slots = list(_SLOT_EXTENDED if "snack_3" in present_snacks else _SLOT_CHILD)
    else:
        slots = [slot for slot in _SLOT_EXTENDED if slot in _SLOT_BASE or slot in present_snacks]

    weekly_plan: list[ContractDayPlan] = []
    for day in range(1, days + 1):
        day_slots: list[ContractDaySlot] = []
        for slot in slots:
            dishes_rows = []
            for row in grouped.get((day, slot), []):
                audience_raw = row.get("audience_groups")
                audience_groups = (
                    [str(item).strip() for item in audience_raw if str(item).strip()]
                    if isinstance(audience_raw, list)
                    else ["all"]
                )
                if not audience_groups:
                    audience_groups = ["all"]
                dishes_rows.append(
                    ContractSlotDish(
                        name=str(row.get("name", "Блюдо")).strip() or "Блюдо",
                        audience_groups=audience_groups,
                    )
                )
            day_slots.append(ContractDaySlot(meal_type=slot, dishes=dishes_rows))
        weekly_plan.append(ContractDayPlan(day=day, slots=day_slots))

    adaptations: list[ContractGroupAdaptation] = []
    for group_id, constraints in sorted(group_constraints.items()):
        rules = [f"{k}={v}" for k, v in constraints["hard"].items() if v]
        cuisines = constraints["soft"].get("cuisines")
        if cuisines:
            rules.append(f"cuisines={cuisines}")
        if group_id.startswith("child_"):
            rules.extend(["mild_spice", "soft_texture", "smaller_portion"])
        adaptations.append(ContractGroupAdaptation(group_id=group_id, rules_applied=rules))
    if not adaptations:
        if groups:
            adaptations.extend(
                ContractGroupAdaptation(group_id=group.id, rules_applied=[]) for group in groups
            )
        else:
            adaptations.append(ContractGroupAdaptation(group_id="all", rules_applied=[]))

    hard_constraints_passed = resolve_hard_constraints_passed(request_payload)

    coverage = soft_coverage_by_group if isinstance(soft_coverage_by_group, dict) else {}
    normalized_coverage: dict[str, float] = {}
    for key, value in coverage.items():
        try:
            normalized_coverage[str(key)] = float(value)
        except (TypeError, ValueError):
            continue

    return MealPlanResponseContractV1(
        schema_version=1,
        request_summary=ContractRequestSummary(
            days=days,
            people_total=people_total,
            groups=groups,
            hard_constraints=hard_summary,
            operational_preferences=operational,
            preference_sources=preference_sources,
            applied_preferences_summary=applied_summary,
        ),
        weekly_plan=weekly_plan,
        group_adaptations=adaptations,
        cart_summary=build_contract_cart_summary(cart_data),
        constraints_check=ContractConstraintsCheck(
            hard_constraints_passed=hard_constraints_passed,
            soft_coverage_by_group=normalized_coverage,
        ),
        notes=["План носит информационный характер и не является медицинской рекомендацией."],
        fallback_message=fallback_message.strip(),
    )
