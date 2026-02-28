"""Contract black-box tests for key ShoppingAgent user flows."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from typing import Any

import pytest

from vkuswill_bot.agents.recovery_hints import FORCE_CART_RECOVERY_HINT
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
        max_tokens: int | None,
        temperature: float | None,
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
    max_tool_calls: int = 20,
    max_input_chars_per_turn: int = 250000,
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
        max_tool_calls=max_tool_calls,
        max_input_chars_per_turn=max_input_chars_per_turn,
    )
    return agent, llm_adapter, mcp_client


@pytest.mark.asyncio
async def test_contract_builds_cart_from_search_and_returns_stable_output() -> None:
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
    progress_events: list[str] = []

    async def _on_progress(text: str) -> None:
        progress_events.append(text)

    result = await agent.process_message(
        user_id=11,
        text="собери корзину из молока",
        on_progress=_on_progress,
    )

    assert "Собрала корзину по вашему запросу." in result
    assert "1. Молоко 3.2% x 1 = 120.00 руб" in result
    assert "<b>Итого: 120.00 руб</b>" in result
    assert '<a href="https://shop.example/cart/basic">Открыть корзину</a>' in result
    assert [name for name, _ in mcp.calls] == [
        "vkusvill_products_search",
        "vkusvill_cart_link_create",
    ]
    assert progress_events[0] == "⚙️ Анализирую запрос..."
    assert any("Ищу товары" in msg for msg in progress_events)
    assert any("Формирую корзину" in msg for msg in progress_events)


@pytest.mark.asyncio
async def test_contract_additive_update_reuses_previous_quantity_when_llm_sends_one() -> None:
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

    first = await agent.process_message(user_id=12, text="собери корзину: молоко 3 штуки")
    second = await agent.process_message(user_id=12, text="добавь ещё молоко")

    assert '<a href="https://shop.example/cart/q3">Открыть корзину</a>' in first
    assert '<a href="https://shop.example/cart/q3">Открыть корзину</a>' in second
    assert len(mcp.calls) == 2
    assert mcp.calls[0][1]["products"][0]["q"] == 3
    assert mcp.calls[1][1]["products"][0]["q"] == 3


@pytest.mark.asyncio
async def test_contract_recipe_flow_filters_pantry_before_next_llm_step() -> None:
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
    agent, llm, mcp = _build_agent(llm_script=llm_script, mcp_responder=_responder, tools=tools)

    result = await agent.process_message(user_id=13, text="собери корзину для борща")

    assert '<a href="https://shop.example/cart/recipe">Открыть корзину</a>' in result
    assert [name for name, _ in mcp.calls] == [
        "recipe_ingredients",
        "recipe_search",
        "vkusvill_cart_link_create",
    ]

    second_llm_messages = llm.calls[1]["messages"]
    recipe_tool_messages = [
        msg
        for msg in second_llm_messages
        if msg.get("role") == "tool" and msg.get("name") == "recipe_ingredients"
    ]
    assert recipe_tool_messages
    sanitized_payload = json.loads(recipe_tool_messages[-1]["content"])
    ingredient_names = [
        str(item.get("name", "")).lower() for item in sanitized_payload["ingredients"]
    ]
    assert "соль" not in ingredient_names
    assert "свекла" in ingredient_names


@pytest.mark.asyncio
async def test_contract_manual_reply_triggers_recovery_and_finishes_with_cart() -> None:
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
    agent, llm, mcp = _build_agent(llm_script=llm_script, mcp_responder=_responder, tools=tools)

    result = await agent.process_message(user_id=14, text="собери корзину из молока")

    assert "Собрала корзину по вашему запросу." in result
    assert '<a href="https://shop.example/cart/recovered-manual">Открыть корзину</a>' in result
    assert [name for name, _ in mcp.calls] == [
        "vkusvill_products_search",
        "vkusvill_cart_link_create",
    ]

    third_llm_messages = llm.calls[2]["messages"]
    system_messages = [
        str(msg.get("content", ""))
        for msg in third_llm_messages
        if isinstance(msg, dict) and msg.get("role") == "system"
    ]
    assert any(FORCE_CART_RECOVERY_HINT in content for content in system_messages)


@pytest.mark.asyncio
async def test_contract_returns_too_many_steps_error_when_tool_loop_exhausted() -> None:
    llm_script = [
        {
            "content": "",
            "tool_calls": [_tool_call("tc-1", "vkusvill_products_search", {"q": "молоко"})],
        },
        {
            "content": "",
            "tool_calls": [_tool_call("tc-2", "vkusvill_products_search", {"q": "кефир"})],
        },
    ]

    def _responder(name: str, _arguments: dict[str, Any]) -> str:
        if name != "vkusvill_products_search":
            raise AssertionError(f"Unexpected MCP tool: {name}")
        return json.dumps({"ok": True, "data": {"items": []}}, ensure_ascii=False)

    tools = [
        {
            "name": "vkusvill_products_search",
            "description": "Search products",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]
    agent, _llm, mcp = _build_agent(
        llm_script=llm_script,
        mcp_responder=_responder,
        tools=tools,
        max_tool_calls=2,
    )

    result = await agent.process_message(user_id=21, text="собери корзину")

    assert "в пределах лимита шагов" in result
    assert [name for name, _ in mcp.calls] == [
        "vkusvill_products_search",
        "vkusvill_products_search",
    ]


@pytest.mark.asyncio
async def test_contract_returns_too_many_steps_error_when_prompt_budget_exceeded() -> None:
    llm_script = [
        {
            "content": "",
            "tool_calls": [_tool_call("tc-1", "vkusvill_products_search", {"q": "молоко"})],
        }
    ]

    def _responder(name: str, _arguments: dict[str, Any]) -> str:
        if name != "vkusvill_products_search":
            raise AssertionError(f"Unexpected MCP tool: {name}")
        return json.dumps({"ok": True, "data": {"items": []}}, ensure_ascii=False)

    tools = [
        {
            "name": "vkusvill_products_search",
            "description": "Search products",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]
    agent, llm, mcp = _build_agent(
        llm_script=llm_script,
        mcp_responder=_responder,
        tools=tools,
        max_tool_calls=5,
        max_input_chars_per_turn=10000,
    )

    huge_text = "собери корзину " + ("молоко " * 2000)
    result = await agent.process_message(user_id=22, text=huge_text)

    assert "в пределах лимита шагов" in result
    assert len(llm.calls) == 1
    assert [name for name, _ in mcp.calls] == ["vkusvill_products_search"]


@pytest.mark.asyncio
async def test_contract_returns_generic_error_on_llm_failure() -> None:
    llm_script: list[dict[str, Any] | Exception] = [RuntimeError("llm is down")]

    def _responder(name: str, _arguments: dict[str, Any]) -> str:
        raise AssertionError(f"MCP should not be called, got: {name}")

    tools = [
        {
            "name": "vkusvill_products_search",
            "description": "Search products",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]
    agent, _llm, mcp = _build_agent(llm_script=llm_script, mcp_responder=_responder, tools=tools)

    result = await agent.process_message(user_id=23, text="собери корзину")

    assert result == "Не удалось обработать запрос. Попробуйте позже."
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_contract_mcp_failure_degrades_and_exposes_mcp_error_to_next_step() -> None:
    llm_script = [
        {
            "content": "",
            "tool_calls": [_tool_call("tc-1", "vkusvill_products_search", {"q": "молоко"})],
        },
        {"content": "Не удалось подобрать товары, уточните запрос."},
    ]

    def _responder(name: str, _arguments: dict[str, Any]) -> str:
        raise RuntimeError("mcp failure")

    tools = [
        {
            "name": "vkusvill_products_search",
            "description": "Search products",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]
    agent, llm, mcp = _build_agent(llm_script=llm_script, mcp_responder=_responder, tools=tools)

    result = await agent.process_message(user_id=24, text="найди молоко")

    assert result == "Не удалось подобрать товары, уточните запрос."
    assert len(mcp.calls) >= 1
    assert all(name == "vkusvill_products_search" for name, _ in mcp.calls)

    second_call_messages = llm.calls[1]["messages"]
    tool_messages = [msg for msg in second_call_messages if msg.get("role") == "tool"]
    assert tool_messages
    payload = json.loads(tool_messages[-1]["content"])
    assert payload["ok"] is False
