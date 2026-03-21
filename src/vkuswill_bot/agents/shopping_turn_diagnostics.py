"""Helpers for shopping turn diagnostics emitted by debug API and tracing."""

from __future__ import annotations

from typing import Any


def initialize_turn_diagnostics(*, user_id: int, state: Any) -> dict[str, Any]:
    return {
        "user_id": user_id,
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


def build_routing_trace_metadata(*, llm_provider: str, state: Any) -> dict[str, Any]:
    return {
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


def store_turn_diagnostics(*, agent: Any, user_id: int, diagnostics: dict[str, Any]) -> None:
    last_turn_diagnostics = getattr(agent, "_last_turn_diagnostics", None)
    if not isinstance(last_turn_diagnostics, dict):
        last_turn_diagnostics = {}
        agent._last_turn_diagnostics = last_turn_diagnostics
    last_turn_diagnostics[user_id] = diagnostics


def build_executor_gate_trace_metadata(
    *,
    agent: Any,
    controller: Any,
    shadow_mode: bool,
    rollout_percent: int,
    bypass_audit: dict[str, Any],
    executor_enabled: bool,
    user_in_rollout: bool,
    can_use_executor: bool,
    executor_gate_reason: str,
) -> dict[str, Any]:
    return {
        "meal_plan_shadow_mode": shadow_mode,
        "meal_plan_rollout_percent_configured": int(
            getattr(agent, "_meal_plan_rollout_percent", 100)
        ),
        "meal_plan_rollout_percent_resolved": rollout_percent,
        "meal_plan_rollout_kpi_gates_enabled": bool(
            getattr(agent, "_meal_plan_rollout_kpi_gates_enabled", True)
        ),
        "meal_plan_rollout_controller_present": controller is not None,
        "meal_plan_rollout_bypass": bypass_audit,
        "meal_plan_executor_enabled": executor_enabled,
        "meal_plan_user_in_rollout": user_in_rollout,
        "meal_plan_can_use_executor": can_use_executor,
        "meal_plan_executor_gate_reason": executor_gate_reason,
    }


def apply_executor_gate_diagnostics(
    *,
    diagnostics: dict[str, Any],
    agent: Any,
    controller: Any,
    shadow_mode: bool,
    rollout_percent: int,
    bypass_audit: dict[str, Any],
    executor_enabled: bool,
    user_in_rollout: bool,
    can_use_executor: bool,
    executor_gate_reason: str,
) -> None:
    diagnostics.update(
        build_executor_gate_trace_metadata(
            agent=agent,
            controller=controller,
            shadow_mode=shadow_mode,
            rollout_percent=rollout_percent,
            bypass_audit=bypass_audit,
            executor_enabled=executor_enabled,
            user_in_rollout=user_in_rollout,
            can_use_executor=can_use_executor,
            executor_gate_reason=executor_gate_reason,
        )
    )
