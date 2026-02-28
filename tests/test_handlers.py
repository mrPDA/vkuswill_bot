"""Тесты обработчиков Telegram (handlers.py).

Тестируем:
- _split_message: разбивка длинных сообщений
- Команды /start, /help, /reset
- handle_text: основной обработчик с моком GigaChatService
- _send_typing_periodically: периодический typing indicator
- Deep-link парсинг в /start
- Survey flow (cmd_survey, callbacks)
- Admin-команды: analytics, funnel, grant_carts, survey_stats
"""

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch


from vkuswill_bot.bot.handlers import (
    _extract_cart_link,
    _sanitize_telegram_html,
    _send_typing_periodically,
    _split_message,
    _survey_pending,
    cart_feedback_negative,
    cart_feedback_positive,
    cart_feedback_reason,
    cmd_help,
    cmd_link_voice,
    cmd_unlink_voice,
    cmd_privacy,
    cmd_reset,
    cmd_start,
    cmd_survey,
    cmd_admin_analytics,
    cmd_admin_cart_feedback,
    cmd_admin_funnel,
    cmd_admin_grant_carts,
    cmd_admin_reset_carts,
    cmd_admin_survey_stats,
    consent_accept_callback,
    handle_text,
    survey_pmf_callback,
    survey_feature_callback,
    survey_done_callback,
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
        assert "Условия корзин" in response_text
        assert "/survey" in response_text
        assert "/invite" in response_text

    async def test_cmd_start_shows_freemium_notice(self):
        """/start содержит объяснение freemium-модели."""
        msg = make_message("/start", user_id=42)

        await cmd_start(
            msg,
            db_user={"message_count": 1, "consent_given_at": "2026-01-01"},
        )

        msg.answer.assert_called_once()
        response_text = msg.answer.call_args[0][0]
        assert "Условия корзин" in response_text
        assert "/survey" in response_text
        assert "/invite" in response_text

    async def test_cmd_reset(self):
        """Команда /reset вызывает reset_conversation."""
        msg = make_message(user_id=42)
        mock_service = MagicMock()
        mock_service.reset_conversation = AsyncMock()

        await cmd_reset(msg, chat_engine=mock_service)

        mock_service.reset_conversation.assert_called_once_with(42)
        msg.answer.assert_called_once()
        assert "сброшен" in msg.answer.call_args[0][0].lower()

    async def test_cmd_reset_no_user(self):
        """Команда /reset без from_user — не падает."""
        msg = make_message()
        msg.from_user = None
        mock_service = MagicMock()

        await cmd_reset(msg, chat_engine=mock_service)

        mock_service.reset_conversation.assert_not_called()
        msg.answer.assert_called_once()

    async def test_cmd_link_voice_success(self):
        """/link_voice генерирует код и отправляет инструкцию."""
        msg = make_message("/link_voice", user_id=42)
        mock_store = AsyncMock()
        mock_store.create_voice_link_code.return_value = "123456"

        await cmd_link_voice(msg, user_store=mock_store)

        mock_store.create_voice_link_code.assert_awaited_once()
        mock_store.log_event.assert_awaited_once()
        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "код 123456" in text
        assert "Привязка Алисы" in text

    async def test_cmd_link_voice_no_store(self):
        """/link_voice без user_store сообщает о недоступности."""
        msg = make_message("/link_voice", user_id=42)

        await cmd_link_voice(msg, user_store=None)

        msg.answer.assert_called_once()
        assert "недоступна" in msg.answer.call_args[0][0]

    async def test_cmd_unlink_voice_success(self):
        """/unlink_voice отвязывает аккаунт и сообщает об успехе."""
        msg = make_message("/unlink_voice", user_id=42)
        mock_store = AsyncMock()
        mock_store.revoke_voice_links_for_user.return_value = 1

        await cmd_unlink_voice(msg, user_store=mock_store)

        mock_store.revoke_voice_links_for_user.assert_awaited_once_with(
            user_id=42,
            provider="alice",
        )
        mock_store.log_event.assert_awaited_once()
        msg.answer.assert_called_once()
        assert "отвязана" in msg.answer.call_args[0][0].lower()

    async def test_cmd_unlink_voice_not_linked(self):
        """/unlink_voice сообщает, если активной привязки нет."""
        msg = make_message("/unlink_voice", user_id=42)
        mock_store = AsyncMock()
        mock_store.revoke_voice_links_for_user.return_value = 0

        await cmd_unlink_voice(msg, user_store=mock_store)

        msg.answer.assert_called_once()
        assert "не найдена" in msg.answer.call_args[0][0].lower()

    async def test_cmd_unlink_voice_no_store(self):
        """/unlink_voice без user_store сообщает о недоступности."""
        msg = make_message("/unlink_voice", user_id=42)

        await cmd_unlink_voice(msg, user_store=None)

        msg.answer.assert_called_once()
        assert "недоступна" in msg.answer.call_args[0][0].lower()


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

        await handle_text(msg, chat_engine=mock_service)

        mock_service.process_message.assert_called_once_with(
            1,
            "Хочу молоко",
            on_progress=ANY,
        )
        msg.answer.assert_called_once_with("Вот молоко за 79 руб!", reply_markup=None)

    async def test_long_response_split(self):
        """Длинный ответ разбивается на части."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "A" * 5000  # > 4096

        await handle_text(msg, chat_engine=mock_service)

        assert msg.answer.call_count == 2

    async def test_error_handling(self):
        """Ошибка в process_message → сообщение об ошибке."""
        msg = make_message("Тест", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.side_effect = RuntimeError("Boom!")

        await handle_text(msg, chat_engine=mock_service)

        msg.answer.assert_called_once()
        response_text = msg.answer.call_args[0][0]
        assert "ошибка" in response_text.lower()

    async def test_no_user(self):
        """Без from_user — ничего не делаем."""
        msg = make_message("text")
        msg.from_user = None
        mock_service = AsyncMock()

        await handle_text(msg, chat_engine=mock_service)

        mock_service.process_message.assert_not_called()

    async def test_no_text(self):
        """Без текста — ничего не делаем."""
        msg = make_message("")
        msg.text = None
        mock_service = AsyncMock()

        await handle_text(msg, chat_engine=mock_service)

        mock_service.process_message.assert_not_called()

    async def test_typing_indicator_sent(self):
        """Индикатор набора отправляется во время обработки."""
        msg = make_message("Тест", user_id=1)
        mock_service = AsyncMock()
        typing_sent = asyncio.Event()

        async def mark_typing(*args, **kwargs):
            typing_sent.set()

        msg.bot.send_chat_action.side_effect = mark_typing

        # Ждём факт отправки typing, без реальных sleep
        async def slow_process(*args, **kwargs):
            await typing_sent.wait()
            return "Ответ"

        mock_service.process_message.side_effect = slow_process

        await handle_text(msg, chat_engine=mock_service)

        # Typing indicator должен был вызваться хотя бы раз
        msg.bot.send_chat_action.assert_called()

    async def test_typing_indicator_exception_handled(self):
        """Ошибка send_chat_action не крашит бота (lines 118-119)."""
        msg = make_message("Тест", user_id=1)
        mock_service = AsyncMock()
        typing_attempted = asyncio.Event()

        async def failing_typing(*args, **kwargs):
            typing_attempted.set()
            raise RuntimeError("Network error")

        msg.bot.send_chat_action.side_effect = failing_typing

        async def slow_process(*args, **kwargs):
            await typing_attempted.wait()
            return "Ответ"

        mock_service.process_message.side_effect = slow_process

        # Не должно бросить исключение
        await handle_text(msg, chat_engine=mock_service)

        msg.answer.assert_called_once_with("Ответ", reply_markup=None)

    async def test_html_safe_link_moved_to_button(self):
        """F-02: Ссылка на корзину заменяется inline-кнопкой."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = (
            'Итого: 500 руб\n<a href="https://vkusvill.ru/?share_basket=123">Корзина</a>'
        )

        await handle_text(msg, chat_engine=mock_service)

        # Текстовая ссылка убрана из сообщения
        response = msg.answer.call_args[0][0]
        assert "share_basket" not in response
        # Но inline-кнопка с URL присутствует
        call_kwargs = msg.answer.call_args
        markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
        assert markup is not None
        assert markup.inline_keyboard[0][0].url == "https://vkusvill.ru/?share_basket=123"

    async def test_html_non_cart_link_preserved(self):
        """F-02: Безопасная НЕ-корзинная ссылка сохраняется в тексте."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = '<a href="https://example.com">Подробнее</a>'

        await handle_text(msg, chat_engine=mock_service)

        response = msg.answer.call_args[0][0]
        assert '<a href="https://example.com">' in response

    async def test_html_script_injection_blocked(self):
        """F-02: XSS через <script> экранируется."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = '<script>alert("xss")</script>Текст'

        await handle_text(msg, chat_engine=mock_service)

        response = msg.answer.call_args[0][0]
        assert "<script>" not in response
        assert "&lt;script&gt;" in response

    async def test_nbsp_entity_preserved(self):
        """F-02: HTML-сущность &nbsp; в названиях товаров сохраняется."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Томатная паста Помидорка 70&nbsp;г: 90 руб"

        await handle_text(msg, chat_engine=mock_service)

        response = msg.answer.call_args[0][0]
        # &nbsp; не должен быть экранирован в &amp;nbsp;
        assert "&nbsp;" in response
        assert "&amp;nbsp;" not in response

    async def test_plain_text_unchanged(self):
        """F-02: Обычный текст без спецсимволов не изменяется."""
        msg = make_message("Запрос", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Молоко 3,2% за 79 руб"

        await handle_text(msg, chat_engine=mock_service)

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
# _extract_cart_link
# ============================================================================


class TestExtractCartLink:
    """Тесты извлечения URL корзины, удаления текстовой ссылки и создания кнопки."""

    def test_extracts_url_and_removes_link(self):
        """Ссылка «Открыть корзину» → кнопка + ссылка удалена из текста."""
        html = (
            "Вот ваша корзина:\n\n"
            "1. Молоко — 79 руб\n\n"
            '<a href="https://vkusvill.ru/?share_basket=abc123">Открыть корзину</a>'
        )
        cleaned, kb = _extract_cart_link(html)
        assert kb is not None
        btn = kb.inline_keyboard[0][0]
        assert btn.url == "https://vkusvill.ru/?share_basket=abc123"
        assert "Открыть корзину" not in cleaned
        assert "share_basket" not in cleaned
        assert "Молоко" in cleaned

    def test_no_cart_link_returns_none(self):
        """Без ссылки на корзину — текст без изменений, None."""
        html = "Вот молоко за 79 руб!"
        cleaned, kb = _extract_cart_link(html)
        assert kb is None
        assert cleaned == html

    def test_non_cart_link_unchanged(self):
        """Ссылка без слова «корзин» — не трогаем."""
        html = '<a href="https://example.com">Подробнее</a>'
        cleaned, kb = _extract_cart_link(html)
        assert kb is None
        assert cleaned == html

    def test_cart_link_case_insensitive(self):
        """Регистр текста ссылки не важен."""
        html = '<a href="https://vkusvill.ru/?basket=1">КОРЗИНА</a>'
        _cleaned, kb = _extract_cart_link(html)
        assert kb is not None
        assert kb.inline_keyboard[0][0].url == "https://vkusvill.ru/?basket=1"

    def test_emoji_prefix_removed(self):
        """Эмодзи 🛒 перед ссылкой тоже убирается."""
        html = (
            "Итого: 500 руб\n\n"
            '\U0001f6d2 <a href="https://vkusvill.ru/?basket=1">Открыть корзину</a>\n\n'
            "Дисклеймер"
        )
        cleaned, kb = _extract_cart_link(html)
        assert kb is not None
        assert "\U0001f6d2" not in cleaned
        assert "Открыть корзину" not in cleaned
        assert "Дисклеймер" in cleaned

    def test_no_triple_newlines(self):
        """После удаления ссылки не остаётся тройных переносов."""
        html = 'Текст\n\n<a href="https://vkusvill.ru/?b=1">Открыть корзину</a>\n\nДисклеймер'
        cleaned, _ = _extract_cart_link(html)
        assert "\n\n\n" not in cleaned


class TestHandleTextCartButton:
    """Тесты inline-кнопки корзины в handle_text."""

    async def test_cart_response_has_inline_button(self):
        """Ответ с корзиной → inline-кнопка, текстовая ссылка убрана."""
        msg = make_message("Собери корзину", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = (
            "Вот корзина:\n"
            "1. Молоко — 79 руб\n"
            "<b>Итого: 79 руб</b>\n"
            '<a href="https://vkusvill.ru/?share_basket=xyz">Открыть корзину</a>'
        )

        await handle_text(msg, chat_engine=mock_service)

        call_kwargs = msg.answer.call_args
        markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
        assert markup is not None
        btn = markup.inline_keyboard[0][0]
        assert btn.url == "https://vkusvill.ru/?share_basket=xyz"
        # Текстовая ссылка убрана из сообщения
        sent_text = call_kwargs[0][0]
        assert "Открыть корзину" not in sent_text

    async def test_no_cart_no_button(self):
        """Обычный ответ без корзины → reply_markup=None."""
        msg = make_message("Привет", user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Привет! Чем помочь?"

        await handle_text(msg, chat_engine=mock_service)

        call_kwargs = msg.answer.call_args
        markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
        assert markup is None


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


# ============================================================================
# Deep-link парсинг в /start
# ============================================================================


class TestCmdStartDeepLink:
    """Тесты парсинга deep-link параметров в /start."""

    async def test_start_organic(self):
        """/start без параметров — organic source."""
        msg = make_message("/start", user_id=42)
        mock_store = AsyncMock()

        await cmd_start(msg, user_store=mock_store, db_user={"message_count": 1})

        msg.answer.assert_called_once()
        assert "Привет" in msg.answer.call_args[0][0]
        # log_event вызван с source=organic
        mock_store.log_event.assert_called_once()
        metadata = mock_store.log_event.call_args[0][2]
        assert metadata["source"] == "organic"
        assert metadata["is_new_user"] is True

    async def test_start_referral(self):
        """/start ref_12345 — referral source + реферальная обработка."""
        msg = make_message("/start ref_12345", user_id=42)
        mock_store = AsyncMock()
        mock_store.process_referral.return_value = {
            "success": True,
            "reason": "linked",
            "referrer_id": 12345,
        }

        await cmd_start(msg, user_store=mock_store, db_user={"message_count": 1})

        # Находим вызов log_event с "bot_start"
        bot_start_calls = [c for c in mock_store.log_event.call_args_list if c[0][1] == "bot_start"]
        assert len(bot_start_calls) == 1
        metadata = bot_start_calls[0][0][2]
        assert metadata["source"] == "referral"
        assert metadata["referrer_id"] == 12345

        # Реферал обработан
        mock_store.process_referral.assert_called_once_with(42, 12345)

        # Факт привязки залогирован
        referral_calls = [
            c for c in mock_store.log_event.call_args_list if c[0][1] == "referral_linked"
        ]
        assert len(referral_calls) == 1

    async def test_start_habr_source(self):
        """/start habr — source=habr."""
        msg = make_message("/start habr", user_id=42)
        mock_store = AsyncMock()

        await cmd_start(msg, user_store=mock_store, db_user={"message_count": 1})

        metadata = mock_store.log_event.call_args[0][2]
        assert metadata["source"] == "habr"

    async def test_start_vc_source(self):
        """/start vc — source=vc."""
        msg = make_message("/start vc", user_id=42)
        mock_store = AsyncMock()

        await cmd_start(msg, user_store=mock_store, db_user={"message_count": 1})

        metadata = mock_store.log_event.call_args[0][2]
        assert metadata["source"] == "vc"

    async def test_start_telegram_source(self):
        """/start telegram — source=telegram."""
        msg = make_message("/start telegram", user_id=42)
        mock_store = AsyncMock()

        await cmd_start(msg, user_store=mock_store, db_user={"message_count": 1})

        metadata = mock_store.log_event.call_args[0][2]
        assert metadata["source"] == "telegram"

    async def test_start_referral_code(self):
        """/start ref_abc — реферальный код (строка), ищет в БД."""
        msg = make_message("/start ref_abc", user_id=42)
        mock_store = AsyncMock()
        mock_store.find_user_by_referral_code.return_value = 999
        mock_store.process_referral.return_value = {
            "success": True,
            "reason": "linked",
            "referrer_id": 999,
        }

        await cmd_start(msg, user_store=mock_store, db_user={"message_count": 1})

        mock_store.find_user_by_referral_code.assert_called_once_with("abc")
        bot_start_calls = [c for c in mock_store.log_event.call_args_list if c[0][1] == "bot_start"]
        metadata = bot_start_calls[0][0][2]
        assert metadata["source"] == "referral"
        assert metadata["referrer_id"] == 999

    async def test_start_invalid_ref(self):
        """/start ref_abc — невалидный реферальный код, не найден в БД → organic."""
        msg = make_message("/start ref_abc", user_id=42)
        mock_store = AsyncMock()
        mock_store.find_user_by_referral_code.return_value = None

        await cmd_start(msg, user_store=mock_store, db_user={"message_count": 1})

        bot_start_calls = [c for c in mock_store.log_event.call_args_list if c[0][1] == "bot_start"]
        metadata = bot_start_calls[0][0][2]
        assert metadata["source"] == "organic"
        assert "referrer_id" not in metadata

    async def test_start_existing_user(self):
        """/start для существующего пользователя — is_new_user=False."""
        msg = make_message("/start", user_id=42)
        mock_store = AsyncMock()

        await cmd_start(
            msg,
            user_store=mock_store,
            db_user={"message_count": 10},
        )

        metadata = mock_store.log_event.call_args[0][2]
        assert metadata["is_new_user"] is False

    async def test_start_without_user_store(self):
        """/start без user_store — не падает, не логирует."""
        msg = make_message("/start", user_id=42)

        await cmd_start(msg, user_store=None)

        msg.answer.assert_called_once()
        assert "Привет" in msg.answer.call_args[0][0]


# ============================================================================
# Informed Consent (ADR-002)
# ============================================================================


class TestInformedConsent:
    """Тесты информированного согласия на обработку данных."""

    async def test_start_new_user_shows_consent(self):
        """Новый пользователь без consent видит уведомление + кнопку."""
        msg = make_message("/start", user_id=42)
        mock_store = AsyncMock()

        await cmd_start(
            msg,
            user_store=mock_store,
            db_user={"message_count": 1, "consent_given_at": None},
        )

        msg.answer.assert_called_once()
        response_text = msg.answer.call_args[0][0]
        assert "ИИ" in response_text
        assert "/privacy" in response_text
        # Inline-кнопка присутствует
        call_kwargs = msg.answer.call_args
        markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
        assert markup is not None
        btn = markup.inline_keyboard[0][0]
        assert btn.callback_data == "consent_accept"

    async def test_start_returning_user_no_consent(self):
        """Returning user с consent — обычное приветствие без кнопки."""
        msg = make_message("/start", user_id=42)
        mock_store = AsyncMock()

        await cmd_start(
            msg,
            user_store=mock_store,
            db_user={"message_count": 10, "consent_given_at": "2026-01-01"},
        )

        msg.answer.assert_called_once()
        response_text = msg.answer.call_args[0][0]
        assert "Привет" in response_text
        # Нет inline-кнопки consent
        call_kwargs = msg.answer.call_args
        markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
        assert markup is None

    async def test_start_existing_user_many_messages_no_consent_notice(self):
        """Existing user с message_count > 1 не видит consent (даже без поля)."""
        msg = make_message("/start", user_id=42)
        mock_store = AsyncMock()

        await cmd_start(
            msg,
            user_store=mock_store,
            db_user={"message_count": 5, "consent_given_at": None},
        )

        msg.answer.assert_called_once()
        call_kwargs = msg.answer.call_args
        markup = call_kwargs.kwargs.get("reply_markup") or call_kwargs[1].get("reply_markup")
        assert markup is None

    async def test_consent_accept_callback(self):
        """Нажатие кнопки «Понятно, начать!» фиксирует explicit consent."""
        callback = _make_callback_query("consent_accept", user_id=42)
        mock_store = AsyncMock()
        mock_store.mark_consent.return_value = True

        await consent_accept_callback(callback, user_store=mock_store)

        mock_store.mark_consent.assert_called_once_with(42, "explicit")
        # Событие залогировано
        log_calls = [c for c in mock_store.log_event.call_args_list if c[0][1] == "consent_given"]
        assert len(log_calls) == 1
        assert log_calls[0][0][2]["consent_type"] == "explicit"
        # Сообщение обновлено (без кнопки)
        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Привет" in text
        callback.answer.assert_called_once()

    async def test_consent_accept_without_store(self):
        """Нажатие кнопки consent без user_store — не падает."""
        callback = _make_callback_query("consent_accept", user_id=42)

        await consent_accept_callback(callback, user_store=None)

        callback.message.edit_text.assert_called_once()
        callback.answer.assert_called_once()

    async def test_implicit_consent_on_text(self):
        """Текстовое сообщение без consent → implicit consent фиксируется."""
        msg = make_message("Хочу молоко", user_id=42)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Вот молоко!"
        mock_store = AsyncMock()
        mock_store.mark_consent.return_value = True  # первый раз

        await handle_text(msg, chat_engine=mock_service, user_store=mock_store)

        mock_store.mark_consent.assert_called_once_with(42, "implicit")
        # Событие залогировано
        consent_calls = [
            c for c in mock_store.log_event.call_args_list if c[0][1] == "consent_given"
        ]
        assert len(consent_calls) == 1
        assert consent_calls[0][0][2]["consent_type"] == "implicit"
        # GigaChat обработал сообщение (не заблокировано)
        mock_service.process_message.assert_called_once()

    async def test_implicit_consent_not_logged_twice(self):
        """Если consent уже есть, событие не логируется повторно."""
        msg = make_message("Хочу молоко", user_id=42)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Вот молоко!"
        mock_store = AsyncMock()
        mock_store.mark_consent.return_value = False  # уже был consent

        await handle_text(msg, chat_engine=mock_service, user_store=mock_store)

        # mark_consent вызван, но log_event для consent НЕ вызван
        mock_store.mark_consent.assert_called_once()
        consent_calls = [
            c for c in mock_store.log_event.call_args_list if c[0][1] == "consent_given"
        ]
        assert len(consent_calls) == 0

    async def test_cmd_privacy(self):
        """Команда /privacy возвращает текст политики."""
        msg = make_message("/privacy")

        await cmd_privacy(msg)

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Политика конфиденциальности" in text
        assert "ИИ" in text
        assert "d.pukinov@yandex.ru" in text
        assert "/reset" in text


# ============================================================================
# Survey Flow
# ============================================================================


def _make_callback_query(data: str, user_id: int = 42) -> MagicMock:
    """Создать мок CallbackQuery."""
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    return callback


class TestSurveyFlow:
    """Тесты survey flow: PMF + фичи + открытый отзыв."""

    async def test_cmd_survey_starts(self):
        """/survey запускает опрос с PMF-вопросом."""
        msg = make_message("/survey", user_id=42)
        mock_store = AsyncMock()

        await cmd_survey(
            msg,
            user_store=mock_store,
            db_user={"survey_completed": False},
        )

        msg.answer.assert_called_once()
        answer_text = msg.answer.call_args[0][0]
        assert "расстроились" in answer_text

    async def test_cmd_survey_already_completed(self):
        """/survey если уже пройден — сообщение об этом."""
        msg = make_message("/survey", user_id=42)
        mock_store = AsyncMock()

        await cmd_survey(
            msg,
            user_store=mock_store,
            db_user={"survey_completed": True},
        )

        msg.answer.assert_called_once()
        assert "уже прошли" in msg.answer.call_args[0][0]

    async def test_cmd_survey_no_store(self):
        """/survey без user_store — недоступен."""
        msg = make_message("/survey", user_id=42)

        await cmd_survey(msg, user_store=None, db_user={"survey_completed": False})

        msg.answer.assert_called_once()
        assert "недоступен" in msg.answer.call_args[0][0]

    async def test_cmd_survey_clears_pending(self):
        """/survey очищает незавершённое состояние."""
        _survey_pending[42] = {"pmf": "very", "feature": "search"}
        msg = make_message("/survey", user_id=42)
        mock_store = AsyncMock()

        await cmd_survey(
            msg,
            user_store=mock_store,
            db_user={"survey_completed": False},
        )

        assert 42 not in _survey_pending

    async def test_pmf_callback(self):
        """survey_pmf_callback переходит к выбору фичи."""
        callback = _make_callback_query("survey_pmf_very")

        await survey_pmf_callback(callback)

        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Очень расстроюсь" in text
        assert "функция" in text.lower() or "полезная" in text.lower()
        callback.answer.assert_called_once()

    async def test_feature_callback_sets_pending(self):
        """survey_feature_callback сохраняет состояние и показывает шаг 3."""
        callback = _make_callback_query("survey_feat_search_very")

        await survey_feature_callback(callback)

        # Промежуточное состояние сохранено
        assert 42 in _survey_pending
        assert _survey_pending[42] == {"pmf": "very", "feature": "search"}
        # Текст шага 3
        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Поиск товаров" in text
        assert "улучшить" in text
        callback.answer.assert_called_once()
        # Cleanup
        _survey_pending.pop(42, None)

    async def test_done_callback_completes_survey(self):
        """survey_done_callback (кнопка «Всё отлично») завершает опрос."""
        callback = _make_callback_query("survey_done_very_recipe")
        mock_store = AsyncMock()
        mock_store.mark_survey_completed_if_not.return_value = True
        mock_store.grant_bonus_carts.return_value = 10
        # Имитируем pending-состояние
        _survey_pending[42] = {"pmf": "very", "feature": "recipe"}

        await survey_done_callback(callback, user_store=mock_store)

        # pending очищен
        assert 42 not in _survey_pending
        # survey помечен завершённым
        mock_store.mark_survey_completed_if_not.assert_called_once_with(42)
        # Логирование survey_completed
        log_calls = [
            c for c in mock_store.log_event.call_args_list if c[0][1] == "survey_completed"
        ]
        assert len(log_calls) == 1
        metadata = log_calls[0][0][2]
        assert metadata["pmf"] == "very"
        assert metadata["useful_feature"] == "recipe"
        assert "feedback" not in metadata
        # Бонусные корзины выданы
        mock_store.grant_bonus_carts.assert_called_once()
        # Ответ пользователю
        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Спасибо" in text
        assert "добавлено" in text

    async def test_done_callback_already_completed(self):
        """survey_done_callback при повторном нажатии — отказ."""
        callback = _make_callback_query("survey_done_very_recipe")
        mock_store = AsyncMock()
        mock_store.mark_survey_completed_if_not.return_value = False

        await survey_done_callback(callback, user_store=mock_store)

        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "уже прошли" in text

    async def test_done_callback_no_store(self):
        """survey_done_callback без user_store — ошибка."""
        callback = _make_callback_query("survey_done_very_recipe")

        await survey_done_callback(callback, user_store=None)

        callback.answer.assert_called_once()

    async def test_text_feedback_completes_survey(self):
        """Текстовый отзыв на шаге 3 завершает survey."""
        # Имитируем pending
        _survey_pending[42] = {"pmf": "somewhat", "feature": "cart"}
        msg = make_message("Хочу больше рецептов", user_id=42)
        mock_service = AsyncMock()
        mock_store = AsyncMock()
        mock_store.mark_survey_completed_if_not.return_value = True
        mock_store.grant_bonus_carts.return_value = 10

        await handle_text(msg, chat_engine=mock_service, user_store=mock_store)

        # pending очищен
        assert 42 not in _survey_pending
        # GigaChat НЕ вызван
        mock_service.process_message.assert_not_called()
        # survey завершён с feedback
        log_calls = [
            c for c in mock_store.log_event.call_args_list if c[0][1] == "survey_completed"
        ]
        assert len(log_calls) == 1
        metadata = log_calls[0][0][2]
        assert metadata["pmf"] == "somewhat"
        assert metadata["useful_feature"] == "cart"
        assert metadata["feedback"] == "Хочу больше рецептов"
        # Ответ пользователю
        msg.answer.assert_called_once()
        assert "Спасибо" in msg.answer.call_args[0][0]

    async def test_text_feedback_no_store_clears_pending(self):
        """Текст при pending + user_store=None: pending очищается, не застревает."""
        _survey_pending[42] = {"pmf": "very", "feature": "search"}
        msg = make_message("Мой отзыв", user_id=42)
        mock_service = AsyncMock()

        await handle_text(msg, chat_engine=mock_service, user_store=None)

        # pending очищен — пользователь не застрянет
        assert 42 not in _survey_pending
        # GigaChat НЕ вызван (сообщение перехвачено, но survey не завершён)
        mock_service.process_message.assert_not_called()
        # Пользователь получил сообщение об ошибке
        msg.answer.assert_called_once()
        assert "/survey" in msg.answer.call_args[0][0]

    async def test_text_after_failed_pending_goes_to_gigachat(self):
        """После очистки pending следующее сообщение уходит в GigaChat."""
        # Имитируем: pending был, user_store=None → pending очищен
        _survey_pending.pop(42, None)
        msg = make_message("Хочу купить молоко", user_id=42)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Вот молоко!"

        await handle_text(msg, chat_engine=mock_service, user_store=None)

        # Теперь GigaChat вызван нормально
        mock_service.process_message.assert_called_once_with(
            42,
            "Хочу купить молоко",
            on_progress=ANY,
        )

    async def test_text_without_pending_goes_to_gigachat(self):
        """Обычный текст (без pending) обрабатывается GigaChat."""
        # Убеждаемся что нет pending
        _survey_pending.pop(42, None)
        msg = make_message("Хочу купить молоко", user_id=42)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Вот молоко!"

        await handle_text(msg, chat_engine=mock_service, user_store=None)

        mock_service.process_message.assert_called_once_with(
            42,
            "Хочу купить молоко",
            on_progress=ANY,
        )


# ============================================================================
# Admin Commands: analytics, funnel, grant_carts, survey_stats
# ============================================================================


class TestAdminAnalytics:
    """Тесты /admin_analytics."""

    async def test_admin_analytics_success(self):
        """Администратор получает аналитику."""
        msg = make_message("/admin_analytics 7", user_id=1)
        mock_agg = AsyncMock()
        mock_agg.get_summary.return_value = {
            "avg_dau": 15,
            "total_new_users": 50,
            "total_sessions": 200,
            "total_carts": 30,
            "total_gmv": 45000,
            "avg_cart_value": 1500,
            "total_searches": 150,
            "total_errors": 5,
            "total_limits": 10,
            "total_surveys": 3,
            "period_start": "2026-02-06",
            "period_end": "2026-02-12",
        }

        await cmd_admin_analytics(
            msg,
            stats_aggregator=mock_agg,
        )

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Аналитика" in text
        assert "DAU" in text

    async def test_admin_analytics_no_aggregator(self):
        """Без StatsAggregator — сообщение об ошибке."""
        msg = make_message("/admin_analytics", user_id=1)

        await cmd_admin_analytics(
            msg,
            stats_aggregator=None,
        )

        text = msg.answer.call_args[0][0]
        assert "не настроен" in text

    async def test_admin_analytics_default_days(self):
        """По умолчанию 7 дней."""
        msg = make_message("/admin_analytics", user_id=1)
        mock_agg = AsyncMock()
        mock_agg.get_summary.return_value = {
            "avg_dau": 0,
            "total_new_users": 0,
            "total_sessions": 0,
            "total_carts": 0,
            "total_gmv": 0,
            "avg_cart_value": 0,
            "total_searches": 0,
            "total_errors": 0,
            "total_limits": 0,
            "total_surveys": 0,
            "period_start": "—",
            "period_end": "—",
        }

        await cmd_admin_analytics(
            msg,
            stats_aggregator=mock_agg,
        )

        mock_agg.get_summary.assert_called_once_with(7)


class TestAdminFunnel:
    """Тесты /admin_funnel."""

    async def test_admin_funnel_success(self):
        """Администратор получает воронку."""
        msg = make_message("/admin_funnel 14", user_id=1)
        mock_agg = AsyncMock()
        mock_agg.get_funnel.return_value = {
            "started": 100,
            "active": 80,
            "searched": 60,
            "carted": 20,
            "hit_limit": 5,
            "surveyed": 3,
        }

        await cmd_admin_funnel(
            msg,
            stats_aggregator=mock_agg,
        )

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Воронка" in text
        assert "/start" in text


class TestAdminGrantCarts:
    """Тесты /admin_grant_carts."""

    async def test_grant_carts_success(self):
        """Администратор выдаёт корзины пользователю."""
        msg = make_message("/admin_grant_carts 123 10", user_id=1)
        mock_store = AsyncMock()
        mock_store.grant_bonus_carts.return_value = 15

        await cmd_admin_grant_carts(
            msg,
            user_store=mock_store,
        )

        mock_store.grant_bonus_carts.assert_called_once_with(123, 10)
        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "добавлено 10" in text
        assert "15" in text

    async def test_grant_carts_user_not_found(self):
        """Пользователь не найден."""
        msg = make_message("/admin_grant_carts 999 5", user_id=1)
        mock_store = AsyncMock()
        mock_store.grant_bonus_carts.return_value = 0

        await cmd_admin_grant_carts(
            msg,
            user_store=mock_store,
        )

        text = msg.answer.call_args[0][0]
        assert "не найден" in text

    async def test_grant_carts_no_args(self):
        """Без аргументов — сообщение об использовании."""
        msg = make_message("/admin_grant_carts", user_id=1)
        mock_store = AsyncMock()

        await cmd_admin_grant_carts(
            msg,
            user_store=mock_store,
        )

        text = msg.answer.call_args[0][0]
        assert "Использование" in text

    async def test_grant_carts_invalid_amount(self):
        """amount вне диапазона [1, 100]."""
        msg = make_message("/admin_grant_carts 123 200", user_id=1)
        mock_store = AsyncMock()

        await cmd_admin_grant_carts(
            msg,
            user_store=mock_store,
        )

        text = msg.answer.call_args[0][0]
        assert "от 1 до 100" in text


class TestAdminResetCarts:
    """Тесты /admin_reset_carts."""

    async def test_reset_carts_success(self):
        """Администратор сбрасывает только счётчик корзин пользователя."""
        msg = make_message("/admin_reset_carts 123", user_id=1)
        mock_store = AsyncMock()
        mock_store.reset_carts.return_value = {
            "carts_created": 0,
            "cart_limit": 5,
            "survey_completed": True,
        }

        await cmd_admin_reset_carts(
            msg,
            user_store=mock_store,
        )

        mock_store.reset_carts.assert_called_once_with(123)
        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Счётчик корзин сброшен" in text
        assert "carts_created: 0" in text
        assert "cart_limit: 5" in text

    async def test_reset_carts_no_args(self):
        """Без аргумента user_id — подсказка по использованию."""
        msg = make_message("/admin_reset_carts", user_id=1)
        mock_store = AsyncMock()

        await cmd_admin_reset_carts(
            msg,
            user_store=mock_store,
        )

        text = msg.answer.call_args[0][0]
        assert "Использование" in text

    async def test_reset_carts_invalid_user_id(self):
        """Невалидный user_id — понятная ошибка."""
        msg = make_message("/admin_reset_carts abc", user_id=1)
        mock_store = AsyncMock()

        await cmd_admin_reset_carts(
            msg,
            user_store=mock_store,
        )

        text = msg.answer.call_args[0][0]
        assert "должен быть числом" in text

    async def test_reset_carts_user_not_found(self):
        """Если пользователя нет, возвращается сообщение об этом."""
        msg = make_message("/admin_reset_carts 999", user_id=1)
        mock_store = AsyncMock()
        mock_store.reset_carts.return_value = None

        await cmd_admin_reset_carts(
            msg,
            user_store=mock_store,
        )

        text = msg.answer.call_args[0][0]
        assert "не найден" in text


class TestAdminSurveyStats:
    """Тесты /admin_survey_stats."""

    async def test_survey_stats_success(self):
        """Администратор получает статистику survey с PMF score."""
        msg = make_message("/admin_survey_stats", user_id=1)
        mock_store = AsyncMock()
        mock_store.get_survey_stats.return_value = {
            "total": 10,
            "pmf": [
                {"answer": "very", "cnt": 6},
                {"answer": "somewhat", "cnt": 3},
                {"answer": "not", "cnt": 1},
            ],
            "features": [
                {"feat": "search", "cnt": 5},
                {"feat": "recipe", "cnt": 4},
            ],
            "feedback_count": 3,
            "recent_feedback": [
                {"text": "Больше рецептов!", "created_at": "2026-02-14"},
            ],
        }

        await cmd_admin_survey_stats(
            msg,
            user_store=mock_store,
        )

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Survey" in text
        assert "PMF score" in text
        assert "60%" in text  # 6 very / 10 total = 60%
        assert "Отзывов" in text

    async def test_survey_stats_empty(self):
        """Нет данных — сообщение об этом."""
        msg = make_message("/admin_survey_stats", user_id=1)
        mock_store = AsyncMock()
        mock_store.get_survey_stats.return_value = {
            "total": 0,
            "pmf": [],
            "features": [],
            "feedback_count": 0,
            "recent_feedback": [],
        }

        await cmd_admin_survey_stats(
            msg,
            user_store=mock_store,
        )

        text = msg.answer.call_args[0][0]
        assert "Ни один" in text


# ============================================================================
# /admin_cart_feedback
# ============================================================================


class TestAdminCartFeedback:
    """Тесты /admin_cart_feedback."""

    async def test_cart_feedback_stats_success(self):
        """Администратор получает статистику фидбека корзин."""
        msg = make_message("/admin_cart_feedback", user_id=1)
        mock_store = AsyncMock()
        mock_store.get_cart_feedback_stats.return_value = {
            "total": 20,
            "positive": 15,
            "negative": 5,
            "satisfaction_pct": 75.0,
            "reasons": [
                {"reason": "Не те товары", "cnt": 3},
                {"reason": "Слишком дорого", "cnt": 2},
            ],
            "recent_negative": [
                {
                    "user_id": 42,
                    "reason": "Не те товары",
                    "cart_link": "https://vkusvill.ru/cart/1",
                    "created_at": None,
                },
            ],
            "daily": [],
        }

        await cmd_admin_cart_feedback(msg, user_store=mock_store)

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "20" in text
        assert "15" in text
        assert "75" in text
        assert "Не те товары" in text

    async def test_cart_feedback_stats_empty(self):
        """Нет данных — сообщение об этом."""
        msg = make_message("/admin_cart_feedback", user_id=1)
        mock_store = AsyncMock()
        mock_store.get_cart_feedback_stats.return_value = {
            "total": 0,
            "positive": 0,
            "negative": 0,
            "satisfaction_pct": 0.0,
            "reasons": [],
            "recent_negative": [],
            "daily": [],
        }

        await cmd_admin_cart_feedback(msg, user_store=mock_store)

        text = msg.answer.call_args[0][0]
        assert "Пока нет" in text

    async def test_cart_feedback_no_store(self):
        """Без user_store — сообщение о недоступности."""
        msg = make_message("/admin_cart_feedback", user_id=1)

        await cmd_admin_cart_feedback(msg, user_store=None)

        text = msg.answer.call_args[0][0]
        assert "недоступна" in text

    async def test_cart_feedback_error(self):
        """Ошибка БД — сообщение об ошибке."""
        msg = make_message("/admin_cart_feedback", user_id=1)
        mock_store = AsyncMock()
        mock_store.get_cart_feedback_stats.side_effect = RuntimeError("DB error")

        await cmd_admin_cart_feedback(msg, user_store=mock_store)

        text = msg.answer.call_args[0][0]
        assert "Ошибка" in text


# ============================================================================
# handle_text: логирование bot_error
# ============================================================================


class TestHandleTextErrorLogging:
    """Тесты логирования bot_error при ошибке в handle_text."""

    async def test_logs_bot_error_event(self):
        """При ошибке process_message логируется bot_error."""
        msg = make_message("Тест", user_id=42)
        mock_service = AsyncMock()
        mock_service.process_message.side_effect = RuntimeError("Boom!")
        mock_store = AsyncMock()
        mock_store.mark_consent.return_value = False  # consent уже был

        await handle_text(msg, chat_engine=mock_service, user_store=mock_store)

        # Ищем именно bot_error среди вызовов log_event
        error_calls = [c for c in mock_store.log_event.call_args_list if c[0][1] == "bot_error"]
        assert len(error_calls) == 1
        call_args = error_calls[0][0]
        assert call_args[0] == 42
        assert call_args[2]["error_type"] == "RuntimeError"

    async def test_no_log_without_user_store(self):
        """Без user_store ошибка не логируется (не падает)."""
        msg = make_message("Тест", user_id=42)
        mock_service = AsyncMock()
        mock_service.process_message.side_effect = RuntimeError("Boom!")

        await handle_text(msg, chat_engine=mock_service, user_store=None)

        msg.answer.assert_called_once()
        assert "ошибка" in msg.answer.call_args[0][0].lower()


# ============================================================================
# Фидбек-кнопки по корзине
# ============================================================================


def _make_cart_callback(
    data: str,
    user_id: int = 42,
    cart_url: str = "https://vkusvill.ru/cart/abc123",
) -> MagicMock:
    """Создать мок CallbackQuery с reply_markup, содержащим cart_url."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.message = MagicMock()
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.message.reply_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть корзину", url=cart_url)],
            [
                InlineKeyboardButton(text="👍", callback_data="cart_fb_pos"),
                InlineKeyboardButton(text="👎", callback_data="cart_fb_neg"),
            ],
        ],
    )
    callback.answer = AsyncMock()
    return callback


