"""Helpers for inferring meal-plan audience size from user text."""

from __future__ import annotations

import re

from vkuswill_bot.services.tool_input_normalizers import normalize_colloquial_numerals

_PEOPLE_RE = re.compile(r"для\s+(\d+)\s+(?:чел|человек)", flags=re.IGNORECASE)
_PEOPLE_WORD_RE = re.compile(
    r"(?:для|на)\s+"
    r"(одного|одной|двоих|троих|четверых|пятерых|шестерых|семерых|"
    r"восьмерых|девятерых|десятерых)\b",
    flags=re.IGNORECASE,
)
_CHILD_COUNT_RE = re.compile(r"(\d+)\s*(?:ребен(?:ок|ка|ку|ком)|дет(?:и|ей|ям|ьми))", re.IGNORECASE)
_SOLO_RE = re.compile(
    r"для\s+(?:одного|одной|себя|меня)|на\s+одного|для\s+1\b",
    re.IGNORECASE,
)
_SEGMENTED_ADULTS_RE = re.compile(r"один[^.]{0,60}(другой|второй)", re.IGNORECASE)

_PEOPLE_WORD_VALUES = {
    "одного": 1,
    "одной": 1,
    "двоих": 2,
    "троих": 3,
    "четверых": 4,
    "пятерых": 5,
    "шестерых": 6,
    "семерых": 7,
    "восьмерых": 8,
    "девятерых": 9,
    "десятерых": 10,
}


def _extract_explicit_people_total(text: str, *, max_people: int = 20) -> int | None:
    normalized = normalize_colloquial_numerals(text).lower().replace("ё", "е")
    if _SOLO_RE.search(normalized):
        return 1

    match = _PEOPLE_RE.search(normalized)
    if match:
        try:
            return max(1, min(max_people, int(match.group(1))))
        except ValueError:
            return None

    word_match = _PEOPLE_WORD_RE.search(normalized)
    if not word_match:
        return None
    return max(1, min(max_people, _PEOPLE_WORD_VALUES.get(word_match.group(1), 1)))


def _extract_child_count_hint(text: str) -> int:
    low = text.lower()
    if "реб" not in low and "дет" not in low:
        return 0
    match = _CHILD_COUNT_RE.search(low)
    if match and match.group(1).isdigit():
        return max(1, int(match.group(1)))
    return 1


def parse_request_people_total(text: str, *, default: int = 1, max_people: int = 20) -> int:
    explicit = _extract_explicit_people_total(text, max_people=max_people)
    if explicit is not None:
        return explicit

    inferred_adults = 2 if _SEGMENTED_ADULTS_RE.search(text.lower()) else 0
    inferred_children = _extract_child_count_hint(text)
    inferred_total = max(default, inferred_adults + inferred_children, inferred_children)
    return max(1, min(max_people, inferred_total))
