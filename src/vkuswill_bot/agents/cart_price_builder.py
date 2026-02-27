"""Построение price_summary для корзины и вспомогательные функции."""

from __future__ import annotations

import contextlib
import math
from typing import Any

from vkuswill_bot.agents.quantity_utils import DISCRETE_UNITS, round_kilogram_quantity
from vkuswill_bot.agents.tool_result_compactor import (
    _safe_float,
    extract_price_value,
    normalize_compact_text,
)

_DISCRETE_UNITS = DISCRETE_UNITS


def normalize_product_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """Нормализовать строку товара в унифицированный dict с xml_id/name/unit/price."""
    xml_id_raw = item.get("xml_id", item.get("id"))
    if isinstance(xml_id_raw, bool):
        return None
    xml_id: int | None = None
    with contextlib.suppress(TypeError, ValueError):
        xml_id = int(xml_id_raw)
    if not isinstance(xml_id, int):
        return None
    name = str(item.get("name", f"Товар {xml_id}")).strip() or f"Товар {xml_id}"
    unit = str(item.get("unit", "шт")).strip() or "шт"
    price = extract_price_value(item.get("price"))
    result: dict[str, Any] = {
        "xml_id": xml_id,
        "name": name,
        "unit": unit,
    }
    if price is not None:
        result["price"] = price
    return result


def aggregate_products_by_xml_id(
    products: list[dict[str, Any]],
) -> tuple[dict[int, float], list[int]]:
    """Агрегировать products по xml_id, суммируя количества."""
    totals: dict[int, float] = {}
    order: list[int] = []
    for item in products:
        if not isinstance(item, dict):
            continue
        xml_id_raw = item.get("xml_id")
        if isinstance(xml_id_raw, bool):
            continue
        xml_id: int | None = None
        with contextlib.suppress(TypeError, ValueError):
            xml_id = int(xml_id_raw)
        if not isinstance(xml_id, int):
            continue
        quantity = _safe_float(item.get("q"), default=1.0)
        if quantity <= 0:
            quantity = 1.0
        if xml_id not in totals:
            totals[xml_id] = 0.0
            order.append(xml_id)
        totals[xml_id] += quantity
    return totals, order


def format_quantity_text(quantity: float, *, unit: str = "") -> str:
    """Форматировать количество с учётом единицы измерения."""
    normalized_unit = unit.strip().lower()
    if normalized_unit in _DISCRETE_UNITS:
        return str(max(1, math.ceil(quantity)))
    if normalized_unit in {"кг", "kg"}:
        return f"{round_kilogram_quantity(quantity):.1f}"
    if float(quantity).is_integer():
        return str(int(quantity))
    return f"{quantity:.3f}".rstrip("0").rstrip(".")


