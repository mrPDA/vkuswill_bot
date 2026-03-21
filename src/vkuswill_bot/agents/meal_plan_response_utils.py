"""Utility helpers for deterministic meal-plan response rendering."""

from __future__ import annotations

import json
import re
from typing import Any

from vkuswill_bot.agents.meal_plan_types import parse_request_days
from vkuswill_bot.agents.meal_plan_response_contract_model import (
    ContractCartGroup,
    ContractCartProduct,
    ContractCartSummary,
)

_PEOPLE_RE = re.compile(r"для\s+(\d+)\s+(?:чел|человек)", flags=re.IGNORECASE)
_CHILD_COUNT_RE = re.compile(r"(\d+)\s*(?:ребен(?:ок|ка|ку|ком)|дет(?:и|ей|ям|ьми))", re.IGNORECASE)
_CHILD_AGE_RE = re.compile(r"ребен\w*[^0-9]{0,12}(\d+)\s*(?:года|лет|год|г)", re.IGNORECASE)
_ALLERGY_RE = re.compile(r"аллерг\w*\s+на\s+([^\n,.;:]+)", re.IGNORECASE)
_TOTAL_RUB_RE = re.compile(r"(\d[\d\s]*(?:[.,]\d+)?)")


def latest_user_text(history: list[dict[str, Any]]) -> str:
    for message in reversed(history):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def extract_days(text: str) -> int:
    return parse_request_days(text, default=7, max_days=31)


def extract_people_total(text: str) -> int:
    match = _PEOPLE_RE.search(text.lower())
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


def extract_child_group(text: str, people_total: int) -> tuple[str | None, int, int | None]:
    low = text.lower()
    if "реб" not in low and "дет" not in low:
        return None, 0, None
    child_count = 1
    match_count = _CHILD_COUNT_RE.search(low)
    if match_count:
        with_value = match_count.group(1).strip()
        if with_value.isdigit():
            child_count = max(1, int(with_value))
    child_count = min(child_count, people_total)
    match_age = _CHILD_AGE_RE.search(low)
    age_years = int(match_age.group(1)) if match_age and match_age.group(1).isdigit() else None
    group_id = f"child_{age_years}y" if age_years is not None else "child"
    return group_id, child_count, age_years


