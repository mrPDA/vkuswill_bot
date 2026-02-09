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
    Messages,
    MessagesRole,
)

from vkuswill_bot.services.gigachat_service import (
    DEFAULT_GIGACHAT_MAX_CONCURRENT,
    GIGACHAT_MAX_RETRIES,
    GigaChatService,
    MAX_CONVERSATIONS,
    MAX_USER_MESSAGE_LENGTH,
)
from vkuswill_bot.services.prompts import SYSTEM_PROMPT

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
    mock_mcp_client, mock_prefs_store, mock_recipe_store,
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

    async def test_reset_conversation(self, service):
        """Сброс удаляет историю пользователя."""
        service._get_history(user_id=42)
        assert 42 in service._conversations

        await service.reset_conversation(user_id=42)
        assert 42 not in service._conversations

    async def test_reset_nonexistent_user(self, service):
        """Сброс несуществующего пользователя не падает."""
        await service.reset_conversation(user_id=999)  # не должно бросить исключение


# ============================================================================
# LRU-вытеснение
# ============================================================================


class TestLRUEviction:
    """Тесты LRU-вытеснения диалогов из памяти."""

    def test_lru_eviction(self, mock_mcp_client):
        """При превышении MAX_CONVERSATIONS старейший диалог удаляется."""
        svc = GigaChatService(
            credentials="test-creds",
            model="GigaChat",
            scope="GIGACHAT_API_PERS",
            mcp_client=mock_mcp_client,
        )

        # Заполняем до лимита
        for uid in range(MAX_CONVERSATIONS):
            svc._get_history(uid)

        assert len(svc._conversations) == MAX_CONVERSATIONS

        # Добавляем ещё одного — первый должен быть вытеснен
        svc._get_history(MAX_CONVERSATIONS)

        assert len(svc._conversations) == MAX_CONVERSATIONS
        assert 0 not in svc._conversations  # самый старый вытеснен
        assert MAX_CONVERSATIONS in svc._conversations  # новый на месте

    def test_lru_access_refreshes(self, mock_mcp_client):
        """Доступ к диалогу перемещает его в конец (не вытесняется)."""
        svc = GigaChatService(
            credentials="test-creds",
            model="GigaChat",
            scope="GIGACHAT_API_PERS",
            mcp_client=mock_mcp_client,
        )

        # Создаём 3 диалога: 0, 1, 2
        for uid in range(3):
            svc._get_history(uid)

        # Обращаемся к 0 — он теперь самый свежий
        svc._get_history(0)

        # Порядок: 1, 2, 0
        keys = list(svc._conversations.keys())
        assert keys == [1, 2, 0]


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
        ) as mock_chat:
            await service.process_message(user_id=1, text=long_text)

        # Проверяем, что в историю попало обрезанное сообщение
        history = service._conversations[1]
        user_msg = [m for m in history if m.role == MessagesRole.USER][0]
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
        user_msg = [m for m in history if m.role == MessagesRole.USER][0]
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
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "молоко"}
                )
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
                return make_function_call_response(
                    "vkusvill_products_search", '{"q": "сыр"}'
                )
            else:
                return make_text_response("Вот сыр.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Сыр")

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
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "хлеб"}
                )
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
            return_value=make_function_call_response(
                "vkusvill_products_search", {"q": "тест"}
            ),
        ):
            result = await service.process_message(user_id=1, text="Тест")

        assert "слишком много шагов" in result.lower() or "/reset" in result
        # MCP вызывается только 1 раз — повторные с теми же аргументами
        # перехватываются детектором зацикливания
        assert mock_mcp_client.call_tool.call_count == 1

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
                return make_function_call_response(
                    "vkusvill_cart_link_create",
                    {"products": [{"xml_id": 123}]},
                )
            else:
                return make_text_response("К сожалению, не удалось создать корзину.")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(
                user_id=1, text="Создай корзину"
            )

        assert "не удалось" in result.lower() or "корзин" in result.lower()

    async def test_successful_call_loop_detection(self, service, mock_mcp_client):
        """Детектор перехватывает повторные успешные вызовы с теми же аргументами."""
        mock_mcp_client.call_tool.return_value = json.dumps({
            "ok": True, "data": {"items": [{"xml_id": 1, "name": "Тест"}]}
        })

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
            return make_function_call_response(
                "vkusvill_products_search", {"q": q}
            )

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
                    "vkusvill_products_search", {"q": "молоко"},
                )
            return make_text_response("Готово!")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Молоко")

        # Только 1 реальный вызов MCP — остальные дубликаты
        assert mock_mcp_client.call_tool.call_count == 1
        assert "Готово!" in result or "/reset" in result

    async def test_call_results_cached_for_duplicates(self, service, mock_mcp_client):
        """Закешированный результат вставляется в историю при дублировании."""
        original_result = json.dumps({
            "ok": True,
            "data": {"items": [{"xml_id": 42, "name": "Кефир"}]},
        })
        mock_mcp_client.call_tool.return_value = original_result

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "кефир"},
                )
            return make_text_response("Готово!")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Кефир")

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
        with patch.object(service._client, "close") as mock_close:
            await service.close()
        # close() был вызван (через asyncio.to_thread)

    async def test_close_with_error(self, service):
        """Ошибка при закрытии логируется, не бросается."""
        with patch.object(
            service._client, "close", side_effect=RuntimeError("close error")
        ):
            # Не должно бросить исключение
            await service.close()


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


# ============================================================================
# Локальные инструменты (предпочтения)
# ============================================================================


