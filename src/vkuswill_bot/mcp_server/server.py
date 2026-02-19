"""MCP-сервер ВкусВилл.

Выставляет умения бота как MCP-инструменты для внешних агентов и LLM:
- search_products       — поиск товаров с препроцессингом запроса
- create_cart           — создание корзины с валидацией и расчётом суммы
- get_previous_cart     — последняя корзина пользователя
- get_preferences       — пищевые предпочтения пользователя
- set_preference        — установить предпочтение
- delete_preference     — удалить предпочтение
- lookup_nutrition      — КБЖУ по названию продукта (Open Food Facts)

Запуск:
    uv run python -m vkuswill_bot.mcp_server              # stdio (для Cursor)
    uv run python -m vkuswill_bot.mcp_server --http       # HTTP на порту 8081
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import Context, FastMCP

from vkuswill_bot.config import config
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.cart_snapshot_store import InMemoryCartSnapshotStore
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.nutrition_service import NutritionService
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.price_cache import PriceCache
from vkuswill_bot.services.recipe_search import RecipeSearchService
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

# Максимальный limit для search_products
SEARCH_LIMIT_MAX = 30


def _error_json(message: str, code: str) -> str:
    """Унифицированный JSON-ответ об ошибке для MCP-инструментов."""
    return json.dumps(
        {
            "ok": False,
            "error": message,
            "code": code,
        },
        ensure_ascii=False,
    )


def _configured_api_keys() -> dict[str, str]:
    """Собрать конфигурированные API ключи MCP (single + multi-key)."""
    api_keys: dict[str, str] = {}
    single = config.mcp_server_api_key.strip()
    if single:
        api_keys["default"] = single

    for client_id, key in config.mcp_server_api_keys.items():
        normalized_client_id = client_id.strip()
        normalized_key = key.strip()
        if normalized_client_id and normalized_key:
            api_keys[normalized_client_id] = normalized_key

    return api_keys


def _get_provided_api_key(ctx: Context) -> str | None:
    """Получить API key из HTTP-заголовков MCP-запроса."""
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        return None

    headers = getattr(request, "headers", {}) or {}
    provided = headers.get("x-mcp-api-key") or headers.get("x-api-key")
    if not provided:
        auth_header = headers.get("authorization", "")
        if isinstance(auth_header, str) and auth_header.lower().startswith("bearer "):
            provided = auth_header[7:].strip()

    if isinstance(provided, str):
        candidate = provided.strip()
        return candidate or None
    return None


def _authorized_client_id(ctx: Context) -> str | None:
    """Вернуть client_id для авторизованного запроса или None."""
    configured_keys = _configured_api_keys()
    if not configured_keys:
        # Авторизация отключена (обратная совместимость).
        return "auth_disabled"

    request = getattr(ctx.request_context, "request", None)
    if request is None:
        # stdio transport (локальный доверенный контур).
        return "stdio"

    provided = _get_provided_api_key(ctx)
    if provided is None:
        return None

    for client_id, api_key in configured_keys.items():
        if hmac.compare_digest(provided, api_key):
            return client_id

    return None


def _require_auth(ctx: Context) -> str | None:
    """Вернуть ошибку авторизации или None."""
    client_id = _authorized_client_id(ctx)
    if client_id is not None:
        return None
    logger.warning(
        "MCP auth failed (request_id=%s, client_id=%s)",
        getattr(ctx, "request_id", "?"),
        getattr(ctx, "client_id", None),
    )
    return _error_json(
        "Недостаточно прав: невалидный API key MCP-сервера",
        "forbidden",
    )


def _require_valid_user_id(user_id: int) -> str | None:
    """Проверить корректность user_id для stateful инструментов."""
    if isinstance(user_id, int) and user_id > 0:
        return None
    return _error_json(
        "Некорректный user_id: требуется положительное целое число",
        "invalid_user_id",
    )


@dataclass
class _Services:
    """Контейнер зависимостей MCP-сервера."""

    tool_executor: ToolExecutor
    prefs_store: PreferencesStore
    nutrition_service: NutritionService
    mcp_client: VkusvillMCPClient

    async def cleanup(self) -> None:
        for name, closer in (
            ("prefs_store", self.prefs_store.close),
            ("nutrition_service", self.nutrition_service.close),
            ("mcp_client", self.mcp_client.close),
        ):
            try:
                await closer()
            except Exception as exc:
                logger.warning("MCP cleanup: не удалось закрыть %s: %s", name, exc)


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[_Services]:
    """Инициализация и завершение сервисов MCP-сервера."""
    logger.info("VkusVill MCP-сервер: инициализация...")

    mcp_client = VkusvillMCPClient(config.mcp_server_url)
    price_cache = PriceCache()
    search_processor = SearchProcessor(price_cache)
    cart_processor = CartProcessor(price_cache)
    prefs_store = PreferencesStore(config.database_path)
    cart_snapshot_store = InMemoryCartSnapshotStore()
    nutrition_service = NutritionService()
    recipe_search_service = RecipeSearchService(
        mcp_client=mcp_client,
        search_processor=search_processor,
        max_concurrency=5,
    )

    tool_executor = ToolExecutor(
        mcp_client=mcp_client,
        search_processor=search_processor,
        cart_processor=cart_processor,
        preferences_store=prefs_store,
        cart_snapshot_store=cart_snapshot_store,
        nutrition_service=nutrition_service,
        recipe_search_service=recipe_search_service,
        user_store=None,  # freemium-логика отключена для внешних агентов
    )

    try:
        tools = await mcp_client.get_tools()
        logger.info(
            "VkusVill upstream MCP: загружено %d инструментов: %s",
            len(tools),
            [t["name"] for t in tools],
        )
    except Exception as exc:
        logger.warning("Не удалось загрузить upstream MCP инструменты: %s", exc)

    services = _Services(
        tool_executor=tool_executor,
        prefs_store=prefs_store,
        nutrition_service=nutrition_service,
        mcp_client=mcp_client,
    )

    try:
        yield services
    finally:
        logger.info("VkusVill MCP-сервер: завершение...")
        await services.cleanup()


mcp = FastMCP(
    "vkuswill-bot",
    instructions=(
        "VkusVill grocery shopping assistant.\n"
        "Workflow: 1) search_products to find items with xml_id, "
        "2) create_cart to build a cart link with total price.\n"
        "For preferences: get_preferences / set_preference / delete_preference.\n"
        "For nutrition info: lookup_nutrition."
    ),
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Инструменты
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_products(
    ctx: Context,
    query: str,
    limit: int = 10,
) -> str:
    """Поиск товаров ВкусВилл по названию или описанию.

    Возвращает JSON-список товаров. Каждый товар содержит:
    - xml_id (int) — идентификатор для create_cart
    - name (str)   — название товара
    - price (float) — цена
    - unit (str)   — единица измерения (кг, шт, л, ...)

    Args:
        query: Поисковый запрос на русском языке (например: \"молоко безлактозное\")
        limit: Максимальное число результатов (1–30, по умолчанию 10)
    """
    auth_error = _require_auth(ctx)
    if auth_error:
        return auth_error

    services: _Services = ctx.request_context.lifespan_context
    args: dict = {"q": query, "limit": min(max(1, limit), SEARCH_LIMIT_MAX)}

    args = await services.tool_executor.preprocess_args(
        "vkusvill_products_search",
        args,
        user_prefs={},
    )

    result = await services.tool_executor.execute(
        "vkusvill_products_search",
        args,
        user_id=0,
    )

    result = await services.tool_executor.postprocess_result(
        "vkusvill_products_search",
        args,
        result,
        user_prefs={},
        search_log={},
        user_id=0,
    )

    return result


@mcp.tool()
async def create_cart(
    ctx: Context,
    products: list[dict],
    user_id: int = 0,
) -> str:
    """Создать ссылку на корзину ВкусВилл из списка товаров.

    Выполняет: исправление дублей → расчёт суммы → ссылка на корзину.
    Возвращает JSON с полями:
    - ok (bool)
    - data.link (str)          — готовая ссылка на корзину
    - data.price_summary.total — итоговая сумма (руб.)

    Args:
        products: Список товаров из search_products.
                  Каждый элемент: {\"xml_id\": <int>, \"q\": <float>}
                  Пример: [{\"xml_id\": 123456, \"q\": 2}, {\"xml_id\": 789012, \"q\": 1}]
        user_id:  Telegram user_id (опционально; сохраняет снимок для get_previous_cart)
    """
    auth_error = _require_auth(ctx)
    if auth_error:
        return auth_error

    user_error = _require_valid_user_id(user_id)
    if user_error:
        return user_error

    services: _Services = ctx.request_context.lifespan_context
    args: dict = {"products": products}

    args = await services.tool_executor.preprocess_args(
        "vkusvill_cart_link_create",
        args,
        user_prefs={},
    )

    result = await services.tool_executor.execute(
        "vkusvill_cart_link_create",
        args,
        user_id=user_id,
    )

    result = await services.tool_executor.postprocess_result(
        "vkusvill_cart_link_create",
        args,
        result,
        user_prefs={},
        search_log={},
        user_id=user_id,
    )

    return result


@mcp.tool()
async def get_previous_cart(
    ctx: Context,
    user_id: int,
) -> str:
    """Получить содержимое предыдущей корзины пользователя.

    Возвращает JSON с полями:
    - ok (bool)
    - products — список товаров с названиями и ценами
    - link     — ссылка на корзину
    - total    — итоговая сумма

    Args:
        user_id: Telegram user_id пользователя
    """
    auth_error = _require_auth(ctx)
    if auth_error:
        return auth_error

    user_error = _require_valid_user_id(user_id)
    if user_error:
        return user_error

    services: _Services = ctx.request_context.lifespan_context
    return await services.tool_executor.execute(
        "get_previous_cart",
        {},
        user_id=user_id,
    )


@mcp.tool()
async def get_preferences(
    ctx: Context,
    user_id: int,
) -> str:
    """Получить пищевые предпочтения пользователя.

    Возвращает JSON: {\"preferences\": [{\"category\": \"...\", \"preference\": \"...\"}]}

    Args:
        user_id: Telegram user_id пользователя
    """
    auth_error = _require_auth(ctx)
    if auth_error:
        return auth_error

    user_error = _require_valid_user_id(user_id)
    if user_error:
        return user_error

    services: _Services = ctx.request_context.lifespan_context
    return await services.prefs_store.get_formatted(user_id)


@mcp.tool()
async def set_preference(
    ctx: Context,
    user_id: int,
    category: str,
    preference: str,
) -> str:
    """Установить пищевое предпочтение пользователя для категории.

    Пример: category=\"молоко\", preference=\"безлактозное\"

    Args:
        user_id:    Telegram user_id пользователя
        category:   Категория (например: \"молоко\", \"хлеб\", \"сыр\")
        preference: Предпочтение (например: \"безлактозное\", \"цельнозерновой\")
    """
    auth_error = _require_auth(ctx)
    if auth_error:
        return auth_error

    user_error = _require_valid_user_id(user_id)
    if user_error:
        return user_error

    services: _Services = ctx.request_context.lifespan_context
    return await services.prefs_store.set(user_id, category, preference)


@mcp.tool()
async def delete_preference(
    ctx: Context,
    user_id: int,
    category: str,
) -> str:
    """Удалить пищевое предпочтение пользователя для категории.

    Args:
        user_id:  Telegram user_id пользователя
        category: Категория для удаления
    """
    auth_error = _require_auth(ctx)
    if auth_error:
        return auth_error

    user_error = _require_valid_user_id(user_id)
    if user_error:
        return user_error

    services: _Services = ctx.request_context.lifespan_context
    return await services.prefs_store.delete(user_id, category)


@mcp.tool()
async def lookup_nutrition(
    ctx: Context,
    product_name: str,
) -> str:
    """Получить КБЖУ продукта: калории, белки, жиры, углеводы на 100 г.

    Данные из Open Food Facts (бесплатно, без ключа API).
    Поддерживает русские и английские названия.

    Args:
        product_name: Название продукта (например: \"куриная грудка\", \"гречка\")
    """
    auth_error = _require_auth(ctx)
    if auth_error:
        return auth_error

    services: _Services = ctx.request_context.lifespan_context
    return await services.tool_executor.execute(
        "nutrition_lookup",
        {"query": product_name},
        user_id=0,
    )
