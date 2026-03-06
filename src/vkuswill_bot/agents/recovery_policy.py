"""Правила recovery-поведения для Shopping turn loop."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.cart_output_renderer import (
    looks_like_cart_ready_reply,
    looks_like_manual_cart_reply,
)
from vkuswill_bot.agents.mcp_response_parser import has_recipe_search_candidates
from vkuswill_bot.agents.response_analysis import looks_like_partial_recipe_reply


def should_continue_recipe_flow_recovery(
    *,
    cart_data_this_turn: dict[str, Any] | None,
    cart_intent: bool,
    tools_called_this_turn: bool,
    recipe_flow_started_this_turn: bool,
    final_text: str,
    cart_flow_recovery_used: bool,
    step: int,
    max_tool_calls: int,
) -> bool:
    return bool(
        cart_data_this_turn is None
        and cart_intent
        and tools_called_this_turn
        and recipe_flow_started_this_turn
        and looks_like_partial_recipe_reply(final_text)
        and not cart_flow_recovery_used
        and step < max_tool_calls
    )


def should_force_manual_recovery(
    *,
    cart_data_this_turn: dict[str, Any] | None,
    cart_intent: bool,
    final_text: str,
    manual_recovery_used: bool,
    step: int,
    max_tool_calls: int,
) -> bool:
    return bool(
        cart_data_this_turn is None
        and cart_intent
        and looks_like_manual_cart_reply(final_text)
        and not manual_recovery_used
        and step < max_tool_calls
    )


def should_force_cart_link_source_recovery(
    *,
    cart_data_this_turn: dict[str, Any] | None,
    cart_intent: bool,
    final_text: str,
    cart_creation_recovery_used: bool,
    step: int,
    max_tool_calls: int,
) -> bool:
    return bool(
        cart_data_this_turn is None
        and cart_intent
        and looks_like_cart_ready_reply(final_text)
        and not cart_creation_recovery_used
        and step < max_tool_calls
    )


def should_force_recipe_to_cart_hint(
    *,
    cart_intent: bool,
    recipe_flow_started_this_turn: bool,
    cart_data_this_turn: dict[str, Any] | None,
    recipe_to_cart_recovery_used: bool,
    history: list[dict[str, Any]],
    step: int,
    max_tool_calls: int,
) -> bool:
    return bool(
        cart_intent
        and recipe_flow_started_this_turn
        and cart_data_this_turn is None
        and not recipe_to_cart_recovery_used
        and has_recipe_search_candidates(history)
        and step < max_tool_calls
    )


def should_force_batch_search_hint(
    *,
    cart_intent: bool,
    cart_data_this_turn: dict[str, Any] | None,
    single_search_steps_streak: int,
    search_batch_recovery_used: bool,
    step: int,
    max_tool_calls: int,
) -> bool:
    return bool(
        cart_intent
        and cart_data_this_turn is None
        and single_search_steps_streak >= 3
        and not search_batch_recovery_used
        and step < max_tool_calls
    )


def should_force_step_budget_warning(
    *,
    cart_data_this_turn: dict[str, Any] | None,
    step_budget_warning_used: bool,
    step: int,
    max_tool_calls: int,
) -> bool:
    """Предупредить модель о приближении к лимиту шагов.

    Срабатывает на ~70% бюджета (но не раньше шага 5),
    если корзина ещё не создана и warning ещё не использован.
    """
    threshold = max(5, int(max_tool_calls * 0.7))
    return bool(
        cart_data_this_turn is None
        and not step_budget_warning_used
        and step >= threshold
        and step < max_tool_calls
    )
