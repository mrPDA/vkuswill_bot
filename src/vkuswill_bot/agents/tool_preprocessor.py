"""Preprocessing and normalization of MCP tool arguments."""

from __future__ import annotations

import contextlib
import copy
import math
from typing import Any

from vkuswill_bot.agents.quantity_utils import (
    DISCRETE_UNITS,
    round_kilogram_quantity,
)
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.search_processor import SearchProcessor

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
    """Безопасно преобразовать значение к float."""
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_additive_cart_intent(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(marker in normalized for marker in _ADDITIVE_CART_MARKERS)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def apply_preferences_to_query(query: str, user_prefs: dict[str, str]) -> str:
    """Дополняет поисковый запрос пользовательскими предпочтениями."""
    if not user_prefs or not query:
        return query
    query_lower = query.strip().lower()
    preference = user_prefs.get(query_lower)
    if preference is None:
        return query
    if query_lower in preference.lower():
        return preference
    return f"{query} {preference}"


def preprocess_tool_args(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    user_preferences: dict[str, str] | None = None,
    product_index: dict[int, dict[str, Any]] | None = None,
    explicit_egg_pack_request: bool = False,
    requested_ingredients: list[dict[str, Any]] | None = None,
    search_query_by_xml_id: dict[int, str] | None = None,
    requested_quantity_overrides: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Нормализует аргументы MCP-инструмента перед вызовом."""
    if tool_name == "vkusvill_cart_link_create":
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
    if tool_name == "vkusvill_products_search":
        normalized_search_args = dict(tool_args)
        if "page" in normalized_search_args:
            normalized_search_args.pop("page", None)
        prefs = user_preferences or {}
        if not prefs:
            return normalized_search_args
        query_key = None
        if isinstance(normalized_search_args.get("q"), str):
            query_key = "q"
        elif isinstance(normalized_search_args.get("query"), str):
            query_key = "query"
        if query_key is None:
            return normalized_search_args
        original_query = str(normalized_search_args.get(query_key, "")).strip()
        if not original_query:
            return normalized_search_args
        enhanced_query = apply_preferences_to_query(original_query, prefs)
        if enhanced_query == original_query:
            return normalized_search_args
        return {**normalized_search_args, query_key: enhanced_query}
    if tool_name == "recipe_search":
        return normalize_recipe_search_args(tool_args)
    return tool_args


def collect_requested_products_snapshot(
    tool_args: dict[str, Any],
    *,
    product_index: dict[int, dict[str, Any]] | None = None,
    explicit_egg_pack_request: bool = False,
) -> list[dict[str, Any]]:
    """Собирает снимок запрошенных товаров для корзины."""
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
    """Восстанавливает количества из предыдущей корзины при аддитивном обновлении."""
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
        if abs(current_q - 1.0) > 1e-9:
            continue
        if abs(previous_q - 1.0) <= 1e-9:
            continue
        row["q"] = previous_q
        updated = True

    if updated:
        return CartProcessor.fix_cart_args({"products": products})
    return tool_args


def normalize_recipe_search_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Нормализует аргументы tool recipe_search."""
    ingredients = tool_args.get("ingredients")
    if not isinstance(ingredients, list):
        return tool_args

    normalized_rows: list[Any] = []
    changed = False
    for row in ingredients:
        if not isinstance(row, dict):
            normalized_rows.append(row)
            continue

        normalized = dict(row)
        raw_query = normalized.get("search_query", "")
        query = str(raw_query).strip() if raw_query is not None else ""
        if query:
            cleaned_query = SearchProcessor.clean_search_query(query)
            if cleaned_query and cleaned_query != query:
                normalized["search_query"] = cleaned_query
                changed = True
        else:
            name = str(normalized.get("name", "")).strip()
            if name:
                normalized["search_query"] = SearchProcessor.clean_search_query(name)
                changed = True

        normalized_rows.append(normalized)

    if not changed:
        return tool_args
    return {**tool_args, "ingredients": normalized_rows}
