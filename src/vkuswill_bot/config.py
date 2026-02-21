"""Конфигурация приложения через переменные окружения."""

from __future__ import annotations

import json

from pydantic import field_validator
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
    gigachat_ca_bundle: str = "certs/russian_ca_bundle.pem"  # CA-bundle Минцифры для SSL

    # MCP
    mcp_server_url: str = "https://mcp001.vkusvill.ru/mcp"
    # API key для входящих запросов к локальному MCP-серверу (HTTP transport).
    # Пусто = проверка отключена (обратная совместимость / локальная разработка).
    mcp_server_api_key: str = ""
    # Реестр API ключей MCP в JSON-формате:
    # {"agent_a":"key1","agent_b":"key2"}
    # Используется для multi-client сценария; может применяться вместе с mcp_server_api_key.
    mcp_server_api_keys: dict[str, str] = {}

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

    # Администраторы (Telegram user IDs — одно число или JSON-массив [111,222])
    admin_user_ids: list[int] = []

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> list[int]:
        """Принять одиночное число (из Lockbox) или JSON-список.

        pydantic-settings JSON-парсит строку env перед передачей в pydantic:
        - "391887253" → int 391887253 → этот валидатор → [391887253]
        - "[111,222]" → list [111,222] → этот валидатор → [111,222]
        """
        if isinstance(v, list):
            return v
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return []  # type: ignore[return-value]

    @field_validator("mcp_server_api_keys", mode="before")
    @classmethod
    def _parse_mcp_server_api_keys(cls, v: object) -> dict[str, str]:
        """Принять JSON-объект ключей MCP-клиентов из env."""
        if v is None:
            return {}

        raw: object = v
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return {}
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError("mcp_server_api_keys must be a JSON object") from exc

        if not isinstance(raw, dict):
            raise ValueError("mcp_server_api_keys must be a JSON object")

        parsed: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("mcp_server_api_keys entries must be string:string")
            client_id = key.strip()
            api_key = value.strip()
            if client_id and api_key:
                parsed[client_id] = api_key
        return parsed

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
    s3_log_retention_days: int = 90  # автоудаление логов через N дней (152-ФЗ)

    # Langfuse (LLM-observability)
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_anonymize_messages: bool = True  # полностью скрывать текст сообщений (152-ФЗ)

    # Freemium лимиты
    free_trial_days: int = 10  # пробный период с безлимитными корзинами
    free_cart_limit: int = 0  # базовый лимит после trial (без бонусов)
    bonus_cart_limit: int = 5  # Вариант A: бонус за survey
    referral_cart_bonus: int = 3  # Вариант B: бонус за приглашение друга
    feedback_cart_bonus: int = 2  # Вариант C: бонус за feedback по корзине
    feedback_bonus_cooldown_days: int = 30  # частота бонуса за feedback
    voice_link_code_ttl_minutes: int = 10  # TTL одноразового кода привязки Алисы
    voice_link_api_key: str = ""  # API key для /voice-link/* endpoint'ов (вариант 1)

    # Системный промпт (переопределение через env для production)
    system_prompt: str = ""

    # Отладка
    debug: bool = False


config = Config()
