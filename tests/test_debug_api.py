"""Tests for stage-only debug API."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aiohttp import web

from vkuswill_bot.services.debug_api import (
    _reset_history_handler,
    _run_shopping_handler,
    should_enable_debug_api,
)


class _DummyRequest:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        app: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.headers = headers or {}
        self.app = app or {}
        self._payload = payload or {}

    async def json(self) -> dict[str, Any]:
        return self._payload


def _read_json(resp: web.Response) -> dict[str, Any]:
    return json.loads(resp.text)


class _DummyChatEngine:
    def __init__(self) -> None:
        self.reset_calls = 0
        self.process_calls = 0

    async def process_message(self, user_id: int, text: str) -> str:
        assert user_id == 42
        assert text == "закажи молоко"
        self.process_calls += 1
        return "Готово"

    async def reset_conversation(self, user_id: int) -> None:
        assert user_id == 42
        self.reset_calls += 1

    async def get_last_cart_snapshot(self, user_id: int) -> dict[str, Any] | None:
        assert user_id == 42
        return {
            "link": "https://vkusvill.ru/?share_basket=123",
            "products": [{"xml_id": 1, "q": 1}],
            "total": 99.0,
        }

    async def get_last_trace_id(self, user_id: int) -> str | None:
        assert user_id == 42
        return "trace-42"

    async def get_last_turn_diagnostics(self, user_id: int) -> dict[str, Any] | None:
        assert user_id == 42
        return {
            "prompt_profile": "cart",
            "meal_plan_can_use_executor": False,
            "execution_path": "standard_turn",
        }


class _FailingChatEngine(_DummyChatEngine):
    async def process_message(self, user_id: int, text: str) -> str:
        await super().process_message(user_id, text)
        raise RuntimeError("executor exploded")


@pytest.mark.asyncio
async def test_run_shopping_handler_requires_auth() -> None:
    req = _DummyRequest(
        headers={},
        app={"debug_api_key": "secret", "debug_chat_engine": _DummyChatEngine()},
        payload={"user_id": 42, "text": "закажи молоко"},
    )
    resp = await _run_shopping_handler(req)  # type: ignore[arg-type]
    assert resp.status == 401
    body = _read_json(resp)
    assert body["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_run_shopping_handler_runs_scenario_and_returns_trace_id() -> None:
    engine = _DummyChatEngine()
    req = _DummyRequest(
        headers={"X-Debug-Api-Key": "secret"},
        app={"debug_api_key": "secret", "debug_chat_engine": engine},
        payload={"user_id": 42, "text": "закажи молоко", "reset_history": True},
    )
    resp = await _run_shopping_handler(req)  # type: ignore[arg-type]
    assert resp.status == 200
    body = _read_json(resp)
    assert body["ok"] is True
    assert body["response"] == "Готово"
    assert body["trace_id"] == "trace-42"
    assert body["cart_link"] == "https://vkusvill.ru/?share_basket=123"
    assert body["items_count"] == 1
    assert body["total_rub"] == 99.0
    assert body["diagnostics"]["prompt_profile"] == "cart"
    assert body["diagnostics"]["execution_path"] == "standard_turn"
    assert engine.reset_calls == 1
    assert engine.process_calls == 1


@pytest.mark.asyncio
async def test_run_shopping_handler_can_skip_reset_history() -> None:
    engine = _DummyChatEngine()
    req = _DummyRequest(
        headers={"X-Debug-Api-Key": "secret"},
        app={"debug_api_key": "secret", "debug_chat_engine": engine},
        payload={"user_id": 42, "text": "закажи молоко", "reset_history": False},
    )
    resp = await _run_shopping_handler(req)  # type: ignore[arg-type]
    assert resp.status == 200
    body = _read_json(resp)
    assert body["history_reset"] is False
    assert engine.reset_calls == 0


@pytest.mark.asyncio
async def test_run_shopping_handler_returns_debug_context_on_exception() -> None:
    engine = _FailingChatEngine()
    req = _DummyRequest(
        headers={"X-Debug-Api-Key": "secret"},
        app={"debug_api_key": "secret", "debug_chat_engine": engine},
        payload={"user_id": 42, "text": "закажи молоко", "reset_history": True},
    )
    resp = await _run_shopping_handler(req)  # type: ignore[arg-type]
    assert resp.status == 502
    body = _read_json(resp)
    assert body["error"] == "llm_error"
    assert body["exception_type"] == "RuntimeError"
    assert body["exception_message"] == "executor exploded"
    assert body["trace_id"] == "trace-42"
    assert body["diagnostics"]["prompt_profile"] == "cart"


@pytest.mark.asyncio
async def test_reset_history_handler_success() -> None:
    engine = _DummyChatEngine()
    req = _DummyRequest(
        headers={"X-Debug-Api-Key": "secret"},
        app={"debug_api_key": "secret", "debug_chat_engine": engine},
        payload={"user_id": 42},
    )
    resp = await _reset_history_handler(req)  # type: ignore[arg-type]
    assert resp.status == 200
    body = _read_json(resp)
    assert body == {"ok": True, "user_id": 42}
    assert engine.reset_calls == 1


def test_should_enable_debug_api_only_non_production_with_key() -> None:
    assert should_enable_debug_api(api_key="secret", environment="staging") is True
    assert should_enable_debug_api(api_key="", environment="staging") is False
    assert should_enable_debug_api(api_key="secret", environment="production") is False
