"""Тесты обработчиков Telegram (handlers.py).

Тестируем:
- _split_message: разбивка длинных сообщений
- Команды /start, /help, /reset
- handle_text: основной обработчик с моком GigaChatService
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vkuswill_bot.bot.handlers import (
    _split_message,
    cmd_help,
    cmd_reset,
    cmd_start,
    handle_text,
)

from helpers import make_message


# ============================================================================
# _split_message
# ============================================================================


class TestSplitMessage:
    """Тесты разбивки длинных сообщений для Telegram."""

    def test_short_message(self):
        """Короткое сообщение не разбивается."""
        result = _split_message("Hello", 4096)
        assert result == ["Hello"]

    def test_exact_limit(self):
        """Сообщение ровно по лимиту — 1 часть."""
        msg = "a" * 4096
        result = _split_message(msg, 4096)
        assert result == [msg]

    def test_splits_on_double_newline(self):
        """Предпочитает разрыв на двойном переводе строки."""
        part1 = "A" * 50
        part2 = "B" * 50
        msg = part1 + "\n\n" + part2
        result = _split_message(msg, 60)
        assert len(result) == 2
        assert result[0] == part1

    def test_splits_on_single_newline(self):
        """Если нет \n\n, разрывает на \n."""
        part1 = "A" * 50
        part2 = "B" * 50
        msg = part1 + "\n" + part2
        result = _split_message(msg, 60)
        assert len(result) == 2
        assert result[0] == part1

    def test_splits_on_space(self):
        """Если нет \n, разрывает на пробеле."""
        msg = "word " * 20  # 100 символов
        result = _split_message(msg, 30)
        assert all(len(chunk) <= 30 for chunk in result)

    def test_hard_split(self):
        """Если нет пробелов — жёсткий разрыв по лимиту."""
        msg = "a" * 100
        result = _split_message(msg, 30)
        assert len(result) == 4  # 30 + 30 + 30 + 10
        assert result[0] == "a" * 30

    def test_empty_string(self):
        """Пустая строка."""
        result = _split_message("", 4096)
        assert result == [""]

    def test_unicode(self):
        """Юникод (русский текст) корректно разбивается."""
        msg = "Привет " * 100
        result = _split_message(msg, 50)
        assert all(len(chunk) <= 50 for chunk in result)


# ============================================================================
# Команды
# ============================================================================


class TestCommands:
    """Тесты обработчиков команд."""

    async def test_cmd_start(self):
        """Команда /start отвечает приветствием."""
        msg = make_message()
        await cmd_start(msg)

        msg.answer.assert_called_once()
        response_text = msg.answer.call_args[0][0]
        assert "Привет" in response_text
        assert "ВкусВилл" in response_text

    async def test_cmd_help(self):
        """Команда /help отвечает инструкцией."""
        msg = make_message()
        await cmd_help(msg)

        msg.answer.assert_called_once()
        response_text = msg.answer.call_args[0][0]
        assert "Выгодно" in response_text
        assert "Любимое" in response_text
        assert "Лайт" in response_text

    async def test_cmd_reset(self):
        """Команда /reset вызывает reset_conversation."""
        msg = make_message(user_id=42)
        mock_service = MagicMock()
        mock_service.reset_conversation = MagicMock()

        await cmd_reset(msg, gigachat_service=mock_service)

        mock_service.reset_conversation.assert_called_once_with(42)
        msg.answer.assert_called_once()
        assert "сброшен" in msg.answer.call_args[0][0].lower()

    async def test_cmd_reset_no_user(self):
        """Команда /reset без from_user — не падает."""
        msg = make_message()
        msg.from_user = None
        mock_service = MagicMock()

        await cmd_reset(msg, gigachat_service=mock_service)

        mock_service.reset_conversation.assert_not_called()
        msg.answer.assert_called_once()


# ============================================================================
# handle_text
# ============================================================================


class TestHandleText:
    """Тесты основного обработчика текстовых сообщений."""

    async def test_normal_response(self):
        """Обычный запрос → ответ GigaChat."""
        msg = make_message("Хочу молоко", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Вот молоко за 79 руб!"

        await handle_text(msg, gigachat_service=mock_service)

        mock_service.process_message.assert_called_once_with(1, "Хочу молоко")
        msg.answer.assert_called_once_with("Вот молоко за 79 руб!")

    async def test_long_response_split(self):
        """Длинный ответ разбивается на части."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "A" * 5000  # > 4096

        await handle_text(msg, gigachat_service=mock_service)

        assert msg.answer.call_count == 2

    async def test_error_handling(self):
        """Ошибка в process_message → сообщение об ошибке."""
        msg = make_message("Тест", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.side_effect = RuntimeError("Boom!")

        await handle_text(msg, gigachat_service=mock_service)

        msg.answer.assert_called_once()
        response_text = msg.answer.call_args[0][0]
        assert "ошибка" in response_text.lower()

    async def test_no_user(self):
        """Без from_user — ничего не делаем."""
        msg = make_message("text")
        msg.from_user = None
        mock_service = AsyncMock()

        await handle_text(msg, gigachat_service=mock_service)

        mock_service.process_message.assert_not_called()

    async def test_no_text(self):
        """Без текста — ничего не делаем."""
        msg = make_message("")
        msg.text = None
        mock_service = AsyncMock()

        await handle_text(msg, gigachat_service=mock_service)

        mock_service.process_message.assert_not_called()

    async def test_typing_indicator_sent(self):
        """Индикатор набора отправляется во время обработки."""
        msg = make_message("Тест", user_id=1)
        mock_service = AsyncMock()

        # process_message с задержкой, чтобы typing-таск успел сработать
        async def slow_process(*args, **kwargs):
            await asyncio.sleep(0.1)
            return "Ответ"

        mock_service.process_message.side_effect = slow_process

        await handle_text(msg, gigachat_service=mock_service)

        # Typing indicator должен был вызваться хотя бы раз
        msg.bot.send_chat_action.assert_called()
