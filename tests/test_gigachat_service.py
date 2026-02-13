"""Тесты GigaChatService.

Тестируем:
- Управление историей диалогов
- Обрезку истории
- LRU-вытеснение диалогов
- Сброс диалога
- Ограничение длины входящего сообщения
- Цикл function calling (process_message) с моками GigaChat и MCP
- Обработку ошибок GigaChat
- Определение зацикливания tool-вызовов
- Лимит шагов
- Закрытие сервиса
- Маршрутизация локальных tool-вызовов (предпочтения)
- Инструмент recipe_ingredients (кеш рецептов)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gigachat.models import (
    Chat,
    MessagesRole,
)

from vkuswill_bot.services.dialog_manager import MAX_CONVERSATIONS
from vkuswill_bot.services.gigachat_service import (
    DEFAULT_GIGACHAT_MAX_CONCURRENT,
    GIGACHAT_MAX_RETRIES,
    GigaChatService,
    MAX_SEARCH_LOG_QUERIES,
    MAX_USER_MESSAGE_LENGTH,
)
from helpers import make_text_response, make_function_call_response


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
    """GigaChatService с замоканным MCP-клиентом (без предпочтений)."""
    svc = GigaChatService(
        credentials="test-creds",
        model="GigaChat",
        scope="GIGACHAT_API_PERS",
        mcp_client=mock_mcp_client,
        max_tool_calls=5,
        max_history=10,
    )
    return svc


@pytest.fixture
def mock_prefs_store() -> AsyncMock:
    """Замоканное хранилище предпочтений."""
    store = AsyncMock()
    store.get_formatted.return_value = json.dumps(
        {"ok": True, "preferences": [], "message": "Нет сохранённых предпочтений."},
        ensure_ascii=False,
    )
    store.set.return_value = json.dumps(
        {"ok": True, "message": "Запомнил: мороженое → пломбир"},
        ensure_ascii=False,
    )
    store.delete.return_value = json.dumps(
        {"ok": True, "message": "Предпочтение «мороженое» удалено."},
        ensure_ascii=False,
    )
    return store


@pytest.fixture
def service_with_prefs(mock_mcp_client, mock_prefs_store) -> GigaChatService:
    """GigaChatService с замоканным MCP-клиентом и хранилищем предпочтений."""
    svc = GigaChatService(
        credentials="test-creds",
        model="GigaChat",
        scope="GIGACHAT_API_PERS",
        mcp_client=mock_mcp_client,
        preferences_store=mock_prefs_store,
        max_tool_calls=5,
        max_history=10,
    )
    return svc


@pytest.fixture
def mock_recipe_store() -> AsyncMock:
    """Замоканное хранилище рецептов."""
    store = AsyncMock()
    store.get.return_value = None  # кеш-промах по умолчанию
    store.save.return_value = None
    return store


@pytest.fixture
def service_with_recipes(mock_mcp_client, mock_recipe_store) -> GigaChatService:
    """GigaChatService с кешем рецептов (без предпочтений)."""
    svc = GigaChatService(
        credentials="test-creds",
        model="GigaChat",
        scope="GIGACHAT_API_PERS",
        mcp_client=mock_mcp_client,
        recipe_store=mock_recipe_store,
        max_tool_calls=5,
        max_history=10,
    )
    return svc


@pytest.fixture
def service_with_all(
    mock_mcp_client,
    mock_prefs_store,
    mock_recipe_store,
) -> GigaChatService:
    """GigaChatService со всеми хранилищами."""
    svc = GigaChatService(
        credentials="test-creds",
        model="GigaChat",
        scope="GIGACHAT_API_PERS",
        mcp_client=mock_mcp_client,
        preferences_store=mock_prefs_store,
        recipe_store=mock_recipe_store,
        max_tool_calls=5,
        max_history=10,
    )
    return svc


# ============================================================================
# Ограничение длины сообщения
# ============================================================================


class TestMessageTruncation:
    """Тесты ограничения длины входящего сообщения."""

    async def test_message_truncation(self, service):
        """Сообщение длиннее MAX_USER_MESSAGE_LENGTH обрезается."""
        long_text = "А" * (MAX_USER_MESSAGE_LENGTH + 1000)

        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            await service.process_message(user_id=1, text=long_text)

        # Проверяем, что в историю попало обрезанное сообщение
        history = service._conversations[1]
        user_msg = next(m for m in history if m.role == MessagesRole.USER)
        assert len(user_msg.content) == MAX_USER_MESSAGE_LENGTH

    async def test_short_message_not_truncated(self, service):
        """Короткое сообщение не обрезается."""
        text = "Привет"

        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ"),
        ):
            await service.process_message(user_id=1, text=text)

        history = service._conversations[1]
        user_msg = next(m for m in history if m.role == MessagesRole.USER)
        assert user_msg.content == text


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
            return_value=make_text_response("Привет! Чем помочь?"),
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
                return make_function_call_response("vkusvill_products_search", {"q": "молоко"})
            else:
                return make_text_response("Нашёл молоко за 79 руб!")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Найди молоко")

        assert "79" in result
        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search", {"q": "молоко", "limit": 5}
        )

    async def test_function_call_with_string_args(self, service, mock_mcp_client):
        """Аргументы могут прийти как строка JSON."""
        mock_mcp_client.call_tool.return_value = '{"ok": true}'

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response("vkusvill_products_search", '{"q": "сыр"}')
            else:
                return make_text_response("Вот сыр.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            await service.process_message(user_id=1, text="Сыр")

        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search", {"q": "сыр", "limit": 5}
        )

    async def test_mcp_error_handled(self, service, mock_mcp_client):
        """Ошибка MCP не крашит процесс, результат ошибки передаётся в GigaChat."""
        mock_mcp_client.call_tool.side_effect = RuntimeError("MCP down")

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response("vkusvill_products_search", {"q": "хлеб"})
            else:
                return make_text_response("Извините, сервис недоступен.")

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

        # GigaChat бесконечно вызывает функции с одинаковыми аргументами
        with patch.object(
            service._client,
            "chat",
            return_value=make_function_call_response("vkusvill_products_search", {"q": "тест"}),
        ):
            result = await service.process_message(user_id=1, text="Тест")

        assert "слишком много шагов" in result.lower() or "/reset" in result
        # MCP вызывается только 1 раз — повторные с теми же аргументами
        # перехватываются детектором зацикливания
        assert mock_mcp_client.call_tool.call_count == 1

    async def test_failed_call_loop_detection(self, service, mock_mcp_client):
        """Если одинаковый tool-вызов провалился 2 раза — прерываем цикл."""
        # MCP возвращает ошибку (ok=false)
        mock_mcp_client.call_tool.return_value = json.dumps({"ok": False, "error": "invalid_input"})

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                # GigaChat трижды пытается вызвать один и тот же инструмент
                return make_function_call_response(
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 123}]},
                )
            else:
                return make_text_response("К сожалению, не удалось создать корзину.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Создай корзину")

        assert "не удалось" in result.lower() or "корзин" in result.lower()

    async def test_successful_call_loop_detection(self, service, mock_mcp_client):
        """Детектор перехватывает повторные успешные вызовы с теми же аргументами."""
        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": True, "data": {"items": [{"xml_id": 1, "name": "Тест"}]}}
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                # GigaChat 4 раза вызывает один и тот же поиск
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "огурцы", "sort": "rating"}
                )
            else:
                return make_text_response("Вот ваши огурцы!")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Найди огурцы")

        assert "огурцы" in result.lower()
        # MCP вызван только 1 раз — остальные перехвачены детектором
        assert mock_mcp_client.call_tool.call_count == 1

    async def test_history_persists_between_messages(self, service):
        """История сохраняется между вызовами process_message."""
        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ 1"),
        ):
            await service.process_message(user_id=1, text="Привет")

        with patch.object(
            service._client,
            "chat",
            return_value=make_text_response("Ответ 2"),
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
            return_value=make_text_response(""),
        ):
            result = await service.process_message(user_id=1, text="Тест")

        # Пустой content → "Не удалось получить ответ."
        assert result == "Не удалось получить ответ."

    async def test_total_steps_safety_limit(self, service, mock_mcp_client):
        """total_steps safety limit (max_total_steps) прерывает цикл."""
        # Каждый вызов — РАЗНЫЕ аргументы, чтобы duplicate detection не срабатывал.
        # Используем текстовые запросы без цифр (clean_search_query удаляет числа).
        # Но max_tool_calls=5, max_total_steps=10, так что 10 шагов — предел.
        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": True, "data": {"items": []}},
        )

        products = ["молоко", "хлеб", "сыр", "масло", "яйца", "кефир", "сметана"]
        step = 0

        def mock_chat(chat: Chat):
            nonlocal step
            step += 1
            q = products[step % len(products)]
            return make_function_call_response("vkusvill_products_search", {"q": q})

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Тест")

        # Должен упереться в max_tool_calls (5 real calls)
        assert "/reset" in result or "слишком много" in result.lower()
        # Ровно 5 реальных вызовов MCP (все уникальные)
        assert mock_mcp_client.call_tool.call_count == 5

    async def test_real_calls_vs_total_steps_different(self, service, mock_mcp_client):
        """real_calls не увеличиваются для дубликатов, но total_steps — да."""
        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": True, "data": {"items": [{"xml_id": 1}]}},
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count <= 6:
                # Все вызовы с одними и теми же аргументами
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "молоко"},
                )
            return make_text_response("Готово!")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Молоко")

        # Только 1 реальный вызов MCP — остальные дубликаты
        assert mock_mcp_client.call_tool.call_count == 1
        assert "Готово!" in result or "/reset" in result

    async def test_call_results_cached_for_duplicates(self, service, mock_mcp_client):
        """Закешированный результат вставляется в историю при дублировании."""
        original_result = json.dumps(
            {
                "ok": True,
                "data": {"items": [{"xml_id": 42, "name": "Кефир"}]},
            }
        )
        mock_mcp_client.call_tool.return_value = original_result

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "кефир"},
                )
            return make_text_response("Готово!")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            await service.process_message(user_id=1, text="Кефир")

        # Проверяем, что в истории FUNCTION-сообщение с результатом (не ошибкой)
        history = service._conversations[1]
        func_msgs = [m for m in history if m.role == MessagesRole.FUNCTION]
        assert len(func_msgs) >= 2  # минимум: реальный + кешированный дубль
        for fm in func_msgs:
            content = json.loads(fm.content)
            assert content["ok"] is True  # все содержат ok=True


# ============================================================================
# Закрытие сервиса
# ============================================================================


class TestClose:
    """Тесты закрытия GigaChatService."""

    async def test_close_success(self, service):
        """Успешное закрытие клиента."""
        with patch.object(service._client, "close"):
            await service.close()
        # close() был вызван (через asyncio.to_thread)

    async def test_close_with_error(self, service):
        """Ошибка при закрытии логируется, не бросается."""
        with patch.object(service._client, "close", side_effect=RuntimeError("close error")):
            # Не должно бросить исключение
            await service.close()


class TestGetFunctions:
    """Тесты _get_functions: загрузка описаний функций для GigaChat."""

    async def test_loads_from_mcp(self, service, mock_mcp_client):
        """Загружает функции из MCP-клиента + get_previous_cart."""
        functions = await service._get_functions()
        names = [f["name"] for f in functions]

        assert "vkusvill_products_search" in names
        assert "get_previous_cart" in names
        mock_mcp_client.get_tools.assert_called_once()

    async def test_caches_result(self, service, mock_mcp_client):
        """Повторный вызов не обращается к MCP."""
        await service._get_functions()
        await service._get_functions()

        mock_mcp_client.get_tools.assert_called_once()


class TestGetFunctionsWithPrefs:
    """Тесты _get_functions с локальными инструментами предпочтений."""

    async def test_includes_local_tools(self, service_with_prefs):
        """При наличии prefs_store добавляются локальные инструменты."""
        functions = await service_with_prefs._get_functions()
        names = [f["name"] for f in functions]
        assert "user_preferences_get" in names
        assert "user_preferences_set" in names
        assert "user_preferences_delete" in names

    async def test_excludes_local_tools_without_store(self, service):
        """Без prefs_store локальных инструментов нет."""
        functions = await service._get_functions()
        names = [f["name"] for f in functions]
        assert "user_preferences_get" not in names
        assert "user_preferences_set" not in names
        assert "user_preferences_delete" not in names


class TestSearchTrimCacheCartFlow:
    """Тесты полного flow: поиск → кеш цен → обрезка → корзина → расчёт."""

    async def test_search_then_cart_full_flow(self, service, mock_mcp_client):
        """Полный цикл: поиск кеширует цены, корзина рассчитывает стоимость."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "xml_id": 41728,
                            "name": "Молоко 3,2%",
                            "price": {"current": 79, "currency": "RUB"},
                            "unit": "шт",
                            "weight": "930 мл",
                            "rating": 4.8,
                            "description": "Длинное описание...",
                            "images": ["img.jpg"],
                        },
                    ]
                },
            }
        )
        cart_result = json.dumps(
            {
                "ok": True,
                "data": {"link": "https://vkusvill.ru/?share_basket=123"},
            }
        )

        mock_mcp_client.call_tool.side_effect = [search_result, cart_result]

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "молоко"},
                )
            elif call_count == 2:
                return make_function_call_response(
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 41728, "q": 2}]},
                )
            else:
                return make_text_response("Корзина с молоком готова! 158 руб.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Купить молоко")

        # Кеш цен обновился после поиска (через search_processor)
        assert 41728 in service._search_processor.price_cache
        assert service._search_processor.price_cache[41728]["price"] == 79
        # Корзина рассчитана
        assert "Корзина" in result or "158" in result or "молоко" in result.lower()

    async def test_function_call_with_invalid_string_args(self, service, mock_mcp_client):
        """GigaChat отправляет невалидную JSON-строку как аргументы.

        GigaChat SDK (Pydantic) валидирует arguments как dict, но код
        defensively обрабатывает строки. Используем MagicMock для обхода
        валидации SDK.
        """
        mock_mcp_client.call_tool.return_value = '{"ok": true}'

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Создаём мок ответа с невалидной строкой arguments
                resp = MagicMock()
                choice = MagicMock()
                msg = MagicMock()
                msg.content = ""
                msg.function_call = MagicMock()
                msg.function_call.name = "vkusvill_products_search"
                msg.function_call.arguments = '{"invalid json'
                msg.functions_state_id = None
                choice.message = msg
                resp.choices = [choice]
                return resp
            return make_text_response("Ответ")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Тест")

        assert isinstance(result, str)
        # Вызов MCP произошёл с пустым dict + limit (fallback при невалидном JSON)
        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search",
            {"limit": 5},
        )

    async def test_function_call_with_non_str_non_dict_args(self, service, mock_mcp_client):
        """GigaChat отправляет аргументы неожиданного типа (list, int и т.д.)."""
        mock_mcp_client.call_tool.return_value = '{"ok": true}'

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Создаём ответ с arguments типа int (не str, не dict)
                resp = make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "test"},
                )
                # Подменяем arguments на non-dict, non-str
                resp.choices[0].message.function_call.arguments = 12345
                return resp
            return make_text_response("Ответ")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Тест")

        assert isinstance(result, str)
        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search",
            {"limit": 5},
        )

    async def test_functions_state_id_preserved(self, service, mock_mcp_client):
        """functions_state_id из ответа GigaChat сохраняется в истории."""
        mock_mcp_client.call_tool.return_value = '{"ok": true}'

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "тест"},
                )
                # Добавляем functions_state_id к сообщению
                resp.choices[0].message.functions_state_id = "state-abc-123"
                return resp
            return make_text_response("Ответ")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            await service.process_message(user_id=1, text="Тест")

        # Проверяем, что functions_state_id попал в историю
        history = service._conversations[1]
        assistant_msgs = [m for m in history if m.role == MessagesRole.ASSISTANT]
        assert any(
            getattr(m, "functions_state_id", None) == "state-abc-123" for m in assistant_msgs
        )


