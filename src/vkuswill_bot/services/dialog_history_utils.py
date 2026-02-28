"""Shared функции работы с историей диалога."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from vkuswill_bot.services.dialog_types import (
    MessageT,
    msg_content,
    msg_function_call,
    msg_name,
    msg_role,
)

logger = logging.getLogger(__name__)

# Макс. длина суммаризированного tool result (символы).
MAX_SUMMARY_LENGTH = 200


def _fmt_products_search(data: dict) -> str:
    products = data.get("products", [])
    query = data.get("query", "")
    if products and isinstance(products, list):
        first = products[0]
        first_name = first.get("name", "?")
        first_price = first.get("price", "?")
        return (
            f'Поиск "{query}": найдено {len(products)} товаров, '
            f"лучший: {first_name} ({first_price}₽)"
        )
    return f'Поиск "{query}": найдено 0 товаров'


def _fmt_cart_link(data: dict) -> str:
    ps = data.get("price_summary", {})
    total = ps.get("total", data.get("total", "?"))
    count = ps.get("count", len(data.get("items", [])))
    link = data.get("cart_link", data.get("link", ""))
    return f"Корзина: {count} товаров, итого {total}₽, ссылка: {link}"


def _fmt_preferences(data: dict) -> str:
    prefs = data.get("preferences", data)
    if isinstance(prefs, dict):
        items = [f"{k}: {v}" for k, v in list(prefs.items())[:5]]
        return f"Предпочтения: {', '.join(items)}" if items else "Предпочтения: пусто"
    return f"Предпочтения: {str(prefs)[:MAX_SUMMARY_LENGTH]}"


def _fmt_recipe(data: dict) -> str:
    dish = data.get("dish", "?")
    ingredients = data.get("ingredients", [])
    count = len(ingredients) if isinstance(ingredients, list) else "?"
    return f'Рецепт "{dish}": {count} ингредиентов'


def _fmt_nutrition(data: dict) -> str:
    product = data.get("product", data.get("query", "?"))
    kcal = data.get("kcal", data.get("calories", "?"))
    return f'КБЖУ "{product}": {kcal} ккал/100г'


_NAME_DISPATCH: dict[str, Callable] = {
    "vkusvill_products_search": _fmt_products_search,
    "vkusvill_cart_link_create": _fmt_cart_link,
    "user_preferences_get": _fmt_preferences,
    "recipe_ingredients": _fmt_recipe,
    "nutrition_lookup": _fmt_nutrition,
}

_KEY_HEURISTICS: list[tuple[str, Callable]] = [
    ("products", _fmt_products_search),
    ("cart_link", _fmt_cart_link),
    ("preferences", _fmt_preferences),
    ("ingredients", _fmt_recipe),
]


def sanitize_history(history: list[MessageT]) -> list[MessageT]:
    """Удалить осиротевшие FUNCTION-сообщения из истории."""
    if len(history) <= 1:
        return history

    result: list[MessageT] = [history[0]]
    for msg in history[1:]:
        if msg_role(msg) == "function":
            last_role = msg_role(result[-1]) if result else ""
            last_fc = msg_function_call(result[-1]) if result else None
            if last_role == "assistant" and last_fc is not None:
                result.append(msg)
            else:
                logger.warning(
                    "Sanitize: удалено осиротевшее FUNCTION-сообщение (name=%s)",
                    msg_name(msg) or "?",
                )
            continue
        result.append(msg)

    return result


def summarize_tool_result(name: str | None, content: str) -> str:
    """Суммаризировать tool result для экономии токенов в истории."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        if len(content) > MAX_SUMMARY_LENGTH:
            return content[:MAX_SUMMARY_LENGTH] + "…"
        return content

    if not isinstance(data, dict):
        if len(content) > MAX_SUMMARY_LENGTH:
            return content[:MAX_SUMMARY_LENGTH] + "…"
        return content

    if name is not None:
        formatter = _NAME_DISPATCH.get(name)
        if formatter is not None:
            return formatter(data)
    else:
        for key, formatter in _KEY_HEURISTICS:
            if key in data:
                return formatter(data)

    if len(content) > MAX_SUMMARY_LENGTH:
        return content[:MAX_SUMMARY_LENGTH] + "…"
    return content


def trim_message_list(history: list[MessageT], max_history: int) -> list[MessageT]:
    """Обрезать историю с суммаризацией старых tool results."""
    if len(history) <= max_history:
        return history

    system = history[0]
    recent_start = len(history) - (max_history - 1)
    old_messages = history[1:recent_start]
    recent_messages = history[recent_start:]

    summarized_old: list[MessageT] = []
    for msg in old_messages:
        if msg_role(msg) == "function" and msg_content(msg):
            summary = summarize_tool_result(msg_name(msg), msg_content(msg))
            new_msg: MessageT = {"role": msg.get("role", "function"), "content": summary}
            if msg_name(msg) is not None:
                new_msg["name"] = msg_name(msg)
            summarized_old.append(new_msg)
        else:
            summarized_old.append(msg)

    result: list[MessageT] = [system, *summarized_old, *recent_messages]
    if len(result) > max_history:
        result = [system, *result[-(max_history - 1) :]]

    return sanitize_history(result)
