"""Tests for cart grouping logic (group_days_for_carts / create_grouped_carts)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from vkuswill_bot.agents.meal_plan_cart_ops import (
    _MAX_CART_PRODUCTS,
    create_grouped_carts,
    group_days_for_carts,
)
from vkuswill_bot.agents.meal_plan_runtime_ops import merge_products

_NEXT_XML_ID = 0


def _make_products(n: int, prefix: str = "prod") -> list[dict[str, Any]]:
    global _NEXT_XML_ID
    items = []
    for i in range(n):
        _NEXT_XML_ID += 1
        items.append(
            {
                "xml_id": _NEXT_XML_ID,
                "name": f"{prefix}_{i + 1}",
                "q": 1,
                "category": "test",
            }
        )
    return items


def test_group_days_single_group_when_under_limit() -> None:
    by_day = {1: _make_products(10, "d1"), 2: _make_products(10, "d2")}
    groups = group_days_for_carts(by_day, merge_products)
    assert len(groups) == 1
    days, products = groups[0]
    assert days == [1, 2]
    assert len(products) <= _MAX_CART_PRODUCTS


def test_group_days_splits_when_over_limit() -> None:
    by_day = {
        1: _make_products(15, "d1"),
        2: _make_products(15, "d2"),
        3: _make_products(10, "d3"),
    }
    groups = group_days_for_carts(by_day, merge_products)
    assert len(groups) >= 2
    for days, products in groups:
        assert len(products) <= _MAX_CART_PRODUCTS
        assert days
    all_days = [d for days, _ in groups for d in days]
    assert sorted(all_days) == [1, 2, 3]


def test_group_days_merges_duplicates() -> None:
    shared = [{"xml_id": 1, "name": "milk", "q": 1, "category": "dairy"}]
    unique_d1 = _make_products(20, "d1")
    unique_d2 = _make_products(20, "d2")
    by_day = {1: shared + unique_d1, 2: shared + unique_d2}
    groups = group_days_for_carts(by_day, merge_products)
    for _days, products in groups:
        assert len(products) <= _MAX_CART_PRODUCTS


def test_group_days_empty_days_skipped() -> None:
    by_day = {1: _make_products(5, "d1"), 2: [], 3: _make_products(5, "d3")}
    groups = group_days_for_carts(by_day, merge_products)
    assert len(groups) == 1
    days, _ = groups[0]
    assert days == [1, 3]


def test_group_days_single_big_day() -> None:
    """One day alone exceeds limit — still gets its own group."""
    by_day = {1: _make_products(35, "d1")}
    groups = group_days_for_carts(by_day, merge_products)
    assert len(groups) == 1
    days, products = groups[0]
    assert days == [1]
    assert len(products) == 35


@dataclass
class _State:
    product_index_this_turn: dict[int, dict[str, Any]] = field(
        default_factory=dict,
    )


class _MockAgent:
    """Reusable mock agent for cart creation tests."""

    def __init__(self, link_template: str = "https://vkusvill.ru/?share_basket=111") -> None:
        self.cart_calls = 0
        self._link_template = link_template

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: Any,
        llm_provider: str,
        call_cache: Any,
        user_id: int,
    ) -> str:
        self.cart_calls += 1
        link = self._link_template.format(n=self.cart_calls)
        return json.dumps({"ok": True, "data": {"link": link}})

    def _ensure_cart_price_summary(self, *, cart_data: Any, product_index: Any) -> None:
        pass

    def _capture_cart_snapshot(
        self, *, user_id: int, tool_name: str, args: Any, result: str
    ) -> None:
        pass


@pytest.mark.asyncio
async def test_create_grouped_carts_single_cart_under_limit() -> None:
    """When total <= 30, one cart is created without groups."""
    products = _make_products(10)

    agent = _MockAgent()
    cart_data, stats = await create_grouped_carts(
        agent=agent,
        state=_State(),
        user_id=1,
        llm_provider="test",
        products=products,
        products_by_day={1: products},
        not_found=["arugula"],
        phase2_deadline_at=time.monotonic() + 10.0,
        timeout_seconds=5.0,
        merge_fn=merge_products,
    )
    assert stats.cart_created is True
    assert agent.cart_calls == 1
    assert cart_data is not None
    assert "share_basket" in cart_data.get("link", "")
    assert cart_data.get("not_found") == ["arugula"]
    assert "groups" not in cart_data


@pytest.mark.asyncio
async def test_create_grouped_carts_multi_cart() -> None:
    """When total > 30, multiple carts are created and groups are returned."""
    d1 = _make_products(20, "d1")
    d2 = _make_products(20, "d2")
    all_products = merge_products(d1 + d2)
    assert len(all_products) > _MAX_CART_PRODUCTS

    agent = _MockAgent(
        link_template="https://vkusvill.ru/?share_basket={n}",
    )
    cart_data, stats = await create_grouped_carts(
        agent=agent,
        state=_State(),
        user_id=1,
        llm_provider="test",
        products=all_products,
        products_by_day={1: d1, 2: d2},
        not_found=[],
        phase2_deadline_at=time.monotonic() + 10.0,
        timeout_seconds=5.0,
        merge_fn=merge_products,
    )
    assert stats.cart_created is True
    assert agent.cart_calls == 2
    assert stats.groups_count == 2
    assert cart_data is not None
    groups = cart_data.get("groups", [])
    assert len(groups) == 2
    assert groups[0]["day_label"] == "День 1"
    assert groups[1]["day_label"] == "День 2"
    assert all(g["cart_created"] for g in groups)
    assert all(g["link"] for g in groups)


@pytest.mark.asyncio
async def test_create_grouped_carts_no_products() -> None:
    class _NoCallAgent:
        async def _call_mcp_tool(self, **kw: Any) -> str:
            raise AssertionError("should not be called")

        def _ensure_cart_price_summary(self, **kw: Any) -> None:
            raise AssertionError("should not be called")

        def _capture_cart_snapshot(self, **kw: Any) -> None:
            raise AssertionError("should not be called")

    cart_data, stats = await create_grouped_carts(
        agent=_NoCallAgent(),
        state=_State(),
        user_id=1,
        llm_provider="test",
        products=[],
        products_by_day={},
        not_found=["milk"],
        phase2_deadline_at=time.monotonic() + 10.0,
        timeout_seconds=5.0,
        merge_fn=merge_products,
    )
    assert stats.attempted is False
    assert cart_data == {"not_found": ["milk"]}
