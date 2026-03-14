"""Tests for meal-plan deterministic response contract rendering."""

from __future__ import annotations

from vkuswill_bot.agents.meal_plan_response_contract_builder import (
    build_meal_plan_response_contract_v1,
)
from vkuswill_bot.agents.meal_plan_response_contract import render_meal_plan_contract_response


def test_render_shows_audience_tags_only_for_multi_group() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data={"price_summary": {"count": 1, "total_text": "Итого: 100 руб"}, "not_found": []},
        user_preference_profile={},
        request_payload={
            "days": 1,
            "people_total": 2,
            "groups": [
                {
                    "id": "adults",
                    "count": 1,
                    "hard_constraints": {"diet": "vegan"},
                    "soft_preferences": {"cuisines": ["italian"]},
                },
                {
                    "id": "child_2y",
                    "count": 1,
                    "hard_constraints": {"allergens_excluded": ["nuts"]},
                    "soft_preferences": {},
                    "age_years": 2,
                },
            ],
            "preference_sources": {"explicit": 2, "stored": 1},
            "applied_preferences_summary": {"total": 4, "applied": 4, "not_applied": 0},
        },
        structured_dishes=[
            {"name": "Каша", "day": 1, "meal_type": "breakfast", "audience_groups": ["all"]},
            {"name": "Пюре", "day": 1, "meal_type": "snack_3", "audience_groups": ["child_2y"]},
        ],
    )
    assert "Поздний перекус" in text
    assert "[child_2y]" in text
    assert "adults.diet=vegan" in text


def test_render_hides_audience_tags_for_single_group() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data={"price_summary": {"count": 1, "total_text": "Итого: 100 руб"}, "not_found": []},
        user_preference_profile={},
        request_payload={
            "days": 1,
            "people_total": 2,
            "groups": [{"id": "adults", "count": 2}],
        },
        structured_dishes=[
            {"name": "Каша", "day": 1, "meal_type": "breakfast", "audience_groups": ["adults"]},
        ],
    )
    assert "Каша" in text
    assert "[adults]" not in text


def test_render_lists_products_when_cart_link_missing() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data={
            "products": [
                {"xml_id": 1, "q": 2, "name": "Тофу", "category": "растительные"},
                {"xml_id": 2, "q": 1, "name": "Помидоры", "category": "овощи"},
            ],
            "not_found": ["шафран"],
        },
        user_preference_profile={},
        request_payload={"days": 1, "people_total": 1, "groups": [{"id": "all", "count": 1}]},
        structured_dishes=[
            {"name": "Суп", "day": 1, "meal_type": "lunch", "audience_groups": ["all"]}
        ],
    )
    assert "не сформирована" in text
    assert "Список товаров:" in text
    assert "Тофу x 2" in text


def test_render_fail_soft_shows_clear_error() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data=None,
        user_preference_profile={},
        fallback_message="Не удалось получить ингредиенты для плана.",
        request_payload={"days": 1, "people_total": 1, "groups": [{"id": "all", "count": 1}]},
        structured_dishes=[],
    )
    assert "План не сформирован" in text
    assert "Не удалось получить ингредиенты для плана." in text


def test_render_section_order() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю для 2 человек"}],
        cart_data={"price_summary": {"count": 2, "total_text": "Итого: 200 руб"}, "not_found": []},
        user_preference_profile={},
        request_payload={
            "days": 2,
            "people_total": 2,
            "groups": [{"id": "adults", "count": 2}],
        },
        structured_dishes=[
            {"name": "Каша", "day": 1, "meal_type": "breakfast", "audience_groups": ["adults"]},
            {"name": "Суп", "day": 2, "meal_type": "lunch", "audience_groups": ["adults"]},
        ],
    )
    order = [
        "План питания",
        "День 1",
        "День 2",
        "Корзина",
    ]
    positions = [text.index(section) for section in order]
    assert positions == sorted(positions)


def test_render_sorts_days_and_slots() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data={"price_summary": {"count": 1, "total_text": "Итого: 100 руб"}, "not_found": []},
        user_preference_profile={},
        request_payload={
            "days": 2,
            "people_total": 2,
            "groups": [
                {"id": "adults", "count": 1},
                {"id": "child_2y", "count": 1, "age_years": 2},
            ],
        },
        structured_dishes=[
            {
                "name": "Перекус поздний",
                "day": 2,
                "meal_type": "snack_3",
                "audience_groups": ["child_2y"],
            },
            {"name": "Обед", "day": 1, "meal_type": "lunch", "audience_groups": ["all"]},
            {"name": "Завтрак", "day": 1, "meal_type": "breakfast", "audience_groups": ["all"]},
        ],
    )
    day1_pos = text.index("День 1")
    day2_pos = text.index("День 2")
    assert day1_pos < day2_pos


def test_build_contract_v1_uses_adr_structured_fields() -> None:
    contract = build_meal_plan_response_contract_v1(
        history=[{"role": "user", "content": "меню на неделю для 2 человек"}],
        request_payload={
            "days": 7,
            "people_total": 2,
            "groups": [
                {"id": "adults", "count": 1, "hard_constraints": {"diet": "vegan"}},
                {
                    "id": "child_2y",
                    "count": 1,
                    "hard_constraints": {"allergens_excluded": ["nuts"]},
                },
            ],
            "applied_preferences_trace": [
                {"field": "hard_constraints.diet", "applied": True},
                {"field": "hard_constraints.allergens_excluded", "applied": True},
            ],
        },
        structured_dishes=[
            {"name": "Овощной суп", "day": 1, "meal_type": "lunch", "audience_groups": ["adults"]},
        ],
        cart_data={"price_summary": {"count": 1, "total": 499.0}, "not_found": ["киноа"]},
        user_preference_profile={},
        soft_coverage_by_group={"adults": 0.8},
        fallback_message="",
    )

    assert contract.schema_version == 1
    assert contract.request_summary.days == 7
    assert contract.request_summary.people_total == 2
    assert [(group.id, group.count) for group in contract.request_summary.groups] == [
        ("adults", 1),
        ("child_2y", 1),
    ]
    assert contract.weekly_plan[0].slots[1].dishes[0].audience_groups == ["adults"]
    assert contract.group_adaptations[0].rules_applied is not None
    assert contract.constraints_check.hard_constraints_passed is True
    assert contract.constraints_check.soft_coverage_by_group["adults"] == 0.8


