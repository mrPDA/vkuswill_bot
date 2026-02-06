"""Тесты GigaChatService.

Тестируем:
- Управление историей диалогов
- Обрезку истории
- Сброс диалога
- Цикл function calling (process_message) с моками GigaChat и MCP
- Обработку ошибок GigaChat
- Определение зацикливания tool-вызовов
- Лимит шагов
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gigachat.models import (
    Chat,
    ChatCompletion,
    Choices,
    FunctionCall,
    Messages,
    MessagesRole,
    Usage,
)

from vkuswill_bot.services.gigachat_service import GigaChatService, SYSTEM_PROMPT


# ============================================================================
# Фикстуры
# ============================================================================


@pytest.fixture
def mock_mcp_client() -> AsyncMock:
    """Замоканный MCP-клиент."""
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
    """GigaChatService с замоканным MCP-клиентом."""
    svc = GigaChatService(
        credentials="test-creds",
        model="GigaChat",
        scope="GIGACHAT_API_PERS",
        mcp_client=mock_mcp_client,
        max_tool_calls=5,
        max_history=10,
    )
    return svc


_USAGE = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)


def _make_text_response(text: str) -> ChatCompletion:
    """Создать ответ GigaChat с текстом (без function_call)."""
    return ChatCompletion(
        choices=[
            Choices(
                message=Messages(
                    role=MessagesRole.ASSISTANT,
                    content=text,
                ),
                index=0,
                finish_reason="stop",
            )
        ],
        created=1000000,
        model="GigaChat",
        usage=_USAGE,
        object="chat.completion",
    )


def _make_function_call_response(
    name: str, arguments: dict | str
) -> ChatCompletion:
    """Создать ответ GigaChat с вызовом функции."""
    # FunctionCall.arguments ожидает dict
    if isinstance(arguments, str):
        args = json.loads(arguments)
    else:
        args = arguments
    return ChatCompletion(
        choices=[
            Choices(
                message=Messages(
                    role=MessagesRole.ASSISTANT,
                    content="",
                    function_call=FunctionCall(name=name, arguments=args),
                ),
                index=0,
                finish_reason="function_call",
            )
        ],
        created=1000000,
        model="GigaChat",
        usage=_USAGE,
        object="chat.completion",
    )


# ============================================================================
# Управление историей
# ============================================================================


class TestHistory:
    """Тесты управления историей диалогов."""

    def test_get_history_creates_new(self, service):
        """Создаёт историю с системным промптом для нового пользователя."""
        history = service._get_history(user_id=1)

        assert len(history) == 1
        assert history[0].role == MessagesRole.SYSTEM
        assert history[0].content == SYSTEM_PROMPT

    def test_get_history_reuses_existing(self, service):
        """Повторный вызов возвращает ту же историю."""
        h1 = service._get_history(user_id=1)
        h2 = service._get_history(user_id=1)

        assert h1 is h2

    def test_different_users_have_separate_histories(self, service):
        """У разных пользователей — отдельные истории."""
        h1 = service._get_history(user_id=1)
        h2 = service._get_history(user_id=2)

        assert h1 is not h2

    def test_trim_history(self, service):
        """Обрезка истории при превышении лимита (max_history=10)."""
        from gigachat.models import Messages

        history = service._get_history(user_id=1)
        # Добавляем 15 сообщений (1 системный + 15 = 16 всего)
        for i in range(15):
            history.append(
                Messages(role=MessagesRole.USER, content=f"msg-{i}")
            )

        service._trim_history(user_id=1)

        trimmed = service._conversations[1]
        assert len(trimmed) == 10  # max_history
        assert trimmed[0].role == MessagesRole.SYSTEM  # системный промпт сохранён
        assert trimmed[-1].content == "msg-14"  # последнее сообщение на месте

    def test_trim_noop_when_short(self, service):
        """Обрезка ничего не делает, если история короткая."""
        history = service._get_history(user_id=1)
        original_len = len(history)

        service._trim_history(user_id=1)

        assert len(service._conversations[1]) == original_len

    def test_reset_conversation(self, service):
        """Сброс удаляет историю пользователя."""
        service._get_history(user_id=42)
        assert 42 in service._conversations

        service.reset_conversation(user_id=42)
        assert 42 not in service._conversations

    def test_reset_nonexistent_user(self, service):
        """Сброс несуществующего пользователя не падает."""
        service.reset_conversation(user_id=999)  # не должно бросить исключение


# ============================================================================
# process_message
# ============================================================================


class TestProcessMessage:
    """Тесты process_message: основной цикл function calling."""

    async def test_simple_text_response(self, service):
        """GigaChat сразу возвращает текст без вызова функций."""
        with patch.object(
            service._client,
            "chat",
            return_value=_make_text_response("Привет! Чем помочь?"),
        ):
            result = await service.process_message(user_id=1, text="Привет")

        assert result == "Привет! Чем помочь?"

    async def test_function_call_then_text(self, service, mock_mcp_client):
        """GigaChat вызывает функцию, получает результат, отвечает текстом."""
        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": True, "products": [{"name": "Молоко", "price": 79}]}
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_function_call_response(
                    "vkusvill_products_search", {"q": "молоко"}
                )
            else:
                return _make_text_response("Нашёл молоко за 79 руб!")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Найди молоко")

        assert "79" in result
        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search", {"q": "молоко"}
        )

    async def test_function_call_with_string_args(self, service, mock_mcp_client):
        """Аргументы могут прийти как строка JSON."""
        mock_mcp_client.call_tool.return_value = '{"ok": true}'

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_function_call_response(
                    "vkusvill_products_search", '{"q": "сыр"}'
                )
            else:
                return _make_text_response("Вот сыр.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Сыр")

        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search", {"q": "сыр"}
        )

    async def test_mcp_error_handled(self, service, mock_mcp_client):
        """Ошибка MCP не крашит процесс, результат ошибки передаётся в GigaChat."""
        mock_mcp_client.call_tool.side_effect = RuntimeError("MCP down")

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_function_call_response(
                    "vkusvill_products_search", {"q": "хлеб"}
                )
            else:
                return _make_text_response("Извините, сервис недоступен.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Хлеб")

        assert "недоступен" in result

    async def test_gigachat_error(self, service):
        """Ошибка GigaChat возвращает сообщение об ошибке."""
        with patch.object(
            service._client,
            "chat",
            side_effect=Exception("GigaChat API error"),
        ):
            result = await service.process_message(user_id=1, text="Тест")

        assert "ошибка" in result.lower()
        assert "/reset" in result

    async def test_max_tool_calls_limit(self, service, mock_mcp_client):
        """При превышении лимита шагов — ответ о необходимости упростить запрос."""
        mock_mcp_client.call_tool.return_value = '{"ok": true, "data": []}'

        # GigaChat бесконечно вызывает функции
        with patch.object(
            service._client,
            "chat",
            return_value=_make_function_call_response(
                "vkusvill_products_search", {"q": "тест"}
            ),
        ):
            result = await service.process_message(user_id=1, text="Тест")

        assert "слишком много шагов" in result.lower() or "/reset" in result
        assert mock_mcp_client.call_tool.call_count == service._max_tool_calls

    async def test_failed_call_loop_detection(self, service, mock_mcp_client):
        """Если одинаковый tool-вызов провалился 2 раза — прерываем цикл."""
        # MCP возвращает ошибку (ok=false)
        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": False, "error": "invalid_input"}
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                # GigaChat трижды пытается вызвать один и тот же инструмент
                return _make_function_call_response(
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 123}]},
                )
            else:
                return _make_text_response("К сожалению, не удалось создать корзину.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(
                user_id=1, text="Создай корзину"
            )

        assert "не удалось" in result.lower() or "корзин" in result.lower()

    async def test_history_persists_between_messages(self, service):
        """История сохраняется между вызовами process_message."""
        with patch.object(
            service._client,
            "chat",
            return_value=_make_text_response("Ответ 1"),
        ):
            await service.process_message(user_id=1, text="Привет")

        with patch.object(
            service._client,
            "chat",
            return_value=_make_text_response("Ответ 2"),
        ):
            await service.process_message(user_id=1, text="Ещё вопрос")

        history = service._conversations[1]
        # system + user1 + assistant1 + user2 + assistant2 = 5
        assert len(history) == 5
        assert history[1].content == "Привет"
        assert history[3].content == "Ещё вопрос"

    async def test_empty_content_fallback(self, service):
        """Если content пустой — возвращаем fallback-сообщение."""
        with patch.object(
            service._client,
            "chat",
            return_value=_make_text_response(""),
        ):
            result = await service.process_message(user_id=1, text="Тест")

        # Пустой content → "Не удалось получить ответ."
        assert result == "Не удалось получить ответ."


class TestGetFunctions:
    """Тесты _get_functions: загрузка описаний функций для GigaChat."""

    async def test_loads_from_mcp(self, service, mock_mcp_client):
        """Загружает функции из MCP-клиента."""
        functions = await service._get_functions()

        assert len(functions) == 1
        assert functions[0]["name"] == "vkusvill_products_search"
        mock_mcp_client.get_tools.assert_called_once()

    async def test_caches_result(self, service, mock_mcp_client):
        """Повторный вызов не обращается к MCP."""
        await service._get_functions()
        await service._get_functions()

        mock_mcp_client.get_tools.assert_called_once()
