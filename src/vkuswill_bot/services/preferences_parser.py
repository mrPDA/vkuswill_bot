"""Парсинг предпочтений пользователя из JSON-ответа."""

from __future__ import annotations

import json
from typing import Any

_DIET_MARKERS: dict[str, tuple[str, ...]] = {
    "vegan": ("vegan", "веган", "plant based", "plant-based", "plant_based"),
    "vegetarian": ("vegetarian", "вегетариан", "без мяса"),
    "halal": ("halal", "халяль", "халал"),
    "default": ("default", "обыч", "стандарт", "omnivore", "омнивор"),
}


def _empty_profile() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "hard_constraints": {},
        "soft_preferences": {
            "cuisines": [],
            "liked_ingredients": [],
            "disliked_ingredients": [],
            "freeform_preferences": {},
        },
        "operational_preferences": {},
    }


def _to_bool(value: str) -> bool | None:
    low = value.strip().lower()
    if low in {"true", "1", "yes", "y", "да", "д"}:
        return True
    if low in {"false", "0", "no", "n", "нет", "н"}:
        return False
    return None


def _to_int(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return None


def _split_values(value: str) -> list[str]:
    separators = [",", ";", "\n", "|"]
    chunks = [value]
    for sep in separators:
        next_chunks: list[str] = []
        for chunk in chunks:
            next_chunks.extend(chunk.split(sep))
        chunks = next_chunks

    result: list[str] = []
    seen: set[str] = set()
    for raw in chunks:
        item = raw.strip()
        if not item:
            continue
        marker = item.lower()
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def _norm_category(category: str) -> tuple[str, str]:
    normalized = " ".join(category.strip().lower().split())
    return normalized, normalized.replace(" ", "_")


def _canonicalize_diet(value: object) -> str:
    low = " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())
    if not low:
        return ""
    for canonical, markers in _DIET_MARKERS.items():
        if any(marker in low for marker in markers):
            return canonical
    return low


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(profile)
    hard = normalized.get("hard_constraints")
    if isinstance(hard, dict):
        normalized_hard = dict(hard)
        if "diet" in normalized_hard:
            canonical = _canonicalize_diet(normalized_hard.get("diet"))
            if canonical:
                normalized_hard["diet"] = canonical
            else:
                normalized_hard.pop("diet", None)
        normalized["hard_constraints"] = normalized_hard
    return normalized


def _apply_legacy_preference(
    profile: dict[str, Any],
    *,
    category: str,
    preference: str,
) -> None:
    hard = profile["hard_constraints"]
    soft = profile["soft_preferences"]
    operational = profile["operational_preferences"]
    freeform = soft["freeform_preferences"]

    cat, cat_alias = _norm_category(category)
    values = _split_values(preference)
    bool_value = _to_bool(preference)
    int_value = _to_int(preference)

    if cat_alias in {"diet", "диета"}:
        canonical_diet = _canonicalize_diet(preference)
        if canonical_diet:
            hard["diet"] = canonical_diet
    elif cat_alias in {
        "allergies",
        "allergy",
        "аллергии",
        "аллергены",
        "allergens",
        "allergens_excluded",
    }:
        hard["allergens_excluded"] = values
    elif cat_alias in {"no_pork", "без_свинины"}:
        hard["no_pork"] = bool_value if bool_value is not None else preference
    elif cat_alias in {"fasting_mode", "пост"}:
        hard["fasting_mode"] = preference
    elif cat_alias in {"cuisine", "cuisines", "кухня", "кухни"}:
        soft["cuisines"] = values
    elif cat_alias in {"liked_ingredients", "likes", "favorite", "люблю", "любимые"}:
        soft["liked_ingredients"] = values
    elif cat_alias in {
        "disliked_ingredients",
        "dislikes",
        "avoid",
        "не_люблю",
        "нелюбимые",
        "исключить",
    }:
        soft["disliked_ingredients"] = values
    elif cat_alias in {"spice_level", "острота"}:
        soft["spice_level"] = preference
    elif cat_alias in {"high_protein", "высокий_белок"}:
        soft["high_protein"] = bool_value if bool_value is not None else preference
    elif cat_alias in {"low_carb", "меньше_углеводов"}:
        soft["low_carb"] = bool_value if bool_value is not None else preference
    elif cat_alias in {"meal_types", "приемы_пищи", "приемы_еды"}:
        operational["meal_types"] = values
    elif cat_alias in {"max_cook_time_min", "макс_время_готовки"}:
        operational["max_cook_time_min"] = int_value if int_value is not None else preference
    elif cat_alias in {"max_dishes", "макс_блюд"}:
        operational["max_dishes"] = int_value if int_value is not None else preference
    else:
        freeform[cat] = preference


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


def parse_preference_profile(result_text: str) -> dict[str, Any]:
    """Извлечь структурированный профиль предпочтений из user_preferences_get.

    Поддерживает 2 формата:
    1) Новый: поле ``profile`` в JSON-ответе.
    2) Legacy fallback: детерминированно маппит ``category -> preference``
       в ``hard/soft/operational`` + ``soft.freeform_preferences``.
    """
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return {}

    if not isinstance(data, dict):
        return {}

    profile = data.get("profile")
    if isinstance(profile, dict):
        return _normalize_profile(profile)

    prefs = data.get("preferences", [])
    if not isinstance(prefs, list):
        return {}

    normalized_profile = _empty_profile()
    has_entries = False
    for item in prefs:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).strip().lower()
        preference = str(item.get("preference", "")).strip()
        if not category or not preference:
            continue
        _apply_legacy_preference(
            normalized_profile,
            category=category,
            preference=preference,
        )
        has_entries = True

    if not has_entries:
        return {}
    return normalized_profile
