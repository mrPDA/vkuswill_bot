"""Unit tests for meal-plan runtime data operations."""

from __future__ import annotations

from vkuswill_bot.agents.meal_plan_runtime_ops import aggregate_ingredients


def test_aggregate_ingredients_deduplicates_by_search_query_and_unit() -> None:
    aggregated = aggregate_ingredients(
        [
            {"name": "Молоко", "search_query": "Молоко", "quantity": 200, "unit": "мл"},
            {"name": "молоко пастеризованное", "search_query": "молоко", "quantity": 400, "unit": "мл"},
            {"name": "Рис", "search_query": "рис", "quantity": 1, "unit": "кг"},
        ]
    )

    by_key = {(row["search_query"], row["unit"]): row for row in aggregated}
    assert ("молоко", "мл") in by_key
    assert by_key[("молоко", "мл")]["quantity"] == 600
    assert ("рис", "кг") in by_key


def test_aggregate_ingredients_keeps_rows_separate_for_different_units() -> None:
    aggregated = aggregate_ingredients(
        [
            {"name": "Молоко", "search_query": "молоко", "quantity": 500, "unit": "мл"},
            {"name": "Молоко сухое", "search_query": "молоко", "quantity": 200, "unit": "г"},
        ]
    )

    keys = {(row["search_query"], row["unit"]) for row in aggregated}
    assert ("молоко", "мл") in keys
    assert ("молоко", "г") in keys
    assert len(aggregated) == 2