class TestParsePreferences:
    """Тесты _parse_preferences: парсинг JSON-результата user_preferences_get."""

    def test_valid_result(self):
        """Корректный JSON парсится в словарь."""
        result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "вареники", "preference": "с картофелем и шкварками"},
                {"category": "Молоко", "preference": "безлактозное 2,5%"},
                {"category": "эскимо", "preference": "пломбир ванильный в молочном шоколаде"},
            ],
        })
        prefs = GigaChatService._parse_preferences(result)
        assert prefs == {
            "вареники": "с картофелем и шкварками",
            "молоко": "безлактозное 2,5%",
            "эскимо": "пломбир ванильный в молочном шоколаде",
        }

    def test_empty_preferences(self):
        """Пустой список предпочтений → пустой словарь."""
        result = json.dumps({"ok": True, "preferences": []})
        assert GigaChatService._parse_preferences(result) == {}

    def test_invalid_json(self):
        """Невалидный JSON → пустой словарь."""
        assert GigaChatService._parse_preferences("not json") == {}

    def test_missing_fields(self):
        """Пропущенные поля category/preference пропускаются."""
        result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "хлеб"},
                {"preference": "чёрный"},
                {"category": "", "preference": "ржаной"},
                {"category": "сыр", "preference": ""},
                {"category": "молоко", "preference": "козье"},
            ],
        })
        prefs = GigaChatService._parse_preferences(result)
        assert prefs == {"молоко": "козье"}

    def test_case_normalization(self):
        """Категория приводится к lower case."""
        result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "Мороженое", "preference": "пломбир"},
            ],
        })
        prefs = GigaChatService._parse_preferences(result)
        assert "мороженое" in prefs


class TestApplyPreferencesToQuery:
    """Тесты _apply_preferences_to_query: подстановка предпочтений."""

    @pytest.fixture
    def prefs(self) -> dict[str, str]:
        return {
            "вареники": "с картофелем и шкварками",
            "молоко": "Молоко безлактозное 2,5%, 900 мл",
            "эскимо": "пломбир ванильный в молочном шоколаде, 70 г",
        }

    def test_exact_match(self, prefs):
        """Точное совпадение категории подставляет предпочтение."""
        result = GigaChatService._apply_preferences_to_query("вареники", prefs)
        assert result == "вареники с картофелем и шкварками"

    def test_exact_match_case_insensitive(self, prefs):
        """Регистронезависимое совпадение."""
        result = GigaChatService._apply_preferences_to_query("Вареники", prefs)
        assert result == "Вареники с картофелем и шкварками"

    def test_category_contained_in_preference(self, prefs):
        """Если предпочтение уже содержит запрос — возвращаем предпочтение."""
        result = GigaChatService._apply_preferences_to_query("молоко", prefs)
        assert result == "Молоко безлактозное 2,5%, 900 мл"

    def test_no_matching_preference(self, prefs):
        """Нет совпадения → запрос без изменений."""
        result = GigaChatService._apply_preferences_to_query("творог", prefs)
        assert result == "творог"

    def test_empty_prefs(self):
        """Пустой словарь предпочтений → запрос без изменений."""
        result = GigaChatService._apply_preferences_to_query("молоко", {})
        assert result == "молоко"

    def test_empty_query(self, prefs):
        """Пустой запрос → пустой запрос."""
        result = GigaChatService._apply_preferences_to_query("", prefs)
        assert result == ""

    def test_partial_match_query_in_category(self, prefs):
        """Запрос содержится в категории: 'эскимо' → подстановка."""
        # "эскимо" — точное совпадение
        result = GigaChatService._apply_preferences_to_query("эскимо", prefs)
        assert "пломбир ванильный" in result

    def test_specific_query_not_overridden(self, prefs):
        """Уточнённый запрос НЕ заменяется предпочтением."""
        # "молоко козье" ≠ "молоко" — пользователь уточнил, не подставляем
        result = GigaChatService._apply_preferences_to_query("молоко козье", prefs)
        assert result == "молоко козье"

    def test_real_case_ice_cream(self):
        """Реальный кейс: 'мороженое' при предпочтении category='мороженое'."""
        prefs = {"мороженое": "пломбир ванильный в молочном шоколаде"}
        result = GigaChatService._apply_preferences_to_query("мороженое", prefs)
        assert result == "мороженое пломбир ванильный в молочном шоколаде"

    def test_real_case_milk(self):
        """Реальный кейс: 'молоко' при предпочтении с полным названием."""
        prefs = {"молоко": "Молоко безлактозное 2,5%, 900 мл"}
        result = GigaChatService._apply_preferences_to_query("молоко", prefs)
        # Предпочтение уже содержит "молоко" → возвращается само предпочтение
        assert result == "Молоко безлактозное 2,5%, 900 мл"


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


