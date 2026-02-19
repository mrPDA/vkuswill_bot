"""Тесты MCP-сервера ВкусВилл.

Тестируем:
- Регистрация инструментов (все 7 штук)
- search_products: препроцессинг, execute, постпроцессинг, ограничение limit
- create_cart: препроцессинг, execute, постпроцессинг, user_id
- get_previous_cart: вызов execute с правильными аргументами
- get_preferences: делегирование в prefs_store.get_formatted
- set_preference: делегирование в prefs_store.set
- delete_preference: делегирование в prefs_store.delete
- lookup_nutrition: вызов execute с nutrition_lookup, маппинг product_name → query
- _lifespan: инициализация и завершение сервисов
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vkuswill_bot.mcp_server import server
from vkuswill_bot.mcp_server.server import (
    _lifespan,
    create_cart,
    delete_preference,
    get_preferences,
    get_previous_cart,
    lookup_nutrition,
    mcp,
    search_products,
    set_preference,
)

# ---------------------------------------------------------------------------
# Тестовые данные
# ---------------------------------------------------------------------------

SEARCH_RESULT = json.dumps(
    {
        "ok": True,
        "products": [{"xml_id": 111, "name": "Молоко", "price": 89.9, "unit": "л"}],
    },
    ensure_ascii=False,
)

CART_RESULT = json.dumps(
    {
        "ok": True,
        "data": {
            "link": "https://vkusvill.ru/cart/?token=abc123",
            "price_summary": {"total": 179.8},
        },
    },
    ensure_ascii=False,
)

PREV_CART_RESULT = json.dumps(
    {
        "ok": True,
        "products": [{"xml_id": 111, "name": "Молоко", "q": 2}],
        "link": "https://vkusvill.ru/cart/?token=abc123",
        "total": 179.8,
    },
    ensure_ascii=False,
)

PREFS_RESULT = json.dumps(
    {
        "ok": True,
        "preferences": [{"category": "молоко", "preference": "безлактозное"}],
    },
    ensure_ascii=False,
)

NUTRITION_RESULT = json.dumps(
    {"ok": True, "product": "гречка", "calories": 313, "protein": 12.6},
    ensure_ascii=False,
)


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_tool_executor(
    *,
    search_result: str = SEARCH_RESULT,
    cart_result: str = CART_RESULT,
    prev_cart_result: str = PREV_CART_RESULT,
    nutrition_result: str = NUTRITION_RESULT,
) -> AsyncMock:
    """Создать мок ToolExecutor с настроенными возвратами."""
    executor = AsyncMock()

    async def _preprocess(tool_name: str, args: dict, user_prefs: dict) -> dict:
        return args

    async def _execute(tool_name: str, args: dict, user_id: int) -> str:
        mapping = {
            "vkusvill_products_search": search_result,
            "vkusvill_cart_link_create": cart_result,
            "get_previous_cart": prev_cart_result,
            "nutrition_lookup": nutrition_result,
        }
        return mapping.get(tool_name, json.dumps({"ok": True}, ensure_ascii=False))

    async def _postprocess(
        tool_name: str,
        args: dict,
        result: str,
        user_prefs: dict,
        search_log: dict,
        user_id: int | None = None,
    ) -> str:
        return result

    executor.preprocess_args.side_effect = _preprocess
    executor.execute.side_effect = _execute
    executor.postprocess_result.side_effect = _postprocess

    return executor


def _make_prefs_store() -> AsyncMock:
    """Создать мок PreferencesStore."""
    store = AsyncMock()
    store.get_formatted.return_value = PREFS_RESULT
    store.set.return_value = json.dumps({"ok": True, "message": "Запомнил"}, ensure_ascii=False)
    store.delete.return_value = json.dumps(
        {"ok": True, "message": "Удалено"}, ensure_ascii=False
    )
    return store


def _make_services(
    *,
    tool_executor: AsyncMock | None = None,
    prefs_store: AsyncMock | None = None,
) -> MagicMock:
    """Создать мок _Services."""
    svc = MagicMock(spec=server._Services)
    svc.tool_executor = tool_executor or _make_tool_executor()
    svc.prefs_store = prefs_store or _make_prefs_store()
    return svc


def _extract_user_id(call_args) -> int | None:
    """Извлечь user_id из call_args (keyword или позиционный аргумент [2])."""
    if call_args.kwargs.get("user_id") is not None:
        return call_args.kwargs["user_id"]
    if len(call_args.args) > 2:
        return call_args.args[2]
    return None


def _make_ctx(services: MagicMock) -> MagicMock:
    """Создать мок Context с lifespan_context и request=None (stdio)."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = services
    ctx.request_context.request = None
    return ctx


