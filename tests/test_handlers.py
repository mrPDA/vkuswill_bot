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
from unittest.mock import AsyncMock, MagicMock, patch


from vkuswill_bot.bot.handlers import (
    _sanitize_telegram_html,
    _send_typing_periodically,
    _split_message,
    cmd_help,
    cmd_reset,
    cmd_start,
    cmd_survey,
    cmd_admin_analytics,
    cmd_admin_funnel,
    cmd_admin_grant_carts,
    cmd_admin_survey_stats,
    handle_text,
    survey_nps_callback,
    survey_feature_callback,
    survey_continue_callback,
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
            "reason": "ok",
            "bonus": 3,
            "new_limit": 8,
        }

        await cmd_start(msg, user_store=mock_store, db_user={"message_count": 1})

        # Находим вызов log_event с "bot_start"
        bot_start_calls = [c for c in mock_store.log_event.call_args_list if c[0][1] == "bot_start"]
        assert len(bot_start_calls) == 1
        metadata = bot_start_calls[0][0][2]
        assert metadata["source"] == "referral"
        assert metadata["referrer_id"] == 12345

        # Реферал обработан
        mock_store.process_referral.assert_called_once_with(42, 12345, 3)

        # Бонус залогирован
        referral_calls = [
            c for c in mock_store.log_event.call_args_list if c[0][1] == "referral_bonus_granted"
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
            "reason": "ok",
            "bonus": 3,
            "new_limit": 8,
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
    """Тесты survey flow: опрос для бонусных корзин."""

    async def test_cmd_survey_starts(self):
        """/survey запускает опрос."""
        msg = make_message("/survey", user_id=42)
        mock_store = AsyncMock()

        await cmd_survey(
            msg,
            user_store=mock_store,
            db_user={"survey_completed": False},
        )

        msg.answer.assert_called_once()
        answer_text = msg.answer.call_args[0][0]
        assert "Оцените бота" in answer_text

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

    async def test_nps_callback(self):
        """survey_nps_callback переходит к выбору фичи."""
        callback = _make_callback_query("survey_nps_3")

        await survey_nps_callback(callback)

        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Оценка" in text
        assert "функция" in text
        callback.answer.assert_called_once()

    async def test_feature_callback(self):
        """survey_feature_callback переходит к вопросу о продолжении."""
        callback = _make_callback_query("survey_feat_search_4")

        await survey_feature_callback(callback)

        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Поиск товаров" in text
        assert "пользоваться ботом" in text
        callback.answer.assert_called_once()

    async def test_continue_callback_completes_survey(self):
        """survey_continue_callback завершает опрос и выдаёт бонус."""
        callback = _make_callback_query("survey_cont_yes_5_recipe")
        mock_store = AsyncMock()
        mock_store.mark_survey_completed_if_not.return_value = True
        mock_store.grant_bonus_carts.return_value = 10

        await survey_continue_callback(callback, user_store=mock_store)

        # survey помечен завершённым
        mock_store.mark_survey_completed_if_not.assert_called_once_with(42)
        # Логирование survey_completed
        log_calls = [
            c for c in mock_store.log_event.call_args_list if c[0][1] == "survey_completed"
        ]
        assert len(log_calls) == 1
        metadata = log_calls[0][0][2]
        assert metadata["nps"] == 5
        assert metadata["useful_feature"] == "recipe"
        assert metadata["will_continue"] == "yes"
        # Бонусные корзины выданы
        mock_store.grant_bonus_carts.assert_called_once()
        # Ответ пользователю
        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Спасибо" in text
        assert "добавлено" in text

    async def test_continue_callback_already_completed(self):
        """survey_continue_callback при повторном нажатии — отказ."""
        callback = _make_callback_query("survey_cont_yes_5_recipe")
        mock_store = AsyncMock()
        mock_store.mark_survey_completed_if_not.return_value = False

        await survey_continue_callback(callback, user_store=mock_store)

        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "уже прошли" in text

    async def test_continue_callback_no_store(self):
        """survey_continue_callback без user_store — ошибка."""
        callback = _make_callback_query("survey_cont_yes_5_recipe")

        await survey_continue_callback(callback, user_store=None)

        callback.answer.assert_called_once()


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
            db_user={"role": "admin"},
            stats_aggregator=mock_agg,
        )

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Аналитика" in text
        assert "DAU" in text

    async def test_admin_analytics_not_admin(self):
        """Не-админ не получает аналитику."""
        msg = make_message("/admin_analytics", user_id=1)

        await cmd_admin_analytics(
            msg,
            db_user={"role": "user"},
            stats_aggregator=AsyncMock(),
        )

        text = msg.answer.call_args[0][0]
        assert "нет прав" in text

    async def test_admin_analytics_no_aggregator(self):
        """Без StatsAggregator — сообщение об ошибке."""
        msg = make_message("/admin_analytics", user_id=1)

        await cmd_admin_analytics(
            msg,
            db_user={"role": "admin"},
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
            db_user={"role": "admin"},
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
            db_user={"role": "admin"},
            stats_aggregator=mock_agg,
        )

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Воронка" in text
        assert "/start" in text

    async def test_admin_funnel_not_admin(self):
        """Не-админ не получает воронку."""
        msg = make_message("/admin_funnel", user_id=1)

        await cmd_admin_funnel(
            msg,
            db_user={"role": "user"},
            stats_aggregator=AsyncMock(),
        )

        text = msg.answer.call_args[0][0]
        assert "нет прав" in text


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
            db_user={"role": "admin"},
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
            db_user={"role": "admin"},
        )

        text = msg.answer.call_args[0][0]
        assert "не найден" in text

    async def test_grant_carts_not_admin(self):
        """Не-админ не может выдавать корзины."""
        msg = make_message("/admin_grant_carts 123 10", user_id=1)
        mock_store = AsyncMock()

        await cmd_admin_grant_carts(
            msg,
            user_store=mock_store,
            db_user={"role": "user"},
        )

        text = msg.answer.call_args[0][0]
        assert "нет прав" in text

    async def test_grant_carts_no_args(self):
        """Без аргументов — сообщение об использовании."""
        msg = make_message("/admin_grant_carts", user_id=1)
        mock_store = AsyncMock()

        await cmd_admin_grant_carts(
            msg,
            user_store=mock_store,
            db_user={"role": "admin"},
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
            db_user={"role": "admin"},
        )

        text = msg.answer.call_args[0][0]
        assert "от 1 до 100" in text


