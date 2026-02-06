"""Конфигурация приложения через переменные окружения."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Настройки бота, GigaChat и MCP."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Telegram
    bot_token: str

    # GigaChat
    gigachat_credentials: str
    gigachat_model: str = "GigaChat"
    gigachat_scope: str = "GIGACHAT_API_PERS"

    # MCP
    mcp_server_url: str = "https://mcp001.vkusvill.ru/mcp"

    # Лимиты
    max_tool_calls: int = 15
    max_history_messages: int = 50

    # Хранилище
    database_path: str = "data/preferences.db"

    # Отладка
    debug: bool = False


config = Config()
