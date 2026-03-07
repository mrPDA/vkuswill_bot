"""Helpers for injecting structured meal-plan preferences into LLM input."""

from __future__ import annotations

import json
from typing import Any


def _is_nonempty_profile_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_is_nonempty_profile_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_is_nonempty_profile_value(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _build_meal_plan_profile_context(profile: dict[str, Any]) -> str | None:
    if not isinstance(profile, dict) or not profile:
        return None
    if not _is_nonempty_profile_value(profile):
        return None
    payload = json.dumps(profile, ensure_ascii=False, sort_keys=True)
    return (
        "[USER_PREFERENCE_PROFILE]\n"
        "Ниже структурированный профиль предпочтений пользователя для meal-plan. "
        "Используй его как основной источник ограничений и вкусов.\n"
        f"{payload}"
    )


def inject_meal_plan_profile_context(
    *,
    llm_input: list[dict[str, Any]],
    prompt_profile: str,
    preference_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    if prompt_profile != "meal_plan":
        return llm_input
    profile_context = _build_meal_plan_profile_context(preference_profile)
    if profile_context is None:
        return llm_input
    context_message = {"role": "system", "content": profile_context}
    if llm_input and llm_input[0].get("role") == "system":
        return [llm_input[0], context_message, *llm_input[1:]]
    return [context_message, *llm_input]
