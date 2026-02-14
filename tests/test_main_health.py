"""Тесты health endpoint в __main__.py."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vkuswill_bot.__main__ import _health_handler


@pytest.mark.asyncio
async def test_health_ok_with_mcp_healthcheck() -> None:
    """Если healthcheck зависимостей успешен, endpoint возвращает 200/ok."""
    redis_client = AsyncMock()
    redis_client.ping.return_value = True

    pg_pool = MagicMock()
    conn = AsyncMock()
    pg_ctx = AsyncMock()
    pg_ctx.__aenter__.return_value = conn
    pg_ctx.__aexit__.return_value = False
    pg_pool.acquire.return_value = pg_ctx

    mcp_client = AsyncMock()
    mcp_client.healthcheck.return_value = True

    request = SimpleNamespace(
        app={
            "redis_client": redis_client,
            "pg_pool": pg_pool,
            "mcp_client": mcp_client,
        }
    )

    response = await _health_handler(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["status"] == "ok"
    assert payload["mcp"] is True
    mcp_client.healthcheck.assert_awaited_once()
    mcp_client.get_tools.assert_not_called()


@pytest.mark.asyncio
async def test_health_degraded_when_mcp_healthcheck_fails() -> None:
    """При отказе MCP healthcheck endpoint деградирует в 503."""
    mcp_client = AsyncMock()
    mcp_client.healthcheck.return_value = False

    request = SimpleNamespace(
        app={
            "redis_client": None,
            "pg_pool": None,
            "mcp_client": mcp_client,
        }
    )

    response = await _health_handler(request)
    payload = json.loads(response.text)

    assert response.status == 503
    assert payload["status"] == "degraded"
    assert payload["mcp"] is False
    mcp_client.healthcheck.assert_awaited_once()
