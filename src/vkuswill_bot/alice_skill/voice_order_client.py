"""HTTP-клиент internal API для voice-заказа через стандартный LLM-цикл."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class HttpVoiceOrderClient:
    """Клиент для /voice-link/order на VM-боте."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 12.0,
        verify_ssl: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client or httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            verify=verify_ssl,
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "X-Voice-Link-Api-Key": self._api_key,
        }
        response = await asyncio.to_thread(
            self._client.post,
            f"{self._base_url}{path}",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise ValueError("Voice order API returned non-object JSON")
        return parsed

    async def create_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/order",
            {
                "user_id": user_id,
                "voice_user_id": voice_user_id,
                "utterance": utterance,
            },
        )

    async def start_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/order/start",
            {
                "user_id": user_id,
                "voice_user_id": voice_user_id,
                "utterance": utterance,
            },
        )

    async def get_order_status(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "user_id": user_id,
            "voice_user_id": voice_user_id,
        }
        if job_id:
            payload["job_id"] = job_id
        return await self._post("/order/status", payload)
