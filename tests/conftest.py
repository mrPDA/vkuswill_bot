"""Общие фикстуры для тестов."""

import os

# Устанавливаем минимальные env-переменные ДО импорта модулей приложения,
# чтобы config = Config() на уровне модуля config.py не падал в CI.
# ADR-006: legacy GigaChat удалён, нужен LLM для shopping_agent.
os.environ.setdefault("BOT_TOKEN", "test-token-for-ci-000000000")
os.environ.setdefault("LLM_API_KEY", "test-llm-key-for-ci")
os.environ.setdefault("LLM_MODEL", "test-model")

from unittest.mock import patch

import pytest

from vkuswill_bot.services.mcp_client import VkusvillMCPClient


MCP_URL = "https://mcp-test.example.com/mcp"


@pytest.fixture(autouse=True)
def _stable_prompts():
    """Отключить загрузку промптов из prompts/*.txt, чтобы наличие файлов
    на диске разработчика не ломало тесты (промпт 24 КБ > max_history_chars 16 КБ).
    Тесты ProductionPromptContent читают файлы напрямую и не затрагиваются."""
    with patch(
        "vkuswill_bot.services.prompts._load_prompt_file",
        return_value=None,
    ):
        yield


@pytest.fixture(autouse=True)
def _reset_mcp_circuit_breaker():
    """Reset module-level MCP circuit breaker so tests don't leak state."""
    from vkuswill_bot.agents.meal_plan_ingredient_collection import _mcp_recipe_breaker

    _mcp_recipe_breaker.record_success()
    yield
    _mcp_recipe_breaker.record_success()


@pytest.fixture
def mcp_client() -> VkusvillMCPClient:
    """MCP-клиент с тестовым URL."""
    return VkusvillMCPClient(MCP_URL)


@pytest.fixture
def mock_env_minimal():
    """Минимальный набор переменных окружения для Config."""
    env = {
        "BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        "LLM_API_KEY": "test-llm-key",
        "LLM_MODEL": "test-model",
    }
    with patch.dict(os.environ, env, clear=True):
        yield env


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
