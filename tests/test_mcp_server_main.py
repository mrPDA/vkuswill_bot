"""Тесты CLI entrypoint MCP-сервера."""

from __future__ import annotations

import sys
from unittest.mock import patch

from vkuswill_bot.mcp_server import __main__ as mcp_main


class TestMcpServerMain:
    """Проверка выбора транспорта и аргументов запуска."""

    def test_default_runs_stdio_transport(self) -> None:
        with (
            patch.object(sys, "argv", ["mcp_server"]),
            patch("vkuswill_bot.mcp_server.server.mcp.run") as run_mock,
        ):
            mcp_main.main()

        run_mock.assert_called_once_with(transport="stdio")

    def test_http_mode_runs_streamable_http_with_defaults(self) -> None:
        with (
            patch.object(sys, "argv", ["mcp_server", "--http"]),
            patch("vkuswill_bot.mcp_server.server.mcp.run") as run_mock,
        ):
            mcp_main.main()

        run_mock.assert_called_once_with(
            transport="streamable-http",
            host="127.0.0.1",
            port=8081,
        )

    def test_http_mode_respects_custom_host_and_port(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["mcp_server", "--http", "--host", "0.0.0.0", "--port", "9000"],
            ),
            patch("vkuswill_bot.mcp_server.server.mcp.run") as run_mock,
        ):
            mcp_main.main()

        run_mock.assert_called_once_with(
            transport="streamable-http",
            host="0.0.0.0",
            port=9000,
        )

