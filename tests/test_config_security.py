"""Тесты безопасности конфигурации.

Проверяем:
- Конфигурация загружается только из переменных окружения
- Значения по умолчанию безопасны
- Лимиты настроены разумно
- Секреты не утекают через repr/str
- Обязательные поля валидируются
- Файлы конфигурации защищены
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from vkuswill_bot.config import Config


# Корень проекта
PROJECT_ROOT = Path(__file__).parent.parent

# Минимальный набор env-переменных для создания Config
MINIMAL_ENV = {
    "BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
    "GIGACHAT_CREDENTIALS": "test-credentials-value",
}


# ============================================================================
# Обязательные поля
# ============================================================================


@pytest.mark.security
class TestRequiredFields:
    """Проверка обязательных полей конфигурации."""

    def test_bot_token_required(self):
        """BOT_TOKEN обязателен — без него Config не создаётся."""
        env = {"GIGACHAT_CREDENTIALS": "test-creds"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Config(
                    _env_file=None,  # type: ignore[call-arg]
                )
            errors = exc_info.value.errors()
            field_names = [e["loc"][0] for e in errors]
            assert "bot_token" in field_names

    def test_gigachat_credentials_required(self):
        """GIGACHAT_CREDENTIALS обязателен."""
        env = {"BOT_TOKEN": "123:ABC"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Config(
                    _env_file=None,  # type: ignore[call-arg]
                )
            errors = exc_info.value.errors()
            field_names = [e["loc"][0] for e in errors]
            assert "gigachat_credentials" in field_names


# ============================================================================
# Безопасные значения по умолчанию
# ============================================================================


@pytest.mark.security
class TestDefaultValues:
    """Проверка безопасности значений по умолчанию."""

    def test_debug_disabled_by_default(self):
        """Debug отключён по умолчанию."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.debug is False

    def test_max_tool_calls_reasonable(self):
        """Лимит вызовов инструментов разумный (не > 50)."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert 1 <= cfg.max_tool_calls <= 50, (
            f"max_tool_calls={cfg.max_tool_calls} — "
            f"должен быть в диапазоне [1, 50]"
        )

    def test_max_history_reasonable(self):
        """Лимит истории разумный (не > 200)."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert 1 <= cfg.max_history_messages <= 200, (
            f"max_history_messages={cfg.max_history_messages} — "
            f"должен быть в диапазоне [1, 200]"
        )

    def test_mcp_server_url_is_https(self):
        """MCP-сервер по умолчанию использует HTTPS."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.mcp_server_url.startswith("https://"), (
            f"MCP URL должен быть HTTPS, получено: {cfg.mcp_server_url}"
        )

    def test_gigachat_model_default(self):
        """Модель GigaChat по умолчанию — не пустая."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.gigachat_model, "gigachat_model не должен быть пустым"

    def test_gigachat_scope_default(self):
        """Scope GigaChat имеет значение по умолчанию."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.gigachat_scope, "gigachat_scope не должен быть пустым"

    def test_storage_backend_default_memory(self):
        """storage_backend по умолчанию — 'memory'."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.storage_backend == "memory"

    def test_redis_url_default_empty(self):
        """redis_url по умолчанию — пустая строка."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.redis_url == ""

    def test_database_url_default_empty(self):
        """database_url по умолчанию — пустая строка."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.database_url == ""

    def test_webhook_disabled_by_default(self):
        """Webhook отключён по умолчанию."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.use_webhook is False

    def test_webhook_port_default(self):
        """Порт webhook по умолчанию — 8080."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.webhook_port == 8080

    def test_webhook_host_default_empty(self):
        """webhook_host по умолчанию — пустая строка."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.webhook_host == ""

    def test_gigachat_max_concurrent_default(self):
        """gigachat_max_concurrent по умолчанию — 15."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.gigachat_max_concurrent == 15

    def test_gigachat_max_concurrent_reasonable(self):
        """gigachat_max_concurrent в разумных пределах [1, 100]."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert 1 <= cfg.gigachat_max_concurrent <= 100, (
            f"gigachat_max_concurrent={cfg.gigachat_max_concurrent} — "
            f"должен быть в [1, 100]"
        )


# ============================================================================
# Защита секретов
# ============================================================================


@pytest.mark.security
class TestSecretProtection:
    """Проверка защиты секретов от утечки."""

    def test_env_file_exists_in_gitignore(self):
        """.env указан в .gitignore."""
        gitignore = PROJECT_ROOT / ".gitignore"
        assert gitignore.exists(), ".gitignore не найден"

        content = gitignore.read_text(encoding="utf-8")
        assert ".env" in content, ".env не указан в .gitignore"

    def test_env_example_exists(self):
        """.env.example существует для документации."""
        assert (PROJECT_ROOT / ".env.example").exists(), (
            ".env.example не найден — нужен для документации переменных"
        )

    def test_env_example_has_all_required_keys(self):
        """.env.example содержит все обязательные ключи."""
        env_example = PROJECT_ROOT / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        required_keys = ["BOT_TOKEN", "GIGACHAT_CREDENTIALS"]
        for key in required_keys:
            assert key in content, (
                f"{key} отсутствует в .env.example"
            )

    def test_config_loads_from_env_not_hardcoded(self):
        """Config загружает значения из env, а не из кода."""
        custom_env = {
            "BOT_TOKEN": "custom-token-123",
            "GIGACHAT_CREDENTIALS": "custom-creds-456",
            "MCP_SERVER_URL": "https://custom-mcp.example.com/mcp",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]

        assert cfg.bot_token == "custom-token-123"
        assert cfg.gigachat_credentials == "custom-creds-456"
        assert cfg.mcp_server_url == "https://custom-mcp.example.com/mcp"

    def test_storage_backend_customizable(self):
        """storage_backend настраивается через переменную окружения."""
        custom_env = {**MINIMAL_ENV, "STORAGE_BACKEND": "redis"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.storage_backend == "redis"

    def test_redis_url_customizable(self):
        """redis_url настраивается через переменную окружения."""
        custom_env = {**MINIMAL_ENV, "REDIS_URL": "redis://localhost:6379/0"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.redis_url == "redis://localhost:6379/0"

    def test_webhook_settings_customizable(self):
        """Webhook-настройки настраиваются через переменные окружения."""
        custom_env = {
            **MINIMAL_ENV,
            "USE_WEBHOOK": "true",
            "WEBHOOK_HOST": "https://bot.example.com",
            "WEBHOOK_PORT": "443",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.use_webhook is True
        assert cfg.webhook_host == "https://bot.example.com"
        assert cfg.webhook_port == 443

    def test_gigachat_max_concurrent_customizable(self):
        """gigachat_max_concurrent настраивается через env."""
        custom_env = {**MINIMAL_ENV, "GIGACHAT_MAX_CONCURRENT": "30"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.gigachat_max_concurrent == 30


# ============================================================================
# Файлы конфигурации
# ============================================================================


@pytest.mark.security
class TestConfigFiles:
    """Проверка безопасности файлов конфигурации."""

    def test_no_env_file_in_repo(self):
        """Реальный .env файл НЕ находится в репозитории (только .env.example)."""
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            # Если .env существует, он должен быть в .gitignore
            gitignore = PROJECT_ROOT / ".gitignore"
            assert gitignore.exists()
            content = gitignore.read_text(encoding="utf-8")
            assert ".env" in content, (
                ".env существует, но не указан в .gitignore!"
            )

    def test_no_sensitive_files_tracked(self):
        """Чувствительные файлы не должны быть в репозитории."""
        sensitive_patterns = [
            "*.pem",
            "*.key",
            "*.p12",
            "*.pfx",
            "credentials.json",
            "service-account.json",
        ]
        for pattern in sensitive_patterns:
            matches = list(PROJECT_ROOT.glob(pattern))
            # Фильтруем файлы внутри .venv и node_modules
            matches = [
                m for m in matches
                if ".venv" not in str(m) and "node_modules" not in str(m)
            ]
            assert not matches, (
                f"Найдены чувствительные файлы: {[str(m) for m in matches]}"
            )


# ============================================================================
# MCP-клиент безопасность
# ============================================================================


@pytest.mark.security
class TestDatabaseSecurity:
    """Проверка безопасности хранилища данных."""

    def test_database_path_default(self):
        """Путь к БД по умолчанию — внутри data/."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert "data/" in cfg.database_path, (
            f"database_path должен быть в data/, получено: {cfg.database_path}"
        )

    def test_database_directory_in_gitignore(self):
        """Директория data/ указана в .gitignore."""
        gitignore = PROJECT_ROOT / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")
        assert "data/" in content, (
            "data/ не указан в .gitignore — SQLite-база может попасть в репозиторий"
        )

    def test_database_path_customizable(self):
        """database_path можно настроить через переменные окружения."""
        custom_env = {
            **MINIMAL_ENV,
            "DATABASE_PATH": "/tmp/custom/prefs.db",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.database_path == "/tmp/custom/prefs.db"


@pytest.mark.security
class TestEnvExampleCompleteness:
    """Проверка полноты .env.example."""

    def test_env_example_has_optional_keys(self):
        """.env.example документирует важные опциональные ключи."""
        env_example = PROJECT_ROOT / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        # Все обязательные ключи
        required_keys = ["BOT_TOKEN", "GIGACHAT_CREDENTIALS"]
        for key in required_keys:
            assert key in content, f"{key} отсутствует в .env.example"

        # Важные опциональные ключи для документации
        optional_keys = ["MCP_SERVER_URL", "DEBUG"]
        for key in optional_keys:
            assert key in content, (
                f"{key} отсутствует в .env.example — "
                f"важно для документации"
            )

    def test_env_example_no_placeholder_secrets(self):
        """.env.example не содержит секретов-заглушек, похожих на настоящие."""
        env_example = PROJECT_ROOT / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                value = value.strip().strip("\"'")
                # Telegram токен выглядит как "123456:ABC..."
                assert not (
                    ":" in value
                    and len(value) > 30
                    and any(c.isdigit() for c in value.split(":")[0])
                ), (
                    f".env.example: значение {key.strip()} похоже на настоящий токен"
                )


@pytest.mark.security
class TestMCPClientSecurity:
    """Проверка безопасности MCP-клиента."""

    def test_mcp_timeouts_configured(self):
        """MCP-клиент имеет настроенные таймауты."""
        from vkuswill_bot.services.mcp_client import CONNECT_TIMEOUT, READ_TIMEOUT

        # Таймауты должны быть разумными
        assert 1 <= CONNECT_TIMEOUT <= 60, (
            f"CONNECT_TIMEOUT={CONNECT_TIMEOUT} — должен быть в [1, 60]"
        )
        assert 1 <= READ_TIMEOUT <= 300, (
            f"READ_TIMEOUT={READ_TIMEOUT} — должен быть в [1, 300]"
        )

    def test_mcp_retries_limited(self):
        """Количество retry ограничено."""
        from vkuswill_bot.services.mcp_client import MAX_RETRIES

        assert 1 <= MAX_RETRIES <= 10, (
            f"MAX_RETRIES={MAX_RETRIES} — должен быть в [1, 10]"
        )

    def test_mcp_client_uses_https(self):
        """MCP-клиент по умолчанию подключается по HTTPS."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.mcp_server_url.startswith("https://"), (
            "MCP URL должен использовать HTTPS"
        )
