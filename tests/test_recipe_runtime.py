"""Unit tests for recipe runtime/parsing/matching helpers."""

from __future__ import annotations

import json

import pytest

from vkuswill_bot.agents.recipe_matching import apply_requested_quantity_overrides
from vkuswill_bot.agents.recipe_parsing import normalize_recipe_ingredient_row
from vkuswill_bot.agents.recipe_runtime import (
    enrich_recipe_equivalents,
    fallback_borscht_ingredients,
    is_recipe_followup,
    sanitize_recipe_ingredients_tool_result,
)


def test_is_recipe_followup_true_for_recent_recipe_user_message() -> None:
    history = [
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "подскажи рецепт борща"},
    ]
    assert is_recipe_followup(text="и еще без лука", history=history) is True


def test_is_recipe_followup_false_for_linking_markers() -> None:
    history = [
        {"role": "user", "content": "подскажи рецепт борща"},
        {"role": "tool", "name": "recipe_ingredients", "content": '{"ok": true}'},
    ]
    assert is_recipe_followup(text="какой код привязки?", history=history) is False


def test_is_recipe_followup_true_for_assistant_recipe_tool_calls() -> None:
    history = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "recipe_search",
                        "arguments": '{"ingredients":[]}',
                    }
                }
            ],
        }
    ]
    assert is_recipe_followup(text="и для двоих", history=history) is True


def test_sanitize_recipe_ingredients_tool_result_filters_top_and_nested_lists() -> None:
    payload = {
        "ok": True,
        "ingredients": [
            {"name": "Соль", "search_query": "соль"},
            {"name": "Свекла", "search_query": "свекла"},
        ],
        "data": {
            "ingredients": [
                {"name": "перец черный молотый", "search_query": "перец черный молотый"},
                {"name": "морковь", "search_query": "морковь"},
            ]
        },
    }

    sanitized = sanitize_recipe_ingredients_tool_result(
        tool_result=json.dumps(payload, ensure_ascii=False),
        explicit_pantry_requests=set(),
    )
    decoded = json.loads(sanitized)
    top_names = [item["name"] for item in decoded["ingredients"]]
    nested_names = [item["name"] for item in decoded["data"]["ingredients"]]

    assert top_names == ["Свекла"]
    assert nested_names == ["морковь"]
    assert set(decoded["pantry_filtered"]) == {"Соль", "перец черный молотый"}


def test_sanitize_recipe_ingredients_tool_result_passthrough_for_non_json() -> None:
    raw = "not-a-json"
    assert (
        sanitize_recipe_ingredients_tool_result(
            tool_result=raw,
            explicit_pantry_requests=set(),
        )
        == raw
    )


def test_enrich_recipe_equivalents_for_eggs_calculates_pack_equivalent() -> None:
    ingredient = {"name": "Яйца", "quantity": 21, "unit": "шт"}
    enrich_recipe_equivalents(ingredient)
    assert ingredient["pack_equivalent"] == 3


def test_fallback_borscht_ingredients_scales_by_servings() -> None:
    rows = fallback_borscht_ingredients(servings=4)
    beet = next(row for row in rows if row["name"] == "свёкла")
    garlic = next(row for row in rows if row["name"] == "чеснок")

    assert beet["quantity"] == pytest.approx(1.34, abs=0.001)
    assert garlic["quantity"] == pytest.approx(20.0, abs=0.001)
    assert garlic["kg_equivalent"] == pytest.approx(0.02, abs=0.001)


def test_normalize_recipe_ingredient_row_parses_string_quantity() -> None:
    row = normalize_recipe_ingredient_row("Мука 2 ст. ложки")
    assert row["name"] == "Мука"
    assert row["quantity"] == pytest.approx(2.0, abs=0.001)
    assert row["unit"] == "ст.л."
    assert row["search_query"]


def test_normalize_recipe_ingredient_row_fixes_empty_quantity_for_dict() -> None:
    row = normalize_recipe_ingredient_row({"name": "Молоко", "quantity": 0, "unit": None})
    assert row["quantity"] == 1.0
    assert row["unit"] == "шт"
    assert row["search_query"]


def test_apply_requested_quantity_overrides_skips_bool_xml_id() -> None:
    snapshot = [{"xml_id": True, "q": 1}, {"xml_id": "15", "q": 1}]
    updated = apply_requested_quantity_overrides(snapshot, {15: 3.0})
    assert updated[0]["q"] == 1
    assert updated[1]["q"] == 3.0
