"""Тесты для vkuswill_bot.agents.product_index_manager."""

from __future__ import annotations

import json

from vkuswill_bot.agents.product_index_manager import (
    build_cart_snapshot,
    build_product_index_from_history,
    update_product_index_from_tool_result,
    update_search_query_by_xml_id,
)


def test_update_product_index_ignores_non_indexable_tool() -> None:
    index: dict[int, dict[str, object]] = {}
    payload = json.dumps(
        {"ok": True, "data": {"items": [{"xml_id": 101, "name": "Молоко", "price": 99}]}},
        ensure_ascii=False,
    )

    update_product_index_from_tool_result(
        product_index=index,
        tool_name="unknown_tool",
        tool_result=payload,
    )
    assert index == {}


def test_update_product_index_skips_placeholder_rows_without_price() -> None:
    index: dict[int, dict[str, object]] = {}
    payload = json.dumps(
        {
            "ok": True,
            "data": {
                "items": [
                    {"xml_id": 1, "name": "Кефир", "price": 129, "unit": "шт"},
                    {"xml_id": 2},
                ]
            },
        },
        ensure_ascii=False,
    )

    update_product_index_from_tool_result(
        product_index=index,
        tool_name="vkusvill_products_search",
        tool_result=payload,
    )

    assert index[1]["name"] == "Кефир"
    assert index[1]["price"] == 129
    assert 2 not in index


def test_build_product_index_from_history_uses_last_20_tool_messages() -> None:
    history = [{"role": "system", "content": "sys"}]
    for xml_id in range(1, 22):
        history.append(
            {
                "role": "tool",
                "name": "vkusvill_products_search",
                "content": json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "items": [
                                {
                                    "xml_id": xml_id,
                                    "name": f"Товар {xml_id}",
                                    "price": xml_id,
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        )

    index = build_product_index_from_history(history)
    assert 1 not in index
    assert 2 in index
    assert 21 in index
    assert len(index) == 20


def test_update_search_query_by_xml_id_uses_q_or_query() -> None:
    payload = json.dumps(
        {"ok": True, "data": {"items": [{"xml_id": 77, "name": "Творог", "price": 189}]}},
        ensure_ascii=False,
    )

    mapping: dict[int, str] = {}
    update_search_query_by_xml_id(
        search_query_by_xml_id=mapping,
        tool_args={"query": "творог"},
        tool_result=payload,
    )

    assert mapping == {77: "творог"}


def test_update_search_query_by_xml_id_skips_empty_query() -> None:
    payload = json.dumps(
        {"ok": True, "data": {"items": [{"xml_id": 77, "name": "Творог", "price": 189}]}},
        ensure_ascii=False,
    )
    mapping: dict[int, str] = {}

    update_search_query_by_xml_id(
        search_query_by_xml_id=mapping,
        tool_args={"q": "   "},
        tool_result=payload,
    )
    assert mapping == {}


def test_build_cart_snapshot_uses_summary_values() -> None:
    args = {"products": [{"xml_id": 1, "q": 1}, {"xml_id": 2, "q": 1}]}
    result = json.dumps(
        {
            "ok": True,
            "data": {
                "link": "https://vkusvill.ru/cart/abc",
                "price_summary": {"total": 320.5, "count": 1},
            },
        },
        ensure_ascii=False,
    )

    snapshot = build_cart_snapshot(args=args, result=result)
    assert snapshot is not None
    assert snapshot["link"] == "https://vkusvill.ru/cart/abc"
    assert snapshot["total"] == 320.5
    assert snapshot["items_count"] == 1


def test_build_cart_snapshot_falls_back_to_args_products_count() -> None:
    args = {"products": [{"xml_id": 1}, {"xml_id": 2}, {"xml_id": 3}]}
    result = json.dumps(
        {"ok": True, "data": {"link": "https://vkusvill.ru/cart/xyz", "price_summary": {}}},
        ensure_ascii=False,
    )

    snapshot = build_cart_snapshot(args=args, result=result)
    assert snapshot is not None
    assert snapshot["items_count"] == 3


def test_build_cart_snapshot_returns_none_for_invalid_payload() -> None:
    assert build_cart_snapshot(args={}, result='{"ok": false}') is None
    assert build_cart_snapshot(args={}, result="not-json") is None
