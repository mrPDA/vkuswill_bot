"""Управление product_index, search_query_by_xml_id и cart snapshot."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
from typing import Any

from vkuswill_bot.agents.cart_price_builder import normalize_product_row
from vkuswill_bot.agents.mcp_response_parser import extract_search_items, parse_json_payload

_INDEXABLE_TOOLS = frozenset(
    {
        "vkusvill_products_search",
        "vkusvill_product_details",
        "recipe_search",
        "get_previous_cart",
        "vkusvill_cart_link_create",
    }
)


def update_product_index_from_tool_result(
    *,
    product_index: dict[int, dict[str, Any]],
    tool_name: str,
    tool_result: str,
) -> None:
    """Обновить product_index новыми товарами из MCP tool-ответа."""
    if tool_name not in _INDEXABLE_TOOLS:
        return
    with contextlib.suppress(Exception):
        payload = json.loads(tool_result)
        for item in extract_search_items(payload):
            normalized = normalize_product_row(item)
            if normalized is None:
                continue
            if (
                normalized.get("name") == f"Товар {normalized['xml_id']}"
                and "price" not in normalized
            ):
                continue
            product_index[normalized["xml_id"]] = normalized


def build_product_index_from_history(
    history: list[dict[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    """Построить product_index из последних 20 tool-сообщений в истории."""
    if not history:
        return {}
    product_index: dict[int, dict[str, Any]] = {}
    for msg in history[-20:]:
        if msg.get("role") != "tool":
            continue
        tool_name = str(msg.get("name", "")).strip()
        content = msg.get("content")
        if not tool_name or not isinstance(content, str) or not content.strip():
            continue
        update_product_index_from_tool_result(
            product_index=product_index,
            tool_name=tool_name,
            tool_result=content,
        )
    return product_index


def update_search_query_by_xml_id(
    *,
    search_query_by_xml_id: dict[int, str],
    tool_args: dict[str, Any],
    tool_result: str,
) -> None:
    """Связать xml_id из результатов поиска с исходным запросом."""
    query = str(tool_args.get("q", tool_args.get("query", ""))).strip()
    if not query:
        return
    payload = parse_json_payload(tool_result)
    for item in extract_search_items(payload):
        normalized = normalize_product_row(item)
        if normalized is None:
            continue
        search_query_by_xml_id[normalized["xml_id"]] = query


def build_cart_snapshot(
    *,
    args: dict[str, Any],
    result: str,
) -> dict[str, Any] | None:
    """Построить снапшот корзины из результата vkusvill_cart_link_create.

    Возвращает dict со снапшотом или None если результат невалиден.
    """
    with contextlib.suppress(Exception):
        parsed = json.loads(result)
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            return None
        data = parsed.get("data", {})
        if not isinstance(data, dict):
            return None
        summary = data.get("price_summary", {})
        total: float | None = None
        items_count = 0
        if isinstance(summary, dict):
            total_raw = summary.get("total")
            if isinstance(total_raw, int | float) and not isinstance(total_raw, bool):
                total = float(total_raw)
            count_raw = summary.get("count")
            if isinstance(count_raw, int) and count_raw >= 0:
                items_count = count_raw
            elif isinstance(count_raw, float) and count_raw.is_integer() and count_raw >= 0:
                items_count = int(count_raw)
            else:
                items = summary.get("items")
                if isinstance(items, list):
                    items_count = len(items)
        if items_count <= 0:
            products = args.get("products")
            if isinstance(products, list):
                items_count = len(products)
        return {
            "products": args.get("products", []),
            "link": data.get("link", ""),
            "total": total,
            "items_count": items_count,
            "price_summary": summary if isinstance(summary, dict) else {},
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
        }
    return None
