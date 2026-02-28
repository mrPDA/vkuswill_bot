"""Recovery hint application for tool-step execution."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.recovery_hints import (
    FORCE_BATCH_SEARCH_HINT,
    FORCE_RECIPE_TO_CART_HINT,
)
from vkuswill_bot.agents.recovery_policy import (
    should_force_batch_search_hint,
    should_force_recipe_to_cart_hint,
)


def apply_post_step_recovery_hints(
    *,
    agent: Any,
    state: Any,
    step: int,
    max_tool_calls: int,
) -> None:
    if should_force_recipe_to_cart_hint(
        cart_intent=state.cart_intent,
        recipe_flow_started_this_turn=state.recipe_flow_started_this_turn,
        cart_data_this_turn=state.cart_data_this_turn,
        recipe_to_cart_recovery_used=state.recipe_to_cart_recovery_used,
        history=state.history,
        step=step,
        max_tool_calls=max_tool_calls,
    ):
        state.recipe_to_cart_recovery_used = True
        state.history.append({"role": "system", "content": FORCE_RECIPE_TO_CART_HINT})
        state.history = agent._normalize_history(state.history)

    if should_force_batch_search_hint(
        cart_intent=state.cart_intent,
        cart_data_this_turn=state.cart_data_this_turn,
        single_search_steps_streak=state.single_search_steps_streak,
        search_batch_recovery_used=state.search_batch_recovery_used,
        step=step,
        max_tool_calls=max_tool_calls,
    ):
        state.search_batch_recovery_used = True
        state.history.append({"role": "system", "content": FORCE_BATCH_SEARCH_HINT})
        state.history = agent._normalize_history(state.history)
