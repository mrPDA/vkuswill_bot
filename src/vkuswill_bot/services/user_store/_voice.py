"""Voice store: привязка voice-аккаунтов (Алиса и др.)."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

VOICE_LINK_CODE_LENGTH = 6


class VoiceStore:
    """Хранилище voice account linking."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        ensure_schema: Callable[[], Awaitable[None]],
    ) -> None:
        self._pool = pool
        self._ensure_schema = ensure_schema

    @staticmethod
    def _hash_voice_link_code(provider: str, code: str) -> str:
        """Хешировать код привязки (в базе храним только hash)."""
        payload = f"{provider}:{code.strip()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _generate_voice_link_code() -> str:
        """Сгенерировать короткий одноразовый код привязки."""
        max_value = 10**VOICE_LINK_CODE_LENGTH
        return f"{secrets.randbelow(max_value):0{VOICE_LINK_CODE_LENGTH}d}"

    async def create_voice_link_code(
        self,
        user_id: int,
        provider: str = "alice",
        ttl_minutes: int = 10,
    ) -> str:
        """Выдать одноразовый код привязки voice-аккаунта.

        У пользователя может быть только один активный код на provider.
        При повторном запросе старые коды инвалидируются.
        """
        await self._ensure_schema()

        provider = provider.strip().lower()
        if not provider:
            raise ValueError("provider должен быть непустым")
        if ttl_minutes < 1:
            raise ValueError("ttl_minutes должен быть >= 1")

        expires_at = datetime.now(UTC) + timedelta(minutes=ttl_minutes)

        async with self._pool.acquire() as conn:
            # Инвалидируем прошлые активные коды пользователя для provider.
            await conn.execute(
                """
                UPDATE voice_link_codes
                SET used_at = NOW()
                WHERE user_id = $1
                  AND voice_provider = $2
                  AND used_at IS NULL
                """,
                user_id,
                provider,
            )

            for _ in range(10):
                code = self._generate_voice_link_code()
                code_hash = self._hash_voice_link_code(provider, code)
                try:
                    await conn.execute(
                        """
                        INSERT INTO voice_link_codes (
                            voice_provider,
                            user_id,
                            code_hash,
                            expires_at
                        )
                        VALUES ($1, $2, $3, $4)
                        """,
                        provider,
                        user_id,
                        code_hash,
                        expires_at,
                    )
                    return code
                except asyncpg.UniqueViolationError:
                    continue

        raise RuntimeError("Не удалось сгенерировать уникальный voice link code")

    async def consume_voice_link_code(
        self,
        provider: str,
        voice_user_id: str,
        code: str,
    ) -> dict[str, Any]:
        """Погасить код и привязать voice-user к internal user.

        Returns:
            ``{"ok": bool, "reason": str, "user_id": int | None}``
        """
        await self._ensure_schema()
        provider = provider.strip().lower()
        voice_user_id = voice_user_id.strip()
        code = code.strip()
        if not provider or not voice_user_id or not code:
            return {"ok": False, "reason": "invalid_input", "user_id": None}

        code_hash = self._hash_voice_link_code(provider, code)
        now = datetime.now(UTC)

        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, user_id, expires_at
                FROM voice_link_codes
                WHERE voice_provider = $1
                  AND code_hash = $2
                  AND used_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                FOR UPDATE
                """,
                provider,
                code_hash,
            )
            if row is None:
                return {"ok": False, "reason": "invalid_code", "user_id": None}

            if row["expires_at"] < now:
                await conn.execute(
                    "UPDATE voice_link_codes SET used_at = NOW() WHERE id = $1",
                    row["id"],
                )
                return {"ok": False, "reason": "code_expired", "user_id": None}

            # Один пользователь -> одна активная связь на provider.
            await conn.execute(
                """
                DELETE FROM voice_account_links
                WHERE voice_provider = $1
                  AND user_id = $2
                """,
                provider,
                row["user_id"],
            )

            await conn.execute(
                """
                INSERT INTO voice_account_links (
                    voice_provider,
                    voice_user_id,
                    user_id,
                    status,
                    linked_at,
                    last_used_at,
                    updated_at
                )
                VALUES ($1, $2, $3, 'active', NOW(), NOW(), NOW())
                ON CONFLICT (voice_provider, voice_user_id)
                DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    status = 'active',
                    linked_at = NOW(),
                    last_used_at = NOW(),
                    updated_at = NOW()
                """,
                provider,
                voice_user_id,
                row["user_id"],
            )

            await conn.execute(
                """
                UPDATE voice_link_codes
                SET used_at = NOW(),
                    used_by_voice_user_id = $2
                WHERE id = $1
                """,
                row["id"],
                voice_user_id,
            )

            return {"ok": True, "reason": "ok", "user_id": row["user_id"]}

    async def resolve_voice_link(
        self,
        provider: str,
        voice_user_id: str,
    ) -> int | None:
        """Вернуть internal user_id по voice account link (active only)."""
        await self._ensure_schema()
        provider = provider.strip().lower()
        voice_user_id = voice_user_id.strip()
        if not provider or not voice_user_id:
            return None

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id
                FROM voice_account_links
                WHERE voice_provider = $1
                  AND voice_user_id = $2
                  AND status = 'active'
                LIMIT 1
                """,
                provider,
                voice_user_id,
            )
            if row is None:
                return None

            await conn.execute(
                """
                UPDATE voice_account_links
                SET last_used_at = NOW(),
                    updated_at = NOW()
                WHERE voice_provider = $1
                  AND voice_user_id = $2
                """,
                provider,
                voice_user_id,
            )
            return row["user_id"]

    async def revoke_voice_links_for_user(
        self,
        user_id: int,
        provider: str = "alice",
    ) -> int:
        """Отвязать voice-аккаунты пользователя по провайдеру.

        Возвращает количество деактивированных активных связей.
        """
        await self._ensure_schema()
        provider = provider.strip().lower()
        if not provider:
            raise ValueError("provider должен быть непустым")

        sql = """
            UPDATE voice_account_links
            SET status = 'revoked',
                updated_at = NOW()
            WHERE user_id = $1
              AND voice_provider = $2
              AND status = 'active'
        """
        async with self._pool.acquire() as conn:
            status = await conn.execute(sql, user_id, provider)
        # asyncpg возвращает строку вида "UPDATE <n>".
        try:
            return int(str(status).split()[-1])
        except (ValueError, IndexError):
            return 0
