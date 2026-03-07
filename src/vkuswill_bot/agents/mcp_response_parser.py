"""Парсинг и извлечение данных из MCP-ответов."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from vkuswill_bot.agents.tool_result_compactor import _safe_float


def parse_json_payload(content: Any) -> Any:
    """Безопасно распарсить JSON из строки, поддерживая обёрнутый в ``` блок."""
    if not isinstance(content, str):
        return content
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(text)
    return {}


def extract_search_items(payload: Any) -> list[dict[str, Any]]:
    """Извлечь товары из MCP-ответа (search / product_details / recipe_search)."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        data_products = data.get("products")
        if isinstance(data_products, list):
            return [item for item in data_products if isinstance(item, dict)]
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        if isinstance(data.get("xml_id"), int | str):
            return [data]
    item = payload.get("item")
    if isinstance(item, dict):
        return [item]
    items = payload.get("items")
    if isinstance(items, list):
        return [entry for entry in items if isinstance(entry, dict)]
    products = payload.get("products")
    if isinstance(products, list):
        return [item for item in products if isinstance(item, dict)]
    found = payload.get("found")
    if isinstance(found, list):
        result: list[dict[str, Any]] = []
        for row in found:
            if not isinstance(row, dict):
                continue
            xml_id = row.get("xml_id")
            if xml_id is None:
                continue
            result.append(
                {
                    "xml_id": xml_id,
                    "name": row.get("name"),
                    "price": row.get("price"),
                    "unit": row.get("unit", "шт"),
                }
            )
        if result:
            return result
    results = payload.get("results")
    if isinstance(results, list):
        expanded: list[dict[str, Any]] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            best_match = row.get("best_match")
            if isinstance(best_match, dict):
                expanded.append(best_match)
        if expanded:
            return expanded
    return []


def extract_cart_data(*, tool_name: str, tool_result: str) -> dict[str, Any] | None:
    """Извлечь данные корзины из успешного ответа vkusvill_cart_link_create."""
    if tool_name != "vkusvill_cart_link_create":
        return None
    with contextlib.suppress(Exception):
        payload = json.loads(tool_result)
        if not isinstance(payload, dict) or not payload.get("ok"):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        link = data.get("link")
        if not isinstance(link, str) or not link.strip():
            return None
        return data
    return None


def extract_recipe_products_from_history(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Извлечь товары из последнего recipe_search tool-результата в истории."""
    for msg in reversed(history):
        if msg.get("role") != "tool" or msg.get("name") != "recipe_search":
            continue
        payload = parse_json_payload(msg.get("content"))
        if not isinstance(payload, dict):
            continue
        found_raw = payload.get("found")
        if not isinstance(found_raw, list):
            results_raw = payload.get("results")
            if isinstance(results_raw, list):
                found_raw = []
                for row in results_raw:
                    if not isinstance(row, dict):
                        continue
                    best_match = row.get("best_match")
                    if not isinstance(best_match, dict):
                        continue
                    found_raw.append(
                        {
                            "xml_id": best_match.get("xml_id"),
                            "suggested_q": best_match.get("suggested_q"),
                        }
                    )
            if not isinstance(found_raw, list):
                continue

        not_found_raw = payload.get("not_found")
        not_found_count = len(not_found_raw) if isinstance(not_found_raw, list) else 0
        quantities_by_xml_id: dict[int, float] = {}
        for row in found_raw:
            if not isinstance(row, dict):
                continue
            xml_id_raw = row.get("xml_id")
            if isinstance(xml_id_raw, bool):
                continue
            try:
                xml_id = int(xml_id_raw)
            except (TypeError, ValueError):
                continue
            suggested_q = _safe_float(row.get("suggested_q"), default=1.0)
            if suggested_q <= 0:
                suggested_q = 1.0
            quantities_by_xml_id[xml_id] = quantities_by_xml_id.get(xml_id, 0.0) + suggested_q

        products: list[dict[str, Any]] = [
            {"xml_id": xml_id, "q": q} for xml_id, q in quantities_by_xml_id.items()
        ]
        return products, not_found_count
    return [], 0


def has_recipe_search_candidates(history: list[dict[str, Any]]) -> bool:
    """Проверить, есть ли товары из recipe_search в истории."""
    products, _ = extract_recipe_products_from_history(history)
    return bool(products)


def extract_all_recipe_ingredients_from_history(
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Извлечь и дедуплицировать ингредиенты из всех recipe_ingredients tool-результатов."""
    seen_queries: set[str] = set()
    collected: list[dict[str, Any]] = []

    for msg in history:
        if msg.get("role") != "tool" or msg.get("name") != "recipe_ingredients":
            continue
        payload = parse_json_payload(msg.get("content"))
        if not isinstance(payload, dict) or not payload.get("ok"):
            continue

        ingredients = payload.get("ingredients")
        if not isinstance(ingredients, list):
            data = payload.get("data")
            if isinstance(data, dict):
                ingredients = data.get("ingredients")
            if not isinstance(ingredients, list):
                continue

        for row in ingredients[:30]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            query = str(row.get("search_query", "")).strip() or name
            key = query.lower()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            collected.append(row)

    return collected