class TestProcessMessageWithPrefs:
    """Тесты process_message с маршрутизацией предпочтений."""

    async def test_preferences_get_routed_locally(
        self,
        service_with_prefs,
        mock_mcp_client,
        mock_prefs_store,
    ):
        """user_preferences_get маршрутизируется локально, не через MCP."""
        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "user_preferences_get",
                    {},
                )
            else:
                return make_text_response("У вас нет предпочтений.")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            await service_with_prefs.process_message(
                user_id=42,
                text="Мои предпочтения",
            )

        # Вызвано хранилище, а не MCP
        mock_prefs_store.get_formatted.assert_called_once_with(42)
        mock_mcp_client.call_tool.assert_not_called()

    async def test_preferences_set_routed_locally(
        self,
        service_with_prefs,
        mock_mcp_client,
        mock_prefs_store,
    ):
        """user_preferences_set маршрутизируется локально."""
        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "user_preferences_set",
                    {"category": "мороженое", "preference": "пломбир в шоколаде"},
                )
            else:
                return make_text_response("Запомнил!")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            await service_with_prefs.process_message(
                user_id=42,
                text="Запомни, я люблю пломбир в шоколаде",
            )

        mock_prefs_store.set.assert_called_once_with(
            42,
            "мороженое",
            "пломбир в шоколаде",
        )
        mock_mcp_client.call_tool.assert_not_called()

    async def test_mcp_tool_still_goes_through_mcp(
        self,
        service_with_prefs,
        mock_mcp_client,
    ):
        """MCP-инструменты по-прежнему маршрутизируются через MCP."""
        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": True, "data": {"items": []}},
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "молоко"},
                )
            else:
                return make_text_response("Ничего не нашёл.")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            await service_with_prefs.process_message(
                user_id=42,
                text="Найди молоко",
            )

        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search",
            {"q": "молоко", "limit": 5},
        )

    async def test_preferences_enhance_search_query(
        self,
        service_with_prefs,
        mock_mcp_client,
        mock_prefs_store,
    ):
        """После загрузки предпочтений поисковый запрос обогащается."""
        # Предпочтения: вареники -> с картофелем и шкварками
        mock_prefs_store.get_formatted.return_value = json.dumps(
            {
                "ok": True,
                "preferences": [
                    {"category": "вареники", "preference": "с картофелем и шкварками"},
                    {"category": "молоко", "preference": "Молоко безлактозное 2,5%, 900 мл"},
                ],
            }
        )

        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": True, "data": {"meta": {"q": "test"}, "items": []}},
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Шаг 1: загрузка предпочтений
                return make_function_call_response(
                    "user_preferences_get",
                    {},
                )
            elif call_count == 2:
                # Шаг 2: поиск вареников
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "вареники"},
                )
            elif call_count == 3:
                # Шаг 3: поиск молока
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "молоко"},
                )
            else:
                return make_text_response("Готово!")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            await service_with_prefs.process_message(
                user_id=42,
                text="Закажи вареники и молоко",
            )

        # Проверяем, что MCP получил обогащённые запросы
        calls = mock_mcp_client.call_tool.call_args_list
        assert len(calls) == 2

        # Вареники: "вареники" → "вареники с картофелем и шкварками"
        assert calls[0].args[0] == "vkusvill_products_search"
        assert calls[0].args[1]["q"] == "вареники с картофелем и шкварками"

        # Молоко: "молоко" → "Молоко безлактозное 2,5%, 900 мл"
        # (предпочтение уже содержит "молоко")
        assert calls[1].args[0] == "vkusvill_products_search"
        assert calls[1].args[1]["q"] == "Молоко безлактозное 2,5%, 900 мл"

    async def test_no_preferences_no_enhancement(
        self,
        service_with_prefs,
        mock_mcp_client,
        mock_prefs_store,
    ):
        """Если предпочтений нет — поиск идёт без изменений."""
        mock_prefs_store.get_formatted.return_value = json.dumps(
            {
                "ok": True,
                "preferences": [],
            }
        )

        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": True, "data": {"meta": {"q": "test"}, "items": []}},
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "user_preferences_get",
                    {},
                )
            elif call_count == 2:
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "творог"},
                )
            else:
                return make_text_response("Готово!")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            await service_with_prefs.process_message(
                user_id=42,
                text="Закажи творог",
            )

        calls = mock_mcp_client.call_tool.call_args_list
        assert len(calls) == 1
        # Запрос не изменён — нет предпочтений для творога
        assert calls[0].args[1]["q"] == "творог"


