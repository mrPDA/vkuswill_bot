"""Общие хелперы для тестов.

Публичные функции для создания тестовых объектов aiogram.
"""

from unittest.mock import AsyncMock, MagicMock


def make_message(text: str = "", user_id: int = 1) -> MagicMock:
    """Создать мок aiogram.types.Message."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.chat = MagicMock()
    msg.chat.id = 100
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_chat_action = AsyncMock()
    return msg