class TestCartFeedbackPositive:
    """Тесты положительного фидбека по корзине."""

    async def test_logs_positive_feedback(self):
        """cart_fb_pos → log_event(cart_feedback, positive)."""
        callback = _make_cart_callback("cart_fb_pos")
        mock_store = AsyncMock()

        await cart_feedback_positive(callback, user_store=mock_store)

        mock_store.log_event.assert_called_once()
        args = mock_store.log_event.call_args[0]
        assert args[0] == 42
        assert args[1] == "cart_feedback"
        assert args[2]["rating"] == "positive"
        assert "vkusvill.ru" in args[2]["cart_link"]

    async def test_edits_reply_markup(self):
        """После 👍 убираются кнопки фидбека."""
        callback = _make_cart_callback("cart_fb_pos")
        mock_store = AsyncMock()

        await cart_feedback_positive(callback, user_store=mock_store)

        callback.message.edit_reply_markup.assert_called_once()
        new_markup = callback.message.edit_reply_markup.call_args[1]["reply_markup"]
        # Должна остаться только одна кнопка — корзина
        assert len(new_markup.inline_keyboard) == 1
        assert new_markup.inline_keyboard[0][0].url is not None

    async def test_answers_callback(self):
        """После 👍 вызывается callback.answer."""
        callback = _make_cart_callback("cart_fb_pos")

        await cart_feedback_positive(callback, user_store=None)

        callback.answer.assert_called_once()

    async def test_works_without_user_store(self):
        """Без user_store не падает."""
        callback = _make_cart_callback("cart_fb_pos")

        await cart_feedback_positive(callback, user_store=None)

        callback.answer.assert_called_once()

    async def test_grants_feedback_bonus_when_due(self):
        """При первом feedback начисляется бонус и логируется событие."""
        callback = _make_cart_callback("cart_fb_pos")
        mock_store = AsyncMock()
        mock_store.grant_feedback_bonus_if_due.return_value = {
            "granted": True,
            "new_limit": 7,
        }

        await cart_feedback_positive(callback, user_store=mock_store)

        mock_store.grant_feedback_bonus_if_due.assert_called_once()
        bonus_calls = [
            c for c in mock_store.log_event.call_args_list if c[0][1] == "feedback_bonus_granted"
        ]
        assert len(bonus_calls) == 1
        assert "Начислено +2" in callback.answer.call_args[0][0]


