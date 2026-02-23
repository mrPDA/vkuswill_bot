"""Unit tests for provider-specific LLM adapters."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from gigachat.models import MessagesRole

from vkuswill_bot.services.llm_adapters import (
    GigaChatLLMAdapter,
    OpenAICompatibleLLMAdapter,
    create_llm_adapter,
    normalize_llm_provider,
)


class _FakeOpenAICompletions:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response


class _FakeOpenAIClient:
    def __init__(self, response: Any) -> None:
        self.completions = _FakeOpenAICompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)

    async def close(self) -> None:
        return None


class _FakeGigaClient:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[Any] = []

    def chat(self, payload: Any) -> Any:
        self.calls.append(payload)
        return self._response


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str, tool_calls: list[_FakeToolCall]) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeOpenAIResponse:
    def __init__(self) -> None:
        self.choices = [
            _FakeChoice(
                _FakeMessage(
                    content="Готово",
                    tool_calls=[
                        _FakeToolCall(
                            "tc-1",
                            "vkusvill_products_search",
                            '{"query":"молоко"}',
                        )
                    ],
                )
            )
        ]
        self.usage = SimpleNamespace(prompt_tokens=123, completion_tokens=45, total_tokens=168)


@pytest.mark.asyncio
async def test_openai_adapter_normalizes_response_shape() -> None:
    adapter = OpenAICompatibleLLMAdapter(client=_FakeOpenAIClient(_FakeOpenAIResponse()))
    result = await adapter.create_completion(
        model="gpt://folder/model/latest",
        messages=[{"role": "user", "content": "найди молоко"}],
        tools=[],
        tool_choice="auto",
    )

    message = result["choices"][0]["message"]
    assert message["content"] == "Готово"
    assert message["tool_calls"][0]["id"] == "tc-1"
    assert message["tool_calls"][0]["function"]["name"] == "vkusvill_products_search"
    assert result["usage"] == {"input": 123, "output": 45, "total": 168}


@pytest.mark.asyncio
async def test_gigachat_adapter_builds_payload_and_normalizes_function_call() -> None:
    gc_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Ищу товары",
                    function_call=SimpleNamespace(
                        name="vkusvill_products_search",
                        arguments='{"query":"батон"}',
                    ),
                )
            )
        ]
    )
    fake_client = _FakeGigaClient(gc_response)
    adapter = GigaChatLLMAdapter(client=fake_client)

    result = await adapter.create_completion(
        model="GigaChat-2-Max",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "найди батон"},
            {
                "role": "tool",
                "name": "vkusvill_products_search",
                "tool_call_id": "tc-prev",
                "content": '{"ok":true}',
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "vkusvill_products_search",
                    "description": "Search products",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            }
        ],
    )

    assert len(fake_client.calls) == 1
    payload = fake_client.calls[0]
    assert payload.model == "GigaChat-2-Max"
    assert payload.function_call == "auto"
    assert payload.messages[0].role == MessagesRole.SYSTEM
    assert payload.messages[1].role == MessagesRole.USER
    assert payload.messages[2].role == MessagesRole.FUNCTION
    assert payload.messages[2].name == "vkusvill_products_search"
    assert payload.functions[0].name == "vkusvill_products_search"

    message = result["choices"][0]["message"]
    assert message["content"] == "Ищу товары"
    assert message["tool_calls"][0]["function"]["name"] == "vkusvill_products_search"
    assert message["tool_calls"][0]["id"].startswith("gc-")


@pytest.mark.asyncio
async def test_gigachat_adapter_respects_tool_choice_none() -> None:
    gc_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Финальный ответ",
                    function_call=None,
                )
            )
        ]
    )
    fake_client = _FakeGigaClient(gc_response)
    adapter = GigaChatLLMAdapter(client=fake_client)

    result = await adapter.create_completion(
        model="GigaChat-2-Max",
        messages=[{"role": "user", "content": "привет"}],
        tools=[],
        tool_choice="none",
    )

    payload = fake_client.calls[0]
    assert payload.function_call == "none"
    assert payload.functions is None
    assert result["choices"][0]["message"]["content"] == "Финальный ответ"
    assert result["choices"][0]["message"]["tool_calls"] == []


@pytest.mark.asyncio
async def test_gigachat_adapter_parses_assistant_tool_call_arguments_json_string() -> None:
    gc_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="Собираю корзину",
                    function_call=None,
                )
            )
        ]
    )
    fake_client = _FakeGigaClient(gc_response)
    adapter = GigaChatLLMAdapter(client=fake_client)

    result = await adapter.create_completion(
        model="GigaChat-2-Max",
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {
                            "name": "vkusvill_products_search",
                            "arguments": '{"q":"творог"}',
                        },
                    }
                ],
            }
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "vkusvill_products_search",
                    "description": "Search products",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                },
            }
        ],
    )

    payload = fake_client.calls[0]
    assistant_message = payload.messages[0]
    assert assistant_message.function_call is not None
    assert assistant_message.function_call.arguments == {"q": "творог"}
    assert result["choices"][0]["message"]["content"] == "Собираю корзину"


def test_create_llm_adapter_and_provider_normalization() -> None:
    assert normalize_llm_provider("qwen") == "qwen_openai"
    assert normalize_llm_provider("openai_compatible") == "qwen_openai"
    assert normalize_llm_provider("gigachat") == "gigachat_sdk"

    adapter = create_llm_adapter(
        provider="qwen_openai",
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_timeout_seconds=10.0,
        gigachat_credentials="creds",
        gigachat_scope="GIGACHAT_API_PERS",
    )
    assert isinstance(adapter, OpenAICompatibleLLMAdapter)

    with pytest.raises(ValueError, match="Unsupported llm provider"):
        create_llm_adapter(
            provider="unknown",
            llm_base_url="https://llm.api.cloud.yandex.net/v1",
            llm_api_key="test-key",
            llm_timeout_seconds=10.0,
            gigachat_credentials="creds",
            gigachat_scope="GIGACHAT_API_PERS",
        )
