"""Мидлвари Telegram-бота."""

from __future__ import annotations

import logging
import time
from datetime import datetime, UTC
from typing import TYPE_CHECKING, Any
from collections.abc import Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

if TYPE_CHECKING:
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

# Настройки по умолчанию
DEFAULT_RATE_LIMIT = 5  # сообщений
DEFAULT_RATE_PERIOD = 60.0  # секунд

# Защита от DDoS: максимальное число отслеживаемых пользователей
DEFAULT_MAX_TRACKED_USERS = 10_000
# Интервал полной очистки устаревших записей (секунды)
_FULL_CLEANUP_INTERVAL = 300.0


# ---------------------------------------------------------------------------
# UserMiddleware — регистрация / блокировка / инъекция user в data
# ---------------------------------------------------------------------------


class UserMiddleware(BaseMiddleware):
    """Мидлварь управления пользователями.

    Выполняется **до** ThrottlingMiddleware. Обязанности:
    - Upsert пользователя (get_or_create) при каждом входящем сообщении.
    - Проверка блокировки (``status == 'blocked'`` → отказ).
    - Инъекция данных пользователя в ``data["db_user"]`` для хендлеров.
    - Инъекция персональных лимитов в ``data["user_limits"]``
      (подхватывается ``ThrottlingMiddleware``).
    - Инкремент счётчика сообщений.
    """

    def __init__(self, user_store: UserStore) -> None:
        self._user_store = user_store

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        """Обработка входящего сообщения."""
        if not isinstance(event, Message):
            return await handler(event, data)

        tg_user = event.from_user
        if tg_user is None:
            return await handler(event, data)

        # Upsert: создать или обновить метаданные
        try:
            db_user = await self._user_store.get_or_create(
                user_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name or "",
                last_name=tg_user.last_name,
                language_code=tg_user.language_code,
            )
        except Exception as exc:
            # Если PostgreSQL недоступен — пропускаем, не блокируем бота
            logger.error("UserMiddleware: ошибка upsert для %d: %s", tg_user.id, exc)
            return await handler(event, data)

        # Проверка блокировки
        if db_user.get("status") == "blocked":
            reason = db_user.get("blocked_reason") or ""
            logger.info(
                "Заблокированный пользователь %d пытался отправить сообщение",
                tg_user.id,
            )
            msg = "Ваш аккаунт заблокирован."
            if reason:
                msg += f" Причина: {reason}"
            await event.answer(msg)
            return None

        # Пробрасываем в data для хендлеров и ThrottlingMiddleware
        data["db_user"] = db_user
        data["user_store"] = self._user_store

        # --- Событие: начало сессии (>30 мин с последнего сообщения) ---
        last_msg_at = db_user.get("last_message_at")
        if last_msg_at is None or (datetime.now(UTC) - last_msg_at).total_seconds() > 1800:
            try:
                _created_at = db_user.get("created_at")
                _day_number = 0
                if _created_at:
                    _day_number = (datetime.now(UTC) - _created_at).days
                await self._user_store.log_event(
                    tg_user.id,
                    "session_start",
                    {
                        "day_number": _day_number,
                        "is_first_session": last_msg_at is None,
                    },
                )
            except Exception:
                logger.debug("Ошибка логирования session_start")

        # Персональные лимиты (если заданы)
        if db_user.get("rate_limit") is not None:
            data["user_limits"] = {
                "rate_limit": db_user["rate_limit"],
                "rate_period": db_user.get("rate_period"),
            }

        # Инкремент счётчика сообщений (fire-and-forget)
        try:
            await self._user_store.increment_message_count(tg_user.id)
        except Exception as exc:
            logger.debug("UserMiddleware: ошибка инкремента: %s", exc)

        return await handler(event, data)


# ---------------------------------------------------------------------------
# ThrottlingMiddleware — rate limiting
# ---------------------------------------------------------------------------


