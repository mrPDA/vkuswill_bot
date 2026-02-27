"""Исполнитель одного turn-а ShoppingAgent (LLM loop + tool loop + recovery)."""

from __future__ import annotations

import contextlib
import copy
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from vkuswill_bot.agents.cart_output_renderer import (
    extract_cart_safety_note,
    render_stable_cart_output,
)
from vkuswill_bot.agents.history_manager import history_char_count
from vkuswill_bot.agents.llm_helpers import (
    assistant_msg,
    estimate_usage_details,
    extract_message,
    extract_text,
    extract_tool_calls,
    parse_tool_args,
)
from vkuswill_bot.agents.mcp_response_parser import extract_cart_data
from vkuswill_bot.agents.prompt_helpers import (
    build_llm_input_messages,
    ensure_system_prompt,
    resolve_prompt_mode,
    resolve_prompt_profile,
)
from vkuswill_bot.agents.product_index_manager import (
    build_product_index_from_history,
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
    extract_explicit_pantry_requests,
    extract_structured_ingredient_requests,
    has_explicit_egg_pack_request,
    sanitize_recipe_ingredients_tool_result,
)
from vkuswill_bot.agents.response_analysis import is_cart_intent
from vkuswill_bot.agents.tool_preprocessor import (
    collect_requested_products_snapshot,
    preprocess_tool_args,
    restore_previous_quantities_for_additive_update,
)
from vkuswill_bot.agents.mcp_helpers import tool_progress_text
from vkuswill_bot.services.prompts import PromptProfile

if TYPE_CHECKING:
    from vkuswill_bot.services.chat_engine import ProgressCallback

logger = logging.getLogger(__name__)

_ERROR_GENERIC = "Не удалось обработать запрос. Попробуйте позже."
_ERROR_TOO_MANY_TOOLS = (
    "Не удалось завершить подбор в пределах лимита шагов. Уточните запрос и попробуйте ещё раз."
)


