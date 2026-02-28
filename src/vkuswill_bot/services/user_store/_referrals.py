"""Referral store: реферальные коды и бонусы."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg


class ReferralStore:
    """Хранилище реферальной системы."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        ensure_schema: Callable[[], Awaitable[None]],
    ) -> None:
        self._pool = pool
        self._ensure_schema = ensure_schema

    async def get_or_create_referral_code(self, user_id: int) -> str:
        """Получить или сгенерировать реферальный код пользователя.

        Код — 8-символьная URL-safe строка, хранится в ``referral_code``.

        Returns:
            Реферальный код пользователя.
        """
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT referral_code FROM users WHERE user_id = $1",
                user_id,
            )
            if existing:
                return existing

            # Генерируем уникальный код с retry при коллизии
            for _ in range(5):
                code = secrets.token_urlsafe(6)[:8]
                try:
                    await conn.execute(
                        "UPDATE users SET referral_code = $2, updated_at = NOW() "
                        "WHERE user_id = $1",
                        user_id,
                        code,
                    )
                    return code
                except asyncpg.UniqueViolationError:
                    continue

            # Fallback: код на основе user_id
            code = f"u{user_id}"
            await conn.execute(
                "UPDATE users SET referral_code = $2, updated_at = NOW() WHERE user_id = $1",
                user_id,
                code,
            )
            return code

    async def find_user_by_referral_code(self, code: str) -> int | None:
        """Найти user_id по реферальному коду.

        Returns:
            user_id владельца кода или None.
        """
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT user_id FROM users WHERE referral_code = $1",
                code,
            )

    async def process_referral(
        self,
        new_user_id: int,
        referrer_id: int,
        _bonus: int | None = None,
    ) -> dict[str, Any]:
        """Обработать реферал: привязать нового пользователя к рефереру.

        Проверки:
        - Нельзя пригласить самого себя.
        - Нельзя привязаться повторно (``referred_by`` уже установлен).
        - Реферер и новый пользователь должны существовать.

        Returns:
            ``{"success": bool, "reason": str, "referrer_id": int}``
        """
        await self._ensure_schema()

        if new_user_id == referrer_id:
            return {"success": False, "reason": "self_referral"}

        async with self._pool.acquire() as conn:
            # Проверяем, что новый пользователь ещё не привязан
            row = await conn.fetchrow(
                "SELECT referred_by FROM users WHERE user_id = $1",
                new_user_id,
            )
            if row and row["referred_by"] is not None:
                return {"success": False, "reason": "already_referred"}

            # Проверяем, что реферер существует
            referrer_exists = await conn.fetchval(
                "SELECT 1 FROM users WHERE user_id = $1",
                referrer_id,
            )
            if not referrer_exists:
                return {"success": False, "reason": "referrer_not_found"}

            # Привязываем реферера к пользователю
            linked = await conn.fetchrow(
                "UPDATE users SET referred_by = $2, updated_at = NOW() "
                "WHERE user_id = $1 RETURNING user_id",
                new_user_id,
                referrer_id,
            )
            if linked is None:
                return {"success": False, "reason": "new_user_not_found"}

        return {"success": True, "reason": "linked", "referrer_id": referrer_id}

    async def grant_referral_bonus_for_first_cart(
        self,
        referred_user_id: int,
        bonus: int = 3,
    ) -> dict[str, Any]:
        """Начислить бонус рефереру после первой успешной корзины друга.

        Бонус выдаётся один раз на приглашённого пользователя.
        """
        await self._ensure_schema()
        safe_bonus = max(0, bonus)

        async with self._pool.acquire() as conn, conn.transaction():
            referral_row = await conn.fetchrow(
                """
                SELECT referred_by, referral_bonus_granted_at
                FROM users
                WHERE user_id = $1
                FOR UPDATE
                """,
                referred_user_id,
            )
            if referral_row is None:
                return {"granted": False, "reason": "user_not_found"}

            referrer_id = referral_row["referred_by"]
            if referrer_id is None:
                return {"granted": False, "reason": "not_referred"}

            if referral_row["referral_bonus_granted_at"] is not None:
                return {
                    "granted": False,
                    "reason": "already_granted",
                    "referrer_id": referrer_id,
                }

            referrer_row = await conn.fetchrow(
                """
                UPDATE users
                SET cart_limit = cart_limit + $2, updated_at = NOW()
                WHERE user_id = $1
                RETURNING cart_limit
                """,
                referrer_id,
                safe_bonus,
            )
            if referrer_row is None:
                return {"granted": False, "reason": "referrer_not_found"}

            await conn.execute(
                """
                UPDATE users
                SET referral_bonus_granted_at = NOW(), updated_at = NOW()
                WHERE user_id = $1
                """,
                referred_user_id,
            )

            return {
                "granted": True,
                "reason": "ok",
                "referrer_id": referrer_id,
                "bonus": safe_bonus,
                "new_limit": referrer_row["cart_limit"],
            }

    async def count_referrals(self, user_id: int) -> int:
        """Количество пользователей, приглашённых данным пользователем."""
        await self._ensure_schema()
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE referred_by = $1",
                user_id,
            )
        return result or 0
