"""Тесты graceful degradation при перегрузке LLM-семафора."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from vkuswill_bot.agents.exceptions import LLMOverloadedError
from vkuswill_bot.agents.shopping_agent import ShoppingAgent


# ---------------------------------------------------------------------------
# Fakes (минимальные копии из test_shopping_agent.py)
# ---------------------------------------------------------------------------


class _FakeDialogManager:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]


class _FakeMCPClient:
    async def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "vkusvill_products_search",
                "description": "Search products",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        return json.dumps({"ok": True}, ensure_ascii=False)


class _SlowLLMAdapter:
    """LLM-адаптер с управляемой задержкой для имитации нагрузки."""

    def __init__(self, *, delay_seconds: float = 5.0) -> None:
        self.delay_seconds = delay_seconds
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
        self.calls.append({"model": model})
        await asyncio.sleep(self.delay_seconds)
        return {
            "choices": [{"message": {"content": "Ответ ассистента", "tool_calls": []}}],
        }

    async def close(self) -> None:
        return None


def _make_agent(
    *,
    llm_max_concurrent: int = 1,
    llm_queue_timeout_seconds: float = 0.5,
    delay_seconds: float = 5.0,
) -> tuple[ShoppingAgent, _SlowLLMAdapter]:
    adapter = _SlowLLMAdapter(delay_seconds=delay_seconds)
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_max_concurrent=llm_max_concurrent,
        llm_queue_timeout_seconds=llm_queue_timeout_seconds,
        mcp_client=_FakeMCPClient(),  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        max_tool_calls=2,
        max_history=10,
        llm_adapters={"qwen_openai": adapter},
    )
    return agent, adapter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_overloaded_returns_friendly_message() -> None:
    """При заполненном семафоре новый запрос получает вежливый отказ, не таймаут."""
    agent, _adapter = _make_agent(
        llm_max_concurrent=1,
        llm_queue_timeout_seconds=0.3,
        delay_seconds=10.0,
    )

    results: list[str] = []

    async def send_message(user_id: int) -> str:
        return await agent.process_message(user_id, "Привет!")

    task1 = asyncio.create_task(send_message(1))
    await asyncio.sleep(0.05)

    result2 = await send_message(2)
    results.append(result2)

    task1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task1

    assert "много запросов" in result2 or "заняты" in result2


@pytest.mark.asyncio
async def test_llm_overloaded_error_raised_directly() -> None:
    """_call_llm поднимает LLMOverloadedError при невозможности захватить семафор."""
    agent, _ = _make_agent(
        llm_max_concurrent=1,
        llm_queue_timeout_seconds=0.2,
        delay_seconds=10.0,
    )

    async def occupy_semaphore() -> None:
        await agent._api_semaphore.acquire()
        await asyncio.sleep(10.0)

    bg = asyncio.create_task(occupy_semaphore())
    await asyncio.sleep(0.01)

    with pytest.raises(LLMOverloadedError):
        await agent._call_llm(
            messages=[{"role": "user", "content": "test"}],
            tools=[],
            llm_provider="qwen_openai",
        )

    bg.cancel()
    with pytest.raises(asyncio.CancelledError):
        await bg


@pytest.mark.asyncio
async def test_normal_request_passes_through() -> None:
    """При свободном семафоре запрос проходит нормально."""
    agent, adapter = _make_agent(
        llm_max_concurrent=2,
        llm_queue_timeout_seconds=5.0,
        delay_seconds=0.01,
    )

    result = await agent.process_message(1, "Привет!")
    assert "много запросов" not in result
    assert len(adapter.calls) > 0


@pytest.mark.asyncio
async def test_semaphore_released_after_llm_error() -> None:
    """Семафор освобождается даже при ошибке LLM."""

    class _FailingAdapter(_SlowLLMAdapter):
        async def create_completion(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("LLM API error")

    adapter = _FailingAdapter(delay_seconds=0.0)
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_max_concurrent=1,
        llm_queue_timeout_seconds=1.0,
        mcp_client=_FakeMCPClient(),  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        max_tool_calls=2,
        max_history=10,
        llm_retries=0,
        llm_adapters={"qwen_openai": adapter},
    )

    result = await agent.process_message(1, "Привет!")
    assert "Не удалось" in result or "Попробуйте" in result

    # Семафор должен быть свободен — второй запрос не должен зависнуть
    assert agent._api_semaphore._value == 1


@pytest.mark.asyncio
async def test_multiple_concurrent_within_limit() -> None:
    """Несколько запросов в пределах лимита семафора проходят параллельно."""
    agent, adapter = _make_agent(
        llm_max_concurrent=3,
        llm_queue_timeout_seconds=5.0,
        delay_seconds=0.1,
    )

    tasks = [
        asyncio.create_task(agent.process_message(uid, "Запрос"))
        for uid in range(3)
    ]
    results = await asyncio.gather(*tasks)

    for r in results:
        assert "много запросов" not in r
    assert len(adapter.calls) == 3
