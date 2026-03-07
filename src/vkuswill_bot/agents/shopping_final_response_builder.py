"""Final-response and recovery component for shopping turn execution."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from vkuswill_bot.agents.cart_output_renderer import (
    extract_cart_safety_note,
    extract_llm_surrounding_text,
    render_stable_cart_output,
)
from vkuswill_bot.agents.llm_helpers import assistant_msg
from vkuswill_bot.agents.meal_plan_response_contract import (
    render_meal_plan_contract_response,
)
from vkuswill_bot.agents.response_analysis import looks_like_textual_tool_call_reply
from vkuswill_bot.agents.recovery_hints import (
    FORCE_CART_FLOW_CONTINUATION_HINT,
    FORCE_CART_LINK_SOURCE_HINT,
    FORCE_CART_RECOVERY_HINT,
    FORCE_NATIVE_TOOL_CALL_HINT,
)
from vkuswill_bot.agents.recovery_policy import (
    should_continue_recipe_flow_recovery,
    should_force_cart_link_source_recovery,
    should_force_manual_recovery,
    should_force_native_tool_call_recovery,
)
from vkuswill_bot.agents.shopping_turn_contracts import NoToolCallsOutcome

logger = logging.getLogger(__name__)
_TEXTUAL_TOOL_CALL_ERROR = "Не удалось корректно выполнить шаг обработки. Попробуйте ещё раз."


class DefaultFinalResponseBuilder:
    @staticmethod
    def _render_meal_plan_response(
        *,
        state: Any,
        fallback_message: str = "",
    ) -> str:
        return render_meal_plan_contract_response(
            history=state.history,
            cart_data=state.cart_data_this_turn,
            user_preference_profile=state.user_preference_profile,
            fallback_message=fallback_message,
        )

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
        if should_force_native_tool_call_recovery(
            final_text=final_text,
            textual_tool_call_recovery_used=state.textual_tool_call_recovery_used,
            step=step,
            max_tool_calls=max_tool_calls,
        ):
            state.textual_tool_call_recovery_used = True
            state.history.append(assistant_msg(message))
            state.history.append({"role": "system", "content": FORCE_NATIVE_TOOL_CALL_HINT})
            state.history = agent._normalize_history(state.history)
            return NoToolCallsOutcome(continue_loop=True)

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

        if state.textual_tool_call_recovery_used and (
            not final_text.strip() or looks_like_textual_tool_call_reply(final_text)
        ):
            final_text = _TEXTUAL_TOOL_CALL_ERROR

        if state.prompt_profile == "meal_plan":
            final_text = self._render_meal_plan_response(
                state=state,
                fallback_message=final_text if state.cart_data_this_turn is None else "",
            )
        elif state.cart_data_this_turn is not None:
            agent._ensure_cart_price_summary(
                cart_data=state.cart_data_this_turn,
                product_index=state.product_index_this_turn,
            )
            preamble, postamble = extract_llm_surrounding_text(final_text)
            safety_note = extract_cart_safety_note(final_text)
            cart_output = render_stable_cart_output(
                state.cart_data_this_turn,
                safety_note=safety_note,
                include_intro=not bool(preamble),
            )
            parts = [p for p in (preamble, cart_output, postamble) if p]
            final_text = "\n\n".join(parts)

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
            if state.cart_data_this_turn is None:
                (
                    state.cart_data_this_turn,
                    recovered_cart_args,
                    recovered_cart_result,
                ) = await agent._recover_cart_from_recipe_ingredients_history(
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

        if state.prompt_profile == "meal_plan":
            final_text = self._render_meal_plan_response(
                state=state,
                fallback_message=too_many_tools_error if state.cart_data_this_turn is None else "",
            )
            agent._history[user_id] = agent._trim_history(
                [*state.history, {"role": "assistant", "content": final_text}],
            )
            if trace is not None:
                trace.update(
                    output=final_text,
                    metadata={
                        "reason": (
                            "max_tool_calls_meal_plan_with_cart"
                            if state.cart_data_this_turn is not None
                            else "max_tool_calls_meal_plan_fail_soft"
                        ),
                        "provider": llm_provider,
                        "tool_calls": max_tool_calls,
                    },
                )
            return final_text

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

    async def try_recipe_cart_recovery(
        self,
        *,
        agent: Any,
        state: Any,
        user_id: int,
        llm_provider: str,
        trace: Any | None,
    ) -> str | None:
        """Попытаться восстановить корзину из recipe_search или recipe_ingredients."""
        cart_data = None
        cart_args: dict[str, Any] = {}
        cart_result = ""

        with contextlib.suppress(Exception):
            (
                cart_data,
                cart_args,
                cart_result,
            ) = await agent._recover_cart_from_recipe_search_history(
                history=state.history,
                llm_provider=llm_provider,
                call_cache=state.mcp_call_cache,
            )
        if cart_data is None:
            with contextlib.suppress(Exception):
                (
                    cart_data,
                    cart_args,
                    cart_result,
                ) = await agent._recover_cart_from_recipe_ingredients_history(
                    history=state.history,
                    llm_provider=llm_provider,
                    call_cache=state.mcp_call_cache,
                )
        if cart_data is None:
            return None

        agent._capture_cart_snapshot(
            user_id=user_id,
            tool_name="vkusvill_cart_link_create",
            args=cart_args,
            result=cart_result,
        )
        state.cart_data_this_turn = cart_data
        agent._ensure_cart_price_summary(
            cart_data=cart_data,
            product_index=state.product_index_this_turn,
        )
        if state.prompt_profile == "meal_plan":
            final_text = self._render_meal_plan_response(state=state)
        else:
            final_text = render_stable_cart_output(cart_data)
        agent._history[user_id] = agent._trim_history(
            [*state.history, {"role": "assistant", "content": final_text}],
        )
        if trace is not None:
            with contextlib.suppress(Exception):
                trace.update(
                    output=final_text,
                    metadata={
                        "reason": "llm_error_recovered_with_cart",
                        "provider": llm_provider,
                    },
                )
        logger.info("Recovered cart from recipe data after LLM error for user %d", user_id)
        return final_text
