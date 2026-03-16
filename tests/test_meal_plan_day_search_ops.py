from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from vkuswill_bot.agents.meal_plan_day_search_ops import search_products_day_by_day
from vkuswill_bot.agents.meal_plan_recipe_search_ops import (
    _match_not_found_to_ingredients,
    search_products,
)


@dataclass
class _State:
    mcp_call_cache: dict[str, str] = field(default_factory=dict)
    explicit_pantry_requests: set[str] = field(default_factory=set)


class _Agent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str:
        self.calls.append((name, arguments))
        query = str(arguments.get("q", "")).strip()
        if query == "яйца куриные":
            return '{"ok": false, "error": "mcp_error", "message": "deadline exceeded"}'
        if query == "яйца":
            return (
                '{"ok": true, "data": {"items": ['
                '{"xml_id": 101, "name": "Яйцо куриное С1", "price": 120, "unit": "шт"}'
                "]}}"
            )
        return '{"ok": true, "data": {"items": []}}'


@pytest.mark.asyncio
async def test_search_products_local_fallback_retries_after_tool_error_json() -> None:
    agent = _Agent()
    state = _State()

    products, not_found, used_chunk_fallback, stats = await search_products(
        agent=agent,
        state=state,
        user_id=1,
        llm_provider="qwen_openai",
        aggregated_ingredients=[
            {
                "name": "яйца",
                "search_query": "яйца куриные",
                "quantity": 2,
                "unit": "шт",
            }
        ],
        phase2_deadline_at=10**9,
        prefer_local_only=True,
    )

    assert used_chunk_fallback is True
    assert stats.final_products_count == 1
    assert not not_found
    assert products[0]["xml_id"] == 101
    queries = [args.get("q") for name, args in agent.calls if name == "vkusvill_products_search"]
    assert queries[:2] == ["яйца куриные", "яйца"]


@pytest.mark.asyncio
async def test_search_products_day_by_day_uses_overall_deadline_for_each_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_deadlines: list[float] = []

    async def _fake_search_products(**kwargs: Any):
        captured_deadlines.append(float(kwargs["phase2_deadline_at"]))
        ingredient = kwargs["aggregated_ingredients"][0]
        return (
            [{"xml_id": int(ingredient["quantity"]), "q": 1.0}],
            [],
            True,
            type(
                "Stats",
                (),
                {
                    "primary_attempted": False,
                    "primary_products_count": 0,
                    "primary_not_found_count": 0,
                    "fallback_reason": "local_search_only",
                    "chunk_count": 1,
                    "chunk_products_count": 1,
                    "chunk_not_found_count": 0,
                    "chunk_failure_count": 0,
                    "local_fallback_chunk_count": 1,
                    "local_fallback_products_count": 1,
                    "local_fallback_not_found_count": 0,
                    "primary_error_type": None,
                    "primary_error_message": None,
                    "chunk_sample_failures": [],
                    "as_dict": lambda self: {"ok": True},
                },
            )(),
        )

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_day_search_ops.search_products",
        _fake_search_products,
    )

    import time as _time

    far_future = _time.monotonic() + 10_000
    (
        products,
        not_found,
        _used_fallback,
        stats,
        pantry_filtered,
        deferred,
        _products_by_day,
    ) = await search_products_day_by_day(
        agent=object(),
        state=_State(),
        user_id=1,
        llm_provider="qwen_openai",
        dishes_payload=[
            {"name": "День 1", "day": 1},
            {"name": "День 2", "day": 2},
        ],
        ingredients_by_dish={
            "день 1": [{"name": "рис", "search_query": "рис", "quantity": 1, "unit": "шт"}],
            "день 2": [{"name": "гречка", "search_query": "гречка", "quantity": 2, "unit": "шт"}],
        },
        phase2_deadline_at=far_future,
        trace=None,
        filter_pantry_fn=lambda items, explicit_pantry_requests: (items, []),
        aggregate_ingredients_fn=lambda items: items,
        prioritize_ingredients_fn=lambda items: (items, []),
    )

    assert len(captured_deadlines) == 2
    assert captured_deadlines[0] <= far_future
    assert captured_deadlines[1] <= far_future
    assert len(products) == 2
    assert not not_found
    assert stats.searched_day_count == 2
    assert pantry_filtered == []
    assert deferred == []


class TestMatchNotFoundToIngredients:
    def test_matches_by_search_query(self) -> None:
        ingredients = [
            {"name": "помидоры", "search_query": "помидор", "quantity": 2, "unit": "шт"},
            {"name": "морковь", "search_query": "морковь", "quantity": 1, "unit": "шт"},
            {"name": "лук", "search_query": "лук", "quantity": 1, "unit": "шт"},
        ]
        matched = _match_not_found_to_ingredients(["помидор", "морковь"], ingredients)
        assert len(matched) == 2
        names = {m["name"] for m in matched}
        assert names == {"помидоры", "морковь"}

    def test_matches_by_name_fallback(self) -> None:
        ingredients = [
            {"name": "томат", "search_query": "томаты свежие", "quantity": 1, "unit": "шт"},
        ]
        matched = _match_not_found_to_ingredients(["томат"], ingredients)
        assert len(matched) == 1

    def test_no_match_returns_empty(self) -> None:
        ingredients = [
            {"name": "рис", "search_query": "рис", "quantity": 1, "unit": "шт"},
        ]
        matched = _match_not_found_to_ingredients(["помидор"], ingredients)
        assert matched == []


@pytest.mark.asyncio
async def test_retry_recovers_previously_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """All first-pass queries for 'помидор' timeout, retry pass succeeds."""
    total_tomato_calls = 0

    class _RetryAgent:
        async def _call_mcp_tool(
            self,
            *,
            name: str,
            arguments: dict[str, Any],
            llm_provider: str,
            call_cache: dict[str, str] | None = None,
            user_id: int | None = None,
        ) -> str:
            nonlocal total_tomato_calls
            query = str(arguments.get("q", "")).strip()
            if "помидор" in query.lower():
                total_tomato_calls += 1
                if total_tomato_calls <= 4:
                    raise TimeoutError("mcp timeout")
            if "помидор" in query.lower():
                return (
                    '{"ok": true, "data": {"items": ['
                    '{"xml_id": 200, "name": "Томаты", "price": 150, "unit": "кг"}'
                    "]}}"
                )
            if query == "молоко":
                return (
                    '{"ok": true, "data": {"items": ['
                    '{"xml_id": 100, "name": "Молоко", "price": 90, "unit": "шт"}'
                    "]}}"
                )
            return '{"ok": true, "data": {"items": []}}'

    monkeypatch.setattr(
        "vkuswill_bot.agents.meal_plan_recipe_search_ops._RETRY_NOT_FOUND_COOLDOWN_SECONDS",
        0.0,
    )

    products, not_found, _used_chunk_fallback, stats = await search_products(
        agent=_RetryAgent(),
        state=_State(),
        user_id=1,
        llm_provider="qwen_openai",
        aggregated_ingredients=[
            {"name": "молоко", "search_query": "молоко", "quantity": 1, "unit": "л"},
            {"name": "помидоры", "search_query": "помидор", "quantity": 2, "unit": "шт"},
        ],
        phase2_deadline_at=10**9,
        prefer_local_only=True,
    )

    assert stats.retry_attempted > 0
    assert stats.retry_recovered > 0
    assert not not_found
    xml_ids = {p["xml_id"] for p in products}
    assert 100 in xml_ids
    assert 200 in xml_ids
