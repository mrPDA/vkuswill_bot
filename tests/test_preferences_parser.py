"""Тесты для vkuswill_bot.services.preferences_parser."""

from __future__ import annotations

import json

from vkuswill_bot.services.preferences_parser import parse_preference_profile, parse_preferences


def test_parse_preferences_returns_empty_for_invalid_json() -> None:
    assert parse_preferences("not-json") == {}


def test_parse_preferences_returns_empty_for_non_list_preferences() -> None:
    payload = json.dumps({"preferences": {"category": "milk", "preference": "козье"}})
    assert parse_preferences(payload) == {}


def test_parse_preferences_extracts_normalized_mapping() -> None:
    payload = json.dumps(
        {
            "preferences": [
                {"category": "  Milk ", "preference": "козье"},
                {"category": "bread", "preference": "бездрожжевой"},
                {"category": "", "preference": "x"},
                {"category": "eggs", "preference": ""},
                "broken",
            ]
        },
        ensure_ascii=False,
    )
    assert parse_preferences(payload) == {
        "milk": "козье",
        "bread": "бездрожжевой",
    }


def test_parse_preference_profile_prefers_profile_field() -> None:
    payload = json.dumps(
        {
            "ok": True,
            "preferences": [{"category": "milk", "preference": "козье"}],
            "profile": {
                "schema_version": 1,
                "hard_constraints": {"diet": "веганское"},
                "soft_preferences": {"cuisines": ["italian"]},
                "operational_preferences": {},
            },
        },
        ensure_ascii=False,
    )
    parsed = parse_preference_profile(payload)
    assert parsed["hard_constraints"]["diet"] == "vegan"
    assert parsed["soft_preferences"]["cuisines"] == ["italian"]


def test_parse_preference_profile_fallback_maps_known_legacy_fields() -> None:
    payload = json.dumps(
        {
            "ok": True,
            "preferences": [
                {"category": "diet", "preference": "вегетарианское"},
                {"category": "allergens_excluded", "preference": "nuts, lactose"},
                {"category": "cuisines", "preference": "italian, georgian"},
                {"category": "liked_ingredients", "preference": "tomato; basil"},
                {"category": "max_dishes", "preference": "8"},
                {"category": "молоко", "preference": "безлактозное"},
            ],
        },
        ensure_ascii=False,
    )
    parsed = parse_preference_profile(payload)
    assert parsed["hard_constraints"]["diet"] == "vegetarian"
    assert parsed["hard_constraints"]["allergens_excluded"] == ["nuts", "lactose"]
    assert parsed["soft_preferences"]["cuisines"] == ["italian", "georgian"]
    assert parsed["soft_preferences"]["liked_ingredients"] == ["tomato", "basil"]
    assert parsed["operational_preferences"]["max_dishes"] == 8
    assert parsed["soft_preferences"]["freeform_preferences"]["молоко"] == "безлактозное"


def test_parse_preference_profile_scoped_diet_does_not_become_global_hard_constraint() -> None:
    payload = json.dumps(
        {
            "ok": True,
            "preferences": [
                {"category": "diet", "preference": "vegetarian for one person in family"},
            ],
        },
        ensure_ascii=False,
    )
    parsed = parse_preference_profile(payload)
    assert "diet" not in parsed["hard_constraints"]
    assert (
        parsed["soft_preferences"]["freeform_preferences"]["diet_scope_note"]
        == "vegetarian for one person in family"
    )


def test_parse_preference_profile_repairs_polluted_profile_using_scoped_legacy_note() -> None:
    payload = json.dumps(
        {
            "ok": True,
            "preferences": [
                {"category": "diet", "preference": "vegetarian for one person in family"},
            ],
            "profile": {
                "schema_version": 1,
                "hard_constraints": {"diet": "vegetarian"},
                "soft_preferences": {"cuisines": ["italian"]},
                "operational_preferences": {},
            },
        },
        ensure_ascii=False,
    )
    parsed = parse_preference_profile(payload)
    assert "diet" not in parsed["hard_constraints"]
    assert parsed["soft_preferences"]["cuisines"] == ["italian"]
    assert (
        parsed["soft_preferences"]["freeform_preferences"]["diet_scope_note"]
        == "vegetarian for one person in family"
    )
