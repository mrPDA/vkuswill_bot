"""Тесты для vkuswill_bot.services.preferences_parser."""

from __future__ import annotations

import json

from vkuswill_bot.services.preferences_parser import parse_preferences


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
