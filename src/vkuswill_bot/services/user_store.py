"""Хранилище пользователей (PostgreSQL).

Управление пользователями бота: регистрация, роли, блокировка,
персональные лимиты, статистика и лог событий.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Допустимые значения для CHECK-ограничений
VALID_ROLES = frozenset({"user", "admin"})
VALID_STATUSES = frozenset({"active", "blocked", "limited"})

VOICE_LINK_CODE_LENGTH = 6


class UserStore:
    """Async-хранилище пользователей на базе asyncpg (PostgreSQL).

    Стиль аналогичен ``PreferencesStore`` — raw SQL, без ORM.
    """

    def __init__(self, pool: asyncpg.Pool, *, schema_ready: bool = False) -> None:
        self._pool = pool
        self._schema_ready = schema_ready

    # ------------------------------------------------------------------
    # Инициализация схемы
    # ------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Применить все SQL-миграции через MigrationRunner.

        Безопасно вызывать многократно — уже применённые миграции
        пропускаются (отслеживаются в таблице ``schema_migrations``).

        В production миграции запускаются один раз из ``__main__.py``;
        повторный вызов здесь — подстраховка для standalone-скриптов.
        """
        if self._schema_ready:
            return
        from vkuswill_bot.services.migration_runner import MigrationRunner

        runner = MigrationRunner(self._pool)
        await runner.run()
        self._schema_ready = True
        logger.info("PostgreSQL: схема актуальна (MigrationRunner)")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

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
        await self.ensure_schema()
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
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1",
                user_id,
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
                "SELECT status FROM users WHERE user_id = $1",
                user_id,
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
        self,
        user_id: int,
        rate_limit: int | None,
        rate_period: float | None,
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

    # ------------------------------------------------------------------
    # Freemium: лимиты корзин
    # ------------------------------------------------------------------

    async def check_cart_limit(
        self,
        user_id: int,
        default_limit: int = 5,
    ) -> dict[str, Any]:
        """Проверить, может ли пользователь создать корзину.

        Args:
            default_limit: лимит по умолчанию, если пользователь не найден.

        Returns:
            ``{"allowed": bool, "carts_created": int, "cart_limit": int,
            "survey_completed": bool}``
        """
        await self.ensure_schema()
        sql = "SELECT carts_created, cart_limit, survey_completed FROM users WHERE user_id = $1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id)
        if not row:
            return {
                "allowed": True,
                "carts_created": 0,
                "cart_limit": default_limit,
                "survey_completed": False,
            }
        return {
            "allowed": row["carts_created"] < row["cart_limit"],
            "carts_created": row["carts_created"],
            "cart_limit": row["cart_limit"],
            "survey_completed": bool(row["survey_completed"]),
        }

    async def increment_carts(self, user_id: int) -> dict[str, Any]:
        """Увеличить счётчик корзин на 1.

        Returns:
            ``{"carts_created": int, "cart_limit": int, "survey_completed": bool}``
        """
        await self.ensure_schema()
        sql = """
            UPDATE users
            SET carts_created = carts_created + 1, updated_at = NOW()
            WHERE user_id = $1
            RETURNING carts_created, cart_limit, survey_completed
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id)
        return dict(row) if row else {}

    async def reset_carts(self, user_id: int) -> dict[str, Any] | None:
        """Сбросить счётчик корзин пользователя до 0.

        Также сбрасывает survey_completed и cart_limit до дефолта.

        Returns:
            Обновлённые данные пользователя или None, если не найден.
        """
        await self.ensure_schema()
        sql = """
            UPDATE users
            SET carts_created = 0,
                cart_limit = 5,
                survey_completed = FALSE,
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
        await self.ensure_schema()
        sql = """
            UPDATE users
            SET cart_limit = cart_limit + $2, updated_at = NOW()
            WHERE user_id = $1
            RETURNING cart_limit
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, user_id, amount)
        return row["cart_limit"] if row else 0

    async def mark_survey_completed(self, user_id: int) -> None:
        """Пометить, что пользователь заполнил survey."""
        await self.ensure_schema()
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
        await self.ensure_schema()
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
        await self.ensure_schema()
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
        await self.ensure_schema()
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

    # ------------------------------------------------------------------
    # Реферальная система
    # ------------------------------------------------------------------

    async def get_or_create_referral_code(self, user_id: int) -> str:
        """Получить или сгенерировать реферальный код пользователя.

        Код — 8-символьная URL-safe строка, хранится в ``referral_code``.

        Returns:
            Реферальный код пользователя.
        """
        import secrets

        await self.ensure_schema()
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
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT user_id FROM users WHERE referral_code = $1",
                code,
            )

    async def process_referral(
        self,
        new_user_id: int,
        referrer_id: int,
        bonus: int = 3,
    ) -> dict[str, Any]:
        """Обработать реферал: привязать нового пользователя и начислить бонус.

        Проверки:
        - Нельзя пригласить самого себя.
        - Нельзя привязаться повторно (``referred_by`` уже установлен).
        - Реферер должен существовать.

        Returns:
            ``{"success": bool, "reason": str, "bonus": int, "new_limit": int}``
        """
        await self.ensure_schema()

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

            # Привязываем реферера
            await conn.execute(
                "UPDATE users SET referred_by = $2, updated_at = NOW() WHERE user_id = $1",
                new_user_id,
                referrer_id,
            )

            # Начисляем бонус рефереру
            row = await conn.fetchrow(
                "UPDATE users SET cart_limit = cart_limit + $2, updated_at = NOW() "
                "WHERE user_id = $1 RETURNING cart_limit",
                referrer_id,
                bonus,
            )
            new_limit = row["cart_limit"] if row else 0

        return {
            "success": True,
            "reason": "ok",
            "bonus": bonus,
            "new_limit": new_limit,
        }

    async def count_referrals(self, user_id: int) -> int:
        """Количество пользователей, приглашённых данным пользователем."""
        await self.ensure_schema()
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE referred_by = $1",
                user_id,
            )
        return result or 0

    # ------------------------------------------------------------------
    # Voice account linking (Алиса и др.)
    # ------------------------------------------------------------------

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
        await self.ensure_schema()

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
        await self.ensure_schema()
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
        await self.ensure_schema()
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

    # ------------------------------------------------------------------
    # Админские запросы
    # ------------------------------------------------------------------

    async def list_users(
        self,
        limit: int = 50,
        offset: int = 0,
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
        await self.ensure_schema()
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

    # ------------------------------------------------------------------
    # Информированное согласие (ADR-002)
    # ------------------------------------------------------------------

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
        await self.ensure_schema()
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Закрыть пул соединений (делегируется вызывающему коду)."""
        # Пул закрывается в __main__.py; метод для единообразия API.
        logger.info("UserStore: close вызван")
