"""Tests for dedicated meal-plan executor runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from vkuswill_bot.agents.meal_plan_executor import run_meal_plan_turn


@dataclass
class _State:
    history: list[dict[str, Any]]
    user_preference_profile: dict[str, Any] = field(default_factory=dict)
    mcp_call_cache: dict[str, str] = field(default_factory=dict)
    product_index_this_turn: dict[int, dict[str, Any]] = field(default_factory=dict)
    explicit_pantry_requests: set[str] = field(default_factory=set)


class _TraceSpy:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.generations: list[dict[str, Any]] = []
        self.spans: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)

    def generation(self, **kwargs: Any) -> Any:
        payload = {"start": kwargs, "end": None}
        self.generations.append(payload)

        class _Generation:
            def end(self_inner, **end_kwargs: Any) -> None:
                payload["end"] = end_kwargs

        return _Generation()

    def span(self, **kwargs: Any) -> Any:
        payload = {"start": kwargs, "end": None}
        self.spans.append(payload)

        class _Span:
            def end(self_inner, **end_kwargs: Any) -> None:
                payload["end"] = end_kwargs

        return _Span()


class _FakeExecutorAgent:
    def __init__(
        self,
        *,
        llm_responses: list[dict[str, Any]],
        mcp_handler: Any,
    ) -> None:
        self._llm_max_tokens_recipe = 1200
        self._llm_responses = llm_responses
        self._mcp_handler = mcp_handler
        self._history: dict[int, list[dict[str, Any]]] = {}
        self.llm_calls: list[dict[str, Any]] = []
        self.mcp_calls: list[tuple[str, dict[str, Any]]] = []
        self.snapshot_calls: list[dict[str, Any]] = []
        self.price_summary_calls: int = 0

    def _trim_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return history[-20:]

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any]:
        self.llm_calls.append(
            {
                "messages": messages,
                "tools": tools,
                "provider": llm_provider,
                "max_tokens_override": max_tokens_override,
            }
        )
        if not self._llm_responses:
            raise RuntimeError("No scripted LLM response")
        return self._llm_responses.pop(0)

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str:
        self.mcp_calls.append((name, arguments))
        result = self._mcp_handler(name, arguments)
        if inspect.isawaitable(result):
            return await result
        return result

    def _ensure_cart_price_summary(
        self,
        *,
        cart_data: dict[str, Any],
        product_index: dict[int, dict[str, Any]],
    ) -> None:
        self.price_summary_calls += 1
        if "price_summary" not in cart_data:
            products = cart_data.get("products")
            count = len(products) if isinstance(products, list) else 0
            cart_data["price_summary"] = {
                "count": count,
                "total_text": "Итого: н/д",
                "items": [],
            }

    def _capture_cart_snapshot(
        self,
        *,
        user_id: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None:
        self.snapshot_calls.append(
            {
                "user_id": user_id,
                "tool_name": tool_name,
                "args": args,
                "result": result,
            }
        )


def _llm_response(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def _products_search_response(
    query: str,
    *,
    xml_id: int = 101,
    name: str | None = None,
) -> str:
    product_name = name or f"Товар для {query}"
    return json.dumps(
        {
            "ok": True,
            "items": [
                {
                    "xml_id": xml_id,
                    "name": product_name,
                    "price": 100,
                    "unit": "шт",
                }
            ],
        },
        ensure_ascii=False,
    )


def _build_plan_payload(*, cuisine: str = "italian") -> dict[str, Any]:
    meal_types = ["breakfast", "lunch", "dinner", "breakfast", "lunch", "dinner", "lunch"]
    dishes = [
        {
            "name": f"Блюдо {idx}",
            "day": idx,
            "meal_type": meal_types[idx - 1],
            "servings_total": 2,
            "audience_groups": ["adults"],
            "cuisine_tags": [cuisine],
        }
        for idx in range(1, 8)
    ]
    return {"schema_version": 1, "dishes": dishes}


@pytest.mark.asyncio
async def test_run_meal_plan_turn_happy_path() -> None:
    plan_payload = _build_plan_payload(cuisine="italian")

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", "")).lower().replace(" ", "")
            ingredients = [
                {
                    "name": f"ингредиент-{dish}",
                    "search_query": f"ing-{dish}",
                    "quantity": 1,
                    "unit": "шт",
                },
                {"name": "помидор", "search_query": "помидор", "quantity": 1, "unit": "шт"},
            ]
            return json.dumps({"ok": True, "ingredients": ingredients}, ensure_ascii=False)
        if name == "vkusvill_products_search":
            query = str(arguments.get("q", "")).strip()
            return _products_search_response(query, xml_id=101, name="Томаты")
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/meal-exec"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(
        history=[{"role": "user", "content": "меню на неделю для 2 человек с итальянской кухней"}],
    )
    trace = _TraceSpy()
    progress: list[str] = []

    async def _on_progress(message: str) -> None:
        progress.append(message)

    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=555,
        text="меню на неделю для 2 человек с итальянской кухней",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=_on_progress,
    )

    assert "🍽 План питания" in result
    assert "Блюдо 1" in result
    assert "https://shop.example/cart/meal-exec" in result
    assert "soft_preferences_coverage" in result
    assert "coverage target >= 0.70" in result
    assert progress == [
        "🧠 Планирую меню...",
        "🥗 Подбираю ингредиенты...",
        "🔍 Ищу товары...",
        "🛒 Формирую корзину...",
    ]
    assert len(agent.snapshot_calls) == 1
    assert agent.price_summary_calls == 1
    assert 555 in agent._history
    metadata = trace.updates[-1]["metadata"]
    assert metadata["reason"] == "meal_plan_executor_completed"
    assert metadata["soft_coverage_min"] >= 0.70
    assert metadata["phase2_deadline_seconds"] == 100.0
    assert metadata["turn_deadline_seconds"] == 120.0


@pytest.mark.asyncio
async def test_run_meal_plan_turn_reserves_tail_budget_for_cart_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_payload = _build_plan_payload(cuisine="italian")
    reserved_deadline = 12345.0
    seen: dict[str, Any] = {}

    async def _fake_search_products(
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[str], bool, Any]:
        seen["phase2_deadline_at"] = kwargs["phase2_deadline_at"]

        class _Stats:
            def as_dict(self) -> dict[str, Any]:
                return {
                    "aggregated_ingredients_count": 1,
                    "primary_attempted": False,
                    "primary_products_count": 0,
                    "primary_not_found_count": 0,
                    "final_products_count": 1,
                    "final_not_found_count": 0,
                    "used_chunk_fallback": False,
                    "chunk_count": 0,
                    "chunk_products_count": 1,
                    "chunk_not_found_count": 0,
                    "fallback_reason": "",
                    "primary_error_type": None,
                    "primary_error_message": None,
                    "chunk_failure_count": 0,
                    "chunk_sample_failures": [],
                    "local_fallback_chunk_count": 0,
                    "local_fallback_products_count": 0,
                    "local_fallback_not_found_count": 0,
                }

        return [{"xml_id": 101, "q": 1}], [], False, _Stats()

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_executor.reserve_deadline",
        lambda deadline_at, *, reserve_seconds: reserved_deadline,
    )
    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_day_search_ops.search_products",
        _fake_search_products,
    )

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {"name": "помидор", "search_query": "помидор", "quantity": 1, "unit": "шт"}
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/meal-exec"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(
        history=[{"role": "user", "content": "меню на неделю для 2 человек"}],
    )
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=555,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: asyncio.sleep(0),
    )

    assert "https://shop.example/cart/meal-exec" in result
    assert seen["phase2_deadline_at"] == reserved_deadline
    search_span = next(
        span for span in trace.spans if span["start"]["name"] == "meal-plan.search-products"
    )
    assert search_span["start"]["input"]["reserved_cart_create_seconds"] == 28.0


@pytest.mark.asyncio
async def test_run_meal_plan_turn_filters_pantry_before_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_payload = _build_plan_payload(cuisine="italian")
    seen: dict[str, Any] = {"aggregated_calls": []}

    async def _fake_search_products(
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[str], bool, Any]:
        seen["aggregated_calls"].append(kwargs["aggregated_ingredients"])

        class _Stats:
            def as_dict(self) -> dict[str, Any]:
                return {
                    "aggregated_ingredients_count": len(kwargs["aggregated_ingredients"]),
                    "primary_attempted": False,
                    "primary_products_count": 0,
                    "primary_not_found_count": 0,
                    "final_products_count": 1,
                    "final_not_found_count": 0,
                    "used_chunk_fallback": False,
                    "chunk_count": 0,
                    "chunk_products_count": 1,
                    "chunk_not_found_count": 0,
                    "fallback_reason": "",
                    "primary_error_type": None,
                    "primary_error_message": None,
                    "chunk_failure_count": 0,
                    "chunk_sample_failures": [],
                    "local_fallback_chunk_count": 0,
                    "local_fallback_products_count": 0,
                    "local_fallback_not_found_count": 0,
                }

        return [{"xml_id": 101, "q": 1}], [], False, _Stats()

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_day_search_ops.search_products",
        _fake_search_products,
    )

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {"name": "Вода", "search_query": "вода", "quantity": 1, "unit": "л"},
                        {"name": "Соль", "search_query": "соль", "quantity": 1, "unit": "щепотка"},
                        {"name": "Помидор", "search_query": "помидор", "quantity": 1, "unit": "шт"},
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/pantry-filter"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=556,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: asyncio.sleep(0),
    )

    assert "https://shop.example/cart/pantry-filter" in result
    assert (
        seen["aggregated_calls"]
        == [[{"name": "Помидор", "search_query": "помидор", "quantity": 1.0, "unit": "шт"}]] * 7
    )
    metadata = trace.updates[-1]["metadata"]
    assert metadata["meal_plan_pantry_filtered"] == ["Вода", "Соль"]


@pytest.mark.asyncio
async def test_run_meal_plan_turn_uses_recomputed_constraints_after_phase2_filter() -> None:
    plan_payload = _build_plan_payload(cuisine="italian")
    plan_payload["dishes"].append(
        {
            "name": "Блюдо 8",
            "day": 7,
            "meal_type": "dinner",
            "servings_total": 2,
            "audience_groups": ["adults"],
            "cuisine_tags": ["russian"],
        }
    )

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", ""))
            ingredients = (
                [
                    {
                        "name": "куриное филе",
                        "search_query": "куриное филе",
                        "quantity": 1,
                        "unit": "шт",
                    }
                ]
                if dish == "Блюдо 8"
                else [
                    {
                        "name": "помидор",
                        "search_query": "помидор",
                        "quantity": 1,
                        "unit": "шт",
                    }
                ]
            )
            return json.dumps({"ok": True, "ingredients": ingredients}, ensure_ascii=False)
        if name == "vkusvill_products_search":
            query = str(arguments.get("q", "")).strip()
            return _products_search_response(query, xml_id=301, name="Томаты")
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/phase2-safe"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(
        history=[
            {"role": "user", "content": "меню на неделю для 2 человек, веганское, итальянское"}
        ]
    )
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=556,
        text="меню на неделю для 2 человек, веганское, итальянское",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "Блюдо 8" not in result
    assert "hard_constraints: соблюдены" in result
    assert "soft_preferences_coverage: adults=1.00" in result
    assert "https://shop.example/cart/phase2-safe" in result


@pytest.mark.asyncio
async def test_run_meal_plan_turn_generation_failure_returns_contract_fallback() -> None:
    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        raise AssertionError(f"MCP must not be called on generation failure: {name} {arguments}")

    state = _State(history=[{"role": "user", "content": "меню на неделю"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response("не json"), _llm_response("тоже не json")],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=777,
        text="меню на неделю",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "Не удалось сгенерировать план" in result
    assert "Проверка ограничений:" in result
    assert trace.updates[-1]["metadata"]["reason"] == "meal_plan_generation_failed"


@pytest.mark.asyncio
async def test_run_meal_plan_turn_generation_failure_falls_back_to_standard_turn() -> None:
    state = _State(history=[{"role": "user", "content": "меню на неделю"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response("не json"), _llm_response("тоже не json")],
        mcp_handler=lambda _name, _arguments: "{}",
    )
    fallback_reasons: list[str] = []

    async def _fallback(reason: str) -> str:
        fallback_reasons.append(reason)
        return f"standard::{reason}"

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=778,
        text="меню на неделю",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
        fallback_to_standard_turn=_fallback,
    )

    assert result.startswith("standard::")
    assert fallback_reasons and "Не удалось сгенерировать план" in fallback_reasons[0]
    assert (
        trace.updates[-1]["metadata"]["reason"]
        == "meal_plan_generation_failed_fallback_to_standard_turn"
    )


@pytest.mark.asyncio
async def test_run_meal_plan_turn_ingredients_failure_is_fail_soft() -> None:
    plan_payload = _build_plan_payload(cuisine="russian")

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            raise RuntimeError("ingredients backend unavailable")
        raise AssertionError(f"Unexpected tool call: {name} {arguments}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    progress: list[str] = []

    async def _on_progress(message: str) -> None:
        progress.append(message)

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=888,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=_on_progress,
    )

    assert "Не удалось получить ингредиенты для плана." in result
    assert "hard_constraints: нарушения обнаружены" in result
    assert trace.updates[-1]["metadata"]["reason"] == "meal_plan_ingredients_empty"
    assert progress == ["🧠 Планирую меню...", "🥗 Подбираю ингредиенты..."]


@pytest.mark.asyncio
async def test_run_meal_plan_turn_keeps_phase1_constraints_when_some_ingredient_rows_missing() -> (
    None
):
    plan_payload = _build_plan_payload(cuisine="russian")

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", ""))
            if dish == "Блюдо 1":
                return json.dumps({"ok": True, "ingredients": []}, ensure_ascii=False)
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {"name": "помидор", "search_query": "помидор", "quantity": 1, "unit": "шт"}
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_products_search":
            query = str(arguments.get("q", "")).strip()
            return _products_search_response(query, xml_id=101, name="Томаты")
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/partial-ingredients"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(
        history=[
            {
                "role": "user",
                "content": "меню на неделю для 2 человек, вегетарианское",
            }
        ]
    )
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=889,
        text="меню на неделю для 2 человек, вегетарианское",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "https://shop.example/cart/partial-ingredients" in result
    assert "Перехожу к стандартной обработке запроса" not in result
    assert trace.updates[-1]["metadata"]["reason"] == "meal_plan_executor_completed"


@pytest.mark.asyncio
async def test_run_meal_plan_turn_writes_langfuse_observations_with_usage() -> None:
    plan_payload = _build_plan_payload(cuisine="italian")

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", "")).lower().replace(" ", "")
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": f"ингредиент-{dish}",
                            "search_query": f"ing-{dish}",
                            "quantity": 1,
                            "unit": "шт",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_products_search":
            query = str(arguments.get("q", "")).strip()
            return _products_search_response(query, xml_id=101, name="Товар")
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/obs"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    llm_response = _llm_response(json.dumps(plan_payload, ensure_ascii=False))
    llm_response["usage"] = {"prompt_tokens": 321, "completion_tokens": 123, "total_tokens": 444}
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(llm_responses=[llm_response], mcp_handler=_mcp)

    result = await run_meal_plan_turn(
        agent=agent,
        state=_State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}]),
        user_id=1001,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "https://shop.example/cart/obs" in result
    generation_names = [row["start"]["name"] for row in trace.generations]
    assert "meal-plan-generation" in generation_names
    meal_plan_generation = next(
        row for row in trace.generations if row["start"]["name"] == "meal-plan-generation"
    )
    assert meal_plan_generation["end"]["usage_details"] == {
        "input": 321,
        "output": 123,
        "total": 444,
    }
    span_names = [row["start"]["name"] for row in trace.spans]
    assert "meal-plan.parse-request" in span_names
    assert "meal-plan.collect-ingredients" in span_names
    assert "meal-plan.search-products" in span_names
    assert "meal-plan.create-cart" in span_names


@pytest.mark.asyncio
async def test_run_meal_plan_turn_uses_chunked_recipe_search_fallback() -> None:
    plan_payload = _build_plan_payload(cuisine="russian")
    recipe_search_calls = 0
    products_search_calls = 0

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        nonlocal recipe_search_calls, products_search_calls
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", "")).lower().replace(" ", "")
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": f"ингредиент-{dish}-a",
                            "search_query": f"ing-{dish}-a",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {
                            "name": f"ингредиент-{dish}-b",
                            "search_query": f"ing-{dish}-b",
                            "quantity": 1,
                            "unit": "шт",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if name == "recipe_search":
            recipe_search_calls += 1
            return json.dumps({"ok": True, "found": [], "not_found": []}, ensure_ascii=False)
        if name == "vkusvill_products_search":
            products_search_calls += 1
            query = str(arguments.get("q", "")).strip()
            return _products_search_response(query, xml_id=500 + products_search_calls)
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/chunked"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=909,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "https://shop.example/cart/chunked" in result
    assert recipe_search_calls == 0
    metadata = trace.updates[-1]["metadata"]
    assert metadata["used_chunk_fallback"] is True
    assert metadata["meal_plan_recipe_search"]["fallback_reason"] == "local_search_only"
    assert products_search_calls == 14


@pytest.mark.asyncio
async def test_run_meal_plan_turn_uses_chunked_recipe_search_fallback_on_partial_primary() -> None:
    plan_payload = _build_plan_payload(cuisine="russian")
    recipe_search_calls = 0
    products_search_calls = 0

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        nonlocal recipe_search_calls, products_search_calls
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", "")).lower().replace(" ", "")
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": f"ингредиент-{dish}-a",
                            "search_query": f"ing-{dish}-a",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {
                            "name": f"ингредиент-{dish}-b",
                            "search_query": f"ing-{dish}-b",
                            "quantity": 1,
                            "unit": "шт",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if name == "recipe_search":
            recipe_search_calls += 1
            return json.dumps({"ok": True, "found": [], "not_found": []}, ensure_ascii=False)
        if name == "vkusvill_products_search":
            products_search_calls += 1
            query = str(arguments.get("q", "")).strip()
            return _products_search_response(query, xml_id=700 + products_search_calls)
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/partial-primary"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=910,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "https://shop.example/cart/partial-primary" in result
    assert recipe_search_calls == 0
    assert products_search_calls == 14
    metadata = trace.updates[-1]["metadata"]
    assert metadata["used_chunk_fallback"] is True
    assert metadata["meal_plan_recipe_search"]["fallback_reason"] == "local_search_only"


@pytest.mark.asyncio
async def test_run_meal_plan_turn_skips_primary_recipe_search_for_large_batches() -> None:
    plan_payload = _build_plan_payload(cuisine="russian")
    recipe_search_calls = 0
    products_search_calls = 0

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        nonlocal recipe_search_calls, products_search_calls
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", "")).lower().replace(" ", "")
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": f"ингредиент-{dish}-a",
                            "search_query": f"ing-{dish}-a",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {
                            "name": f"ингредиент-{dish}-b",
                            "search_query": f"ing-{dish}-b",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {
                            "name": f"ингредиент-{dish}-c",
                            "search_query": f"ing-{dish}-c",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {
                            "name": f"ингредиент-{dish}-d",
                            "search_query": f"ing-{dish}-d",
                            "quantity": 1,
                            "unit": "шт",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if name == "recipe_search":
            recipe_search_calls += 1
            return json.dumps(
                {
                    "ok": True,
                    "found": [{"xml_id": 900 + recipe_search_calls, "suggested_q": 1}],
                    "not_found": [],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_products_search":
            products_search_calls += 1
            query = str(arguments.get("q", "")).strip() or "товар"
            return json.dumps(
                {
                    "ok": True,
                    "items": [
                        {
                            "xml_id": 1000 + products_search_calls,
                            "name": f"Товар для {query}",
                            "price": 100,
                            "unit": "шт",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/large-batch"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=911,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "https://shop.example/cart/large-batch" in result
    assert recipe_search_calls == 0
    assert products_search_calls == 28
    metadata = trace.updates[-1]["metadata"]
    assert metadata["meal_plan_recipe_search"]["primary_attempted"] is False
    assert metadata["meal_plan_recipe_search"]["used_chunk_fallback"] is True
    assert metadata["meal_plan_recipe_search"]["fallback_reason"] == "local_search_only"
    assert metadata["meal_plan_recipe_search"]["local_fallback_chunk_count"] == 7
    assert metadata["meal_plan_recipe_search"]["chunk_failure_count"] == 0


@pytest.mark.asyncio
async def test_run_meal_plan_turn_uses_local_products_fallback_on_chunk_timeout() -> None:
    plan_payload = _build_plan_payload(cuisine="russian")
    xml_ids_by_query: dict[str, int] = {}
    recipe_search_calls = 0

    def _xml_id(query: str) -> int:
        existing = xml_ids_by_query.get(query)
        if existing is not None:
            return existing
        assigned = 4000 + len(xml_ids_by_query) + 1
        xml_ids_by_query[query] = assigned
        return assigned

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        nonlocal recipe_search_calls
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", "")).lower().replace(" ", "")
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": f"ингредиент-{dish}-a",
                            "search_query": f"ing-{dish}-a",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {
                            "name": f"ингредиент-{dish}-b",
                            "search_query": f"ing-{dish}-b",
                            "quantity": 1,
                            "unit": "шт",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if name == "recipe_search":
            recipe_search_calls += 1
            raise TimeoutError("recipe_search timeout")
        if name == "vkusvill_products_search":
            query = str(arguments.get("q", "")).strip()
            return json.dumps(
                {
                    "ok": True,
                    "items": [
                        {
                            "xml_id": _xml_id(query),
                            "name": f"Товар для {query}",
                            "price": 100,
                            "unit": "шт",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/local-products-fallback"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=912,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "https://shop.example/cart/local-products-fallback" in result
    assert recipe_search_calls == 0
    metadata = trace.updates[-1]["metadata"]
    assert metadata["meal_plan_recipe_search"]["fallback_reason"] == "local_search_only"
    assert metadata["meal_plan_recipe_search"]["local_fallback_chunk_count"] == 7
    assert metadata["meal_plan_recipe_search"]["local_fallback_products_count"] == 14
    assert metadata["meal_plan_recipe_search"]["chunk_failure_count"] == 0


@pytest.mark.asyncio
async def test_run_meal_plan_turn_respects_timeout_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_executor.RECIPE_INGREDIENTS_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr("vkuswill_bot.agents.meal_plan_executor.PHASE2_DEADLINE_SECONDS", 0.05)

    plan_payload = _build_plan_payload(cuisine="russian")

    async def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            await asyncio.sleep(0.03)
            return json.dumps({"ok": True, "ingredients": []}, ensure_ascii=False)
        raise AssertionError(f"Unexpected tool call: {name} {arguments}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=990,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "Не удалось получить ингредиенты для плана." in result
    assert trace.updates[-1]["metadata"]["reason"] == "meal_plan_ingredients_empty"


@pytest.mark.asyncio
async def test_run_meal_plan_turn_bounds_phase2_by_turn_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vkuswill_bot.agents.meal_plan_executor.TURN_DEADLINE_SECONDS", 0.03)
    monkeypatch.setattr("vkuswill_bot.agents.meal_plan_executor.PHASE2_DEADLINE_SECONDS", 1.0)
    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_executor.RECIPE_INGREDIENTS_TIMEOUT_SECONDS",
        0.2,
    )

    plan_payload = _build_plan_payload(cuisine="russian")

    async def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            await asyncio.sleep(0.02)
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": "рис",
                            "search_query": "рис",
                            "quantity": 1,
                            "unit": "кг",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if name == "recipe_search":
            return json.dumps(
                {
                    "ok": True,
                    "found": [{"xml_id": 501, "suggested_q": 1, "name": "Рис"}],
                    "not_found": [],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/late"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name} {arguments}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    original_call_llm = agent._call_llm

    async def _slow_call_llm(**kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return await original_call_llm(**kwargs)

    agent._call_llm = _slow_call_llm  # type: ignore[method-assign]

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=9901,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "Не удалось получить ингредиенты для плана." in result
    assert trace.updates[-1]["metadata"]["reason"] == "meal_plan_ingredients_empty"
    assert all(name != "recipe_search" for name, _args in agent.mcp_calls)
    assert all(name != "vkusvill_cart_link_create" for name, _args in agent.mcp_calls)


@pytest.mark.asyncio
async def test_run_meal_plan_turn_fail_softs_when_hard_constraints_remain_after_retry() -> None:
    plan_payload = _build_plan_payload(cuisine="italian")

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": "куриное филе",
                            "search_query": "куриное филе",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {"name": "рис", "search_query": "рис", "quantity": 1, "unit": "кг"},
                    ],
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name} {arguments}")

    state = _State(
        history=[{"role": "user", "content": "меню на неделю для 2 человек, веганское"}],
    )
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=991,
        text="меню на неделю для 2 человек, веганское",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "Не удалось собрать безопасный план после retry" in result
    assert (
        trace.updates[-1]["metadata"]["reason"] == "meal_plan_hard_constraints_violated_after_retry"
    )
    assert all(name != "recipe_search" for name, _args in agent.mcp_calls)


@pytest.mark.asyncio
async def test_run_meal_plan_turn_falls_back_to_standard_turn_after_phase2_retry_failure() -> None:
    plan_payload = _build_plan_payload(cuisine="italian")

    def _mcp(name: str, arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": "куриное филе",
                            "search_query": "куриное филе",
                            "quantity": 1,
                            "unit": "шт",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name} {arguments}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек, веганское"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )
    fallback_reasons: list[str] = []

    async def _fallback(reason: str) -> str:
        fallback_reasons.append(reason)
        return f"standard::{reason}"

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=992,
        text="меню на неделю для 2 человек, веганское",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
        fallback_to_standard_turn=_fallback,
    )

    assert result.startswith("standard::")
    assert fallback_reasons
    assert "Не удалось собрать безопасный план после retry" in fallback_reasons[0]
    assert all(name != "recipe_search" for name, _args in agent.mcp_calls)


@pytest.mark.asyncio
async def test_run_meal_plan_turn_parse_failure_falls_back_to_standard_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _State(history=[{"role": "user", "content": "меню на неделю"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[],
        mcp_handler=lambda _name, _arguments: "{}",
    )
    fallback_reasons: list[str] = []

    async def _fallback(reason: str) -> str:
        fallback_reasons.append(reason)
        return f"standard::{reason}"

    def _raise_parse_error(_text: str, _profile: dict[str, Any]) -> Any:
        raise ValueError("parse failed")

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_executor.parse_meal_plan_request",
        _raise_parse_error,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=1200,
        text="меню на неделю",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
        fallback_to_standard_turn=_fallback,
    )

    assert result.startswith("standard::")
    assert fallback_reasons
    assert "Не удалось разобрать meal-plan запрос" in fallback_reasons[0]
    assert (
        trace.updates[-1]["metadata"]["reason"]
        == "meal_plan_parse_failed_fallback_to_standard_turn"
    )


@pytest.mark.asyncio
async def test_run_meal_plan_turn_parse_failure_returns_fail_soft_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _State(history=[{"role": "user", "content": "меню на неделю"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[],
        mcp_handler=lambda _name, _arguments: "{}",
    )

    def _raise_parse_error(_text: str, _profile: dict[str, Any]) -> Any:
        raise RuntimeError("broken parser")

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_executor.parse_meal_plan_request",
        _raise_parse_error,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=1201,
        text="меню на неделю",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert "Не удалось разобрать meal-plan запрос" in result
    assert "Статус: план не сформирован" in result
    assert trace.updates[-1]["metadata"]["reason"] == "meal_plan_parse_failed"


@pytest.mark.asyncio
async def test_run_meal_plan_turn_cart_create_double_timeout_returns_structured_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vkuswill_bot.agents.meal_plan_executor.CART_CREATE_TIMEOUT_SECONDS", 0.01)
    plan_payload = _build_plan_payload(cuisine="italian")
    cart_calls = 0

    async def _mcp(name: str, arguments: dict[str, Any]) -> str:
        nonlocal cart_calls
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", ""))
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": f"инг-{dish}",
                            "search_query": f"инг-{dish}",
                            "quantity": 1,
                            "unit": "шт",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_products_search":
            return _products_search_response("ингредиент", xml_id=901, name="Томаты")
        if name == "vkusvill_cart_link_create":
            cart_calls += 1
            await asyncio.sleep(0.2)
            return "{}"
        raise AssertionError(f"Unexpected tool call: {name} {arguments}")

    state = _State(history=[{"role": "user", "content": "меню на неделю для 2 человек"}])
    trace = _TraceSpy()
    agent = _FakeExecutorAgent(
        llm_responses=[_llm_response(json.dumps(plan_payload, ensure_ascii=False))],
        mcp_handler=_mcp,
    )

    result = await run_meal_plan_turn(
        agent=agent,
        state=state,
        user_id=1202,
        text="меню на неделю для 2 человек",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=lambda _msg: _done(),
    )

    assert cart_calls == 1
    assert "Ссылка: не сформирована" in result
    assert "Список товаров (без ссылки):" in result
    assert "Томаты x 7" in result
    assert "Не найдено: нет" in result


async def _done() -> None:
    return None