def test_build_contract_v1_marks_hard_constraints_violation_from_trace() -> None:
    contract = build_meal_plan_response_contract_v1(
        history=[{"role": "user", "content": "меню на неделю для 2 человек"}],
        request_payload={
            "days": 1,
            "people_total": 2,
            "groups": [{"id": "adults", "count": 2, "hard_constraints": {"diet": "vegan"}}],
            "applied_preferences_trace": [{"field": "hard_constraints.diet", "applied": False}],
        },
        structured_dishes=[],
        cart_data=None,
        user_preference_profile={},
        soft_coverage_by_group=None,
        fallback_message="",
    )
    assert contract.constraints_check.hard_constraints_passed is False


def test_render_skips_empty_days() -> None:
    """Days with no assigned dishes should not appear in the output."""
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data={"price_summary": {"count": 1, "total_text": "Итого: 100 руб"}, "not_found": []},
        user_preference_profile={},
        request_payload={
            "days": 7,
            "people_total": 1,
            "groups": [{"id": "adults", "count": 1}],
        },
        structured_dishes=[
            {"name": "Каша", "day": 1, "meal_type": "breakfast", "audience_groups": ["adults"]},
            {"name": "Суп", "day": 2, "meal_type": "lunch", "audience_groups": ["adults"]},
        ],
    )
    assert "День 1" in text
    assert "День 2" in text
    assert "День 3" not in text
    assert "День 7" not in text


def test_render_shows_not_found_items() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data={
            "price_summary": {"count": 5, "total_text": "Итого: 500 руб"},
            "link": "https://vkusvill.ru/?share_basket=123",
            "not_found": ["чеснок", "имбирь"],
        },
        user_preference_profile={},
        request_payload={"days": 1, "people_total": 1, "groups": [{"id": "all", "count": 1}]},
        structured_dishes=[
            {"name": "Суп", "day": 1, "meal_type": "lunch", "audience_groups": ["all"]},
        ],
    )
    assert "Не нашли во ВкусВилл: чеснок, имбирь" in text
    assert "https://vkusvill.ru/?share_basket=123" in text


def test_render_multi_cart_groups() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data={
            "price_summary": {"count": 40},
            "link": "https://vkusvill.ru/?share_basket=111",
            "not_found": [],
            "products": [],
            "groups": [
                {
                    "day_label": "Дни 1-3",
                    "link": "https://vkusvill.ru/?share_basket=111",
                    "products": [{"xml_id": 1, "name": "A", "q": 1, "category": "x"}] * 20,
                    "cart_created": True,
                },
                {
                    "day_label": "Дни 4-7",
                    "link": "https://vkusvill.ru/?share_basket=222",
                    "products": [{"xml_id": 2, "name": "B", "q": 1, "category": "y"}] * 20,
                    "cart_created": True,
                },
            ],
        },
        user_preference_profile={},
        request_payload={"days": 7, "people_total": 1, "groups": [{"id": "all", "count": 1}]},
        structured_dishes=[
            {"name": "Суп", "day": 1, "meal_type": "lunch", "audience_groups": ["all"]},
        ],
    )
    assert "Корзины ВкусВилл" in text
    assert "Дни 1-3" in text
    assert "Дни 4-7" in text
    assert "share_basket=111" in text
    assert "share_basket=222" in text


def test_build_contract_v1_child_default_has_five_slots_per_day() -> None:
    contract = build_meal_plan_response_contract_v1(
        history=[{"role": "user", "content": "меню на неделю для 2 человек, ребенок 2 года"}],
        request_payload={
            "days": 3,
            "people_total": 2,
            "groups": [
                {"id": "adults", "count": 1},
                {"id": "child_2y", "count": 1, "age_years": 2},
            ],
        },
        structured_dishes=[
            {"name": "Каша", "day": 1, "meal_type": "breakfast", "audience_groups": ["all"]},
            {"name": "Суп", "day": 1, "meal_type": "lunch", "audience_groups": ["all"]},
        ],
        cart_data=None,
        user_preference_profile={},
        soft_coverage_by_group=None,
        fallback_message="",
    )

    expected = ["breakfast", "snack_1", "lunch", "snack_2", "dinner"]
    assert len(contract.weekly_plan) == 3
    for day in contract.weekly_plan:
        assert [slot.meal_type for slot in day.slots] == expected


def test_render_uses_html_formatting() -> None:
    text = render_meal_plan_contract_response(
        history=[{"role": "user", "content": "меню на неделю"}],
        cart_data={"price_summary": {"count": 1, "total_text": "Итого: 100 руб"}, "not_found": []},
        user_preference_profile={},
        request_payload={
            "days": 1,
            "people_total": 2,
            "groups": [{"id": "adults", "count": 2}],
        },
        structured_dishes=[
            {"name": "Каша", "day": 1, "meal_type": "breakfast", "audience_groups": ["adults"]},
        ],
    )
    assert "<b>" in text
    assert "План питания" in text
