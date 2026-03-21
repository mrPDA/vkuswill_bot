"""Tests for meal-plan response helpers."""

from __future__ import annotations

from vkuswill_bot.agents.meal_plan_response_utils import extract_days


def test_extract_days_supports_word_based_day_count() -> None:
    assert extract_days("собери мне обеды для здорового питания на два дня") == 2
