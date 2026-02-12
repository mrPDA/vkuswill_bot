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
    gigachat_model: str = "GigaChat-2-Max"
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
    webhook_cert_path: str = ""  # путь к самоподписанному SSL-сертификату для Telegram

    # S3 логирование (Yandex Object Storage)
    s3_log_enabled: bool = False
    s3_log_bucket: str = ""
    s3_log_prefix: str = "logs"
    s3_log_endpoint: str = "https://storage.yandexcloud.net"
    s3_log_region: str = "ru-central1"
    s3_log_access_key: str = ""
    s3_log_secret_key: str = ""
    s3_log_flush_interval: int = 60  # секунд
    s3_log_flush_size: int = 500  # записей

    # Langfuse (LLM-observability)
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_anonymize_messages: bool = False  # полностью скрывать текст сообщений

    # Отладка
    debug: bool = False


config = Config()
