"""Freemium store: лимиты корзин, survey, feedback бонусы."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg


class FreemiumStore:
    """Хранилище freemium-логики: лимиты корзин, survey, feedback."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        ensure_schema: Callable[[], Awaitable[None]],
    ) -> None:
        self._pool = pool
        self._ensure_schema = ensure_schema

    async def get_limits(self, user_id: int) -> dict[str, Any] | None:
        """Получить персональные лимиты.

        Returns:
            ``{"rate_limit": int, "rate_period": float}`` или ``None``
            если лимиты не заданы (используются дефолтные из config).
        """
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT rate_limit, rate_period FROM users WHERE user_id = $1",
                user_id,
            )
        if row and row["rate_limit"] is not None:
            return {"rate_limit": row["rate_limit"], "rate_period": row["rate_period"]}
        return None

    async def set_limits(
        self,
        user_id: int,
        rate_limit: int | None,
        rate_period: float | None,
    ) -> bool:
        """Установить персональные лимиты (None = сброс к дефолтным)."""
        await self._ensure_schema()
        sql = """
            UPDATE users
            SET rate_limit = $2, rate_period = $3, updated_at = NOW()
            WHERE user_id = $1
            RETURNING user_id
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, rate_limit, rate_period)
        return row is not None

    async def check_cart_limit(
        self,
        user_id: int,
        default_limit: int = 0,
        trial_days: int = 10,
    ) -> dict[str, Any]:
        """Проверить, может ли пользователь создать корзину.

        Args:
            default_limit: лимит по умолчанию, если пользователь не найден.
            trial_days: длительность trial-периода с безлимитными корзинами.

        Returns:
            ``{"allowed": bool, "carts_created": int, "cart_limit": int,
            "survey_completed": bool, "trial_active": bool}``
        """
        await self._ensure_schema()
        sql = """
            SELECT carts_created, cart_limit, survey_completed, created_at
            FROM users
            WHERE user_id = $1
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id)
        now = datetime.now(UTC)
        safe_trial_days = max(0, trial_days)
        if not row:
            trial_ends_at = now + timedelta(days=safe_trial_days)
            return {
                "allowed": True,
                "carts_created": 0,
                "cart_limit": default_limit,
                "survey_completed": False,
                "trial_active": safe_trial_days > 0,
                "trial_ends_at": trial_ends_at,
                "trial_days_left": safe_trial_days,
            }

        created_at = row["created_at"]
        trial_ends_at = created_at + timedelta(days=safe_trial_days)
        trial_active = safe_trial_days > 0 and now < trial_ends_at
        trial_days_left = 0
        if trial_active:
            seconds_left = max(0, int((trial_ends_at - now).total_seconds()))
            trial_days_left = max(1, (seconds_left + 86399) // 86400)

        return {
            "allowed": True if trial_active else row["carts_created"] < row["cart_limit"],
            "carts_created": row["carts_created"],
            "cart_limit": row["cart_limit"],
            "survey_completed": bool(row["survey_completed"]),
            "trial_active": trial_active,
            "trial_ends_at": trial_ends_at,
            "trial_days_left": trial_days_left,
        }

    async def increment_carts(
        self,
        user_id: int,
        trial_days: int = 10,
    ) -> dict[str, Any]:
        """Увеличить счётчик корзин на 1.

        Returns:
            ``{"carts_created": int, "cart_limit": int, "survey_completed": bool,
            "trial_active": bool}``
        """
        await self._ensure_schema()
        safe_trial_days = max(0, trial_days)
        sql = """
            UPDATE users
            SET carts_created = CASE
                    WHEN created_at >= NOW() - ($2 * INTERVAL '1 day')
                    THEN carts_created
                    ELSE carts_created + 1
                END,
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING carts_created, cart_limit, survey_completed, created_at
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, safe_trial_days)
        if not row:
            return {}

        now = datetime.now(UTC)
        trial_ends_at = row["created_at"] + timedelta(days=safe_trial_days)
        trial_active = safe_trial_days > 0 and now < trial_ends_at
        trial_days_left = 0
        if trial_active:
            seconds_left = max(0, int((trial_ends_at - now).total_seconds()))
            trial_days_left = max(1, (seconds_left + 86399) // 86400)

        return {
            "carts_created": row["carts_created"],
            "cart_limit": row["cart_limit"],
            "survey_completed": bool(row["survey_completed"]),
            "trial_active": trial_active,
            "trial_ends_at": trial_ends_at,
            "trial_days_left": trial_days_left,
        }

    async def reset_carts(self, user_id: int) -> dict[str, Any] | None:
        """Сбросить счётчик корзин пользователя до 0.

        Используется админ-командой ``/admin_reset_carts`` для ручного
        восстановления возможности создавать корзины без потери выданного лимита.

        Returns:
            Обновлённые данные пользователя или None, если не найден.
        """
        await self._ensure_schema()
        sql = """
            UPDATE users
            SET carts_created = 0,
                updated_at = NOW()
            WHERE user_id = $1
            RETURNING carts_created, cart_limit, survey_completed
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id)
        return dict(row) if row else None

    async def grant_bonus_carts(self, user_id: int, amount: int = 5) -> int:
        """Увеличить лимит корзин.

        Returns:
            Новый лимит корзин (0 если пользователь не найден).
        """
        await self._ensure_schema()
        sql = """
            UPDATE users
            SET cart_limit = cart_limit + $2, updated_at = NOW()
            WHERE user_id = $1
            RETURNING cart_limit
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, amount)
        return row["cart_limit"] if row else 0

    async def grant_feedback_bonus_if_due(
        self,
        user_id: int,
        amount: int = 2,
        cooldown_days: int = 30,
    ) -> dict[str, Any]:
        """Выдать бонус за feedback, если истёк cooldown."""
        await self._ensure_schema()
        safe_cooldown_days = max(1, cooldown_days)
        safe_amount = max(0, amount)

        sql = """
            UPDATE users
            SET cart_limit = cart_limit + $2,
                feedback_bonus_granted_at = NOW(),
                updated_at = NOW()
            WHERE user_id = $1
              AND (
                feedback_bonus_granted_at IS NULL
                OR feedback_bonus_granted_at <= NOW() - ($3 * INTERVAL '1 day')
              )
            RETURNING cart_limit, feedback_bonus_granted_at
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                user_id,
                safe_amount,
                safe_cooldown_days,
            )
            if row:
                return {
                    "granted": True,
                    "reason": "ok",
                    "bonus": safe_amount,
                    "new_limit": row["cart_limit"],
                    "granted_at": row["feedback_bonus_granted_at"],
                }

            user_row = await conn.fetchrow(
                "SELECT feedback_bonus_granted_at FROM users WHERE user_id = $1",
                user_id,
            )

        if user_row is None:
            return {
                "granted": False,
                "reason": "user_not_found",
                "bonus": safe_amount,
                "new_limit": 0,
            }

        last_granted_at = user_row["feedback_bonus_granted_at"]
        next_bonus_at = None
        if last_granted_at is not None:
            next_bonus_at = last_granted_at + timedelta(days=safe_cooldown_days)

        return {
            "granted": False,
            "reason": "cooldown",
            "bonus": safe_amount,
            "new_limit": 0,
            "last_granted_at": last_granted_at,
            "next_bonus_at": next_bonus_at,
        }

    async def mark_survey_completed(self, user_id: int) -> None:
        """Пометить, что пользователь заполнил survey."""
        await self._ensure_schema()
        sql = """
            UPDATE users
            SET survey_completed = TRUE, updated_at = NOW()
            WHERE user_id = $1
        """
        async with self._pool.acquire() as conn:
            await conn.execute(sql, user_id)

    async def mark_survey_completed_if_not(self, user_id: int) -> bool:
        """Атомарно пометить survey_completed, если ещё не пройден.

        Предотвращает race condition при двойном нажатии кнопки.

        Returns:
            True если пометка выставлена (первый раз), False если уже был пройден.
        """
        await self._ensure_schema()
        sql = """
            UPDATE users SET survey_completed = TRUE, updated_at = NOW()
            WHERE user_id = $1 AND survey_completed = FALSE
            RETURNING user_id
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id)
        return row is not None

    async def get_survey_stats(self) -> dict[str, Any]:
        """Агрегированная статистика по survey (для /admin_survey_stats).

        Returns:
            Словарь с total, pmf (список), features (список),
            feedback_count (int), recent_feedback (список).
        """
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM user_events WHERE event_type = 'survey_completed'"
            )
            pmf = await conn.fetch(
                "SELECT metadata->>'pmf' AS answer, COUNT(*) AS cnt "
                "FROM user_events WHERE event_type = 'survey_completed' "
                "AND metadata->>'pmf' IS NOT NULL "
                "GROUP BY answer ORDER BY cnt DESC"
            )
            features = await conn.fetch(
                "SELECT metadata->>'useful_feature' AS feat, COUNT(*) AS cnt "
                "FROM user_events WHERE event_type = 'survey_completed' "
                "GROUP BY feat ORDER BY cnt DESC"
            )
            feedback_count = await conn.fetchval(
                "SELECT COUNT(*) FROM user_events WHERE event_type = 'survey_completed' "
                "AND metadata->>'feedback' IS NOT NULL "
                "AND metadata->>'feedback' != ''"
            )
            recent_feedback = await conn.fetch(
                "SELECT metadata->>'feedback' AS text, created_at "
                "FROM user_events WHERE event_type = 'survey_completed' "
                "AND metadata->>'feedback' IS NOT NULL "
                "AND metadata->>'feedback' != '' "
                "ORDER BY created_at DESC LIMIT 10"
            )
        return {
            "total": total or 0,
            "pmf": [dict(r) for r in pmf],
            "features": [dict(r) for r in features],
            "feedback_count": feedback_count or 0,
            "recent_feedback": [dict(r) for r in recent_feedback],
        }

    async def get_cart_feedback_stats(self) -> dict[str, Any]:
        """Агрегированная статистика по фидбеку корзин.

        Returns:
            Словарь с total, positive, negative, satisfaction_pct,
            reasons (список), recent_negative (список).
        """
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM user_events WHERE event_type = 'cart_feedback'"
            )
            positive = await conn.fetchval(
                "SELECT COUNT(*) FROM user_events "
                "WHERE event_type = 'cart_feedback' "
                "AND metadata->>'rating' = 'positive'"
            )
            negative = await conn.fetchval(
                "SELECT COUNT(*) FROM user_events "
                "WHERE event_type = 'cart_feedback' "
                "AND metadata->>'rating' = 'negative'"
            )
            reasons = await conn.fetch(
                "SELECT metadata->>'reason' AS reason, COUNT(*) AS cnt "
                "FROM user_events "
                "WHERE event_type = 'cart_feedback' "
                "AND metadata->>'rating' = 'negative' "
                "AND metadata->>'reason' IS NOT NULL "
                "GROUP BY reason ORDER BY cnt DESC"
            )
            recent_negative = await conn.fetch(
                "SELECT u.user_id, e.metadata->>'reason' AS reason, "
                "e.metadata->>'cart_link' AS cart_link, e.created_at "
                "FROM user_events e "
                "JOIN users u ON u.user_id = e.user_id "
                "WHERE e.event_type = 'cart_feedback' "
                "AND e.metadata->>'rating' = 'negative' "
                "ORDER BY e.created_at DESC LIMIT 10"
            )
            daily = await conn.fetch(
                "SELECT DATE(created_at) AS day, "
                "metadata->>'rating' AS rating, COUNT(*) AS cnt "
                "FROM user_events "
                "WHERE event_type = 'cart_feedback' "
                "GROUP BY day, rating ORDER BY day DESC LIMIT 30"
            )
        total = total or 0
        positive = positive or 0
        negative = negative or 0
        satisfaction = round(positive / total * 100, 1) if total > 0 else 0.0
        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "satisfaction_pct": satisfaction,
            "reasons": [dict(r) for r in reasons],
            "recent_negative": [dict(r) for r in recent_negative],
            "daily": [dict(r) for r in daily],
        }