class TestCallLocalTool:
    """Тесты _call_local_tool: маршрутизация предпочтений."""

    async def test_preferences_get(self, service_with_prefs, mock_prefs_store):
        """user_preferences_get вызывает get_formatted."""
        result = await service_with_prefs._call_local_tool(
            "user_preferences_get", {}, user_id=42,
        )
        mock_prefs_store.get_formatted.assert_called_once_with(42)
        parsed = json.loads(result)
        assert parsed["ok"] is True

    async def test_preferences_set(self, service_with_prefs, mock_prefs_store):
        """user_preferences_set вызывает set с правильными аргументами."""
        result = await service_with_prefs._call_local_tool(
            "user_preferences_set",
            {"category": "мороженое", "preference": "пломбир"},
            user_id=42,
        )
        mock_prefs_store.set.assert_called_once_with(42, "мороженое", "пломбир")
        assert "Запомнил" in result

    async def test_preferences_delete(self, service_with_prefs, mock_prefs_store):
        """user_preferences_delete вызывает delete."""
        result = await service_with_prefs._call_local_tool(
            "user_preferences_delete",
            {"category": "мороженое"},
            user_id=42,
        )
        mock_prefs_store.delete.assert_called_once_with(42, "мороженое")
        assert "удалено" in result

    async def test_set_missing_category(self, service_with_prefs):
        """set без категории возвращает ошибку."""
        result = await service_with_prefs._call_local_tool(
            "user_preferences_set",
            {"preference": "пломбир"},
            user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_set_missing_preference(self, service_with_prefs):
        """set без предпочтения возвращает ошибку."""
        result = await service_with_prefs._call_local_tool(
            "user_preferences_set",
            {"category": "мороженое"},
            user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_delete_missing_category(self, service_with_prefs):
        """delete без категории возвращает ошибку."""
        result = await service_with_prefs._call_local_tool(
            "user_preferences_delete",
            {},
            user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_no_store_returns_error(self, service):
        """Без prefs_store — ошибка."""
        result = await service._call_local_tool(
            "user_preferences_get", {}, user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "не настроено" in parsed["error"]

    async def test_unknown_tool(self, service_with_prefs):
        """Неизвестный локальный инструмент — ошибка."""
        result = await service_with_prefs._call_local_tool(
            "unknown_tool", {}, user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False


# ============================================================================
# Интеграционные тесты: поиск → кеш цен → корзина → расчёт
# ============================================================================


class TestParsePreferencesEdgeCases:
    """Дополнительные тесты _parse_preferences для непокрытых строк."""

    def test_preferences_not_list(self):
        """preferences — не список → пустой словарь (line 218)."""
        result = json.dumps({"ok": True, "preferences": "not-a-list"})
        assert GigaChatService._parse_preferences(result) == {}

    def test_preferences_is_dict(self):
        """preferences — словарь вместо списка → пустой словарь."""
        result = json.dumps({"ok": True, "preferences": {"key": "value"}})
        assert GigaChatService._parse_preferences(result) == {}

    def test_non_dict_items_in_preferences(self):
        """Не-dict элементы в списке preferences пропускаются (line 223)."""
        result = json.dumps({
            "ok": True,
            "preferences": [
                "string_item",
                42,
                None,
                {"category": "молоко", "preference": "козье"},
            ],
        })
        prefs = GigaChatService._parse_preferences(result)
        assert prefs == {"молоко": "козье"}

    def test_preferences_is_none(self):
        """preferences=None → пустой словарь."""
        result = json.dumps({"ok": True, "preferences": None})
        assert GigaChatService._parse_preferences(result) == {}


# ============================================================================
# Прямые unit-тесты вспомогательных методов
# ============================================================================


class TestParseToolArguments:
    """Тесты _parse_tool_arguments: парсинг аргументов функции от GigaChat."""

    def test_dict_passthrough(self):
        """Dict возвращается как есть."""
        args = {"q": "молоко", "limit": 5}
        assert GigaChatService._parse_tool_arguments(args) == args

    def test_json_string(self):
        """JSON-строка парсится в dict."""
        assert GigaChatService._parse_tool_arguments('{"q": "сыр"}') == {"q": "сыр"}

    def test_invalid_json_string(self):
        """Невалидный JSON → пустой dict."""
        assert GigaChatService._parse_tool_arguments('{"invalid') == {}

    def test_none_returns_empty_dict(self):
        """None → пустой dict."""
        assert GigaChatService._parse_tool_arguments(None) == {}

    def test_int_returns_empty_dict(self):
        """int → пустой dict."""
        assert GigaChatService._parse_tool_arguments(12345) == {}

    def test_list_returns_empty_dict(self):
        """list → пустой dict."""
        assert GigaChatService._parse_tool_arguments([1, 2, 3]) == {}

    def test_empty_string(self):
        """Пустая строка → пустой dict (не валидный JSON)."""
        assert GigaChatService._parse_tool_arguments("") == {}

    def test_empty_dict(self):
        """Пустой dict → пустой dict."""
        assert GigaChatService._parse_tool_arguments({}) == {}


class TestAppendAssistantMessage:
    """Тесты _append_assistant_message: добавление сообщения ассистента в историю."""

    def test_text_message(self):
        """Текстовое сообщение (без function_call)."""
        history: list[Messages] = []
        msg = MagicMock()
        msg.content = "Привет!"
        msg.function_call = None
        msg.functions_state_id = None

        GigaChatService._append_assistant_message(history, msg)

        assert len(history) == 1
        assert history[0].role == MessagesRole.ASSISTANT
        assert history[0].content == "Привет!"

    def test_function_call_preserved(self):
        """function_call сохраняется в истории."""
        history: list[Messages] = []
        msg = MagicMock()
        msg.content = ""
        fc = MagicMock()
        fc.name = "vkusvill_products_search"
        fc.arguments = {"q": "молоко"}
        msg.function_call = fc
        msg.functions_state_id = None

        GigaChatService._append_assistant_message(history, msg)

        assert history[0].function_call is fc

    def test_functions_state_id_preserved(self):
        """functions_state_id сохраняется в истории."""
        history: list[Messages] = []
        msg = MagicMock()
        msg.content = ""
        msg.function_call = MagicMock()
        msg.functions_state_id = "state-123"

        GigaChatService._append_assistant_message(history, msg)

        assert history[0].functions_state_id == "state-123"

    def test_no_functions_state_id_attr(self):
        """Если у msg нет атрибута functions_state_id — не падает."""
        history: list[Messages] = []
        msg = MagicMock(spec=["content", "function_call"])
        msg.content = "text"
        msg.function_call = None

        GigaChatService._append_assistant_message(history, msg)
        assert len(history) == 1

    def test_empty_content_defaults_to_empty_string(self):
        """None content → пустая строка."""
        history: list[Messages] = []
        msg = MagicMock()
        msg.content = None
        msg.function_call = None
        msg.functions_state_id = None

        GigaChatService._append_assistant_message(history, msg)
        assert history[0].content == ""


class TestPreprocessToolArgs:
    """Тесты _preprocess_tool_args: предобработка аргументов инструмента."""

    def test_cart_fix_applied(self, service):
        """Для корзины вызывается fix_unit_quantities."""
        service._search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        args = {"products": [{"xml_id": 100, "q": 0.5}]}
        result = service._preprocess_tool_args(
            "vkusvill_cart_link_create", args, {},
        )
        assert result["products"][0]["q"] == 1  # округлено

    def test_search_with_preferences(self, service):
        """Для поиска подставляются предпочтения."""
        prefs = {"молоко": "козье 3,2%"}
        args = {"q": "молоко"}
        result = service._preprocess_tool_args(
            "vkusvill_products_search", args, prefs,
        )
        assert result["q"] == "молоко козье 3,2%"

    def test_search_without_preferences(self, service):
        """Для поиска без предпочтений — аргументы без изменений."""
        args = {"q": "творог"}
        result = service._preprocess_tool_args(
            "vkusvill_products_search", args, {},
        )
        assert result["q"] == "творог"

    def test_other_tool_passthrough(self, service):
        """Для прочих инструментов аргументы не меняются."""
        args = {"xml_id": 123}
        result = service._preprocess_tool_args(
            "vkusvill_product_details", args, {},
        )
        assert result == args

    def test_search_preference_not_applied_if_no_match(self, service):
        """Предпочтения без совпадения не меняют запрос."""
        prefs = {"хлеб": "бородинский"}
        args = {"q": "молоко"}
        result = service._preprocess_tool_args(
            "vkusvill_products_search", args, prefs,
        )
        assert result["q"] == "молоко"


class TestIsDuplicateCall:
    """Тесты _is_duplicate_call: обнаружение зацикливания."""

    def test_first_call_not_duplicate(self, service):
        """Первый вызов — не дубликат."""
        call_counts: dict[str, int] = {}
        call_results: dict[str, str] = {}
        history: list[Messages] = []

        is_dup = service._is_duplicate_call(
            "vkusvill_products_search", {"q": "молоко"},
            call_counts, call_results, history,
        )
        assert is_dup is False
        assert len(history) == 0

    def test_second_call_returns_cached_result(self, service):
        """Второй одинаковый вызов — возвращает закешированный результат."""
        call_counts: dict[str, int] = {}
        call_results: dict[str, str] = {}
        history: list[Messages] = []
        args = {"q": "молоко"}

        # Первый вызов — не дубликат
        service._is_duplicate_call(
            "vkusvill_products_search", args,
            call_counts, call_results, history,
        )

        # Сохраняем результат (как делает process_message)
        call_key = f"vkusvill_products_search:{json.dumps(args, sort_keys=True)}"
        cached = json.dumps({"ok": True, "data": {"items": [{"xml_id": 123}]}})
        call_results[call_key] = cached

        # Второй вызов — дубликат, возвращает закешированный результат
        is_dup = service._is_duplicate_call(
            "vkusvill_products_search", args,
            call_counts, call_results, history,
        )

        assert is_dup is True
        assert len(history) == 1
        assert history[0].role == MessagesRole.FUNCTION
        # Вместо ошибки — реальный результат
        content = json.loads(history[0].content)
        assert content["ok"] is True
        assert content["data"]["items"][0]["xml_id"] == 123

    def test_second_call_without_cached_result(self, service):
        """Дубликат без кеша — возвращает пустой OK."""
        call_counts: dict[str, int] = {}
        call_results: dict[str, str] = {}
        history: list[Messages] = []
        args = {"q": "молоко"}

        service._is_duplicate_call(
            "vkusvill_products_search", args,
            call_counts, call_results, history,
        )
        is_dup = service._is_duplicate_call(
            "vkusvill_products_search", args,
            call_counts, call_results, history,
        )

        assert is_dup is True
        content = json.loads(history[0].content)
        assert content["ok"] is True

    def test_different_args_not_duplicate(self, service):
        """Разные аргументы — не дубликат."""
        call_counts: dict[str, int] = {}
        call_results: dict[str, str] = {}
        history: list[Messages] = []

        service._is_duplicate_call(
            "vkusvill_products_search", {"q": "молоко"},
            call_counts, call_results, history,
        )
        is_dup = service._is_duplicate_call(
            "vkusvill_products_search", {"q": "хлеб"},
            call_counts, call_results, history,
        )

        assert is_dup is False
        assert len(history) == 0

    def test_different_tool_not_duplicate(self, service):
        """Разные инструменты — не дубликат."""
        call_counts: dict[str, int] = {}
        call_results: dict[str, str] = {}
        history: list[Messages] = []
        args = {"q": "молоко"}

        service._is_duplicate_call(
            "vkusvill_products_search", args,
            call_counts, call_results, history,
        )
        is_dup = service._is_duplicate_call(
            "vkusvill_product_details", args,
            call_counts, call_results, history,
        )

        assert is_dup is False


class TestExecuteTool:
    """Тесты _execute_tool: выполнение инструментов."""

    async def test_mcp_tool(self, service, mock_mcp_client):
        """MCP-инструмент вызывается через MCP-клиент."""
        mock_mcp_client.call_tool.return_value = '{"ok": true}'

        result = await service._execute_tool(
            "vkusvill_products_search", {"q": "молоко"}, user_id=1,
        )

        assert result == '{"ok": true}'
        mock_mcp_client.call_tool.assert_called_once()

    async def test_local_tool(self, service_with_prefs, mock_prefs_store):
        """Локальный инструмент вызывается напрямую."""
        result = await service_with_prefs._execute_tool(
            "user_preferences_get", {}, user_id=42,
        )
        mock_prefs_store.get_formatted.assert_called_once_with(42)
        assert '"ok": true' in result

    async def test_mcp_error_returns_json(self, service, mock_mcp_client):
        """Ошибка MCP → JSON с error."""
        mock_mcp_client.call_tool.side_effect = RuntimeError("MCP down")

        result = await service._execute_tool(
            "vkusvill_products_search", {"q": "тест"}, user_id=1,
        )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "MCP down" in parsed["error"]


class TestPostprocessToolResult:
    """Тесты _postprocess_tool_result: постобработка результата инструмента."""

    def test_preferences_get_parsed(self, service):
        """user_preferences_get парсит предпочтения в user_prefs."""
        prefs_result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "молоко", "preference": "козье"},
            ],
        })
        user_prefs: dict[str, str] = {}
        search_log: dict[str, set[int]] = {}

        result = service._postprocess_tool_result(
            "user_preferences_get", {}, prefs_result,
            user_prefs, search_log,
        )

        assert user_prefs == {"молоко": "козье"}
        assert result == prefs_result

    def test_search_caches_and_trims(self, service):
        """vkusvill_products_search кеширует цены и обрезает результат."""
        search_result = json.dumps({
            "ok": True,
            "data": {
                "items": [
                    {
                        "xml_id": 100,
                        "name": "Молоко",
                        "price": {"current": 79, "currency": "RUB"},
                        "unit": "шт",
                        "description": "Длинное...",
                        "images": ["img.jpg"],
                    }
                ]
            },
        })
        user_prefs: dict[str, str] = {}
        search_log: dict[str, set[int]] = {}

        result = service._postprocess_tool_result(
            "vkusvill_products_search",
            {"q": "молоко"},
            search_result,
            user_prefs,
            search_log,
        )

        # Цены закешированы
        assert 100 in service._search_processor.price_cache
        # Результат обрезан (нет description)
        parsed = json.loads(result)
        assert "description" not in parsed["data"]["items"][0]
        # search_log обновлён
        assert "молоко" in search_log
        assert 100 in search_log["молоко"]

    def test_cart_calculates_total(self, service):
        """vkusvill_cart_link_create рассчитывает стоимость."""
        service._search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}
        user_prefs: dict[str, str] = {}
        search_log: dict[str, set[int]] = {}

        result = service._postprocess_tool_result(
            "vkusvill_cart_link_create", args, cart_result,
            user_prefs, search_log,
        )

        parsed = json.loads(result)
        assert "price_summary" in parsed["data"]
        assert parsed["data"]["price_summary"]["total"] == 158.0

    def test_cart_with_verification(self, service):
        """vkusvill_cart_link_create добавляет verification если есть search_log."""
        service._search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}
        user_prefs: dict[str, str] = {}
        search_log: dict[str, set[int]] = {"молоко": {100}}

        result = service._postprocess_tool_result(
            "vkusvill_cart_link_create", args, cart_result,
            user_prefs, search_log,
        )

        parsed = json.loads(result)
        assert "verification" in parsed["data"]
        assert parsed["data"]["verification"]["ok"] is True

    def test_unknown_tool_passthrough(self, service):
        """Неизвестный инструмент — результат без изменений."""
        result = service._postprocess_tool_result(
            "unknown_tool", {}, '{"some": "data"}', {}, {},
        )
        assert result == '{"some": "data"}'

    def test_search_empty_query_not_logged(self, service):
        """Пустой запрос не попадает в search_log."""
        search_result = json.dumps({
            "ok": True,
            "data": {
                "items": [
                    {"xml_id": 100, "name": "Товар", "price": {"current": 50}, "unit": "шт"},
                ]
            },
        })
        search_log: dict[str, set[int]] = {}

        service._postprocess_tool_result(
            "vkusvill_products_search", {"q": ""}, search_result, {}, search_log,
        )

        assert "" not in search_log

    def test_preferences_replaces_existing(self, service):
        """Повторная загрузка предпочтений заменяет старые."""
        user_prefs = {"старое": "значение"}
        prefs_result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "новое", "preference": "значение"},
            ],
        })

        service._postprocess_tool_result(
            "user_preferences_get", {}, prefs_result, user_prefs, {},
        )

        assert "старое" not in user_prefs
        assert user_prefs == {"новое": "значение"}


