"""Тесты для vkuswill_bot.agents.prompt_helpers."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents import prompt_helpers


def test_resolve_prompt_mode() -> None:
    assert (
        prompt_helpers.resolve_prompt_mode(
            step=1,
            expecting_final_answer=False,
            compact_followup_prompt_enabled=True,
        )
        == "start"
    )
    assert (
        prompt_helpers.resolve_prompt_mode(
            step=2,
            expecting_final_answer=False,
            compact_followup_prompt_enabled=True,
        )
        == "compact"
    )
    assert (
        prompt_helpers.resolve_prompt_mode(
            step=3,
            expecting_final_answer=True,
            compact_followup_prompt_enabled=True,
        )
        == "finalize"
    )


def test_ensure_system_prompt_inserts_and_replaces(monkeypatch: Any) -> None:
    monkeypatch.setattr(prompt_helpers, "resolve_system_prompt", lambda **_kwargs: "SYS")

    inserted = prompt_helpers.ensure_system_prompt(
        history=[{"role": "user", "content": "hi"}],
        prompt_profile="general",
        mode="start",
        prompt_profiles_enabled=False,
    )
    assert inserted[0] == {"role": "system", "content": "SYS"}

    replaced = prompt_helpers.ensure_system_prompt(
        history=[{"role": "system", "content": "old"}, {"role": "user", "content": "hi"}],
        prompt_profile="general",
        mode="start",
        prompt_profiles_enabled=False,
    )
    assert replaced[0] == {"role": "system", "content": "SYS"}


def test_build_llm_input_messages_delegates_to_ensure_system_prompt(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        prompt_helpers,
        "ensure_system_prompt",
        lambda **_kwargs: [{"role": "system", "content": "patched"}],
    )
    result = prompt_helpers.build_llm_input_messages(
        history=[{"role": "user", "content": "x"}],
        prompt_profile="general",
        mode="start",
        prompt_profiles_enabled=False,
    )
    assert result == [{"role": "system", "content": "patched"}]


def test_resolve_prompt_profile_uses_followup_when_general(monkeypatch: Any) -> None:
    monkeypatch.setattr(prompt_helpers, "detect_prompt_profile", lambda _text: "general")
    monkeypatch.setattr(prompt_helpers, "is_recipe_followup", lambda **_kwargs: True)

    result = prompt_helpers.resolve_prompt_profile(text="и еще", history=[{"role": "user"}])
    assert result == "recipe"


def test_resolve_prompt_profile_keeps_non_general(monkeypatch: Any) -> None:
    monkeypatch.setattr(prompt_helpers, "detect_prompt_profile", lambda _text: "recipe")
    monkeypatch.setattr(prompt_helpers, "is_recipe_followup", lambda **_kwargs: False)

    result = prompt_helpers.resolve_prompt_profile(text="борщ", history=None)
    assert result == "recipe"
