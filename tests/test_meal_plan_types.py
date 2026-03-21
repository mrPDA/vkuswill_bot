"""Tests for meal-plan request parsing domain types."""

from __future__ import annotations

from vkuswill_bot.agents.meal_plan_types import parse_meal_plan_request


def test_parse_meal_plan_request_with_child_and_allergy() -> None:
    profile = {
        "hard_constraints": {"diet": "vegetarian", "allergens_excluded": ["лосось"]},
        "soft_preferences": {"cuisines": ["italian"]},
        "operational_preferences": {},
    }
    request = parse_meal_plan_request(
        "Собери меню на 7 дней для 4 человек, один ребенок 2 года с аллергией на орехи",
        profile,
    )

    assert request.days == 7
    assert request.people_total == 4
    assert len(request.groups) == 2
    groups = {group.id: group for group in request.groups}
    assert groups["adults"].count == 3
    assert groups["child_2y"].count == 1
    assert "орехи" in groups["adults"].hard_constraints["allergens_excluded"]
    assert "лосось" in groups["child_2y"].hard_constraints["allergens_excluded"]
    assert "italian" in groups["child_2y"].soft_preferences["cuisines"]
    assert request.operational_preferences["meal_slots_child"] >= 5


def test_parse_meal_plan_request_fallbacks_to_default_group() -> None:
    request = parse_meal_plan_request("Рацион на неделю", {})

    assert request.days == 7
    assert request.people_total == 1
    assert len(request.groups) == 1
    assert request.groups[0].id == "adults"
    assert request.groups[0].count == 1


def test_parse_meal_plan_request_supports_segmented_adult_preferences() -> None:
    request = parse_meal_plan_request(
        "Меню на неделю для 2 человек: один веган, другой предпочитает итальянскую кухню",
        {},
    )
    groups = {group.id: group for group in request.groups}
    assert groups["vegan_user"].count == 1
    assert groups["vegan_user"].hard_constraints["diet"] == "vegan"
    assert groups["italian_user"].count == 1
    assert "italian" in groups["italian_user"].soft_preferences["cuisines"]


def test_parse_meal_plan_request_supports_single_segmented_adult_diet() -> None:
    request = parse_meal_plan_request(
        "Собери корзину на неделю для 4 человек. один из них вегетарианец",
        {},
    )
    groups = {group.id: group for group in request.groups}
    assert groups["vegetarian_user"].count == 1
    assert groups["vegetarian_user"].hard_constraints["diet"] == "vegetarian"
    assert groups["adults"].count == 3
    assert "diet" not in groups["adults"].hard_constraints


def test_parse_meal_plan_request_includes_preferences_trace_sources() -> None:
    profile = {
        "hard_constraints": {"diet": "vegetarian"},
        "soft_preferences": {"freeform_preferences": {"люблю": "тыква"}},
        "operational_preferences": {"max_dishes": 8},
    }
    request = parse_meal_plan_request(
        "Собери меню на неделю для 2 человек с аллергией на орехи",
        profile,
    )
    sources = {row.get("source") for row in request.preferences_trace}
    assert "stored" in sources
    assert "explicit" in sources
    assert "freeform" in sources


def test_parse_meal_plan_request_explicit_diet_overrides_stored_conflict() -> None:
    profile = {
        "hard_constraints": {"diet": "vegetarian"},
        "soft_preferences": {},
        "operational_preferences": {},
    }
    request = parse_meal_plan_request("Собери веганское меню на неделю для 2 человек", profile)

    assert request.groups
    assert all(group.hard_constraints.get("diet") == "vegan" for group in request.groups)
    assert any(
        row.get("source") == "stored" and row.get("field") == "hard_constraints.diet"
        for row in request.preferences_trace
    )
    assert any(
        row.get("source") == "explicit"
        and row.get("field") == "hard_constraints.diet"
        and row.get("value") == "vegan"
        for row in request.preferences_trace
    )
