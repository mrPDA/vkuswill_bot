"""Shared quantity normalization primitives for cart-related logic."""

from __future__ import annotations

import math

DISCRETE_UNITS = frozenset({"шт", "уп", "пач", "бут", "бан", "пак"})


def round_kilogram_quantity(quantity: float) -> float:
    """Round kilograms up to 0.1 with a minimum of 0.1."""
    safe_quantity = quantity if quantity > 0 else 0.1
    rounded = math.ceil(safe_quantity * 10) / 10
    return round(max(0.1, rounded), 1)
