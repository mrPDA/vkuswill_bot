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
    gigachat_max_concurrent: int = 15  # макс. параллельных запросов к GigaChat

    # MCP
    mcp_server_url: str = "https://mcp001.vkusvill.ru/mcp"

    # Лимиты
    max_tool_calls: int = 20
    max_history_messages: int = 50

    # Хранилище (SQLite — legacy)
    database_path: str = "data/preferences.db"
    recipe_database_path: str = "data/recipes.db"

    # Бэкенд хранилища: "redis" | "memory"
    storage_backend: str = "memory"

    # Redis
    redis_url: str = ""

    # PostgreSQL (управление пользователями)
    database_url: str = ""
    db_pool_min: int = 2
    db_pool_max: int = 10

    # Администраторы (Telegram user IDs через запятую)
    admin_user_ids: list[int] = []

    # Webhook
    use_webhook: bool = False
    webhook_host: str = ""
    webhook_port: int = 8080

    # Отладка
    debug: bool = False


config = Config()
