"""Тесты для vkuswill_bot.agents.mcp_helpers."""

from __future__ import annotations

import json

from vkuswill_bot.agents.mcp_helpers import (
    is_successful_tool_result,
    make_mcp_call_cache_key,
    tool_progress_text,
    with_virtual_recipe_tools,
)


def test_with_virtual_recipe_tools_adds_missing_virtual_tools() -> None:
    raw_tools = [
        {
            "name": "vkusvill_products_search",
            "description": "Search",
            "parameters": {"type": "object"},
        }
    ]

    result = with_virtual_recipe_tools(raw_tools)
    names = {str(row.get("name", "")).strip() for row in result}
    assert "recipe_ingredients" in names
    assert "recipe_search" in names


def test_with_virtual_recipe_tools_does_not_duplicate_existing() -> None:
    raw_tools = [
        {"name": "recipe_ingredients", "description": "", "parameters": {}},
        {"name": "recipe_search", "description": "", "parameters": {}},
    ]

    result = with_virtual_recipe_tools(raw_tools)
    names = [str(row.get("name", "")).strip() for row in result]
    assert names.count("recipe_ingredients") == 1
    assert names.count("recipe_search") == 1


def test_make_mcp_call_cache_key_is_stable_for_argument_order() -> None:
    key_a = make_mcp_call_cache_key(
        name="vkusvill_products_search",
        arguments={"limit": 5, "q": "молоко"},
    )
    key_b = make_mcp_call_cache_key(
        name="vkusvill_products_search",
        arguments={"q": "молоко", "limit": 5},
    )
    assert key_a == key_b


def test_is_successful_tool_result() -> None:
    assert is_successful_tool_result(json.dumps({"ok": True}, ensure_ascii=False)) is True
    assert is_successful_tool_result(json.dumps({"ok": False}, ensure_ascii=False)) is False
    assert is_successful_tool_result("not json") is False


def test_tool_progress_text_mapping_and_fallback() -> None:
    assert tool_progress_text("vkusvill_products_search").startswith("🔍")
    assert tool_progress_text("vkusvill_cart_link_create").startswith("🛒")
    assert tool_progress_text("recipe_ingredients").startswith("🍳")
    assert tool_progress_text("unknown_tool").startswith("⚙️")
