"""Deterministic purchase quantity calculation from recipe dosing."""

from __future__ import annotations

import math
from typing import Any

from vkuswill_bot.agents.recipe_quantity_rules import RecipeQuantityRules


class RecipeQuantityCalculator(RecipeQuantityRules):
    """Детерминированный расчет purchase-количества по рецептурной дозировке."""

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

        requested = quantity if quantity > 0 else 1.0
        return requested, max(1, math.ceil(requested))
