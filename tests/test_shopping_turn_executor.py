"""Tests for shopping turn executor routing/metrics behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from vkuswill_bot.agents import shopping_turn_executor as executor_mod
from vkuswill_bot.agents.shopping_turn_types import TurnState


@dataclass
class _RoutingCall:
    profile: str
    executed_via_executor: bool
    shadow_mode: bool
    user_id: int | None
    trace_id: str | None
    rollout_bypass: dict[str, Any]


class _MetricsSinkSpy:
    def __init__(self) -> None:
        self.routing_calls: list[_RoutingCall] = []

    async def record_routing(
        self,
        *,
        profile: str,
        executed_via_executor: bool,
        shadow_mode: bool,
        user_id: int | None = None,
        trace_id: str | None = None,
        ground_truth_profile: str | None = None,
        label_source: str = "unlabeled",
        rollout_bypass: dict[str, Any] | None = None,
    ) -> str:
        _ = ground_truth_profile, label_source
        self.routing_calls.append(
            _RoutingCall(
                profile=profile,
                executed_via_executor=executed_via_executor,
                shadow_mode=shadow_mode,
                user_id=user_id,
                trace_id=trace_id,
                rollout_bypass=dict(rollout_bypass or {}),
            )
        )
        return trace_id or "trace"

    async def record_executor_result(
        self,
        *,
        outcome: str,
        latency_ms: float,
        user_id: int | None = None,
        trace_id: str | None = None,
        phase: str = "full_turn",
    ) -> None:
        _ = outcome, latency_ms, user_id, trace_id, phase


class _AgentStub:
    def __init__(self, metrics_sink: _MetricsSinkSpy) -> None:
        self._history: dict[int, list[dict[str, Any]]] = {}
        self._meal_plan_shadow_mode_enabled = False
        self._meal_plan_rollout_percent = 100
        self._meal_plan_rollout_controller = None
        self._meal_plan_allow_unvalidated_rollout = True
        self._meal_plan_unvalidated_rollout_reason = "staging smoke test"
        self._meal_plan_unvalidated_rollout_actor = "qa-bot"
        self._meal_plan_unvalidated_rollout_expires_at = (
            datetime.now(UTC) + timedelta(minutes=30)
        ).isoformat()
        self._meal_plan_unvalidated_rollout_max_ttl_seconds = 86400
        self._deployment_environment = "staging"
        self._meal_plan_executor_enabled = True
        self._meal_plan_metrics_sink = metrics_sink
        self._max_tool_calls = 1
        self._compact_followup_prompt_enabled = False
        self._prompt_profiles_enabled = False
        self._max_input_chars_per_turn = 100_000
        self._llm_max_tokens_recipe = None
        self._llm_routing_strategy = "single_provider"

    def _create_trace(
        self,
        *,
        user_id: int,
        text: str,
        llm_provider: str,
        prompt_profile: str | None,
    ) -> None:
        _ = user_id, text, llm_provider, prompt_profile
        return None

    async def _get_tools(self) -> list[dict[str, Any]]:
        return []

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any]:
        _ = messages, tools, llm_provider, max_tokens_override
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    @staticmethod
    def _extract_usage_details(response: Any) -> None:
        _ = response
        return None

    def _trim_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return history

    def _resolve_model_for_provider(self, llm_provider: str) -> str:
        _ = llm_provider
        return "test-model"


def _state_for_profile(profile: str, *, text: str) -> TurnState:
    return TurnState(
        history=[{"role": "user", "content": text}],
        previous_cart_products=[],
        prompt_profile=profile,  # type: ignore[arg-type]
        product_index_this_turn={},
        cart_intent=False,
        explicit_pantry_requests=set(),
        explicit_egg_pack_request=False,
        requested_ingredients=[],
        user_preferences={},
        user_preference_profile={},
    )


@pytest.mark.asyncio
async def test_run_locked_turn_does_not_duplicate_routing_event_on_internal_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _MetricsSinkSpy()
    agent = _AgentStub(metrics_sink=metrics)
    text = "собери меню на неделю для 2 человек"
    state_calls = 0

    async def _fake_build_turn_state(
        *,
        agent: Any,
        user_id: int,
        text: str,
        trace: Any | None = None,
    ) -> TurnState:
        nonlocal state_calls
        _ = agent, user_id, trace
        state_calls += 1
        if state_calls == 1:
            return _state_for_profile("meal_plan", text=text)
        return _state_for_profile("general", text=text)

    async def _fake_run_meal_plan_turn(**kwargs: Any) -> str:
        fallback_to_standard_turn = kwargs["fallback_to_standard_turn"]
        return await fallback_to_standard_turn("meal plan fallback")

    monkeypatch.setattr(executor_mod, "build_turn_state", _fake_build_turn_state)
    monkeypatch.setattr(executor_mod, "run_meal_plan_turn", _fake_run_meal_plan_turn)

    result = await executor_mod.run_locked_turn(
        agent=agent, user_id=11, text=text, on_progress=None, llm_provider="qwen_openai"
    )

    assert state_calls == 2  # initial turn + internal fallback continuation
    assert len(metrics.routing_calls) == 1
    assert metrics.routing_calls[0].profile == "meal_plan"
    assert metrics.routing_calls[0].executed_via_executor is True
    assert metrics.routing_calls[0].rollout_bypass.get("active") is True
    assert "Перехожу к стандартной обработке запроса." in result


@pytest.mark.asyncio
async def test_run_locked_turn_ignores_unvalidated_rollout_override_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _MetricsSinkSpy()
    agent = _AgentStub(metrics_sink=metrics)
    agent._deployment_environment = "production"
    text = "собери меню на неделю для 2 человек"

    async def _fake_build_turn_state(
        *,
        agent: Any,
        user_id: int,
        text: str,
        trace: Any | None = None,
    ) -> TurnState:
        _ = agent, user_id, trace
        return _state_for_profile("meal_plan", text=text)

    monkeypatch.setattr(executor_mod, "build_turn_state", _fake_build_turn_state)

    result = await executor_mod.run_locked_turn(
        agent=agent, user_id=12, text=text, on_progress=None, llm_provider="qwen_openai"
    )

    assert result
    assert len(metrics.routing_calls) == 1
    assert metrics.routing_calls[0].executed_via_executor is False
    assert metrics.routing_calls[0].rollout_bypass.get("blocked_by") == "production_environment"


@pytest.mark.asyncio
async def test_run_locked_turn_blocks_expired_non_prod_rollout_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _MetricsSinkSpy()
    agent = _AgentStub(metrics_sink=metrics)
    agent._meal_plan_unvalidated_rollout_expires_at = (
        datetime.now(UTC) - timedelta(minutes=1)
    ).isoformat()
    text = "собери меню на неделю для 2 человек"

    async def _fake_build_turn_state(
        *,
        agent: Any,
        user_id: int,
        text: str,
        trace: Any | None = None,
    ) -> TurnState:
        _ = agent, user_id, trace
        return _state_for_profile("meal_plan", text=text)

    monkeypatch.setattr(executor_mod, "build_turn_state", _fake_build_turn_state)

    result = await executor_mod.run_locked_turn(
        agent=agent, user_id=13, text=text, on_progress=None, llm_provider="qwen_openai"
    )

    assert result
    assert len(metrics.routing_calls) == 1
    assert metrics.routing_calls[0].executed_via_executor is False
    assert metrics.routing_calls[0].rollout_bypass.get("blocked_by") == "expired"
