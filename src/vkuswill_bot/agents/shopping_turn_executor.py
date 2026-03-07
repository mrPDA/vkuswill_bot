"""Исполнитель одного turn-а ShoppingAgent (LLM loop + tool loop + recovery)."""

from __future__ import annotations
import contextlib
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any
from vkuswill_bot.agents.exceptions import LLMOverloadedError
from vkuswill_bot.agents.meal_plan_executor import run_meal_plan_turn
from vkuswill_bot.agents.shopping_turn_message_ops import (
    build_turn_llm_input,
    estimate_usage,
    unpack_llm_response,
)
from vkuswill_bot.agents.shopping_final_response_builder import DefaultFinalResponseBuilder
from vkuswill_bot.agents.shopping_tool_step_processor import DefaultToolStepProcessor
from vkuswill_bot.agents.shopping_turn_types import (
    ShoppingTurnAgentProtocol,
    build_turn_state,
)
from vkuswill_bot.services.meal_plan_rollout_policy import (
    evaluate_non_prod_rollout_bypass,
    resolve_rollout_percent,
)
from vkuswill_bot.services.meal_plan_trace_metadata import (
    history_char_count,
    resolve_metrics_trace_id,
)

if TYPE_CHECKING:
    from vkuswill_bot.services.chat_engine import ProgressCallback
logger = logging.getLogger(__name__)
_ERROR_GENERIC = "Не удалось обработать запрос. Попробуйте позже."
_ERROR_OVERLOADED = "Сейчас много запросов, все ассистенты заняты. Попробуйте через 1–2 минуты."
_ERROR_TOO_MANY_TOOLS = (
    "Не удалось завершить в пределах лимита шагов. Уточните запрос и попробуйте ещё раз."
)


def _is_user_in_rollout(*, user_id: int, rollout_percent: int) -> bool:
    percent = max(0, min(100, int(rollout_percent)))
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    digest = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < percent


