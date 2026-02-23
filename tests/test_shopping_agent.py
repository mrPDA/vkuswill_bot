"""Unit-тесты ShoppingAgent (вариант E)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from vkuswill_bot.agents.shopping_agent import ShoppingAgent


class _FakeDialogManager:
    def __init__(self) -> None:
        self._locks: dict[int, Any] = {}

    def get_lock(self, user_id: int) -> Any:
        import asyncio

        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]


class _FakeMCPClient:
    def __init__(self, *, tool_result: str = '{"ok": true}', fail: bool = False) -> None:
        self.tool_result = tool_result
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "vkusvill_products_search",
                "description": "Search products",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            {
                "name": "vkusvill_cart_link_create",
                "description": "Create cart",
                "parameters": {"type": "object", "properties": {"products": {"type": "array"}}},
            },
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        if self.fail:
            raise RuntimeError("mcp failure")
        return self.tool_result


class _FallbackMCPClient(_FakeMCPClient):
    def __init__(self) -> None:
        super().__init__(tool_result='{"ok": true}')

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        if name == "recipe_ingredients":
            raise RuntimeError(
                "MCP JSON-RPC error -32601: Method not found: Tool 'recipe_ingredients'"
            )
        if name == "recipe_search":
            raise RuntimeError("MCP JSON-RPC error -32601: Method not found: Tool 'recipe_search'")
        if name == "vkusvill_products_search":
            return json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {
                                "xml_id": 111,
                                "name": "Свекла",
                                "price": 54,
                                "unit": "кг",
                            },
                            {
                                "xml_id": 222,
                                "name": "Свекла мытая",
                                "price": 65,
                                "unit": "кг",
                            },
                        ]
                    },
                },
                ensure_ascii=False,
            )
        return self.tool_result


class _CartLinkOnlyMCPClient(_FakeMCPClient):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        if name == "vkusvill_products_search":
            return json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {"xml_id": 1001, "name": "Яйцо куриное С1", "price": 120, "unit": "шт"},
                            {
                                "xml_id": 1002,
                                "name": "Масло сливочное 82,5%, 200 г",
                                "price": 282,
                                "unit": "шт",
                            },
                        ]
                    },
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://vkusvill.ru/?share_basket=123456789"}},
                ensure_ascii=False,
            )
        return self.tool_result


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, tool_call_id: str, name: str, arguments: str) -> None:
        self.id = tool_call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[_FakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = scripted
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._scripted:
            raise RuntimeError("No scripted LLM response")
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeLLMClient:
    def __init__(self, scripted: list[Any]) -> None:
        self.completions = _FakeCompletions(scripted)
        self.chat = SimpleNamespace(completions=self.completions)

    async def close(self) -> None:
        return None


class _FakeLLMAdapter:
    def __init__(
        self,
        *,
        text: str,
        delay_seconds: float = 0.0,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.text = text
        self.delay_seconds = delay_seconds
        self.usage = usage
        self.calls: list[dict[str, Any]] = []

    async def create_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self.delay_seconds > 0:
            import asyncio

            await asyncio.sleep(self.delay_seconds)
        response: dict[str, Any] = {
            "choices": [
                {
                    "message": {
                        "content": self.text,
                        "tool_calls": [],
                    }
                }
            ]
        }
        if self.usage is not None:
            response["usage"] = self.usage
        return response

    async def close(self) -> None:
        return None


class _LangfuseGenSpy:
    def __init__(self) -> None:
        self.end_calls: list[dict[str, Any]] = []

    def end(self, **kwargs: Any) -> None:
        self.end_calls.append(kwargs)


class _LangfuseTraceSpy:
    def __init__(self) -> None:
        self.gen = _LangfuseGenSpy()
        self.updates: list[dict[str, Any]] = []

    def generation(self, **_kwargs: Any) -> _LangfuseGenSpy:
        return self.gen

    def span(self, **_kwargs: Any) -> Any:
        class _Span:
            def end(self, **_kwargs: Any) -> None:
                return None

        return _Span()

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class _LangfuseServiceSpy:
    def __init__(self) -> None:
        self.trace_spy = _LangfuseTraceSpy()

    def trace(self, **_kwargs: Any) -> _LangfuseTraceSpy:
        return self.trace_spy


def _agent(
    *,
    llm_script: list[Any],
    mcp_client: _FakeMCPClient | None = None,
    max_tool_calls: int = 5,
) -> tuple[ShoppingAgent, _FakeMCPClient]:
    mcp = mcp_client or _FakeMCPClient()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        max_tool_calls=max_tool_calls,
        max_history=20,
        llm_client=_FakeLLMClient(llm_script),
    )
    return agent, mcp


@pytest.mark.asyncio
async def test_text_only_response() -> None:
    agent, _mcp = _agent(llm_script=[_FakeResponse(_FakeMessage(content="Готово"))])
    result = await agent.process_message(user_id=42, text="привет")
    assert result == "Готово"


@pytest.mark.asyncio
async def test_tool_call_then_final_text_and_snapshot() -> None:
    cart_payload = json.dumps(
        {
            "ok": True,
            "data": {
                "link": "https://shop.example/cart/1",
                "price_summary": {"total": 430.0},
            },
        },
        ensure_ascii=False,
    )
    mcp = _FakeMCPClient(tool_result=cart_payload)
    tool_call = _FakeToolCall(
        "tc-1",
        "vkusvill_cart_link_create",
        '{"products":[{"xml_id":1,"q":1}]}',
    )
    llm_script = [
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
        _FakeResponse(_FakeMessage(content="Корзина собрана")),
    ]
    agent, mcp_client = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=42, text="собери корзину")
    assert "Собрала корзину по вашему запросу." in result
    assert "Итого: 430.00 руб" in result
    assert '<a href="https://shop.example/cart/1">Открыть корзину</a>' in result
    assert mcp_client.calls == [
        ("vkusvill_cart_link_create", {"products": [{"xml_id": 1, "q": 1}]})
    ]

    snapshot = await agent.get_last_cart_snapshot(42)
    assert snapshot is not None
    assert snapshot["link"] == "https://shop.example/cart/1"
    assert snapshot["total"] == 430.0
    assert snapshot["products"] == [{"xml_id": 1, "q": 1}]


@pytest.mark.asyncio
async def test_stabilize_when_final_reply_contains_wrong_cart_link() -> None:
    cart_payload = json.dumps(
        {
            "ok": True,
            "data": {
                "link": "https://shop.example/cart/42",
                "price_summary": {
                    "total": 310.0,
                    "total_text": "Итого: 310.00 руб",
                    "items": [
                        "- Яйцо куриное x 4 = 220.00 руб",
                        "- Масло сливочное x 1 = 90.00 руб",
                    ],
                },
            },
        },
        ensure_ascii=False,
    )
    mcp = _FakeMCPClient(tool_result=cart_payload)
    tool_call = _FakeToolCall(
        "tc-1",
        "vkusvill_cart_link_create",
        '{"products":[{"xml_id":1,"q":4},{"xml_id":2,"q":1}]}',
    )
    llm_script = [
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
        _FakeResponse(
            _FakeMessage(
                content=(
                    "Готово! Собрала корзину.\n"
                    '<a href="https://vkusvill.ru/cart">Открыть корзину</a>'
                )
            )
        ),
    ]
    agent, _mcp = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=42, text="собери корзину")
    assert "https://vkusvill.ru/cart" not in result
    assert '<a href="https://shop.example/cart/42">Открыть корзину</a>' in result
    assert "Итого: 310.00 руб" in result


@pytest.mark.asyncio
async def test_builds_price_summary_when_cart_response_has_only_link() -> None:
    mcp = _CartLinkOnlyMCPClient(tool_result='{"ok": true}')
    llm_script = [
        _FakeResponse(
            _FakeMessage(
                content="",
                tool_calls=[
                    _FakeToolCall(
                        "tc-1",
                        "vkusvill_products_search",
                        '{"q":"яйцо куриное","limit":5}',
                    )
                ],
            )
        ),
        _FakeResponse(
            _FakeMessage(
                content="",
                tool_calls=[
                    _FakeToolCall(
                        "tc-2",
                        "vkusvill_cart_link_create",
                        '{"products":[{"xml_id":1001,"q":4},{"xml_id":1002,"q":0.5}]}',
                    )
                ],
            )
        ),
        _FakeResponse(_FakeMessage(content="Собрала корзину по вашему запросу.")),
    ]
    agent, _ = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=200, text="собери яичницу")
    assert "Яйцо куриное С1 x 4 = 480.00 руб" in result
    assert "Масло сливочное 82,5%, 200 г x 0.5 = 141.00 руб" in result
    assert "Итого: 621.00 руб" in result
    assert '<a href="https://vkusvill.ru/?share_basket=123456789">Открыть корзину</a>' in result


@pytest.mark.asyncio
async def test_forces_cart_tool_call_when_llm_claims_cart_ready_without_tool_result() -> None:
    cart_payload = json.dumps(
        {
            "ok": True,
            "data": {
                "link": "https://shop.example/cart/ready-1",
                "price_summary": {
                    "total": 259.0,
                    "total_text": "Итого: 259.00 руб",
                    "items": ["- Молоко 3,2% x 1 = 99.00 руб", "- Яйца x 1 = 160.00 руб"],
                },
            },
        },
        ensure_ascii=False,
    )
    mcp = _FakeMCPClient(tool_result=cart_payload)
    llm_script = [
        _FakeResponse(_FakeMessage(content="Собрала корзину. Открыть корзину: https://vkusvill.ru/cart")),
        _FakeResponse(
            _FakeMessage(
                content="",
                tool_calls=[
                    _FakeToolCall(
                        "tc-1",
                        "vkusvill_cart_link_create",
                        '{"products":[{"xml_id":1,"q":1},{"xml_id":2,"q":1}]}',
                    )
                ],
            )
        ),
        _FakeResponse(_FakeMessage(content="Готово.")),
    ]
    agent, mcp_client = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=333, text="закажи молоко и яйца")
    assert "https://vkusvill.ru/cart" not in result
    assert '<a href="https://shop.example/cart/ready-1">Открыть корзину</a>' in result
    assert "Итого: 259.00 руб" in result
    assert mcp_client.calls == [
        (
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 1, "q": 1}, {"xml_id": 2, "q": 1}]},
        )
    ]


@pytest.mark.asyncio
async def test_manual_cart_reply_is_retried_and_stabilized() -> None:
    cart_payload = json.dumps(
        {
            "ok": True,
            "data": {
                "link": "https://shop.example/cart/2",
                "price_summary": {
                    "total": 259.0,
                    "total_text": "Итого: 259.00 руб",
                    "items": [
                        "- Молоко 3,2%: 99 руб/шт x 1 = 99.00 руб",
                        "- Яйца: 160 руб/уп x 1 = 160.00 руб",
                    ],
                },
            },
        },
        ensure_ascii=False,
    )
    mcp = _FakeMCPClient(tool_result=cart_payload)
    tool_call = _FakeToolCall(
        "tc-1",
        "vkusvill_cart_link_create",
        '{"products":[{"xml_id":1,"q":1},{"xml_id":2,"q":1}]}',
    )
    llm_script = [
        _FakeResponse(_FakeMessage(content="Соберите корзину вручную в приложении")),
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
        _FakeResponse(_FakeMessage(content="Оформите заказ самостоятельно на сайте")),
    ]
    agent, mcp_client = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=42, text="собери корзину из молока и яиц")
    assert "вручную" not in result.lower()
    assert "самостоятельно" not in result.lower()
    assert "Итого: 259.00 руб" in result
    assert '<a href="https://shop.example/cart/2">Открыть корзину</a>' in result
    assert mcp_client.calls == [
        (
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 1, "q": 1}, {"xml_id": 2, "q": 1}]},
        )
    ]


@pytest.mark.asyncio
async def test_stabilize_wrong_zero_items_summary_after_cart_created() -> None:
    cart_payload = json.dumps(
        {
            "ok": True,
            "data": {
                "link": "https://shop.example/cart/3",
                "price_summary": {
                    "total": 99.0,
                    "total_text": "Итого: 99.00 руб",
                    "items": ["- Молоко: 99 руб/шт x 1 = 99.00 руб"],
                },
            },
        },
        ensure_ascii=False,
    )
    mcp = _FakeMCPClient(tool_result=cart_payload)
    tool_call = _FakeToolCall(
        "tc-1",
        "vkusvill_cart_link_create",
        '{"products":[{"xml_id":1,"q":1}]}',
    )
    llm_script = [
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
        _FakeResponse(_FakeMessage(content="Готово. Собрала 0 товаров.")),
    ]
    agent, _mcp = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=42, text="закажи молоко")
    assert "0 товаров" not in result
    assert "Молоко: 99 руб/шт x 1 = 99.00 руб" in result
    assert '<a href="https://shop.example/cart/3">Открыть корзину</a>' in result


@pytest.mark.asyncio
async def test_stabilize_when_final_reply_misses_item_prices() -> None:
    cart_payload = json.dumps(
        {
            "ok": True,
            "data": {
                "link": "https://shop.example/cart/4",
                "price_summary": {
                    "total": 643.0,
                    "total_text": "Итого: 643.00 руб",
                    "items": [
                        "- Яйцо куриное С0 Жёлтики 6 шт x 4 = 404.00 руб",
                        "- Масло сливочное 82,5%, 100 г x 1 = 198.00 руб",
                        "- Соль Славяна помол №1 пакет 1 кг x 1 = 41.00 руб",
                    ],
                },
            },
        },
        ensure_ascii=False,
    )
    mcp = _FakeMCPClient(tool_result=cart_payload)
    tool_call = _FakeToolCall(
        "tc-1",
        "vkusvill_cart_link_create",
        '{"products":[{"xml_id":1,"q":4},{"xml_id":2,"q":1},{"xml_id":3,"q":1}]}',
    )
    llm_script = [
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
        _FakeResponse(
            _FakeMessage(content="Собрала корзину: яйца, сливочное масло и соль. Корзина готова.")
        ),
    ]
    agent, _mcp = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=77, text="собери корзину для яичницы")
    assert "x 4 = 404.00 руб" in result
    assert "x 1 = 198.00 руб" in result
    assert "x 1 = 41.00 руб" in result
    assert "Итого: 643.00 руб" in result
    assert '<a href="https://shop.example/cart/4">Открыть корзину</a>' in result


@pytest.mark.asyncio
async def test_tool_result_compacted_in_history_for_next_llm_call() -> None:
    large_items = [
        {
            "xml_id": idx,
            "name": f"Очень длинное название товара номер {idx} " + ("X" * 120),
            "price": 100 + idx,
            "unit": "шт",
            "rating": {"average": 4.9},
        }
        for idx in range(1, 80)
    ]
    large_tool_result = json.dumps(
        {
            "ok": True,
            "data": {
                "meta": {"q": "борщ", "total": len(large_items), "page": 1, "pages": 8},
                "items": large_items,
            },
        },
        ensure_ascii=False,
    )
    mcp = _FakeMCPClient(tool_result=large_tool_result)
    tool_call = _FakeToolCall("tc-1", "vkusvill_products_search", '{"q":"борщ"}')
    llm_client = _FakeLLMClient(
        [
            _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
            _FakeResponse(_FakeMessage(content="ok")),
        ]
    )
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        max_tool_result_chars=1200,
        max_history=20,
    )

    result = await agent.process_message(user_id=42, text="собери борщ")
    assert result == "ok"
    assert len(llm_client.completions.calls) == 2

    second_call_messages = llm_client.completions.calls[1]["messages"]
    tool_messages = [msg for msg in second_call_messages if msg.get("role") == "tool"]
    assert tool_messages, "Второй вызов LLM должен содержать tool-result"
    compact_content = tool_messages[-1]["content"]
    assert isinstance(compact_content, str)
    assert len(compact_content) <= 1200
    assert len(compact_content) < len(large_tool_result)

    compact_payload = json.loads(compact_content)
    assert compact_payload["ok"] is True
    assert compact_payload["meta"]["q"] == "борщ"
    assert 1 <= len(compact_payload["items"]) <= 5


@pytest.mark.asyncio
async def test_invalid_tool_args_fallback_to_empty_dict() -> None:
    mcp = _FakeMCPClient(tool_result='{"ok": true}')
    tool_call = _FakeToolCall("tc-1", "vkusvill_products_search", "{bad-json")
    llm_script = [
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
        _FakeResponse(_FakeMessage(content="ok")),
    ]
    agent, mcp_client = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=42, text="найди молоко")
    assert result == "ok"
    assert mcp_client.calls == [("vkusvill_products_search", {})]


@pytest.mark.asyncio
async def test_cart_tool_args_autofill_q_when_missing() -> None:
    mcp = _FakeMCPClient(tool_result='{"ok": true}')
    tool_call = _FakeToolCall(
        "tc-1",
        "vkusvill_cart_link_create",
        '{"products":[{"xml_id":29566},{"xml_id":37589}]}',
    )
    llm_script = [
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
        _FakeResponse(_FakeMessage(content="ok")),
    ]
    agent, mcp_client = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=42, text="собери корзину")
    assert result == "ok"
    assert mcp_client.calls == [
        (
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 29566, "q": 1}, {"xml_id": 37589, "q": 1}]},
        )
    ]


@pytest.mark.asyncio
async def test_mcp_failure_returns_fallback_tool_result_and_continue() -> None:
    mcp = _FakeMCPClient(fail=True)
    tool_call = _FakeToolCall("tc-1", "vkusvill_products_search", '{"query":"молоко"}')
    llm_script = [
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call])),
        _FakeResponse(_FakeMessage(content="Не удалось найти товары")),
    ]
    agent, _mcp = _agent(llm_script=llm_script, mcp_client=mcp)

    result = await agent.process_message(user_id=42, text="найди молоко")
    assert "Не удалось" in result


@pytest.mark.asyncio
async def test_llm_failure_returns_generic_error() -> None:
    agent, _mcp = _agent(llm_script=[RuntimeError("llm down")])
    result = await agent.process_message(user_id=42, text="собери корзину")
    assert result == "Не удалось обработать запрос. Попробуйте позже."


@pytest.mark.asyncio
async def test_max_tool_calls_guard() -> None:
    tool_call_1 = _FakeToolCall("tc-1", "vkusvill_products_search", '{"query":"молоко"}')
    tool_call_2 = _FakeToolCall("tc-2", "vkusvill_products_search", '{"query":"яйца"}')
    llm_script = [
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call_1])),
        _FakeResponse(_FakeMessage(content="", tool_calls=[tool_call_2])),
    ]
    agent, _mcp = _agent(llm_script=llm_script, max_tool_calls=2)

    result = await agent.process_message(user_id=42, text="собери корзину")
    assert "в пределах лимита шагов" in result


@pytest.mark.asyncio
async def test_routing_single_user_gigachat_multi_user_qwen() -> None:
    mcp = _FakeMCPClient()
    giga_adapter = _FakeLLMAdapter(text="Ответ Giga", delay_seconds=0.15)
    qwen_adapter = _FakeLLMAdapter(text="Ответ Qwen")
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/qwen-model/latest",
        llm_max_concurrent=4,
        llm_provider="qwen_openai",
        llm_routing_strategy="single_user_gigachat_multi_user_qwen",
        llm_singleton_provider="gigachat_sdk",
        llm_burst_provider="qwen_openai",
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        gigachat_model="GigaChat-2-Max",
        llm_adapters={
            "gigachat_sdk": giga_adapter,
            "qwen_openai": qwen_adapter,
        },
    )

    import asyncio

    t1 = asyncio.create_task(agent.process_message(user_id=1, text="первый запрос"))
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(agent.process_message(user_id=2, text="второй запрос"))
    r1, r2 = await asyncio.gather(t1, t2)

    assert r1 == "Ответ Giga"
    assert r2 == "Ответ Qwen"
    assert len(giga_adapter.calls) == 1
    assert len(qwen_adapter.calls) == 1
    assert giga_adapter.calls[0]["model"] == "GigaChat-2-Max"
    assert qwen_adapter.calls[0]["model"] == "gpt://folder/qwen-model/latest"


@pytest.mark.asyncio
async def test_forwards_llm_max_tokens_and_temperature() -> None:
    mcp = _FakeMCPClient()
    adapter = _FakeLLMAdapter(text="ok")
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/qwen-model/latest",
        llm_max_concurrent=2,
        llm_provider="qwen_openai",
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_adapters={"qwen_openai": adapter},
        llm_max_tokens=900,
        llm_temperature=0.2,
    )

    result = await agent.process_message(user_id=99, text="привет")
    assert result == "ok"
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["max_tokens"] == 900
    assert adapter.calls[0]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_recipe_ingredients_method_not_found_uses_llm_fallback() -> None:
    mcp = _FallbackMCPClient()
    llm_adapter = _FakeLLMAdapter(
        text='[{"name":"свёкла","quantity":1,"unit":"шт","search_query":"свекла"}]'
    )
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/qwen-model/latest",
        llm_max_concurrent=2,
        llm_provider="qwen_openai",
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_adapters={"qwen_openai": llm_adapter},
    )

    raw = await agent._call_mcp_tool(
        name="recipe_ingredients",
        arguments={"dish": "борщ", "servings": 2},
        llm_provider="qwen_openai",
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["source"] == "shopping_agent_fallback"
    assert len(payload["ingredients"]) >= 1
    assert payload["ingredients"][0]["search_query"] == "свекла"


@pytest.mark.asyncio
async def test_recipe_search_method_not_found_uses_local_search_fallback() -> None:
    mcp = _FallbackMCPClient()
    llm_adapter = _FakeLLMAdapter(text="ok")
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/qwen-model/latest",
        llm_max_concurrent=2,
        llm_provider="qwen_openai",
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_adapters={"qwen_openai": llm_adapter},
    )

    raw = await agent._call_mcp_tool(
        name="recipe_search",
        arguments={
            "ingredients": [
                {
                    "name": "свёкла",
                    "quantity": 0.5,
                    "unit": "кг",
                    "search_query": "свекла",
                    "kg_equivalent": 0.5,
                }
            ]
        },
        llm_provider="qwen_openai",
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["source"] == "shopping_agent_fallback"
    assert payload["results"][0]["best_match"]["xml_id"] == 111
    assert payload["results"][0]["best_match"]["suggested_q"] == 0.5
    assert payload["data"]["found"][0]["item"]["xml_id"] == 111
    assert payload["data"]["found"][0]["suggested_q"] == 0.5


@pytest.mark.asyncio
async def test_recipe_search_fallback_accepts_string_ingredients() -> None:
    mcp = _FallbackMCPClient()
    llm_adapter = _FakeLLMAdapter(text="ok")
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/qwen-model/latest",
        llm_max_concurrent=2,
        llm_provider="qwen_openai",
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_adapters={"qwen_openai": llm_adapter},
    )

    raw = await agent._call_mcp_tool(
        name="recipe_search",
        arguments={"ingredients": ["говядина 300 г", "свекла 2 шт"]},
        llm_provider="qwen_openai",
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert len(payload["results"]) >= 1
    assert payload["results"][0]["best_match"]["xml_id"] == 111
    assert payload["data"]["found"][0]["item"]["xml_id"] == 111


@pytest.mark.asyncio
async def test_compact_recipe_search_handles_top_level_results_shape() -> None:
    agent, _mcp = _agent(llm_script=[_FakeResponse(_FakeMessage(content="ok"))])
    payload = {
        "ok": True,
        "results": [
            {
                "ingredient": "спагетти",
                "best_match": {
                    "xml_id": 781,
                    "name": 'Макароны "Спагетти"',
                    "price": {"current": 89, "currency": "RUB"},
                    "suggested_q": 1,
                },
            },
            {
                "ingredient": "бекон",
                "best_match": {
                    "xml_id": 103297,
                    "name": "Бекон сырокопченый",
                    "price": {"current": 189, "currency": "RUB"},
                    "suggested_q": 1,
                },
            },
        ],
        "not_found": ["черный перец"],
    }

    compact = agent._compact_recipe_search(payload)
    assert compact["ok"] is True
    assert len(compact["found"]) == 2
    assert compact["found"][0]["xml_id"] == 781
    assert compact["found"][0]["suggested_q"] == 1
    assert compact["found"][1]["xml_id"] == 103297
    assert compact["not_found"] == ["черный перец"]


def test_trim_history_recompacts_legacy_tool_messages() -> None:
    agent, _mcp = _agent(llm_script=[_FakeResponse(_FakeMessage(content="ok"))])
    agent._max_tool_result_chars = 500
    huge_tool_payload = json.dumps(
        {
            "ok": True,
            "data": {
                "meta": {"q": "молоко", "total": 10, "page": 1, "pages": 1},
                "items": [
                    {
                        "xml_id": idx,
                        "name": f"Молоко очень длинное название {idx} " + ("X" * 200),
                        "price": 100 + idx,
                        "unit": "шт",
                    }
                    for idx in range(10)
                ],
            },
        },
        ensure_ascii=False,
    )
    history = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc-1", "type": "function", "function": {"name": "x"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "tc-1",
            "name": "vkusvill_products_search",
            "content": huge_tool_payload,
        },
    ]

    trimmed = agent._trim_history_by_chars(history)
    tool_messages = [msg for msg in trimmed if msg.get("role") == "tool"]
    assert tool_messages
    assert len(tool_messages[0]["content"]) <= 500


def test_extract_usage_details_from_normalized_dict() -> None:
    agent, _mcp = _agent(llm_script=[_FakeResponse(_FakeMessage(content="ok"))])
    usage = agent._extract_usage_details(
        {
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "usage": {"input": 101, "output": 19, "total": 120},
        }
    )
    assert usage == {"input": 101, "output": 19, "total": 120}


def test_extract_usage_details_from_alt_qwen_shape() -> None:
    agent, _mcp = _agent(llm_script=[_FakeResponse(_FakeMessage(content="ok"))])
    usage = agent._extract_usage_details(
        {
            "usage": {
                "input_tokens": "88",
                "outputTokens": 12,
            }
        }
    )
    assert usage == {"input": 88, "output": 12, "total": 100}


@pytest.mark.asyncio
async def test_langfuse_generation_receives_estimated_usage_when_provider_usage_missing() -> None:
    mcp = _FakeMCPClient()
    llm_adapter = _FakeLLMAdapter(text="ok", usage=None)
    langfuse = _LangfuseServiceSpy()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/qwen-model/latest",
        llm_max_concurrent=2,
        llm_provider="qwen_openai",
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_adapters={"qwen_openai": llm_adapter},
        langfuse_service=langfuse,  # type: ignore[arg-type]
    )

    result = await agent.process_message(user_id=7, text="привет")
    assert result == "ok"
    assert langfuse.trace_spy.gen.end_calls
    end_call = langfuse.trace_spy.gen.end_calls[-1]
    usage_details = end_call.get("usage_details")
    assert isinstance(usage_details, dict)
    assert usage_details["input"] > 0
    assert usage_details["output"] > 0
    assert usage_details["total"] == usage_details["input"] + usage_details["output"]
    assert end_call["metadata"]["usage_source"] == "estimated"


@pytest.mark.asyncio
async def test_langfuse_generation_marks_provider_usage_when_present() -> None:
    mcp = _FakeMCPClient()
    llm_adapter = _FakeLLMAdapter(text="ok", usage={"input": 10, "output": 3, "total": 13})
    langfuse = _LangfuseServiceSpy()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/qwen-model/latest",
        llm_max_concurrent=2,
        llm_provider="qwen_openai",
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_adapters={"qwen_openai": llm_adapter},
        langfuse_service=langfuse,  # type: ignore[arg-type]
    )

    result = await agent.process_message(user_id=8, text="привет")
    assert result == "ok"
    end_call = langfuse.trace_spy.gen.end_calls[-1]
    assert end_call["usage_details"] == {"input": 10, "output": 3, "total": 13}
    assert end_call["metadata"]["usage_source"] == "provider"


@pytest.mark.asyncio
async def test_prompt_profile_and_compact_followup_are_applied_per_step() -> None:
    mcp = _FakeMCPClient(tool_result='{"ok": true}')
    llm_client = _FakeLLMClient(
        [
            _FakeResponse(
                _FakeMessage(
                    content="",
                    tool_calls=[
                        _FakeToolCall(
                            "tc-1",
                            "vkusvill_products_search",
                            '{"query":"молоко"}',
                        )
                    ],
                )
            ),
            _FakeResponse(_FakeMessage(content="ok")),
        ]
    )
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        compact_followup_prompt_enabled=True,
    )

    result = await agent.process_message(user_id=123, text="закажи молоко")
    assert result == "ok"
    assert len(llm_client.completions.calls) == 2
    first_system = llm_client.completions.calls[0]["messages"][0]["content"]
    second_system = llm_client.completions.calls[1]["messages"][0]["content"]
    assert "[PROMPT_PROFILE:cart]" in first_system
    assert "[PROMPT_MODE:compact_followup]" not in first_system
    assert "[PROMPT_PROFILE:cart]" in second_system
    assert "[PROMPT_MODE:compact_followup]" in second_system


@pytest.mark.asyncio
async def test_compact_followup_prompt_can_be_disabled() -> None:
    mcp = _FakeMCPClient(tool_result='{"ok": true}')
    llm_client = _FakeLLMClient(
        [
            _FakeResponse(
                _FakeMessage(
                    content="",
                    tool_calls=[
                        _FakeToolCall(
                            "tc-1",
                            "vkusvill_products_search",
                            '{"query":"молоко"}',
                        )
                    ],
                )
            ),
            _FakeResponse(_FakeMessage(content="ok")),
        ]
    )
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        compact_followup_prompt_enabled=False,
    )

    result = await agent.process_message(user_id=124, text="закажи молоко")
    assert result == "ok"
    second_system = llm_client.completions.calls[1]["messages"][0]["content"]
    assert "[PROMPT_PROFILE:cart]" in second_system
    assert "[PROMPT_MODE:compact_followup]" not in second_system


@pytest.mark.asyncio
async def test_final_step_uses_finalize_prompt_mode_after_cart_created() -> None:
    cart_payload = json.dumps(
        {
            "ok": True,
            "data": {
                "link": "https://shop.example/cart/final",
                "price_summary": {
                    "total": 99.0,
                    "total_text": "Итого: 99.00 руб",
                    "items": ["- Молоко x 1 = 99.00 руб"],
                },
            },
        },
        ensure_ascii=False,
    )
    mcp = _FakeMCPClient(tool_result=cart_payload)
    llm_client = _FakeLLMClient(
        [
            _FakeResponse(
                _FakeMessage(
                    content="",
                    tool_calls=[
                        _FakeToolCall(
                            "tc-1",
                            "vkusvill_cart_link_create",
                            '{"products":[{"xml_id":1,"q":1}]}',
                        )
                    ],
                )
            ),
            _FakeResponse(_FakeMessage(content="ok")),
        ]
    )
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        compact_followup_prompt_enabled=True,
    )

    result = await agent.process_message(user_id=125, text="закажи молоко")
    assert "Итого: 99.00 руб" in result
    assert len(llm_client.completions.calls) == 2
    second_system = llm_client.completions.calls[1]["messages"][0]["content"]
    assert "[PROMPT_PROFILE:cart]" in second_system
    assert "[PROMPT_MODE:finalize]" in second_system
