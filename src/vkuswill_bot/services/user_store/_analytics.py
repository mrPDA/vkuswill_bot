"""Analytics store: статистика, события, админские запросы."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


class AnalyticsStore:
    """Хранилище аналитики и статистики."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        ensure_schema: Callable[[], Awaitable[None]],
    ) -> None:
        self._pool = pool
        self._ensure_schema = ensure_schema

    async def increment_message_count(self, user_id: int) -> None:
        """Увеличить счётчик сообщений и обновить last_message_at."""
        await self._ensure_schema()
        sql = """
            UPDATE users
            SET message_count = message_count + 1,
                last_message_at = NOW(),
                updated_at = NOW()
            WHERE user_id = $1
        """
        async with self._pool.acquire() as conn:
            await conn.execute(sql, user_id)

    async def log_event(
        self,
        user_id: int,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Записать событие в ``user_events``."""
        await self._ensure_schema()
        sql = """
            INSERT INTO user_events (user_id, event_type, metadata)
            VALUES ($1, $2, $3)
        """
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        async with self._pool.acquire() as conn:
            await conn.execute(sql, user_id, event_type, meta_json)

    async def get_stats(self, user_id: int) -> dict[str, Any] | None:
        """Получить статистику пользователя.

        Returns:
            Словарь с message_count, created_at, last_message_at,
            events_count и событиями по типам. None если пользователь не найден.
        """
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT message_count, created_at, last_message_at FROM users WHERE user_id = $1",
                user_id,
            )
            if not user_row:
                return None

            events_rows = await conn.fetch(
                "SELECT event_type, COUNT(*) as cnt "
                "FROM user_events WHERE user_id = $1 "
                "GROUP BY event_type ORDER BY cnt DESC",
                user_id,
            )

        events_summary = {row["event_type"]: row["cnt"] for row in events_rows}
        return {
            "message_count": user_row["message_count"],
            "created_at": user_row["created_at"],
            "last_message_at": user_row["last_message_at"],
            "events": events_summary,
        }

    async def list_users(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Список пользователей (для админ-панели)."""
        await self._ensure_schema()
        sql = """
            SELECT user_id, username, first_name, role, status,
                   message_count, last_message_at, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, limit, offset)
        return [dict(r) for r in rows]

    async def count_users(self) -> int:
        """Общее количество зарегистрированных пользователей."""
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) as cnt FROM users")
        return row["cnt"] if row else 0

    async def count_active_today(self) -> int:
        """Количество активных сегодня (DAU)."""
        await self._ensure_schema()
        today = datetime.now(UTC).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM users WHERE last_message_at >= $1",
                today,
            )
        return row["cnt"] if row else 0

    async def ensure_admins(self, admin_ids: list[int]) -> None:
        """Гарантировать, что указанные user_id имеют роль admin.

        Вызывается при старте бота для начальных админов из .env.
        Создаёт записи, если пользователь ещё не существует.
        """
        if not admin_ids:
            return
        await self._ensure_schema()
        sql = """
            INSERT INTO users (user_id, role)
            VALUES ($1, 'admin')
            ON CONFLICT (user_id) DO UPDATE SET
                role = 'admin',
                updated_at = NOW()
        """
        async with self._pool.acquire() as conn:
            for uid in admin_ids:
                await conn.execute(sql, uid)
        logger.info("Администраторы установлены: %s", admin_ids)
