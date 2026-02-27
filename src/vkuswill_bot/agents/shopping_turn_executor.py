"""Исполнитель одного turn-а ShoppingAgent (LLM loop + tool loop + recovery)."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from vkuswill_bot.agents.history_manager import history_char_count
from vkuswill_bot.agents.llm_helpers import (
    estimate_usage_details,
    extract_message,
    extract_text,
    extract_tool_calls,
)
from vkuswill_bot.agents.prompt_helpers import (
    build_llm_input_messages,
    ensure_system_prompt,
    resolve_prompt_mode,
    resolve_prompt_profile,
)
from vkuswill_bot.agents.product_index_manager import (
    build_product_index_from_history,
)
from vkuswill_bot.agents.recipe_helpers import (
    extract_explicit_pantry_requests,
    extract_structured_ingredient_requests,
    has_explicit_egg_pack_request,
)
from vkuswill_bot.agents.response_analysis import is_cart_intent
from vkuswill_bot.agents.shopping_turn_components import (
    DefaultFinalResponseBuilder,
    DefaultToolStepProcessor,
    FinalResponseBuilder,
    ToolStepProcessor,
)
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

    tool_step_processor: ToolStepProcessor = DefaultToolStepProcessor()
    final_response_builder: FinalResponseBuilder = DefaultFinalResponseBuilder()

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
            no_tool_calls_outcome = final_response_builder.handle_no_tool_calls(
                agent=agent,
                state=state,
                message=message,
                final_text=final_text,
                step=step,
                user_id=user_id,
                llm_provider=llm_provider,
                trace=trace,
                max_tool_calls=agent._max_tool_calls,
            )
            if no_tool_calls_outcome.continue_loop:
                continue
            return no_tool_calls_outcome.final_text or _ERROR_GENERIC

        await tool_step_processor.run_step(
            agent=agent,
            state=state,
            message=message,
            tool_calls=tool_calls,
            step=step,
            user_id=user_id,
            text=text,
            llm_provider=llm_provider,
            trace=trace,
            on_progress=_progress,
            max_tool_calls=agent._max_tool_calls,
        )

    return await final_response_builder.finalize_after_max_steps(
        agent=agent,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        trace=trace,
        max_tool_calls=agent._max_tool_calls,
        too_many_tools_error=_ERROR_TOO_MANY_TOOLS,
    )
