"""Unit tests for provider-specific LLM adapters."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from vkuswill_bot.services.llm_adapters import (
    OpenAICompatibleLLMAdapter,
    create_llm_adapter,
    extract_usage_details,
    normalize_chat_response,
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
        max_tokens=777,
        temperature=0.3,
    )

    message = result["choices"][0]["message"]
    assert message["content"] == "Готово"
    assert message["tool_calls"][0]["id"] == "tc-1"
    assert message["tool_calls"][0]["function"]["name"] == "vkusvill_products_search"
    assert result["usage"] == {"input": 123, "output": 45, "total": 168}
    request = adapter._client.completions.calls[0]
    assert request["max_tokens"] == 777
    assert request["temperature"] == 0.3
    assert "tools" not in request
    assert "tool_choice" not in request


@pytest.mark.asyncio
async def test_openai_adapter_preserves_tools_when_present() -> None:
    adapter = OpenAICompatibleLLMAdapter(client=_FakeOpenAIClient(_FakeOpenAIResponse()))
    result = await adapter.create_completion(
        model="gpt://folder/model/latest",
        messages=[{"role": "user", "content": "найди молоко"}],
        tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        tool_choice="auto",
    )

    message = result["choices"][0]["message"]
    assert message["content"] == "Готово"
    request = adapter._client.completions.calls[0]
    assert request["tools"][0]["function"]["name"] == "search"
    assert request["tool_choice"] == "auto"


def test_create_llm_adapter_and_provider_normalization() -> None:
    assert normalize_llm_provider("qwen") == "qwen_openai"
    assert normalize_llm_provider("openai_compatible") == "qwen_openai"

    adapter = create_llm_adapter(
        provider="qwen_openai",
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_timeout_seconds=10.0,
    )
    assert isinstance(adapter, OpenAICompatibleLLMAdapter)

    with pytest.raises(ValueError, match="Unsupported llm provider"):
        create_llm_adapter(
            provider="unknown",
            llm_base_url="https://llm.api.cloud.yandex.net/v1",
            llm_api_key="test-key",
            llm_timeout_seconds=10.0,
        )


def test_extract_usage_details_accepts_alt_keys_and_string_numbers() -> None:
    usage = extract_usage_details(
        {
            "usage": {
                "input_tokens": "120",
                "outputTokens": 35,
            }
        }
    )
    assert usage == {"input": 120, "output": 35, "total": 155}


def test_extract_usage_details_accepts_model_dump_objects() -> None:
    class _UsageObj:
        def model_dump(self) -> dict[str, int]:
            return {"promptTokens": 40, "completionTokens": 11}

    usage = extract_usage_details({"usage": _UsageObj()})
    assert usage == {"input": 40, "output": 11, "total": 51}


def test_normalize_chat_response_parses_inline_tool_call_tags() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "content": (
                        "<tool_call>\n"
                        '{"name":"vkusvill_product_details","id":59422}\n'
                        "</tool_call>\n"
                        "<tool_call>\n"
                        '{"name":"vkusvill_cart_link_create","arguments":{"products":[{"xml_id":59422,"q":1}]}}\n'
                        "</tool_call>"
                    ),
                    "tool_calls": [],
                }
            }
        ]
    }

    normalized = normalize_chat_response(response)
    message = normalized["choices"][0]["message"]
    assert message["content"] == ""
    assert len(message["tool_calls"]) == 2
    assert message["tool_calls"][0]["function"]["name"] == "vkusvill_product_details"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"id": 59422}'
    assert message["tool_calls"][1]["function"]["name"] == "vkusvill_cart_link_create"
    assert '"xml_id": 59422' in message["tool_calls"][1]["function"]["arguments"]