class TestSearchTrimCacheCartFlow:
    """Тесты полного flow: поиск → кеш цен → обрезка → корзина → расчёт."""

    async def test_search_then_cart_full_flow(self, service, mock_mcp_client):
        """Полный цикл: поиск кеширует цены, корзина рассчитывает стоимость."""
        search_result = json.dumps({
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
        })
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })

        mock_mcp_client.call_tool.side_effect = [search_result, cart_result]

        call_count = 0

        def mock_chat(chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "молоко"},
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
            "vkusvill_products_search", {"limit": 5},
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
                    "vkusvill_products_search", {"q": "test"},
                )
                # Подменяем arguments на non-dict, non-str
                resp.choices[0].message.function_call.arguments = 12345
                return resp
            return make_text_response("Ответ")

        with patch.object(service._client, "chat", side_effect=mock_chat):
            result = await service.process_message(user_id=1, text="Тест")

        assert isinstance(result, str)
        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search", {"limit": 5},
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
                    "vkusvill_products_search", {"q": "тест"},
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
            getattr(m, "functions_state_id", None) == "state-abc-123"
            for m in assistant_msgs
        )


class TestProcessMessageWithPrefs:
    """Тесты process_message с маршрутизацией предпочтений."""

    async def test_preferences_get_routed_locally(
        self, service_with_prefs, mock_mcp_client, mock_prefs_store,
    ):
        """user_preferences_get маршрутизируется локально, не через MCP."""
        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "user_preferences_get", {},
                )
            else:
                return make_text_response("У вас нет предпочтений.")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            result = await service_with_prefs.process_message(
                user_id=42, text="Мои предпочтения",
            )

        # Вызвано хранилище, а не MCP
        mock_prefs_store.get_formatted.assert_called_once_with(42)
        mock_mcp_client.call_tool.assert_not_called()

    async def test_preferences_set_routed_locally(
        self, service_with_prefs, mock_mcp_client, mock_prefs_store,
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
            result = await service_with_prefs.process_message(
                user_id=42, text="Запомни, я люблю пломбир в шоколаде",
            )

        mock_prefs_store.set.assert_called_once_with(
            42, "мороженое", "пломбир в шоколаде",
        )
        mock_mcp_client.call_tool.assert_not_called()

    async def test_mcp_tool_still_goes_through_mcp(
        self, service_with_prefs, mock_mcp_client,
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
                    "vkusvill_products_search", {"q": "молоко"},
                )
            else:
                return make_text_response("Ничего не нашёл.")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            await service_with_prefs.process_message(
                user_id=42, text="Найди молоко",
            )

        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search", {"q": "молоко", "limit": 5},
        )

    async def test_preferences_enhance_search_query(
        self, service_with_prefs, mock_mcp_client, mock_prefs_store,
    ):
        """После загрузки предпочтений поисковый запрос обогащается."""
        # Предпочтения: вареники -> с картофелем и шкварками
        mock_prefs_store.get_formatted.return_value = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "вареники", "preference": "с картофелем и шкварками"},
                {"category": "молоко", "preference": "Молоко безлактозное 2,5%, 900 мл"},
            ],
        })

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
                    "user_preferences_get", {},
                )
            elif call_count == 2:
                # Шаг 2: поиск вареников
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "вареники"},
                )
            elif call_count == 3:
                # Шаг 3: поиск молока
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "молоко"},
                )
            else:
                return make_text_response("Готово!")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            await service_with_prefs.process_message(
                user_id=42, text="Закажи вареники и молоко",
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
        self, service_with_prefs, mock_mcp_client, mock_prefs_store,
    ):
        """Если предпочтений нет — поиск идёт без изменений."""
        mock_prefs_store.get_formatted.return_value = json.dumps({
            "ok": True,
            "preferences": [],
        })

        mock_mcp_client.call_tool.return_value = json.dumps(
            {"ok": True, "data": {"meta": {"q": "test"}, "items": []}},
        )

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "user_preferences_get", {},
                )
            elif call_count == 2:
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "творог"},
                )
            else:
                return make_text_response("Готово!")

        with patch.object(service_with_prefs._client, "chat", side_effect=mock_chat):
            await service_with_prefs.process_message(
                user_id=42, text="Закажи творог",
            )

        calls = mock_mcp_client.call_tool.call_args_list
        assert len(calls) == 1
        # Запрос не изменён — нет предпочтений для творога
        assert calls[0].args[1]["q"] == "творог"


