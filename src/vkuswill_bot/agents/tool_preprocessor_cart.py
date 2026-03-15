"""Cart-related preprocessing helpers for MCP tool arguments."""

from __future__ import annotations

import contextlib
import copy
import math
from typing import Any

from vkuswill_bot.agents.quantity_utils import DISCRETE_UNITS, round_kilogram_quantity
from vkuswill_bot.services.cart_processor import CartProcessor

_EGG_PACK_SIZE = 10
_ADDITIVE_CART_MARKERS = (
    "добав",
    "ещё",
    "еще",
    "дополни",
    "и еще",
    "плюс",
    "к этой корзин",
    "к предыдущ",
)


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_additive_cart_intent(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(marker in normalized for marker in _ADDITIVE_CART_MARKERS)


def preprocess_cart_link_args(
    tool_args: dict[str, Any],
    *,
    product_index: dict[int, dict[str, Any]] | None = None,
    explicit_egg_pack_request: bool = False,
) -> dict[str, Any]:
    """Normalize cart products before `vkusvill_cart_link_create` call."""
    normalized = CartProcessor.fix_cart_args(tool_args)
    products = normalized.get("products")
    if not isinstance(products, list) or explicit_egg_pack_request:
        return normalized
    product_lookup = product_index or {}
    for item in products:
        if not isinstance(item, dict):
            continue
        xml_id_raw = item.get("xml_id")
        if isinstance(xml_id_raw, bool):
            continue
        try:
            xml_id = int(xml_id_raw)
        except (TypeError, ValueError):
            continue
        product = product_lookup.get(xml_id)
        if not isinstance(product, dict):
            continue
        name = str(product.get("name", "")).strip().lower()
        q = _safe_float(item.get("q"), default=1.0)
        if q <= 0:
            q = 1
        unit = str(product.get("unit", "")).strip().lower()
        if any(stem in name for stem in ("яйц", "яиц", "яйк")):
            if q <= 1:
                item["q"] = 1
                continue
            item["q"] = max(1, math.ceil(q / _EGG_PACK_SIZE))
            continue
        if unit in DISCRETE_UNITS:
            item["q"] = max(1, math.ceil(q))
            continue
        if unit in {"кг", "kg"}:
            item["q"] = round_kilogram_quantity(q)
    return normalized


def collect_requested_products_snapshot(
    tool_args: dict[str, Any],
    *,
    product_index: dict[int, dict[str, Any]] | None = None,
    explicit_egg_pack_request: bool = False,
) -> list[dict[str, Any]]:
    """Collect normalized requested products snapshot from cart arguments."""
    products = tool_args.get("products")
    if not isinstance(products, list):
        return []
    normalized = CartProcessor.fix_cart_args({"products": copy.deepcopy(products)})
    snapshot = normalized.get("products")
    if not isinstance(snapshot, list):
        return []
    if explicit_egg_pack_request:
        return [item for item in snapshot if isinstance(item, dict)]

    lookup = product_index or {}
    normalized_snapshot: list[dict[str, Any]] = []
    for item in snapshot:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        xml_id_raw = row.get("xml_id")
        if isinstance(xml_id_raw, bool):
            normalized_snapshot.append(row)
            continue
        xml_id: int | None = None
        with contextlib.suppress(TypeError, ValueError):
            xml_id = int(xml_id_raw)
        product = lookup.get(xml_id) if isinstance(xml_id, int) else None
        if not isinstance(product, dict):
            normalized_snapshot.append(row)
            continue
        name = str(product.get("name", "")).strip().lower()
        if not any(stem in name for stem in ("яйц", "яиц", "яйк")):
            unit = str(product.get("unit", "")).strip().lower()
            if unit in {"кг", "kg"}:
                quantity = _safe_float(row.get("q"), default=0.1)
                row["q"] = round_kilogram_quantity(quantity)
            normalized_snapshot.append(row)
            continue
        quantity = _safe_float(row.get("q"), default=1.0)
        if quantity <= 1:
            row["q"] = 1
        else:
            row["q"] = max(1, math.ceil(quantity / _EGG_PACK_SIZE))
        normalized_snapshot.append(row)
    return normalized_snapshot


def restore_previous_quantities_for_additive_update(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    user_text: str,
    previous_products: list[dict[str, Any]],
    requested_quantity_overrides: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Restore previous cart quantities for additive updates."""
    if tool_name != "vkusvill_cart_link_create":
        return tool_args
    if not _is_additive_cart_intent(user_text):
        return tool_args
    products = tool_args.get("products")
    if not isinstance(products, list) or not products or not previous_products:
        return tool_args

    prev_by_xml_id: dict[int, float] = {}
    for row in previous_products:
        if not isinstance(row, dict):
            continue
        xml_id_raw = row.get("xml_id")
        if isinstance(xml_id_raw, bool):
            continue
        xml_id: int | None = None
        with contextlib.suppress(TypeError, ValueError):
            xml_id = int(xml_id_raw)
        if not isinstance(xml_id, int):
            continue
        prev_q = _safe_float(row.get("q"), default=1.0)
        if prev_q <= 0:
            prev_q = 1.0
        prev_by_xml_id[xml_id] = prev_q

    if not prev_by_xml_id:
        return tool_args
    explicit_xml_ids = set(requested_quantity_overrides or {})
    updated = False
    for row in products:
        if not isinstance(row, dict):
            continue
        xml_id_raw = row.get("xml_id")
        if isinstance(xml_id_raw, bool):
            continue
        xml_id: int | None = None
        with contextlib.suppress(TypeError, ValueError):
            xml_id = int(xml_id_raw)
        if not isinstance(xml_id, int) or xml_id not in prev_by_xml_id:
            continue
        if xml_id in explicit_xml_ids:
            continue
        current_q = _safe_float(row.get("q"), default=1.0)
        if current_q <= 0:
            current_q = 1.0
        previous_q = prev_by_xml_id[xml_id]
        if abs(current_q - previous_q) < 0.01:
            continue
        row["q"] = previous_q
        updated = True

    if updated:
        return CartProcessor.fix_cart_args({"products": products})
    return tool_args
