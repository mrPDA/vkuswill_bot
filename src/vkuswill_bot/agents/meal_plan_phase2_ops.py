"""Phase 2 runtime helpers for meal-plan executor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from vkuswill_bot.agents.meal_plan_generator import generate_meal_plan
from vkuswill_bot.agents.meal_plan_quality import validate_hard_constraints_with_ingredients
from vkuswill_bot.agents.meal_plan_execution_helpers import (
    build_phase2_request_payload,
    recompute_selected_phase2_render_state,
    select_safe_phase2_payload,
    soft_coverage_for_render,
)
from vkuswill_bot.agents.meal_plan_runtime_ops import extract_ingredients
from vkuswill_bot.agents.meal_plan_runtime_policy import call_with_timeout_retry, deadline_remaining


class MealPlanPhase2AgentProtocol(Protocol):
    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str: ...


@dataclass(slots=True)
class Phase2SafetyOutcome:
    proceed: bool
    dishes_payload: list[dict[str, Any]]
    flat_ingredients: list[dict[str, Any]]
    soft_coverage_by_group: dict[str, float]
    request_payload: dict[str, Any]
    fallback_reason: str = ""


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _group_sizes_from_request(request: Any) -> dict[str, int]:
    groups = getattr(request, "groups", None)
    if not isinstance(groups, list):
        return {}
    sizes: dict[str, int] = {}
    for group in groups:
        group_id = str(getattr(group, "id", "")).strip()
        count = _positive_int(getattr(group, "count", None))
        if group_id and count is not None:
            sizes[group_id] = count
    return sizes


def _effective_servings_for_dish(*, dish: dict[str, Any], group_sizes: dict[str, int]) -> int:
    hint = _positive_int(dish.get("servings_total"))
    audience_raw = dish.get("audience_groups")
    audience = []
    if isinstance(audience_raw, list):
        audience = [str(group_id).strip() for group_id in audience_raw if str(group_id).strip()]
    if group_sizes and audience:
        computed = sum(
            group_sizes[group_id] for group_id in dict.fromkeys(audience) if group_id in group_sizes
        )
        if computed > 0:
            return computed
    if hint is not None:
        return hint
    return 1


async def collect_ingredients_for_dishes(
    *,
    agent: MealPlanPhase2AgentProtocol,
    request: Any,
    state: Any,
    user_id: int,
    llm_provider: str,
    dishes_payload: list[dict[str, Any]],
    phase2_deadline_at: float,
    timeout_seconds: float,
    semaphore_limit: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    semaphore = asyncio.Semaphore(max(1, semaphore_limit))
    group_sizes = _group_sizes_from_request(request)

    async def _load_ingredients(dish: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        servings = _effective_servings_for_dish(dish=dish, group_sizes=group_sizes)
        async with semaphore:
            result = await call_with_timeout_retry(
                operation=lambda: agent._call_mcp_tool(
                    name="recipe_ingredients",
                    arguments={"dish": dish["name"], "servings": servings},
                    llm_provider=llm_provider,
                    call_cache=state.mcp_call_cache,
                    user_id=user_id,
                ),
                timeout_seconds=timeout_seconds,
                hard_deadline_at=phase2_deadline_at,
            )
            return str(dish["name"]), extract_ingredients(result)

    tasks = [_load_ingredients(dish) for dish in dishes_payload]
    chunks = await asyncio.gather(*tasks, return_exceptions=True)
    flat_ingredients: list[dict[str, Any]] = []
    by_dish: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        if isinstance(chunk, Exception):
            continue
        dish_name, rows = chunk
        dish_key = str(dish_name).strip().lower()
        if dish_key:
            by_dish.setdefault(dish_key, []).extend(rows)
        flat_ingredients.extend(rows)
    return flat_ingredients, by_dish


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
    retry_flat, retry_by_dish = await collect_ingredients_for_dishes(
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