# ============================================================================
# recipe_ingredients: _get_functions
# ============================================================================


class TestGetFunctionsWithRecipes:
    """Тесты добавления recipe_ingredients в функции."""

    async def test_includes_recipe_tool(self, service_with_recipes):
        """При наличии recipe_store добавляется recipe_ingredients."""
        functions = await service_with_recipes._get_functions()
        names = [f["name"] for f in functions]
        assert "recipe_ingredients" in names

    async def test_excludes_recipe_tool_without_store(self, service):
        """Без recipe_store инструмента recipe_ingredients нет."""
        functions = await service._get_functions()
        names = [f["name"] for f in functions]
        assert "recipe_ingredients" not in names

    async def test_includes_both_recipes_and_prefs(self, service_with_all):
        """С обоими хранилищами — оба набора инструментов."""
        functions = await service_with_all._get_functions()
        names = [f["name"] for f in functions]
        assert "recipe_ingredients" in names
        assert "user_preferences_get" in names


# ============================================================================
# recipe_ingredients: _handle_recipe_ingredients
# ============================================================================


class TestHandleRecipeIngredients:
    """Тесты обработки recipe_ingredients."""

    async def test_no_store_returns_error(self, service):
        """Без recipe_store возвращает ошибку."""
        result = await service._handle_recipe_ingredients({"dish": "борщ"})
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "не настроен" in parsed["error"]

    async def test_empty_dish_returns_error(self, service_with_recipes):
        """Пустое название блюда — ошибка."""
        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": ""},
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "Не указано" in parsed["error"]

    async def test_cache_hit(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Кеш-попадание — возвращает из кеша без LLM."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [
                {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свёкла"},
            ],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": 4},
        )
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert parsed["cached"] is True
        assert len(parsed["ingredients"]) == 1
        assert parsed["ingredients"][0]["name"] == "свёкла"

    async def test_cache_hit_with_scaling(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Кеш-попадание с другим числом порций — масштабирует."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [
                {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свёкла"},
            ],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": 8},
        )
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert parsed["cached"] is True
        assert parsed["ingredients"][0]["quantity"] == 1.0  # 0.5 * 8/4

    async def test_cache_miss_calls_llm(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Кеш-промах — вызывает GigaChat для извлечения рецепта."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps(
            [
                {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свёкла"},
            ],
            ensure_ascii=False,
        )

        with patch.object(
            service_with_recipes._client,
            "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ", "servings": 4},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["cached"] is False
        assert len(parsed["ingredients"]) == 1

        # Проверяем, что рецепт был закеширован
        mock_recipe_store.save.assert_called_once()

    async def test_llm_error_returns_fallback(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Ошибка LLM — возвращает ошибку с инструкцией для GigaChat."""
        mock_recipe_store.get.return_value = None

        with patch.object(
            service_with_recipes._client,
            "chat",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ"},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "самостоятельно" in parsed["error"]

    async def test_default_servings(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Без параметра servings используется 2 по умолчанию."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 2,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ"},
        )
        parsed = json.loads(result)

        assert parsed["servings"] == 2
        assert parsed["ok"] is True

    async def test_invalid_servings_defaults_to_2(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Некорректный servings заменяется на 2."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 2,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": -1},
        )
        parsed = json.loads(result)
        assert parsed["servings"] == 2

    async def test_cache_miss_with_markdown_response(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """GigaChat возвращает JSON в markdown-обёртке — парсится корректно."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[
            0
        ].message.content = '```json\n[{"name": "свёкла", "quantity": 0.5}]\n```'

        with patch.object(
            service_with_recipes._client,
            "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ"},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["ingredients"][0]["name"] == "свёкла"

    async def test_hint_in_result(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Результат содержит hint для GigaChat с инструкцией по kg_equivalent."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла"}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ"},
        )
        parsed = json.loads(result)
        assert "hint" in parsed
        assert "vkusvill_products_search" in parsed["hint"]
        assert "kg_equivalent" in parsed["hint"]

    async def test_cache_hit_enriched_with_kg(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Кеш-попадание — ингредиенты в шт обогащаются kg_equivalent."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [
                {"name": "картофель", "quantity": 4, "unit": "шт", "search_query": "картофель"},
                {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свёкла"},
            ],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": 4},
        )
        parsed = json.loads(result)

        # Картофель (шт) — обогащён
        assert parsed["ingredients"][0].get("kg_equivalent") == 0.6  # 4 * 0.15
        # Свёкла (кг) — не обогащается
        assert "kg_equivalent" not in parsed["ingredients"][1]

    async def test_llm_result_enriched_with_kg(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Кеш-промах — LLM-результат тоже обогащается kg_equivalent."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps(
            [
                {"name": "лук репчатый", "quantity": 2, "unit": "шт", "search_query": "лук"},
                {"name": "говядина", "quantity": 0.8, "unit": "кг", "search_query": "говядина"},
            ],
            ensure_ascii=False,
        )

        with patch.object(
            service_with_recipes._client,
            "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "азу", "servings": 4},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is True
        # Лук (шт) — обогащён
        assert parsed["ingredients"][0].get("kg_equivalent") == 0.2  # 2 * 0.1
        # Говядина (кг) — не обогащается
        assert "kg_equivalent" not in parsed["ingredients"][1]


# ============================================================================
# recipe_ingredients: маршрутизация через process_message
# ============================================================================


class TestRecipeToolRouting:
    """Тесты маршрутизации recipe_ingredients в process_message."""

    async def test_recipe_routed_locally(
        self,
        service_with_recipes,
        mock_recipe_store,
        mock_mcp_client,
    ):
        """recipe_ingredients маршрутизируется через RecipeService, не через MCP."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла"}],
        }

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "recipe_ingredients",
                    {"dish": "борщ", "servings": 4},
                )
            return make_text_response("Вот рецепт борща!")

        with patch.object(service_with_recipes._client, "chat", side_effect=mock_chat):
            result = await service_with_recipes.process_message(
                user_id=42,
                text="Какие ингредиенты для борща?",
            )

        assert isinstance(result, str)
        mock_recipe_store.get.assert_called_once()
        mock_mcp_client.call_tool.assert_not_called()


# ============================================================================
# recipe_ingredients: дополнительные edge-cases
# ============================================================================


class TestHandleRecipeIngredientsEdgeCases:
    """Дополнительные тесты _handle_recipe_ingredients для покрытия."""

    async def test_cache_save_failure_handled(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """Ошибка при сохранении в кеш не крашит — результат возвращается (lines 440-441)."""
        mock_recipe_store.get.return_value = None
        mock_recipe_store.save.side_effect = RuntimeError("DB write error")

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps(
            [
                {"name": "мясо", "quantity": 1, "unit": "кг", "search_query": "говядина"},
            ],
            ensure_ascii=False,
        )

        with patch.object(
            service_with_recipes._client,
            "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "азу", "servings": 4},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["cached"] is False
        assert len(parsed["ingredients"]) == 1
        # save был вызван, но ошибка перехвачена
        mock_recipe_store.save.assert_called_once()

    async def test_llm_returns_empty_list(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """LLM вернул пустой массив — ошибка (line 486)."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = "[]"

        with patch.object(
            service_with_recipes._client,
            "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ"},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "самостоятельно" in parsed["error"]

    async def test_llm_returns_dict_instead_of_list(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """LLM вернул dict вместо list — ошибка (line 486)."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = '{"error": "bad request"}'

        with patch.object(
            service_with_recipes._client,
            "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ"},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_servings_string_defaults_to_2(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """servings="два" (строка) → заменяется на 2."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 2,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": "два"},
        )
        parsed = json.loads(result)
        assert parsed["servings"] == 2

    async def test_servings_zero_defaults_to_2(
        self,
        service_with_recipes,
        mock_recipe_store,
    ):
        """servings=0 → заменяется на 2."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 2,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": 0},
        )
        parsed = json.loads(result)
        assert parsed["servings"] == 2

    async def test_dish_with_whitespace_only(
        self,
        service_with_recipes,
    ):
        """dish=" " → ошибка (пустое после strip)."""
        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "   "},
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_recipe_integration_through_process_message(
        self,
        service_with_recipes,
        mock_mcp_client,
        mock_recipe_store,
    ):
        """Интеграционный тест: recipe_ingredients через process_message."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [
                {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свёкла"},
                {"name": "капуста", "quantity": 0.3, "unit": "кг", "search_query": "капуста"},
            ],
        }

        mock_mcp_client.call_tool.return_value = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {"xml_id": 1, "name": "Свёкла", "price": {"current": 50}, "unit": "кг"}
                    ]
                },
            }
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "recipe_ingredients",
                    {"dish": "борщ", "servings": 4},
                )
            elif call_count == 2:
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "свёкла"},
                )
            elif call_count == 3:
                return make_function_call_response(
                    "vkusvill_products_search",
                    {"q": "капуста"},
                )
            else:
                return make_text_response("Вот ваш борщ!")

        with patch.object(service_with_recipes._client, "chat", side_effect=mock_chat):
            result = await service_with_recipes.process_message(
                user_id=1,
                text="Собери продукты для борща",
            )

        assert isinstance(result, str)
        assert len(result) > 0
        # recipe_ingredients маршрутизирован локально
        mock_recipe_store.get.assert_called_once()
        # MCP вызван для поиска (дважды — свёкла и капуста)
        assert mock_mcp_client.call_tool.call_count == 2


