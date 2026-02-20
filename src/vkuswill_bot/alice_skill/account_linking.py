"""Account linking для voice-клиентов."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from typing import Protocol

import httpx

if TYPE_CHECKING:
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)


class AccountLinkStore(Protocol):
    """Хранилище связи voice user -> internal user."""

    async def resolve_internal_user_id(self, voice_user_id: str) -> int | None:
        """Вернуть внутренний user_id по voice user_id, если связь активна."""

    async def consume_link_code(
        self,
        voice_user_id: str,
        code: str,
    ) -> dict[str, object]:
        """Погасить код привязки и активировать связь."""


class InMemoryAccountLinkStore:
    """Простое in-memory хранилище связей для MVP/локальной отладки."""

    def __init__(
        self,
        links: dict[str, int] | None = None,
        codes: dict[str, int] | None = None,
    ) -> None:
        self._links = dict(links or {})
        self._codes = dict(codes or {})

    async def resolve_internal_user_id(self, voice_user_id: str) -> int | None:
        return self._links.get(voice_user_id)

    async def consume_link_code(
        self,
        voice_user_id: str,
        code: str,
    ) -> dict[str, object]:
        user_id = self._codes.pop(code, None)
        if user_id is None:
            return {"ok": False, "reason": "invalid_code", "user_id": None}
        self._links[voice_user_id] = user_id
        return {"ok": True, "reason": "ok", "user_id": user_id}


class UnavailableAccountLinkStore:
    """Заглушка fail-closed для production linking."""

    async def resolve_internal_user_id(self, voice_user_id: str) -> int | None:
        del voice_user_id
        return None

    async def consume_link_code(
        self,
        voice_user_id: str,
        code: str,
    ) -> dict[str, object]:
        del voice_user_id, code
        return {"ok": False, "reason": "linking_unavailable", "user_id": None}


class PostgresAccountLinkStore:
    """Хранилище voice linking поверх UserStore (production)."""

    def __init__(self, user_store: UserStore, provider: str = "alice") -> None:
        self._user_store = user_store
        self._provider = provider

    async def resolve_internal_user_id(self, voice_user_id: str) -> int | None:
        return await self._user_store.resolve_voice_link(self._provider, voice_user_id)

    async def consume_link_code(
        self,
        voice_user_id: str,
        code: str,
    ) -> dict[str, object]:
        return await self._user_store.consume_voice_link_code(
            provider=self._provider,
            voice_user_id=voice_user_id,
            code=code,
        )


class HttpAccountLinkStore:
    """Хранилище linking через HTTP API бота (вариант 1, production)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        provider: str = "alice",
        timeout_seconds: float = 5.0,
        verify_ssl: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._provider = provider
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client or httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            verify=verify_ssl,
        )

    async def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
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
        return parsed if isinstance(parsed, dict) else {}

    async def resolve_internal_user_id(self, voice_user_id: str) -> int | None:
        try:
            data = await self._post_json(
                "/resolve",
                {"provider": self._provider, "voice_user_id": voice_user_id},
            )
        except Exception as exc:
            logger.warning("Voice link resolve via API failed: %s", exc)
            return None
        user_id = data.get("user_id")
        return user_id if isinstance(user_id, int) else None

    async def consume_link_code(
        self,
        voice_user_id: str,
        code: str,
    ) -> dict[str, object]:
        try:
            data = await self._post_json(
                "/consume",
                {
                    "provider": self._provider,
                    "voice_user_id": voice_user_id,
                    "code": code,
                },
            )
        except Exception as exc:
            logger.warning("Voice link consume via API failed: %s", exc)
            return {"ok": False, "reason": "linking_unavailable", "user_id": None}
        if not isinstance(data.get("ok"), bool):
            return {"ok": False, "reason": "invalid_response", "user_id": None}
        return data
