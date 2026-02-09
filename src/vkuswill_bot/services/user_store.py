"""Хранилище пользователей (PostgreSQL).

Управление пользователями бота: регистрация, роли, блокировка,
персональные лимиты, статистика и лог событий.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Путь к SQL-миграции (рядом с корнем проекта)
_MIGRATION_PATH = Path(__file__).resolve().parents[3] / "migrations" / "001_create_users.sql"

# Допустимые значения для CHECK-ограничений
VALID_ROLES = frozenset({"user", "admin"})
VALID_STATUSES = frozenset({"active", "blocked", "limited"})


class UserStore:
    """Async-хранилище пользователей на базе asyncpg (PostgreSQL).

    Стиль аналогичен ``PreferencesStore`` — raw SQL, без ORM.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._schema_ready = False

    # ------------------------------------------------------------------
    # Инициализация схемы
    # ------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Создать таблицы, если они ещё не существуют."""
        if self._schema_ready:
            return
        sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute(sql)
        self._schema_ready = True
        logger.info("PostgreSQL: схема users/user_events готова")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def get_or_create(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str = "",
        last_name: str | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        """Upsert: создать пользователя или обновить метаданные.

        Вызывается при каждом входящем сообщении (из ``UserMiddleware``).
        При конфликте обновляет ``username``, ``first_name``, ``last_name``,
        ``language_code`` и ``updated_at`` — Telegram-данные могут меняться.

        Returns:
            Словарь с полями пользователя.
        """
        await self.ensure_schema()
        sql = """
            INSERT INTO users (user_id, username, first_name, last_name, language_code)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO UPDATE SET
                username      = EXCLUDED.username,
                first_name    = EXCLUDED.first_name,
                last_name     = EXCLUDED.last_name,
                language_code = EXCLUDED.language_code,
                updated_at    = NOW()
            RETURNING *
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                sql, user_id, username, first_name, last_name, language_code,
            )
        return dict(row) if row else {}

    async def get(self, user_id: int) -> dict[str, Any] | None:
        """Получить пользователя по Telegram ID."""
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1", user_id,
            )
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Статус (блокировка)
    # ------------------------------------------------------------------

    async def is_blocked(self, user_id: int) -> bool:
        """Проверить, заблокирован ли пользователь."""
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM users WHERE user_id = $1", user_id,
            )
        return row is not None and row["status"] == "blocked"

    async def block(self, user_id: int, reason: str = "") -> bool:
        """Заблокировать пользователя.

        Returns:
            True если пользователь найден и заблокирован.
        """
        await self.ensure_schema()
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
        await self.ensure_schema()
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

    # ------------------------------------------------------------------
    # Роли
    # ------------------------------------------------------------------

    async def is_admin(self, user_id: int) -> bool:
        """Проверить, является ли пользователь администратором."""
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT role FROM users WHERE user_id = $1", user_id,
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
        await self.ensure_schema()
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

    # ------------------------------------------------------------------
    # Персональные лимиты
    # ------------------------------------------------------------------

    async def get_limits(self, user_id: int) -> dict[str, Any] | None:
        """Получить персональные лимиты.

        Returns:
            ``{"rate_limit": int, "rate_period": float}`` или ``None``
            если лимиты не заданы (используются дефолтные из config).
        """
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT rate_limit, rate_period FROM users WHERE user_id = $1",
                user_id,
            )
        if row and row["rate_limit"] is not None:
            return {"rate_limit": row["rate_limit"], "rate_period": row["rate_period"]}
        return None

    async def set_limits(
        self, user_id: int, rate_limit: int | None, rate_period: float | None,
    ) -> bool:
        """Установить персональные лимиты (None = сброс к дефолтным)."""
        await self.ensure_schema()
        sql = """
            UPDATE users
            SET rate_limit = $2, rate_period = $3, updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, rate_limit, rate_period)
        return row is not None

    # ------------------------------------------------------------------
    # Статистика
    # ------------------------------------------------------------------

    async def increment_message_count(self, user_id: int) -> None:
        """Увеличить счётчик сообщений и обновить last_message_at."""
        await self.ensure_schema()
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
        await self.ensure_schema()
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
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT message_count, created_at, last_message_at "
                "FROM users WHERE user_id = $1",
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

    # ------------------------------------------------------------------
    # Админские запросы
    # ------------------------------------------------------------------

    async def list_users(
        self, limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Список пользователей (для админ-панели)."""
        await self.ensure_schema()
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
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) as cnt FROM users")
        return row["cnt"] if row else 0

    async def count_active_today(self) -> int:
        """Количество активных сегодня (DAU)."""
        await self.ensure_schema()
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
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
        await self.ensure_schema()
        sql = """
            INSERT INTO users (user_id, first_name, role)
            VALUES ($1, 'Admin', 'admin')
            ON CONFLICT (user_id) DO UPDATE SET
                role = 'admin',
                updated_at = NOW()
        """
        async with self._pool.acquire() as conn:
            for uid in admin_ids:
                await conn.execute(sql, uid)
        logger.info("Администраторы установлены: %s", admin_ids)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Закрыть пул соединений (делегируется вызывающему коду)."""
        # Пул закрывается в __main__.py; метод для единообразия API.
        logger.info("UserStore: close вызван")
