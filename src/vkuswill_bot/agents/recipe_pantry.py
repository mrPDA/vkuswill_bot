"""Pantry and ingredient text classification helpers."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.pantry_tags import (
    PANTRY_TAG_PEPPER,
    PANTRY_TAG_SALT,
    PANTRY_TAG_SUGAR,
    PANTRY_TAG_WATER,
)


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy lexical checks."""
    return text.strip().lower().replace("ё", "е")


def looks_like_pepper_vegetable(text: str) -> bool:
    """Return True when text points to pepper as a vegetable."""
    normalized = normalize_text(text)
    vegetable_markers = (
        "болгар",
        "сладк",
        "чили",
        "халапень",
        "пепперони",
        "перец овощ",
        "фаршированн",
    )
    return any(marker in normalized for marker in vegetable_markers)


def is_explicit_seasoning_pepper_request(text: str) -> bool:
    """Return True when text explicitly requests seasoning pepper."""
    normalized = normalize_text(text)
    if "перец" not in normalized:
        return False
    spice_markers = (
        "черн",
        "красн",
        "бел",
        "молот",
        "горош",
        "душист",
        "приправ",
    )
    if any(marker in normalized for marker in spice_markers):
        return True
    if looks_like_pepper_vegetable(normalized):
        return False
    return "соль" in normalized or "сахар" in normalized


def _is_plain_water(text: str) -> bool:
    """Return True when text refers to plain cooking/drinking water, not a compound."""
    if "вод" not in text:
        return False
    non_water_markers = (
        "водк",
        "водор",
        "минерал",
        "газир",
        "лимонад",
        "сок",
        "компот",
        "бульон",
        "розов",
    )
    return not any(m in text for m in non_water_markers)


def detect_pantry_tag_for_ingredient(row: dict[str, Any]) -> str | None:
    """Resolve pantry tag for an ingredient row if applicable."""
    name = normalize_text(str(row.get("name", "")))
    query = normalize_text(str(row.get("search_query", "")))
    text = f"{name} {query}".strip()
    if not text:
        return None
    if "соль" in text:
        return PANTRY_TAG_SALT
    if "сахар" in text:
        return PANTRY_TAG_SUGAR
    if "перец" in text and not looks_like_pepper_vegetable(text):
        return PANTRY_TAG_PEPPER
    if _is_plain_water(text):
        return PANTRY_TAG_WATER
    return None


def extract_explicit_pantry_requests(user_text: str) -> set[str]:
    """Extract pantry ingredients explicitly requested by user."""
    normalized = normalize_text(user_text)
    requested: set[str] = set()
    if "соль" in normalized:
        requested.add(PANTRY_TAG_SALT)
    if "сахар" in normalized:
        requested.add(PANTRY_TAG_SUGAR)
    if is_explicit_seasoning_pepper_request(normalized):
        requested.add(PANTRY_TAG_PEPPER)
    if _is_plain_water(normalized):
        requested.add(PANTRY_TAG_WATER)
    return requested


def has_explicit_egg_pack_request(text: str) -> bool:
    """Detect explicit request for egg packs."""
    normalized = normalize_text(text)
    if not any(stem in normalized for stem in ("яйц", "яиц", "яйк")):
        return False
    pack_markers = ("упаков", "десят", "дюжин")
    return any(marker in normalized for marker in pack_markers)
