"""Deterministic meal-plan response renderer (Response Contract v1)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from vkuswill_bot.agents.meal_plan_response_contract_builder import (
    build_meal_plan_response_contract_v1,
)
from vkuswill_bot.agents.meal_plan_response_contract_model import ContractCartProduct

_SLOT_LABELS = {
    "breakfast": "Завтрак",
    "snack_1": "Перекус 1",
    "lunch": "Обед",
    "snack_2": "Перекус 2",
    "dinner": "Ужин",
    "snack_3": "Перекус 3",
}


def _groups_summary(groups: list[Any]) -> str:
    parts: list[str] = []
    for row in groups:
        group_id = str(getattr(row, "id", "")).strip()
        count = getattr(row, "count", None)
        if not group_id:
            continue
        if isinstance(count, int):
            parts.append(f"{group_id} ({count})")
        else:
            parts.append(group_id)
    return ", ".join(parts) if parts else "all"


def render_meal_plan_contract_response(
    *,
    history: list[dict[str, Any]],
    cart_data: dict[str, Any] | None,
    user_preference_profile: dict[str, Any],
    fallback_message: str = "",
    request_payload: dict[str, Any] | None = None,
    structured_dishes: list[dict[str, Any]] | None = None,
    soft_coverage_by_group: dict[str, float] | None = None,
) -> str:
    """Render deterministic meal-plan response by typed Response Contract v1."""
    contract = build_meal_plan_response_contract_v1(
        history=history,
        request_payload=request_payload,
        structured_dishes=structured_dishes,
        cart_data=cart_data,
        user_preference_profile=user_preference_profile,
        soft_coverage_by_group=soft_coverage_by_group,
        fallback_message=fallback_message,
    )
    plan_not_formed = all(not slot.dishes for day in contract.weekly_plan for slot in day.slots)
    plan_not_formed_reason = (
        contract.fallback_message
        if contract.fallback_message
        else "не удалось сформировать план из валидных данных."
    )

    lines = [
        "🍽 План питания",
        "",
        "Параметры запроса:",
        f"- Период: {contract.request_summary.days} дн.",
        f"- Людей: {contract.request_summary.people_total}",
        f"- Группы: {_groups_summary(contract.request_summary.groups)}",
        "- Жесткие ограничения: " + (
            ", ".join(contract.request_summary.hard_constraints)
            if contract.request_summary.hard_constraints
            else "не указаны явно"
        ),
    ]
    if contract.request_summary.operational_preferences:
        lines.append(f"- Операционные ограничения: {contract.request_summary.operational_preferences}")
    if contract.request_summary.preference_sources:
        pairs = ", ".join(f"{k}={v}" for k, v in sorted(contract.request_summary.preference_sources.items()))
        lines.append(f"- Источники предпочтений: {pairs}")
    if contract.request_summary.applied_preferences_summary:
        total = int(contract.request_summary.applied_preferences_summary.get("total", 0))
        applied = int(contract.request_summary.applied_preferences_summary.get("applied", 0))
        lines.append(f"- Примененные предпочтения: {applied}/{total}")

    lines.extend(["", "План по дням:"])
    if plan_not_formed:
        lines.append("- Статус: план не сформирован")
        lines.append(f"- Причина: {plan_not_formed_reason}")
    for day in contract.weekly_plan:
        lines.append(f"День {day.day}:")
        for slot in day.slots:
            slot_label = _SLOT_LABELS.get(slot.meal_type, slot.meal_type)
            if not slot.dishes:
                lines.append(f"- {slot_label}: (без назначения)")
                continue
            for dish in slot.dishes:
                audience = ",".join(dish.audience_groups) if dish.audience_groups else "all"
                lines.append(f"- {slot_label}: {dish.name} [{audience}]")

    lines.extend(["", "Адаптации по группам:"])
    for adaptation in contract.group_adaptations:
        rules = ", ".join(adaptation.rules_applied) if adaptation.rules_applied else "базовое меню"
        lines.append(f"- {adaptation.group_id}: {rules}")

    items_count = (
        str(contract.cart_summary.items_count)
        if isinstance(contract.cart_summary.items_count, int)
        else "н/д"
    )
    total_text = contract.cart_summary.total_text.strip() or (
        f"Итого: {contract.cart_summary.total_rub} руб"
        if isinstance(contract.cart_summary.total_rub, int)
        else "н/д"
    )
    not_found_text = (
        ", ".join(contract.cart_summary.not_found) if contract.cart_summary.not_found else "нет"
    )

    lines.extend(["", "Корзина:"])
    lines.append(f"- Товаров: {items_count}")
    lines.append(f"- Сумма: {total_text}")
    lines.append(f"- Ссылка: {contract.cart_summary.link}")
    lines.append(f"- Не найдено: {not_found_text}")

    if contract.cart_summary.link == "не сформирована" and contract.cart_summary.products:
        by_category: dict[str, list[ContractCartProduct]] = defaultdict(list)
        for row in contract.cart_summary.products:
            by_category[row.category].append(row)
        lines.extend(["", "Список товаров (без ссылки):"])
        for category in sorted(by_category):
            lines.append(f"- Категория: {category}")
            for row in by_category[category]:
                lines.append(f"  - {row.name} x {row.quantity_text}")

    lines.extend(["", "Проверка ограничений:"])
    lines.append(
        "- hard_constraints: "
        + ("соблюдены" if contract.constraints_check.hard_constraints_passed else "нарушения обнаружены")
    )
    if contract.constraints_check.soft_coverage_by_group:
        coverage = ", ".join(
            f"{group_id}={value:.2f}"
            for group_id, value in sorted(contract.constraints_check.soft_coverage_by_group.items())
        )
        lines.append(f"- soft_preferences_coverage: {coverage}")
        low = [
            group_id
            for group_id, value in contract.constraints_check.soft_coverage_by_group.items()
            if value < 0.70
        ]
        soft_status = (
            "below target for groups: " + ", ".join(sorted(low))
            if low
            else "coverage target >= 0.70 достигнут"
        )
    else:
        soft_status = "best-effort (coverage не рассчитан в этом ответе)"
    lines.append(f"- soft_preferences: {soft_status}")

    lines.extend(["", "Примечания:"])
    if contract.fallback_message and not plan_not_formed:
        lines.append(f"- Дополнительно от модели: {contract.fallback_message}")
    for note in contract.notes:
        lines.append(f"- {note}")
    return "\n".join(lines).strip()
