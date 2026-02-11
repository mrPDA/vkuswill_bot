"""Тесты обработчиков Telegram (handlers.py).

Тестируем:
- _split_message: разбивка длинных сообщений
- Команды /start, /help, /reset
- handle_text: основной обработчик с моком GigaChatService
- _send_typing_periodically: периодический typing indicator
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from vkuswill_bot.bot.handlers import (
    _sanitize_telegram_html,
    _send_typing_periodically,
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
        mock_service.reset_conversation = AsyncMock()

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

    async def test_typing_indicator_exception_handled(self):
        """Ошибка send_chat_action не крашит бота (lines 118-119)."""
        msg = make_message("Тест", user_id=1)
        mock_service = AsyncMock()

        # send_chat_action выбрасывает исключение
        msg.bot.send_chat_action.side_effect = RuntimeError("Network error")

        # process_message с задержкой, чтобы typing-таск успел сработать
        async def slow_process(*args, **kwargs):
            await asyncio.sleep(0.15)
            return "Ответ"

        mock_service.process_message.side_effect = slow_process

        # Не должно бросить исключение
        await handle_text(msg, gigachat_service=mock_service)

        msg.answer.assert_called_once_with("Ответ")

    async def test_html_safe_link_preserved(self):
        """F-02: Безопасная ссылка <a href="https://..."> сохраняется."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = (
            '<a href="https://vkusvill.ru/?share_basket=123">Корзина</a>'
        )

        await handle_text(msg, gigachat_service=mock_service)

        response = msg.answer.call_args[0][0]
        assert '<a href="https://vkusvill.ru/?share_basket=123">' in response
        assert "</a>" in response

    async def test_html_script_injection_blocked(self):
        """F-02: XSS через <script> экранируется."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = '<script>alert("xss")</script>Текст'

        await handle_text(msg, gigachat_service=mock_service)

        response = msg.answer.call_args[0][0]
        assert "<script>" not in response
        assert "&lt;script&gt;" in response

    async def test_nbsp_entity_preserved(self):
        """F-02: HTML-сущность &nbsp; в названиях товаров сохраняется."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Томатная паста Помидорка 70&nbsp;г: 90 руб"

        await handle_text(msg, gigachat_service=mock_service)

        response = msg.answer.call_args[0][0]
        # &nbsp; не должен быть экранирован в &amp;nbsp;
        assert "&nbsp;" in response
        assert "&amp;nbsp;" not in response

    async def test_plain_text_unchanged(self):
        """F-02: Обычный текст без спецсимволов не изменяется."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Молоко 3,2% за 79 руб"

        await handle_text(msg, gigachat_service=mock_service)

        response = msg.answer.call_args[0][0]
        assert response == "Молоко 3,2% за 79 руб"


# ============================================================================
# _sanitize_telegram_html
# ============================================================================


class TestSanitizeTelegramHtml:
    """F-02: Тесты whitelist-санитайзера HTML для Telegram."""

    # -- Разрешённые теги проходят --

    def test_bold_preserved(self):
        assert _sanitize_telegram_html("<b>жирный</b>") == "<b>жирный</b>"

    def test_italic_preserved(self):
        assert _sanitize_telegram_html("<i>курсив</i>") == "<i>курсив</i>"

    def test_code_preserved(self):
        assert _sanitize_telegram_html("<code>код</code>") == "<code>код</code>"

    def test_pre_preserved(self):
        assert _sanitize_telegram_html("<pre>блок</pre>") == "<pre>блок</pre>"

    def test_safe_link_preserved(self):
        html = '<a href="https://vkusvill.ru/?basket=1">Ссылка</a>'
        assert _sanitize_telegram_html(html) == html

    def test_http_link_preserved(self):
        html = '<a href="http://example.com">Ссылка</a>'
        assert _sanitize_telegram_html(html) == html

    # -- Опасные теги блокируются --

    def test_script_blocked(self):
        result = _sanitize_telegram_html("<script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_img_blocked(self):
        result = _sanitize_telegram_html("<img src=x onerror=alert(1)>")
        assert "<img" not in result
        assert "&lt;img" in result

    def test_iframe_blocked(self):
        result = _sanitize_telegram_html('<iframe src="evil.com"></iframe>')
        assert "<iframe" not in result
        assert "&lt;iframe" in result

    def test_div_blocked(self):
        result = _sanitize_telegram_html("<div>текст</div>")
        assert "<div>" not in result
        assert "&lt;div&gt;" in result

    # -- Опасные атрибуты на ссылках блокируются --

    def test_javascript_href_blocked(self):
        html = '<a href="javascript:alert(1)">click</a>'
        result = _sanitize_telegram_html(html)
        # Тег экранирован — Telegram не отрендерит как ссылку
        assert "&lt;a" in result
        assert "<a href=" not in result

    def test_onclick_on_link_blocked(self):
        html = '<a onclick="alert(1)" href="https://ok.com">click</a>'
        result = _sanitize_telegram_html(html)
        assert "onclick" not in result or "&lt;a" in result

    def test_data_href_blocked(self):
        html = '<a href="data:text/html,<script>alert(1)</script>">x</a>'
        result = _sanitize_telegram_html(html)
        assert "data:" not in result or "&lt;a" in result

    # -- Атрибуты на обычных тегах удаляются --

    def test_bold_onclick_stripped(self):
        result = _sanitize_telegram_html('<b onclick="evil()">текст</b>')
        assert result == "<b>текст</b>"

    # -- HTML-сущности сохраняются --

    def test_nbsp_preserved(self):
        assert _sanitize_telegram_html("70&nbsp;г") == "70&nbsp;г"

    def test_amp_preserved(self):
        assert _sanitize_telegram_html("A &amp; B") == "A &amp; B"

    # -- Обычный текст не затрагивается --

    def test_plain_text(self):
        text = "Молоко 3,2% за 79 руб"
        assert _sanitize_telegram_html(text) == text

    def test_plain_text_with_numbers(self):
        text = "Итого: 984.25 руб"
        assert _sanitize_telegram_html(text) == text

    # -- Комплексный кейс: реальный ответ бота --

    def test_real_bot_response(self):
        """Реальный ответ бота с &nbsp; и ссылкой — всё сохраняется."""
        response = (
            "Томатная паста 70&nbsp;г: 90 руб\n"
            "Итого: 984 руб\n"
            '<a href="https://vkusvill.ru/?share_basket=123">Открыть корзину</a>'
        )
        result = _sanitize_telegram_html(response)
        assert "&nbsp;" in result
        assert "&amp;nbsp;" not in result
        assert '<a href="https://vkusvill.ru/?share_basket=123">' in result
        assert "</a>" in result


# ============================================================================
# _send_typing_periodically
# ============================================================================


class TestSendTypingPeriodically:
    """Тесты _send_typing_periodically: периодический typing indicator."""

    async def test_timeout_loop_continues(self):
        """TimeoutError в wait_for не прерывает цикл (lines 122-123).

        Мокаем asyncio.wait_for чтобы сначала бросить TimeoutError,
        а затем вернуть нормальный результат (событие установлено).
        """
        msg = make_message("Тест", user_id=1)
        stop_event = asyncio.Event()

        call_count = 0

        async def fake_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            # Первый вызов — TimeoutError (цикл продолжается)
            if call_count == 1:
                raise TimeoutError()
            # Второй вызов — устанавливаем событие и "завершаемся"
            stop_event.set()
            return

        with patch("vkuswill_bot.bot.handlers.asyncio.wait_for", side_effect=fake_wait_for):
            await _send_typing_periodically(msg, stop_event)

        # send_chat_action вызвано минимум 2 раза
        # (до первого wait_for и до второго)
        assert msg.bot.send_chat_action.call_count >= 2
        assert call_count == 2

    async def test_stops_on_event(self):
        """Цикл останавливается когда stop_event установлен заранее."""
        msg = make_message("Тест", user_id=1)
        stop_event = asyncio.Event()

        # Устанавливаем событие через мок wait_for
        async def immediate_return(coro, timeout):
            stop_event.set()
            return

        # Разрешаем одну итерацию цикла, затем останавливаемся
        with patch(
            "vkuswill_bot.bot.handlers.asyncio.wait_for",
            side_effect=immediate_return,
        ):
            await _send_typing_periodically(msg, stop_event)

        # send_chat_action вызван 1 раз (до wait_for)
        msg.bot.send_chat_action.assert_called_once()
