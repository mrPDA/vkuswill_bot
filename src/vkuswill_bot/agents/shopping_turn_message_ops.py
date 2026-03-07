"""LLM message operations shared by shopping turn executor."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.llm_helpers import (
    estimate_usage_details,
    extract_message,
    extract_text,
    extract_tool_calls,
)
from vkuswill_bot.agents.meal_plan_prompt_context import inject_meal_plan_profile_context
from vkuswill_bot.agents.prompt_helpers import (
    build_llm_input_messages_with_metadata,
    resolve_prompt_mode,
)


def build_turn_llm_input(
    *,
    history: list[dict[str, Any]],
    prompt_profile: str,
    step: int,
    expecting_final_answer: bool,
    compact_followup_prompt_enabled: bool,
    prompt_profiles_enabled: bool,
    preference_profile: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Build per-step LLM input with resolved prompt mode and profile context injection."""
    prompt_mode = resolve_prompt_mode(
        step=step,
        expecting_final_answer=expecting_final_answer,
        compact_followup_prompt_enabled=compact_followup_prompt_enabled,
    )
    llm_input, prompt_metadata = build_llm_input_messages_with_metadata(
        history=history,
        prompt_profile=prompt_profile,
        mode=prompt_mode,
        prompt_profiles_enabled=prompt_profiles_enabled,
    )
    return (
        prompt_mode,
        inject_meal_plan_profile_context(
            llm_input=llm_input,
            prompt_profile=prompt_profile,
            preference_profile=preference_profile,
        ),
        prompt_metadata,
    )


def unpack_llm_response(response: Any) -> tuple[Any, list[dict[str, Any]], str]:
    """Extract message, tool calls and text from provider response."""
    message = extract_message(response)
    return message, extract_tool_calls(message), extract_text(message)


def estimate_usage(
    *,
    messages: list[dict[str, Any]],
    message: dict[str, Any],
) -> dict[str, int]:
    """Estimate usage details when provider did not return usage block."""
    return estimate_usage_details(messages=messages, message=message)
