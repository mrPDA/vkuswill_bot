"""Идемпотентность voice-команд в коротком окне."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from vkuswill_bot.alice_skill.models import VoiceOrderResult


@dataclass
class _Entry:
    status: str  # in_progress | done
    expires_at: float
    result: VoiceOrderResult | None = None


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
