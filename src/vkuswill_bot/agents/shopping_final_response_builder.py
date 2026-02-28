"""Final-response and recovery component for shopping turn execution."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.cart_output_renderer import (
    extract_cart_safety_note,
    render_stable_cart_output,
)
from vkuswill_bot.agents.llm_helpers import assistant_msg
from vkuswill_bot.agents.recovery_hints import (
    FORCE_CART_FLOW_CONTINUATION_HINT,
    FORCE_CART_LINK_SOURCE_HINT,
    FORCE_CART_RECOVERY_HINT,
)
from vkuswill_bot.agents.recovery_policy import (
    should_continue_recipe_flow_recovery,
    should_force_cart_link_source_recovery,
    should_force_manual_recovery,
)
from vkuswill_bot.agents.shopping_turn_contracts import NoToolCallsOutcome


class DefaultFinalResponseBuilder:
    def handle_no_tool_calls(
        self,
        *,
        agent: Any,
        state: Any,
        message: Any,
        final_text: str,
        step: int,
        user_id: int,
        llm_provider: str,
        trace: Any | None,
        max_tool_calls: int,
    ) -> NoToolCallsOutcome:
        if should_continue_recipe_flow_recovery(
            cart_data_this_turn=state.cart_data_this_turn,
            cart_intent=state.cart_intent,
            tools_called_this_turn=state.tools_called_this_turn,
            recipe_flow_started_this_turn=state.recipe_flow_started_this_turn,
            final_text=final_text,
            cart_flow_recovery_used=state.cart_flow_recovery_used,
            step=step,
            max_tool_calls=max_tool_calls,
        ):
            state.cart_flow_recovery_used = True
            state.history.append(assistant_msg(message))
            state.history.append({"role": "system", "content": FORCE_CART_FLOW_CONTINUATION_HINT})
            state.history = agent._normalize_history(state.history)
            return NoToolCallsOutcome(continue_loop=True)

        if should_force_manual_recovery(
            cart_data_this_turn=state.cart_data_this_turn,
            cart_intent=state.cart_intent,
            final_text=final_text,
            manual_recovery_used=state.manual_recovery_used,
            step=step,
            max_tool_calls=max_tool_calls,
        ):
            state.manual_recovery_used = True
            state.history.append(assistant_msg(message))
            state.history.append({"role": "system", "content": FORCE_CART_RECOVERY_HINT})
            state.history = agent._normalize_history(state.history)
            return NoToolCallsOutcome(continue_loop=True)

        if should_force_cart_link_source_recovery(
            cart_data_this_turn=state.cart_data_this_turn,
            cart_intent=state.cart_intent,
            final_text=final_text,
            cart_creation_recovery_used=state.cart_creation_recovery_used,
            step=step,
            max_tool_calls=max_tool_calls,
        ):
            state.cart_creation_recovery_used = True
            state.history.append(assistant_msg(message))
            state.history.append({"role": "system", "content": FORCE_CART_LINK_SOURCE_HINT})
            state.history = agent._normalize_history(state.history)
            return NoToolCallsOutcome(continue_loop=True)

        if state.cart_data_this_turn is not None:
            agent._ensure_cart_price_summary(
                cart_data=state.cart_data_this_turn,
                product_index=state.product_index_this_turn,
            )
            safety_note = extract_cart_safety_note(final_text)
            final_text = render_stable_cart_output(
                state.cart_data_this_turn,
                safety_note=safety_note,
            )

        agent._history[user_id] = agent._trim_history([*state.history, assistant_msg(message)])
        if trace is not None:
            trace.update(
                output=final_text,
                metadata={"tool_calls": step - 1, "provider": llm_provider},
            )
        return NoToolCallsOutcome(continue_loop=False, final_text=final_text)

    async def finalize_after_max_steps(
        self,
        *,
        agent: Any,
        state: Any,
        user_id: int,
        llm_provider: str,
        trace: Any | None,
        max_tool_calls: int,
        too_many_tools_error: str,
    ) -> str:
        if (
            state.cart_data_this_turn is None
            and state.cart_intent
            and state.recipe_flow_started_this_turn
        ):
            (
                state.cart_data_this_turn,
                recovered_cart_args,
                recovered_cart_result,
            ) = await agent._recover_cart_from_recipe_search_history(
                history=state.history,
                llm_provider=llm_provider,
                call_cache=state.mcp_call_cache,
            )
            if state.cart_data_this_turn is not None:
                agent._capture_cart_snapshot(
                    user_id=user_id,
                    tool_name="vkusvill_cart_link_create",
                    args=recovered_cart_args,
                    result=recovered_cart_result,
                )

        if state.cart_data_this_turn is not None:
            agent._ensure_cart_price_summary(
                cart_data=state.cart_data_this_turn,
                product_index=state.product_index_this_turn,
            )
            final_text = render_stable_cart_output(state.cart_data_this_turn)
            agent._history[user_id] = agent._trim_history(
                [*state.history, {"role": "assistant", "content": final_text}],
            )
            if trace is not None:
                trace.update(
                    output=final_text,
                    metadata={
                        "reason": "max_tool_calls_recovered_with_cart",
                        "provider": llm_provider,
                        "tool_calls": max_tool_calls,
                    },
                )
            return final_text

        agent._history[user_id] = state.history
        if trace is not None:
            trace.update(
                output=too_many_tools_error,
                metadata={"reason": "max_tool_calls", "provider": llm_provider},
            )
        return too_many_tools_error
