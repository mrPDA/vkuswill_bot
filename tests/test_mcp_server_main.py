"""Тесты CLI entrypoint MCP-сервера."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        fake_mcp = SimpleNamespace(
            settings=SimpleNamespace(host="127.0.0.1", port=8000),
            run=Mock(),
        )
        with (
            patch.object(sys, "argv", ["mcp_server", "--http"]),
            patch("vkuswill_bot.mcp_server.server.mcp", fake_mcp),
        ):
            mcp_main.main()

        fake_mcp.run.assert_called_once_with(transport="streamable-http")
        assert fake_mcp.settings.host == "127.0.0.1"
        assert fake_mcp.settings.port == 8081

    def test_http_mode_respects_custom_host_and_port(self) -> None:
        fake_mcp = SimpleNamespace(
            settings=SimpleNamespace(host="127.0.0.1", port=8000),
            run=Mock(),
        )
        with (
            patch.object(
                sys,
                "argv",
                ["mcp_server", "--http", "--host", "0.0.0.0", "--port", "9000"],
            ),
            patch("vkuswill_bot.mcp_server.server.mcp", fake_mcp),
        ):
            mcp_main.main()

        fake_mcp.run.assert_called_once_with(transport="streamable-http")
        assert fake_mcp.settings.host == "0.0.0.0"
        assert fake_mcp.settings.port == 9000
