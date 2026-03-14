"""Deterministic meal-plan response renderer (Response Contract v1)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from vkuswill_bot.agents.meal_plan_response_contract_builder import (
    build_meal_plan_response_contract_v1,
)
from vkuswill_bot.agents.meal_plan_response_contract_model import (
    ContractCartProduct,
    MealPlanResponseContractV1,
)

_SLOT_LABELS = {
    "breakfast": "Завтрак",
    "snack_1": "Перекус",
    "lunch": "Обед",
    "snack_2": "Полдник",
    "dinner": "Ужин",
    "snack_3": "Поздний перекус",
}


def _has_multiple_groups(contract: MealPlanResponseContractV1) -> bool:
    return len(contract.request_summary.groups) > 1


def _format_hard_constraints(contract: MealPlanResponseContractV1) -> str:
    if not contract.request_summary.hard_constraints:
        return ""
    return ", ".join(contract.request_summary.hard_constraints)


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
    """Render user-friendly meal-plan response."""
    contract = build_meal_plan_response_contract_v1(
        history=history,
        request_payload=request_payload,
        structured_dishes=structured_dishes,
        cart_data=cart_data,
        user_preference_profile=user_preference_profile,
        soft_coverage_by_group=soft_coverage_by_group,
        fallback_message=fallback_message,
    )
    multi_group = _has_multiple_groups(contract)
    plan_not_formed = all(not slot.dishes for day in contract.weekly_plan for slot in day.slots)

    lines: list[str] = []

    # --- Header ---
    lines.append(
        f"<b>🍽 План питания на {contract.request_summary.days} дн. "
        f"для {contract.request_summary.people_total} чел.</b>"
    )

    constraints_text = _format_hard_constraints(contract)
    if constraints_text:
        lines.append(f"Ограничения: {constraints_text}")

    if multi_group:
        parts = []
        for adaptation in contract.group_adaptations:
            rules = ", ".join(adaptation.rules_applied) if adaptation.rules_applied else None
            if rules:
                parts.append(f"{adaptation.group_id}: {rules}")
        if parts:
            lines.append("Адаптации: " + "; ".join(parts))

    # --- Meal plan ---
    if plan_not_formed:
        reason = (
            contract.fallback_message
            if contract.fallback_message
            else "не удалось сформировать план."
        )
        lines.extend(["", f"⚠️ План не сформирован: {reason}"])
    else:
        for day in contract.weekly_plan:
            day_has_dishes = any(slot.dishes for slot in day.slots)
            if not day_has_dishes:
                continue
            lines.append(f"\n<b>День {day.day}</b>")
            for slot in day.slots:
                if not slot.dishes:
                    continue
                slot_label = _SLOT_LABELS.get(slot.meal_type, slot.meal_type)
                for dish in slot.dishes:
                    if multi_group and dish.audience_groups:
                        audience = ", ".join(dish.audience_groups)
                        lines.append(f"  {slot_label}: {dish.name} [{audience}]")
                    else:
                        lines.append(f"  {slot_label}: {dish.name}")

    # --- Cart ---
    cart = contract.cart_summary
    items_count = (
        str(cart.items_count)
        if isinstance(cart.items_count, int)
        else "н/д"
    )

    cart_groups = cart.groups
    if len(cart_groups) > 1:
        lines.append(f"\n<b>🛒 Корзины ВкусВилл ({items_count} товаров)</b>")
        for g in cart_groups:
            link_text = g.link if g.link else "не создана"
            lines.append(f"  {g.day_label} ({g.items_count} товаров): {link_text}")
    elif cart.link and cart.link != "не сформирована":
        lines.append(f"\n<b>🛒 Корзина ВкусВилл ({items_count} товаров)</b>")
        lines.append(cart.link)
    else:
        lines.append(f"\n<b>🛒 Корзина</b> ({items_count} товаров)")
        lines.append("Ссылка: не сформирована")
        if cart.products:
            by_category: dict[str, list[ContractCartProduct]] = defaultdict(list)
            for row in cart.products:
                by_category[row.category].append(row)
            lines.append("")
            lines.append("Список товаров:")
            for category in sorted(by_category):
                for row in by_category[category]:
                    lines.append(f"  • {row.name} x {row.quantity_text}")

    if cart.not_found:
        lines.append(f"\nНе нашли во ВкусВилл: {', '.join(cart.not_found)}")

    # --- Notes ---
    if contract.fallback_message and not plan_not_formed:
        lines.append(f"\n<i>{contract.fallback_message}</i>")
    for note in contract.notes:
        lines.append(f"\n<i>{note}</i>")

    return "\n".join(lines).strip()