class ThrottlingMiddleware(BaseMiddleware):
    """Мидлварь для ограничения частоты сообщений от пользователей.

    Лимитирует количество сообщений от одного пользователя
    за указанный период. При превышении — отправляет предупреждение.

    Поддерживает персональные лимиты из ``data["user_limits"]``
    (устанавливаются ``UserMiddleware``).

    Содержит защиту от неограниченного роста памяти:
    - периодическая полная очистка устаревших записей;
    - жёсткий лимит на число отслеживаемых пользователей.

    Args:
        rate_limit: Максимальное количество сообщений за период.
        period: Период в секундах.
        max_tracked_users: Максимум отслеживаемых пользователей.
    """

    def __init__(
        self,
        rate_limit: int = DEFAULT_RATE_LIMIT,
        period: float = DEFAULT_RATE_PERIOD,
        max_tracked_users: int = DEFAULT_MAX_TRACKED_USERS,
    ) -> None:
        self.rate_limit = rate_limit
        self.period = period
        self._max_tracked_users = max_tracked_users
        # user_id -> список timestamp-ов сообщений
        self._user_timestamps: dict[int, list[float]] = {}
        self._last_full_cleanup: float = time.monotonic()

    def _full_cleanup(self, now: float) -> None:
        """Полная очистка: удалить всех пользователей с устаревшими записями."""
        cutoff = now - self.period
        stale_users = [
            uid
            for uid, timestamps in self._user_timestamps.items()
            if not timestamps or timestamps[-1] <= cutoff
        ]
        for uid in stale_users:
            del self._user_timestamps[uid]
        self._last_full_cleanup = now
        if stale_users:
            logger.debug(
                "Throttle full cleanup: удалено %d устаревших записей, осталось %d",
                len(stale_users),
                len(self._user_timestamps),
            )

    def _cleanup_timestamps(self, user_id: int, now: float) -> None:
        """Удалить устаревшие записи для пользователя."""
        timestamps = self._user_timestamps.get(user_id)
        if not timestamps:
            return
        cutoff = now - self.period
        self._user_timestamps[user_id] = [ts for ts in timestamps if ts > cutoff]
        # Удаляем ключ если список пуст — не держим мусор
        if not self._user_timestamps[user_id]:
            del self._user_timestamps[user_id]

    def _is_rate_limited(
        self,
        user_id: int,
        limit_override: int | None = None,
        period_override: float | None = None,
    ) -> bool:
        """Проверить, превышен ли лимит для пользователя.

        Args:
            limit_override: Персональный лимит (из UserStore).
            period_override: Персональный период (из UserStore).
        """
        effective_limit = limit_override or self.rate_limit
        effective_period = period_override or self.period
        now = time.monotonic()

        # Периодическая полная очистка для защиты от роста памяти
        if now - self._last_full_cleanup >= _FULL_CLEANUP_INTERVAL:
            self._full_cleanup(now)

        # Жёсткий лимит: если словарь переполнен — форсируем очистку
        if len(self._user_timestamps) >= self._max_tracked_users:
            self._full_cleanup(now)
            # Если после очистки всё ещё слишком много — пропускаем
            # tracking для нового пользователя (rate limit не применяется,
            # но память не растёт)
            if (
                len(self._user_timestamps) >= self._max_tracked_users
                and user_id not in self._user_timestamps
            ):
                logger.warning(
                    "Throttle overflow: %d tracked users, пропускаем tracking для user %d",
                    len(self._user_timestamps),
                    user_id,
                )
                return False

        self._cleanup_timestamps(user_id, now)
        timestamps = self._user_timestamps.get(user_id, [])

        if len(timestamps) >= effective_limit:
            logger.warning(
                "Rate limit: пользователь %d превысил лимит (%d/%d за %.0f сек)",
                user_id,
                len(timestamps),
                effective_limit,
                effective_period,
            )
            return True

        if user_id not in self._user_timestamps:
            self._user_timestamps[user_id] = []
        self._user_timestamps[user_id].append(now)
        return False

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        """Обработка входящего сообщения с проверкой rate limit."""
        if not isinstance(event, Message):
            return await handler(event, data)

        user = event.from_user
        if user is None:
            return await handler(event, data)

        # Персональные лимиты из UserMiddleware (если доступны)
        user_limits = data.get("user_limits")
        limit_override = None
        period_override = None
        if user_limits:
            limit_override = user_limits.get("rate_limit")
            period_override = user_limits.get("rate_period")

        effective_period = period_override or self.period

        if self._is_rate_limited(user.id, limit_override, period_override):
            wait_seconds = int(effective_period)
            await event.answer(
                f"⏳ Слишком много сообщений. "
                f"Подождите {wait_seconds} секунд перед следующим запросом."
            )
            return None

        return await handler(event, data)
