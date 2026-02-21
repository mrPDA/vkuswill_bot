"""Тесты HTTP API для voice linking (вариант 1)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web

from vkuswill_bot.services.voice_link_api import (
    _consume_handler,
    _order_handler,
    _order_start_handler,
    _order_status_handler,
    _resolve_handler,
)


class _DummyStore:
    async def consume_voice_link_code(
        self,
        provider: str,
        voice_user_id: str,
        code: str,
    ) -> dict[str, Any]:
        if provider == "alice" and voice_user_id == "alice-1" and code == "123456":
            return {"ok": True, "reason": "ok", "user_id": 42}
        return {"ok": False, "reason": "invalid_code", "user_id": None}

    async def resolve_voice_link(self, provider: str, voice_user_id: str) -> int | None:
        if provider == "alice" and voice_user_id == "alice-1":
            return 42
        return None


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


@pytest.mark.asyncio
async def test_consume_handler_unauthorized() -> None:
    req = _DummyRequest(
        headers={},
        app={"voice_link_api_key": "secret", "voice_link_user_store": _DummyStore()},
        payload={"provider": "alice", "voice_user_id": "alice-1", "code": "123456"},
    )
    resp = await _consume_handler(req)  # type: ignore[arg-type]
    assert resp.status == 401
    body = _read_json(resp)
    assert body["ok"] is False
    assert body["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_consume_handler_success() -> None:
    req = _DummyRequest(
        headers={"X-Voice-Link-Api-Key": "secret"},
        app={"voice_link_api_key": "secret", "voice_link_user_store": _DummyStore()},
        payload={"provider": "alice", "voice_user_id": "alice-1", "code": "123456"},
    )
    resp = await _consume_handler(req)  # type: ignore[arg-type]
    assert resp.status == 200
    body = _read_json(resp)
    assert body["ok"] is True
    assert body["user_id"] == 42


@pytest.mark.asyncio
async def test_resolve_handler_success() -> None:
    req = _DummyRequest(
        headers={"X-Voice-Link-Api-Key": "secret"},
        app={"voice_link_api_key": "secret", "voice_link_user_store": _DummyStore()},
        payload={"provider": "alice", "voice_user_id": "alice-1"},
    )
    resp = await _resolve_handler(req)  # type: ignore[arg-type]
    assert resp.status == 200
    body = _read_json(resp)
    assert body == {"ok": True, "user_id": 42}


class _DummyGigaChat:
    def __init__(self) -> None:
        self._snapshot_calls = 0
        self._before_snapshot: dict[str, Any] | None = {
            "link": "https://shop.example/cart/old",
            "created_at": "2026-02-21T00:00:00+00:00",
            "products": [{"xml_id": 1, "q": 1}],
            "total": 100.0,
        }
        self._after_snapshot: dict[str, Any] | None = {
            "link": "https://shop.example/cart/new",
            "created_at": "2026-02-21T00:01:00+00:00",
            "products": [{"xml_id": 2, "q": 1}, {"xml_id": 3, "q": 1}],
            "total": 300.0,
        }

    async def get_last_cart_snapshot(self, user_id: int) -> dict[str, Any] | None:
        assert user_id == 42
        self._snapshot_calls += 1
        return self._before_snapshot if self._snapshot_calls == 1 else self._after_snapshot

    async def process_message(self, user_id: int, text: str) -> str:
        assert user_id == 42
        assert text == "Собери корзину: молоко и яйца"
        return "Готово"


@pytest.mark.asyncio
async def test_order_handler_success() -> None:
    req = _DummyRequest(
        headers={"X-Voice-Link-Api-Key": "secret"},
        app={"voice_link_api_key": "secret", "voice_link_gigachat_service": _DummyGigaChat()},
        payload={"user_id": 42, "utterance": "Собери корзину: молоко и яйца"},
    )
    resp = await _order_handler(req)  # type: ignore[arg-type]
    assert resp.status == 200
    body = _read_json(resp)
    assert body["ok"] is True
    assert body["cart_link"] == "https://shop.example/cart/new"
    assert body["items_count"] == 2
    assert body["total_rub"] == 300.0


@pytest.mark.asyncio
async def test_order_handler_cart_not_created() -> None:
    svc = _DummyGigaChat()
    svc._after_snapshot = svc._before_snapshot
    req = _DummyRequest(
        headers={"X-Voice-Link-Api-Key": "secret"},
        app={"voice_link_api_key": "secret", "voice_link_gigachat_service": svc},
        payload={"user_id": 42, "utterance": "Собери корзину: молоко и яйца"},
    )
    resp = await _order_handler(req)  # type: ignore[arg-type]
    assert resp.status == 200
    body = _read_json(resp)
    assert body["ok"] is False
    assert body["error"] == "cart_not_created"


@pytest.mark.asyncio
async def test_order_start_and_status_done() -> None:
    app = {
        "voice_link_api_key": "secret",
        "voice_link_gigachat_service": _DummyGigaChat(),
    }
    start_req = _DummyRequest(
        headers={"X-Voice-Link-Api-Key": "secret"},
        app=app,
        payload={
            "user_id": 42,
            "voice_user_id": "alice-1",
            "utterance": "Собери корзину: молоко и яйца",
        },
    )
    start_resp = await _order_start_handler(start_req)  # type: ignore[arg-type]
    assert start_resp.status == 200
    start_body = _read_json(start_resp)
    assert start_body["ok"] is True
    assert start_body["status"] == "processing"
    job_id = start_body["job_id"]

    status_body: dict[str, Any] = {}
    for _ in range(20):
        status_req = _DummyRequest(
            headers={"X-Voice-Link-Api-Key": "secret"},
            app=app,
            payload={"job_id": job_id, "user_id": 42, "voice_user_id": "alice-1"},
        )
        status_resp = await _order_status_handler(status_req)  # type: ignore[arg-type]
        assert status_resp.status == 200
        status_body = _read_json(status_resp)
        if status_body["status"] == "done":
            break
        await asyncio.sleep(0)

    assert status_body["status"] == "done"
    assert status_body["cart_link"] == "https://shop.example/cart/new"
    assert status_body["items_count"] == 2
    assert status_body["total_rub"] == 300.0


@pytest.mark.asyncio
async def test_order_status_not_found() -> None:
    app = {
        "voice_link_api_key": "secret",
        "voice_link_gigachat_service": _DummyGigaChat(),
    }
    status_req = _DummyRequest(
        headers={"X-Voice-Link-Api-Key": "secret"},
        app=app,
        payload={"user_id": 42, "voice_user_id": "alice-1", "job_id": "missing"},
    )
    status_resp = await _order_status_handler(status_req)  # type: ignore[arg-type]
    assert status_resp.status == 200
    body = _read_json(status_resp)
    assert body == {"ok": True, "status": "not_found"}
