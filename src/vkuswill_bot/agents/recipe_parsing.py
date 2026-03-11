"""Parsing helpers for recipe ingredient extraction and normalization."""

from __future__ import annotations

import re
from typing import Any

from vkuswill_bot.agents.recipe_pantry import normalize_text
from vkuswill_bot.agents.recipe_quantity_calculator import RecipeQuantityCalculator
from vkuswill_bot.agents.recipe_search_vocabulary import (
    COMPOUND_PREFIX_WORDS,
    FALLBACK_QUERY_ALIASES,
    FALLBACK_QUERY_REMOVABLE_WORDS,
)
from vkuswill_bot.agents.tool_result_compactor import _safe_float
from vkuswill_bot.services.search_processor import SearchProcessor

_QUANTITY_UNIT_PATTERN = (
    r"кг|kg|г|гр|л|l|мл|ml|шт|штук|шт\.|"
    r"зубчик(?:а|ов)?|головк(?:а|и|ок)|лист(?:а|ов)?|"
    r"столов(?:ая|ые)\s+ложк[аи]|чайн(?:ая|ые)\s+ложк[аи]|"
    r"ст\.?\s*ложк[аи]?|ч\.?\s*ложк[аи]?|"
    r"ст\.?\s*л\.?|ч\.?\s*л\.?"
)
_RANGE_QUANTITY_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*({_QUANTITY_UNIT_PATTERN})",
    flags=re.IGNORECASE,
)
_SINGLE_QUANTITY_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*({_QUANTITY_UNIT_PATTERN})",
    flags=re.IGNORECASE,
)
_CLEAN_INGREDIENT_PREFIX_RE = (
    re.compile(r"^\s*собери(?:\s+мне)?\s+корзин[ауые]?\s+", flags=re.IGNORECASE),
    re.compile(r"^\s*добав(?:ь|ьте)(?:\s+в\s+корзин[ауые]?)?\s+", flags=re.IGNORECASE),
    re.compile(r"^\s*закаж(?:и|ите)\s+", flags=re.IGNORECASE),
)
_LEADING_AND_RE = re.compile(r"^\s*и\s+", flags=re.IGNORECASE)
_INLINE_INGREDIENT_RE = re.compile(
    rf"(?:^|[\n,;])\s*(?P<name>[^\n,;]+?)\s*[-–—:]\s*"
    rf"(?P<qty>\d+(?:[.,]\d+)?(?:\s*[-–—]\s*\d+(?:[.,]\d+)?)?\s*(?:{_QUANTITY_UNIT_PATTERN}))"
    r"(?=$|[\n,;])",
    flags=re.IGNORECASE,
)
_FALLBACK_QUERY_SPLIT_RE = (
    re.compile(r"\s+или\s+.*$", flags=re.IGNORECASE),
    re.compile(r"\s+для\s+.*$", flags=re.IGNORECASE),
)

_FALLBACK_QUERY_REMOVABLE_WORDS = FALLBACK_QUERY_REMOVABLE_WORDS
_FALLBACK_QUERY_ALIASES = FALLBACK_QUERY_ALIASES
_COMPOUND_PREFIX_WORDS = COMPOUND_PREFIX_WORDS


def parse_quantity_hint(text: str) -> tuple[float, str, str] | None:
    """Parse quantity + normalized unit from free text."""
    normalized = normalize_text(text)
    if not normalized:
        return None
    match = _RANGE_QUANTITY_RE.search(normalized)
    if match is not None:
        low = _safe_float(match.group(1), default=-1.0)
        high = _safe_float(match.group(2), default=-1.0)
        if low > 0 and high > 0:
            unit = RecipeQuantityCalculator._normalize_unit(match.group(3))
            if unit:
                return max(low, high), unit, match.group(0)

    match = _SINGLE_QUANTITY_RE.search(normalized)
    if match is None:
        return None
    quantity = _safe_float(match.group(1), default=-1.0)
    if quantity <= 0:
        return None
    unit = RecipeQuantityCalculator._normalize_unit(match.group(2))
    if not unit:
        return None
    return quantity, unit, match.group(0)


def clean_structured_ingredient_name(name_raw: str) -> str:
    """Strip command prefixes from ingredient name fragments."""
    name = name_raw.strip(" ,.;:").strip()
    if not name:
        return ""
    for pattern in _CLEAN_INGREDIENT_PREFIX_RE:
        name = pattern.sub("", name).strip()
    name = _LEADING_AND_RE.sub("", name).strip()
    return name


def build_fallback_search_queries(*, query: str, ingredient_name: str) -> list[str]:
    """Build deduplicated fallback queries from strict to broad."""
    candidates: list[str] = []
    for raw in (
        query,
        SearchProcessor.clean_search_query(query),
        ingredient_name,
        SearchProcessor.clean_search_query(ingredient_name),
    ):
        for value in _expand_fallback_query_variants(str(raw).strip()):
            if value and value not in candidates:
                candidates.append(value)
    return candidates


def _extract_core_noun(text: str) -> str | None:
    """Extract the core food noun by stripping compound prefixes.

    For "филе куриной грудки" -> "грудки", for "стебель сельдерея" -> "сельдерея".
    Returns None when no simplification is possible.
    """
    tokens = text.strip().lower().split()
    if len(tokens) < 2:
        return None
    norm_first = normalize_text(tokens[0]).replace("ё", "е")
    if norm_first in _COMPOUND_PREFIX_WORDS:
        remainder = " ".join(tokens[1:]).strip()
        cleaned = " ".join(
            t
            for t in remainder.split()
            if normalize_text(t).replace("ё", "е") not in _FALLBACK_QUERY_REMOVABLE_WORDS
        ).strip()
        if cleaned and cleaned != text.strip().lower():
            return cleaned
    return None


