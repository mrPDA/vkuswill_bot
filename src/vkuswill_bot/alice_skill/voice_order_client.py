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

    async def create_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, Any]:
        payload = {
            "user_id": user_id,
            "voice_user_id": voice_user_id,
            "utterance": utterance,
        }
        headers = {
            "Content-Type": "application/json",
            "X-Voice-Link-Api-Key": self._api_key,
        }
        response = await asyncio.to_thread(
            self._client.post,
            f"{self._base_url}/order",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise ValueError("Voice order API returned non-object JSON")
        return parsed
