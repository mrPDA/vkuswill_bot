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

    def test_cart_fix_applied(self, executor, search_processor):
        search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        args = {"products": [{"xml_id": 100, "q": 0.5}]}
        result = executor.preprocess_args(
            "vkusvill_cart_link_create", args, {},
        )
        assert result["products"][0]["q"] == 1

    def test_search_with_preferences(self, executor):
        prefs = {"молоко": "козье 3,2%"}
        args = {"q": "молоко"}
        result = executor.preprocess_args(
            "vkusvill_products_search", args, prefs,
        )
        assert result["q"] == "молоко козье 3,2%"

    def test_search_without_preferences(self, executor):
        args = {"q": "творог"}
        result = executor.preprocess_args(
            "vkusvill_products_search", args, {},
        )
        assert result["q"] == "творог"

    def test_other_tool_passthrough(self, executor):
        args = {"xml_id": 123}
        result = executor.preprocess_args(
            "vkusvill_product_details", args, {},
        )
        assert result == args

    def test_search_preference_not_applied_if_no_match(self, executor):
        prefs = {"хлеб": "бородинский"}
        args = {"q": "молоко"}
        result = executor.preprocess_args(
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

    def test_preferences_get_parsed(self, executor):
        prefs_result = json.dumps({
            "ok": True,
            "preferences": [
                {"category": "молоко", "preference": "козье"},
            ],
        })
        user_prefs: dict[str, str] = {}
        search_log: dict[str, set[int]] = {}

        result = executor.postprocess_result(
            "user_preferences_get", {}, prefs_result,
            user_prefs, search_log,
        )

        assert user_prefs == {"молоко": "козье"}
        assert result == prefs_result

    def test_search_caches_and_trims(self, executor):
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

        result = executor.postprocess_result(
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

    def test_cart_calculates_total(self, executor, search_processor):
        search_processor.price_cache[100] = {
            "name": "Молоко", "price": 79, "unit": "шт",
        }
        cart_result = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })
        args = {"products": [{"xml_id": 100, "q": 2}]}

        result = executor.postprocess_result(
            "vkusvill_cart_link_create", args, cart_result, {}, {},
        )

        parsed = json.loads(result)
        assert "price_summary" in parsed["data"]
        assert parsed["data"]["price_summary"]["total"] == 158.0

    def test_unknown_tool_passthrough(self, executor):
        result = executor.postprocess_result(
            "unknown_tool", {}, '{"some": "data"}', {}, {},
        )
        assert result == '{"some": "data"}'


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
