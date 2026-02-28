"""Identity store: CRUD, роли, блокировка."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

VALID_ROLES = frozenset({"user", "admin"})
VALID_STATUSES = frozenset({"active", "blocked", "limited"})


class IdentityStore:
    """Хранилище идентичности пользователей: get/get_or_create, роли, статус."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        ensure_schema: Callable[[], Awaitable[None]],
    ) -> None:
        self._pool = pool
        self._ensure_schema = ensure_schema

    async def get_or_create(
        self,
        user_id: int,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        """Upsert: создать пользователя или обновить language_code.

        Вызывается при каждом входящем сообщении (из ``UserMiddleware``).
        PII (username, first_name, last_name) не сохраняются — приватность.

        Returns:
            Словарь с полями пользователя.
        """
        await self._ensure_schema()
        sql = """
            INSERT INTO users (user_id, language_code)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET
                language_code = EXCLUDED.language_code,
                updated_at    = NOW()
            RETURNING *
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                user_id,
                language_code,
            )
        return dict(row) if row else {}

    async def get(self, user_id: int) -> dict[str, Any] | None:
        """Получить пользователя по Telegram ID."""
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1",
                user_id,
            )
        return dict(row) if row else None

    async def is_blocked(self, user_id: int) -> bool:
        """Проверить, заблокирован ли пользователь."""
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM users WHERE user_id = $1",
                user_id,
            )
        return row is not None and row["status"] == "blocked"

    async def block(self, user_id: int, reason: str = "") -> bool:
        """Заблокировать пользователя.

        Returns:
            True если пользователь найден и заблокирован.
        """
        await self._ensure_schema()
        sql = """
            UPDATE users
            SET status = 'blocked',
                blocked_reason = $2,
                blocked_at = NOW(),
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, reason)
        if row:
            logger.info("Пользователь %d заблокирован: %s", user_id, reason)
            return True
        return False

    async def unblock(self, user_id: int) -> bool:
        """Разблокировать пользователя.

        Returns:
            True если пользователь найден и разблокирован.
        """
        await self._ensure_schema()
        sql = """
            UPDATE users
            SET status = 'active',
                blocked_reason = NULL,
                blocked_at = NULL,
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id)
        if row:
            logger.info("Пользователь %d разблокирован", user_id)
            return True
        return False

    async def is_admin(self, user_id: int) -> bool:
        """Проверить, является ли пользователь администратором."""
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT role FROM users WHERE user_id = $1",
                user_id,
            )
        return row is not None and row["role"] == "admin"

    async def set_role(self, user_id: int, role: str) -> bool:
        """Установить роль пользователя.

        Args:
            role: ``'user'`` или ``'admin'``.

        Returns:
            True если пользователь найден и роль обновлена.
        """
        if role not in VALID_ROLES:
            raise ValueError(f"Недопустимая роль: {role!r}, допустимо: {VALID_ROLES}")
        await self._ensure_schema()
        sql = """
            UPDATE users SET role = $2, updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, role)
        if row:
            logger.info("Роль пользователя %d → %s", user_id, role)
            return True
        return False
