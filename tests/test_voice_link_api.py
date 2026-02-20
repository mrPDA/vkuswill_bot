"""Тесты HTTP API для voice linking (вариант 1)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from aiohttp import web

from vkuswill_bot.services.voice_link_api import _consume_handler, _resolve_handler


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
