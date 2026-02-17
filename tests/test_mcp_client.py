"""Тесты MCP-клиента (VkusvillMCPClient).

Тестируем:
- Чистые функции: _fix_cart_args, _parse_sse_response, _headers, _next_id
- JSON-RPC вызовы через httpx (мокаем respx)
- Retry-логику при сбоях
- Кеширование инструментов
- Инициализацию и сброс сессии
"""

import json

import httpx
import pytest
import respx

from tests.conftest import (
    INIT_RESPONSE_JSON,
    MCP_URL,
    TOOL_CALL_RESPONSE_JSON,
    TOOLS_LIST_RESPONSE_JSON,
)
from vkuswill_bot.services.mcp_client import VkusvillMCPClient


# ============================================================================
# Чистые функции (без I/O)
# ============================================================================


class TestCleanSearchQuery:
    """Тесты _clean_search_query: очистка поисковых запросов от цифр и единиц."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            # Числа с единицами
            ("Творог 5% 400 гр", "Творог"),
            ("Творог 5% 400 г", "Творог"),
            ("молоко 3,2% 450 мл", "молоко"),
            ("картофель 1,5 кг", "картофель"),
            ("сливки 200 мл", "сливки"),
            # Числа с единицами-словами
            ("тунец 2 банки", "тунец"),
            ("молоко 4 бутылки", "молоко"),
            ("макароны 2 пачки", "макароны"),
            ("сок 1 литр", "сок"),
            # Отдельные числа (количество)
            ("молоко 4", "молоко"),
            ("мороженое 2", "мороженое"),
            ("вареники 2", "вареники"),
            ("яйца 10", "яйца"),
            # Без изменений
            ("темный хлеб", "темный хлеб"),
            ("куриное филе", "куриное филе"),
            ("соус песто", "соус песто"),
            ("спагетти", "спагетти"),
            # Сложные случаи
            ("Творог 5% 400 гр обезжиренный", "Творог обезжиренный"),
            ("масло 82,5% 200 г сливочное", "масло сливочное"),
            # Пустой результат → вернуть оригинал
            ("123", "123"),
            ("5%", "5%"),
        ],
    )
    def test_clean_query(self, raw, expected):
        """Проверяет очистку поисковых запросов."""
        assert VkusvillMCPClient._clean_search_query(raw) == expected

    def test_empty_string(self):
        """Пустая строка остаётся пустой."""
        assert VkusvillMCPClient._clean_search_query("") == ""

    def test_only_text(self):
        """Строка без цифр не изменяется."""
        assert VkusvillMCPClient._clean_search_query("пармезан") == "пармезан"


class TestFixCartArgs:
    """Тесты _fix_cart_args: автоподстановка q=1 и дедупликация."""

    def test_adds_q_when_missing(self):
        args = {"products": [{"xml_id": 123}, {"xml_id": 456}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] == [
            {"xml_id": 123, "q": 1},
            {"xml_id": 456, "q": 1},
        ]

    def test_preserves_existing_q(self):
        args = {"products": [{"xml_id": 123, "q": 3}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0]["q"] == 3

    def test_mixed_items(self):
        args = {"products": [{"xml_id": 1, "q": 2}, {"xml_id": 2}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] == [
            {"xml_id": 1, "q": 2},
            {"xml_id": 2, "q": 1},
        ]

    def test_deduplicates_same_xml_id(self):
        """Главный баг: GigaChat дублирует xml_id вместо q."""
        args = {
            "products": [
                {"xml_id": 103297},
                {"xml_id": 103297},
                {"xml_id": 103297},
                {"xml_id": 103297},
            ]
        }
        result = VkusvillMCPClient._fix_cart_args(args)
        assert len(result["products"]) == 1
        assert result["products"][0] == {"xml_id": 103297, "q": 4}

    def test_deduplicates_with_existing_q(self):
        """Дедупликация суммирует q."""
        args = {
            "products": [
                {"xml_id": 100, "q": 2},
                {"xml_id": 100, "q": 3},
            ]
        }
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] == [{"xml_id": 100, "q": 5}]

    def test_deduplicates_preserves_order(self):
        """Порядок по первому вхождению xml_id."""
        args = {
            "products": [
                {"xml_id": 2},
                {"xml_id": 1},
                {"xml_id": 2},
            ]
        }
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] == [
            {"xml_id": 2, "q": 2},
            {"xml_id": 1, "q": 1},
        ]

    def test_empty_products(self):
        args = {"products": []}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] == []

    def test_no_products_key(self):
        args = {"something": "else"}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result == {"something": "else"}

    def test_non_list_products(self):
        args = {"products": "not-a-list"}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] == "not-a-list"

    def test_non_dict_items_skipped_in_merge(self):
        """Не-dict элементы пропускаются при объединении."""
        args = {"products": [123, "abc", {"xml_id": 1}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] == [{"xml_id": 1, "q": 1}]

    def test_fractional_q_preserved(self):
        """Дробные q для весовых товаров сохраняются."""
        args = {"products": [{"xml_id": 41728, "q": 1.5}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0]["q"] == 1.5

    def test_fractional_q_summed_on_dedup(self):
        """Дробные q суммируются при дедупликации."""
        args = {
            "products": [
                {"xml_id": 41728, "q": 0.5},
                {"xml_id": 41728, "q": 0.7},
            ]
        }
        result = VkusvillMCPClient._fix_cart_args(args)
        assert len(result["products"]) == 1
        assert abs(result["products"][0]["q"] - 1.2) < 1e-9


class TestParseSSEResponse:
    """Тесты _parse_sse_response: парсинг SSE text/event-stream."""

    def test_parses_result(self):
        raw = 'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
        result = VkusvillMCPClient._parse_sse_response(raw)
        assert result == {"tools": []}

    def test_parses_multiple_data_lines(self):
        raw = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"value":42}}\n\n'
        result = VkusvillMCPClient._parse_sse_response(raw)
        assert result == {"value": 42}

    def test_error_raises_runtime_error(self):
        raw = 'data: {"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"Bad request"}}\n'
        with pytest.raises(RuntimeError, match="Bad request"):
            VkusvillMCPClient._parse_sse_response(raw)

    def test_empty_data_line_ignored(self):
        raw = 'data: \n\ndata: {"result": {"ok": true}}\n'
        result = VkusvillMCPClient._parse_sse_response(raw)
        assert result == {"ok": True}

    def test_invalid_json_ignored(self):
        raw = 'data: not-json\ndata: {"result": {"ok": true}}\n'
        result = VkusvillMCPClient._parse_sse_response(raw)
        assert result == {"ok": True}

    def test_no_data_lines(self):
        raw = "event: ping\nretry: 5000\n"
        result = VkusvillMCPClient._parse_sse_response(raw)
        assert result is None

    def test_empty_string(self):
        result = VkusvillMCPClient._parse_sse_response("")
        assert result is None


class TestHeaders:
    """Тесты _headers: формирование заголовков."""

    def test_without_session(self, mcp_client):
        headers = mcp_client._headers()
        assert headers["Content-Type"] == "application/json"
        assert "application/json" in headers["Accept"]
        assert "mcp-session-id" not in headers

    def test_with_session(self, mcp_client):
        mcp_client._session_id = "test-session-123"
        headers = mcp_client._headers()
        assert headers["mcp-session-id"] == "test-session-123"


class TestNextId:
    """Тесты _next_id: инкрементальный счётчик."""

    def test_increments(self, mcp_client):
        assert mcp_client._next_id() == 1
        assert mcp_client._next_id() == 2
        assert mcp_client._next_id() == 3


# ============================================================================
# Асинхронные тесты с мокированием HTTP (respx)
# ============================================================================


def _mock_init_and_notify(mock: respx.MockRouter) -> None:
    """Замокировать инициализацию MCP-сессии (initialize + notify)."""
    mock.post(MCP_URL).mock(
        side_effect=[
            # initialize → 200
            httpx.Response(
                200,
                json=INIT_RESPONSE_JSON,
                headers={"mcp-session-id": "sid-test"},
            ),
            # notifications/initialized → 202
            httpx.Response(202),
        ]
    )


def _mock_init_notify_and_call(
    mock: respx.MockRouter,
    tool_response: httpx.Response,
    *,
    session_id: str = "sid-test",
) -> None:
    """Замокировать initialize + notify + один tools/call."""
    mock.post(MCP_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=INIT_RESPONSE_JSON,
                headers={"mcp-session-id": session_id},
            ),
            httpx.Response(202),
            tool_response,
        ]
    )


class TestGetTools:
    """Тесты get_tools: загрузка и кеширование инструментов."""

    @respx.mock
    async def test_success(self, mcp_client):
        """Успешная загрузка инструментов."""
        respx.post(MCP_URL).mock(
            side_effect=[
                # initialize
                httpx.Response(
                    200,
                    json=INIT_RESPONSE_JSON,
                    headers={"mcp-session-id": "sid-1"},
                ),
                # notifications/initialized
                httpx.Response(202),
                # tools/list
                httpx.Response(200, json=TOOLS_LIST_RESPONSE_JSON),
            ]
        )

        tools = await mcp_client.get_tools()

        assert len(tools) == 3
        names = [t["name"] for t in tools]
        assert "vkusvill_products_search" in names
        assert "vkusvill_product_details" in names
        assert "vkusvill_cart_link_create" in names

    @respx.mock
    async def test_caching(self, mcp_client):
        """Повторный вызов возвращает кеш, без HTTP-запросов."""
        respx.post(MCP_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=INIT_RESPONSE_JSON,
                    headers={"mcp-session-id": "sid-cache"},
                ),
                httpx.Response(202),
                httpx.Response(200, json=TOOLS_LIST_RESPONSE_JSON),
            ]
        )

        tools = await mcp_client.get_tools()
        calls_before = respx.calls.call_count
        tools_cached = await mcp_client.get_tools()

        assert len(tools) == 3
        assert tools == tools_cached
        # Проверяем, что HTTP-запросов не было
        assert respx.calls.call_count == calls_before

    @respx.mock
    async def test_retry_on_failure(self, mcp_client):
        """Retry при ошибке подключения."""
        respx.post(MCP_URL).mock(
            side_effect=[
                # 1-я попытка: ошибка
                httpx.ConnectError("Connection refused"),
                # 2-я попытка: initialize
                httpx.Response(
                    200,
                    json=INIT_RESPONSE_JSON,
                    headers={"mcp-session-id": "sid-retry"},
                ),
                # notifications/initialized
                httpx.Response(202),
                # tools/list
                httpx.Response(200, json=TOOLS_LIST_RESPONSE_JSON),
            ]
        )

        tools = await mcp_client.get_tools()
        assert len(tools) == 3

    @respx.mock
    async def test_all_retries_fail(self, mcp_client):
        """Если все попытки провалились — выбрасываем исключение."""
        respx.post(MCP_URL).mock(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        with pytest.raises(httpx.ConnectError):
            await mcp_client.get_tools()


class TestCallTool:
    """Тесты call_tool: вызов инструментов через JSON-RPC."""

    @respx.mock
    async def test_success(self, mcp_client):
        """Успешный вызов инструмента."""
        _mock_init_notify_and_call(
            respx,
            httpx.Response(200, json=TOOL_CALL_RESPONSE_JSON),
            session_id="sid-existing",
        )

        result = await mcp_client.call_tool("vkusvill_products_search", {"q": "молоко"})

        assert "Спагетти" in result
        assert "89" in result
        await mcp_client.close()

    @respx.mock
    async def test_cart_args_passthrough(self, mcp_client):
        """call_tool передаёт аргументы корзины без предобработки (она в ToolExecutor)."""
        cart_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": '{"url": "https://vkusvill.ru/cart/123"}'}]
            },
        }
        _mock_init_notify_and_call(
            respx,
            httpx.Response(200, json=cart_response),
            session_id="sid-existing",
        )

        await mcp_client.call_tool(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 100}]},  # нет q — и не добавляется
        )

        # call_tool больше не добавляет q=1 — это делает ToolExecutor.preprocess_args
        request_body = json.loads(respx.calls.last.request.content)
        products = request_body["params"]["arguments"]["products"]
        assert products[0] == {"xml_id": 100}
        await mcp_client.close()

    @respx.mock
    async def test_search_passthrough_no_limit(self, mcp_client):
        """call_tool не добавляет limit — это делает ToolExecutor.preprocess_args."""
        _mock_init_notify_and_call(
            respx,
            httpx.Response(200, json=TOOL_CALL_RESPONSE_JSON),
            session_id="sid-existing",
        )

        await mcp_client.call_tool("vkusvill_products_search", {"q": "молоко"})

        request_body = json.loads(respx.calls.last.request.content)
        args = request_body["params"]["arguments"]
        assert "limit" not in args  # limit добавляется в ToolExecutor, не в call_tool
        await mcp_client.close()

    @respx.mock
    async def test_search_limit_not_overwritten(self, mcp_client):
        """Если limit уже указан явно, он не перезаписывается."""
        _mock_init_notify_and_call(
            respx,
            httpx.Response(200, json=TOOL_CALL_RESPONSE_JSON),
            session_id="sid-existing",
        )

        await mcp_client.call_tool("vkusvill_products_search", {"q": "молоко", "limit": 20})

        request_body = json.loads(respx.calls.last.request.content)
        args = request_body["params"]["arguments"]
        assert args["limit"] == 20
        await mcp_client.close()

    @respx.mock
    async def test_search_limit_not_added_to_other_tools(self, mcp_client):
        """Лимит НЕ добавляется к другим инструментам (корзина и т.д.)."""
        cart_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": '{"url": "https://vkusvill.ru/cart/123"}'}]
            },
        }
        _mock_init_notify_and_call(
            respx,
            httpx.Response(200, json=cart_response),
            session_id="sid-existing",
        )

        await mcp_client.call_tool(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 100, "q": 1}]},
        )

        request_body = json.loads(respx.calls.last.request.content)
        args = request_body["params"]["arguments"]
        assert "limit" not in args
        await mcp_client.close()

    @respx.mock
    async def test_reinitializes_on_failure(self, mcp_client):
        """Если сессия потеряна, переинициализируется."""
        respx.post(MCP_URL).mock(
            side_effect=[
                # 1-я попытка: ошибка при вызове (сессия сброшена)
                httpx.ConnectError("timeout"),
                # 2-я попытка: initialize
                httpx.Response(
                    200,
                    json=INIT_RESPONSE_JSON,
                    headers={"mcp-session-id": "sid-new"},
                ),
                # notifications/initialized
                httpx.Response(202),
                # tools/call
                httpx.Response(200, json=TOOL_CALL_RESPONSE_JSON),
            ]
        )

        result = await mcp_client.call_tool("vkusvill_products_search", {"q": "хлеб"})

        assert "Спагетти" in result

    @respx.mock
    async def test_sse_response(self, mcp_client):
        """Обработка SSE-ответа от сервера."""
        sse_body = (
            'data: {"jsonrpc":"2.0","id":1,"result":'
            '{"content":[{"type":"text","text":"SSE result"}]}}\n\n'
        )
        _mock_init_notify_and_call(
            respx,
            httpx.Response(
                200,
                text=sse_body,
                headers={"content-type": "text/event-stream"},
            ),
            session_id="sid-existing",
        )

        result = await mcp_client.call_tool("vkusvill_products_search", {"q": "тест"})

        assert result == "SSE result"
        await mcp_client.close()

    @respx.mock
    async def test_empty_result(self, mcp_client):
        """Если сервер вернул 202 (нет результата) — пустая строка."""
        _mock_init_notify_and_call(
            respx,
            httpx.Response(202),
            session_id="sid-existing",
        )

        result = await mcp_client.call_tool("vkusvill_products_search", {"q": "тест"})

        assert result == ""
        await mcp_client.close()


class TestGetPackageVersion:
    """Тесты _get_package_version: получение версии пакета."""

    def test_returns_version_string(self):
        """Возвращает строку версии."""
        from vkuswill_bot.services.mcp_client import _get_package_version

        version = _get_package_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_returns_dev_version_when_not_installed(self):
        """Возвращает 0.0.0-dev если пакет не найден."""
        from vkuswill_bot.services.mcp_client import _get_package_version
        from unittest.mock import patch
        import importlib.metadata

        with patch(
            "vkuswill_bot.services.mcp_client.importlib.metadata.version",
            side_effect=importlib.metadata.PackageNotFoundError,
        ):
            version = _get_package_version()

        assert version == "0.0.0-dev"


class TestRpcCallErrors:
    """Тесты обработки ошибок в _rpc_call."""

    @respx.mock
    async def test_json_rpc_error_in_json_response(self, mcp_client):
        """JSON-RPC ошибка в обычном JSON-ответе (не SSE) вызывает RuntimeError."""
        client = await mcp_client._get_client()

        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        }
        respx.post(MCP_URL).mock(
            return_value=httpx.Response(200, json=error_response),
        )

        with pytest.raises(RuntimeError, match="Method not found"):
            await mcp_client._rpc_call(client, "nonexistent/method")
        await mcp_client.close()

    @respx.mock
    async def test_rpc_notify_with_params(self, mcp_client):
        """_rpc_notify передаёт params если указаны."""
        client = await mcp_client._get_client()

        respx.post(MCP_URL).mock(
            return_value=httpx.Response(202),
        )

        await mcp_client._rpc_notify(
            client,
            "notifications/test",
            params={"key": "value"},
        )

        request_body = json.loads(respx.calls.last.request.content)
        assert request_body["params"] == {"key": "value"}
        await mcp_client.close()

    @respx.mock
    async def test_rpc_notify_error_status(self, mcp_client):
        """_rpc_notify вызывает raise_for_status при ошибке."""
        client = await mcp_client._get_client()

        respx.post(MCP_URL).mock(
            return_value=httpx.Response(500, text="Internal Server Error"),
        )

        with pytest.raises(httpx.HTTPStatusError):
            await mcp_client._rpc_notify(client, "notifications/test")
        await mcp_client.close()

    @respx.mock
    async def test_call_tool_all_retries_fail(self, mcp_client):
        """call_tool — все retry провалились, исключение пробрасывается."""
        respx.post(MCP_URL).mock(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        with pytest.raises(httpx.ConnectError):
            await mcp_client.call_tool("vkusvill_products_search", {"q": "test"})

    @respx.mock
    async def test_call_tool_no_text_content(self, mcp_client):
        """call_tool с ответом без text-элементов возвращает JSON."""
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "image", "data": "base64..."}]},
        }
        _mock_init_notify_and_call(
            respx,
            httpx.Response(200, json=response),
            session_id="sid-existing",
        )

        result = await mcp_client.call_tool("vkusvill_product_details", {"xml_id": 123})

        # Нет text-элементов → json.dumps(result)
        assert "content" in result
        await mcp_client.close()

    @respx.mock
    async def test_get_client_recreates_when_closed(self, mcp_client):
        """_get_client создаёт нового клиента если старый закрыт."""
        # Создаём и закрываем клиент
        mcp_client._client = httpx.AsyncClient()
        await mcp_client._client.aclose()
        assert mcp_client._client.is_closed

        # Должен создать нового
        client = await mcp_client._get_client()
        assert not client.is_closed
        await mcp_client.close()


class TestFixCartArgsEdgeCases:
    """Дополнительные edge-case тесты _fix_cart_args."""

    def test_item_without_xml_id(self):
        """Элемент без xml_id пропускается при дедупликации."""
        args = {"products": [{"q": 5}, {"xml_id": 1}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        # Элемент без xml_id пропущен
        assert result["products"] == [{"xml_id": 1, "q": 1}]

    def test_xml_id_none(self):
        """xml_id=None пропускается."""
        args = {"products": [{"xml_id": None, "q": 1}, {"xml_id": 2}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"] == [{"xml_id": 2, "q": 1}]


class TestSearchQueryCleaningInCallTool:
    """Тесты очистки поисковых запросов при вызове call_tool (lines 369-370)."""

    @respx.mock
    async def test_search_query_passthrough(self, mcp_client):
        """call_tool не очищает запрос — это делает ToolExecutor.preprocess_args."""
        _mock_init_notify_and_call(
            respx,
            httpx.Response(200, json=TOOL_CALL_RESPONSE_JSON),
            session_id="sid-existing",
        )

        await mcp_client.call_tool("vkusvill_products_search", {"q": "Творог 5% 400 гр"})

        request_body = json.loads(respx.calls.last.request.content)
        args = request_body["params"]["arguments"]
        # call_tool передаёт запрос как есть — очистка в ToolExecutor
        assert args["q"] == "Творог 5% 400 гр"
        await mcp_client.close()

    @respx.mock
    async def test_query_without_numbers_unchanged(self, mcp_client):
        """Запрос без цифр отправляется без изменений."""
        _mock_init_notify_and_call(
            respx,
            httpx.Response(200, json=TOOL_CALL_RESPONSE_JSON),
            session_id="sid-existing",
        )

        await mcp_client.call_tool("vkusvill_products_search", {"q": "молоко"})

        request_body = json.loads(respx.calls.last.request.content)
        args = request_body["params"]["arguments"]
        assert args["q"] == "молоко"
        await mcp_client.close()


class TestSessionManagement:
    """Тесты управления MCP-сессией."""

    async def test_ensure_initialized_creates_session(self, mcp_client):
        """_ensure_initialized создаёт сессию при первом вызове."""
        assert mcp_client._session_id is None
        assert mcp_client._client is None

        with respx.mock:
            respx.post(MCP_URL).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json=INIT_RESPONSE_JSON,
                        headers={"mcp-session-id": "new-sid"},
                    ),
                    httpx.Response(202),
                ]
            )

            client = await mcp_client._ensure_initialized()

        assert mcp_client._session_id == "new-sid"
        assert client is not None
        await mcp_client.close()

    async def test_ensure_initialized_reuses_session(self, mcp_client):
        """Если сессия уже есть — не переинициализирует."""
        with respx.mock:
            respx.post(MCP_URL).mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json=INIT_RESPONSE_JSON,
                        headers={"mcp-session-id": "existing-sid"},
                    ),
                    httpx.Response(202),
                ]
            )

            client = await mcp_client._ensure_initialized()
            call_count_before = respx.calls.call_count
            same_client = await mcp_client._ensure_initialized()
            assert respx.calls.call_count == call_count_before

        assert same_client is client
        await mcp_client.close()

    async def test_reset_session(self, mcp_client):
        """_reset_session сбрасывает session_id и закрывает клиент."""
        with respx.mock:
            _mock_init_and_notify(respx)
            await mcp_client._ensure_initialized()

        await mcp_client._reset_session()

        assert mcp_client._session_id is None
        assert mcp_client._client is None

    async def test_close(self, mcp_client):
        """close() корректно очищает ресурсы."""
        with respx.mock:
            _mock_init_and_notify(respx)
            await mcp_client._ensure_initialized()

        await mcp_client.close()

        assert mcp_client._session_id is None
        assert mcp_client._client is None
