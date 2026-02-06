"""Тесты ThrottlingMiddleware.

Тестируем:
- Пропуск сообщений в пределах лимита
- Блокировка при превышении лимита
- Сброс лимита после истечения периода
- Изоляция лимитов между пользователями
- Обработка сообщений без from_user
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vkuswill_bot.bot.middlewares import ThrottlingMiddleware


def _make_message_event(user_id: int = 1) -> MagicMock:
    """Создать мок Message для middleware."""
    from aiogram.types import Message

    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


class TestThrottlingMiddleware:
    """Тесты rate-limiting middleware."""

    async def test_allows_within_limit(self):
        """Сообщения в пределах лимита проходят."""
        mw = ThrottlingMiddleware(rate_limit=3, period=60.0)
        handler = AsyncMock(return_value="ok")

        for _ in range(3):
            event = _make_message_event(user_id=1)
            result = await mw(handler, event, {})
            assert result == "ok"

        assert handler.call_count == 3

    async def test_blocks_over_limit(self):
        """Сообщения сверх лимита блокируются."""
        mw = ThrottlingMiddleware(rate_limit=2, period=60.0)
        handler = AsyncMock(return_value="ok")

        # Первые 2 проходят
        for _ in range(2):
            event = _make_message_event(user_id=1)
            await mw(handler, event, {})

        assert handler.call_count == 2

        # 3-е блокируется
        event = _make_message_event(user_id=1)
        result = await mw(handler, event, {})

        assert result is None
        event.answer.assert_called_once()
        answer_text = event.answer.call_args[0][0]
        assert "Слишком много" in answer_text or "Подождите" in answer_text

    async def test_resets_after_period(self):
        """Лимит сбрасывается после истечения периода."""
        mw = ThrottlingMiddleware(rate_limit=2, period=1.0)
        handler = AsyncMock(return_value="ok")

        # Исчерпываем лимит
        for _ in range(2):
            event = _make_message_event(user_id=1)
            await mw(handler, event, {})

        # Мокаем time.monotonic чтобы "перемотать" время
        original_timestamps = mw._user_timestamps[1].copy()
        # Устанавливаем timestamps в прошлое
        mw._user_timestamps[1] = [t - 2.0 for t in original_timestamps]

        # Теперь сообщение проходит
        event = _make_message_event(user_id=1)
        result = await mw(handler, event, {})
        assert result == "ok"

    async def test_independent_user_limits(self):
        """У каждого пользователя свой лимит."""
        mw = ThrottlingMiddleware(rate_limit=1, period=60.0)
        handler = AsyncMock(return_value="ok")

        # Пользователь 1 — первое сообщение
        event1 = _make_message_event(user_id=1)
        result = await mw(handler, event1, {})
        assert result == "ok"

        # Пользователь 1 — второе (заблокировано)
        event1b = _make_message_event(user_id=1)
        result = await mw(handler, event1b, {})
        assert result is None

        # Пользователь 2 — первое (проходит, свой лимит)
        event2 = _make_message_event(user_id=2)
        result = await mw(handler, event2, {})
        assert result == "ok"

    async def test_no_user_passes_through(self):
        """Сообщение без from_user проходит без проверки."""
        mw = ThrottlingMiddleware(rate_limit=1, period=60.0)
        handler = AsyncMock(return_value="ok")

        event = _make_message_event()
        event.from_user = None
        result = await mw(handler, event, {})
        assert result == "ok"

    async def test_non_message_event_passes(self):
        """Событие не-Message проходит без проверки."""
        mw = ThrottlingMiddleware(rate_limit=1, period=60.0)
        handler = AsyncMock(return_value="ok")

        event = MagicMock()  # Не Message
        # Убираем spec чтобы isinstance проверка не прошла
        event.__class__ = type("NotMessage", (), {})
        result = await mw(handler, event, {})
        assert result == "ok"

    async def test_default_parameters(self):
        """Значения по умолчанию корректны."""
        mw = ThrottlingMiddleware()
        assert mw.rate_limit == 5
        assert mw.period == 60.0

    async def test_custom_parameters(self):
        """Кастомные параметры принимаются."""
        mw = ThrottlingMiddleware(rate_limit=10, period=120.0)
        assert mw.rate_limit == 10
        assert mw.period == 120.0
