"""Base units/text utilities for recipe quantity calculation."""

from __future__ import annotations

import contextlib
import html
import math
import re
from typing import Any


class RecipeQuantityBase:
    _EGG_PACK_SIZE = 10

    _KILOGRAM_UNITS = frozenset({"кг", "kg"})
    _GRAM_UNITS = frozenset({"г", "гр", "gram", "grams"})
    _LITER_UNITS = frozenset({"л", "l"})
    _MILLILITER_UNITS = frozenset({"мл", "ml"})
    _PIECE_UNITS = frozenset({"шт", "штука", "штук", "piece", "pieces"})
    _TABLESPOON_UNITS = frozenset({"ст.л.", "ст.л", "ст л", "столовая ложка", "столовые ложки"})
    _TEASPOON_UNITS = frozenset({"ч.л.", "ч.л", "ч л", "чайная ложка", "чайные ложки"})
    _GARLIC_CLOVE_UNITS = frozenset({"зубчик", "clove", "cloves"})
    _GARLIC_HEAD_UNITS = frozenset({"головка"})
    _LEAF_UNITS = frozenset({"лист"})
    _DISCRETE_UNITS = frozenset({"шт", "уп", "пач", "бут", "бан", "пак"})
    _MASSY_MARKERS = (
        "мук",
        "сахар",
        "соль",
        "круп",
        "рис",
        "греч",
        "спец",
        "какао",
        "крахмал",
    )
    _LIQUID_MARKERS = (
        "масл",
        "молок",
        "сливк",
        "вода",
        "кефир",
        "йогурт",
        "соус",
        "уксус",
        "сок",
    )
    _AVG_PIECE_KG_BY_STEM: tuple[tuple[str, float], ...] = (
        ("картоф", 0.15),
        ("лук", 0.10),
        ("морков", 0.08),
        ("свекл", 0.20),
        ("помидор", 0.12),
        ("огур", 0.12),
        ("яблок", 0.18),
        ("банан", 0.13),
        ("перец", 0.15),
        ("кабач", 0.25),
        ("баклаж", 0.25),
        ("чеснок", 0.03),
    )

    @classmethod
    def parse_quantity_and_unit(cls, raw_text: str) -> tuple[float | None, str | None, str]:
        text = cls._normalize_text(raw_text)
        if not text:
            return None, None, ""
        patterns = (
            r"(\d+(?:[.,]\d+)?)\s*(кг|kg|г|гр|л|l|мл|ml|шт|штук|шт\.|\s*"
            r"зубчик(?:а|ов)?|головк(?:а|и|ок)|лист(?:а|ов)?|"
            r"ст\.?\s*л\.?|ч\.?\s*л\.?)",
            r"(\d+(?:[.,]\d+)?)\s*(столов(?:ая|ые)\s+ложк[аи]|чайн(?:ая|ые)\s+ложк[аи])",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            quantity = cls._safe_float(match.group(1), default=-1.0)
            if quantity <= 0:
                continue
            unit = cls._normalize_unit(match.group(2))
            if not unit:
                continue
            return quantity, unit, match.group(0)
        return None, None, ""

    @staticmethod
    def _round_up_step(value: float, *, step: float, minimum: float) -> float:
        if value <= 0:
            value = minimum
        rounded = math.ceil(value / step) * step
        return round(max(minimum, rounded), 3)

    @staticmethod
    def _safe_float(value: Any, *, default: float) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                return float(value.replace(",", "."))
        return default

    @classmethod
    def _normalize_unit(cls, raw_unit: Any) -> str:
        text = cls._normalize_text(raw_unit)
        aliases = {
            "кг": "кг",
            "kg": "kg",
            "г": "г",
            "гр": "г",
            "gram": "г",
            "grams": "г",
            "л": "л",
            "l": "l",
            "мл": "мл",
            "ml": "ml",
            "шт": "шт",
            "шт.": "шт",
            "штук": "шт",
            "штука": "шт",
            "piece": "шт",
            "pieces": "шт",
            "ст.л": "ст.л.",
            "ст.л.": "ст.л.",
            "ст л": "ст.л.",
            "ст. л": "ст.л.",
            "ст. ложка": "ст.л.",
            "ст. ложки": "ст.л.",
            "столовая ложка": "ст.л.",
            "столовые ложки": "ст.л.",
            "ч.л": "ч.л.",
            "ч.л.": "ч.л.",
            "ч л": "ч.л.",
            "ч. л": "ч.л.",
            "ч. ложка": "ч.л.",
            "ч. ложки": "ч.л.",
            "чайная ложка": "ч.л.",
            "чайные ложки": "ч.л.",
            "зубчик": "зубчик",
            "зубчика": "зубчик",
            "зубчиков": "зубчик",
            "головка": "головка",
            "головки": "головка",
            "головок": "головка",
            "лист": "лист",
            "листа": "лист",
            "листов": "лист",
        }
        return aliases.get(text, text)

    @staticmethod
    def _normalize_text(raw_text: Any) -> str:
        text = html.unescape(str(raw_text or "")).strip().lower().replace("ё", "е")
        return re.sub(r"\s+", " ", text)

    @classmethod
    def _extract_pack_number(cls, text: str) -> int:
        normalized = cls._normalize_text(text)
        if "десят" in normalized:
            return 10
        match = re.search(r"(\d+)\s*(?:шт|штук|шт\.)\b", normalized, flags=re.IGNORECASE)
        if match is None:
            return 0
        return max(0, int(match.group(1)))

    @classmethod
    def _avg_piece_kg(cls, ingredient_name: str) -> float:
        normalized = cls._normalize_text(ingredient_name)
        for stem, weight in cls._AVG_PIECE_KG_BY_STEM:
            if stem in normalized:
                return weight
        return 0.15

    @classmethod
    def _is_massy_ingredient(cls, ingredient_name: str) -> bool:
        normalized = cls._normalize_text(ingredient_name)
        return any(marker in normalized for marker in cls._MASSY_MARKERS)

    @classmethod
    def _is_liquid_ingredient(cls, ingredient_name: str) -> bool:
        normalized = cls._normalize_text(ingredient_name)
        return any(marker in normalized for marker in cls._LIQUID_MARKERS)

    @classmethod
    def _is_garlic_ingredient(cls, ingredient_name: str) -> bool:
        normalized = cls._normalize_text(ingredient_name)
        return "чеснок" in normalized

    @classmethod
    def _is_bay_leaf_ingredient(cls, ingredient_name: str) -> bool:
        normalized = cls._normalize_text(ingredient_name)
        return "лавров" in normalized or "лавруш" in normalized

    @classmethod
    def _searchable_product_text(cls, item: dict[str, Any]) -> str:
        name = cls._normalize_text(item.get("name"))
        description = cls._normalize_text(item.get("description"))
        return f"{name} {description}".strip()
