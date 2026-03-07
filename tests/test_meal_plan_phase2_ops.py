"""Unit tests for meal-plan Phase 2 safety recalculation."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from vkuswill_bot.agents.meal_plan_execution_helpers import soft_coverage_for_render
from vkuswill_bot.agents.meal_plan_phase2_ops import (
    collect_ingredients_for_dishes,
    enforce_phase2_safety_policy,
)
from vkuswill_bot.agents.meal_plan_cart_ops import maybe_create_cart_from_products
from vkuswill_bot.agents.meal_plan_types import MealPlan, MealPlanDish, parse_meal_plan_request


@dataclass
class _State:
    mcp_call_cache: dict[str, str] = field(default_factory=dict)
    product_index_this_turn: dict[int, dict[str, Any]] = field(default_factory=dict)


def _build_plan(*, total_dishes: int, violating_index: int | None) -> MealPlan:
    dishes: list[MealPlanDish] = []
    for idx in range(1, total_dishes + 1):
        dishes.append(
            MealPlanDish(
                name=f"Блюдо {idx}",
                day=min(idx, 7),
                meal_type="lunch",
                servings_total=2,
                audience_groups=["adults"],
                cuisine_tags=["russian" if violating_index == idx else "italian"],
            )
        )
    return MealPlan(schema_version=1, dishes=dishes)


def _ingredients_for_plan(
    *,
    meal_plan: MealPlan,
    violating_index: int | None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    flat: list[dict[str, Any]] = []
    by_dish: dict[str, list[dict[str, Any]]] = {}
    for idx, dish in enumerate(meal_plan.dishes, start=1):
        rows = (
            [{"name": "куриное филе", "search_query": "куриное филе"}]
            if violating_index == idx
            else [{"name": "помидор", "search_query": "помидор"}]
        )
        key = dish.name.strip().lower()
        by_dish[key] = rows
        flat.extend(rows)
    return flat, by_dish


def _has_hard_not_applied(payload: dict[str, Any]) -> bool:
    trace = payload.get("applied_preferences_trace")
    if not isinstance(trace, list):
        return False
    return any(
        isinstance(row, dict)
        and str(row.get("field", "")).startswith("hard_constraints.")
        and row.get("applied") is False
        for row in trace
    )


@pytest.mark.asyncio
async def test_phase2_recomputes_safety_payload_after_filter_without_retry() -> None:
    request = parse_meal_plan_request(
        "меню на неделю для 2 человек, веганское, итальянская кухня",
        {},
    )
    meal_plan = _build_plan(total_dishes=8, violating_index=8)
    dishes_payload = [dish.to_dict() for dish in meal_plan.dishes]
    flat_ingredients, by_dish = _ingredients_for_plan(meal_plan=meal_plan, violating_index=8)
    initial_coverage = soft_coverage_for_render(request=request, dishes=meal_plan.dishes)

    outcome = await enforce_phase2_safety_policy(
        agent=object(),
        state=_State(),
        user_id=1,
        llm_provider="qwen_openai",
        request=request,
        meal_plan=meal_plan,
        dishes_payload=dishes_payload,
        flat_ingredients=flat_ingredients,
        ingredients_by_dish=by_dish,
        soft_coverage_by_group=initial_coverage,
        phase2_deadline_at=time.monotonic() + 30,
        recipe_ingredients_timeout_seconds=1.0,
        turn_deadline_at=time.monotonic() + 30,
        on_progress=lambda _msg: _done(),
    )

    assert outcome.proceed is True
    assert len(outcome.dishes_payload) == 7
    assert outcome.soft_coverage_by_group["adults"] == pytest.approx(1.0)
    assert _has_hard_not_applied(outcome.request_payload) is False
    assert outcome.request_payload["applied_preferences_summary"]["not_applied"] == 0


@pytest.mark.asyncio
async def test_phase2_recomputes_safety_payload_after_retry_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = parse_meal_plan_request(
        "меню на неделю для 2 человек, веганское, итальянская кухня",
        {},
    )
    initial_plan = _build_plan(total_dishes=7, violating_index=7)
    initial_dishes_payload = [dish.to_dict() for dish in initial_plan.dishes]
    initial_flat, initial_by_dish = _ingredients_for_plan(meal_plan=initial_plan, violating_index=7)
    initial_coverage = soft_coverage_for_render(request=request, dishes=initial_plan.dishes)

    retry_plan = _build_plan(total_dishes=8, violating_index=8)
    retry_flat, retry_by_dish = _ingredients_for_plan(meal_plan=retry_plan, violating_index=8)
    progress: list[str] = []

    async def _fake_generate_meal_plan(
        *,
        agent: Any,
        request: Any,
        llm_provider: str,
    ) -> tuple[MealPlan, str]:
        return retry_plan, ""

    async def _fake_collect_ingredients_for_dishes(
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        return retry_flat, retry_by_dish

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_phase2_ops.generate_meal_plan",
        _fake_generate_meal_plan,
    )
    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_phase2_ops.collect_ingredients_for_dishes",
        _fake_collect_ingredients_for_dishes,
    )

    async def _on_progress(message: str) -> None:
        progress.append(message)

    outcome = await enforce_phase2_safety_policy(
        agent=object(),
        state=_State(),
        user_id=2,
        llm_provider="qwen_openai",
        request=request,
        meal_plan=initial_plan,
        dishes_payload=initial_dishes_payload,
        flat_ingredients=initial_flat,
        ingredients_by_dish=initial_by_dish,
        soft_coverage_by_group=initial_coverage,
        phase2_deadline_at=time.monotonic() + 30,
        recipe_ingredients_timeout_seconds=1.0,
        turn_deadline_at=time.monotonic() + 30,
        on_progress=_on_progress,
    )

    assert outcome.proceed is True
    assert len(outcome.dishes_payload) == 7
    assert outcome.soft_coverage_by_group["adults"] == pytest.approx(1.0)
    assert _has_hard_not_applied(outcome.request_payload) is False
    assert progress == ["♻️ Перепланирую безопасное меню..."]


@pytest.mark.asyncio
async def test_phase2_retry_failure_returns_recomputed_safe_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = parse_meal_plan_request(
        "меню на неделю для 2 человек, веганское, итальянская кухня",
        {},
    )
    initial_plan = _build_plan(total_dishes=7, violating_index=7)
    initial_dishes_payload = [dish.to_dict() for dish in initial_plan.dishes]
    initial_flat, initial_by_dish = _ingredients_for_plan(meal_plan=initial_plan, violating_index=7)
    initial_coverage = soft_coverage_for_render(request=request, dishes=initial_plan.dishes)

    async def _fake_generate_meal_plan(
        *,
        agent: Any,
        request: Any,
        llm_provider: str,
    ) -> tuple[None, str]:
        return None, "retry failed"

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_phase2_ops.generate_meal_plan",
        _fake_generate_meal_plan,
    )

    outcome = await enforce_phase2_safety_policy(
        agent=object(),
        state=_State(),
        user_id=3,
        llm_provider="qwen_openai",
        request=request,
        meal_plan=initial_plan,
        dishes_payload=initial_dishes_payload,
        flat_ingredients=initial_flat,
        ingredients_by_dish=initial_by_dish,
        soft_coverage_by_group=initial_coverage,
        phase2_deadline_at=time.monotonic() + 30,
        recipe_ingredients_timeout_seconds=1.0,
        turn_deadline_at=time.monotonic() + 30,
        on_progress=lambda _msg: _done(),
    )

    assert outcome.proceed is False
    assert len(outcome.dishes_payload) == 6
    assert outcome.soft_coverage_by_group["adults"] == pytest.approx(1.0)
    assert _has_hard_not_applied(outcome.request_payload) is False
    assert "Не удалось собрать безопасный план после retry" in outcome.fallback_reason


@pytest.mark.asyncio
async def test_maybe_create_cart_from_products_retries_after_timeout() -> None:
    class _Agent:
        def __init__(self) -> None:
            self.calls = 0
            self.snapshots = 0

        async def _call_mcp_tool(
            self,
            *,
            name: str,
            arguments: dict[str, Any],
            llm_provider: str,
            call_cache: dict[str, str] | None = None,
            user_id: int | None = None,
        ) -> str:
            assert name == "vkusvill_cart_link_create"
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.2)
                return "{}"
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/retry-ok"}},
                ensure_ascii=False,
            )

        def _ensure_cart_price_summary(
            self,
            *,
            cart_data: dict[str, Any],
            product_index: dict[int, dict[str, Any]],
        ) -> None:
            if "price_summary" not in cart_data:
                cart_data["price_summary"] = {"count": len(cart_data.get("products", []))}

        def _capture_cart_snapshot(
            self,
            *,
            user_id: int,
            tool_name: str,
            args: dict[str, Any],
            result: str,
        ) -> None:
            self.snapshots += 1

    agent = _Agent()
    cart_data = await maybe_create_cart_from_products(
        agent=agent,
        state=_State(),
        user_id=77,
        llm_provider="qwen_openai",
        products=[{"xml_id": 11, "q": 1}],
        not_found=[],
        phase2_deadline_at=time.monotonic() + 1.0,
        timeout_seconds=0.01,
    )

    assert cart_data is not None
    assert cart_data["link"] == "https://shop.example/cart/retry-ok"
    assert agent.calls == 2
    assert agent.snapshots == 1


@pytest.mark.asyncio
async def test_collect_ingredients_for_dishes_respects_default_semaphore_limit() -> None:
    class _Agent:
        def __init__(self) -> None:
            self.inflight = 0
            self.max_inflight = 0

        async def _call_mcp_tool(
            self,
            *,
            name: str,
            arguments: dict[str, Any],
            llm_provider: str,
            call_cache: dict[str, str] | None = None,
            user_id: int | None = None,
        ) -> str:
            assert name == "recipe_ingredients"
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            try:
                await asyncio.sleep(0.02)
                dish = str(arguments.get("dish", "dish"))
                return json.dumps(
                    {
                        "ok": True,
                        "ingredients": [
                            {"name": dish, "search_query": dish, "quantity": 1, "unit": "шт"}
                        ],
                    },
                    ensure_ascii=False,
                )
            finally:
                self.inflight -= 1

    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    dishes_payload = [{"name": f"Блюдо {idx}", "servings_total": 2} for idx in range(1, 9)]
    agent = _Agent()
    flat, by_dish = await collect_ingredients_for_dishes(
        agent=agent,
        request=request,
        state=_State(),
        user_id=10,
        llm_provider="qwen_openai",
        dishes_payload=dishes_payload,
        phase2_deadline_at=time.monotonic() + 5.0,
        timeout_seconds=1.0,
    )

    assert agent.max_inflight <= 6
    assert len(flat) == 8
    assert len(by_dish) == 8


@pytest.mark.asyncio
async def test_collect_ingredients_for_dishes_returns_partial_success_when_one_call_fails() -> None:
    class _Agent:
        async def _call_mcp_tool(
            self,
            *,
            name: str,
            arguments: dict[str, Any],
            llm_provider: str,
            call_cache: dict[str, str] | None = None,
            user_id: int | None = None,
        ) -> str:
            assert name == "recipe_ingredients"
            dish = str(arguments.get("dish", ""))
            if dish == "Блюдо 3":
                raise RuntimeError("backend unavailable")
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {"name": dish, "search_query": dish, "quantity": 1, "unit": "шт"}
                    ],
                },
                ensure_ascii=False,
            )

    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    dishes_payload = [{"name": f"Блюдо {idx}", "servings_total": 2} for idx in range(1, 6)]
    flat, by_dish = await collect_ingredients_for_dishes(
        agent=_Agent(),
        request=request,
        state=_State(),
        user_id=11,
        llm_provider="qwen_openai",
        dishes_payload=dishes_payload,
        phase2_deadline_at=time.monotonic() + 5.0,
        timeout_seconds=1.0,
    )

    assert len(flat) == 4
    assert "блюдо 3" not in by_dish
    assert len(by_dish) == 4


@pytest.mark.asyncio
async def test_collect_ingredients_for_dishes_computes_servings_from_request_groups() -> None:
    captured_servings: dict[str, int] = {}

    class _Agent:
        async def _call_mcp_tool(
            self,
            *,
            name: str,
            arguments: dict[str, Any],
            llm_provider: str,
            call_cache: dict[str, str] | None = None,
            user_id: int | None = None,
        ) -> str:
            assert name == "recipe_ingredients"
            dish = str(arguments.get("dish", ""))
            captured_servings[dish] = int(arguments.get("servings", 0))
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {"name": dish, "search_query": dish, "quantity": 1, "unit": "шт"}
                    ],
                },
                ensure_ascii=False,
            )

    request = parse_meal_plan_request("меню на неделю для 4 человек, один ребенок 2 года", {})
    dishes_payload = [
        {
            "name": "Только для взрослых",
            "servings_total": 1,
            "audience_groups": ["adults"],
        },
        {
            "name": "Только для ребенка",
            "servings_total": 9,
            "audience_groups": ["child_2y"],
        },
        {
            "name": "Для всех",
            "servings_total": 1,
            "audience_groups": ["adults", "child_2y"],
        },
    ]
    flat, by_dish = await collect_ingredients_for_dishes(
        agent=_Agent(),
        request=request,
        state=_State(),
        user_id=12,
        llm_provider="qwen_openai",
        dishes_payload=dishes_payload,
        phase2_deadline_at=time.monotonic() + 5.0,
        timeout_seconds=1.0,
    )

    assert len(flat) == 3
    assert len(by_dish) == 3
    assert captured_servings == {
        "Только для взрослых": 3,
        "Только для ребенка": 1,
        "Для всех": 4,
    }


async def _done() -> None:
    return None
