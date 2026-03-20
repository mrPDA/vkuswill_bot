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
from vkuswill_bot.services.cart_intent_heuristics import looks_like_cart_product_list

_MEAL_TYPES = frozenset({
    "завтрак", "обед", "ужин", "десерт", "полдник", "перекус",
})

_DISH_STEMS = (
    "борщ", "суп", "паста", "лазань", "пицц", "салат", "карбонар",
    "плов", "окрошк", "блин", "оладь", "каш", "омлет", "стейк",
    "чизкейк", "шарлотк", "котлет", "пельмен", "вареник",
    "шашлык", "ролл", "суши", "бургер", "тефтел", "запеканк",
    "ризотто", "гуляш", "азу", "рагу",
)


def count_expected_recipe_courses(text: str) -> int:
    """Estimate number of distinct dishes/courses the user wants to cook.

    Returns >= 2 when a multi-course request is detected (e.g. breakfast + lunch + dinner).
    """
    low = text.lower()
    meal_count = sum(1 for m in _MEAL_TYPES if m in low)
    dish_count = sum(1 for d in _DISH_STEMS if d in low)
    return max(meal_count, dish_count)


def is_additive_cart_intent(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(marker in normalized for marker in ADDITIVE_CART_MARKERS)


def is_cart_intent(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(marker in normalized for marker in CART_INTENT_MARKERS) or (
        looks_like_cart_product_list(user_text)
    )


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


def looks_like_textual_tool_call_reply(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if "<tool_call>" in normalized:
        return True
    if normalized.startswith("{") and '"name"' in normalized and '"arguments"' in normalized:
        return True
    return bool(
        '"name": "vkusvill_' in normalized
        or '"name": "recipe_' in normalized
        or '"name": "user_preferences_get"' in normalized
    )


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
