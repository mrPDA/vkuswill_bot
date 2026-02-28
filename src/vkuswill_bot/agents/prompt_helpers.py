"""Вспомогательные функции управления промптами для ShoppingAgent."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.recipe_runtime import is_recipe_followup
from vkuswill_bot.services.prompts import (
    PromptMode,
    PromptProfile,
    detect_prompt_profile,
    get_profiled_system_prompt,
    get_system_prompt,
)


def resolve_prompt_mode(
    *,
    step: int,
    expecting_final_answer: bool,
    compact_followup_prompt_enabled: bool,
) -> PromptMode:
    """Определить режим промпта для текущего шага."""
    if expecting_final_answer:
        return "finalize"
    if step <= 1:
        return "start"
    if compact_followup_prompt_enabled:
        return "compact"
    return "start"


def resolve_system_prompt(
    *,
    prompt_profile: PromptProfile,
    mode: PromptMode,
    prompt_profiles_enabled: bool,
) -> str:
    """Получить текст system-prompt с учётом профиля и режима."""
    if not prompt_profiles_enabled:
        return get_system_prompt()
    return get_profiled_system_prompt(profile=prompt_profile, mode=mode)


def ensure_system_prompt(
    *,
    history: list[dict[str, Any]] | None,
    prompt_profile: PromptProfile,
    mode: PromptMode,
    prompt_profiles_enabled: bool,
) -> list[dict[str, Any]]:
    """Обеспечить первый system-message с нужной версией промпта."""
    prompt = resolve_system_prompt(
        prompt_profile=prompt_profile,
        mode=mode,
        prompt_profiles_enabled=prompt_profiles_enabled,
    )
    prepared = list(history) if history is not None else []
    if prepared and prepared[0].get("role") == "system":
        prepared[0] = {"role": "system", "content": prompt}
        return prepared
    return [{"role": "system", "content": prompt}, *prepared]


def build_llm_input_messages(
    *,
    history: list[dict[str, Any]],
    prompt_profile: PromptProfile,
    mode: PromptMode,
    prompt_profiles_enabled: bool,
) -> list[dict[str, Any]]:
    """Построить финальный список сообщений для LLM."""
    return ensure_system_prompt(
        history=history,
        prompt_profile=prompt_profile,
        mode=mode,
        prompt_profiles_enabled=prompt_profiles_enabled,
    )


def resolve_prompt_profile(
    *,
    text: str,
    history: list[dict[str, Any]] | None,
) -> PromptProfile:
    """Определить профиль промпта по тексту и истории."""
    profile = detect_prompt_profile(text)
    if profile != "general":
        return profile
    if is_recipe_followup(text=text, history=history):
        return "recipe"
    return profile