# ============================================================================
# _is_rate_limit_error: определение rate limit ошибок
# ============================================================================


class TestIsRateLimitError:
    """Тесты _is_rate_limit_error: определение 429 / rate limit ошибок."""

    def test_429_in_message(self):
        """Сообщение содержит '429' → rate limit."""
        exc = RuntimeError("HTTP 429 Too Many Requests")
        assert GigaChatService._is_rate_limit_error(exc) is True

    def test_rate_limit_in_message(self):
        """Сообщение содержит 'rate limit' → rate limit."""
        exc = RuntimeError("Rate limit exceeded")
        assert GigaChatService._is_rate_limit_error(exc) is True

    def test_too_many_in_message(self):
        """Сообщение содержит 'too many' → rate limit."""
        exc = RuntimeError("Too many requests, please slow down")
        assert GigaChatService._is_rate_limit_error(exc) is True

    def test_case_insensitive(self):
        """Проверка регистронезависимая."""
        exc = RuntimeError("RATE LIMIT")
        assert GigaChatService._is_rate_limit_error(exc) is True

    def test_not_rate_limit(self):
        """Обычная ошибка — не rate limit."""
        exc = RuntimeError("Connection refused")
        assert GigaChatService._is_rate_limit_error(exc) is False

    def test_empty_message(self):
        """Пустое сообщение — не rate limit."""
        exc = RuntimeError("")
        assert GigaChatService._is_rate_limit_error(exc) is False

    def test_timeout_not_rate_limit(self):
        """Таймаут — не rate limit."""
        exc = TimeoutError("Read timed out")
        assert GigaChatService._is_rate_limit_error(exc) is False

    def test_value_error(self):
        """ValueError — не rate limit."""
        exc = ValueError("Invalid argument")
        assert GigaChatService._is_rate_limit_error(exc) is False

    def test_httpx_429_status_code(self):
        """Исключение с response.status_code=429 → rate limit."""
        exc = RuntimeError("HTTP error")
        exc.response = MagicMock(status_code=429)
        assert GigaChatService._is_rate_limit_error(exc) is True

    def test_httpx_500_status_code(self):
        """Исключение с response.status_code=500 → не rate limit."""
        exc = RuntimeError("HTTP error")
        exc.response = MagicMock(status_code=500)
        assert GigaChatService._is_rate_limit_error(exc) is False