# ============================================================================
# Парсинг JSON из LLM
# ============================================================================


class TestParseJsonFromLLM:
    """Тесты _parse_json_from_llm: извлечение JSON из ответа GigaChat."""

    def test_plain_json_array(self):
        """Обычный JSON-массив."""
        content = '[{"name": "мясо", "quantity": 1}]'
        result = GigaChatService._parse_json_from_llm(content)
        assert result == [{"name": "мясо", "quantity": 1}]

    def test_json_with_markdown_code_block(self):
        """JSON обёрнутый в ```json...```."""
        content = '```json\n[{"name": "мясо"}]\n```'
        result = GigaChatService._parse_json_from_llm(content)
        assert result == [{"name": "мясо"}]

    def test_json_with_plain_code_block(self):
        """JSON обёрнутый в ```...``` без указания языка."""
        content = '```\n[{"name": "мясо"}]\n```'
        result = GigaChatService._parse_json_from_llm(content)
        assert result == [{"name": "мясо"}]

    def test_json_with_whitespace(self):
        """JSON с пробелами и переносами строк."""
        content = '  \n [{"name": "мясо"}] \n  '
        result = GigaChatService._parse_json_from_llm(content)
        assert result == [{"name": "мясо"}]

    def test_invalid_json_raises(self):
        """Невалидный JSON вызывает ошибку."""
        with pytest.raises(json.JSONDecodeError):
            GigaChatService._parse_json_from_llm("not json at all")

    def test_json_object(self):
        """JSON-объект (не массив)."""
        content = '{"ok": true}'
        result = GigaChatService._parse_json_from_llm(content)
        assert result == {"ok": True}


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
        self, service_with_recipes, mock_recipe_store,
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
        self, service_with_recipes, mock_recipe_store,
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
        self, service_with_recipes, mock_recipe_store,
    ):
        """Кеш-промах — вызывает GigaChat для извлечения рецепта."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps([
            {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свёкла"},
        ], ensure_ascii=False)

        with patch.object(
            service_with_recipes._client, "chat",
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
        self, service_with_recipes, mock_recipe_store,
    ):
        """Ошибка LLM — возвращает ошибку с инструкцией для GigaChat."""
        mock_recipe_store.get.return_value = None

        with patch.object(
            service_with_recipes._client, "chat",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ"},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "самостоятельно" in parsed["error"]

    async def test_default_servings(
        self, service_with_recipes, mock_recipe_store,
    ):
        """Без параметра servings используется 4 по умолчанию."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ"},
        )
        parsed = json.loads(result)

        assert parsed["servings"] == 4
        assert parsed["ok"] is True

    async def test_invalid_servings_defaults_to_4(
        self, service_with_recipes, mock_recipe_store,
    ):
        """Некорректный servings заменяется на 4."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": -1},
        )
        parsed = json.loads(result)
        assert parsed["servings"] == 4

    async def test_cache_miss_with_markdown_response(
        self, service_with_recipes, mock_recipe_store,
    ):
        """GigaChat возвращает JSON в markdown-обёртке — парсится корректно."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = (
            '```json\n[{"name": "свёкла", "quantity": 0.5}]\n```'
        )

        with patch.object(
            service_with_recipes._client, "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ"},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["ingredients"][0]["name"] == "свёкла"

    async def test_hint_in_result(
        self, service_with_recipes, mock_recipe_store,
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
        self, service_with_recipes, mock_recipe_store,
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
        self, service_with_recipes, mock_recipe_store,
    ):
        """Кеш-промах — LLM-результат тоже обогащается kg_equivalent."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps([
            {"name": "лук репчатый", "quantity": 2, "unit": "шт", "search_query": "лук"},
            {"name": "говядина", "quantity": 0.8, "unit": "кг", "search_query": "говядина"},
        ], ensure_ascii=False)

        with patch.object(
            service_with_recipes._client, "chat",
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
# recipe_ingredients через _execute_tool (маршрутизация)
# ============================================================================


# ============================================================================
# _enrich_with_kg: обогащение ингредиентов эквивалентом в кг
# ============================================================================


class TestEnrichWithKg:
    """Тесты _enrich_with_kg: добавление kg_equivalent для штучных ингредиентов."""

    # Таблица весов для тестов (подмножество из _handle_recipe_ingredients)
    WEIGHTS = {
        "картофель": 0.15,
        "лук": 0.1,
        "морковь": 0.15,
        "свекла": 0.3,
        "помидор": 0.15,
    }

    def test_adds_kg_equivalent_for_piece_items(self):
        """Ингредиент в шт с совпадением — добавляется kg_equivalent."""
        items = [
            {"name": "лук репчатый", "quantity": 3, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.3  # 3 * 0.1

    def test_adds_kg_equivalent_for_potato(self):
        """Картофель 5 шт → kg_equivalent=0.75."""
        items = [
            {"name": "Картофель молодой", "quantity": 5, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.75  # 5 * 0.15

    def test_skips_weight_units(self):
        """Ингредиенты в кг/г/мл/л не обогащаются."""
        items = [
            {"name": "картофель", "quantity": 1, "unit": "кг"},
            {"name": "морковь", "quantity": 200, "unit": "г"},
            {"name": "лук", "quantity": 100, "unit": "мл"},
            {"name": "свекла", "quantity": 0.5, "unit": "л"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        for item in result:
            assert "kg_equivalent" not in item

    def test_skips_non_dict_items(self):
        """Не-dict элементы пропускаются без ошибки."""
        items = [
            "строка",
            42,
            None,
            {"name": "лук", "quantity": 2, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        # Только последний dict-элемент обогащён
        assert result[-1]["kg_equivalent"] == 0.2

    def test_skips_no_match(self):
        """Ингредиент без совпадения в таблице — не обогащается."""
        items = [
            {"name": "сметана", "quantity": 1, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert "kg_equivalent" not in result[0]

    def test_skips_zero_quantity(self):
        """quantity=0 — не обогащается."""
        items = [
            {"name": "лук", "quantity": 0, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert "kg_equivalent" not in result[0]

    def test_skips_negative_quantity(self):
        """Отрицательное quantity — не обогащается."""
        items = [
            {"name": "лук", "quantity": -1, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert "kg_equivalent" not in result[0]

    def test_substring_matching(self):
        """Подстрока: 'морковь' найдена в 'морковь свежая'."""
        items = [
            {"name": "морковь свежая", "quantity": 2, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.3  # 2 * 0.15

    def test_mixed_items(self):
        """Смешанный список: одни обогащаются, другие — нет."""
        items = [
            {"name": "картофель", "quantity": 4, "unit": "шт"},
            {"name": "сливочное масло", "quantity": 1, "unit": "шт"},
            {"name": "помидор", "quantity": 3, "unit": "шт"},
            {"name": "курица", "quantity": 0.8, "unit": "кг"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.6  # 4 * 0.15
        assert "kg_equivalent" not in result[1]  # нет в таблице
        assert result[2]["kg_equivalent"] == 0.45  # 3 * 0.15
        assert "kg_equivalent" not in result[3]  # unit="кг"

    def test_empty_list(self):
        """Пустой список — возвращает пустой."""
        result = GigaChatService._enrich_with_kg([], self.WEIGHTS)
        assert result == []

    def test_empty_weights(self):
        """Пустая таблица весов — ничего не обогащается."""
        items = [
            {"name": "лук", "quantity": 2, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, {})
        assert "kg_equivalent" not in result[0]

    def test_rounding(self):
        """Результат округляется до 2 знаков."""
        items = [
            {"name": "свекла", "quantity": 3, "unit": "шт"},  # 3 * 0.3 = 0.9
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.9

    def test_mutates_in_place(self):
        """Метод мутирует items in-place."""
        items = [
            {"name": "лук", "quantity": 2, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert result is items
        assert items[0]["kg_equivalent"] == 0.2

    def test_missing_unit_defaults_to_empty(self):
        """Ингредиент без unit — не в весовых, ищем в таблице."""
        items = [
            {"name": "помидор", "quantity": 4},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.6  # 4 * 0.15

    def test_fractional_quantity(self):
        """Дробное quantity корректно обрабатывается."""
        items = [
            {"name": "лук", "quantity": 1.5, "unit": "шт"},
        ]
        result = GigaChatService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.15  # 1.5 * 0.1


# ============================================================================
# _format_recipe_result: форматирование результата
# ============================================================================


class TestFormatRecipeResult:
    """Тесты _format_recipe_result: формирование JSON-ответа рецепта."""

    def test_basic_structure(self):
        """Результат содержит все обязательные поля."""
        result = GigaChatService._format_recipe_result(
            dish="борщ", servings=4,
            ingredients=[{"name": "свёкла"}], cached=True,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["dish"] == "борщ"
        assert parsed["servings"] == 4
        assert parsed["cached"] is True
        assert len(parsed["ingredients"]) == 1
        assert "hint" in parsed

    def test_hint_mentions_kg_equivalent(self):
        """hint содержит инструкцию про kg_equivalent."""
        result = GigaChatService._format_recipe_result(
            dish="азу", servings=2,
            ingredients=[], cached=False,
        )
        parsed = json.loads(result)
        assert "kg_equivalent" in parsed["hint"]
        assert "vkusvill_products_search" in parsed["hint"]

    def test_cached_false(self):
        """cached=False корректно отражается."""
        result = GigaChatService._format_recipe_result(
            dish="плов", servings=6,
            ingredients=[{"name": "рис"}, {"name": "морковь"}],
            cached=False,
        )
        parsed = json.loads(result)
        assert parsed["cached"] is False
        assert len(parsed["ingredients"]) == 2

    def test_unicode_dish_name(self):
        """Русское название блюда сохраняется (ensure_ascii=False)."""
        result = GigaChatService._format_recipe_result(
            dish="Щи из квашеной капусты", servings=4,
            ingredients=[], cached=True,
        )
        assert "Щи из квашеной капусты" in result


# ============================================================================
# recipe_ingredients через _execute_tool (маршрутизация)
# ============================================================================


class TestRecipeToolRouting:
    """Тесты маршрутизации recipe_ingredients через _execute_tool."""

    async def test_recipe_routed_locally(
        self, service_with_recipes, mock_recipe_store, mock_mcp_client,
    ):
        """recipe_ingredients маршрутизируется локально, не через MCP."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла"}],
        }

        result = await service_with_recipes._execute_tool(
            "recipe_ingredients", {"dish": "борщ"}, user_id=42,
        )
        parsed = json.loads(result)

        assert parsed["ok"] is True
        mock_mcp_client.call_tool.assert_not_called()


