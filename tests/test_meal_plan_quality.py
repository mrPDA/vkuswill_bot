"""Unit tests for meal-plan quality metrics."""

from __future__ import annotations

from vkuswill_bot.agents.meal_plan_quality import (
    build_applied_preferences_trace,
    calculate_soft_coverage,
    format_soft_coverage_error,
    low_soft_coverage_groups,
    validate_hard_constraints,
    validate_hard_constraints_with_ingredients,
)
from vkuswill_bot.agents.meal_plan_types import MealPlanDish, parse_meal_plan_request


def test_calculate_soft_coverage_for_cuisine_preferences() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек с итальянской кухней", {})
    dishes = [
        MealPlanDish(
            name=f"Блюдо {idx}",
            day=idx,
            meal_type="lunch",
            servings_total=2,
            audience_groups=["adults"],
            cuisine_tags=["italian" if idx <= 5 else "russian"],
        )
        for idx in range(1, 8)
    ]

    coverage = calculate_soft_coverage(request=request, dishes=dishes)

    assert "adults" in coverage
    assert coverage["adults"] == 5 / 7


def test_low_soft_coverage_groups_and_format() -> None:
    low = low_soft_coverage_groups(coverage_by_group={"adults": 0.55, "child_2y": 0.9})

    assert low == {"adults": 0.55}
    text = format_soft_coverage_error(low_groups=low)
    assert "soft_preferences coverage < 0.70" in text
    assert "adults=0.55" in text


def test_validate_hard_constraints_detects_vegan_violation() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек, один веган", {})
    dishes = [
        MealPlanDish(
            name=f"Курица {idx}",
            day=idx,
            meal_type="lunch",
            servings_total=2,
            audience_groups=["adults"],
            cuisine_tags=[],
        )
        for idx in range(1, 8)
    ]
    violations = validate_hard_constraints(request=request, dishes=dishes)
    assert violations
    assert "diet=vegan" in violations[0]


def test_validate_hard_constraints_detects_violation_for_russian_diet_alias_from_stored() -> None:
    request = parse_meal_plan_request(
        "меню на неделю для 2 человек",
        {"hard_constraints": {"diet": "веганское"}},
    )
    dishes = [
        MealPlanDish(
            name="Курица с рисом",
            day=1,
            meal_type="lunch",
            servings_total=2,
            audience_groups=["adults"],
            cuisine_tags=[],
        )
    ]

    violations = validate_hard_constraints(request=request, dishes=dishes)
    assert violations
    assert "diet=vegan" in violations[0]


def test_validate_hard_constraints_with_ingredients_detects_hidden_violation() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек, один веган", {})
    dishes = [
        MealPlanDish(
            name="Овощное рагу",
            day=1,
            meal_type="lunch",
            servings_total=2,
            audience_groups=["adults"],
            cuisine_tags=["russian"],
        )
    ]
    violations, trace = validate_hard_constraints_with_ingredients(
        request=request,
        dishes=dishes,
        ingredients_by_dish={
            "овощное рагу": [
                {"name": "куриное филе", "search_query": "куриное филе"},
                {"name": "картофель", "search_query": "картофель"},
            ]
        },
    )

    assert violations
    assert "ingredients" in violations[0]
    assert any(
        row.get("field") == "hard_constraints.diet" and row.get("applied") is False for row in trace
    )


def test_build_applied_preferences_trace_includes_sources_and_coverage() -> None:
    request = parse_meal_plan_request(
        "меню на неделю для 2 человек с итальянской кухней",
        {
            "soft_preferences": {"cuisines": ["italian"]},
            "hard_constraints": {"diet": "vegetarian"},
        },
    )
    phase1_trace = [
        {
            "type": "applied_preference",
            "stage": "phase1_generation",
            "scope": "dish_group",
            "group_id": "adults",
            "field": "hard_constraints.diet",
            "value": "vegetarian",
            "applied": True,
            "dish": "Паста",
        }
    ]

    merged = build_applied_preferences_trace(
        request=request,
        phase1_applied_trace=phase1_trace,
        soft_coverage_by_group={"adults": 0.75},
    )

    assert any(row.get("sources") for row in merged if row.get("field") == "hard_constraints.diet")
    assert any(
        row.get("field") == "soft_preferences.cuisines" and row.get("applied") is True
        for row in merged
    )