class TestCartFeedbackNegative:
    """Тесты негативного фидбека — уточняющие причины."""

    async def test_shows_reason_buttons(self):
        """cart_fb_neg → клавиатура с причинами."""
        callback = _make_cart_callback("cart_fb_neg")

        await cart_feedback_negative(callback)

        callback.message.edit_reply_markup.assert_called_once()
        new_markup = callback.message.edit_reply_markup.call_args[1]["reply_markup"]
        # Первый ряд — корзина, потом 2 ряда причин
        assert len(new_markup.inline_keyboard) == 3
        # Проверяем что есть кнопки причин
        all_data = [
            btn.callback_data
            for row in new_markup.inline_keyboard
            for btn in row
            if btn.callback_data
        ]
        assert "cart_fb_r_products" in all_data
        assert "cart_fb_r_quantity" in all_data
        assert "cart_fb_r_price" in all_data
        assert "cart_fb_r_other" in all_data

    async def test_answers_callback(self):
        """Вызывается callback.answer после нажатия 👎."""
        callback = _make_cart_callback("cart_fb_neg")

        await cart_feedback_negative(callback)

        callback.answer.assert_called_once()


class TestCartFeedbackReason:
    """Тесты выбора конкретной причины."""

    async def test_logs_reason_products(self):
        """cart_fb_r_products → log_event(negative, Не те товары)."""
        callback = _make_cart_callback("cart_fb_r_products")
        mock_store = AsyncMock()

        await cart_feedback_reason(callback, user_store=mock_store)

        mock_store.log_event.assert_called_once()
        args = mock_store.log_event.call_args[0]
        assert args[1] == "cart_feedback"
        assert args[2]["rating"] == "negative"
        assert args[2]["reason"] == "Не те товары"

    async def test_logs_reason_quantity(self):
        """cart_fb_r_quantity → log_event(negative, Количество)."""
        callback = _make_cart_callback("cart_fb_r_quantity")
        mock_store = AsyncMock()

        await cart_feedback_reason(callback, user_store=mock_store)

        args = mock_store.log_event.call_args[0]
        assert args[2]["reason"] == "Неправильное количество"

    async def test_logs_reason_price(self):
        """cart_fb_r_price → log_event(negative, Дорого)."""
        callback = _make_cart_callback("cart_fb_r_price")
        mock_store = AsyncMock()

        await cart_feedback_reason(callback, user_store=mock_store)

        args = mock_store.log_event.call_args[0]
        assert args[2]["reason"] == "Слишком дорого"

    async def test_edits_markup_to_cart_only(self):
        """После выбора причины остаётся только кнопка корзины."""
        callback = _make_cart_callback("cart_fb_r_other")
        mock_store = AsyncMock()

        await cart_feedback_reason(callback, user_store=mock_store)

        callback.message.edit_reply_markup.assert_called_once()
        new_markup = callback.message.edit_reply_markup.call_args[1]["reply_markup"]
        assert len(new_markup.inline_keyboard) == 1

    async def test_works_without_user_store(self):
        """Без user_store не падает."""
        callback = _make_cart_callback("cart_fb_r_price")

        await cart_feedback_reason(callback, user_store=None)

        callback.answer.assert_called_once()

    async def test_bonus_cooldown_message(self):
        """Если бонус не положен из-за cooldown — сообщение об ограничении."""
        callback = _make_cart_callback("cart_fb_r_price")
        mock_store = AsyncMock()
        mock_store.grant_feedback_bonus_if_due.return_value = {
            "granted": False,
            "reason": "cooldown",
        }

        await cart_feedback_reason(callback, user_store=mock_store)

        mock_store.grant_feedback_bonus_if_due.assert_called_once()
        assert "1 раз в 30 дней" in callback.answer.call_args[0][0]


class TestExtractCartLinkWithFeedback:
    """Тесты _extract_cart_link с кнопками фидбека."""

    def test_includes_feedback_buttons(self):
        """Keyboard содержит кнопки фидбека."""
        html = 'Корзина готова! <a href="https://vkusvill.ru/cart/1">Открыть корзину</a>'
        _text, keyboard = _extract_cart_link(html)
        assert keyboard is not None
        # 2 ряда: корзина + фидбек
        assert len(keyboard.inline_keyboard) == 2
        fb_row = keyboard.inline_keyboard[1]
        assert fb_row[0].callback_data == "cart_fb_pos"
        assert fb_row[1].callback_data == "cart_fb_neg"

    def test_no_feedback_without_cart(self):
        """Без ссылки на корзину — keyboard=None."""
        _text, keyboard = _extract_cart_link("Просто текст")
        assert keyboard is None