# ============================================================================
# _call_gigachat: семафор и retry при rate limit
# ============================================================================


class TestCallGigachat:
    """Тесты _call_gigachat: семафор, retry при 429, ограничение параллелизма."""

    async def test_successful_call(self, service):
        """Успешный вызов возвращает ответ GigaChat."""
        expected = make_text_response("Привет!")
        with patch.object(
            service._client,
            "chat",
            return_value=expected,
        ):
            result = await service._call_gigachat(
                history=[],
                functions=[],
            )
        assert result is expected

    async def test_retry_on_rate_limit(self, service):
        """Retry при получении rate limit (429)."""
        expected = make_text_response("Ответ после retry")
        rate_limit_error = RuntimeError("HTTP 429 Too Many Requests")

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise rate_limit_error
            return expected

        with (
            patch.object(service._client, "chat", side_effect=mock_chat),
            patch("vkuswill_bot.services.gigachat_service.asyncio.sleep"),
        ):
            result = await service._call_gigachat(
                history=[],
                functions=[],
            )

        assert result is expected
        assert call_count == 2

    async def test_raises_after_max_retries(self, service):
        """Бросает исключение после исчерпания retry."""
        rate_limit_error = RuntimeError("HTTP 429 Too Many Requests")

        with (
            patch.object(service._client, "chat", side_effect=rate_limit_error),
            patch("vkuswill_bot.services.gigachat_service.asyncio.sleep"),
            pytest.raises(RuntimeError, match="429"),
        ):
            await service._call_gigachat(
                history=[],
                functions=[],
            )

    async def test_non_rate_limit_error_not_retried(self, service):
        """Обычная ошибка (не rate limit) не повторяется."""
        error = RuntimeError("Connection refused")

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            raise error

        with (
            patch.object(service._client, "chat", side_effect=mock_chat),
            pytest.raises(RuntimeError, match="Connection refused"),
        ):
            await service._call_gigachat(history=[], functions=[])

        # Только 1 вызов — retry не было
        assert call_count == 1

    async def test_exponential_backoff_delays(self, service):
        """Retry использует exponential backoff (1s, 2s, 4s, 8s)."""
        rate_limit_error = RuntimeError("429")
        sleep_calls = []

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count < GIGACHAT_MAX_RETRIES:
                raise rate_limit_error
            return make_text_response("OK")

        with (
            patch.object(service._client, "chat", side_effect=mock_chat),
            patch(
                "vkuswill_bot.services.gigachat_service.asyncio.sleep",
                side_effect=mock_sleep,
            ),
        ):
            await service._call_gigachat(
                history=[],
                functions=[],
            )

        # delay = 2 ** attempt: attempt=0 → 1, attempt=1 → 2, attempt=2 → 4, attempt=3 → 8
        assert sleep_calls == [1, 2, 4, 8]

    async def test_semaphore_limits_concurrency(self, mock_mcp_client):
        """Семафор ограничивает параллельные вызовы."""
        svc = GigaChatService(
            credentials="test-creds",
            model="GigaChat",
            scope="GIGACHAT_API_PERS",
            mcp_client=mock_mcp_client,
            gigachat_max_concurrent=2,  # лимит 2
        )

        # Проверяем, что семафор создан с правильным лимитом
        assert svc._api_semaphore._value == 2

    async def test_default_max_concurrent(self, mock_mcp_client):
        """По умолчанию gigachat_max_concurrent = DEFAULT_GIGACHAT_MAX_CONCURRENT."""
        svc = GigaChatService(
            credentials="test-creds",
            model="GigaChat",
            scope="GIGACHAT_API_PERS",
            mcp_client=mock_mcp_client,
        )
        assert svc._api_semaphore._value == DEFAULT_GIGACHAT_MAX_CONCURRENT


