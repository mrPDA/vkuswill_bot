"""Tool-specific compaction strategies for MCP payloads."""

from __future__ import annotations

import contextlib
from typing import Any

from vkuswill_bot.services.preference_scope import split_scoped_preferences

from vkuswill_bot.agents.tool_value_utils import (
    _safe_float,
    extract_price_value,
    normalize_compact_text,
    score_search_candidate,
    tokenize_query_terms,
)


def compact_products_search(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": payload.get("ok")}
    data = payload.get("data")
    if not isinstance(data, dict):
        has_compact_shape = any(key in payload for key in ("meta", "items", "relevance_warning"))
        if not has_compact_shape:
            return result
        data = payload

    query_text = ""
    meta = data.get("meta", {})
    if isinstance(meta, dict):
        compact_meta: dict[str, Any] = {}
        for key in ("q", "total", "has_more"):
            if key in meta:
                compact_meta[key] = meta.get(key)
        if compact_meta:
            result["meta"] = compact_meta
            query_text = str(compact_meta.get("q", "")).strip()

    items = data.get("items", [])
    if isinstance(items, list):
        scored_items: list[dict[str, Any]] = []
        query_terms = tokenize_query_terms(query_text)
        for item in items[:10]:
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

            name = normalize_compact_text(item.get("name"))
            if not name:
                continue
            rating = item.get("rating")
            rating_avg = rating.get("average") if isinstance(rating, dict) else rating
            if not isinstance(rating_avg, int | float) or isinstance(rating_avg, bool):
                rating_avg = None
            price = item.get("price")
            if isinstance(price, dict):
                price = price.get("current")
            price_value = _safe_float(price, default=-1.0)
            unit = str(item.get("unit", "")).strip()
            score, confidence = score_search_candidate(
                query_terms=query_terms,
                product_name=name,
                rating=rating_avg,
            )
            scored_items.append(
                {
                    "xml_id": xml_id,
                    "name": name,
                    "price": price_value if price_value >= 0 else None,
                    "unit": unit or None,
                    "rating": rating_avg,
                    "confidence": confidence,
                    "_score": score,
                }
            )

        scored_items.sort(key=lambda row: row.get("_score", 0.0), reverse=True)
        top_items = []
        for row in scored_items[:3]:
            top_items.append({k: v for k, v in row.items() if k != "_score" and v is not None})
        result["items"] = top_items

    relevance_warning = data.get("relevance_warning")
    if relevance_warning:
        result["relevance_warning"] = relevance_warning
    return result


def compact_product_details(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": payload.get("ok")}
    data = payload.get("data")
    if isinstance(data, dict):
        price = data.get("price")
        if isinstance(price, dict):
            price = price.get("current")
        rating = data.get("rating")
        rating_value = rating.get("average") if isinstance(rating, dict) else rating
        weight = data.get("weight")
        compact_weight: dict[str, Any] | None = None
        if isinstance(weight, dict):
            compact_weight = {}
            if "value" in weight:
                compact_weight["value"] = weight.get("value")
            if "unit" in weight:
                compact_weight["unit"] = weight.get("unit")
            if not compact_weight:
                compact_weight = None

        compact_data: dict[str, Any] = {
            "xml_id": data.get("xml_id", data.get("id")),
            "name": normalize_compact_text(data.get("name")),
            "brand": normalize_compact_text(data.get("brand")),
            "price": price,
            "unit": normalize_compact_text(data.get("unit")),
            "weight": compact_weight,
            "rating": rating_value,
        }
        result["data"] = {
            key: value for key, value in compact_data.items() if value is not None and value != ""
        }

    if "error" in payload:
        result["error"] = payload.get("error")
    if "message" in payload:
        result["message"] = payload.get("message")
    return result


def compact_recipe_ingredients(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": payload.get("ok")}
    data = payload.get("data")
    if not isinstance(data, dict):
        has_compact_shape = any(key in payload for key in ("dish", "servings", "ingredients"))
        if not has_compact_shape:
            return result
        data = payload

    result["dish"] = data.get("dish", payload.get("dish"))
    result["servings"] = data.get("servings", payload.get("servings"))
    ingredients = data.get("ingredients")
    if not isinstance(ingredients, list):
        ingredients = payload.get("ingredients", [])
    if isinstance(ingredients, list):
        compact_ingredients: list[dict[str, Any]] = []
        for row in ingredients[:30]:
            if not isinstance(row, dict):
                continue

            compact_row: dict[str, Any] = {
                "name": row.get("name"),
                "quantity": row.get("quantity"),
                "unit": row.get("unit"),
            }
            if row.get("optional") is True:
                compact_row["optional"] = True
            for field in (
                "search_query",
                "kg_equivalent",
                "l_equivalent",
                "pack_equivalent",
            ):
                value = row.get(field)
                if value is not None and value != "":
                    compact_row[field] = value
            compact_ingredients.append(compact_row)

        result["ingredients"] = compact_ingredients
    return result


def compact_recipe_search(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": payload.get("ok")}
    data = payload.get("data")
    compact_found: list[dict[str, Any]] = []
    not_found: list[Any] = []

    if isinstance(data, dict):
        found = data.get("found", [])
        if isinstance(found, list):
            for row in found[:40]:
                if not isinstance(row, dict):
                    continue
                item = row.get("item")
                compact_found.append(
                    {
                        "ingredient": normalize_compact_text(row.get("ingredient")),
                        "suggested_q": row.get("suggested_q"),
                        "xml_id": (item.get("xml_id") if isinstance(item, dict) else None),
                        "name": (
                            normalize_compact_text(item.get("name"))
                            if isinstance(item, dict)
                            else None
                        ),
                        "price": (
                            extract_price_value(item.get("price"))
                            if isinstance(item, dict)
                            else None
                        ),
                    }
                )
        raw_not_found = data.get("not_found", [])
        if isinstance(raw_not_found, list):
            not_found = raw_not_found

    if not compact_found:
        raw_found = payload.get("found", [])
        if isinstance(raw_found, list):
            for row in raw_found[:40]:
                if not isinstance(row, dict):
                    continue
                compact_found.append(
                    {
                        "ingredient": normalize_compact_text(row.get("ingredient")),
                        "suggested_q": row.get("suggested_q"),
                        "xml_id": row.get("xml_id"),
                        "name": normalize_compact_text(row.get("name")),
                        "price": extract_price_value(row.get("price")),
                    }
                )
        if not not_found:
            raw_not_found = payload.get("not_found", [])
            if isinstance(raw_not_found, list):
                not_found = raw_not_found

    if not compact_found:
        results = payload.get("results", [])
        if isinstance(results, list):
            for row in results[:40]:
                if not isinstance(row, dict):
                    continue
                best_match = row.get("best_match")
                if not isinstance(best_match, dict):
                    continue
                compact_found.append(
                    {
                        "ingredient": normalize_compact_text(row.get("ingredient")),
                        "suggested_q": best_match.get("suggested_q"),
                        "xml_id": best_match.get("xml_id"),
                        "name": normalize_compact_text(best_match.get("name")),
                        "price": extract_price_value(best_match.get("price")),
                    }
                )
        if not not_found:
            raw_not_found = payload.get("not_found", [])
            if isinstance(raw_not_found, list):
                not_found = raw_not_found

    result["found"] = compact_found
    if isinstance(not_found, list):
        result["not_found"] = not_found[:40]
    return result


def compact_cart_link(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": payload.get("ok")}
    data = payload.get("data")
    if isinstance(data, dict):
        result["link"] = data.get("link")
        price_summary = data.get("price_summary")
        if isinstance(price_summary, dict):
            result["price_summary"] = price_summary
    if "error" in payload:
        result["error"] = payload.get("error")
    if "message" in payload:
        result["message"] = payload.get("message")
    return result


def compact_preferences_get(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": payload.get("ok")}
    regular, scoped = split_scoped_preferences(payload.get("preferences"))
    if regular:
        result["preferences"] = regular[:20]
    if scoped:
        result["scoped_preferences"] = [{**row, "scope": "group_specific"} for row in scoped[:10]]
        result["scoped_preferences_note"] = (
            "Эти ограничения относятся к части семьи и не являются "
            "глобальным запретом для всей корзины."
        )
    profile = payload.get("profile")
    if isinstance(profile, dict):
        result["profile"] = profile
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        result["message"] = message
    return result


def compact_generic(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("ok", "error", "message", "data"):
        if key in payload:
            result[key] = payload[key]
    return result or payload
