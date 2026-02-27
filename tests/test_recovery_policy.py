"""Unit-тесты для vkuswill_bot.agents.recovery_policy."""

from __future__ import annotations

import json

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
