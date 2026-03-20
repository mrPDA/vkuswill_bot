"""Shared heuristics for cart-like plain product lists."""

from __future__ import annotations

import re

_SEGMENT_SPLIT_RE = re.compile(r"[,;\n]+|\s+и\s+")
_UNIT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(%|шт\w*|гр\w*|г\b|кг\w*|мл\w*|л\b|литр\w*)?",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[а-яё-]{2,}", re.IGNORECASE)
_NON_PRODUCT_WORDS = frozenset(
    {
        "а",
        "без",
        "бы",
        "в",
        "вы",
        "где",
        "да",
        "для",
        "дела",
        "и",
        "из",
        "или",
        "как",
        "какая",
        "какие",
        "какой",
        "какое",
        "ли",
        "мне",
        "можно",
        "мы",
        "на",
        "надо",
        "не",
        "но",
        "ну",
        "нужно",
        "пожалуйста",
        "подскажи",
        "почему",
        "привет",
        "просто",
        "расскажи",
        "спасибо",
        "только",
        "ты",
        "у",
        "что",
        "это",
        "я",
    }
)
_DISQUALIFYING_PHRASES = (
    "что можно приготовить",
    "что приготовить",
    "как приготовить",
    "как сделать",
    "рецепт",
    "приготов",
    "свари",
    "испеч",
    "статус",
    "привяз",
)


def _is_product_segment(segment: str) -> bool:
    cleaned = _UNIT_RE.sub(" ", segment.lower().replace("ё", "е"))
    words = [word for word in _WORD_RE.findall(cleaned) if word not in _NON_PRODUCT_WORDS]
    return 1 <= len(words) <= 3


def looks_like_cart_product_list(text: str) -> bool:
    """Return True for short grocery lists without explicit cart verbs."""
    normalized = text.strip().lower().replace("ё", "е")
    if not normalized or "?" in normalized:
        return False
    if any(phrase in normalized for phrase in _DISQUALIFYING_PHRASES):
        return False
    if normalized.startswith("без "):
        return False

    segments = [segment.strip(" .,!:-") for segment in _SEGMENT_SPLIT_RE.split(normalized)]
    valid_segments = [segment for segment in segments if segment and _is_product_segment(segment)]
    return len(valid_segments) >= 2
