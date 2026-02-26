"""Response / intent analysis helpers extracted from ShoppingAgent."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.cart_output_renderer import looks_like_cart_ready_reply
from vkuswill_bot.agents.intent_markers import (
    ADDITIVE_CART_MARKERS,
    CART_INTENT_MARKERS,
    EXPLICIT_NEW_CART_MARKERS,
    MODIFY_EXISTING_CART_MARKERS,
    STATUS_QUERY_MARKERS,
)


def is_additive_cart_intent(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(marker in normalized for marker in ADDITIVE_CART_MARKERS)


def is_cart_intent(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(marker in normalized for marker in CART_INTENT_MARKERS)


def looks_like_partial_recipe_reply(text: str) -> bool:
    normalized = text.lower()
    if not normalized.strip():
        return False
    if "открыть корзину" in normalized or "share_basket" in normalized:
        return False
    markers = (
        "ингредиент",
        "подобрал",
        "подобрала",
        "рецепт",
        "список продуктов",
        "могу продолж",
        "если нужно",
    )
    return any(marker in normalized for marker in markers)


def should_start_fresh_context(
    *,
    text: str,
    history: list[dict[str, Any]] | None,
) -> bool:
    if not history or len(history) < 3:
        return False

    normalized = text.lower()
    if any(marker in normalized for marker in MODIFY_EXISTING_CART_MARKERS):
        return False

    if not is_cart_intent(text):
        return False

    last_assistant_text = ""
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            last_assistant_text = content
            break
    if not last_assistant_text:
        return False

    response_low = last_assistant_text.lower()
    has_last_cart = looks_like_cart_ready_reply(last_assistant_text) or (
        "<a href=" in response_low and "vkusvill.ru" in response_low
    )
    if not has_last_cart:
        return False

    # Статус/проверка не должны запускать новую корзину.
    if any(marker in normalized for marker in STATUS_QUERY_MARKERS):
        return False

    if any(marker in normalized for marker in EXPLICIT_NEW_CART_MARKERS):
        return True

    # Если корзина уже собрана и пользователь не просит явную модификацию,
    # трактуем сообщение как новый запрос на новую корзину.
    return True
