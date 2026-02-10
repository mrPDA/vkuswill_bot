"""Тесты ToolExecutor.

Тестируем:
- Парсинг аргументов (parse_arguments)
- Сборка сообщения ассистента (build_assistant_message)
- Предобработка аргументов (preprocess_args)
- Детекция зацикливания (is_duplicate_call)
- Выполнение инструментов (execute)
- Постобработка результатов (postprocess_result)
- Маршрутизация локальных инструментов (_call_local_tool)
- Парсинг/подстановка предпочтений
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from gigachat.models import Messages, MessagesRole

from vkuswill_bot.services.tool_executor import (
    CallTracker,
    ToolExecutor,
)
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.cart_processor import CartProcessor


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
def search_processor() -> SearchProcessor:
    return SearchProcessor()


@pytest.fixture
def cart_processor(search_processor) -> CartProcessor:
    return CartProcessor(search_processor.price_cache)


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
def executor(mock_mcp_client, search_processor, cart_processor) -> ToolExecutor:
    """ToolExecutor без хранилища предпочтений."""
    return ToolExecutor(
        mcp_client=mock_mcp_client,
        search_processor=search_processor,
        cart_processor=cart_processor,
    )


@pytest.fixture
def executor_with_prefs(
    mock_mcp_client, search_processor, cart_processor, mock_prefs_store,
) -> ToolExecutor:
    """ToolExecutor с хранилищем предпочтений."""
    return ToolExecutor(
        mcp_client=mock_mcp_client,
        search_processor=search_processor,
        cart_processor=cart_processor,
        preferences_store=mock_prefs_store,
    )


# ============================================================================
# parse_arguments
# ============================================================================


class TestParseArguments:
    """Тесты parse_arguments: парсинг аргументов функции от GigaChat."""

    def test_dict_passthrough(self):
        args = {"q": "молоко", "limit": 5}
        assert ToolExecutor.parse_arguments(args) == args

    def test_json_string(self):
        assert ToolExecutor.parse_arguments('{"q": "сыр"}') == {"q": "сыр"}

    def test_invalid_json_string(self):
        assert ToolExecutor.parse_arguments('{"invalid') == {}

    def test_none_returns_empty_dict(self):
        assert ToolExecutor.parse_arguments(None) == {}

    def test_int_returns_empty_dict(self):
        assert ToolExecutor.parse_arguments(12345) == {}

    def test_list_returns_empty_dict(self):
        assert ToolExecutor.parse_arguments([1, 2, 3]) == {}

    def test_empty_string(self):
        assert ToolExecutor.parse_arguments("") == {}

    def test_empty_dict(self):
        assert ToolExecutor.parse_arguments({}) == {}


# ============================================================================
# build_assistant_message
# ============================================================================


class TestBuildAssistantMessage:
    """Тесты build_assistant_message."""

    def test_text_message(self):
        history: list[Messages] = []
        msg = MagicMock()
        msg.content = "Привет!"
        msg.function_call = None
        msg.functions_state_id = None

        ToolExecutor.build_assistant_message(history, msg)

        assert len(history) == 1
        assert history[0].role == MessagesRole.ASSISTANT
        assert history[0].content == "Привет!"

    def test_function_call_preserved(self):
        history: list[Messages] = []
        msg = MagicMock()
        msg.content = ""
        fc = MagicMock()
        fc.name = "vkusvill_products_search"
        fc.arguments = {"q": "молоко"}
        msg.function_call = fc
        msg.functions_state_id = None

        ToolExecutor.build_assistant_message(history, msg)

        assert history[0].function_call is fc

    def test_functions_state_id_preserved(self):
        history: list[Messages] = []
        msg = MagicMock()
        msg.content = ""
        msg.function_call = MagicMock()
        msg.functions_state_id = "state-123"

        ToolExecutor.build_assistant_message(history, msg)

        assert history[0].functions_state_id == "state-123"

    def test_no_functions_state_id_attr(self):
        history: list[Messages] = []
        msg = MagicMock(spec=["content", "function_call"])
        msg.content = "text"
        msg.function_call = None

        ToolExecutor.build_assistant_message(history, msg)
        assert len(history) == 1

    def test_empty_content_defaults_to_empty_string(self):
        history: list[Messages] = []
        msg = MagicMock()
        msg.content = None
        msg.function_call = None
        msg.functions_state_id = None

        ToolExecutor.build_assistant_message(history, msg)
        assert history[0].content == ""


# ============================================================================
# preprocess_args
# ============================================================================


class TestPreprocessArgs:
    """Тесты preprocess_args: предобработка аргументов."""

    async def test_cart_fix_applied(self, executor, search_processor):
        search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        args = {"products": [{"xml_id": 100, "q": 0.5}]}
        result = await executor.preprocess_args(
            "vkusvill_cart_link_create", args, {},
        )
        assert result["products"][0]["q"] == 1

    async def test_search_with_preferences(self, executor):
        prefs = {"молоко": "козье 3,2%"}
        args = {"q": "молоко"}
        result = await executor.preprocess_args(
            "vkusvill_products_search", args, prefs,
        )
        assert result["q"] == "молоко козье 3,2%"

    async def test_search_without_preferences(self, executor):
        args = {"q": "творог"}
        result = await executor.preprocess_args(
            "vkusvill_products_search", args, {},
        )
        assert result["q"] == "творог"

    async def test_other_tool_passthrough(self, executor):
        args = {"xml_id": 123}
        result = await executor.preprocess_args(
            "vkusvill_product_details", args, {},
        )
        assert result == args

    async def test_search_preference_not_applied_if_no_match(self, executor):
        prefs = {"хлеб": "бородинский"}
        args = {"q": "молоко"}
        result = await executor.preprocess_args(
            "vkusvill_products_search", args, prefs,
        )
        assert result["q"] == "молоко"


# ============================================================================
# is_duplicate_call / CallTracker
# ============================================================================


class TestIsDuplicateCall:
    """Тесты is_duplicate_call: обнаружение зацикливания."""

    def test_first_call_not_duplicate(self, executor):
        tracker = CallTracker()
        history: list[Messages] = []

        is_dup = executor.is_duplicate_call(
            "vkusvill_products_search", {"q": "молоко"},
            tracker, history,
        )
        assert is_dup is False
        assert len(history) == 0

    def test_second_call_returns_cached_result(self, executor):
        tracker = CallTracker()
        history: list[Messages] = []
        args = {"q": "молоко"}

        executor.is_duplicate_call(
            "vkusvill_products_search", args, tracker, history,
        )

        cached = json.dumps({"ok": True, "data": {"items": [{"xml_id": 123}]}})
        tracker.record_result("vkusvill_products_search", args, cached)

        is_dup = executor.is_duplicate_call(
            "vkusvill_products_search", args, tracker, history,
        )

        assert is_dup is True
        assert len(history) == 1
        assert history[0].role == MessagesRole.FUNCTION
        content = json.loads(history[0].content)
        assert content["ok"] is True

    def test_second_call_without_cached_result(self, executor):
        tracker = CallTracker()
        history: list[Messages] = []
        args = {"q": "молоко"}

        executor.is_duplicate_call(
            "vkusvill_products_search", args, tracker, history,
        )
        is_dup = executor.is_duplicate_call(
            "vkusvill_products_search", args, tracker, history,
        )

        assert is_dup is True
        content = json.loads(history[0].content)
        assert content["ok"] is True

    def test_different_args_not_duplicate(self, executor):
        tracker = CallTracker()
        history: list[Messages] = []

        executor.is_duplicate_call(
            "vkusvill_products_search", {"q": "молоко"}, tracker, history,
        )
        is_dup = executor.is_duplicate_call(
            "vkusvill_products_search", {"q": "хлеб"}, tracker, history,
        )

        assert is_dup is False
        assert len(history) == 0

    def test_different_tool_not_duplicate(self, executor):
        tracker = CallTracker()
        history: list[Messages] = []
        args = {"q": "молоко"}

        executor.is_duplicate_call(
            "vkusvill_products_search", args, tracker, history,
        )
        is_dup = executor.is_duplicate_call(
            "vkusvill_product_details", args, tracker, history,
        )

        assert is_dup is False


# ============================================================================
# execute
# ============================================================================


class TestExecute:
    """Тесты execute: выполнение инструментов."""

    async def test_mcp_tool(self, executor, mock_mcp_client):
        mock_mcp_client.call_tool.return_value = '{"ok": true}'

        result = await executor.execute(
            "vkusvill_products_search", {"q": "молоко"}, user_id=1,
        )

        assert result == '{"ok": true}'
        mock_mcp_client.call_tool.assert_called_once()

    async def test_local_tool(self, executor_with_prefs, mock_prefs_store):
        result = await executor_with_prefs.execute(
            "user_preferences_get", {}, user_id=42,
        )
        mock_prefs_store.get_formatted.assert_called_once_with(42)
        assert '"ok": true' in result

    async def test_mcp_error_returns_json(self, executor, mock_mcp_client):
        mock_mcp_client.call_tool.side_effect = RuntimeError("MCP down")

        result = await executor.execute(
            "vkusvill_products_search", {"q": "тест"}, user_id=1,
        )

        parsed = json.loads(result)
        assert "error" in parsed
        assert "MCP down" in parsed["error"]


# ============================================================================
# postprocess_result
# ============================================================================


class TestPostprocessResult:
    """Тесты postprocess_result: постобработка результата."""

    async def test_preferences_get_parsed(self, executor):
        prefs_result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "молоко", "preference": "козье"},
            ],
        })
        user_prefs: dict[str, str] = {}
        search_log: dict[str, set[int]] = {}

        result = await executor.postprocess_result(
            "user_preferences_get", {}, prefs_result,
            user_prefs, search_log,
        )

        assert user_prefs == {"молоко": "козье"}
        assert result == prefs_result

    async def test_search_caches_and_trims(self, executor):
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

        result = await executor.postprocess_result(
            "vkusvill_products_search",
            {"q": "молоко"},
            search_result,
            user_prefs,
            search_log,
        )

        assert 100 in executor._search_processor.price_cache
        parsed = json.loads(result)
        assert "description" not in parsed["data"]["items"][0]
        assert "молоко" in search_log
        assert 100 in search_log["молоко"]

    async def test_cart_calculates_total(self, executor, search_processor):
        search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}

        result = await executor.postprocess_result(
            "vkusvill_cart_link_create", args, cart_result, {}, {},
        )

        parsed = json.loads(result)
        assert "price_summary" in parsed["data"]
        assert parsed["data"]["price_summary"]["total"] == 158.0

    async def test_unknown_tool_passthrough(self, executor):
        result = await executor.postprocess_result(
            "unknown_tool", {}, '{"some": "data"}', {}, {},
        )
        assert result == '{"some": "data"}'

    async def test_cart_with_verification(self, executor, search_processor):
        """vkusvill_cart_link_create добавляет verification если есть search_log."""
        search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}
        search_log = {"молоко": {100}}

        result = await executor.postprocess_result(
            "vkusvill_cart_link_create", args, cart_result, {}, search_log,
        )

        parsed = json.loads(result)
        assert "verification" in parsed["data"]
        assert parsed["data"]["verification"]["ok"] is True

    async def test_search_empty_query_not_logged(self, executor):
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

        await executor.postprocess_result(
            "vkusvill_products_search", {"q": ""}, search_result, {}, search_log,
        )

        assert "" not in search_log

    async def test_preferences_replaces_existing(self, executor):
        """Повторная загрузка предпочтений заменяет старые."""
        user_prefs = {"старое": "значение"}
        prefs_result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "новое", "preference": "значение"},
            ],
        })

        await executor.postprocess_result(
            "user_preferences_get", {}, prefs_result, user_prefs, {},
        )

        assert "старое" not in user_prefs
        assert user_prefs == {"новое": "значение"}


# ============================================================================
# _call_local_tool
# ============================================================================


class TestCallLocalTool:
    """Тесты _call_local_tool: маршрутизация предпочтений."""

    async def test_preferences_get(self, executor_with_prefs, mock_prefs_store):
        result = await executor_with_prefs._call_local_tool(
            "user_preferences_get", {}, user_id=42,
        )
        mock_prefs_store.get_formatted.assert_called_once_with(42)
        parsed = json.loads(result)
        assert parsed["ok"] is True

    async def test_preferences_set(self, executor_with_prefs, mock_prefs_store):
        result = await executor_with_prefs._call_local_tool(
            "user_preferences_set",
            {"category": "мороженое", "preference": "пломбир"},
            user_id=42,
        )
        mock_prefs_store.set.assert_called_once_with(42, "мороженое", "пломбир")
        assert "Запомнил" in result

    async def test_preferences_delete(self, executor_with_prefs, mock_prefs_store):
        result = await executor_with_prefs._call_local_tool(
            "user_preferences_delete",
            {"category": "мороженое"},
            user_id=42,
        )
        mock_prefs_store.delete.assert_called_once_with(42, "мороженое")
        assert "удалено" in result

    async def test_set_missing_category(self, executor_with_prefs):
        result = await executor_with_prefs._call_local_tool(
            "user_preferences_set",
            {"preference": "пломбир"},
            user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_no_store_returns_error(self, executor):
        result = await executor._call_local_tool(
            "user_preferences_get", {}, user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "не настроено" in parsed["error"]

    async def test_unknown_tool(self, executor_with_prefs):
        result = await executor_with_prefs._call_local_tool(
            "unknown_tool", {}, user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_recipe_without_service_returns_error(self, executor):
        result = await executor._call_local_tool(
            "recipe_ingredients", {"dish": "борщ"}, user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "не настроен" in parsed["error"]

    async def test_set_missing_preference(self, executor_with_prefs):
        """set без предпочтения возвращает ошибку."""
        result = await executor_with_prefs._call_local_tool(
            "user_preferences_set",
            {"category": "мороженое"},
            user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_delete_missing_category(self, executor_with_prefs):
        """delete без категории возвращает ошибку."""
        result = await executor_with_prefs._call_local_tool(
            "user_preferences_delete",
            {},
            user_id=42,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is False


# ============================================================================
# _parse_preferences
# ============================================================================


class TestParsePreferences:
    """Тесты _parse_preferences."""

    def test_valid_result(self):
        result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "вареники", "preference": "с картофелем и шкварками"},
                {"category": "Молоко", "preference": "безлактозное 2,5%"},
            ],
        })
        prefs = ToolExecutor._parse_preferences(result)
        assert prefs == {
            "вареники": "с картофелем и шкварками",
            "молоко": "безлактозное 2,5%",
        }

    def test_empty_preferences(self):
        result = json.dumps({"ok": True, "preferences": []})
        assert ToolExecutor._parse_preferences(result) == {}

    def test_invalid_json(self):
        assert ToolExecutor._parse_preferences("not json") == {}

    def test_preferences_not_list(self):
        result = json.dumps({"ok": True, "preferences": "not-a-list"})
        assert ToolExecutor._parse_preferences(result) == {}

    def test_non_dict_items(self):
        result = json.dumps({
            "ok": True,
            "preferences": ["string", 42, None, {"category": "молоко", "preference": "козье"}],
        })
        assert ToolExecutor._parse_preferences(result) == {"молоко": "козье"}

    def test_preferences_is_dict(self):
        """preferences — словарь вместо списка → пустой словарь."""
        result = json.dumps({"ok": True, "preferences": {"key": "value"}})
        assert ToolExecutor._parse_preferences(result) == {}

    def test_preferences_is_none(self):
        """preferences=None → пустой словарь."""
        result = json.dumps({"ok": True, "preferences": None})
        assert ToolExecutor._parse_preferences(result) == {}

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
        prefs = ToolExecutor._parse_preferences(result)
        assert prefs == {"молоко": "козье"}

    def test_case_normalization(self):
        """Категория приводится к lower case."""
        result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "Мороженое", "preference": "пломбир"},
            ],
        })
        prefs = ToolExecutor._parse_preferences(result)
        assert "мороженое" in prefs


# ============================================================================
# _apply_preferences_to_query
# ============================================================================


class TestApplyPreferencesToQuery:
    """Тесты _apply_preferences_to_query."""

    @pytest.fixture
    def prefs(self) -> dict[str, str]:
        return {
            "вареники": "с картофелем и шкварками",
            "молоко": "Молоко безлактозное 2,5%, 900 мл",
            "эскимо": "пломбир ванильный в молочном шоколаде, 70 г",
        }

    def test_exact_match(self, prefs):
        result = ToolExecutor._apply_preferences_to_query("вареники", prefs)
        assert result == "вареники с картофелем и шкварками"

    def test_category_contained_in_preference(self, prefs):
        result = ToolExecutor._apply_preferences_to_query("молоко", prefs)
        assert result == "Молоко безлактозное 2,5%, 900 мл"

    def test_no_match(self, prefs):
        result = ToolExecutor._apply_preferences_to_query("творог", prefs)
        assert result == "творог"

    def test_empty_prefs(self):
        result = ToolExecutor._apply_preferences_to_query("молоко", {})
        assert result == "молоко"

    def test_empty_query(self, prefs):
        result = ToolExecutor._apply_preferences_to_query("", prefs)
        assert result == ""

    def test_exact_match_case_insensitive(self, prefs):
        """Регистронезависимое совпадение."""
        result = ToolExecutor._apply_preferences_to_query("Вареники", prefs)
        assert result == "Вареники с картофелем и шкварками"

    def test_partial_match(self, prefs):
        """Запрос содержится в категории: 'эскимо' → подстановка."""
        result = ToolExecutor._apply_preferences_to_query("эскимо", prefs)
        assert "пломбир ванильный" in result

    def test_specific_query_not_overridden(self, prefs):
        """Уточнённый запрос НЕ заменяется предпочтением."""
        result = ToolExecutor._apply_preferences_to_query("молоко козье", prefs)
        assert result == "молоко козье"

    def test_real_case_ice_cream(self):
        """Реальный кейс: 'мороженое' при предпочтении category='мороженое'."""
        prefs = {"мороженое": "пломбир ванильный в молочном шоколаде"}
        result = ToolExecutor._apply_preferences_to_query("мороженое", prefs)
        assert result == "мороженое пломбир ванильный в молочном шоколаде"

    def test_real_case_milk(self):
        """Реальный кейс: 'молоко' при предпочтении с полным названием."""
        prefs = {"молоко": "Молоко безлактозное 2,5%, 900 мл"}
        result = ToolExecutor._apply_preferences_to_query("молоко", prefs)
        assert result == "Молоко безлактозное 2,5%, 900 мл"


# ============================================================================
# CallTracker
# ============================================================================


class TestCallTracker:
    """Тесты CallTracker."""

    def test_make_key(self):
        tracker = CallTracker()
        key = tracker.make_key("tool_name", {"a": 1, "b": 2})
        assert "tool_name" in key
        assert '"a": 1' in key

    def test_record_and_retrieve_result(self):
        tracker = CallTracker()
        tracker.record_result("tool", {"q": "test"}, '{"ok": true}')
        key = tracker.make_key("tool", {"q": "test"})
        assert tracker.call_results[key] == '{"ok": true}'


# ============================================================================
# Тесты preprocess_args: очистка запроса + fix_cart_args + limit
# ============================================================================

class TestPreprocessArgsSearchCleaning:
    """Тесты предобработки аргументов поиска в preprocess_args."""

    @pytest.fixture
    def executor(self):
        mcp = AsyncMock()
        sp = MagicMock()
        sp.clean_search_query = MagicMock(side_effect=lambda q: q.split()[0] if " " in q else q)
        cp = MagicMock()
        cp.fix_unit_quantities = AsyncMock(side_effect=lambda x: x)
        return ToolExecutor(mcp_client=mcp, search_processor=sp, cart_processor=cp)

    async def test_search_query_cleaned(self, executor):
        """Поисковый запрос очищается через SearchProcessor."""
        args = await executor.preprocess_args(
            "vkusvill_products_search", {"q": "Творог 5%"}, {},
        )
        executor._search_processor.clean_search_query.assert_called_once_with("Творог 5%")
        assert args["q"] == "Творог"

    async def test_search_limit_added(self, executor):
        """limit автоматически добавляется к поиску."""
        args = await executor.preprocess_args(
            "vkusvill_products_search", {"q": "молоко"}, {},
        )
        assert "limit" in args
        assert args["limit"] == 5

    async def test_search_limit_not_overwritten(self, executor):
        """Если limit уже есть — не перезаписывается."""
        args = await executor.preprocess_args(
            "vkusvill_products_search", {"q": "молоко", "limit": 20}, {},
        )
        assert args["limit"] == 20

    async def test_cart_fix_cart_args_called(self, executor):
        """fix_cart_args вызывается для корзины."""
        executor._cart_processor.fix_cart_args = MagicMock(side_effect=lambda x: x)
        executor._cart_processor.fix_unit_quantities = AsyncMock(side_effect=lambda x: x)
        await executor.preprocess_args(
            "vkusvill_cart_link_create", {"products": [{"xml_id": 1}]}, {},
        )
        executor._cart_processor.fix_cart_args.assert_called_once()
        executor._cart_processor.fix_unit_quantities.assert_called_once()


# ============================================================================
# postprocess_result с cart_snapshot_store
# ============================================================================


class TestPostprocessResultWithSnapshot:
    """Тесты postprocess_result: сохранение снимка корзины через CartSnapshotStore."""

    @pytest.fixture
    def mock_snapshot_store(self) -> AsyncMock:
        """Замоканный CartSnapshotStore."""
        store = AsyncMock()
        store.save = AsyncMock()
        return store

    @pytest.fixture
    def executor_with_snapshot(
        self, mock_mcp_client, search_processor, cart_processor, mock_snapshot_store,
    ) -> ToolExecutor:
        """ToolExecutor с CartSnapshotStore."""
        return ToolExecutor(
            mcp_client=mock_mcp_client,
            search_processor=search_processor,
            cart_processor=cart_processor,
            cart_snapshot_store=mock_snapshot_store,
        )

    async def test_cart_saves_snapshot(self, executor_with_snapshot, mock_snapshot_store, search_processor):
        """При создании корзины снимок сохраняется, если есть user_id и store."""
        search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}

        await executor_with_snapshot.postprocess_result(
            "vkusvill_cart_link_create", args, cart_result, {}, {},
            user_id=42,
        )

        mock_snapshot_store.save.assert_called_once()
        call_kwargs = mock_snapshot_store.save.call_args.kwargs
        assert call_kwargs["user_id"] == 42
        assert call_kwargs["products"] == [{"xml_id": 100, "q": 2}]
        assert "vkusvill.ru" in call_kwargs["link"]
        assert call_kwargs["total"] == 158.0

    async def test_cart_no_snapshot_without_user_id(
        self, executor_with_snapshot, mock_snapshot_store, search_processor,
    ):
        """Без user_id снимок НЕ сохраняется (обратная совместимость)."""
        search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}

        await executor_with_snapshot.postprocess_result(
            "vkusvill_cart_link_create", args, cart_result, {}, {},
        )

        mock_snapshot_store.save.assert_not_called()

    async def test_cart_no_snapshot_without_store(self, executor, search_processor):
        """Без cart_snapshot_store снимок НЕ сохраняется."""
        search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}

        # executor без cart_snapshot_store — не должно упасть
        result = await executor.postprocess_result(
            "vkusvill_cart_link_create", args, cart_result, {}, {},
            user_id=42,
        )

        parsed = json.loads(result)
        assert "price_summary" in parsed["data"]

    async def test_snapshot_not_for_search(
        self, executor_with_snapshot, mock_snapshot_store,
    ):
        """Снимок НЕ сохраняется для vkusvill_products_search."""
        search_result = json.dumps({
            "ok": True,
            "data": {"items": [{"xml_id": 100, "name": "Молоко", "price": {"current": 79}, "unit": "шт"}]},
        })

        await executor_with_snapshot.postprocess_result(
            "vkusvill_products_search", {"q": "молоко"}, search_result, {}, {},
            user_id=42,
        )

        mock_snapshot_store.save.assert_not_called()


# ============================================================================
# _save_cart_snapshot: извлечение данных из результата корзины
# ============================================================================


class TestSaveCartSnapshot:
    """Тесты _save_cart_snapshot: извлечение и сохранение снимка."""

    @pytest.fixture
    def mock_snapshot_store(self) -> AsyncMock:
        store = AsyncMock()
        store.save = AsyncMock()
        return store

    @pytest.fixture
    def executor_with_snapshot(
        self, mock_mcp_client, search_processor, cart_processor, mock_snapshot_store,
    ) -> ToolExecutor:
        return ToolExecutor(
            mcp_client=mock_mcp_client,
            search_processor=search_processor,
            cart_processor=cart_processor,
            cart_snapshot_store=mock_snapshot_store,
        )

    async def test_extracts_link_and_total(self, executor_with_snapshot, mock_snapshot_store):
        """Извлекает link и total из результата корзины."""
        result = json.dumps({
            "ok": True,
            "data": {
                "link": "https://vkusvill.ru/?share_basket=abc",
                "price_summary": {"total": 500.0, "total_text": "500.00 ₽"},
            },
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}

        await executor_with_snapshot._save_cart_snapshot(42, args, result)

        mock_snapshot_store.save.assert_called_once_with(
            user_id=42,
            products=[{"xml_id": 100, "q": 2}],
            link="https://vkusvill.ru/?share_basket=abc",
            total=500.0,
        )

    async def test_handles_missing_price_summary(self, executor_with_snapshot, mock_snapshot_store):
        """Без price_summary — total=None."""
        result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=abc"},
        })
        args = {"products": [{"xml_id": 100, "q": 1}]}

        await executor_with_snapshot._save_cart_snapshot(42, args, result)

        call_kwargs = mock_snapshot_store.save.call_args.kwargs
        assert call_kwargs["total"] is None
        assert call_kwargs["link"] == "https://vkusvill.ru/?share_basket=abc"

    async def test_handles_invalid_json(self, executor_with_snapshot, mock_snapshot_store):
        """Невалидный JSON — link="", total=None, но не крашит."""
        args = {"products": [{"xml_id": 100}]}

        await executor_with_snapshot._save_cart_snapshot(42, args, "not json")

        mock_snapshot_store.save.assert_called_once()
        call_kwargs = mock_snapshot_store.save.call_args.kwargs
        assert call_kwargs["link"] == ""
        assert call_kwargs["total"] is None

    async def test_handles_data_not_dict(self, executor_with_snapshot, mock_snapshot_store):
        """data — не dict → link="", total=None."""
        result = json.dumps({"ok": True, "data": "string"})
        args = {"products": []}

        await executor_with_snapshot._save_cart_snapshot(42, args, result)

        call_kwargs = mock_snapshot_store.save.call_args.kwargs
        assert call_kwargs["link"] == ""
        assert call_kwargs["total"] is None

    async def test_empty_products(self, executor_with_snapshot, mock_snapshot_store):
        """Пустой список products передаётся корректно."""
        result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=xyz"},
        })

        await executor_with_snapshot._save_cart_snapshot(42, {"products": []}, result)

        call_kwargs = mock_snapshot_store.save.call_args.kwargs
        assert call_kwargs["products"] == []

    async def test_price_summary_not_dict(self, executor_with_snapshot, mock_snapshot_store):
        """price_summary — не dict → total=None."""
        result = json.dumps({
            "ok": True,
            "data": {
                "link": "https://vkusvill.ru/?share_basket=abc",
                "price_summary": "invalid",
            },
        })
        args = {"products": []}

        await executor_with_snapshot._save_cart_snapshot(42, args, result)

        call_kwargs = mock_snapshot_store.save.call_args.kwargs
        assert call_kwargs["total"] is None


# ============================================================================
# get_previous_cart
# ============================================================================


class TestGetPreviousCart:
    """Тесты get_previous_cart: получение предыдущей корзины."""

    @pytest.fixture
    def mock_snapshot_store(self):
        """Мок CartSnapshotStore."""
        store = AsyncMock()
        store.get = AsyncMock(return_value=None)
        return store

    @pytest.fixture
    def executor_with_cart(
        self, mock_mcp_client, search_processor, cart_processor, mock_snapshot_store,
    ):
        """ToolExecutor с CartSnapshotStore."""
        return ToolExecutor(
            mcp_client=mock_mcp_client,
            search_processor=search_processor,
            cart_processor=cart_processor,
            cart_snapshot_store=mock_snapshot_store,
        )

    async def test_returns_no_cart_when_store_none(self, executor):
        """Без cart_snapshot_store — возвращает 'недоступна'."""
        result = await executor._get_previous_cart(user_id=42)
        data = json.loads(result)
        assert data["ok"] is False
        assert "недоступна" in data["message"]

    async def test_returns_no_cart_when_empty(
        self, executor_with_cart, mock_snapshot_store,
    ):
        """Если корзины нет — возвращает 'нет предыдущей корзины'."""
        mock_snapshot_store.get.return_value = None
        result = await executor_with_cart._get_previous_cart(user_id=42)
        data = json.loads(result)
        assert data["ok"] is True
        assert "нет предыдущей корзины" in data["message"]

    async def test_returns_previous_cart(
        self, executor_with_cart, mock_snapshot_store, search_processor,
    ):
        """Возвращает снимок корзины с обогащёнными данными."""
        # Добавляем цены в кеш
        search_processor.price_cache._set_sync(100, "Молоко", 89.0, "шт")
        mock_snapshot_store.get.return_value = {
            "products": [{"xml_id": 100, "q": 2}],
            "link": "https://vkusvill.ru/?share_basket=123",
            "total": 178.0,
            "created_at": "2026-02-10T12:00:00+00:00",
        }

        result = await executor_with_cart._get_previous_cart(user_id=42)
        data = json.loads(result)

        assert data["ok"] is True
        assert len(data["products"]) == 1
        assert data["products"][0]["xml_id"] == 100
        assert data["products"][0]["q"] == 2
        assert data["products"][0]["name"] == "Молоко"
        assert data["products"][0]["price"] == 89.0
        assert data["link"] == "https://vkusvill.ru/?share_basket=123"
        assert data["total"] == 178.0

    async def test_enriches_products_without_cache(
        self, executor_with_cart, mock_snapshot_store,
    ):
        """Товары без кеша цен — возвращает xml_id и q без name/price."""
        mock_snapshot_store.get.return_value = {
            "products": [{"xml_id": 999, "q": 1}],
            "link": "",
            "total": None,
            "created_at": "",
        }

        result = await executor_with_cart._get_previous_cart(user_id=42)
        data = json.loads(result)

        assert data["ok"] is True
        assert data["products"][0]["xml_id"] == 999
        assert "name" not in data["products"][0]

    async def test_routes_via_call_local_tool(
        self, executor_with_cart,
    ):
        """get_previous_cart маршрутизируется через _call_local_tool."""
        result = await executor_with_cart._call_local_tool(
            "get_previous_cart", {}, user_id=42,
        )
        data = json.loads(result)
        assert data["ok"] is True
        assert "нет предыдущей корзины" in data["message"]