class ShoppingTurnAgentProtocol(Protocol):
    _history: dict[int, list[dict[str, Any]]]
    _last_cart_snapshot: dict[int, dict[str, Any]]
    _prompt_profiles_enabled: bool
    _compact_followup_prompt_enabled: bool
    _max_tool_calls: int
    _max_input_chars_per_turn: int
    _llm_routing_strategy: str

    def _should_start_fresh_context(
        self,
        *,
        text: str,
        history: list[dict[str, Any]] | None,
    ) -> bool: ...

    def _normalize_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    async def _load_user_preferences(self, user_id: int) -> dict[str, str]: ...

    async def _get_tools(self) -> list[dict[str, Any]]: ...

    def _create_trace(
        self,
        *,
        user_id: int,
        text: str,
        llm_provider: str,
        prompt_profile: PromptProfile,
    ) -> Any | None: ...

    def _resolve_model_for_provider(self, llm_provider: str) -> str: ...

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
    ) -> Any: ...

    @staticmethod
    def _extract_usage_details(response: Any) -> dict[str, int] | None: ...

    def _ensure_cart_price_summary(
        self,
        *,
        cart_data: dict[str, Any],
        product_index: dict[int, dict[str, Any]],
    ) -> None: ...

    def _trim_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
    ) -> str: ...

    def _capture_cart_snapshot(
        self,
        *,
        user_id: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None: ...

    def _prepare_tool_result_for_history(self, tool_name: str, tool_result: str) -> str: ...

    async def _recover_cart_from_recipe_search_history(
        self,
        *,
        history: list[dict[str, Any]],
        llm_provider: str,
        call_cache: dict[str, str],
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]: ...


@dataclass(slots=True)
class TurnState:
    history: list[dict[str, Any]]
    previous_cart_products: list[Any]
    prompt_profile: PromptProfile
    product_index_this_turn: dict[int, dict[str, Any]]
    cart_intent: bool
    explicit_pantry_requests: set[str]
    explicit_egg_pack_request: bool
    requested_ingredients: list[dict[str, Any]]
    user_preferences: dict[str, str]
    cart_data_this_turn: dict[str, Any] | None = None
    manual_recovery_used: bool = False
    cart_creation_recovery_used: bool = False
    search_batch_recovery_used: bool = False
    cart_flow_recovery_used: bool = False
    recipe_to_cart_recovery_used: bool = False
    single_search_steps_streak: int = 0
    tools_called_this_turn: bool = False
    recipe_flow_started_this_turn: bool = False
    total_llm_input_chars: int = 0
    mcp_call_cache: dict[str, str] = field(default_factory=dict)
    search_query_by_xml_id_this_turn: dict[int, str] = field(default_factory=dict)


async def _build_turn_state(
    *,
    agent: ShoppingTurnAgentProtocol,
    user_id: int,
    text: str,
) -> TurnState:
    history = agent._history.get(user_id)
    previous_cart_snapshot = agent._last_cart_snapshot.get(user_id)
    previous_cart_products = (
        previous_cart_snapshot.get("products")
        if isinstance(previous_cart_snapshot, dict)
        and isinstance(previous_cart_snapshot.get("products"), list)
        else []
    )
    if agent._should_start_fresh_context(text=text, history=history):
        history = None

    prompt_profile = resolve_prompt_profile(text=text, history=history)
    history = ensure_system_prompt(
        history=history,
        prompt_profile=prompt_profile,
        mode="start",
        prompt_profiles_enabled=agent._prompt_profiles_enabled,
    )

    product_index_this_turn: dict[int, dict[str, Any]] = build_product_index_from_history(history)
    history.append({"role": "user", "content": text})
    normalized_history = agent._normalize_history(history)

    return TurnState(
        history=normalized_history,
        previous_cart_products=previous_cart_products,
        prompt_profile=prompt_profile,
        product_index_this_turn=product_index_this_turn,
        cart_intent=is_cart_intent(text),
        explicit_pantry_requests=extract_explicit_pantry_requests(text),
        explicit_egg_pack_request=has_explicit_egg_pack_request(text),
        requested_ingredients=extract_structured_ingredient_requests(text),
        user_preferences=await agent._load_user_preferences(user_id),
    )


async def run_locked_turn(
    *,
    agent: ShoppingTurnAgentProtocol,
    user_id: int,
    text: str,
    on_progress: ProgressCallback | None,
    llm_provider: str,
) -> str:
    """Выполнить полный цикл обработки пользовательского сообщения под user-lock."""
    state = await _build_turn_state(agent=agent, user_id=user_id, text=text)

    tools = await agent._get_tools()
    trace = agent._create_trace(
        user_id=user_id,
        text=text,
        llm_provider=llm_provider,
        prompt_profile=state.prompt_profile,
    )

    async def _progress(message: str) -> None:
        if on_progress is None:
            return
        with contextlib.suppress(Exception):
            await on_progress(message)

    await _progress("⚙️ Анализирую запрос...")

    for step in range(1, agent._max_tool_calls + 1):
        prompt_mode = resolve_prompt_mode(
            step=step,
            expecting_final_answer=state.cart_data_this_turn is not None,
            compact_followup_prompt_enabled=agent._compact_followup_prompt_enabled,
        )
        llm_input = build_llm_input_messages(
            history=state.history,
            prompt_profile=state.prompt_profile,
            mode=prompt_mode,
            prompt_profiles_enabled=agent._prompt_profiles_enabled,
        )
        llm_input_chars = history_char_count(llm_input)
        state.total_llm_input_chars += llm_input_chars
        if step > 1 and state.total_llm_input_chars > agent._max_input_chars_per_turn:
            logger.warning(
                "ShoppingAgent prompt budget exceeded: total_chars=%d step=%d",
                state.total_llm_input_chars,
                step,
            )
            agent._history[user_id] = agent._normalize_history(state.history)
            if trace is not None:
                trace.update(
                    output=_ERROR_TOO_MANY_TOOLS,
                    metadata={
                        "reason": "prompt_budget_exceeded",
                        "provider": llm_provider,
                        "input_chars_total": state.total_llm_input_chars,
                    },
                )
            return _ERROR_TOO_MANY_TOOLS

        gen = None
        if trace is not None:
            gen = trace.generation(
                name=f"shopping-agent-{step}",
                model=agent._resolve_model_for_provider(llm_provider),
                input=llm_input,
                model_parameters={
                    "tools": len(tools),
                    "step": step,
                    "provider": llm_provider,
                    "routing_strategy": agent._llm_routing_strategy,
                    "prompt_profile": state.prompt_profile,
                    "prompt_mode": prompt_mode,
                    "compact_prompt": prompt_mode == "compact",
                },
            )

        try:
            response = await agent._call_llm(
                messages=llm_input,
                tools=tools,
                llm_provider=llm_provider,
            )
        except Exception as exc:
            logger.error("ShoppingAgent LLM error: %s", exc, exc_info=True)
            if gen is not None:
                gen.end(output=str(exc), level="ERROR", status_message="LLM error")
            agent._history[user_id] = state.history
            return _ERROR_GENERIC

        message = extract_message(response)
        usage_details = agent._extract_usage_details(response)
        usage_source = "provider"
        if usage_details is None:
            usage_details = estimate_usage_details(messages=llm_input, message=message)
            usage_source = "estimated" if usage_details is not None else "missing"
            logger.warning(
                "ShoppingAgent response has no usage details (provider=%s, step=%d, source=%s)",
                llm_provider,
                step,
                usage_source,
            )
        if gen is not None:
            gen.end(
                output=message,
                usage_details=usage_details,
                metadata={
                    "usage_source": usage_source,
                    "provider": llm_provider,
                    "prompt_profile": state.prompt_profile,
                },
            )

        tool_calls = extract_tool_calls(message)
        if not tool_calls:
            final_text = extract_text(message) or _ERROR_GENERIC

            if should_continue_recipe_flow_recovery(
                cart_data_this_turn=state.cart_data_this_turn,
                cart_intent=state.cart_intent,
                tools_called_this_turn=state.tools_called_this_turn,
                recipe_flow_started_this_turn=state.recipe_flow_started_this_turn,
                final_text=final_text,
                cart_flow_recovery_used=state.cart_flow_recovery_used,
                step=step,
                max_tool_calls=agent._max_tool_calls,
            ):
                state.cart_flow_recovery_used = True
                logger.info("Recipe flow recovery: continue tool-chain until cart_link_create")
                state.history.append(assistant_msg(message))
                state.history.append(
                    {"role": "system", "content": FORCE_CART_FLOW_CONTINUATION_HINT}
                )
                state.history = agent._normalize_history(state.history)
                continue

            if should_force_manual_recovery(
                cart_data_this_turn=state.cart_data_this_turn,
                cart_intent=state.cart_intent,
                final_text=final_text,
                manual_recovery_used=state.manual_recovery_used,
                step=step,
                max_tool_calls=agent._max_tool_calls,
            ):
                state.manual_recovery_used = True
                state.history.append(assistant_msg(message))
                state.history.append({"role": "system", "content": FORCE_CART_RECOVERY_HINT})
                state.history = agent._normalize_history(state.history)
                continue

            if should_force_cart_link_source_recovery(
                cart_data_this_turn=state.cart_data_this_turn,
                cart_intent=state.cart_intent,
                final_text=final_text,
                cart_creation_recovery_used=state.cart_creation_recovery_used,
                step=step,
                max_tool_calls=agent._max_tool_calls,
            ):
                state.cart_creation_recovery_used = True
                state.history.append(assistant_msg(message))
                state.history.append({"role": "system", "content": FORCE_CART_LINK_SOURCE_HINT})
                state.history = agent._normalize_history(state.history)
                continue

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
            return final_text

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

            await _progress(tool_progress_text(tool_name))
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
            max_tool_calls=agent._max_tool_calls,
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
            max_tool_calls=agent._max_tool_calls,
        ):
            state.search_batch_recovery_used = True
            state.history.append({"role": "system", "content": FORCE_BATCH_SEARCH_HINT})
            state.history = agent._normalize_history(state.history)

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
                    "tool_calls": agent._max_tool_calls,
                },
            )
        return final_text

    agent._history[user_id] = state.history
    if trace is not None:
        trace.update(
            output=_ERROR_TOO_MANY_TOOLS,
            metadata={"reason": "max_tool_calls", "provider": llm_provider},
        )
    return _ERROR_TOO_MANY_TOOLS
