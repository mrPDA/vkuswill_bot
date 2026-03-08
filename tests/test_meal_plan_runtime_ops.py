"""Unit tests for meal-plan runtime data operations."""

from __future__ import annotations

import json

from vkuswill_bot.agents.meal_plan_runtime_ops import (
    aggregate_ingredients,
    extract_products_from_recipe_search,
    filter_pantry_ingredients_for_search,
    prioritize_ingredients_for_search,
)


def test_aggregate_ingredients_deduplicates_by_search_query_and_unit() -> None:
    aggregated = aggregate_ingredients(
        [
            {"name": "Молоко", "search_query": "Молоко", "quantity": 200, "unit": "мл"},
            {
                "name": "молоко пастеризованное",
                "search_query": "молоко",
                "quantity": 400,
                "unit": "мл",
            },
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


def test_filter_pantry_ingredients_for_search_removes_non_explicit_pantry() -> None:
    filtered, removed = filter_pantry_ingredients_for_search(
        items=[
            {"name": "Вода", "search_query": "вода", "quantity": 1, "unit": "л"},
            {"name": "Соль", "search_query": "соль", "quantity": 1, "unit": "щепотка"},
            {"name": "Помидор", "search_query": "помидор", "quantity": 2, "unit": "шт"},
        ],
        explicit_pantry_requests=set(),
    )

    assert filtered == [{"name": "Помидор", "search_query": "помидор", "quantity": 2, "unit": "шт"}]
    assert removed == ["Вода", "Соль"]


def test_filter_pantry_ingredients_for_search_keeps_explicit_pantry() -> None:
    filtered, removed = filter_pantry_ingredients_for_search(
        items=[
            {"name": "Вода", "search_query": "вода", "quantity": 1, "unit": "л"},
            {"name": "Соль", "search_query": "соль", "quantity": 1, "unit": "щепотка"},
        ],
        explicit_pantry_requests={"water", "salt"},
    )

    assert filtered == [
        {"name": "Вода", "search_query": "вода", "quantity": 1, "unit": "л"},
        {"name": "Соль", "search_query": "соль", "quantity": 1, "unit": "щепотка"},
    ]
    assert removed == []


def test_prioritize_ingredients_for_search_limits_low_priority_tail() -> None:
    selected, deferred = prioritize_ingredients_for_search(
        items=[
            {"name": "Картофель", "search_query": "картофель", "quantity": 800, "unit": "г"},
            {"name": "Морковь", "search_query": "морковь", "quantity": 300, "unit": "г"},
            {
                "name": "Оливковое масло",
                "search_query": "оливковое масло",
                "quantity": 2,
                "unit": "ст.л.",
            },
            {"name": "Розмарин", "search_query": "розмарин сушеный", "quantity": 1, "unit": "ч.л."},
        ],
        max_items=2,
    )

    assert [row["search_query"] for row in selected] == ["картофель", "морковь"]
    assert deferred == ["Оливковое масло", "Розмарин"]


def test_prioritize_ingredients_for_search_keeps_all_when_under_limit() -> None:
    items = [
        {"name": "Тофу", "search_query": "тофу", "quantity": 300, "unit": "г"},
        {"name": "Брокколи", "search_query": "брокколи", "quantity": 400, "unit": "г"},
    ]

    selected, deferred = prioritize_ingredients_for_search(items=items, max_items=5)

    assert selected == items
    assert deferred == []


def test_extract_products_from_recipe_search_supports_results_best_match_shape() -> None:
    payload = {
        "ok": True,
        "results": [
            {
                "ingredient": "тофу",
                "best_match": {
                    "xml_id": 301,
                    "suggested_q": 2,
                    "name": "Тофу классический",
                    "category": "растительный белок",
                },
            },
            {
                "ingredient": "брокколи",
                "best_match": {
                    "xml_id": 302,
                    "suggested_q": 1,
                    "name": "Брокколи",
                    "category": "овощи",
                },
            },
        ],
        "not_found": ["кинза"],
    }

    products, not_found = extract_products_from_recipe_search(
        json.dumps(payload, ensure_ascii=False)
    )

    assert products == [
        {
            "xml_id": 301,
            "q": 2.0,
            "name": "Тофу классический",
            "category": "растительный белок",
        },
        {
            "xml_id": 302,
            "q": 1.0,
            "name": "Брокколи",
            "category": "овощи",
        },
    ]
    assert not_found == ["кинза"]


def test_extract_products_from_recipe_search_supports_data_found_shape() -> None:
    payload = {
        "ok": True,
        "data": {
            "found": [
                {"xml_id": 411, "suggested_q": 1, "name": "Рис", "category": "крупы"},
            ],
            "not_found": ["базилик"],
        },
    }

    products, not_found = extract_products_from_recipe_search(
        json.dumps(payload, ensure_ascii=False)
    )

    assert products == [{"xml_id": 411, "q": 1.0, "name": "Рис", "category": "крупы"}]
    assert not_found == ["базилик"]


def test_extract_products_from_recipe_search_supports_data_found_item_shape() -> None:
    payload = {
        "ok": True,
        "data": {
            "found": [
                {
                    "ingredient": "рис",
                    "suggested_q": 2,
                    "item": {
                        "xml_id": 512,
                        "name": "Рис длиннозерный",
                        "category": "крупы",
                    },
                }
            ],
            "not_found": [],
        },
    }

    products, not_found = extract_products_from_recipe_search(
        json.dumps(payload, ensure_ascii=False)
    )

    assert products == [{"xml_id": 512, "q": 2.0, "name": "Рис длиннозерный", "category": "крупы"}]
    assert not_found == []
