"""Парсинг предпочтений пользователя из JSON-ответа."""

from __future__ import annotations

import json


def parse_preferences(result_text: str) -> dict[str, str]:
    """Извлечь предпочтения из результата user_preferences_get.

    Returns:
        Словарь {категория_lower: preference_text}.
    """
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return {}

    prefs = data.get("preferences", [])
    if not isinstance(prefs, list):
        return {}

    result: dict[str, str] = {}
    for item in prefs:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).strip().lower()
        preference = str(item.get("preference", "")).strip()
        if category and preference:
            result[category] = preference
    return result
