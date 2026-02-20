"""MCP-клиент для взаимодействия с сервером ВкусВилл.

Использует прямые JSON-RPC POST-вызовы через httpx
с **постоянным** HTTP-соединением (keep-alive) вместо
пересоздания сессии на каждый вызов.
"""

import asyncio
import importlib.metadata
import json
import logging
import traceback
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _get_package_version() -> str:
    """Получить версию пакета из метаданных."""
    try:
        return importlib.metadata.version("vkuswill-bot")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0-dev"


# Таймауты (секунды)
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 120
# Количество попыток при ошибке
MAX_RETRIES = 3
RETRY_DELAY = 2.0

# JSON-RPC
JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-03-26"


class VkusvillMCPClient:
    """Клиент для MCP-сервера ВкусВилл.

    Поддерживает постоянное HTTP-соединение и MCP-сессию.
    Автоматически переинициализирует сессию при потере.

    Инструменты:
    - vkusvill_products_search — поиск товаров
    - vkusvill_product_details — детали товара (состав, КБЖУ)
    - vkusvill_cart_link_create — создание ссылки на корзину
    """

    def __init__(self, server_url: str, api_key: str | None = None) -> None:
        self.server_url = server_url
        self.api_key = api_key
        self._tools_cache: list[dict] | None = None
        self._session_id: str | None = None
        self._request_id: int = 0
        self._client: httpx.Client | None = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _get_client(self) -> httpx.Client:
        """Получить или создать постоянный httpx-клиент."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
                follow_redirects=True,
                # keep-alive включён по умолчанию в httpx
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        """Общие заголовки для MCP-запросов."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    async def _rpc_call(
        self,
        client: httpx.Client,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Выполнить JSON-RPC вызов к MCP-серверу."""
        request_id = self._next_id()
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        logger.debug("JSON-RPC → %s (id=%d)", method, request_id)

        response = await asyncio.to_thread(
            client.post,
            self.server_url,
            json=payload,
            headers=self._headers(),
        )

        logger.debug(
            "JSON-RPC ← %s status=%d ct=%s",
            method,
            response.status_code,
            response.headers.get("content-type", "?"),
        )

        # Сохраняем session-id
        new_session_id = response.headers.get("mcp-session-id")
        if new_session_id:
            self._session_id = new_session_id
            logger.debug("Session ID: %s", self._session_id)

        # 202 = принято (notification)
        if response.status_code == 202:
            return None

        response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        # SSE-ответ
        if "text/event-stream" in content_type:
            return self._parse_sse_response(response.text)

        # JSON-ответ
        data = response.json()
        if "error" in data:
            error = data["error"]
            raise RuntimeError(f"MCP JSON-RPC error {error.get('code')}: {error.get('message')}")
        return data.get("result")

    @staticmethod
    def _parse_sse_response(raw: str) -> dict | None:
        """Извлечь JSON из SSE-ответа (text/event-stream)."""
        result = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                data_str = line[len("data:") :].strip()
                if data_str:
                    try:
                        msg = json.loads(data_str)
                        if "result" in msg:
                            result = msg["result"]
                        elif "error" in msg:
                            error = msg["error"]
                            raise RuntimeError(
                                f"MCP JSON-RPC error {error.get('code')}: {error.get('message')}"
                            )
                    except json.JSONDecodeError:
                        continue
        return result

    async def _rpc_notify(
        self,
        client: httpx.Client,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Отправить JSON-RPC уведомление (без id)."""
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        logger.debug("JSON-RPC notify → %s", method)

        response = await asyncio.to_thread(
            client.post,
            self.server_url,
            json=payload,
            headers=self._headers(),
        )
        if response.status_code not in (200, 202, 204):
            response.raise_for_status()

    async def _ensure_initialized(self) -> httpx.Client:
        """Убедиться, что MCP-сессия инициализирована.

        Если сессия уже есть — возвращает клиент.
        Иначе — инициализирует новую.
        """
        client = await self._get_client()

        if self._session_id is not None:
            return client

        # Инициализация
        logger.info("MCP: инициализация сессии...")
        result = await self._rpc_call(
            client,
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "vkuswill-bot",
                    "version": _get_package_version(),
                },
            },
        )
        logger.debug("MCP initialize result: %s", result)
        await self._rpc_notify(client, "notifications/initialized")
        logger.info("MCP: сессия инициализирована (sid=%s)", self._session_id)

        return client

    async def _reset_session(self) -> None:
        """Сбросить сессию (при ошибке подключения)."""
        self._session_id = None
        if self._client and not self._client.is_closed:
            await asyncio.to_thread(self._client.close)
        self._client = None

    async def close(self) -> None:
        """Закрыть клиент."""
        await self._reset_session()

    async def get_tools(self) -> list[dict]:
        """Получить список инструментов с MCP-сервера.

        Результат кешируется.
        """
        if self._tools_cache is not None:
            return self._tools_cache

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                client = await self._ensure_initialized()
                result = await self._rpc_call(client, "tools/list")
                tools_raw = result.get("tools", []) if result else []

                self._tools_cache = []
                for tool in tools_raw:
                    self._tools_cache.append(
                        {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "parameters": tool.get("inputSchema", {}),
                        }
                    )

                logger.info(
                    "MCP: загружено %d инструментов: %s",
                    len(self._tools_cache),
                    [t["name"] for t in self._tools_cache],
                )
                return self._tools_cache

            except Exception as e:
                last_error = e
                logger.warning(
                    "MCP get_tools попытка %d/%d: %r",
                    attempt + 1,
                    MAX_RETRIES,
                    e,
                )
                logger.debug("MCP get_tools traceback:\n%s", traceback.format_exc())
                await self._reset_session()
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))

        raise last_error or RuntimeError("MCP get_tools failed")

    # ---- Обратная совместимость: делегаты в SearchProcessor/CartProcessor ----

    @staticmethod
    def _fix_cart_args(arguments: dict) -> dict:
        """Делегирует в CartProcessor.fix_cart_args (обратная совместимость)."""
        from vkuswill_bot.services.cart_processor import CartProcessor

        return CartProcessor.fix_cart_args(arguments)

    # Алиасы для тестов
    from vkuswill_bot.services.search_processor import SEARCH_LIMIT
    from vkuswill_bot.services.search_processor import SearchProcessor as _SP

    _UNIT_PATTERN = _SP._UNIT_PATTERN
    _STANDALONE_NUM = _SP._STANDALONE_NUM

    @classmethod
    def _clean_search_query(cls, query: str) -> str:
        """Делегирует в SearchProcessor.clean_search_query (обратная совместимость)."""
        from vkuswill_bot.services.search_processor import SearchProcessor

        return SearchProcessor.clean_search_query(query)

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Вызвать инструмент на MCP-сервере.

        Использует постоянное соединение и переинициализирует
        сессию при ошибке.

        Примечание: предобработка аргументов (очистка запроса, fix_cart_args)
        теперь выполняется в ToolExecutor.preprocess_args() перед вызовом.
        """
        logger.info("MCP вызов: %s(%s)", name, arguments)

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                client = await self._ensure_initialized()
                result = await self._rpc_call(
                    client,
                    "tools/call",
                    {"name": name, "arguments": arguments},
                )

                if result is None:
                    return ""

                content_list = result.get("content", [])
                texts = []
                for item in content_list:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(item.get("text", ""))

                response = "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
                logger.debug("MCP ответ %s: %s", name, response[:500])
                return response

            except Exception as e:
                last_error = e
                logger.warning(
                    "MCP call_tool %s попытка %d/%d: %r",
                    name,
                    attempt + 1,
                    MAX_RETRIES,
                    e,
                )
                logger.debug("MCP call_tool %s traceback:\n%s", name, traceback.format_exc())
                # Сбрасываем сессию и пробуем заново
                await self._reset_session()
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))

        raise last_error or RuntimeError(f"MCP call_tool {name} failed")
