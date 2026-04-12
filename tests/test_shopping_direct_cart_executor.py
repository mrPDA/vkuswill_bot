from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from vkuswill_bot.agents.cart_price_builder import ensure_cart_price_summary
from vkuswill_bot.agents.shopping_direct_cart_executor import (
    should_use_explicit_cart_fast_path,
    try_explicit_cart_fast_path,
)


class _AgentStub:
    def __init__(self, *, recipe_search_result: str, cart_result: str | None = None) -> None:
        self._history: dict[int, list[dict[str, Any]]] = {}
        self._last_cart_snapshot: dict[int, dict[str, Any]] = {}
        self.recipe_search_result = recipe_search_result
        self.cart_result = cart_result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _trim_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return history

    def _prepare_tool_result_for_history(self, tool_name: str, tool_result: str) -> str:
        _ = tool_name
        return tool_result

    def _capture_cart_snapshot(
        self,
        *,
        user_id: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None:
        _ = tool_name, result
        self._last_cart_snapshot[user_id] = {"products": list(args.get("products", []))}

    def _ensure_cart_price_summary(
        self,
        *,
        cart_data: dict[str, Any],
        product_index: dict[int, dict[str, Any]],
    ) -> None:
        ensure_cart_price_summary(cart_data=cart_data, product_index=product_index)

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str:
        _ = llm_provider, call_cache, user_id
        self.calls.append((name, arguments))
        if name == "recipe_search":
            return self.recipe_search_result
        if name == "vkusvill_cart_link_create" and self.cart_result is not None:
            return self.cart_result
        raise AssertionError(name)


def _state(*, direct_cart_requests: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        prompt_profile="cart",
        previous_cart_products=[],
        direct_cart_requests=direct_cart_requests,
        product_index_this_turn={},
        mcp_call_cache={},
        history=[],
        cart_data_this_turn=None,
    )


def test_should_use_explicit_cart_fast_path() -> None:
    state = _state(
        direct_cart_requests=[
            {"name": "чипсы", "search_query": "чипсы", "quantity": 1.0, "unit": "шт"},
            {"name": "торт", "search_query": "торт", "quantity": 1.0, "unit": "шт"},
        ]
    )

    assert should_use_explicit_cart_fast_path(state=state, text="чипсы, торт") is True
    assert (
        should_use_explicit_cart_fast_path(
            state=state,
            text="что можно приготовить из чипсов и торта?",
        )
        is False
    )


@pytest.mark.asyncio
async def test_try_explicit_cart_fast_path_rejects_luxury_substitutes() -> None:
    agent = _AgentStub(
        recipe_search_result=json.dumps(
            {
                "ok": True,
                "results": [
                    {
                        "search_query": "трюфели",
                        "best_match": {
                            "xml_id": 101,
                            "suggested_q": 1,
                            "name": 'Конфеты "Трюфель"',
                            "price": 286,
                            "unit": "шт",
                        },
                    },
                    {
                        "search_query": "фуа-гра",
                        "best_match": {
                            "xml_id": 202,
                            "suggested_q": 1,
                            "name": "Паштет Французский с фуа-гра",
                            "price": 312,
                            "unit": "шт",
                        },
                    },
                ],
                "not_found": ["устрицы"],
            },
            ensure_ascii=False,
        )
    )
    state = _state(
        direct_cart_requests=[
            {"name": "трюфели", "search_query": "трюфели", "quantity": 1.0, "unit": "шт"},
            {"name": "фуа-гра", "search_query": "фуа-гра", "quantity": 1.0, "unit": "шт"},
            {"name": "устрицы", "search_query": "устрицы", "quantity": 1.0, "unit": "шт"},
        ]
    )

    result = await try_explicit_cart_fast_path(
        agent=agent,
        state=state,
        user_id=42,
        text="найди мне трюфели, фуа-гра и устрицы",
        llm_provider="qwen_openai",
        trace=None,
    )

    assert result == "Не нашла точных товаров в каталоге: устрицы, трюфели, фуа-гра."
    assert [name for name, _args in agent.calls] == ["recipe_search"]


@pytest.mark.asyncio
async def test_try_explicit_cart_fast_path_builds_cart() -> None:
    agent = _AgentStub(
        recipe_search_result=json.dumps(
            {
                "ok": True,
                "results": [
                    {
                        "search_query": "чипсы",
                        "best_match": {
                            "xml_id": 101,
                            "suggested_q": 1,
                            "name": "Чипсы картофельные",
                            "price": 120,
                            "unit": "шт",
                        },
                    },
                    {
                        "search_query": "торт",
                        "best_match": {
                            "xml_id": 202,
                            "suggested_q": 1,
                            "name": "Торт Медовик",
                            "price": 680,
                            "unit": "шт",
                        },
                    },
                ],
                "not_found": [],
            },
            ensure_ascii=False,
        ),
        cart_result=json.dumps(
            {
                "ok": True,
                "data": {
                    "link": "https://vkusvill.ru/?share_basket=test",
                    "price_summary": {"total": 800.0},
                },
            },
            ensure_ascii=False,
        ),
    )
    state = _state(
        direct_cart_requests=[
            {"name": "чипсы", "search_query": "чипсы", "quantity": 1.0, "unit": "шт"},
            {"name": "торт", "search_query": "торт", "quantity": 1.0, "unit": "шт"},
        ]
    )

    result = await try_explicit_cart_fast_path(
        agent=agent,
        state=state,
        user_id=42,
        text="день рождения: чипсы, торт",
        llm_provider="qwen_openai",
        trace=None,
    )

    assert "Чипсы картофельные" in result
    assert "Торт Медовик" in result
    assert "share_basket=test" in result
    assert state.cart_data_this_turn is not None
    assert agent._last_cart_snapshot[42]["link"] == "https://vkusvill.ru/?share_basket=test"
