from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from vkuswill_bot.agents.meal_plan_day_search_ops import search_products_day_by_day
from vkuswill_bot.agents.meal_plan_recipe_search_ops import search_products


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

    products, not_found, _used_fallback, stats, pantry_filtered, deferred = (
        await search_products_day_by_day(
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
                "день 2": [
                    {"name": "гречка", "search_query": "гречка", "quantity": 2, "unit": "шт"}
                ],
            },
            phase2_deadline_at=12345.0,
            trace=None,
            filter_pantry_fn=lambda items, explicit_pantry_requests: (items, []),
            aggregate_ingredients_fn=lambda items: items,
            prioritize_ingredients_fn=lambda items: (items, []),
        )
    )

    assert captured_deadlines == [12345.0, 12345.0]
    assert len(products) == 2
    assert not not_found
    assert stats.searched_day_count == 2
    assert pantry_filtered == []
    assert deferred == []
