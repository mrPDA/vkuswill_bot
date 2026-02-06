"""Мидлвари Telegram-бота."""

import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Настройки по умолчанию
DEFAULT_RATE_LIMIT = 5  # сообщений
DEFAULT_RATE_PERIOD = 60.0  # секунд


class ThrottlingMiddleware(BaseMiddleware):
    """Мидлварь для ограничения частоты сообщений от пользователей.

    Лимитирует количество сообщений от одного пользователя
    за указанный период. При превышении — отправляет предупреждение.

    Args:
        rate_limit: Максимальное количество сообщений за период.
        period: Период в секундах.
    """

    def __init__(
        self,
        rate_limit: int = DEFAULT_RATE_LIMIT,
        period: float = DEFAULT_RATE_PERIOD,
    ) -> None:
        self.rate_limit = rate_limit
        self.period = period
        # user_id -> список timestamp-ов сообщений
        self._user_timestamps: dict[int, list[float]] = defaultdict(list)

    def _cleanup_timestamps(self, user_id: int, now: float) -> None:
        """Удалить устаревшие записи для пользователя."""
        cutoff = now - self.period
        self._user_timestamps[user_id] = [
            ts for ts in self._user_timestamps[user_id] if ts > cutoff
        ]

    def _is_rate_limited(self, user_id: int) -> bool:
        """Проверить, превышен ли лимит для пользователя."""
        now = time.monotonic()
        self._cleanup_timestamps(user_id, now)
        timestamps = self._user_timestamps[user_id]

        if len(timestamps) >= self.rate_limit:
            logger.warning(
                "Rate limit: пользователь %d превысил лимит (%d/%d за %.0f сек)",
                user_id,
                len(timestamps),
                self.rate_limit,
                self.period,
            )
            return True

        timestamps.append(now)
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

        if self._is_rate_limited(user.id):
            wait_seconds = int(self.period)
            await event.answer(
                f"⏳ Слишком много сообщений. "
                f"Подождите {wait_seconds} секунд перед следующим запросом."
            )
            return None

        return await handler(event, data)
