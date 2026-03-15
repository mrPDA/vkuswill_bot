"""Tests for turn-state input normalization."""

from __future__ import annotations

from typing import Any

import pytest

from vkuswill_bot.agents.shopping_turn_types import build_turn_state


class _AgentStub:
    def __init__(self) -> None:
        self._history: dict[int, list[dict[str, Any]]] = {}
        self._last_cart_snapshot: dict[int, dict[str, Any]] = {}
        self._last_trace_id: dict[int, str] = {}
        self._prompt_profiles_enabled = False
        self._compact_followup_prompt_enabled = False
        self._max_tool_calls = 1
        self._max_input_chars_per_turn = 10_000
        self._llm_routing_strategy = "single_provider"
        self._meal_plan_intent_routing_enabled = True
        self.classify_inputs: list[str] = []
        self.fresh_context_inputs: list[str] = []

    def _should_start_fresh_context(
        self,
        *,
        text: str,
        history: list[dict[str, Any]] | None,
    ) -> bool:
        _ = history
        self.fresh_context_inputs.append(text)
        return False

    def _normalize_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return history

    async def _classify_intent(
        self,
        text: str,
        *,
        trace: Any | None = None,
    ) -> None:
        _ = trace
        self.classify_inputs.append(text)
        return None

    async def _load_user_preferences(self, user_id: int) -> dict[str, str]:
        _ = user_id
        return {}


@pytest.mark.asyncio
async def test_build_turn_state_normalizes_multilingual_product_lists() -> None:
    agent = _AgentStub()

    state = await build_turn_state(
        agent=agent,
        user_id=101,
        text="I need milk, bread, eggs and cheese please",
    )

    assert agent.fresh_context_inputs == ["молоко, хлеб, яйца и сыр"]
    assert agent.classify_inputs == ["молоко, хлеб, яйца и сыр"]
    assert state.history[-1]["content"] == "молоко, хлеб, яйца и сыр"
