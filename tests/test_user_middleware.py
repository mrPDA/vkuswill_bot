"""Тесты для UserMiddleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from vkuswill_bot.bot.middlewares import UserMiddleware


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_message(user_id: int = 123, username: str = "testuser") -> MagicMock:
    """Создать мок aiogram.types.Message (проходит isinstance-проверку)."""
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.from_user.first_name = "Test"
    msg.from_user.last_name = "User"
    msg.from_user.language_code = "ru"
    msg.answer = AsyncMock()
    return msg


def _make_non_message_event() -> MagicMock:
    """Создать мок события, которое НЕ является Message."""
    event = MagicMock(spec=[])  # Пустой spec → не isinstance(Message)
    return event


def _make_user_store(
    user_data: dict | None = None,
    get_or_create_error: Exception | None = None,
) -> AsyncMock:
    """Создать мок UserStore."""
    store = AsyncMock()

    if get_or_create_error:
        store.get_or_create.side_effect = get_or_create_error
    else:
        store.get_or_create.return_value = user_data or {
            "user_id": 123,
            "username": "testuser",
            "first_name": "Test",
            "role": "user",
            "status": "active",
            "rate_limit": None,
            "rate_period": None,
        }

    store.increment_message_count = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestUserMiddlewareNormalFlow:
    """Тесты нормального потока работы."""

    @pytest.mark.asyncio
    async def test_active_user_passes_through(self):
        """Активный пользователь проходит middleware."""
        store = _make_user_store()
        mw = UserMiddleware(store)
        handler = AsyncMock(return_value="ok")
        msg = _make_message()

        result = await mw(handler, msg, {})

        assert result == "ok"
        handler.assert_called_once()
        store.get_or_create.assert_called_once()
        store.increment_message_count.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_injects_db_user_into_data(self):
        """Middleware инжектирует db_user и user_store в data."""
        user_data = {
            "user_id": 123,
            "username": "testuser",
            "role": "user",
            "status": "active",
            "rate_limit": None,
        }
        store = _make_user_store(user_data)
        mw = UserMiddleware(store)
        handler = AsyncMock()
        msg = _make_message()
        data: dict = {}

        await mw(handler, msg, data)

        assert data["db_user"] == user_data
        assert data["user_store"] is store

    @pytest.mark.asyncio
    async def test_injects_user_limits_when_set(self):
        """Middleware инжектирует user_limits если заданы."""
        user_data = {
            "user_id": 123,
            "status": "active",
            "rate_limit": 10,
            "rate_period": 30.0,
        }
        store = _make_user_store(user_data)
        mw = UserMiddleware(store)
        handler = AsyncMock()
        data: dict = {}

        await mw(handler, _make_message(), data)

        assert data["user_limits"] == {"rate_limit": 10, "rate_period": 30.0}

    @pytest.mark.asyncio
    async def test_no_user_limits_when_default(self):
        """Middleware НЕ инжектирует user_limits если лимиты дефолтные."""
        store = _make_user_store()  # rate_limit=None
        mw = UserMiddleware(store)
        handler = AsyncMock()
        data: dict = {}

        await mw(handler, _make_message(), data)

        assert "user_limits" not in data


class TestUserMiddlewareBlocking:
    """Тесты блокировки пользователей."""

    @pytest.mark.asyncio
    async def test_blocked_user_rejected(self):
        """Заблокированный пользователь отклоняется."""
        user_data = {
            "user_id": 123,
            "status": "blocked",
            "blocked_reason": "спам",
            "rate_limit": None,
        }
        store = _make_user_store(user_data)
        mw = UserMiddleware(store)
        handler = AsyncMock()
        msg = _make_message()

        result = await mw(handler, msg, {})

        assert result is None
        handler.assert_not_called()
        msg.answer.assert_called_once()
        answer_text = msg.answer.call_args[0][0]
        assert "заблокирован" in answer_text
        assert "спам" in answer_text

    @pytest.mark.asyncio
    async def test_blocked_user_no_reason(self):
        """Заблокированный пользователь без причины."""
        user_data = {
            "user_id": 123,
            "status": "blocked",
            "blocked_reason": "",
            "rate_limit": None,
        }
        store = _make_user_store(user_data)
        mw = UserMiddleware(store)
        handler = AsyncMock()
        msg = _make_message()

        await mw(handler, msg, {})

        answer_text = msg.answer.call_args[0][0]
        assert "заблокирован" in answer_text
        assert "Причина" not in answer_text


class TestUserMiddlewareEdgeCases:
    """Тесты граничных случаев."""

    @pytest.mark.asyncio
    async def test_no_from_user_passes_through(self):
        """Сообщение без from_user проходит без обработки."""
        store = _make_user_store()
        mw = UserMiddleware(store)
        handler = AsyncMock(return_value="ok")
        msg = MagicMock(spec=Message)
        msg.from_user = None

        result = await mw(handler, msg, {})

        assert result == "ok"
        store.get_or_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_postgres_error_passes_through(self):
        """При ошибке PostgreSQL пользователь пропускается."""
        store = _make_user_store(get_or_create_error=ConnectionError("no pg"))
        mw = UserMiddleware(store)
        handler = AsyncMock(return_value="ok")
        msg = _make_message()

        result = await mw(handler, msg, {})

        assert result == "ok"
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_increment_error_ignored(self):
        """Ошибка инкремента счётчика не блокирует обработку."""
        store = _make_user_store()
        store.increment_message_count.side_effect = RuntimeError("db error")
        mw = UserMiddleware(store)
        handler = AsyncMock(return_value="ok")
        msg = _make_message()

        result = await mw(handler, msg, {})

        assert result == "ok"
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_message_event_passes_through(self):
        """Событие, не являющееся Message, проходит без обработки."""
        store = _make_user_store()
        mw = UserMiddleware(store)
        handler = AsyncMock(return_value="ok")
        event = _make_non_message_event()

        result = await mw(handler, event, {})

        assert result == "ok"
        store.get_or_create.assert_not_called()
        store.increment_message_count.assert_not_called()
