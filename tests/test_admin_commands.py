"""Тесты для админ-команд бота."""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from vkuswill_bot.bot.handlers import (
    AdminFilter,
    cmd_admin_block,
    cmd_admin_stats,
    cmd_admin_unblock,
    cmd_admin_user,
    handle_admin_unauthorized,
)


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_message(
    text: str,
    user_id: int = 100,
    is_admin: bool = True,
) -> MagicMock:
    """Создать мок aiogram.types.Message для admin-команд."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _make_db_user(role: str = "admin", status: str = "active") -> dict:
    """Создать мок db_user (словарь из UserMiddleware)."""
    return {
        "user_id": 100,
        "role": role,
        "status": status,
    }


def _make_user_store() -> AsyncMock:
    """Создать мок UserStore."""
    store = AsyncMock()
    store.block.return_value = True
    store.unblock.return_value = True
    store.count_users.return_value = 42
    store.count_active_today.return_value = 10
    store.get.return_value = None
    return store


# ---------------------------------------------------------------------------
# AdminFilter
# ---------------------------------------------------------------------------


class TestAdminFilter:
    """Тесты AdminFilter — проверка прав администратора."""

    @pytest.mark.asyncio
    async def test_admin_allowed(self):
        """Администратор проходит фильтр."""
        f = AdminFilter()
        msg = _make_message("/admin_block 999")
        result = await f(msg, db_user=_make_db_user(role="admin"))
        assert result is True

    @pytest.mark.asyncio
    async def test_user_rejected(self):
        """Обычный пользователь не проходит фильтр (без побочных эффектов)."""
        f = AdminFilter()
        msg = _make_message("/admin_block 999")
        result = await f(msg, db_user=_make_db_user(role="user"))
        assert result is False
        # AdminFilter — чистый фильтр, не отправляет сообщений.
        # Отказ отправляется отдельным хендлером handle_admin_unauthorized.
        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_db_user_rejected(self):
        """Без db_user (PostgreSQL недоступен) → нет прав, без сообщения."""
        f = AdminFilter()
        msg = _make_message("/admin_block 999")
        result = await f(msg, db_user=None)
        assert result is False
        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_unauthorized_handler_sends_rejection(self):
        """handle_admin_unauthorized отправляет сообщение об отказе."""
        msg = _make_message("/admin_analytics")
        await handle_admin_unauthorized(msg)
        msg.answer.assert_called_once()
        assert "нет прав" in msg.answer.call_args[0][0]


# ---------------------------------------------------------------------------
# /admin_block
# ---------------------------------------------------------------------------


class TestAdminBlock:
    """Тесты команды /admin_block."""

    @pytest.mark.asyncio
    async def test_block_success(self):
        """Успешная блокировка пользователя."""
        msg = _make_message("/admin_block 999 спам")
        store = _make_user_store()

        await cmd_admin_block(msg, store)

        store.block.assert_called_once_with(999, "спам")
        msg.answer.assert_called_once()
        assert "заблокирован" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_block_without_reason(self):
        """Блокировка без причины."""
        msg = _make_message("/admin_block 999")
        store = _make_user_store()

        await cmd_admin_block(msg, store)

        store.block.assert_called_once_with(999, "")

    @pytest.mark.asyncio
    async def test_block_no_user_id(self):
        """Без user_id → подсказка."""
        msg = _make_message("/admin_block")
        store = _make_user_store()

        await cmd_admin_block(msg, store)

        store.block.assert_not_called()
        assert "Использование" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_block_invalid_user_id(self):
        """Некорректный user_id → ошибка."""
        msg = _make_message("/admin_block abc")
        store = _make_user_store()

        await cmd_admin_block(msg, store)

        store.block.assert_not_called()
        assert "числом" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_block_self_prevented(self):
        """Нельзя заблокировать самого себя."""
        msg = _make_message("/admin_block 100 test", user_id=100)
        store = _make_user_store()

        await cmd_admin_block(msg, store)

        store.block.assert_not_called()
        assert "самого себя" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_block_user_not_found(self):
        """Блокировка несуществующего пользователя."""
        msg = _make_message("/admin_block 999")
        store = _make_user_store()
        store.block.return_value = False

        await cmd_admin_block(msg, store)

        assert "не найден" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_block_no_from_user(self):
        """Сообщение без from_user → игнор."""
        msg = _make_message("/admin_block 999")
        msg.from_user = None
        store = _make_user_store()

        await cmd_admin_block(msg, store)

        store.block.assert_not_called()


# ---------------------------------------------------------------------------
# /admin_unblock
# ---------------------------------------------------------------------------


class TestAdminUnblock:
    """Тесты команды /admin_unblock."""

    @pytest.mark.asyncio
    async def test_unblock_success(self):
        """Успешная разблокировка."""
        msg = _make_message("/admin_unblock 999")
        store = _make_user_store()

        await cmd_admin_unblock(msg, store)

        store.unblock.assert_called_once_with(999)
        assert "разблокирован" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_unblock_no_user_id(self):
        """Без user_id → подсказка."""
        msg = _make_message("/admin_unblock")
        store = _make_user_store()

        await cmd_admin_unblock(msg, store)

        store.unblock.assert_not_called()

    @pytest.mark.asyncio
    async def test_unblock_user_not_found(self):
        """Разблокировка несуществующего пользователя."""
        msg = _make_message("/admin_unblock 999")
        store = _make_user_store()
        store.unblock.return_value = False

        await cmd_admin_unblock(msg, store)

        assert "не найден" in msg.answer.call_args[0][0]


# ---------------------------------------------------------------------------
# /admin_stats
# ---------------------------------------------------------------------------


class TestAdminStats:
    """Тесты команды /admin_stats."""

    @pytest.mark.asyncio
    async def test_stats_success(self):
        """Успешный вывод статистики."""
        msg = _make_message("/admin_stats")
        store = _make_user_store()

        await cmd_admin_stats(msg, store)

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "42" in text  # total users
        assert "10" in text  # DAU


# ---------------------------------------------------------------------------
# /admin_user
# ---------------------------------------------------------------------------


class TestAdminUser:
    """Тесты команды /admin_user."""

    @pytest.mark.asyncio
    async def test_user_info_success(self):
        """Успешный вывод информации о пользователе (без PII)."""
        msg = _make_message("/admin_user 999")
        store = _make_user_store()
        store.get.return_value = {
            "user_id": 999,
            "role": "user",
            "status": "active",
            "message_count": 100,
            "carts_created": 3,
            "cart_limit": 5,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "last_message_at": datetime(2026, 2, 9, tzinfo=UTC),
            "blocked_reason": None,
        }

        await cmd_admin_user(msg, store)

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "999" in text
        assert "100" in text
        # PII не должно быть в выводе
        assert "Username" not in text
        assert "Имя" not in text

    @pytest.mark.asyncio
    async def test_user_info_blocked(self):
        """Информация о заблокированном пользователе."""
        msg = _make_message("/admin_user 999")
        store = _make_user_store()
        store.get.return_value = {
            "user_id": 999,
            "role": "user",
            "status": "blocked",
            "blocked_reason": "спам",
            "message_count": 5,
            "carts_created": 0,
            "cart_limit": 5,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "last_message_at": None,
        }

        await cmd_admin_user(msg, store)

        text = msg.answer.call_args[0][0]
        assert "blocked" in text
        assert "спам" in text

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        """Пользователь не найден."""
        msg = _make_message("/admin_user 999")
        store = _make_user_store()
        store.get.return_value = None

        await cmd_admin_user(msg, store)

        assert "не найден" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_user_no_user_id(self):
        """Без user_id → подсказка."""
        msg = _make_message("/admin_user")
        store = _make_user_store()

        await cmd_admin_user(msg, store)

        assert "Использование" in msg.answer.call_args[0][0]
