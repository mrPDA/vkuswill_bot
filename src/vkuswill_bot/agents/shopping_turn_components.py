"""Компоненты исполнения turn-а: шаги инструментов и финализация ответа."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from vkuswill_bot.agents.cart_output_renderer import (
    extract_cart_safety_note,
    render_stable_cart_output,
)
from vkuswill_bot.agents.llm_helpers import assistant_msg, parse_tool_args
from vkuswill_bot.agents.mcp_helpers import tool_progress_text
from vkuswill_bot.agents.mcp_response_parser import extract_cart_data
from vkuswill_bot.agents.product_index_manager import (
    update_product_index_from_tool_result,
    update_search_query_by_xml_id,
)
from vkuswill_bot.agents.recovery_hints import (
    FORCE_BATCH_SEARCH_HINT,
    FORCE_CART_FLOW_CONTINUATION_HINT,
    FORCE_CART_LINK_SOURCE_HINT,
    FORCE_CART_RECOVERY_HINT,
    FORCE_RECIPE_TO_CART_HINT,
)
from vkuswill_bot.agents.recovery_policy import (
    should_continue_recipe_flow_recovery,
    should_force_batch_search_hint,
    should_force_cart_link_source_recovery,
    should_force_manual_recovery,
    should_force_recipe_to_cart_hint,
)
from vkuswill_bot.agents.recipe_helpers import (
    apply_requested_quantity_overrides,
    match_requested_ingredient,
    sanitize_recipe_ingredients_tool_result,
)
from vkuswill_bot.agents.recipe_quantity_calculator import RecipeQuantityCalculator
from vkuswill_bot.agents.tool_preprocessor import (
    collect_requested_products_snapshot,
    preprocess_tool_args,
    restore_previous_quantities_for_additive_update,
)

ProgressReporter = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class NoToolCallsOutcome:
    continue_loop: bool
    final_text: str | None = None


class ToolStepProcessor(Protocol):
    async def run_step(
        self,
        *,
        agent: Any,
        state: Any,
        message: Any,
        tool_calls: list[dict[str, Any]],
        step: int,
        user_id: int,
        text: str,
        llm_provider: str,
        trace: Any | None,
        on_progress: ProgressReporter,
        max_tool_calls: int,
    ) -> None: ...


class FinalResponseBuilder(Protocol):
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
    ) -> NoToolCallsOutcome: ...

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
    ) -> str: ...


class DefaultToolStepProcessor:
    async def run_step(
        self,
        *,
        agent: Any,
        state: Any,
        message: Any,
        tool_calls: list[dict[str, Any]],
        step: int,
        user_id: int,
        text: str,
        llm_provider: str,
        trace: Any | None,
        on_progress: ProgressReporter,
        max_tool_calls: int,
    ) -> None:
        state.history.append(assistant_msg(message))
        step_tool_names = [
            str(tool_call.get("name", "")).strip()
            for tool_call in tool_calls
            if str(tool_call.get("name", "")).strip()
        ]
        if (
            state.cart_intent
            and state.cart_data_this_turn is None
            and len(step_tool_names) == 1
            and step_tool_names[0] == "vkusvill_products_search"
        ):
            state.single_search_steps_streak += 1
        else:
            state.single_search_steps_streak = 0

        for tool_call in tool_calls:
            tool_name = str(tool_call.get("name", "")).strip()
            tool_call_id = str(tool_call.get("id", "")).strip()
            raw_tool_args = parse_tool_args(tool_call.get("arguments"))
            raw_tool_args_snapshot = copy.deepcopy(raw_tool_args)
            requested_quantity_overrides: dict[int, float] = {}
            tool_args = preprocess_tool_args(
                tool_name,
                raw_tool_args,
                user_preferences=state.user_preferences,
                product_index=state.product_index_this_turn,
                explicit_egg_pack_request=state.explicit_egg_pack_request,
                requested_ingredients=state.requested_ingredients,
                search_query_by_xml_id=state.search_query_by_xml_id_this_turn,
                requested_quantity_overrides=requested_quantity_overrides,
            )
            apply_requested_ingredient_overrides(
                tool_name=tool_name,
                tool_args=tool_args,
                product_index=state.product_index_this_turn,
                explicit_egg_pack_request=state.explicit_egg_pack_request,
                requested_ingredients=state.requested_ingredients,
                search_query_by_xml_id=state.search_query_by_xml_id_this_turn,
                requested_quantity_overrides=requested_quantity_overrides,
            )
            tool_args = restore_previous_quantities_for_additive_update(
                tool_name=tool_name,
                tool_args=tool_args,
                user_text=text,
                previous_products=state.previous_cart_products,
                requested_quantity_overrides=requested_quantity_overrides,
            )
            requested_products_snapshot = (
                apply_requested_quantity_overrides(
                    collect_requested_products_snapshot(
                        raw_tool_args_snapshot,
                        product_index=state.product_index_this_turn,
                        explicit_egg_pack_request=state.explicit_egg_pack_request,
                    ),
                    requested_quantity_overrides,
                )
                if tool_name == "vkusvill_cart_link_create"
                else []
            )

            await on_progress(tool_progress_text(tool_name))
            tool_span = trace.span(name=f"tool:{tool_name}", input=tool_args) if trace else None

            tool_result = await agent._call_mcp_tool(
                name=tool_name,
                arguments=tool_args,
                llm_provider=llm_provider,
                call_cache=state.mcp_call_cache,
            )
            if tool_name == "recipe_ingredients":
                tool_result = sanitize_recipe_ingredients_tool_result(
                    tool_result=tool_result,
                    explicit_pantry_requests=state.explicit_pantry_requests,
                )
            update_product_index_from_tool_result(
                product_index=state.product_index_this_turn,
                tool_name=tool_name,
                tool_result=tool_result,
            )
            if tool_name == "vkusvill_products_search":
                update_search_query_by_xml_id(
                    search_query_by_xml_id=state.search_query_by_xml_id_this_turn,
                    tool_args=tool_args,
                    tool_result=tool_result,
                )
            state.tools_called_this_turn = True
            if tool_name in {"recipe_ingredients", "recipe_search"}:
                state.recipe_flow_started_this_turn = True

            cart_data = extract_cart_data(tool_name=tool_name, tool_result=tool_result)
            if cart_data is not None:
                products = tool_args.get("products")
                if isinstance(products, list) and "products" not in cart_data:
                    cart_data["products"] = products
                if requested_products_snapshot and "requested_products" not in cart_data:
                    cart_data["requested_products"] = requested_products_snapshot
                state.cart_data_this_turn = cart_data

            agent._capture_cart_snapshot(
                user_id=user_id,
                tool_name=tool_name,
                args=tool_args,
                result=tool_result,
            )

            if tool_span is not None:
                tool_span.end(output=tool_result[:5000])

            state.history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": agent._prepare_tool_result_for_history(tool_name, tool_result),
                }
            )

        state.history = agent._normalize_history(state.history)
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


def apply_requested_ingredient_overrides(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    product_index: dict[int, dict[str, Any]],
    explicit_egg_pack_request: bool,
    requested_ingredients: list[dict[str, Any]],
    search_query_by_xml_id: dict[int, str],
    requested_quantity_overrides: dict[int, float],
) -> None:
    if tool_name != "vkusvill_cart_link_create":
        return
    if explicit_egg_pack_request:
        return
    if not requested_ingredients:
        return
    products = tool_args.get("products")
    if not isinstance(products, list):
        return

    for row in products:
        if not isinstance(row, dict):
            continue
        xml_id_raw = row.get("xml_id")
        if isinstance(xml_id_raw, bool):
            continue
        try:
            xml_id = int(xml_id_raw)
        except (TypeError, ValueError):
            continue
        product = product_index.get(xml_id)
        if not isinstance(product, dict):
            continue
        matched_ingredient = match_requested_ingredient(
            product=product,
            xml_id=xml_id,
            requested_ingredients=requested_ingredients,
            search_query_by_xml_id=search_query_by_xml_id,
        )
        if matched_ingredient is None:
            continue
        requested_q, purchase_q = RecipeQuantityCalculator.calculate_requested_and_purchase_q(
            ingredient=matched_ingredient,
            item=product,
        )
        if requested_q > 0:
            requested_quantity_overrides[xml_id] = requested_q
        row["q"] = purchase_q
