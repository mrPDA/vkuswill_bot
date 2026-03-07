"""Вспомогательные функции управления промптами для ShoppingAgent."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.recipe_runtime import is_recipe_followup
from vkuswill_bot.services.prompts import (
    PromptMode,
    PromptProfile,
    detect_prompt_profile,
    get_profiled_system_prompt_with_metadata,
    get_system_prompt_with_metadata,
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
    return resolve_system_prompt_bundle(
        prompt_profile=prompt_profile,
        mode=mode,
        prompt_profiles_enabled=prompt_profiles_enabled,
    )[0]


def resolve_system_prompt_bundle(
    *,
    prompt_profile: PromptProfile,
    mode: PromptMode,
    prompt_profiles_enabled: bool,
) -> tuple[str, dict[str, Any]]:
    """Получить system-prompt и provenance metadata для tracing."""
    if not prompt_profiles_enabled:
        text, metadata = get_system_prompt_with_metadata()
        return text, {
            "strategy": "full_system_prompt",
            "components": [metadata],
            "sha256": metadata.get("sha256"),
        }
    return get_profiled_system_prompt_with_metadata(profile=prompt_profile, mode=mode)


def ensure_system_prompt(
    *,
    history: list[dict[str, Any]] | None,
    prompt_profile: PromptProfile,
    mode: PromptMode,
    prompt_profiles_enabled: bool,
) -> list[dict[str, Any]]:
    """Обеспечить первый system-message с нужной версией промпта."""
    return ensure_system_prompt_with_metadata(
        prompt_profile=prompt_profile,
        history=history,
        mode=mode,
        prompt_profiles_enabled=prompt_profiles_enabled,
    )[0]


def ensure_system_prompt_with_metadata(
    *,
    history: list[dict[str, Any]] | None,
    prompt_profile: PromptProfile,
    mode: PromptMode,
    prompt_profiles_enabled: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Обеспечить первый system-message и вернуть metadata по prompt source."""
    prompt, metadata = resolve_system_prompt_bundle(
        prompt_profile=prompt_profile,
        mode=mode,
        prompt_profiles_enabled=prompt_profiles_enabled,
    )
    prepared = list(history) if history is not None else []
    if prepared and prepared[0].get("role") == "system":
        prepared[0] = {"role": "system", "content": prompt}
        return prepared, metadata
    return [{"role": "system", "content": prompt}, *prepared], metadata


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


def build_llm_input_messages_with_metadata(
    *,
    history: list[dict[str, Any]],
    prompt_profile: PromptProfile,
    mode: PromptMode,
    prompt_profiles_enabled: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Построить список сообщений для LLM и metadata по system prompt."""
    return ensure_system_prompt_with_metadata(
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
