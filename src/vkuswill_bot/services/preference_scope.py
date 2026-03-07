"""Helpers for detecting scoped user preferences."""

from __future__ import annotations

from typing import Any

_DIET_MARKERS: tuple[str, ...] = (
    "vegan",
    "веган",
    "vegetarian",
    "вегетариан",
    "без мяса",
    "halal",
    "халяль",
    "халал",
)

_GROUP_SCOPED_MARKERS: tuple[str, ...] = (
    "for one person",
    "one person",
    "one of",
    "in family",
    "family member",
    "один человек",
    "для одного",
    "один из",
    "в семье",
    "член семьи",
)


def _normalize_preference_text(value: Any) -> str:
    return " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())


def is_group_scoped_diet_preference(value: Any) -> bool:
    """Return True when a diet preference applies only to part of a family/group."""
    low = _normalize_preference_text(value)
    if not low:
        return False
    if not any(marker in low for marker in _DIET_MARKERS):
        return False
    return any(marker in low for marker in _GROUP_SCOPED_MARKERS)


def split_scoped_preferences(
    preferences: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split raw preferences into global rows and group-scoped rows."""
    regular: list[dict[str, Any]] = []
    scoped: list[dict[str, Any]] = []
    if not isinstance(preferences, list):
        return regular, scoped

    for row in preferences:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category", "")).strip().lower()
        preference = str(row.get("preference", "")).strip()
        if not category or not preference:
            continue
        normalized_row = {"category": category, "preference": preference}
        if category in {"diet", "диета"} and is_group_scoped_diet_preference(preference):
            scoped.append(normalized_row)
        else:
            regular.append(normalized_row)
    return regular, scoped
