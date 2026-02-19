"""Точка входа MCP-сервера ВкусВилл.

Запуск:
    # stdio — для Cursor / Claude Desktop
    uv run python -m vkuswill_bot.mcp_server

    # HTTP — для удалённых агентов
    uv run python -m vkuswill_bot.mcp_server --http
    uv run python -m vkuswill_bot.mcp_server --http --host 0.0.0.0 --port 9000

Требования:
    - Файл .env с переменными BOT_TOKEN, GIGACHAT_CREDENTIALS, MCP_SERVER_URL
    - Директория data/ для SQLite (preferences.db)
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VkusVill Bot MCP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python -m vkuswill_bot.mcp_server\n"
            "  uv run python -m vkuswill_bot.mcp_server --http --port 8081\n"
        ),
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Запустить HTTP-сервер (по умолчанию: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Хост HTTP-сервера (по умолчанию: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Порт HTTP-сервера (по умолчанию: 8081)",
    )
    args = parser.parse_args()

    from vkuswill_bot.mcp_server.server import mcp

    if args.http:
        logger.info(
            "Запуск VkusVill MCP-сервера (HTTP %s:%d)...",
            args.host,
            args.port,
        )
        # В mcp>=1.26 host/port конфигурируются через settings, а не через kwargs run().
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        logger.info("Запуск VkusVill MCP-сервера (stdio)...")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
