"""Исполнитель одного turn-а ShoppingAgent (LLM loop + tool loop + recovery)."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from vkuswill_bot.agents.llm_helpers import (
    estimate_usage_details,
    extract_message,
    extract_text,
    extract_tool_calls,
)
from vkuswill_bot.agents.prompt_helpers import (
    build_llm_input_messages,
    resolve_prompt_mode,
)
from vkuswill_bot.agents.shopping_final_response_builder import DefaultFinalResponseBuilder
from vkuswill_bot.agents.shopping_tool_step_processor import DefaultToolStepProcessor
from vkuswill_bot.agents.shopping_turn_types import (
    ShoppingTurnAgentProtocol,
    build_turn_state,
)

if TYPE_CHECKING:
    from vkuswill_bot.services.chat_engine import ProgressCallback

logger = logging.getLogger(__name__)

_ERROR_GENERIC = "Не удалось обработать запрос. Попробуйте позже."
_ERROR_TOO_MANY_TOOLS = (
    "Не удалось завершить подбор в пределах лимита шагов. Уточните запрос и попробуйте ещё раз."
)


def _history_char_count(history: list[dict[str, Any]]) -> int:
    total = 0
    for message in history:
        with contextlib.suppress(Exception):
            total += len(json.dumps(message, ensure_ascii=False))
    return total


async def run_locked_turn(
    *,
    agent: ShoppingTurnAgentProtocol,
    user_id: int,
    text: str,
    on_progress: ProgressCallback | None,
    llm_provider: str,
) -> str:
    """Выполнить полный цикл обработки пользовательского сообщения под user-lock."""
    state = await build_turn_state(agent=agent, user_id=user_id, text=text)

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

    tool_step_processor: Any = DefaultToolStepProcessor()
    final_response_builder: Any = DefaultFinalResponseBuilder()

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
        llm_input_chars = _history_char_count(llm_input)
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
