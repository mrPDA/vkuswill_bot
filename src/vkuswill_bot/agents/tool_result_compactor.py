"""Compaction of tool results for LLM history budget."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from vkuswill_bot.agents.tool_result_compact_strategies import (
    compact_cart_link,
    compact_generic,
    compact_product_details,
    compact_products_search,
    compact_recipe_ingredients,
    compact_recipe_search,
)
from vkuswill_bot.agents.tool_value_utils import (
    _safe_float,
    extract_price_value,
    normalize_compact_text,
    score_search_candidate,
    tokenize_query_terms,
)


_PREFERENCE_TOOLS = frozenset(
    {
        "user_preferences_get",
        "user_preferences_set",
        "user_preferences_delete",
    }
)


class ToolResultCompactor:
    """Сжимает tool-результаты MCP для передачи в LLM context window."""

    def __init__(self, *, max_tool_result_chars: int = 1800) -> None:
        self._max_tool_result_chars = max(300, max_tool_result_chars)

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

    def compact_tool_result(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "vkusvill_products_search":
            return compact_products_search(payload)
        if tool_name == "vkusvill_product_details":
            return compact_product_details(payload)
        if tool_name == "recipe_ingredients":
            return compact_recipe_ingredients(payload)
        if tool_name == "recipe_search":
            return compact_recipe_search(payload)
        if tool_name == "vkusvill_cart_link_create":
            return compact_cart_link(payload)
        if tool_name in _PREFERENCE_TOOLS:
            return payload
        return compact_generic(payload)


__all__ = [
    "ToolResultCompactor",
    "_safe_float",
    "extract_price_value",
    "normalize_compact_text",
    "score_search_candidate",
    "tokenize_query_terms",
]
