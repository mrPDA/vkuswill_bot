"""Вспомогательные утилиты для работы с MCP-инструментами."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from vkuswill_bot.services.prompts import LOCAL_TOOLS, RECIPE_SEARCH_TOOL, RECIPE_TOOL

# Preference tool names — обрабатываются локально (SQLite), минуя MCP
PREFERENCE_TOOL_NAMES = frozenset(
    {
        "user_preferences_get",
        "user_preferences_set",
        "user_preferences_delete",
    }
)


def with_virtual_preference_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Добавить виртуальные preference-инструменты, если их ещё нет."""
    prepared = list(raw_tools)
    existing_names = {
        str(tool.get("name", "")).strip() for tool in prepared if isinstance(tool, dict)
    }
    for tool_def in LOCAL_TOOLS:
        name = str(tool_def.get("name", "")).strip()
        if not name or name not in PREFERENCE_TOOL_NAMES or name in existing_names:
            continue
        prepared.append(
            {
                "name": name,
                "description": str(tool_def.get("description", "")),
                "parameters": tool_def.get("parameters", {}),
            }
        )
        existing_names.add(name)
    return prepared


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


async def handle_local_preference_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    store: Any,
    user_id: int | None,
) -> str:
    """Обработать preference tool call локально через PreferencesStore."""
    if store is None or user_id is None:
        return json.dumps(
            {"ok": False, "error": "Хранилище предпочтений не настроено"},
            ensure_ascii=False,
        )

    if name == "user_preferences_get":
        return await store.get_formatted(user_id)

    if name == "user_preferences_set":
        category = arguments.get("category", "")
        preference = arguments.get("preference", "")
        if not category or not preference:
            return json.dumps(
                {"ok": False, "error": "Не указана категория или предпочтение"},
                ensure_ascii=False,
            )
        return await store.set(user_id, category, preference)

    if name == "user_preferences_delete":
        category = arguments.get("category", "")
        if not category:
            return json.dumps(
                {"ok": False, "error": "Не указана категория"},
                ensure_ascii=False,
            )
        return await store.delete(user_id, category)

    return json.dumps(
        {"ok": False, "error": f"Неизвестный preference tool: {name}"},
        ensure_ascii=False,
    )


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