def ensure_cart_price_summary(
    *,
    cart_data: dict[str, Any],
    product_index: dict[int, dict[str, Any]],
) -> None:
    """Синтезировать price_summary для cart_data, если его ещё нет или он неполный."""
    summary = cart_data.get("price_summary")
    if isinstance(summary, dict):
        items = summary.get("items")
        total_text = summary.get("total_text")
        has_total_text = isinstance(total_text, str) and bool(total_text.strip())
        if isinstance(items, list) and items and has_total_text:
            return

    products = cart_data.get("products")
    if not isinstance(products, list) or not products:
        return
    requested_products = cart_data.get("requested_products")
    if not isinstance(requested_products, list) or not requested_products:
        requested_products = products

    lines: list[str] = []
    recipe_lines: list[str] = []
    item_details: list[dict[str, Any]] = []
    total = 0.0
    recipe_total = 0.0
    dual_pricing = False
    all_priced = True
    original_total_text = ""
    original_total_value = -1.0
    if isinstance(summary, dict):
        total_text_raw = summary.get("total_text")
        if isinstance(total_text_raw, str) and total_text_raw.strip():
            original_total_text = total_text_raw.strip()
        original_total_value = _safe_float(summary.get("total"), default=-1.0)

    purchase_by_xml_id, purchase_order = aggregate_products_by_xml_id(products)
    recipe_by_xml_id, _recipe_order = aggregate_products_by_xml_id(requested_products)
    for xml_id_int in purchase_order:
        quantity = purchase_by_xml_id.get(xml_id_int, 1.0)
        if quantity <= 0:
            quantity = 1.0
        recipe_quantity = recipe_by_xml_id.get(xml_id_int, quantity)
        if recipe_quantity <= 0:
            recipe_quantity = quantity
        if abs(quantity - recipe_quantity) >= 1e-9:
            dual_pricing = True

        normalized = product_index.get(xml_id_int)
        if normalized is None:
            normalized = normalize_product_row({"xml_id": xml_id_int, "q": quantity})
        raw_name = (
            str(normalized.get("name", f"Товар {xml_id_int}")).strip()
            if normalized
            else f"Товар {xml_id_int}"
        )
        name = normalize_compact_text(raw_name) or f"Товар {xml_id_int}"
        price = (
            _safe_float(normalized.get("price"), default=-1.0)
            if isinstance(normalized, dict)
            else -1.0
        )
        unit = str(normalized.get("unit", "")).strip() if isinstance(normalized, dict) else ""
        quantity_text = format_quantity_text(quantity, unit=unit)

        if price >= 0:
            subtotal = price * quantity
            recipe_subtotal = price * recipe_quantity
            total += subtotal
            recipe_total += recipe_subtotal
            lines.append(f"- {name} x {quantity_text} = {subtotal:.2f} руб")
            recipe_lines.append(
                f"- {name} x {format_quantity_text(recipe_quantity, unit=unit)} = "
                f"{recipe_subtotal:.2f} руб"
            )
            item_details.append(
                {
                    "xml_id": xml_id_int,
                    "name": name,
                    "unit": normalized.get("unit") if isinstance(normalized, dict) else None,
                    "unit_price": price,
                    "recipe_q": recipe_quantity,
                    "purchase_q": quantity,
                    "recipe_subtotal": round(recipe_subtotal, 2),
                    "purchase_subtotal": round(subtotal, 2),
                    "overbuy_subtotal": round(subtotal - recipe_subtotal, 2),
                }
            )
        else:
            all_priced = False
            lines.append(f"- {name} x {quantity_text} = цена уточняется")
            recipe_lines.append(
                f"- {name} x {format_quantity_text(recipe_quantity, unit=unit)} = цена уточняется"
            )
            item_details.append(
                {
                    "xml_id": xml_id_int,
                    "name": name,
                    "recipe_q": recipe_quantity,
                    "purchase_q": quantity,
                }
            )

    if not lines:
        return

    synthesized: dict[str, Any] = {
        "items": lines,
        "recipe_items": recipe_lines,
        "item_details": item_details,
        "dual_pricing": dual_pricing,
        "count": len(lines),
    }
    if all_priced:
        rounded_total = round(total, 2)
        rounded_recipe_total = round(recipe_total, 2)
        overbuy_total = round(rounded_total - rounded_recipe_total, 2)
        synthesized["total"] = rounded_total
        synthesized["total_text"] = f"Итого: {rounded_total:.2f} руб"
        synthesized["purchase_total"] = rounded_total
        synthesized["purchase_total_text"] = f"К покупке: {rounded_total:.2f} руб"
        synthesized["recipe_total"] = rounded_recipe_total
        synthesized["recipe_total_text"] = f"По рецепту: {rounded_recipe_total:.2f} руб"
        synthesized["overbuy_total"] = overbuy_total
        synthesized["overbuy_total_text"] = f"Переплата из-за упаковок: {overbuy_total:.2f} руб"
    elif original_total_text:
        synthesized["total_text"] = original_total_text
    elif original_total_value >= 0:
        synthesized["total"] = round(original_total_value, 2)
        synthesized["total_text"] = f"Итого: {original_total_value:.2f} руб"
    else:
        synthesized["total_text"] = "Итого: будет рассчитано при открытии корзины"

    cart_data["price_summary"] = synthesized
