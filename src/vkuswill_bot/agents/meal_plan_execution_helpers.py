"""Shared runtime helpers for meal-plan executor orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Protocol

from vkuswill_bot.agents.meal_plan_generator import generate_meal_plan
from vkuswill_bot.agents.meal_plan_quality import (
    build_applied_preferences_trace,
    validate_hard_constraints_with_ingredients,
)
from vkuswill_bot.agents.meal_plan_response_contract import render_meal_plan_contract_response
from vkuswill_bot.agents.meal_plan_runtime_ops import (
    aggregate_ingredients,
    extract_products_from_recipe_search,
    merge_products,
    request_payload_for_renderer as _request_payload_for_renderer,
    soft_coverage_for_renderer as _soft_coverage_for_renderer,
)
from vkuswill_bot.agents.meal_plan_runtime_policy import (
    RECIPE_SEARCH_TIMEOUT_SECONDS,
    call_with_timeout_retry,
    deadline_remaining,
)


class MealPlanHelperAgentProtocol(Protocol):
    _history: dict[int, list[dict[str, Any]]]

    def _trim_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str: ...


def update_history(
    agent: MealPlanHelperAgentProtocol,
    state: Any,
    user_id: int,
    final_text: str,
) -> None:
    agent._history[user_id] = agent._trim_history(
        [*state.history, {"role": "assistant", "content": final_text}]
    )


def render_response(
    *,
    state: Any,
    request_payload: dict[str, Any],
    dishes: list[dict[str, Any]],
    cart_data: dict[str, Any] | None,
    fallback_message: str = "",
    soft_coverage_by_group: dict[str, float] | None = None,
) -> str:
    return render_meal_plan_contract_response(
        history=state.history,
        cart_data=cart_data,
        user_preference_profile=state.user_preference_profile,
        fallback_message=fallback_message,
        request_payload=request_payload,
        structured_dishes=dishes,
        soft_coverage_by_group=soft_coverage_by_group,
    )


def request_payload_for_render(
    request: Any,
    *,
    hard_constraints_passed: bool | None = None,
) -> dict[str, Any]:
    """Thin wrapper to keep executor dependencies compact."""
    return _request_payload_for_renderer(
        request,
        hard_constraints_passed=hard_constraints_passed,
    )


async def generate_plan_with_deadline(
    *,
    agent: Any,
    request: Any,
    llm_provider: str,
    turn_deadline_at: float,
) -> tuple[Any | None, str]:
    try:
        plan, _ = await asyncio.wait_for(
            generate_meal_plan(agent=agent, request=request, llm_provider=llm_provider),
            timeout=max(0.1, deadline_remaining(turn_deadline_at)),
        )
    except TimeoutError:
        return None, "Превышен turn deadline"
    return plan, ""


def build_phase2_request_payload(
    *,
    request: Any,
    phase1_applied_trace: list[dict[str, Any]],
    phase2_applied_trace: list[dict[str, Any]],
    soft_coverage_by_group: dict[str, float],
    hard_constraints_passed: bool,
) -> dict[str, Any]:
    request.applied_preferences_trace = build_applied_preferences_trace(
        request=request,
        phase1_applied_trace=phase1_applied_trace,
        phase2_applied_trace=phase2_applied_trace,
        soft_coverage_by_group=soft_coverage_by_group,
    )
    return request_payload_for_render(
        request,
        hard_constraints_passed=hard_constraints_passed,
    )


def soft_coverage_for_render(*, request: Any, dishes: list[Any]) -> dict[str, float]:
    """Thin wrapper to keep executor dependencies compact."""
    return _soft_coverage_for_renderer(request=request, dishes=dishes)


def select_safe_phase2_payload(
    *,
    dishes_payload: list[dict[str, Any]],
    ingredients_by_dish: dict[str, list[dict[str, Any]]],
    phase2_trace: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    violating_dish_keys = {
        str(row.get("dish", "")).strip().lower()
        for row in phase2_trace
        if (
            isinstance(row, dict)
            and row.get("applied") is False
            and str(row.get("dish", "")).strip()
        )
    }
    safe_dishes_payload = [
        dish
        for dish in dishes_payload
        if str(dish.get("name", "")).strip().lower() not in violating_dish_keys
    ]
    safe_flat_ingredients = [
        row
        for dish in safe_dishes_payload
        for row in ingredients_by_dish.get(str(dish.get("name", "")).strip().lower(), [])
    ]
    return safe_dishes_payload, safe_flat_ingredients


def recompute_selected_phase2_render_state(
    *,
    request: Any,
    phase1_applied_trace: list[dict[str, Any]],
    selected_payload: list[dict[str, Any]],
    candidate_dishes: list[Any],
    ingredients_by_dish: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, float], dict[str, Any], list[str]]:
    # Rebuild coverage and applied-trace strictly for the final safe dishes.
    selected_keys = {
        str(dish.get("name", "")).strip().lower()
        for dish in selected_payload
        if str(dish.get("name", "")).strip()
    }
    selected_dishes = [
        dish
        for dish in candidate_dishes
        if str(getattr(dish, "name", "")).strip().lower() in selected_keys
    ]
    coverage = soft_coverage_for_render(request=request, dishes=selected_dishes)
    selected_violations, selected_trace = validate_hard_constraints_with_ingredients(
        request=request,
        dishes=selected_dishes,
        ingredients_by_dish=ingredients_by_dish,
    )
    payload = build_phase2_request_payload(
        request=request,
        phase1_applied_trace=phase1_applied_trace,
        phase2_applied_trace=selected_trace,
        soft_coverage_by_group=coverage,
        hard_constraints_passed=not selected_violations,
    )
    return coverage, payload, selected_violations


def aggregate_ingredients_for_search(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Thin wrapper to keep executor dependencies compact."""
    return aggregate_ingredients(items)


