"""Shared structured payloads for meal-plan request parsing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MealPlanRequestExtraction:
    days: int | None = None
    people_total: int | None = None
    requested_meal_types: list[str] | None = None
    child_count: int | None = None
    child_age_years: int | None = None
    diet: str | None = None
    cuisines: list[str] | None = None
    allergens_excluded: list[str] | None = None
