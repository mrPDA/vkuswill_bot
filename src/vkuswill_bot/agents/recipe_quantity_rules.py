"""Calculation rules for recipe quantity conversion."""

from __future__ import annotations

import re
from typing import Any

from vkuswill_bot.agents.recipe_quantity_base import RecipeQuantityBase


class RecipeQuantityRules(RecipeQuantityBase):
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
        if cls._is_liquid_ingredient(ingredient_name):
            return quantity
        return 0.0

    @classmethod
    def _infer_pack_pieces(cls, *, item: dict[str, Any], product_name: str) -> int:
        if any(stem in product_name for stem in ("яйц", "яиц", "яйк")):
            parsed = cls._extract_pack_number(product_name)
            return parsed if parsed > 0 else cls._EGG_PACK_SIZE
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