def extract_allergens(text: str, profile: dict[str, Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    match = _ALLERGY_RE.search(text)
    if match:
        raw = match.group(1).strip().lower()
        for part in re.split(r"\s+и\s+|,\s*", raw):
            value = part.strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)

    hard = profile.get("hard_constraints")
    if isinstance(hard, dict):
        raw = hard.get("allergens_excluded")
        if isinstance(raw, list):
            for item in raw:
                value = str(item).strip().lower()
                if value and value not in seen:
                    seen.add(value)
                    result.append(value)
    return result


def extract_dishes_from_history(history: list[dict[str, Any]]) -> list[str]:
    dishes: list[str] = []
    seen: set[str] = set()
    for message in history:
        if message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            if not isinstance(fn, dict):
                continue
            if str(fn.get("name", "")).strip() != "recipe_ingredients":
                continue
            raw_args = fn.get("arguments")
            payload: dict[str, Any] = {}
            if isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    payload = {}
            elif isinstance(raw_args, dict):
                payload = raw_args
            dish = str(payload.get("dish", "")).strip()
            if not dish:
                continue
            marker = dish.lower()
            if marker in seen:
                continue
            seen.add(marker)
            dishes.append(dish)
    return dishes


def cart_summary_lines(cart_data: dict[str, Any] | None) -> list[str]:
    if not isinstance(cart_data, dict):
        return [
            "- Товаров: н/д",
            "- Сумма: н/д",
            "- Ссылка: не сформирована",
            "- Не найдено: н/д",
        ]

    summary = cart_data.get("price_summary")
    summary_dict = summary if isinstance(summary, dict) else {}
    count = summary_dict.get("count")
    if not isinstance(count, int) or count < 0:
        items = summary_dict.get("items")
        count = len(items) if isinstance(items, list) else 0
    total_text = summary_dict.get("total_text")
    if not isinstance(total_text, str) or not total_text.strip():
        total_value = summary_dict.get("total")
        if isinstance(total_value, int | float):
            total_text = f"Итого: {float(total_value):.2f} руб"
        else:
            total_text = "н/д"
    link = str(cart_data.get("link", "")).strip() or "не сформирована"
    not_found = cart_data.get("not_found")
    if isinstance(not_found, list):
        not_found_text = ", ".join(str(item).strip() for item in not_found)
    else:
        not_found_text = "н/д"
    if not not_found_text:
        not_found_text = "нет"
    return [
        f"- Товаров: {count}",
        f"- Сумма: {total_text}",
        f"- Ссылка: {link}",
        f"- Не найдено: {not_found_text}",
    ]


def cart_products_by_category_lines(cart_data: dict[str, Any] | None) -> list[str]:
    if not isinstance(cart_data, dict):
        return []
    products = cart_data.get("products")
    if not isinstance(products, list) or not products:
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in products:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category", "")).strip() or "прочее"
        grouped.setdefault(category, []).append(row)
    lines: list[str] = []
    for category in sorted(grouped):
        lines.append(f"- Категория: {category}")
        for row in grouped[category]:
            name = str(row.get("name", "")).strip()
            xml_id = row.get("xml_id")
            if not name:
                name = f"xml_id={xml_id}" if xml_id is not None else "товар"
            quantity_raw = row.get("q", 1)
            try:
                quantity = float(quantity_raw)
            except (TypeError, ValueError):
                quantity = 1.0
            quantity_text = f"{int(quantity)}" if quantity.is_integer() else f"{quantity:.2f}"
            lines.append(f"  - {name} x {quantity_text}")
    return lines


def _extract_total_rub(summary: dict[str, Any]) -> int | None:
    total_raw = summary.get("total")
    if isinstance(total_raw, int | float) and float(total_raw) >= 0:
        return round(float(total_raw))

    total_text = str(summary.get("total_text", "")).strip()
    if not total_text:
        return None
    for match in reversed(_TOTAL_RUB_RE.findall(total_text)):
        normalized = match.replace(" ", "").replace(",", ".")
        try:
            value = float(normalized)
        except ValueError:
            continue
        if value >= 0:
            return round(value)
    return None


def build_contract_cart_summary(cart_data: dict[str, Any] | None) -> ContractCartSummary:
    if not isinstance(cart_data, dict):
        return ContractCartSummary(
            items_count=None,
            total_rub=None,
            link="не сформирована",
            total_text="н/д",
            not_found=[],
        )
    summary = (
        cart_data.get("price_summary") if isinstance(cart_data.get("price_summary"), dict) else {}
    )
    count_raw = summary.get("count")
    items_count: int | None = count_raw if isinstance(count_raw, int) and count_raw >= 0 else None
    if items_count is None:
        items = summary.get("items")
        if isinstance(items, list):
            items_count = len(items)
    total_rub = _extract_total_rub(summary)
    total_text = str(summary.get("total_text", "")).strip()
    if not total_text:
        total_text = f"Итого: {total_rub} руб" if isinstance(total_rub, int) else "н/д"
    link = str(cart_data.get("link", "")).strip() or "не сформирована"
    not_found_raw = cart_data.get("not_found")
    not_found = (
        [str(item).strip() for item in not_found_raw if str(item).strip()]
        if isinstance(not_found_raw, list)
        else []
    )

    products: list[ContractCartProduct] = []
    for row in cart_data.get("products", []) if isinstance(cart_data.get("products"), list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip() or "товар"
        category = str(row.get("category", "")).strip() or "прочее"
        q_raw = row.get("q", 1)
        try:
            q = float(q_raw)
        except (TypeError, ValueError):
            q = 1.0
        q = 1.0 if q <= 0 else q
        q_text = f"{int(q)}" if q.is_integer() else f"{q:.2f}"
        products.append(ContractCartProduct(category=category, name=name, quantity_text=q_text))

    groups: list[ContractCartGroup] = []
    groups_raw = cart_data.get("groups")
    if isinstance(groups_raw, list):
        for g_row in groups_raw:
            if not isinstance(g_row, dict):
                continue
            g_label = str(g_row.get("day_label", "")).strip()
            g_link = str(g_row.get("link", "")).strip()
            g_products_raw = g_row.get("products", [])
            g_products: list[ContractCartProduct] = []
            if isinstance(g_products_raw, list):
                for row in g_products_raw:
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("name", "")).strip() or "товар"
                    category = str(row.get("category", "")).strip() or "прочее"
                    q_raw = row.get("q", 1)
                    try:
                        q = float(q_raw)
                    except (TypeError, ValueError):
                        q = 1.0
                    q = 1.0 if q <= 0 else q
                    q_text = f"{int(q)}" if q.is_integer() else f"{q:.2f}"
                    g_products.append(
                        ContractCartProduct(category=category, name=name, quantity_text=q_text)
                    )
            groups.append(
                ContractCartGroup(
                    day_label=g_label,
                    link=g_link,
                    items_count=len(g_products),
                    products=g_products,
                )
            )

    if groups:
        # При наличии групп суммируем по группам: один товар может входить
        # в несколько корзин (разные дни), поэтому merged products занижают счёт.
        items_count = sum(g.items_count for g in groups)
    elif items_count is None and products:
        items_count = len(products)

    return ContractCartSummary(
        items_count=items_count,
        total_rub=total_rub,
        link=link,
        total_text=total_text,
        not_found=not_found,
        products=products,
        groups=groups,
    )


def resolve_hard_constraints_passed(request_payload: dict[str, Any] | None) -> bool:
    if not isinstance(request_payload, dict):
        return False
    explicit = request_payload.get("hard_constraints_passed")
    if isinstance(explicit, bool):
        return explicit
    trace = request_payload.get("applied_preferences_trace")
    if not isinstance(trace, list):
        return False
    hard_trace = [
        row
        for row in trace
        if isinstance(row, dict)
        and (
            str(row.get("field", "")).startswith("hard_constraints.")
            or ".hard_constraints." in str(row.get("field", ""))
        )
    ]
    return bool(hard_trace) and all(row.get("applied") is True for row in hard_trace)
