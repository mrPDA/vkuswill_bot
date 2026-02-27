"""Unit-тесты для vkuswill_bot.agents.recovery_policy."""

from __future__ import annotations

import json

import pytest

from vkuswill_bot.agents.recovery_policy import (
    should_continue_recipe_flow_recovery,
    should_force_batch_search_hint,
    should_force_cart_link_source_recovery,
    should_force_manual_recovery,
    should_force_recipe_to_cart_hint,
)


def test_should_continue_recipe_flow_recovery_positive() -> None:
    assert (
        should_continue_recipe_flow_recovery(
            cart_data_this_turn=None,
            cart_intent=True,
            tools_called_this_turn=True,
            recipe_flow_started_this_turn=True,
            final_text="Подобрала ингредиенты для рецепта, могу продолжить.",
            cart_flow_recovery_used=False,
            step=2,
            max_tool_calls=5,
        )
        is True
    )


def test_should_continue_recipe_flow_recovery_negative_when_hint_already_used() -> None:
    assert (
        should_continue_recipe_flow_recovery(
            cart_data_this_turn=None,
            cart_intent=True,
            tools_called_this_turn=True,
            recipe_flow_started_this_turn=True,
            final_text="Подобрала ингредиенты для рецепта, могу продолжить.",
            cart_flow_recovery_used=True,
            step=2,
            max_tool_calls=5,
        )
        is False
    )


def test_should_force_manual_recovery_detects_manual_reply() -> None:
    assert (
        should_force_manual_recovery(
            cart_data_this_turn=None,
            cart_intent=True,
            final_text="Перейдите на сайт и соберите корзину сами.",
            manual_recovery_used=False,
            step=1,
            max_tool_calls=5,
        )
        is True
    )


def test_should_force_cart_link_source_recovery_detects_fake_ready_reply() -> None:
    assert (
        should_force_cart_link_source_recovery(
            cart_data_this_turn=None,
            cart_intent=True,
            final_text="Корзина готова, Итого: 500 руб.",
            cart_creation_recovery_used=False,
            step=1,
            max_tool_calls=5,
        )
        is True
    )


def test_should_force_recipe_to_cart_hint_requires_recipe_candidates() -> None:
    history = [
        {"role": "assistant", "content": "ok"},
        {
            "role": "tool",
            "name": "recipe_search",
            "content": json.dumps(
                {"found": [{"xml_id": 123, "suggested_q": 1.0}], "not_found": []},
                ensure_ascii=False,
            ),
        },
    ]
    assert (
        should_force_recipe_to_cart_hint(
            cart_intent=True,
            recipe_flow_started_this_turn=True,
            cart_data_this_turn=None,
            recipe_to_cart_recovery_used=False,
            history=history,
            step=2,
            max_tool_calls=5,
        )
        is True
    )


def test_should_force_batch_search_hint_requires_streak_threshold() -> None:
    assert (
        should_force_batch_search_hint(
            cart_intent=True,
            cart_data_this_turn=None,
            single_search_steps_streak=3,
            search_batch_recovery_used=False,
            step=3,
            max_tool_calls=5,
        )
        is True
    )
    assert (
        should_force_batch_search_hint(
            cart_intent=True,
            cart_data_this_turn=None,
            single_search_steps_streak=2,
            search_batch_recovery_used=False,
            step=3,
            max_tool_calls=5,
        )
        is False
    )


@pytest.mark.parametrize(
    ("text", "used", "step", "max_tool_calls", "expected"),
    [
        ("Перейдите на сайт и соберите корзину сами.", False, 1, 5, True),
        ("Перейдите на сайт и соберите корзину сами.", True, 1, 5, False),
        ("Собрала корзину по вашему запросу.", False, 1, 5, False),
        ("Перейдите на сайт и соберите корзину сами.", False, 5, 5, False),
    ],
)
def test_should_force_manual_recovery_matrix(
    text: str,
    used: bool,
    step: int,
    max_tool_calls: int,
    expected: bool,
) -> None:
    assert (
        should_force_manual_recovery(
            cart_data_this_turn=None,
            cart_intent=True,
            final_text=text,
            manual_recovery_used=used,
            step=step,
            max_tool_calls=max_tool_calls,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("streak", "used", "step", "max_tool_calls", "expected"),
    [
        (3, False, 3, 5, True),
        (4, False, 2, 5, True),
        (2, False, 3, 5, False),
        (3, True, 3, 5, False),
        (3, False, 5, 5, False),
    ],
)
def test_should_force_batch_search_hint_matrix(
    streak: int,
    used: bool,
    step: int,
    max_tool_calls: int,
    expected: bool,
) -> None:
    assert (
        should_force_batch_search_hint(
            cart_intent=True,
            cart_data_this_turn=None,
            single_search_steps_streak=streak,
            search_batch_recovery_used=used,
            step=step,
            max_tool_calls=max_tool_calls,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("tools_called", "recipe_started", "used", "text", "expected"),
    [
        (True, True, False, "Подобрала ингредиенты для рецепта, могу продолжить.", True),
        (False, True, False, "Подобрала ингредиенты для рецепта, могу продолжить.", False),
        (True, False, False, "Подобрала ингредиенты для рецепта, могу продолжить.", False),
        (True, True, True, "Подобрала ингредиенты для рецепта, могу продолжить.", False),
        (True, True, False, "Корзина готова.", False),
    ],
)
def test_should_continue_recipe_flow_recovery_matrix(
    tools_called: bool,
    recipe_started: bool,
    used: bool,
    text: str,
    expected: bool,
) -> None:
    assert (
        should_continue_recipe_flow_recovery(
            cart_data_this_turn=None,
            cart_intent=True,
            tools_called_this_turn=tools_called,
            recipe_flow_started_this_turn=recipe_started,
            final_text=text,
            cart_flow_recovery_used=used,
            step=2,
            max_tool_calls=5,
        )
        is expected
    )