def _make_http_ctx(services: MagicMock, headers: dict[str, str]) -> MagicMock:
    """Создать мок Context с HTTP request headers."""
    ctx = _make_ctx(services)
    req = MagicMock()
    req.headers = headers
    ctx.request_context.request = req
    return ctx


def _assert_error(result: str, code: str) -> None:
    """Проверить унифицированный JSON-ответ об ошибке."""
    data = json.loads(result)
    assert data["ok"] is False
    assert data["code"] == code


# ---------------------------------------------------------------------------
# TestToolRegistration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Все инструменты зарегистрированы в FastMCP."""

    def test_tool_count(self) -> None:
        tools = mcp._tool_manager.list_tools()
        assert len(tools) == 7

    def test_tool_names(self) -> None:
        names = {t.name for t in mcp._tool_manager.list_tools()}
        assert names == {
            "search_products",
            "create_cart",
            "get_previous_cart",
            "get_preferences",
            "set_preference",
            "delete_preference",
            "lookup_nutrition",
        }

    def test_server_name(self) -> None:
        assert mcp.name == "vkuswill-bot"


# ---------------------------------------------------------------------------
# TestSearchProducts
# ---------------------------------------------------------------------------


class TestSearchProducts:
    """Тесты search_products."""

    async def test_basic_call_returns_search_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await search_products(ctx, query="молоко", limit=5)

        assert result == SEARCH_RESULT

    async def test_calls_preprocess_args(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await search_products(ctx, query="молоко 3.2% 1 л", limit=10)

        svc.tool_executor.preprocess_args.assert_awaited_once()
        call_args = svc.tool_executor.preprocess_args.call_args
        assert call_args.args[0] == "vkusvill_products_search"
        assert call_args.args[1]["q"] == "молоко 3.2% 1 л"

    async def test_calls_execute_with_correct_tool_name(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await search_products(ctx, query="хлеб", limit=10)

        svc.tool_executor.execute.assert_awaited_once()
        assert svc.tool_executor.execute.call_args.args[0] == "vkusvill_products_search"

    async def test_execute_user_id_is_zero(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await search_products(ctx, query="сыр")

        assert _extract_user_id(svc.tool_executor.execute.call_args) == 0

    async def test_calls_postprocess_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await search_products(ctx, query="масло")

        svc.tool_executor.postprocess_result.assert_awaited_once()
        assert svc.tool_executor.postprocess_result.call_args.args[0] == "vkusvill_products_search"

    @pytest.mark.parametrize(
        "limit_in, limit_expected",
        [
            (10, 10),
            (1, 1),
            (30, 30),
            (0, 1),       # clamped to min
            (-5, 1),      # clamped to min
            (31, 30),     # clamped to max
            (100, 30),    # clamped to max
        ],
    )
    async def test_limit_clamping(self, limit_in: int, limit_expected: int) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await search_products(ctx, query="молоко", limit=limit_in)

        call_args = svc.tool_executor.preprocess_args.call_args
        assert call_args.args[1]["limit"] == limit_expected

    async def test_default_limit_is_10(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await search_products(ctx, query="кефир")

        call_args = svc.tool_executor.preprocess_args.call_args
        assert call_args.args[1]["limit"] == 10


# ---------------------------------------------------------------------------
# TestCreateCart
# ---------------------------------------------------------------------------


class TestCreateCart:
    """Тесты create_cart."""

    async def test_basic_call_returns_cart_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)
        products = [{"xml_id": 111, "q": 2}]

        result = await create_cart(ctx, products=products, user_id=1)

        assert result == CART_RESULT

    async def test_products_passed_to_preprocess(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)
        products = [{"xml_id": 111, "q": 2}, {"xml_id": 222, "q": 1}]

        await create_cart(ctx, products=products, user_id=1)

        call_args = svc.tool_executor.preprocess_args.call_args
        assert call_args.args[0] == "vkusvill_cart_link_create"
        assert call_args.args[1]["products"] == products

    async def test_user_id_propagated_to_execute(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await create_cart(ctx, products=[], user_id=42)

        assert _extract_user_id(svc.tool_executor.execute.call_args) == 42

    async def test_default_user_id_is_rejected(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await create_cart(ctx, products=[])
        _assert_error(result, "invalid_user_id")
        svc.tool_executor.execute.assert_not_awaited()

    async def test_calls_postprocess_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await create_cart(ctx, products=[{"xml_id": 111, "q": 1}], user_id=1)

        svc.tool_executor.postprocess_result.assert_awaited_once()
        assert svc.tool_executor.postprocess_result.call_args.args[0] == "vkusvill_cart_link_create"

    async def test_user_id_propagated_to_postprocess(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await create_cart(ctx, products=[], user_id=99)

        post_call = svc.tool_executor.postprocess_result.call_args
        # user_id — последний позиционный аргумент или keyword
        assert post_call.kwargs.get("user_id") == 99 or post_call.args[-1] == 99


# ---------------------------------------------------------------------------
# TestGetPreviousCart
# ---------------------------------------------------------------------------


class TestGetPreviousCart:
    """Тесты get_previous_cart."""

    async def test_returns_executor_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await get_previous_cart(ctx, user_id=7)

        assert result == PREV_CART_RESULT

    async def test_calls_execute_with_get_previous_cart(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await get_previous_cart(ctx, user_id=7)

        svc.tool_executor.execute.assert_awaited_once_with(
            "get_previous_cart", {}, user_id=7
        )

    async def test_user_id_passed_correctly(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await get_previous_cart(ctx, user_id=42)

        call_args = svc.tool_executor.execute.call_args
        assert call_args.kwargs["user_id"] == 42

    async def test_does_not_call_preprocess(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await get_previous_cart(ctx, user_id=1)

        svc.tool_executor.preprocess_args.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestGetPreferences
# ---------------------------------------------------------------------------


class TestGetPreferences:
    """Тесты get_preferences."""

    async def test_returns_prefs_store_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await get_preferences(ctx, user_id=5)

        assert result == PREFS_RESULT

    async def test_calls_get_formatted_with_user_id(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await get_preferences(ctx, user_id=5)

        svc.prefs_store.get_formatted.assert_awaited_once_with(5)

    async def test_does_not_call_tool_executor(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await get_preferences(ctx, user_id=5)

        svc.tool_executor.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestSetPreference
# ---------------------------------------------------------------------------


class TestSetPreference:
    """Тесты set_preference."""

    async def test_returns_prefs_store_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await set_preference(ctx, user_id=1, category="молоко", preference="безлактозное")

        assert json.loads(result)["ok"] is True

    async def test_calls_set_with_correct_args(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await set_preference(ctx, user_id=3, category="хлеб", preference="цельнозерновой")

        svc.prefs_store.set.assert_awaited_once_with(3, "хлеб", "цельнозерновой")

    @pytest.mark.parametrize(
        "user_id, category, preference",
        [
            (1, "молоко", "безлактозное 1,5%"),
            (999, "сыр", "рикотта"),
            (10, "мороженое", "пломбир в шоколаде на палочке"),
        ],
    )
    async def test_parametrized(
        self, user_id: int, category: str, preference: str
    ) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await set_preference(ctx, user_id=user_id, category=category, preference=preference)

        svc.prefs_store.set.assert_awaited_once_with(user_id, category, preference)


# ---------------------------------------------------------------------------
# TestDeletePreference
# ---------------------------------------------------------------------------


class TestDeletePreference:
    """Тесты delete_preference."""

    async def test_returns_prefs_store_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await delete_preference(ctx, user_id=2, category="молоко")

        assert json.loads(result)["ok"] is True

    async def test_calls_delete_with_correct_args(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await delete_preference(ctx, user_id=7, category="хлеб")

        svc.prefs_store.delete.assert_awaited_once_with(7, "хлеб")

    async def test_does_not_call_tool_executor(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await delete_preference(ctx, user_id=1, category="сыр")

        svc.tool_executor.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestAccessControl
# ---------------------------------------------------------------------------


class TestAccessControl:
    """Тесты проверок прав доступа и user_id."""

    async def test_get_previous_cart_rejects_invalid_user_id(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await get_previous_cart(ctx, user_id=0)

        _assert_error(result, "invalid_user_id")
        svc.tool_executor.execute.assert_not_awaited()

    async def test_set_preference_rejects_invalid_user_id(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await set_preference(ctx, user_id=-1, category="молоко", preference="2.5%")

        _assert_error(result, "invalid_user_id")
        svc.prefs_store.set.assert_not_awaited()

    async def test_requires_api_key_for_http_when_configured(self) -> None:
        svc = _make_services()
        ctx = _make_http_ctx(svc, headers={})

        with (
            patch.object(server.config, "mcp_server_api_key", "secret-token"),
            patch.object(server.config, "mcp_server_api_keys", {}),
        ):
            result = await search_products(ctx, query="молоко")

        _assert_error(result, "forbidden")
        svc.tool_executor.preprocess_args.assert_not_awaited()

    async def test_accepts_x_mcp_api_key_header(self) -> None:
        svc = _make_services()
        ctx = _make_http_ctx(svc, headers={"x-mcp-api-key": "secret-token"})

        with (
            patch.object(server.config, "mcp_server_api_key", "secret-token"),
            patch.object(server.config, "mcp_server_api_keys", {}),
        ):
            result = await search_products(ctx, query="молоко")

        assert result == SEARCH_RESULT
        svc.tool_executor.execute.assert_awaited_once()

    async def test_accepts_authorization_bearer_header(self) -> None:
        svc = _make_services()
        ctx = _make_http_ctx(svc, headers={"authorization": "Bearer secret-token"})

        with (
            patch.object(server.config, "mcp_server_api_key", "secret-token"),
            patch.object(server.config, "mcp_server_api_keys", {}),
        ):
            result = await lookup_nutrition(ctx, product_name="гречка")

        assert result == NUTRITION_RESULT
        svc.tool_executor.execute.assert_awaited_once()

    async def test_stdio_allowed_even_when_api_key_enabled(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)  # request=None => stdio

        with (
            patch.object(server.config, "mcp_server_api_key", "secret-token"),
            patch.object(server.config, "mcp_server_api_keys", {}),
        ):
            result = await search_products(ctx, query="молоко")

        assert result == SEARCH_RESULT

    async def test_accepts_key_from_multi_client_registry(self) -> None:
        svc = _make_services()
        ctx = _make_http_ctx(svc, headers={"x-mcp-api-key": "key-client-b"})

        with (
            patch.object(server.config, "mcp_server_api_key", ""),
            patch.object(
                server.config,
                "mcp_server_api_keys",
                {"client_a": "key-client-a", "client_b": "key-client-b"},
            ),
        ):
            result = await search_products(ctx, query="молоко")

        assert result == SEARCH_RESULT
        svc.tool_executor.execute.assert_awaited_once()

    async def test_rejects_unknown_key_from_multi_client_registry(self) -> None:
        svc = _make_services()
        ctx = _make_http_ctx(svc, headers={"x-mcp-api-key": "unknown"})

        with (
            patch.object(server.config, "mcp_server_api_key", ""),
            patch.object(
                server.config,
                "mcp_server_api_keys",
                {"client_a": "key-client-a", "client_b": "key-client-b"},
            ),
        ):
            result = await search_products(ctx, query="молоко")

        _assert_error(result, "forbidden")
        svc.tool_executor.execute.assert_not_awaited()

    async def test_accepts_default_and_registry_keys_together(self) -> None:
        svc = _make_services()
        ctx = _make_http_ctx(svc, headers={"authorization": "Bearer key-client-a"})

        with (
            patch.object(server.config, "mcp_server_api_key", "legacy-single"),
            patch.object(server.config, "mcp_server_api_keys", {"client_a": "key-client-a"}),
        ):
            result = await lookup_nutrition(ctx, product_name="гречка")

        assert result == NUTRITION_RESULT
        svc.tool_executor.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestLookupNutrition
# ---------------------------------------------------------------------------


class TestLookupNutrition:
    """Тесты lookup_nutrition."""

    async def test_returns_nutrition_result(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        result = await lookup_nutrition(ctx, product_name="гречка")

        assert result == NUTRITION_RESULT

    async def test_calls_execute_with_nutrition_lookup(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await lookup_nutrition(ctx, product_name="куриная грудка")

        svc.tool_executor.execute.assert_awaited_once()
        call_args = svc.tool_executor.execute.call_args
        assert call_args.args[0] == "nutrition_lookup"

    async def test_product_name_mapped_to_query_key(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await lookup_nutrition(ctx, product_name="овсянка")

        call_args = svc.tool_executor.execute.call_args
        assert call_args.args[1] == {"query": "овсянка"}

    async def test_user_id_is_zero(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await lookup_nutrition(ctx, product_name="рис")

        assert _extract_user_id(svc.tool_executor.execute.call_args) == 0

    async def test_does_not_call_prefs_store(self) -> None:
        svc = _make_services()
        ctx = _make_ctx(svc)

        await lookup_nutrition(ctx, product_name="картофель")

        svc.prefs_store.get_formatted.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestLifespan
# ---------------------------------------------------------------------------


class TestLifespan:
    """Тесты инициализации и завершения сервисов через _lifespan."""

    async def test_yields_services_instance(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_tools.return_value = []
        mock_prefs = AsyncMock()
        mock_nutrition = AsyncMock()
        mock_executor = MagicMock()

        with (
            patch("vkuswill_bot.mcp_server.server.VkusvillMCPClient", return_value=mock_client),
            patch("vkuswill_bot.mcp_server.server.PriceCache"),
            patch("vkuswill_bot.mcp_server.server.SearchProcessor"),
            patch("vkuswill_bot.mcp_server.server.CartProcessor"),
            patch("vkuswill_bot.mcp_server.server.PreferencesStore", return_value=mock_prefs),
            patch("vkuswill_bot.mcp_server.server.InMemoryCartSnapshotStore"),
            patch("vkuswill_bot.mcp_server.server.NutritionService", return_value=mock_nutrition),
            patch("vkuswill_bot.mcp_server.server.RecipeSearchService"),
            patch("vkuswill_bot.mcp_server.server.ToolExecutor", return_value=mock_executor),
        ):
            mock_app = MagicMock()
            async with _lifespan(mock_app) as services:
                assert isinstance(services, server._Services)
                assert services.tool_executor is mock_executor
                assert services.prefs_store is mock_prefs
                assert services.mcp_client is mock_client

    async def test_cleanup_called_on_exit(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_tools.return_value = []
        mock_prefs = AsyncMock()
        mock_nutrition = AsyncMock()

        with (
            patch("vkuswill_bot.mcp_server.server.VkusvillMCPClient", return_value=mock_client),
            patch("vkuswill_bot.mcp_server.server.PriceCache"),
            patch("vkuswill_bot.mcp_server.server.SearchProcessor"),
            patch("vkuswill_bot.mcp_server.server.CartProcessor"),
            patch("vkuswill_bot.mcp_server.server.PreferencesStore", return_value=mock_prefs),
            patch("vkuswill_bot.mcp_server.server.InMemoryCartSnapshotStore"),
            patch("vkuswill_bot.mcp_server.server.NutritionService", return_value=mock_nutrition),
            patch("vkuswill_bot.mcp_server.server.RecipeSearchService"),
            patch("vkuswill_bot.mcp_server.server.ToolExecutor"),
        ):
            mock_app = MagicMock()
            async with _lifespan(mock_app):
                pass

            mock_prefs.close.assert_awaited_once()
            mock_nutrition.close.assert_awaited_once()
            mock_client.close.assert_awaited_once()

    async def test_upstream_mcp_tools_loaded_on_startup(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_tools.return_value = [
            {"name": "vkusvill_products_search"},
            {"name": "vkusvill_cart_link_create"},
        ]

        with (
            patch("vkuswill_bot.mcp_server.server.VkusvillMCPClient", return_value=mock_client),
            patch("vkuswill_bot.mcp_server.server.PriceCache"),
            patch("vkuswill_bot.mcp_server.server.SearchProcessor"),
            patch("vkuswill_bot.mcp_server.server.CartProcessor"),
            patch("vkuswill_bot.mcp_server.server.PreferencesStore", return_value=AsyncMock()),
            patch("vkuswill_bot.mcp_server.server.InMemoryCartSnapshotStore"),
            patch("vkuswill_bot.mcp_server.server.NutritionService", return_value=AsyncMock()),
            patch("vkuswill_bot.mcp_server.server.RecipeSearchService"),
            patch("vkuswill_bot.mcp_server.server.ToolExecutor"),
        ):
            mock_app = MagicMock()
            async with _lifespan(mock_app):
                pass

            mock_client.get_tools.assert_awaited_once()

    async def test_upstream_mcp_failure_does_not_crash_lifespan(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_tools.side_effect = RuntimeError("upstream недоступен")

        with (
            patch("vkuswill_bot.mcp_server.server.VkusvillMCPClient", return_value=mock_client),
            patch("vkuswill_bot.mcp_server.server.PriceCache"),
            patch("vkuswill_bot.mcp_server.server.SearchProcessor"),
            patch("vkuswill_bot.mcp_server.server.CartProcessor"),
            patch("vkuswill_bot.mcp_server.server.PreferencesStore", return_value=AsyncMock()),
            patch("vkuswill_bot.mcp_server.server.InMemoryCartSnapshotStore"),
            patch("vkuswill_bot.mcp_server.server.NutritionService", return_value=AsyncMock()),
            patch("vkuswill_bot.mcp_server.server.RecipeSearchService"),
            patch("vkuswill_bot.mcp_server.server.ToolExecutor"),
        ):
            mock_app = MagicMock()
            # Ошибка upstream не должна ронять lifespan
            async with _lifespan(mock_app) as services:
                assert services is not None


class TestServicesCleanup:
    """Тесты _Services.cleanup: best-effort закрытие ресурсов."""

    async def test_cleanup_continues_after_close_error(self) -> None:
        prefs = AsyncMock()
        prefs.close.side_effect = RuntimeError("db close failed")
        nutrition = AsyncMock()
        client = AsyncMock()

        services = server._Services(
            tool_executor=MagicMock(),
            prefs_store=prefs,
            nutrition_service=nutrition,
            mcp_client=client,
        )

        await services.cleanup()

        prefs.close.assert_awaited_once()
        nutrition.close.assert_awaited_once()
        client.close.assert_awaited_once()
