"""Компактизация tool-результатов для LLM history."""

from __future__ import annotations

import contextlib
import html
import json
import re
from typing import Any


class ToolResultCompactor:
    """Сжимает tool-результаты MCP для передачи в LLM context window."""

    def __init__(self, *, max_tool_result_chars: int = 1800) -> None:
        self._max_tool_result_chars = max(300, max_tool_result_chars)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def prepare_tool_result_for_history(self, tool_name: str, tool_result: str) -> str:
        """Сжать tool-result для history, чтобы не переполнять контекст LLM."""
        with contextlib.suppress(Exception):
            parsed = json.loads(tool_result)
            if isinstance(parsed, dict):
                compact = self.compact_tool_result(tool_name, parsed)
                return self.fit_payload_to_limit(compact)
        return tool_result[: self._max_tool_result_chars]

    def build_cached_tool_stub(self, *, tool_name: str, compact_content: str) -> str:
        """Построить сверх-компактный stub для повторного tool-результата в history."""
        base: dict[str, Any] = {"ok": True, "cached": True, "duplicate": True}
        with contextlib.suppress(Exception):
            parsed = json.loads(compact_content)
            if isinstance(parsed, dict):
                if "ok" in parsed:
                    base["ok"] = bool(parsed.get("ok"))
                if tool_name == "vkusvill_products_search":
                    meta = parsed.get("meta")
                    if isinstance(meta, dict):
                        q = str(meta.get("q", "")).strip()
                        if q:
                            base["meta"] = {"q": q}
                    items = parsed.get("items")
                    if isinstance(items, list) and items:
                        first = items[0]
                        if isinstance(first, dict):
                            base["item"] = {
                                key: first.get(key)
                                for key in ("xml_id", "name", "price", "unit")
                                if first.get(key) is not None
                            }
                elif tool_name == "vkusvill_product_details":
                    data = parsed.get("data")
                    if isinstance(data, dict):
                        base["data"] = {
                            key: data.get(key)
                            for key in ("xml_id", "name", "price", "unit")
                            if data.get(key) is not None
                        }
                elif tool_name == "recipe_ingredients":
                    dish = str(parsed.get("dish", "")).strip()
                    if dish:
                        base["dish"] = dish
                    servings = parsed.get("servings")
                    if isinstance(servings, int | float) and not isinstance(servings, bool):
                        base["servings"] = servings
                elif tool_name == "recipe_search":
                    found = parsed.get("found")
                    if isinstance(found, list):
                        base["found_count"] = len(found)
                    not_found = parsed.get("not_found")
                    if isinstance(not_found, list):
                        base["not_found_count"] = len(not_found)
        return self.fit_payload_to_limit(base)

    def fit_payload_to_limit(self, payload: dict[str, Any]) -> str:
        """Уместить JSON-пейлоад в лимит, сохранив валидный JSON."""
        compact = dict(payload)
        encoded = json.dumps(compact, ensure_ascii=False)
        if len(encoded) <= self._max_tool_result_chars:
            return encoded

        def _trim_list(key: str, keep: int) -> None:
            value = compact.get(key)
            if isinstance(value, list):
                compact[key] = value[:keep]

        for key in ("items", "found", "ingredients", "not_found"):
            _trim_list(key, 1)
            encoded = json.dumps(compact, ensure_ascii=False)
            if len(encoded) <= self._max_tool_result_chars:
                return encoded

        for key in ("relevance_warning", "message"):
            value = compact.get(key)
            if isinstance(value, str) and len(value) > 160:
                compact[key] = value[:160]
            encoded = json.dumps(compact, ensure_ascii=False)
            if len(encoded) <= self._max_tool_result_chars:
                return encoded

        tiny = {
            "ok": payload.get("ok"),
            "error": payload.get("error"),
            "message": "tool_result_truncated",
        }
        return json.dumps(tiny, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Tool-specific compaction
    # ------------------------------------------------------------------

    def compact_tool_result(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "vkusvill_products_search":
            return self._compact_products_search(payload)
        if tool_name == "vkusvill_product_details":
            return self._compact_product_details(payload)
        if tool_name == "recipe_ingredients":
            return self._compact_recipe_ingredients(payload)
        if tool_name == "recipe_search":
            return self._compact_recipe_search(payload)
        if tool_name == "vkusvill_cart_link_create":
            return self._compact_cart_link(payload)
        return self._compact_generic(payload)

    def _compact_products_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": payload.get("ok")}
        data = payload.get("data")
        if not isinstance(data, dict):
            has_compact_shape = any(
                key in payload for key in ("meta", "items", "relevance_warning")
            )
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

    @classmethod
    def _compact_product_details(cls, payload: dict[str, Any]) -> dict[str, Any]:
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
                key: value
                for key, value in compact_data.items()
                if value is not None and value != ""
            }

        if "error" in payload:
            result["error"] = payload.get("error")
        if "message" in payload:
            result["message"] = payload.get("message")
        return result

    def _compact_recipe_ingredients(self, payload: dict[str, Any]) -> dict[str, Any]:
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

    def _compact_recipe_search(self, payload: dict[str, Any]) -> dict[str, Any]:
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

        # Идемпотентность компактизации: поддержать уже-compact shape.
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

        # Совместимость с fallback-форматом: top-level results/best_match.
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

    @staticmethod
    def _compact_cart_link(payload: dict[str, Any]) -> dict[str, Any]:
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

    @staticmethod
    def _compact_generic(payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ("ok", "error", "message", "data"):
            if key in payload:
                result[key] = payload[key]
        return result or payload


# ------------------------------------------------------------------
# Module-level helper functions (shared with ShoppingAgent)
# ------------------------------------------------------------------


def normalize_compact_text(value: Any) -> str:
    """Нормализовать текст: unescape HTML, убрать теги, сжать пробелы."""
    text = str(value or "")
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize_query_terms(query: str) -> list[str]:
    """Токенизировать поисковый запрос для ранжирования."""
    normalized = normalize_compact_text(query).lower().replace("ё", "е")
    tokens = re.findall(r"[a-zа-я0-9]+", normalized, flags=re.IGNORECASE)
    return [token for token in tokens if len(token) >= 2][:6]


def score_search_candidate(
    *,
    query_terms: list[str],
    product_name: str,
    rating: float | None,
) -> tuple[float, float]:
    """Оценить релевантность товара поисковому запросу."""
    normalized_name = normalize_compact_text(product_name).lower().replace("ё", "е")
    if not query_terms:
        rating_bonus = (rating or 0.0) / 10 if rating is not None else 0.0
        return rating_bonus, 0.5

    matched = sum(1 for token in query_terms if token in normalized_name)
    coverage = matched / max(1, len(query_terms))
    prefix_bonus = 0.2 if normalized_name.startswith(query_terms[0]) else 0.0
    rating_bonus = (rating or 0.0) / 10 if rating is not None else 0.0
    score = coverage * 2.5 + prefix_bonus + rating_bonus
    confidence = min(0.99, max(0.0, 0.3 + coverage * 0.7))
    return score, round(confidence, 2)


def extract_price_value(raw_price: Any) -> float | None:
    """Извлечь числовое значение цены из разных форматов."""
    if isinstance(raw_price, dict):
        for key in ("current", "value", "amount", "price"):
            if key in raw_price:
                price = _safe_float(raw_price.get(key), default=-1.0)
                if price >= 0:
                    return price
        return None
    price = _safe_float(raw_price, default=-1.0)
    return price if price >= 0 else None


def _safe_float(value: Any, *, default: float) -> float:
    """Безопасное преобразование в float."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return float(value.replace(",", "."))
    return default
