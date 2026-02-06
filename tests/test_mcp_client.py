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


class TestFixCartArgs:
    """Тесты _fix_cart_args: автоподстановка q=1 в товары корзины."""

    def test_adds_q_when_missing(self):
        args = {"products": [{"xml_id": 123}, {"xml_id": 456}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0]["q"] == 1
        assert result["products"][1]["q"] == 1

    def test_preserves_existing_q(self):
        args = {"products": [{"xml_id": 123, "q": 3}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0]["q"] == 3

    def test_mixed_items(self):
        args = {"products": [{"xml_id": 1, "q": 2}, {"xml_id": 2}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0]["q"] == 2
        assert result["products"][1]["q"] == 1

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

    def test_non_dict_items_in_products(self):
        args = {"products": [123, "abc", {"xml_id": 1}]}
        result = VkusvillMCPClient._fix_cart_args(args)
        assert result["products"][0] == 123
        assert result["products"][1] == "abc"
        assert result["products"][2] == {"xml_id": 1, "q": 1}


class TestParseSSEResponse:
    """Тесты _parse_sse_response: парсинг SSE text/event-stream."""

    def test_parses_result(self):
        raw = 'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
        result = VkusvillMCPClient._parse_sse_response(raw)
        assert result == {"tools": []}

    def test_parses_multiple_data_lines(self):
        raw = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"value":42}}\n'
            "\n"
        )
        result = VkusvillMCPClient._parse_sse_response(raw)
        assert result == {"value": 42}

    def test_error_raises_runtime_error(self):
        raw = 'data: {"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"Bad request"}}\n'
        with pytest.raises(RuntimeError, match="Bad request"):
            VkusvillMCPClient._parse_sse_response(raw)

    def test_empty_data_line_ignored(self):
        raw = "data: \n\ndata: {\"result\": {\"ok\": true}}\n"
        result = VkusvillMCPClient._parse_sse_response(raw)
        assert result == {"ok": True}

    def test_invalid_json_ignored(self):
        raw = "data: not-json\ndata: {\"result\": {\"ok\": true}}\n"
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
        mcp_client._tools_cache = [{"name": "cached_tool", "description": "", "parameters": {}}]

        tools = await mcp_client.get_tools()

        assert len(tools) == 1
        assert tools[0]["name"] == "cached_tool"
        # Проверяем, что HTTP-запросов не было
        assert respx.calls.call_count == 0

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
        # Сессия уже инициализирована
        mcp_client._session_id = "sid-existing"
        mcp_client._client = httpx.AsyncClient()

        respx.post(MCP_URL).mock(
            return_value=httpx.Response(200, json=TOOL_CALL_RESPONSE_JSON),
        )

        result = await mcp_client.call_tool(
            "vkusvill_products_search", {"q": "молоко"}
        )

        assert "Спагетти" in result
        assert "89" in result
        await mcp_client.close()

    @respx.mock
    async def test_cart_fix_applied(self, mcp_client):
        """_fix_cart_args вызывается для vkusvill_cart_link_create."""
        mcp_client._session_id = "sid-existing"
        mcp_client._client = httpx.AsyncClient()

        cart_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": '{"url": "https://vkusvill.ru/cart/123"}'}]
            },
        }
        respx.post(MCP_URL).mock(
            return_value=httpx.Response(200, json=cart_response),
        )

        await mcp_client.call_tool(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 100}]},  # нет q!
        )

        # Проверяем, что в запросе был добавлен q=1
        request_body = json.loads(respx.calls.last.request.content)
        products = request_body["params"]["arguments"]["products"]
        assert products[0]["q"] == 1
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

        result = await mcp_client.call_tool(
            "vkusvill_products_search", {"q": "хлеб"}
        )

        assert "Спагетти" in result

    @respx.mock
    async def test_sse_response(self, mcp_client):
        """Обработка SSE-ответа от сервера."""
        mcp_client._session_id = "sid-existing"
        mcp_client._client = httpx.AsyncClient()

        sse_body = (
            'data: {"jsonrpc":"2.0","id":1,"result":'
            '{"content":[{"type":"text","text":"SSE result"}]}}\n\n'
        )
        respx.post(MCP_URL).mock(
            return_value=httpx.Response(
                200,
                text=sse_body,
                headers={"content-type": "text/event-stream"},
            ),
        )

        result = await mcp_client.call_tool(
            "vkusvill_products_search", {"q": "тест"}
        )

        assert result == "SSE result"
        await mcp_client.close()

    @respx.mock
    async def test_empty_result(self, mcp_client):
        """Если сервер вернул 202 (нет результата) — пустая строка."""
        mcp_client._session_id = "sid-existing"
        mcp_client._client = httpx.AsyncClient()

        respx.post(MCP_URL).mock(
            return_value=httpx.Response(202),
        )

        result = await mcp_client.call_tool(
            "vkusvill_products_search", {"q": "тест"}
        )

        assert result == ""
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
        mcp_client._session_id = "existing-sid"
        mcp_client._client = httpx.AsyncClient()

        client = await mcp_client._ensure_initialized()

        assert mcp_client._session_id == "existing-sid"
        assert client is mcp_client._client
        await mcp_client.close()

    async def test_reset_session(self, mcp_client):
        """_reset_session сбрасывает session_id и закрывает клиент."""
        mcp_client._session_id = "old-sid"
        mcp_client._client = httpx.AsyncClient()

        await mcp_client._reset_session()

        assert mcp_client._session_id is None
        assert mcp_client._client is None

    async def test_close(self, mcp_client):
        """close() корректно очищает ресурсы."""
        mcp_client._session_id = "sid"
        mcp_client._client = httpx.AsyncClient()

        await mcp_client.close()

        assert mcp_client._session_id is None
        assert mcp_client._client is None