# ============================================================================
# Константы модуля
# ============================================================================


class TestModuleConstants:
    """Тесты констант модуля gigachat_service."""

    def test_max_retries_value(self):
        """GIGACHAT_MAX_RETRIES имеет разумное значение."""
        assert 1 <= GIGACHAT_MAX_RETRIES <= 10

    def test_max_conversations_value(self):
        """MAX_CONVERSATIONS имеет значение 1000."""
        assert MAX_CONVERSATIONS == 1000

    def test_max_user_message_length_value(self):
        """MAX_USER_MESSAGE_LENGTH = 4096."""
        assert MAX_USER_MESSAGE_LENGTH == 4096

    def test_default_gigachat_max_concurrent(self):
        """DEFAULT_GIGACHAT_MAX_CONCURRENT = 15."""
        assert DEFAULT_GIGACHAT_MAX_CONCURRENT == 15


# ============================================================================
# Session-level search_log (Phase A)
# ============================================================================


class TestSessionSearchLog:
    """Тесты персистентного search_log на уровне сессии.

    search_log накапливается между сообщениями пользователя
    и очищается при /reset.
    """

    @pytest.fixture
    def service(self, mock_mcp_client):
        """GigaChatService для тестов search_log."""
        return GigaChatService(
            credentials="test-creds",
            model="GigaChat",
            scope="GIGACHAT_API_PERS",
            mcp_client=mock_mcp_client,
        )

    def test_get_search_log_empty_by_default(self, service):
        """Новый пользователь — пустой search_log."""
        log = service._get_search_log(user_id=42)
        assert log == {}

    def test_save_and_get_search_log(self, service):
        """Сохранение и загрузка search_log."""
        log = {"молоко": {100, 200}, "хлеб": {300}}
        service._save_search_log(user_id=42, search_log=log)

        loaded = service._get_search_log(user_id=42)
        assert loaded == log
        assert loaded["молоко"] == {100, 200}

    def test_search_log_persists_across_calls(self, service):
        """search_log сохраняется между вызовами."""
        service._save_search_log(42, {"q1": {1, 2}})
        service._save_search_log(42, {"q1": {1, 2}, "q2": {3}})

        log = service._get_search_log(42)
        assert "q1" in log
        assert "q2" in log

    def test_search_log_isolates_users(self, service):
        """search_log не пересекается между пользователями."""
        service._save_search_log(1, {"a": {10}})
        service._save_search_log(2, {"b": {20}})

        assert service._get_search_log(1) == {"a": {10}}
        assert service._get_search_log(2) == {"b": {20}}

    async def test_reset_clears_search_log(self, service):
        """reset_conversation очищает search_log пользователя."""
        service._save_search_log(42, {"q": {1}})
        assert service._get_search_log(42) != {}

        await service.reset_conversation(42)
        assert service._get_search_log(42) == {}

    def test_search_log_size_limit(self, service):
        """search_log обрезается при превышении MAX_SEARCH_LOG_QUERIES."""
        big_log = {f"query_{i}": {i} for i in range(MAX_SEARCH_LOG_QUERIES + 50)}
        service._save_search_log(42, big_log)

        saved = service._get_search_log(42)
        assert len(saved) <= MAX_SEARCH_LOG_QUERIES

    def test_max_search_log_queries_value(self):
        """MAX_SEARCH_LOG_QUERIES имеет разумное значение."""
        assert 10 <= MAX_SEARCH_LOG_QUERIES <= 500


