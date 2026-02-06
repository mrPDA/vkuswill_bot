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
                return make_function_call_response(
                    "vkusvill_products_search", '{"q": "сыр"}'
                )
            else:
                return make_text_response("Вот сыр.")

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
        # Вызов MCP произошёл с пустым dict (fallback при невалидном JSON)
        mock_mcp_client.call_tool.assert_called_once_with(
            "vkusvill_products_search", {},
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
            "vkusvill_products_search", {},
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
            "vkusvill_products_search", {"q": "молоко"},
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