class TestAdminSurveyStats:
    """Тесты /admin_survey_stats."""

    async def test_survey_stats_success(self):
        """Администратор получает статистику survey."""
        msg = make_message("/admin_survey_stats", user_id=1)
        mock_store = AsyncMock()
        mock_store.get_survey_stats.return_value = {
            "total": 10,
            "avg_nps": 4.5,
            "will_continue": [
                {"answer": "yes", "cnt": 7},
                {"answer": "maybe", "cnt": 3},
            ],
            "features": [
                {"feat": "search", "cnt": 5},
                {"feat": "recipe", "cnt": 4},
            ],
        }

        await cmd_admin_survey_stats(
            msg,
            user_store=mock_store,
            db_user={"role": "admin"},
        )

        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Survey" in text
        assert "4.5" in text

    async def test_survey_stats_empty(self):
        """Нет данных — сообщение об этом."""
        msg = make_message("/admin_survey_stats", user_id=1)
        mock_store = AsyncMock()
        mock_store.get_survey_stats.return_value = {
            "total": 0,
            "avg_nps": 0.0,
            "will_continue": [],
            "features": [],
        }

        await cmd_admin_survey_stats(
            msg,
            user_store=mock_store,
            db_user={"role": "admin"},
        )

        text = msg.answer.call_args[0][0]
        assert "Ни один" in text

    async def test_survey_stats_not_admin(self):
        """Не-админ не получает статистику."""
        msg = make_message("/admin_survey_stats", user_id=1)
        mock_store = AsyncMock()

        await cmd_admin_survey_stats(
            msg,
            user_store=mock_store,
            db_user={"role": "user"},
        )

        text = msg.answer.call_args[0][0]
        assert "нет прав" in text


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

        await handle_text(msg, gigachat_service=mock_service, user_store=mock_store)

        mock_store.log_event.assert_called_once()
        call_args = mock_store.log_event.call_args[0]
        assert call_args[0] == 42
        assert call_args[1] == "bot_error"
        assert call_args[2]["error_type"] == "RuntimeError"

    async def test_no_log_without_user_store(self):
        """Без user_store ошибка не логируется (не падает)."""
        msg = make_message("Тест", user_id=42)
        mock_service = AsyncMock()
        mock_service.process_message.side_effect = RuntimeError("Boom!")

        await handle_text(msg, gigachat_service=mock_service, user_store=None)

        msg.answer.assert_called_once()
        assert "ошибка" in msg.answer.call_args[0][0].lower()
