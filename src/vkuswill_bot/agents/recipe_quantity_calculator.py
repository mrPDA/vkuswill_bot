"""Детерминированный расчет purchase-количества по рецептурной дозировке."""

from __future__ import annotations

import contextlib
import html
import math
import re
from typing import Any

_EGG_PACK_SIZE = 10


class RecipeQuantityCalculator:
    """Детерминированный расчет purchase-количества по рецептурной дозировке."""

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
    def calculate_purchase_q(cls, ingredient: dict[str, Any], item: dict[str, Any]) -> int | float:
        _requested_q, purchase_q = cls.calculate_requested_and_purchase_q(
            ingredient=ingredient,
            item=item,
        )
        return purchase_q

    @classmethod
    def calculate_requested_and_purchase_q(
        cls,
        *,
        ingredient: dict[str, Any],
        item: dict[str, Any],
    ) -> tuple[float, int | float]:
        quantity = cls._safe_float(ingredient.get("quantity"), default=1.0)
        if quantity <= 0:
            quantity = 1.0
        ingredient_name = cls._normalize_text(ingredient.get("name"))
        ingredient_unit = cls._normalize_unit(ingredient.get("unit"))
        product_name = cls._normalize_text(item.get("name"))
        product_unit = cls._normalize_unit(item.get("unit")) or "шт"

        if product_unit in cls._KILOGRAM_UNITS:
            required_kg = cls._required_kilograms(
                quantity=quantity,
                ingredient_unit=ingredient_unit,
                ingredient_name=ingredient_name or product_name,
            )
            if required_kg <= 0:
                required_kg = 0.1
            return required_kg, cls._round_up_step(required_kg, step=0.1, minimum=0.1)

        if product_unit in cls._LITER_UNITS:
            required_l = cls._required_liters(
                quantity=quantity,
                ingredient_unit=ingredient_unit,
                ingredient_name=ingredient_name,
            )
            if required_l <= 0:
                required_l = 0.1
            return required_l, cls._round_up_step(required_l, step=0.1, minimum=0.1)

        if product_unit in cls._DISCRETE_UNITS:
            purchase_units = cls._required_discrete_units(
                quantity=quantity,
                ingredient_unit=ingredient_unit,
                ingredient_name=ingredient_name,
                item=item,
                product_name=product_name,
            )
            requested_units = purchase_units if purchase_units > 0 else 1.0
            return requested_units, max(1, math.ceil(requested_units))

        # Фолбэк для нестандартных unit.
        requested = quantity if quantity > 0 else 1.0
        return requested, max(1, math.ceil(requested))

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

    @classmethod
    def _required_discrete_units(
        cls,
        *,
        quantity: float,
        ingredient_unit: str,
        ingredient_name: str,
        item: dict[str, Any],
        product_name: str,
    ) -> float:
        if ingredient_unit in cls._PIECE_UNITS:
            # Для "лавровый лист 2 шт" нужен 1 пакет специй, а не 2 пакета.
            special_piece_grams = cls._required_grams(
                quantity=quantity,
                ingredient_unit=ingredient_unit,
                ingredient_name=ingredient_name,
            )
            if special_piece_grams > 0:
                pack_grams = cls._infer_pack_grams(item=item)
                if pack_grams > 0:
                    return special_piece_grams / pack_grams
                return 1.0
            pack_pieces = cls._infer_pack_pieces(item=item, product_name=product_name)
            return quantity / max(pack_pieces, 1)

        required_grams = cls._required_grams(
            quantity=quantity,
            ingredient_unit=ingredient_unit,
            ingredient_name=ingredient_name,
        )
        if required_grams > 0:
            pack_grams = cls._infer_pack_grams(item=item)
            if pack_grams > 0:
                return required_grams / pack_grams
            return 1.0

        required_ml = cls._required_milliliters(
            quantity=quantity,
            ingredient_unit=ingredient_unit,
            ingredient_name=ingredient_name,
        )
        if required_ml > 0:
            pack_ml = cls._infer_pack_milliliters(item=item)
            if pack_ml > 0:
                return required_ml / pack_ml
            return 1.0

        return max(1.0, quantity)

    @classmethod
    def _required_kilograms(
        cls,
        *,
        quantity: float,
        ingredient_unit: str,
        ingredient_name: str,
    ) -> float:
        if ingredient_unit in cls._KILOGRAM_UNITS:
            return quantity
        if ingredient_unit in cls._GRAM_UNITS:
            return quantity / 1000
        if ingredient_unit in cls._PIECE_UNITS:
            return quantity * cls._avg_piece_kg(ingredient_name)
        grams = cls._required_grams(
            quantity=quantity,
            ingredient_unit=ingredient_unit,
            ingredient_name=ingredient_name,
        )
        if grams > 0:
            return grams / 1000
        ml = cls._required_milliliters(
            quantity=quantity,
            ingredient_unit=ingredient_unit,
            ingredient_name=ingredient_name,
        )
        if ml > 0:
            return ml / 1000
        return 0.0

    @classmethod
    def _required_liters(
        cls,
        *,
        quantity: float,
        ingredient_unit: str,
        ingredient_name: str,
    ) -> float:
        if ingredient_unit in cls._LITER_UNITS:
            return quantity
        ml = cls._required_milliliters(
            quantity=quantity,
            ingredient_unit=ingredient_unit,
            ingredient_name=ingredient_name,
        )
        if ml > 0:
            return ml / 1000
        return 0.0

    @classmethod
    def _required_grams(
        cls,
        *,
        quantity: float,
        ingredient_unit: str,
        ingredient_name: str,
    ) -> float:
        if ingredient_unit in cls._GRAM_UNITS:
            return quantity
        if ingredient_unit in cls._KILOGRAM_UNITS:
            return quantity * 1000
        if cls._is_garlic_ingredient(ingredient_name):
            if ingredient_unit in cls._GARLIC_CLOVE_UNITS:
                return quantity * 5.0
            if ingredient_unit in cls._GARLIC_HEAD_UNITS:
                return quantity * 30.0
        if cls._is_bay_leaf_ingredient(ingredient_name) and (
            ingredient_unit in cls._PIECE_UNITS or ingredient_unit in cls._LEAF_UNITS
        ):
            return quantity * 0.2
        if ingredient_unit in cls._TABLESPOON_UNITS:
            grams_per_tbsp = 10.0
            if cls._is_massy_ingredient(ingredient_name):
                if "мук" in ingredient_name:
                    grams_per_tbsp = 8.0
                elif "сахар" in ingredient_name:
                    grams_per_tbsp = 12.0
                elif "соль" in ingredient_name:
                    grams_per_tbsp = 15.0
            return quantity * grams_per_tbsp
        if ingredient_unit in cls._TEASPOON_UNITS:
            grams_per_tsp = 3.0
            if cls._is_massy_ingredient(ingredient_name):
                if "мук" in ingredient_name:
                    grams_per_tsp = 2.5
                elif "сахар" in ingredient_name:
                    grams_per_tsp = 4.0
                elif "соль" in ingredient_name:
                    grams_per_tsp = 5.0
            return quantity * grams_per_tsp
        return 0.0

    @classmethod
    def _required_milliliters(
        cls,
        *,
        quantity: float,
        ingredient_unit: str,
        ingredient_name: str,
    ) -> float:
        if ingredient_unit in cls._MILLILITER_UNITS:
            return quantity
        if ingredient_unit in cls._LITER_UNITS:
            return quantity * 1000
        if ingredient_unit in cls._TABLESPOON_UNITS:
            return quantity * 15.0
        if ingredient_unit in cls._TEASPOON_UNITS:
            return quantity * 5.0
        # Для жидкостей без unit (редко) лучше fallback в 0.
        if cls._is_liquid_ingredient(ingredient_name):
            return quantity
        return 0.0

    @classmethod
    def _infer_pack_pieces(cls, *, item: dict[str, Any], product_name: str) -> int:
        if any(stem in product_name for stem in ("яйц", "яиц", "яйк")):
            parsed = cls._extract_pack_number(product_name)
            return parsed if parsed > 0 else _EGG_PACK_SIZE
        parsed = cls._extract_pack_number(cls._searchable_product_text(item))
        return max(1, parsed)

    @classmethod
    def _infer_pack_grams(cls, *, item: dict[str, Any]) -> float:
        weight = item.get("weight")
        if isinstance(weight, dict):
            value = cls._safe_float(weight.get("value"), default=-1.0)
            unit = cls._normalize_unit(weight.get("unit"))
            if value > 0:
                if unit in cls._KILOGRAM_UNITS:
                    return value * 1000
                if unit in cls._GRAM_UNITS:
                    return value
        text = cls._searchable_product_text(item)
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*(кг|kg|г|гр)\b", text, flags=re.IGNORECASE)
        if match is None:
            return 0.0
        value = cls._safe_float(match.group(1), default=-1.0)
        if value <= 0:
            return 0.0
        unit = cls._normalize_unit(match.group(2))
        if unit in cls._KILOGRAM_UNITS:
            return value * 1000
        return value

    @classmethod
    def _infer_pack_milliliters(cls, *, item: dict[str, Any]) -> float:
        weight = item.get("weight")
        if isinstance(weight, dict):
            value = cls._safe_float(weight.get("value"), default=-1.0)
            unit = cls._normalize_unit(weight.get("unit"))
            if value > 0:
                if unit in cls._LITER_UNITS:
                    return value * 1000
                if unit in cls._MILLILITER_UNITS:
                    return value
        text = cls._searchable_product_text(item)
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*(л|l|мл|ml)\b", text, flags=re.IGNORECASE)
        if match is None:
            return 0.0
        value = cls._safe_float(match.group(1), default=-1.0)
        if value <= 0:
            return 0.0
        unit = cls._normalize_unit(match.group(2))
        if unit in cls._LITER_UNITS:
            return value * 1000
        return value

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
            "столовая ложка": "ст.л.",
            "столовые ложки": "ст.л.",
            "ч.л": "ч.л.",
            "ч.л.": "ч.л.",
            "ч л": "ч.л.",
            "ч. л": "ч.л.",
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
    def _searchable_product_text(cls, item: dict[str, Any]) -> str:
        name = cls._normalize_text(item.get("name"))
        description = cls._normalize_text(item.get("description"))
        return f"{name} {description}".strip()
