"""Dedicated meal-plan executor: generation, programmatic execution, deterministic rendering."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
import time
from typing import Any, Protocol

from vkuswill_bot.agents.meal_plan_execution_helpers import (
    aggregate_ingredients_for_search,
    elapsed_ms,
    finalize_fail_soft,
    generate_plan_with_deadline,
    request_payload_for_render,
    render_response,
    soft_coverage_for_render,
    update_history,
)
from vkuswill_bot.agents.meal_plan_phase2_ops import (
    collect_ingredients_for_dishes,
    enforce_phase2_safety_policy,
)
from vkuswill_bot.agents.meal_plan_recipe_search_ops import search_products
from vkuswill_bot.agents.meal_plan_cart_ops import maybe_create_cart_from_products
from vkuswill_bot.agents.meal_plan_runtime_policy import (
    CART_CREATE_TIMEOUT_SECONDS,
    PHASE2_DEADLINE_SECONDS,
    RECIPE_INGREDIENTS_TIMEOUT_SECONDS,
    TURN_DEADLINE_SECONDS,
    bounded_deadline,
    deadline_after,
    deadline_remaining,
)
from vkuswill_bot.agents.meal_plan_types import parse_meal_plan_request
from vkuswill_bot.services.meal_plan_trace_metadata import update_success_trace

ProgressReporter = Callable[[str], Awaitable[None]]
FallbackToStandardTurn = Callable[[str], Awaitable[str]]


class MealPlanExecutorAgentProtocol(Protocol):
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

    def _ensure_cart_price_summary(
        self,
        *,
        cart_data: dict[str, Any],
        product_index: dict[int, dict[str, Any]],
    ) -> None: ...

    def _capture_cart_snapshot(
        self,
        *,
        user_id: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None: ...


async def run_meal_plan_turn(
    *,
    agent: MealPlanExecutorAgentProtocol,
    state: Any,
    user_id: int,
    text: str,
    llm_provider: str,
    trace: Any | None,
    on_progress: ProgressReporter,
    fallback_to_standard_turn: FallbackToStandardTurn | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    """Execute meal-plan flow outside generic tool-loop."""
    started_at = time.monotonic()
    turn_deadline_at = deadline_after(TURN_DEADLINE_SECONDS)
    get_tools = getattr(agent, "_get_tools", None)
    if callable(get_tools):
        with contextlib.suppress(Exception):
            await get_tools()

    await on_progress("🧠 Планирую меню...")
    parse_span = trace.span(name="meal-plan.parse-request", input={"text": text}) if trace else None
    try:
        request = parse_meal_plan_request(text, state.user_preference_profile)
        if parse_span is not None:
            parse_span.end(
                output={
                    "days": request.days,
                    "people_total": request.people_total,
                    "groups": [group.to_prompt_dict() for group in request.groups],
                }
            )
    except Exception as exc:
        if parse_span is not None:
            parse_span.end(
                output=str(exc),
                level="ERROR",
                status_message="meal_plan_parse_failed",
            )
        parse_error = f"Не удалось разобрать meal-plan запрос: {exc}"
        if fallback_to_standard_turn is not None:
            if trace is not None:
                trace.update(
                    metadata={
                        "reason": "meal_plan_parse_failed_fallback_to_standard_turn",
                        "total_elapsed_ms": elapsed_ms(started_at),
                    },
                )
            return await fallback_to_standard_turn(parse_error)
        return finalize_fail_soft(
            agent=agent,
            state=state,
            user_id=user_id,
            trace=trace,
            request_payload={"hard_constraints_passed": False},
            dishes=[],
            cart_data=None,
            fallback_message=parse_error,
            reason="meal_plan_parse_failed",
            started_at=started_at,
        )
    request_payload = request_payload_for_render(
        request,
        hard_constraints_passed=False,
    )

    meal_plan, generation_error = await generate_plan_with_deadline(
        agent=agent,
        request=request,
        llm_provider=llm_provider,
        turn_deadline_at=turn_deadline_at,
        trace=trace,
    )
    phase1_elapsed_ms = elapsed_ms(started_at)
    if meal_plan is None:
        if fallback_to_standard_turn is not None:
            if trace is not None:
                trace.update(
                    metadata={
                        "reason": "meal_plan_generation_failed_fallback_to_standard_turn",
                        "phase1_elapsed_ms": phase1_elapsed_ms,
                        "total_elapsed_ms": elapsed_ms(started_at),
                    },
                )
            return await fallback_to_standard_turn(
                f"Не удалось сгенерировать план: {generation_error}"
            )
        return finalize_fail_soft(
            agent=agent,
            state=state,
            user_id=user_id,
            trace=trace,
            request_payload=request_payload,
            dishes=[],
            cart_data=None,
            fallback_message=f"Не удалось сгенерировать план: {generation_error}",
            reason="meal_plan_generation_failed",
            started_at=started_at,
            phase1_started_at=started_at,
        )

    if deadline_remaining(turn_deadline_at) <= 0:
        return finalize_fail_soft(
            agent=agent,
            state=state,
            user_id=user_id,
            trace=trace,
            request_payload=request_payload,
            dishes=[],
            cart_data=None,
            fallback_message="Превышен лимит времени обработки meal-plan.",
            reason="meal_plan_turn_deadline_exceeded",
            started_at=started_at,
        )

    dishes_payload = [dish.to_dict() for dish in meal_plan.dishes]
    request_payload = request_payload_for_render(
        request,
        hard_constraints_passed=False,
    )
    soft_coverage_by_group = soft_coverage_for_render(request=request, dishes=meal_plan.dishes)
    phase2_started_at = time.monotonic()
    phase2_deadline_at = bounded_deadline(
        PHASE2_DEADLINE_SECONDS,
        hard_deadline_at=turn_deadline_at,
    )

    await on_progress("🥗 Подбираю ингредиенты...")
    ingredient_span = (
        trace.span(
            name="meal-plan.collect-ingredients",
            input={"dishes_count": len(dishes_payload)},
        )
        if trace
        else None
    )
    flat_ingredients, ingredients_by_dish, ingredient_stats = await collect_ingredients_for_dishes(
        agent=agent,
        request=request,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        dishes_payload=dishes_payload,
        phase2_deadline_at=phase2_deadline_at,
        timeout_seconds=RECIPE_INGREDIENTS_TIMEOUT_SECONDS,
        trace=trace,
    )
    if ingredient_span is not None:
        ingredient_span.end(
            output={
                "flat_ingredients": len(flat_ingredients),
                "by_dish": len(ingredients_by_dish),
                "stats": ingredient_stats.as_dict(),
            },
            level="DEFAULT" if flat_ingredients else "WARNING",
            status_message=None if flat_ingredients else "meal_plan_ingredients_empty",
        )
    if isinstance(diagnostics, dict):
        diagnostics["meal_plan_ingredient_collection"] = ingredient_stats.as_dict()

    if not flat_ingredients:
        return finalize_fail_soft(
            agent=agent,
            state=state,
            user_id=user_id,
            trace=trace,
            request_payload=request_payload,
            dishes=dishes_payload,
            cart_data=None,
            fallback_message="Не удалось получить ингредиенты для плана.",
            reason="meal_plan_ingredients_empty",
            started_at=started_at,
            phase1_started_at=started_at,
            phase2_started_at=phase2_started_at,
            soft_coverage_by_group=soft_coverage_by_group,
            extra_metadata={"ingredient_collection": ingredient_stats.as_dict()},
        )

    phase2_safety_span = (
        trace.span(
            name="meal-plan.phase2-safety",
            input={
                "dishes_count": len(dishes_payload),
                "flat_ingredients": len(flat_ingredients),
            },
        )
        if trace
        else None
    )
    phase2_safety = await enforce_phase2_safety_policy(
        agent=agent,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        request=request,
        meal_plan=meal_plan,
        dishes_payload=dishes_payload,
        flat_ingredients=flat_ingredients,
        ingredients_by_dish=ingredients_by_dish,
        soft_coverage_by_group=soft_coverage_by_group,
        phase2_deadline_at=phase2_deadline_at,
        recipe_ingredients_timeout_seconds=RECIPE_INGREDIENTS_TIMEOUT_SECONDS,
        turn_deadline_at=turn_deadline_at,
        on_progress=on_progress,
        trace=trace,
    )
    if phase2_safety_span is not None:
        phase2_safety_span.end(
            output={
                "proceed": phase2_safety.proceed,
                "dishes_count": len(phase2_safety.dishes_payload),
                "flat_ingredients": len(phase2_safety.flat_ingredients),
                "fallback_reason": phase2_safety.fallback_reason,
            },
            level="DEFAULT" if phase2_safety.proceed else "WARNING",
            status_message=None if phase2_safety.proceed else "meal_plan_phase2_safety_fallback",
        )
    if not phase2_safety.proceed:
        if fallback_to_standard_turn is not None:
            return await fallback_to_standard_turn(phase2_safety.fallback_reason)
        return finalize_fail_soft(
            agent=agent,
            state=state,
            user_id=user_id,
            trace=trace,
            request_payload=phase2_safety.request_payload,
            dishes=phase2_safety.dishes_payload,
            cart_data=None,
            fallback_message=phase2_safety.fallback_reason,
            reason="meal_plan_hard_constraints_violated_after_retry",
            started_at=started_at,
            phase1_started_at=started_at,
            phase2_started_at=phase2_started_at,
            soft_coverage_by_group=phase2_safety.soft_coverage_by_group,
        )
    dishes_payload = phase2_safety.dishes_payload
    flat_ingredients = phase2_safety.flat_ingredients
    soft_coverage_by_group = phase2_safety.soft_coverage_by_group
    request_payload = phase2_safety.request_payload

    aggregated = aggregate_ingredients_for_search(flat_ingredients)
    await on_progress("🔍 Ищу товары...")
    search_span = (
        trace.span(
            name="meal-plan.search-products",
            input={"aggregated_ingredients_count": len(aggregated)},
        )
        if trace
        else None
    )
    products, not_found, used_chunk_fallback, search_stats = await search_products(
        agent=agent,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        aggregated_ingredients=aggregated,
        phase2_deadline_at=phase2_deadline_at,
    )
    if search_span is not None:
        search_span.end(
            output={
                "products_count": len(products),
                "not_found_count": len(not_found),
                "used_chunk_fallback": used_chunk_fallback,
                "stats": search_stats.as_dict(),
            },
            level="DEFAULT" if products else "WARNING",
            status_message=None if products else "meal_plan_search_empty_products",
        )
    if isinstance(diagnostics, dict):
        diagnostics["meal_plan_recipe_search"] = search_stats.as_dict()

    cart_data: dict[str, Any] | None = None
    if products:
        await on_progress("🛒 Формирую корзину...")
    cart_span = (
        trace.span(
            name="meal-plan.create-cart",
            input={"products_count": len(products), "not_found_count": len(not_found)},
        )
        if trace
        else None
    )
    cart_data, cart_stats = await maybe_create_cart_from_products(
        agent=agent,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        products=products,
        not_found=not_found,
        phase2_deadline_at=phase2_deadline_at,
        timeout_seconds=CART_CREATE_TIMEOUT_SECONDS,
    )
    if cart_span is not None:
        cart_span.end(
            output={
                "cart_created": cart_stats.cart_created,
                "has_link": cart_stats.has_link,
                "returned_products_count": cart_stats.returned_products_count,
                "failed_before_response": cart_stats.failed_before_response,
            },
            level="DEFAULT" if cart_stats.cart_created else "WARNING",
            status_message=None if cart_stats.cart_created else "meal_plan_cart_not_created",
        )
    if isinstance(diagnostics, dict):
        diagnostics["meal_plan_cart_create"] = cart_stats.as_dict()

    final_text = render_response(
        state=state,
        request_payload=request_payload,
        dishes=dishes_payload,
        cart_data=cart_data,
        soft_coverage_by_group=soft_coverage_by_group,
    )
    update_history(agent, state, user_id, final_text)
    update_success_trace(
        trace=trace,
        output=final_text,
        dishes_payload=dishes_payload,
        aggregated_ingredients=aggregated,
        products=products,
        soft_coverage_by_group=soft_coverage_by_group,
        request=request,
        request_payload=request_payload,
        phase1_elapsed_ms=phase1_elapsed_ms,
        phase2_started_at=phase2_started_at,
        started_at=started_at,
        used_chunk_fallback=used_chunk_fallback,
        phase2_deadline_seconds=PHASE2_DEADLINE_SECONDS,
        turn_deadline_seconds=TURN_DEADLINE_SECONDS,
        search_stats=search_stats.as_dict(),
        cart_stats=cart_stats.as_dict(),
    )
    return final_text
