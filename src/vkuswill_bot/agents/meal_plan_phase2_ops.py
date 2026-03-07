"""Phase 2 runtime helpers for meal-plan executor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from vkuswill_bot.agents.meal_plan_ingredient_collection import collect_ingredients_for_dishes
from vkuswill_bot.agents.meal_plan_generator import generate_meal_plan
from vkuswill_bot.agents.meal_plan_quality import validate_hard_constraints_with_ingredients
from vkuswill_bot.agents.meal_plan_execution_helpers import (
    build_phase2_request_payload,
    recompute_selected_phase2_render_state,
    select_safe_phase2_payload,
    soft_coverage_for_render,
)
from vkuswill_bot.agents.meal_plan_runtime_policy import deadline_remaining


@dataclass(slots=True)
class Phase2SafetyOutcome:
    proceed: bool
    dishes_payload: list[dict[str, Any]]
    flat_ingredients: list[dict[str, Any]]
    soft_coverage_by_group: dict[str, float]
    request_payload: dict[str, Any]
    fallback_reason: str = ""

async def enforce_phase2_safety_policy(
    *,
    agent: Any,
    state: Any,
    user_id: int,
    llm_provider: str,
    request: Any,
    meal_plan: Any,
    dishes_payload: list[dict[str, Any]],
    flat_ingredients: list[dict[str, Any]],
    ingredients_by_dish: dict[str, list[dict[str, Any]]],
    soft_coverage_by_group: dict[str, float],
    phase2_deadline_at: float,
    recipe_ingredients_timeout_seconds: float,
    turn_deadline_at: float,
    on_progress: Any,
) -> Phase2SafetyOutcome:
    phase1_trace = list(request.applied_preferences_trace)

    hard_violations, phase2_trace = validate_hard_constraints_with_ingredients(
        request=request,
        dishes=meal_plan.dishes,
        ingredients_by_dish=ingredients_by_dish,
    )
    if not hard_violations:
        request_payload = build_phase2_request_payload(
            request=request,
            phase1_applied_trace=phase1_trace,
            phase2_applied_trace=phase2_trace,
            soft_coverage_by_group=soft_coverage_by_group,
            hard_constraints_passed=True,
        )
        return Phase2SafetyOutcome(
            proceed=True,
            dishes_payload=dishes_payload,
            flat_ingredients=flat_ingredients,
            soft_coverage_by_group=soft_coverage_by_group,
            request_payload=request_payload,
        )

    safe_dishes, safe_flat = select_safe_phase2_payload(
        dishes_payload=dishes_payload,
        ingredients_by_dish=ingredients_by_dish,
        phase2_trace=phase2_trace,
    )
    safe_coverage, safe_payload, safe_violations = recompute_selected_phase2_render_state(
        request=request,
        phase1_applied_trace=phase1_trace,
        selected_payload=safe_dishes,
        candidate_dishes=meal_plan.dishes,
        ingredients_by_dish=ingredients_by_dish,
    )
    details = "; ".join(hard_violations[:3])
    if len(safe_dishes) >= 7 and not safe_violations:
        return Phase2SafetyOutcome(
            proceed=True,
            dishes_payload=safe_dishes,
            flat_ingredients=safe_flat,
            soft_coverage_by_group=safe_coverage,
            request_payload=safe_payload,
        )

    request.operational_preferences["phase2_violation_feedback"] = details
    await on_progress("♻️ Перепланирую безопасное меню...")
    retry_plan = None
    retry_error = "Unknown phase2 retry error"
    try:
        retry_plan, retry_error = await asyncio.wait_for(
            generate_meal_plan(
                agent=agent,
                request=request,
                llm_provider=llm_provider,
            ),
            timeout=max(0.1, deadline_remaining(turn_deadline_at)),
        )
    except TimeoutError:
        retry_error = "Retry generation timed out"
    except Exception as exc:
        retry_error = str(exc)
    if retry_plan is None:
        return Phase2SafetyOutcome(
            proceed=False,
            dishes_payload=safe_dishes,
            flat_ingredients=safe_flat,
            soft_coverage_by_group=safe_coverage,
            request_payload=safe_payload,
            fallback_reason=f"Не удалось собрать безопасный план после retry: {retry_error}",
        )

    retry_dishes_payload = [dish.to_dict() for dish in retry_plan.dishes]
    retry_coverage = soft_coverage_for_render(request=request, dishes=retry_plan.dishes)
    retry_flat, retry_by_dish, _retry_stats = await collect_ingredients_for_dishes(
        agent=agent,
        request=request,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        dishes_payload=retry_dishes_payload,
        phase2_deadline_at=phase2_deadline_at,
        timeout_seconds=recipe_ingredients_timeout_seconds,
    )
    retry_violations, retry_trace = validate_hard_constraints_with_ingredients(
        request=request,
        dishes=retry_plan.dishes,
        ingredients_by_dish=retry_by_dish,
    )
    if retry_violations:
        retry_details = "; ".join(retry_violations[:3])
        retry_dishes, retry_flat_selected = select_safe_phase2_payload(
            dishes_payload=retry_dishes_payload,
            ingredients_by_dish=retry_by_dish,
            phase2_trace=retry_trace,
        )
        (
            retry_safe_coverage,
            retry_safe_payload,
            retry_safe_violations,
        ) = recompute_selected_phase2_render_state(
            request=request,
            phase1_applied_trace=phase1_trace,
            selected_payload=retry_dishes,
            candidate_dishes=retry_plan.dishes,
            ingredients_by_dish=retry_by_dish,
        )
        if len(retry_dishes) < 7 or retry_safe_violations:
            return Phase2SafetyOutcome(
                proceed=False,
                dishes_payload=retry_dishes,
                flat_ingredients=retry_flat_selected,
                soft_coverage_by_group=retry_safe_coverage,
                request_payload=retry_safe_payload,
                fallback_reason=f"Не удалось собрать безопасный план после retry: {retry_details}",
            )
        return Phase2SafetyOutcome(
            proceed=True,
            dishes_payload=retry_dishes,
            flat_ingredients=retry_flat_selected,
            soft_coverage_by_group=retry_safe_coverage,
            request_payload=retry_safe_payload,
        )

    retry_payload = build_phase2_request_payload(
        request=request,
        phase1_applied_trace=phase1_trace,
        phase2_applied_trace=retry_trace,
        soft_coverage_by_group=retry_coverage,
        hard_constraints_passed=True,
    )
    return Phase2SafetyOutcome(
        proceed=True,
        dishes_payload=retry_dishes_payload,
        flat_ingredients=retry_flat,
        soft_coverage_by_group=retry_coverage,
        request_payload=retry_payload,
    )
