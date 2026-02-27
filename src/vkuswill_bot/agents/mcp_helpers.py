"""Вспомогательные утилиты для работы с MCP-инструментами."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from vkuswill_bot.services.prompts import RECIPE_SEARCH_TOOL, RECIPE_TOOL


def with_virtual_recipe_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Добавить виртуальные recipe-инструменты, если их ещё нет."""
    prepared = list(raw_tools)
    existing_names = {
        str(tool.get("name", "")).strip() for tool in prepared if isinstance(tool, dict)
    }
    virtual_tools = (
        {
            "name": str(RECIPE_TOOL.get("name", "")).strip(),
            "description": str(RECIPE_TOOL.get("description", "")),
            "parameters": RECIPE_TOOL.get("parameters", {}),
        },
        {
            "name": str(RECIPE_SEARCH_TOOL.get("name", "")).strip(),
            "description": str(RECIPE_SEARCH_TOOL.get("description", "")),
            "parameters": RECIPE_SEARCH_TOOL.get("parameters", {}),
        },
    )
    for virtual in virtual_tools:
        name = str(virtual.get("name", "")).strip()
        if not name or name in existing_names:
            continue
        prepared.append(virtual)
        existing_names.add(name)
    return prepared


def make_mcp_call_cache_key(*, name: str, arguments: dict[str, Any]) -> str:
    """Построить ключ кэша для MCP-вызова."""
    args_json = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{name}:{args_json}"


def is_successful_tool_result(tool_result: str) -> bool:
    """Проверить, что tool-result — успешный JSON с ok=true."""
    with contextlib.suppress(Exception):
        payload = json.loads(tool_result)
        if isinstance(payload, dict) and payload.get("ok") is True:
            return True
    return False


def tool_progress_text(tool_name: str) -> str:
    """Текст прогресса для пользователя по имени инструмента."""
    mapping = {
        "vkusvill_products_search": "\U0001f50d Ищу товары...",
        "vkusvill_cart_link_create": "\U0001f6d2 Формирую корзину...",
        "recipe_ingredients": "\U0001f373 Подбираю рецепт...",
        "recipe_search": "\U0001f50d Ищу продукты по рецепту...",
    }
    return mapping.get(tool_name, "\u2699\ufe0f Обрабатываю запрос...")
