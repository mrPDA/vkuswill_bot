"""Consent store: информированное согласие (ADR-002)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import asyncpg


class ConsentStore:
    """Хранилище согласия на обработку данных."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        ensure_schema: Callable[[], Awaitable[None]],
    ) -> None:
        self._pool = pool
        self._ensure_schema = ensure_schema

    async def mark_consent(
        self,
        user_id: int,
        consent_type: str = "explicit",
    ) -> bool:
        """Зафиксировать согласие пользователя на обработку данных.

        Атомарная операция: ставит ``consent_given_at`` только если ещё
        не заполнено (предотвращает перезапись explicit → implicit).

        Args:
            consent_type: ``'explicit'`` (кнопка) или ``'implicit'`` (продолжение использования).

        Returns:
            True если согласие зафиксировано (первый раз), False если уже было.
        """
        await self._ensure_schema()
        sql = """
            UPDATE users
            SET consent_given_at = NOW(),
                consent_type = $2,
                updated_at = NOW()
            WHERE user_id = $1 AND consent_given_at IS NULL
            RETURNING user_id
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, consent_type)
        return row is not None