def _expand_fallback_query_variants(raw: str) -> list[str]:
    original = str(raw).strip(" ,.;:").strip()
    normalized = SearchProcessor.clean_search_query(original).strip(" ,.;:").strip()
    if not original and not normalized:
        return []
    variants: list[str] = []
    if original:
        variants.append(original)
    if normalized and normalized not in variants:
        variants.append(normalized)
    alias = _FALLBACK_QUERY_ALIASES.get(normalized.lower().replace("ё", "е"))
    if alias:
        variants.append(alias)
    for pattern in _FALLBACK_QUERY_SPLIT_RE:
        simplified = pattern.sub("", normalized).strip(" ,.;:").strip()
        if simplified:
            variants.append(simplified)
    for base in list(variants):
        tokens = [
            token
            for token in re.split(r"\s+", base)
            if token
            and normalize_text(token).replace("ё", "е") not in _FALLBACK_QUERY_REMOVABLE_WORDS
        ]
        simplified = " ".join(tokens).strip()
        if simplified:
            variants.append(simplified)

    core_noun = _extract_core_noun(normalized)
    if core_noun:
        variants.append(core_noun)

    deduped: list[str] = []
    seen_keys: set[str] = set()
    for value in variants:
        raw_value = str(value).strip(" ,.;:").strip()
        cleaned_key = SearchProcessor.clean_search_query(raw_value).strip(" ,.;:").strip()
        if not raw_value or not cleaned_key or cleaned_key in seen_keys:
            continue
        seen_keys.add(cleaned_key)
        deduped.append(raw_value)
    return deduped


def extract_structured_ingredient_requests(user_text: str) -> list[dict[str, Any]]:
    """Extract structured ingredient rows from user free text."""
    if not user_text.strip():
        return []
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, float, str]] = set()

    def _append_row(name_raw: str, quantity: float, unit: str) -> None:
        cleaned_name = clean_structured_ingredient_name(name_raw)
        if not cleaned_name:
            return
        search_query = SearchProcessor.clean_search_query(cleaned_name) or cleaned_name
        key = (search_query.lower(), round(quantity, 4), unit)
        if key in seen_keys:
            return
        seen_keys.add(key)
        rows.append(
            {
                "name": cleaned_name,
                "quantity": quantity,
                "unit": unit,
                "search_query": search_query,
            }
        )

    normalized_text = user_text.replace("\r", "\n")
    for match in _INLINE_INGREDIENT_RE.finditer(normalized_text):
        name_part = str(match.group("name") or "").strip()
        qty_part = str(match.group("qty") or "").strip()
        parsed = parse_quantity_hint(qty_part)
        if parsed is None:
            continue
        quantity, unit, _fragment = parsed
        _append_row(name_part, quantity, unit)

    for raw_line in normalized_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _INLINE_INGREDIENT_RE.search(line):
            continue
        line = re.sub(r"^\s*\d+[.)]\s*", "", line).strip()
        line = line.lstrip("-• ").strip()
        if not line:
            continue
        name_part = line
        quantity_part = line
        split_match = re.match(r"^(.*?)(?:\s*[-–—:]\s*)(.+)$", line)
        if split_match is not None:
            left = split_match.group(1).strip()
            right = split_match.group(2).strip()
            if left and right:
                name_part = left
                quantity_part = right
        parsed = parse_quantity_hint(quantity_part)
        if parsed is None and quantity_part != line:
            parsed = parse_quantity_hint(line)
        if parsed is None:
            continue
        quantity, unit, _fragment = parsed
        _append_row(name_part, quantity, unit)
    return rows[:30]


def normalize_recipe_ingredient_row(row_raw: Any) -> dict[str, Any]:
    """Normalize ingredient row (dict or str) into unified payload."""
    if isinstance(row_raw, dict):
        row = dict(row_raw)
        quantity = _safe_float(row.get("quantity"), default=1.0)
        if quantity <= 0:
            quantity = 1.0
        unit = RecipeQuantityCalculator._normalize_unit(row.get("unit")) or "шт"
        row["quantity"] = quantity
        row["unit"] = unit
        search_query = str(row.get("search_query", "")).strip()
        if not search_query:
            search_query = SearchProcessor.clean_search_query(str(row.get("name", "")).strip())
        if search_query:
            row["search_query"] = search_query
        return row
    if not isinstance(row_raw, str):
        return {}

    text = row_raw.strip()
    if not text:
        return {}
    cleaned = text.replace("по вкусу", "").strip(" ,.-")
    parsed = parse_quantity_hint(cleaned)
    if parsed is not None:
        parsed_q, parsed_unit, fragment = parsed
    else:
        parsed_q, parsed_unit, fragment = RecipeQuantityCalculator.parse_quantity_and_unit(cleaned)
    if fragment:
        name = re.sub(re.escape(fragment), " ", cleaned, flags=re.IGNORECASE).strip(" ,.-")
    else:
        name = cleaned
    if not name:
        name = cleaned
    query = SearchProcessor.clean_search_query(name) or name
    return {
        "name": name or text,
        "search_query": query,
        "quantity": parsed_q if parsed_q is not None else 1.0,
        "unit": parsed_unit or "шт",
    }
