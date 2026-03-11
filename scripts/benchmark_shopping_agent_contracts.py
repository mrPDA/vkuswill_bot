#!/usr/bin/env python3
"""Microbenchmark for representative ShoppingAgent contract flows."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vkuswill_bot.agents.shopping_agent import ShoppingAgent


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


class _FakeDialogManager:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]


class _ScriptedLLMAdapter:
    def __init__(self, scripted_messages: list[dict[str, Any] | Exception]) -> None:
        self._script = list(scripted_messages)
        self.calls: list[dict[str, Any]] = []

    async def create_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "model": model,
                "messages": copy.deepcopy(messages),
                "tools_count": len(tools),
                "tool_choice": tool_choice,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if not self._script:
            raise AssertionError("LLM script exhausted")
        next_item = self._script.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return {"choices": [{"message": next_item}]}

    async def close(self) -> None:
        return None


class _FakeMCPClient:
    def __init__(
        self,
        *,
        responder: Callable[[str, dict[str, Any]], str],
        tools: list[dict[str, Any]],
    ) -> None:
        self._responder = responder
        self._tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_tools(self) -> list[dict[str, Any]]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return self._responder(name, arguments)


def _make_cart_payload(*, link: str, item_line: str, total: float) -> str:
    return json.dumps(
        {
            "ok": True,
            "data": {
                "link": link,
                "price_summary": {
                    "items": [item_line],
                    "total": total,
                    "total_text": f"Итого: {total:.2f} руб",
                },
            },
        },
        ensure_ascii=False,
    )


def _build_agent(
    *,
    llm_script: list[dict[str, Any] | Exception],
    mcp_responder: Callable[[str, dict[str, Any]], str],
    tools: list[dict[str, Any]],
) -> tuple[ShoppingAgent, _ScriptedLLMAdapter, _FakeMCPClient]:
    llm_adapter = _ScriptedLLMAdapter(llm_script)
    mcp_client = _FakeMCPClient(responder=mcp_responder, tools=tools)
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/qwen-model/latest",
        llm_max_concurrent=2,
        llm_provider="qwen_openai",
        mcp_client=mcp_client,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_adapters={"qwen_openai": llm_adapter},
    )
    return agent, llm_adapter, mcp_client


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    values_sorted = sorted(values)
    index = (len(values_sorted) - 1) * p
    low = int(index)
    high = min(low + 1, len(values_sorted) - 1)
    weight = index - low
    return values_sorted[low] * (1 - weight) + values_sorted[high] * weight


async def _run_flow_basic_cart() -> tuple[float, int]:
    llm_script = [
        {
            "content": "",
            "tool_calls": [_tool_call("tc-1", "vkusvill_products_search", {"q": "молоко"})],
        },
        {
            "content": "",
            "tool_calls": [
                _tool_call(
                    "tc-2",
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 501, "q": 1}]},
                )
            ],
        },
        {"content": "Готово"},
    ]

    def _responder(name: str, _arguments: dict[str, Any]) -> str:
        if name == "vkusvill_products_search":
            return json.dumps(
                {
                    "ok": True,
                    "data": {"items": [{"xml_id": 501, "name": "Молоко 3.2%", "price": 120}]},
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return _make_cart_payload(
                link="https://shop.example/cart/basic",
                item_line="- Молоко 3.2% x 1 = 120.00 руб",
                total=120.0,
            )
        raise AssertionError(f"Unexpected MCP tool: {name}")

    tools = [
        {
            "name": "vkusvill_products_search",
            "description": "Search products",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
        {
            "name": "vkusvill_cart_link_create",
            "description": "Create cart",
            "parameters": {"type": "object", "properties": {"products": {"type": "array"}}},
        },
    ]
    agent, _llm, mcp = _build_agent(llm_script=llm_script, mcp_responder=_responder, tools=tools)
    start = time.perf_counter()
    _ = await agent.process_message(user_id=111, text="собери корзину из молока")
    latency_ms = (time.perf_counter() - start) * 1000
    return latency_ms, len(mcp.calls)


async def _run_flow_additive_cart() -> tuple[float, int]:
    llm_script = [
        {
            "content": "",
            "tool_calls": [
                _tool_call(
                    "tc-1",
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 700, "q": 3}]},
                )
            ],
        },
        {"content": "ok"},
        {
            "content": "",
            "tool_calls": [
                _tool_call(
                    "tc-2",
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 700, "q": 1}]},
                )
            ],
        },
        {"content": "ok"},
    ]

    def _responder(name: str, arguments: dict[str, Any]) -> str:
        if name != "vkusvill_cart_link_create":
            raise AssertionError(f"Unexpected MCP tool: {name}")
        products = arguments.get("products") or []
        q = int(float(products[0]["q"])) if products else 1
        total = float(120 * q)
        return _make_cart_payload(
            link=f"https://shop.example/cart/q{q}",
            item_line=f"- Молоко 3.2% x {q} = {total:.2f} руб",
            total=total,
        )

    tools = [
        {
            "name": "vkusvill_cart_link_create",
            "description": "Create cart",
            "parameters": {"type": "object", "properties": {"products": {"type": "array"}}},
        }
    ]
    agent, _llm, mcp = _build_agent(llm_script=llm_script, mcp_responder=_responder, tools=tools)
    start = time.perf_counter()
    _ = await agent.process_message(user_id=222, text="собери корзину: молоко 3 штуки")
    _ = await agent.process_message(user_id=222, text="добавь ещё молоко")
    latency_ms = (time.perf_counter() - start) * 1000
    return latency_ms, len(mcp.calls)


async def _run_flow_recipe_cart() -> tuple[float, int]:
    llm_script = [
        {
            "content": "",
            "tool_calls": [
                _tool_call(
                    "tc-1",
                    "recipe_ingredients",
                    {"dish": "борщ", "servings": 2},
                )
            ],
        },
        {
            "content": "",
            "tool_calls": [
                _tool_call(
                    "tc-2",
                    "recipe_search",
                    {"ingredients": [{"name": "свекла", "quantity": 1, "unit": "шт"}]},
                )
            ],
        },
        {
            "content": "",
            "tool_calls": [
                _tool_call(
                    "tc-3",
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 1701, "q": 1}]},
                )
            ],
        },
        {"content": "ok"},
    ]

    def _responder(name: str, _arguments: dict[str, Any]) -> str:
        if name == "recipe_ingredients":
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {"name": "свекла", "search_query": "свекла", "quantity": 1, "unit": "шт"},
                        {"name": "соль", "search_query": "соль", "quantity": 1, "unit": "ч.л."},
                    ],
                },
                ensure_ascii=False,
            )
        if name == "recipe_search":
            return json.dumps(
                {
                    "ok": True,
                    "results": [
                        {
                            "ingredient": "свекла",
                            "best_match": {
                                "xml_id": 1701,
                                "name": "Свекла",
                                "price": {"current": 80},
                                "suggested_q": 1,
                                "unit": "шт",
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return _make_cart_payload(
                link="https://shop.example/cart/recipe",
                item_line="- Свекла x 1 = 80.00 руб",
                total=80.0,
            )
        raise AssertionError(f"Unexpected MCP tool: {name}")

    tools = [
        {
            "name": "recipe_ingredients",
            "description": "Get ingredients",
            "parameters": {"type": "object", "properties": {"dish": {"type": "string"}}},
        },
        {
            "name": "recipe_search",
            "description": "Search by ingredients",
            "parameters": {"type": "object", "properties": {"ingredients": {"type": "array"}}},
        },
        {
            "name": "vkusvill_cart_link_create",
            "description": "Create cart",
            "parameters": {"type": "object", "properties": {"products": {"type": "array"}}},
        },
    ]
    agent, _llm, mcp = _build_agent(llm_script=llm_script, mcp_responder=_responder, tools=tools)
    start = time.perf_counter()
    _ = await agent.process_message(user_id=333, text="собери корзину для борща")
    latency_ms = (time.perf_counter() - start) * 1000
    return latency_ms, len(mcp.calls)


async def _run_flow_manual_recovery() -> tuple[float, int]:
    llm_script = [
        {
            "content": "",
            "tool_calls": [_tool_call("tc-1", "vkusvill_products_search", {"q": "молоко"})],
        },
        {"content": "Перейдите на сайт и соберите корзину сами."},
        {
            "content": "",
            "tool_calls": [
                _tool_call(
                    "tc-2",
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 501, "q": 1}]},
                )
            ],
        },
        {"content": "ok"},
    ]

    def _responder(name: str, _arguments: dict[str, Any]) -> str:
        if name == "vkusvill_products_search":
            return json.dumps(
                {"ok": True, "data": {"items": [{"xml_id": 501, "name": "Молоко", "price": 120}]}},
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return _make_cart_payload(
                link="https://shop.example/cart/recovered-manual",
                item_line="- Молоко x 1 = 120.00 руб",
                total=120.0,
            )
        raise AssertionError(f"Unexpected MCP tool: {name}")

    tools = [
        {
            "name": "vkusvill_products_search",
            "description": "Search products",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
        {
            "name": "vkusvill_cart_link_create",
            "description": "Create cart",
            "parameters": {"type": "object", "properties": {"products": {"type": "array"}}},
        },
    ]
    agent, _llm, mcp = _build_agent(llm_script=llm_script, mcp_responder=_responder, tools=tools)
    start = time.perf_counter()
    _ = await agent.process_message(user_id=444, text="собери корзину из молока")
    latency_ms = (time.perf_counter() - start) * 1000
    return latency_ms, len(mcp.calls)


async def _benchmark(iterations: int) -> dict[str, Any]:
    flows: list[tuple[str, Callable[[], Any]]] = [
        ("basic_cart", _run_flow_basic_cart),
        ("additive_cart", _run_flow_additive_cart),
        ("recipe_cart", _run_flow_recipe_cart),
        ("manual_recovery", _run_flow_manual_recovery),
    ]
    report: dict[str, Any] = {"iterations": iterations, "flows": {}}

    for name, flow in flows:
        latencies: list[float] = []
        tool_calls: list[int] = []
        for _ in range(iterations):
            latency_ms, calls = await flow()
            latencies.append(latency_ms)
            tool_calls.append(calls)

        report["flows"][name] = {
            "latency_ms": {
                "mean": round(statistics.fmean(latencies), 3),
                "p50": round(_percentile(latencies, 0.5), 3),
                "p95": round(_percentile(latencies, 0.95), 3),
                "p99": round(_percentile(latencies, 0.99), 3),
                "min": round(min(latencies), 3),
                "max": round(max(latencies), 3),
            },
            "tool_calls_per_flow": {
                "mean": round(statistics.fmean(tool_calls), 3),
                "p50": round(_percentile([float(v) for v in tool_calls], 0.5), 3),
                "min": min(tool_calls),
                "max": max(tool_calls),
            },
        }
    return report


def main() -> None:
    logging.getLogger("vkuswill_bot").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument(
        "--output",
        default="ci-artifacts/agents-perf/benchmark_contract_flows.json",
    )
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(_benchmark(max(1, args.iterations)))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Benchmark report written to: {output}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
