"""Идемпотентность voice-команд в коротком окне."""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING
from typing import Protocol

from vkuswill_bot.alice_skill.models import DeliveryResult
from vkuswill_bot.alice_skill.models import VoiceOrderResult

if TYPE_CHECKING:
    from redis.asyncio import Redis


@dataclass
class _Entry:
    status: str  # in_progress | done
    expires_at: float
    result: VoiceOrderResult | None = None


class IdempotencyStore(Protocol):
    """Контракт хранилища идемпотентности для voice-команд."""

    async def get_done(self, key: str) -> VoiceOrderResult | None:
        """Вернуть готовый результат по ключу, если он ещё валиден."""

    async def try_start(self, key: str, ttl_seconds: int) -> bool:
        """Забронировать ключ для обработки, если он свободен."""

    async def mark_done(self, key: str, result: VoiceOrderResult, ttl_seconds: int) -> None:
        """Зафиксировать успешный результат по ключу."""

    async def clear(self, key: str) -> None:
        """Очистить ключ при ошибке."""


class InMemoryIdempotencyStore:
    """In-memory хранение идемпотентных ключей (best effort)."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def _prune_expired(self, now: float) -> None:
        expired = [k for k, v in self._entries.items() if v.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    async def get_done(self, key: str) -> VoiceOrderResult | None:
        """Вернуть готовый результат по ключу, если он ещё валиден."""
        now = monotonic()
        self._prune_expired(now)
        entry = self._entries.get(key)
        if entry and entry.status == "done":
            return entry.result
        return None

    async def try_start(self, key: str, ttl_seconds: int) -> bool:
        """Забронировать ключ для обработки, если он свободен."""
        now = monotonic()
        self._prune_expired(now)
        entry = self._entries.get(key)
        if entry and entry.status == "in_progress":
            return False
        self._entries[key] = _Entry(
            status="in_progress",
            expires_at=now + ttl_seconds,
            result=None,
        )
        return True

    async def mark_done(self, key: str, result: VoiceOrderResult, ttl_seconds: int) -> None:
        """Зафиксировать успешный результат по ключу."""
        now = monotonic()
        self._entries[key] = _Entry(
            status="done",
            expires_at=now + ttl_seconds,
            result=result,
        )

    async def clear(self, key: str) -> None:
        """Очистить ключ при ошибке."""
        self._entries.pop(key, None)


class RedisIdempotencyStore:
    """Redis-хранилище идемпотентности (shared между инстансами)."""

    def __init__(
        self,
        redis: Redis,
        *,
        key_prefix: str = "alice:idem:",
        fallback_store: InMemoryIdempotencyStore | None = None,
    ) -> None:
        self._redis = redis
        self._key_prefix = key_prefix
        self._fallback_store = fallback_store or InMemoryIdempotencyStore()

    def _full_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    @staticmethod
    def _serialize_result(result: VoiceOrderResult) -> dict:
        delivery = None
        if result.delivery is not None:
            delivery = {
                "status": result.delivery.status,
                "channel": result.delivery.channel,
                "button_title": result.delivery.button_title,
                "button_url": result.delivery.button_url,
            }
        return {
            "ok": result.ok,
            "voice_text": result.voice_text,
            "cart_link": result.cart_link,
            "total_rub": result.total_rub,
            "items_count": result.items_count,
            "delivery": delivery,
            "requires_linking": result.requires_linking,
            "error_code": result.error_code,
        }

    @staticmethod
    def _deserialize_result(payload: dict) -> VoiceOrderResult | None:
        if not isinstance(payload, dict):
            return None
        voice_text = payload.get("voice_text")
        ok = payload.get("ok")
        if not isinstance(voice_text, str) or not isinstance(ok, bool):
            return None

        delivery_payload = payload.get("delivery")
        delivery = None
        if isinstance(delivery_payload, dict):
            status = delivery_payload.get("status")
            channel = delivery_payload.get("channel")
            if isinstance(status, str) and isinstance(channel, str):
                button_title = delivery_payload.get("button_title")
                button_url = delivery_payload.get("button_url")
                delivery = DeliveryResult(
                    status=status,
                    channel=channel,
                    button_title=button_title if isinstance(button_title, str) else None,
                    button_url=button_url if isinstance(button_url, str) else None,
                )

        total_rub = payload.get("total_rub")
        total_rub = None if not isinstance(total_rub, int | float) else float(total_rub)

        items_count = payload.get("items_count", 0)
        if not isinstance(items_count, int):
            items_count = 0

        requires_linking = payload.get("requires_linking", False)
        if not isinstance(requires_linking, bool):
            requires_linking = False

        error_code = payload.get("error_code")
        if not isinstance(error_code, str):
            error_code = None

        cart_link = payload.get("cart_link")
        if not isinstance(cart_link, str):
            cart_link = None

        return VoiceOrderResult(
            ok=ok,
            voice_text=voice_text,
            cart_link=cart_link,
            total_rub=total_rub,
            items_count=items_count,
            delivery=delivery,
            requires_linking=requires_linking,
            error_code=error_code,
        )

    async def get_done(self, key: str) -> VoiceOrderResult | None:
        full_key = self._full_key(key)
        try:
            raw = await self._redis.get(full_key)
        except Exception:
            return await self._fallback_store.get_done(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if not isinstance(raw, str):
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if payload.get("status") != "done":
            return None
        result_payload = payload.get("result")
        if not isinstance(result_payload, dict):
            return None
        return self._deserialize_result(result_payload)

    async def try_start(self, key: str, ttl_seconds: int) -> bool:
        full_key = self._full_key(key)
        payload = {"status": "in_progress"}
        try:
            created = await self._redis.set(
                full_key,
                json.dumps(payload, ensure_ascii=False),
                ex=max(1, ttl_seconds),
                nx=True,
            )
        except Exception:
            return await self._fallback_store.try_start(key, ttl_seconds)
        return bool(created)

    async def mark_done(self, key: str, result: VoiceOrderResult, ttl_seconds: int) -> None:
        full_key = self._full_key(key)
        payload = {
            "status": "done",
            "result": self._serialize_result(result),
        }
        try:
            await self._redis.set(
                full_key,
                json.dumps(payload, ensure_ascii=False),
                ex=max(1, ttl_seconds),
            )
        except Exception:
            await self._fallback_store.mark_done(key, result, ttl_seconds)
            return

    async def clear(self, key: str) -> None:
        full_key = self._full_key(key)
        try:
            await self._redis.delete(full_key)
        except Exception:
            await self._fallback_store.clear(key)
            return
