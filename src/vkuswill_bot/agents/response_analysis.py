"""Response / intent analysis helpers extracted from ShoppingAgent."""

from __future__ import annotations

import re
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

_MEAL_TYPE_MARKER_RE = re.compile(
    r"(?:(?:для|на)\s+)?"
    r"(завтрак\w*|обед\w*|ужин\w*|десерт\w*|полдник\w*|перекус\w*)",
    re.IGNORECASE,
)
_SERVINGS_RE = re.compile(
    r"(?:на\s+)?(?:\d+\s*порц\w*|\d+\s*(?:чел\w*|персон\w*)|"
    r"двоих|двух|троих|тр[её]х|четверых|четыр[её]х)",
    re.IGNORECASE,
)
_SEGMENT_WORD_RE = re.compile(r"[а-яё-]{2,}", re.IGNORECASE)
_SEGMENT_STOP_WORDS = frozenset(
    {
        "а",
        "без",
        "бы",
        "для",
        "еще",
        "ещё",
        "и",
        "или",
        "к",
        "мне",
        "на",
        "но",
        "нужен",
        "нужна",
        "нужно",
        "нужны",
        "плюс",
        "подбери",
        "подобрать",
        "собери",
        "что",
        "чтонибудь",
        "что-нибудь",
        "все",
        "всё",
    }
)


def _segment_has_explicit_course_content(segment: str) -> bool:
    cleaned = _SERVINGS_RE.sub(" ", segment.lower().replace("ё", "е"))
    cleaned = re.sub(r"^[\s\-—–:;,]+", "", cleaned)
    cleaned = re.sub(r"[\s\-—–:;,]+$", "", cleaned)
    cleaned = re.sub(r"\s+(?:и|плюс|все|всё|ещё|еще)\s*$", "", cleaned)
    words = [
        word
        for word in _SEGMENT_WORD_RE.findall(cleaned)
        if word not in _SEGMENT_STOP_WORDS
    ]
    return bool(words)


def count_expected_recipe_courses(text: str) -> int:
    """Estimate number of distinct dishes/courses the user wants to cook.

    Returns >= 2 when a multi-course request is detected (e.g. breakfast + lunch + dinner).
    """
    low = text.lower()
    meal_count = sum(1 for m in _MEAL_TYPES if m in low)
    dish_count = sum(1 for d in _DISH_STEMS if d in low)
    return max(meal_count, dish_count)


def count_explicit_recipe_courses(text: str) -> int:
    """Count only courses that include an explicit dish or ingredients.

    This excludes abstract meal-slot requests like "завтрак и обед", where
    meal types are mentioned but no concrete dishes are given.
    """
    low = text.lower()
    dish_count = sum(1 for d in _DISH_STEMS if d in low)
    markers = list(_MEAL_TYPE_MARKER_RE.finditer(low))
    explicit_meal_courses = 0

    for index, marker in enumerate(markers):
        seg_start = marker.end()
        seg_end = markers[index + 1].start() if index + 1 < len(markers) else len(low)
        if _segment_has_explicit_course_content(low[seg_start:seg_end]):
            explicit_meal_courses += 1

    return max(dish_count, explicit_meal_courses)


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
