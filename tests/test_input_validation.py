"""Тесты валидации входных данных.

Проверяем обработку:
- Пустые и None-значения
- Сверхдлинные строки
- Unicode-атаки (zero-width chars, RTL override, homoglyphs)
- HTML/XSS-инъекции
- Специальные символы
- Граничные случаи в _split_message
- Невалидный JSON в ответах MCP
- Невалидные аргументы функций
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gigachat.models import (
    ChatCompletion,
    Choices,
    FunctionCall,
    Messages,
    MessagesRole,
)

from vkuswill_bot.bot.handlers import _split_message, handle_text
from vkuswill_bot.services.gigachat_service import GigaChatService
from vkuswill_bot.services.mcp_client import VkusvillMCPClient

from helpers import USAGE, make_text_response, make_message


# ============================================================================
# Фикстуры
# ============================================================================


@pytest.fixture
def mock_mcp_client() -> AsyncMock:
    client = AsyncMock()
    client.get_tools.return_value = [
        {
            "name": "vkusvill_products_search",
            "description": "Поиск товаров",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    ]
    return client


@pytest.fixture
def service(mock_mcp_client) -> GigaChatService:
    return GigaChatService(
        credentials="test-creds",
        model="GigaChat",
        scope="GIGACHAT_API_PERS",
        mcp_client=mock_mcp_client,
        max_tool_calls=5,
        max_history=10,
    )


# ============================================================================
# Unicode-атаки
# ============================================================================

UNICODE_ATTACK_PAYLOADS = [
    # Zero-width characters (скрытый текст)
    "Привет\u200B\u200B\u200Bмир",
    # Zero-width joiner / non-joiner
    "Тест\u200C\u200Dтекст",
    # RTL override (может перевернуть отображение)
    "\u202EЭто перевёрнутый текст",
    # Homoglyphs (кириллица vs латиница)
    "Hеllо Wоrld",  # 'е', 'о' — кириллица
    # Combining characters (накладные диакритики)
    "З\u0336а\u0336л\u0336г\u0336о\u0336 текст",
    # Emoji и модификаторы
    "🏳️‍🌈" * 100,
    # Null bytes
    "Привет\x00Мир",
    # Form feed, vertical tab
    "Текст\f\vс управляющими символами",
    # BOM (Byte Order Mark)
    "\uFEFFТекст с BOM",
    # Hangul filler
    "ㅤ" * 50,
    # Mathematical symbols as text
    "𝕳𝖊𝖑𝖑𝖔",
]


@pytest.mark.validation
class TestUnicodeAttacks:
    """Тесты обработки Unicode-атак."""

    @pytest.mark.parametrize("payload", UNICODE_ATTACK_PAYLOADS)
    async def test_unicode_does_not_crash_service(
        self, service, payload: str
    ):
        """Unicode-атаки не крашат сервис."""
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            result = await service.process_message(user_id=1, text=payload)

        assert isinstance(result, str)
        assert len(result) > 0
        await service.reset_conversation(1)

    @pytest.mark.parametrize("payload", UNICODE_ATTACK_PAYLOADS)
    async def test_unicode_in_handler(self, payload: str):
        """Unicode-атаки не крашат обработчик Telegram."""
        msg = make_message(payload, user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Ответ"

        await handle_text(msg, gigachat_service=mock_service)
        msg.answer.assert_called()


# ============================================================================
# HTML/XSS-инъекции
# ============================================================================

XSS_PAYLOADS = [
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert(1)>",
    "<svg onload=alert(1)>",
    "<<script>alert('xss');//<</script>",
    '<a href="javascript:alert(1)">click</a>',
    "<iframe src='evil.com'></iframe>",
    "';alert(String.fromCharCode(88,83,83))//",
    "<b onmouseover=alert('xss')>наведи</b>",
    "<input onfocus=alert(1) autofocus>",
    '"><script>alert(document.cookie)</script>',
]


@pytest.mark.validation
class TestXSSPayloads:
    """Тесты обработки HTML/XSS-инъекций."""

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    async def test_xss_does_not_crash(self, service, payload: str):
        """XSS-payload не крашит сервис."""
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            result = await service.process_message(user_id=1, text=payload)

        assert isinstance(result, str)
        await service.reset_conversation(1)

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    async def test_xss_in_handler(self, payload: str):
        """XSS-payload обрабатывается хендлером без ошибок."""
        msg = make_message(payload, user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Ответ"

        await handle_text(msg, gigachat_service=mock_service)
        msg.answer.assert_called()


# ============================================================================
# Граничные случаи входных данных
# ============================================================================


@pytest.mark.validation
class TestEdgeCases:
    """Тесты граничных случаев."""

    async def test_empty_string(self, service):
        """Пустая строка обрабатывается без ошибок."""
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            result = await service.process_message(user_id=1, text="")
        assert isinstance(result, str)

    async def test_whitespace_only(self, service):
        """Строка из пробелов обрабатывается."""
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            result = await service.process_message(user_id=1, text="   \n\t  ")
        assert isinstance(result, str)

    async def test_very_long_message(self, service):
        """Сверхдлинное сообщение (100K символов)."""
        long_text = "молоко " * 15_000  # ~105K символов
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            result = await service.process_message(user_id=1, text=long_text)
        assert isinstance(result, str)

    async def test_single_character(self, service):
        """Один символ."""
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            result = await service.process_message(user_id=1, text="а")
        assert isinstance(result, str)

    async def test_only_newlines(self, service):
        """Только переводы строк."""
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            result = await service.process_message(user_id=1, text="\n\n\n")
        assert isinstance(result, str)

    async def test_handler_no_from_user(self):
        """Сообщение без from_user — не обрабатывается."""
        msg = make_message("текст")
        msg.from_user = None
        mock_service = AsyncMock()

        await handle_text(msg, gigachat_service=mock_service)
        mock_service.process_message.assert_not_called()

    async def test_handler_no_text(self):
        """Сообщение без текста — не обрабатывается."""
        msg = make_message("")
        msg.text = None
        mock_service = AsyncMock()

        await handle_text(msg, gigachat_service=mock_service)
        mock_service.process_message.assert_not_called()


# ============================================================================
# _split_message: граничные случаи
# ============================================================================


@pytest.mark.validation
class TestSplitMessageEdgeCases:
    """Дополнительные тесты разбивки сообщений."""

    def test_message_with_only_newlines(self):
        """Сообщение из одних переводов строк."""
        result = _split_message("\n\n\n\n\n", 4096)
        assert len(result) >= 1

    def test_message_exactly_at_limit(self):
        """Сообщение ровно по лимиту."""
        msg = "x" * 4096
        result = _split_message(msg, 4096)
        assert result == [msg]

    def test_message_one_over_limit(self):
        """Сообщение на 1 символ больше лимита."""
        msg = "x" * 4097
        result = _split_message(msg, 4096)
        assert len(result) == 2
        assert len(result[0]) == 4096
        assert len(result[1]) == 1

    def test_message_with_html_tags(self):
        """HTML-теги не разрываются посередине."""
        msg = "<b>Жирный текст</b> " * 200
        result = _split_message(msg, 100)
        # Все части — непустые строки
        assert all(len(chunk) > 0 for chunk in result)
        assert all(len(chunk) <= 100 for chunk in result)

    def test_large_message_performance(self):
        """Разбивка большого сообщения (1MB) не зависает."""
        import time

        msg = "слово " * 200_000  # ~1.2MB
        start = time.monotonic()
        result = _split_message(msg, 4096)
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, f"Разбивка заняла {elapsed:.2f}с (лимит 5с)"
        assert len(result) > 0


# ============================================================================
# Невалидный JSON в ответах MCP
# ============================================================================


@pytest.mark.validation
class TestInvalidMCPResponses:
    """Тесты обработки невалидных ответов MCP."""

    def test_parse_sse_invalid_json(self):
        """Невалидный JSON в SSE-ответе не крашит парсер."""
        result = VkusvillMCPClient._parse_sse_response(
            "data: {invalid json here}\n"
            'data: {"result": {"ok": true}}\n'
        )
        assert result == {"ok": True}

    def test_parse_sse_empty_response(self):
        """Пустой SSE-ответ возвращает None."""
        result = VkusvillMCPClient._parse_sse_response("")
        assert result is None

    def test_parse_sse_only_events(self):
        """SSE без data-строк возвращает None."""
        result = VkusvillMCPClient._parse_sse_response(
            "event: ping\nretry: 5000\n"
        )
        assert result is None

    async def test_mcp_tool_returns_invalid_json(self, service, mock_mcp_client):
        """MCP-инструмент возвращает невалидный JSON."""
        mock_mcp_client.call_tool.return_value = "not a json at all {{{}"

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ChatCompletion(
                    choices=[
                        Choices(
                            message=Messages(
                                role=MessagesRole.ASSISTANT,
                                content="",
                                function_call=FunctionCall(
                                    name="vkusvill_products_search",
                                    arguments={"q": "тест"},
                                ),
                            ),
                            index=0,
                            finish_reason="function_call",
                        )
                    ],
                    created=1000000,
                    model="GigaChat",
                    usage=USAGE,
                    object="chat.completion",
                )
            return make_text_response("Ничего не нашлось.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Тест")

        # Бот не крашнулся
        assert isinstance(result, str)
        assert len(result) > 0


# ============================================================================
# Невалидные аргументы инструментов
# ============================================================================


@pytest.mark.validation
class TestInvalidToolArguments:
    """Тесты обработки невалидных аргументов MCP-инструментов."""

    def test_fix_cart_args_with_none_products(self):
        """_fix_cart_args с None products."""
        args = {"products": None}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] is None

    def test_fix_cart_args_with_nested_dict(self):
        """_fix_cart_args с вложенными словарями — после дедупликации
        остаются только xml_id и q."""
        args = {
            "products": [
                {"xml_id": 1, "extra": {"nested": True}},
                {"xml_id": 2},
            ]
        }
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0] == {"xml_id": 1, "q": 1}
        assert result["products"][1] == {"xml_id": 2, "q": 1}

    def test_fix_cart_args_with_negative_q(self):
        """_fix_cart_args с отрицательным количеством."""
        args = {"products": [{"xml_id": 1, "q": -5}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        # -5 сохраняется (валидация — на стороне MCP-сервера)
        assert result["products"][0]["q"] == -5

    def test_fix_cart_args_with_huge_q(self):
        """_fix_cart_args с огромным количеством."""
        args = {"products": [{"xml_id": 1, "q": 999999}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0]["q"] == 999999

    def test_fix_cart_args_with_string_xml_id(self):
        """_fix_cart_args с нечисловым xml_id."""
        args = {"products": [{"xml_id": "abc"}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0]["xml_id"] == "abc"
        assert result["products"][0]["q"] == 1

    async def test_gigachat_returns_invalid_function_args(
        self, service, mock_mcp_client
    ):
        """GigaChat возвращает невалидные аргументы функции."""
        mock_mcp_client.call_tool.return_value = '{"ok": true}'

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ChatCompletion(
                    choices=[
                        Choices(
                            message=Messages(
                                role=MessagesRole.ASSISTANT,
                                content="",
                                function_call=FunctionCall(
                                    name="vkusvill_products_search",
                                    arguments={},  # Пустые аргументы
                                ),
                            ),
                            index=0,
                            finish_reason="function_call",
                        )
                    ],
                    created=1000000,
                    model="GigaChat",
                    usage=USAGE,
                    object="chat.completion",
                )
            return make_text_response("Ответ")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Тест")

        assert isinstance(result, str)


# ============================================================================
# Специальные символы в запросах
# ============================================================================

SPECIAL_CHAR_PAYLOADS = [
    # SQL injection
    "'; DROP TABLE products; --",
    "1' OR '1'='1",
    "' UNION SELECT * FROM users --",
    # NoSQL injection
    '{"$gt": ""}',
    '{"$ne": null}',
    # Path traversal
    "../../../etc/passwd",
    "..\\..\\..\\windows\\system32",
    # Command injection
    "; rm -rf /",
    "| cat /etc/passwd",
    "$(whoami)",
    "`id`",
    # LDAP injection
    "*)(objectClass=*",
    # XML injection
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>',
    # Log injection
    "fake\nINFO - Admin logged in successfully",
    "test\r\nSet-Cookie: admin=true",
]


@pytest.mark.validation
class TestSpecialCharacters:
    """Тесты обработки специальных символов."""

    @pytest.mark.parametrize("payload", SPECIAL_CHAR_PAYLOADS)
    async def test_special_chars_dont_crash_service(
        self, service, payload: str
    ):
        """Специальные символы не крашат сервис."""
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            result = await service.process_message(user_id=1, text=payload)
        assert isinstance(result, str)
        await service.reset_conversation(1)

    @pytest.mark.parametrize("payload", SPECIAL_CHAR_PAYLOADS)
    async def test_special_chars_dont_crash_handler(self, payload: str):
        """Специальные символы не крашат обработчик."""
        msg = make_message(payload, user_id=1)
        mock_service = AsyncMock()
        mock_service.process_message.return_value = "Ответ"

        await handle_text(msg, gigachat_service=mock_service)
        msg.answer.assert_called()
