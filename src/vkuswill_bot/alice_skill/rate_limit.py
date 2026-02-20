"""Rate limiting для Alice-канала."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING
from typing import Protocol

if TYPE_CHECKING:
    from redis.asyncio import Redis


class RateLimiter(Protocol):
    """Контракт лимитера запросов."""

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """Проверить и зафиксировать попытку по ключу."""


@dataclass
class _Counter:
    count: int
    reset_at: float


class InMemoryRateLimiter:
    """In-memory fixed-window limiter (best effort)."""

    def __init__(self) -> None:
        self._counters: dict[str, _Counter] = {}

    def _prune(self, now: float) -> None:
        expired_keys = [k for k, v in self._counters.items() if v.reset_at <= now]
        for key in expired_keys:
            self._counters.pop(key, None)

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit <= 0 or window_seconds <= 0:
            return True
        now = monotonic()
        self._prune(now)
        counter = self._counters.get(key)
        if counter is None or counter.reset_at <= now:
            self._counters[key] = _Counter(count=1, reset_at=now + window_seconds)
            return True
        if counter.count >= limit:
            return False
        counter.count += 1
        return True


class RedisRateLimiter:
    """Redis fixed-window limiter shared между инстансами."""

    def __init__(
        self,
        redis: Redis,
        *,
        key_prefix: str = "alice:rl:",
        fallback_limiter: InMemoryRateLimiter | None = None,
    ) -> None:
        self._redis = redis
        self._key_prefix = key_prefix
        self._fallback = fallback_limiter or InMemoryRateLimiter()

    def _full_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit <= 0 or window_seconds <= 0:
            return True
        full_key = self._full_key(key)
        try:
            count = await self._redis.incr(full_key)
            if count == 1:
                await self._redis.expire(full_key, max(1, window_seconds))
            return count <= limit
        except Exception:
            return await self._fallback.allow(key, limit=limit, window_seconds=window_seconds)