def elapsed_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def finalize_fail_soft(
    *,
    agent: MealPlanHelperAgentProtocol,
    state: Any,
    user_id: int,
    trace: Any | None,
    request_payload: dict[str, Any],
    dishes: list[dict[str, Any]],
    cart_data: dict[str, Any] | None,
    fallback_message: str,
    reason: str,
    started_at: float,
    phase1_started_at: float | None = None,
    phase2_started_at: float | None = None,
    soft_coverage_by_group: dict[str, float] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    final_text = render_response(
        state=state,
        request_payload=request_payload,
        dishes=dishes,
        cart_data=cart_data,
        fallback_message=fallback_message,
        soft_coverage_by_group=soft_coverage_by_group,
    )
    update_history(agent, state, user_id, final_text)
    if trace is not None:
        metadata: dict[str, Any] = {"reason": reason, "total_elapsed_ms": elapsed_ms(started_at)}
        if phase1_started_at is not None:
            metadata["phase1_elapsed_ms"] = elapsed_ms(phase1_started_at)
        if phase2_started_at is not None:
            metadata["phase2_elapsed_ms"] = elapsed_ms(phase2_started_at)
        if isinstance(extra_metadata, dict):
            metadata.update(extra_metadata)
        trace.update(output=final_text, metadata=metadata)
    return final_text


async def search_products(
    *,
    agent: MealPlanHelperAgentProtocol,
    state: Any,
    user_id: int,
    llm_provider: str,
    aggregated_ingredients: list[dict[str, Any]],
    phase2_deadline_at: float,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    async def _call_recipe_search(ingredients: list[dict[str, Any]]) -> str:
        return await call_with_timeout_retry(
            operation=lambda: agent._call_mcp_tool(
                name="recipe_search",
                arguments={"ingredients": ingredients},
                llm_provider=llm_provider,
                call_cache=state.mcp_call_cache,
                user_id=user_id,
            ),
            timeout_seconds=RECIPE_SEARCH_TIMEOUT_SECONDS,
            hard_deadline_at=phase2_deadline_at,
        )

    used_chunk_fallback = False
    try:
        primary = await _call_recipe_search(aggregated_ingredients)
        products, not_found = extract_products_from_recipe_search(primary)
    except Exception:
        products, not_found = [], []

    if products or len(aggregated_ingredients) <= 12:
        return merge_products(products), not_found, used_chunk_fallback

    used_chunk_fallback = True
    merged_products: list[dict[str, Any]] = []
    merged_not_found: list[str] = []
    for start in range(0, len(aggregated_ingredients), 12):
        chunk = aggregated_ingredients[start : start + 12]
        chunk_result = None
        with contextlib.suppress(Exception):
            chunk_result = await _call_recipe_search(chunk)
        if chunk_result is None:
            continue
        chunk_products, chunk_not_found = extract_products_from_recipe_search(chunk_result)
        merged_products.extend(chunk_products)
        for item in chunk_not_found:
            if item not in merged_not_found:
                merged_not_found.append(item)
    return merge_products(merged_products), merged_not_found, used_chunk_fallback