async def run_locked_turn(
    *,
    agent: ShoppingTurnAgentProtocol,
    user_id: int,
    text: str,
    on_progress: ProgressCallback | None,
    llm_provider: str,
    record_routing_event: bool = True,
) -> str:
    """Выполнить полный цикл обработки пользовательского сообщения под user-lock."""
    trace = agent._create_trace(
        user_id=user_id,
        text=text,
        llm_provider=llm_provider,
        prompt_profile=None,
    )
    state = await build_turn_state(agent=agent, user_id=user_id, text=text, trace=trace)
    if trace is not None:
        trace.update(
            metadata={
                "provider": llm_provider,
                "prompt_profile": state.prompt_profile,
                "llm_prompt_profile": state.llm_prompt_profile,
                "llm_prompt_confidence": state.llm_prompt_confidence,
                "llm_prompt_reason": state.llm_prompt_reason,
                "heuristic_prompt_profile": state.heuristic_prompt_profile,
                "intent_conflict": state.intent_conflict,
                "intent_conflict_severity": state.intent_conflict_severity,
                "route_override_applied": state.route_override_applied,
                "route_override_from": state.route_override_from,
                "route_override_to": state.route_override_to,
                "route_override_reason": state.route_override_reason,
            }
        )

    async def _progress(message: str) -> None:
        if on_progress is None:
            return
        with contextlib.suppress(Exception):
            await on_progress(message)

    shadow_mode = bool(getattr(agent, "_meal_plan_shadow_mode_enabled", False))
    rollout_percent = int(getattr(agent, "_meal_plan_rollout_percent", 100))
    controller = getattr(agent, "_meal_plan_rollout_controller", None)
    bypass = evaluate_non_prod_rollout_bypass(
        enabled=bool(getattr(agent, "_meal_plan_allow_unvalidated_rollout", False)),
        environment=str(getattr(agent, "_deployment_environment", "production")),
        reason=str(getattr(agent, "_meal_plan_unvalidated_rollout_reason", "")),
        actor=str(getattr(agent, "_meal_plan_unvalidated_rollout_actor", "")),
        expires_at=str(getattr(agent, "_meal_plan_unvalidated_rollout_expires_at", "")),
        max_ttl_seconds=int(
            getattr(agent, "_meal_plan_unvalidated_rollout_max_ttl_seconds", 86400)
        ),
    )
    rollout_percent = await resolve_rollout_percent(
        shadow_mode=shadow_mode,
        configured_percent=rollout_percent,
        controller=controller,
        allow_unvalidated=bypass.allow_unvalidated,
    )
    can_use_executor = (
        state.prompt_profile == "meal_plan"
        and getattr(agent, "_meal_plan_executor_enabled", False)
        and not shadow_mode
        and _is_user_in_rollout(user_id=user_id, rollout_percent=rollout_percent)
    )
    metrics_trace_id = resolve_metrics_trace_id(trace=trace, user_id=user_id)
    metrics_sink = getattr(agent, "_meal_plan_metrics_sink", None)
    if record_routing_event and metrics_sink is not None:
        with contextlib.suppress(Exception):
            await metrics_sink.record_routing(
                profile=state.prompt_profile,
                executed_via_executor=can_use_executor,
                shadow_mode=shadow_mode,
                user_id=user_id,
                trace_id=metrics_trace_id,
                rollout_bypass=bypass.audit.as_dict(),
            )
    if can_use_executor:

        async def _fallback_to_standard_turn(reason: str) -> str:
            notice = f"⚠️ {reason}. Перехожу к стандартной обработке запроса."
            previous = bool(getattr(agent, "_meal_plan_executor_enabled", False))
            agent._meal_plan_executor_enabled = False
            try:
                fallback_text = await run_locked_turn(
                    agent=agent,
                    user_id=user_id,
                    text=text,
                    on_progress=on_progress,
                    llm_provider=llm_provider,
                    record_routing_event=False,
                )
            finally:
                agent._meal_plan_executor_enabled = previous
            return f"{notice}\n\n{fallback_text}".strip()

        started_at = time.monotonic()
        result = await run_meal_plan_turn(
            agent=agent,
            state=state,
            user_id=user_id,
            text=text,
            llm_provider=llm_provider,
            trace=trace,
            on_progress=_progress,
            fallback_to_standard_turn=_fallback_to_standard_turn,
        )
        if metrics_sink is not None:
            with contextlib.suppress(Exception):
                await metrics_sink.record_executor_result(
                    outcome=(
                        "fallback"
                        if "Перехожу к стандартной обработке запроса." in result
                        else "success"
                    ),
                    latency_ms=(time.monotonic() - started_at) * 1000,
                    user_id=user_id,
                    trace_id=metrics_trace_id,
                )
        return result
    tools = await agent._get_tools()
    tool_step_processor: Any = DefaultToolStepProcessor()
    final_response_builder: Any = DefaultFinalResponseBuilder()
    await _progress("⚙️ Анализирую запрос...")
    for step in range(1, agent._max_tool_calls + 1):
        prompt_mode, llm_input, prompt_metadata = build_turn_llm_input(
            history=state.history,
            prompt_profile=state.prompt_profile,
            step=step,
            expecting_final_answer=state.cart_data_this_turn is not None,
            compact_followup_prompt_enabled=agent._compact_followup_prompt_enabled,
            prompt_profiles_enabled=agent._prompt_profiles_enabled,
            preference_profile=state.user_preference_profile,
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
        max_tokens_override = None
        if (
            state.recipe_flow_started_this_turn
            and getattr(agent, "_llm_max_tokens_recipe", None) is not None
        ):
            max_tokens_override = agent._llm_max_tokens_recipe
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
                    "temperature": agent._llm_temperature,
                    "max_tokens": (
                        max_tokens_override
                        if max_tokens_override is not None
                        else getattr(agent, "_llm_max_tokens", None)
                    ),
                },
                metadata={"prompt": prompt_metadata},
            )
        try:
            response = await agent._call_llm(
                messages=llm_input,
                tools=tools,
                llm_provider=llm_provider,
                max_tokens_override=max_tokens_override,
            )
        except LLMOverloadedError:
            logger.warning("ShoppingAgent LLM overloaded for user %d at step %d", user_id, step)
            if gen is not None:
                gen.end(output=_ERROR_OVERLOADED, level="WARNING", status_message="LLM overloaded")
            agent._history[user_id] = state.history
            return _ERROR_OVERLOADED
        except Exception as exc:
            logger.error("ShoppingAgent LLM error: %s", exc, exc_info=True)
            if gen is not None:
                gen.end(output=str(exc), level="ERROR", status_message="LLM error")

            if (
                state.cart_data_this_turn is None
                and state.cart_intent
                and state.recipe_flow_started_this_turn
            ):
                recovered = await final_response_builder.try_recipe_cart_recovery(
                    agent=agent,
                    state=state,
                    user_id=user_id,
                    llm_provider=llm_provider,
                    trace=trace,
                )
                if recovered is not None:
                    return recovered
            agent._history[user_id] = state.history
            return _ERROR_GENERIC
        message, tool_calls, final_text = unpack_llm_response(response)
        usage_details = agent._extract_usage_details(response)
        usage_source = "provider"
        if usage_details is None:
            usage_details = estimate_usage(messages=llm_input, message=message)
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
        if not tool_calls:
            final_text = final_text or _ERROR_GENERIC
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
