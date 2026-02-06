"""Общие фикстуры для тестов."""

import pytest

from vkuswill_bot.services.mcp_client import VkusvillMCPClient


MCP_URL = "https://mcp-test.example.com/mcp"


@pytest.fixture
def mcp_client() -> VkusvillMCPClient:
    """MCP-клиент с тестовым URL."""
    return VkusvillMCPClient(MCP_URL)


# -- Типичные ответы MCP-сервера для моков -----------------------------------

INIT_RESPONSE_JSON = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "serverInfo": {"name": "vkusvill-mcp", "version": "1.0.0"},
    },
}

TOOLS_LIST_RESPONSE_JSON = {
    "jsonrpc": "2.0",
    "id": 2,
    "result": {
        "tools": [
            {
                "name": "vkusvill_products_search",
                "description": "Поиск товаров ВкусВилл",
                "inputSchema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            },
            {
                "name": "vkusvill_product_details",
                "description": "Детали товара",
                "inputSchema": {
                    "type": "object",
                    "properties": {"xml_id": {"type": "integer"}},
                    "required": ["xml_id"],
                },
            },
            {
                "name": "vkusvill_cart_link_create",
                "description": "Создать ссылку на корзину",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "products": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "xml_id": {"type": "integer"},
                                    "q": {"type": "integer"},
                                },
                            },
                        }
                    },
                    "required": ["products"],
                },
            },
        ]
    },
}

TOOL_CALL_RESPONSE_JSON = {
    "jsonrpc": "2.0",
    "id": 3,
    "result": {
        "content": [
            {
                "type": "text",
                "text": '{"ok": true, "products": [{"name": "Спагетти", "price": 89}]}',
            }
        ]
    },
}
