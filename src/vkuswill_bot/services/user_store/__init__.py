"""Хранилище пользователей (PostgreSQL).

Управление пользователями бота: регистрация, роли, блокировка,
персональные лимиты, статистика и лог событий.

Пакет с bounded contexts: IdentityStore, FreemiumStore, ReferralStore,
VoiceStore, AnalyticsStore, ConsentStore. UserStore — фасад для совместимости.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from ._analytics import AnalyticsStore
from ._consent import ConsentStore
from ._freemium import FreemiumStore
from ._identity import IdentityStore, VALID_ROLES, VALID_STATUSES
from ._referrals import ReferralStore
from ._voice import VoiceStore, VOICE_LINK_CODE_LENGTH

logger = logging.getLogger(__name__)

__all__ = [
    "VALID_ROLES",
    "VALID_STATUSES",
    "VOICE_LINK_CODE_LENGTH",
    "UserStore",
]


class UserStore:
    """Async-хранилище пользователей на базе asyncpg (PostgreSQL).

    Фасад над bounded contexts. Стиль аналогичен ``PreferencesStore`` —
    raw SQL, без ORM.
    """

    def __init__(self, pool: asyncpg.Pool, *, schema_ready: bool = False) -> None:
        self._pool = pool
        self._schema_ready = schema_ready
        self._identity = IdentityStore(pool, self.ensure_schema)
        self._freemium = FreemiumStore(pool, self.ensure_schema)
        self._referrals = ReferralStore(pool, self.ensure_schema)
        self._voice = VoiceStore(pool, self.ensure_schema)
        self._analytics = AnalyticsStore(pool, self.ensure_schema)
        self._consent = ConsentStore(pool, self.ensure_schema)

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

    # IdentityStore
    async def get_or_create(
        self,
        user_id: int,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        """Upsert: создать пользователя или обновить language_code."""
        return await self._identity.get_or_create(user_id, language_code)

    async def get(self, user_id: int) -> dict[str, Any] | None:
        """Получить пользователя по Telegram ID."""
        return await self._identity.get(user_id)

    async def is_blocked(self, user_id: int) -> bool:
        """Проверить, заблокирован ли пользователь."""
        return await self._identity.is_blocked(user_id)

    async def block(self, user_id: int, reason: str = "") -> bool:
        """Заблокировать пользователя."""
        return await self._identity.block(user_id, reason)

    async def unblock(self, user_id: int) -> bool:
        """Разблокировать пользователя."""
        return await self._identity.unblock(user_id)

    async def is_admin(self, user_id: int) -> bool:
        """Проверить, является ли пользователь администратором."""
        return await self._identity.is_admin(user_id)

    async def set_role(self, user_id: int, role: str) -> bool:
        """Установить роль пользователя."""
        return await self._identity.set_role(user_id, role)

    # FreemiumStore
    async def get_limits(self, user_id: int) -> dict[str, Any] | None:
        """Получить персональные лимиты."""
        return await self._freemium.get_limits(user_id)

    async def set_limits(
        self,
        user_id: int,
        rate_limit: int | None,
        rate_period: float | None,
    ) -> bool:
        """Установить персональные лимиты."""
        return await self._freemium.set_limits(user_id, rate_limit, rate_period)

    async def check_cart_limit(
        self,
        user_id: int,
        default_limit: int = 0,
        trial_days: int = 10,
    ) -> dict[str, Any]:
        """Проверить, может ли пользователь создать корзину."""
        return await self._freemium.check_cart_limit(user_id, default_limit, trial_days)

    async def increment_carts(
        self,
        user_id: int,
        trial_days: int = 10,
    ) -> dict[str, Any]:
        """Увеличить счётчик корзин на 1."""
        return await self._freemium.increment_carts(user_id, trial_days)

    async def reset_carts(self, user_id: int) -> dict[str, Any] | None:
        """Сбросить счётчик корзин пользователя до 0."""
        return await self._freemium.reset_carts(user_id)

    async def grant_bonus_carts(self, user_id: int, amount: int = 5) -> int:
        """Увеличить лимит корзин."""
        return await self._freemium.grant_bonus_carts(user_id, amount)

    async def grant_feedback_bonus_if_due(
        self,
        user_id: int,
        amount: int = 2,
        cooldown_days: int = 30,
    ) -> dict[str, Any]:
        """Выдать бонус за feedback, если истёк cooldown."""
        return await self._freemium.grant_feedback_bonus_if_due(user_id, amount, cooldown_days)

    async def mark_survey_completed(self, user_id: int) -> None:
        """Пометить, что пользователь заполнил survey."""
        return await self._freemium.mark_survey_completed(user_id)

    async def mark_survey_completed_if_not(self, user_id: int) -> bool:
        """Атомарно пометить survey_completed, если ещё не пройден."""
        return await self._freemium.mark_survey_completed_if_not(user_id)

    async def get_survey_stats(self) -> dict[str, Any]:
        """Агрегированная статистика по survey."""
        return await self._freemium.get_survey_stats()

    async def get_cart_feedback_stats(self) -> dict[str, Any]:
        """Агрегированная статистика по фидбеку корзин."""
        return await self._freemium.get_cart_feedback_stats()

    # ReferralStore
    async def get_or_create_referral_code(self, user_id: int) -> str:
        """Получить или сгенерировать реферальный код пользователя."""
        return await self._referrals.get_or_create_referral_code(user_id)

    async def find_user_by_referral_code(self, code: str) -> int | None:
        """Найти user_id по реферальному коду."""
        return await self._referrals.find_user_by_referral_code(code)

    async def process_referral(
        self,
        new_user_id: int,
        referrer_id: int,
        _bonus: int | None = None,
    ) -> dict[str, Any]:
        """Обработать реферал: привязать нового пользователя к рефереру."""
        return await self._referrals.process_referral(new_user_id, referrer_id, _bonus)

    async def grant_referral_bonus_for_first_cart(
        self,
        referred_user_id: int,
        bonus: int = 3,
    ) -> dict[str, Any]:
        """Начислить бонус рефереру после первой успешной корзины друга."""
        return await self._referrals.grant_referral_bonus_for_first_cart(referred_user_id, bonus)

    async def count_referrals(self, user_id: int) -> int:
        """Количество пользователей, приглашённых данным пользователем."""
        return await self._referrals.count_referrals(user_id)

    # VoiceStore
    async def create_voice_link_code(
        self,
        user_id: int,
        provider: str = "alice",
        ttl_minutes: int = 10,
    ) -> str:
        """Выдать одноразовый код привязки voice-аккаунта."""
        return await self._voice.create_voice_link_code(user_id, provider, ttl_minutes)

    async def consume_voice_link_code(
        self,
        provider: str,
        voice_user_id: str,
        code: str,
    ) -> dict[str, Any]:
        """Погасить код и привязать voice-user к internal user."""
        return await self._voice.consume_voice_link_code(provider, voice_user_id, code)

    async def resolve_voice_link(
        self,
        provider: str,
        voice_user_id: str,
    ) -> int | None:
        """Вернуть internal user_id по voice account link (active only)."""
        return await self._voice.resolve_voice_link(provider, voice_user_id)

    async def revoke_voice_links_for_user(
        self,
        user_id: int,
        provider: str = "alice",
    ) -> int:
        """Отвязать voice-аккаунты пользователя по провайдеру."""
        return await self._voice.revoke_voice_links_for_user(user_id, provider)

    # AnalyticsStore
    async def increment_message_count(self, user_id: int) -> None:
        """Увеличить счётчик сообщений и обновить last_message_at."""
        return await self._analytics.increment_message_count(user_id)

    async def log_event(
        self,
        user_id: int,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Записать событие в ``user_events``."""
        return await self._analytics.log_event(user_id, event_type, metadata)

    async def get_stats(self, user_id: int) -> dict[str, Any] | None:
        """Получить статистику пользователя."""
        return await self._analytics.get_stats(user_id)

    async def list_users(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Список пользователей (для админ-панели)."""
        return await self._analytics.list_users(limit, offset)

    async def count_users(self) -> int:
        """Общее количество зарегистрированных пользователей."""
        return await self._analytics.count_users()

    async def count_active_today(self) -> int:
        """Количество активных сегодня (DAU)."""
        return await self._analytics.count_active_today()

    async def ensure_admins(self, admin_ids: list[int]) -> None:
        """Гарантировать, что указанные user_id имеют роль admin."""
        return await self._analytics.ensure_admins(admin_ids)

    # ConsentStore
    async def mark_consent(
        self,
        user_id: int,
        consent_type: str = "explicit",
    ) -> bool:
        """Зафиксировать согласие пользователя на обработку данных."""
        return await self._consent.mark_consent(user_id, consent_type)

    async def close(self) -> None:
        """Закрыть пул соединений (делегируется вызывающему коду)."""
        # Пул закрывается в __main__.py; метод для единообразия API.
        logger.info("UserStore: close вызван")