# ============================================================================
# recipe_ingredients: дополнительные edge-cases
# ============================================================================


class TestHandleRecipeIngredientsEdgeCases:
    """Дополнительные тесты _handle_recipe_ingredients для покрытия."""

    async def test_cache_save_failure_handled(
        self, service_with_recipes, mock_recipe_store,
    ):
        """Ошибка при сохранении в кеш не крашит — результат возвращается (lines 440-441)."""
        mock_recipe_store.get.return_value = None
        mock_recipe_store.save.side_effect = RuntimeError("DB write error")

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps([
            {"name": "мясо", "quantity": 1, "unit": "кг", "search_query": "говядина"},
        ], ensure_ascii=False)

        with patch.object(
            service_with_recipes._client, "chat",
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
        self, service_with_recipes, mock_recipe_store,
    ):
        """LLM вернул пустой массив — ошибка (line 486)."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = "[]"

        with patch.object(
            service_with_recipes._client, "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ"},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "самостоятельно" in parsed["error"]

    async def test_llm_returns_dict_instead_of_list(
        self, service_with_recipes, mock_recipe_store,
    ):
        """LLM вернул dict вместо list — ошибка (line 486)."""
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = '{"error": "bad request"}'

        with patch.object(
            service_with_recipes._client, "chat",
            return_value=llm_response,
        ):
            result = await service_with_recipes._handle_recipe_ingredients(
                {"dish": "борщ"},
            )

        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_servings_string_defaults_to_4(
        self, service_with_recipes, mock_recipe_store,
    ):
        """servings="два" (строка) → заменяется на 4."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": "два"},
        )
        parsed = json.loads(result)
        assert parsed["servings"] == 4

    async def test_servings_zero_defaults_to_4(
        self, service_with_recipes, mock_recipe_store,
    ):
        """servings=0 → заменяется на 4."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "борщ", "servings": 0},
        )
        parsed = json.loads(result)
        assert parsed["servings"] == 4

    async def test_dish_with_whitespace_only(
        self, service_with_recipes,
    ):
        """dish=" " → ошибка (пустое после strip)."""
        result = await service_with_recipes._handle_recipe_ingredients(
            {"dish": "   "},
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_recipe_integration_through_process_message(
        self, service_with_recipes, mock_mcp_client, mock_recipe_store,
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

        mock_mcp_client.call_tool.return_value = json.dumps({
            "ok": True,
            "data": {"items": [{"xml_id": 1, "name": "Свёкла", "price": {"current": 50}, "unit": "кг"}]},
        })

        call_count = 0

        def mock_chat(chat: Chat):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_function_call_response(
                    "recipe_ingredients", {"dish": "борщ", "servings": 4},
                )
            elif call_count == 2:
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "свёкла"},
                )
            elif call_count == 3:
                return make_function_call_response(
                    "vkusvill_products_search", {"q": "капуста"},
                )
            else:
                return make_text_response("Вот ваш борщ!")

        with patch.object(service_with_recipes._client, "chat", side_effect=mock_chat):
            result = await service_with_recipes.process_message(
                user_id=1, text="Собери продукты для борща",
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

    def test_rate_in_message(self):
        """Сообщение содержит 'rate' → rate limit."""
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

        with patch.object(service._client, "chat", side_effect=mock_chat):
            with patch("vkuswill_bot.services.gigachat_service.asyncio.sleep"):
                result = await service._call_gigachat(
                    history=[],
                    functions=[],
                )

        assert result is expected
        assert call_count == 2

    async def test_raises_after_max_retries(self, service):
        """Бросает исключение после исчерпания retry."""
        rate_limit_error = RuntimeError("HTTP 429 Too Many Requests")

        with patch.object(
            service._client,
            "chat",
            side_effect=rate_limit_error,
        ):
            with patch("vkuswill_bot.services.gigachat_service.asyncio.sleep"):
                with pytest.raises(RuntimeError, match="429"):
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

        with patch.object(service._client, "chat", side_effect=mock_chat):
            with pytest.raises(RuntimeError, match="Connection refused"):
                await service._call_gigachat(
                    history=[],
                    functions=[],
                )

        # Только 1 вызов — retry не было
        assert call_count == 1

    async def test_exponential_backoff_delays(self, service):
        """Retry использует exponential backoff (1s, 2s)."""
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

        with patch.object(service._client, "chat", side_effect=mock_chat):
            with patch(
                "vkuswill_bot.services.gigachat_service.asyncio.sleep",
                side_effect=mock_sleep,
            ):
                await service._call_gigachat(
                    history=[],
                    functions=[],
                )

        # delay = 2 ** attempt: attempt=0 → 1, attempt=1 → 2
        assert sleep_calls == [1, 2]

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