class TestGetPreviousCartTool:
    """Тесты: get_previous_cart включён в функции GigaChat."""

    async def test_always_included(self, service):
        """get_previous_cart всегда в списке функций."""
        functions = await service._get_functions()
        names = [f["name"] for f in functions]
        assert "get_previous_cart" in names


# ============================================================================
# _extract_usage: structured logging + precached_prompt_tokens
# ============================================================================


class TestExtractUsage:
    """Тесты _extract_usage — извлечение usage и cost из ответа GigaChat."""

    def test_basic_usage(self, service):
        """Извлекает prompt/completion/total tokens и рассчитывает cost."""
        response = make_text_response("Привет")

        usage, cost = service._extract_usage(response)

        assert usage is not None
        assert usage["input"] == 10
        assert usage["output"] == 5
        assert usage["total"] == 15
        # cost_details: GigaChat = 65₽/1M, без precached
        assert cost is not None
        price = 65 / 1_000_000
        assert cost["input"] == pytest.approx(10 * price)
        assert cost["output"] == pytest.approx(5 * price)
        assert cost["total"] == pytest.approx(15 * price)

    def test_none_usage(self, service):
        """Возвращает (None, None) если usage отсутствует."""
        response = make_text_response("Привет")
        response.usage = None

        usage, cost = service._extract_usage(response)

        assert usage is None
        assert cost is None

    def test_partial_usage(self, service):
        """Работает с частичным usage (только prompt_tokens)."""
        response = make_text_response("Привет")
        response.usage = MagicMock()
        response.usage.prompt_tokens = 50
        response.usage.completion_tokens = None
        response.usage.total_tokens = None

        usage, cost = service._extract_usage(response)

        assert usage == {"input": 50}
        # cost рассчитывается
        assert cost is not None
        price = 65 / 1_000_000
        assert cost["input"] == pytest.approx(50 * price)
        assert cost["output"] == pytest.approx(0)

    def test_non_int_values_ignored(self, service):
        """Нецелочисленные значения игнорируются."""
        response = make_text_response("Привет")
        response.usage = MagicMock()
        response.usage.prompt_tokens = "not_a_number"
        response.usage.completion_tokens = 5
        response.usage.total_tokens = None

        usage, _cost = service._extract_usage(response)

        assert usage == {"output": 5}

    def test_all_none_returns_none(self, service):
        """Если все значения None — возвращает (None, None)."""
        response = make_text_response("Привет")
        response.usage = MagicMock()
        response.usage.prompt_tokens = None
        response.usage.completion_tokens = None
        response.usage.total_tokens = None

        usage, cost = service._extract_usage(response)

        assert usage is None
        assert cost is None

    def test_precached_tokens_reduce_cost(self, service, caplog):
        """precached_tokens вычитаются из input cost (не тарифицируются)."""
        import logging

        response = make_text_response("Привет")
        response.usage = MagicMock()
        response.usage.prompt_tokens = 100
        response.usage.completion_tokens = 20
        response.usage.total_tokens = 120
        response.usage.precached_prompt_tokens = 80

        with caplog.at_level(logging.INFO):
            usage, cost = service._extract_usage(response)

        assert usage is not None
        assert usage["input"] == 100
        assert usage["precached_tokens"] == 80
        assert usage["billable_tokens"] == 40  # 120 - 80
        # cost: billable_input = 100 - 80 = 20, output = 20
        assert cost is not None
        price = 65 / 1_000_000
        assert cost["input"] == pytest.approx(20 * price)  # вычтены precached
        assert cost["output"] == pytest.approx(20 * price)
        assert cost["total"] == pytest.approx(40 * price)
        # Лог содержит precached и billable
        log_output = " ".join(caplog.messages)
        assert "precached" in log_output
        assert "80" in log_output
        assert "billable" in log_output

    def test_unknown_model_no_cost(self, mock_mcp_client):
        """Для неизвестной модели cost_details = None."""
        svc = GigaChatService(
            credentials="test-creds",
            model="UnknownModel-99",
            scope="GIGACHAT_API_PERS",
            mcp_client=mock_mcp_client,
        )
        response = make_text_response("Привет")

        usage, cost = svc._extract_usage(response)

        assert usage is not None
        assert cost is None
