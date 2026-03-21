"""Конфигурация приложения через переменные окружения."""

from __future__ import annotations

import json

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Настройки бота, chat engine и MCP."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Игнорировать legacy GIGACHAT_* и прочие лишние env
    )

    # Telegram
    bot_token: str
    # HTTP(S) прокси для Bot API (CONNECT), если api.telegram.org недоступен с хоста/Pi
    telegram_http_proxy: str = ""

    # Chat engine runtime selection (ADR-006: legacy removed)
    chat_engine: str = "shopping_agent"

    # OpenAI-compatible LLM (Qwen via Yandex Cloud AI Studio)
    llm_provider: str = "qwen_openai"  # qwen_openai
    # single_provider
    llm_routing_strategy: str = "single_provider"
    llm_base_url: str = "https://llm.api.cloud.yandex.net/v1"
    llm_api_key: str = ""
    llm_model: str = Field(
        default="",
        description=(
            "Yandex Cloud AI Studio: gpt://<folder_id>/qwen3-235b-a22b-fp8/latest (см. ADR-004)"
        ),
    )
    llm_max_concurrent: int = 10
    llm_queue_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=120.0,
        description="Таймаут ожидания свободного слота LLM-семафора (graceful degradation)",
    )
    llm_max_tokens: int = Field(default=900, ge=1, le=8192)
    llm_max_tokens_recipe: int = Field(
        default=2048,
        ge=1,
        le=8192,
        description="max_tokens для финального ответа при корзине по рецепту",
    )
    llm_temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    llm_prompt_profiles_enabled: bool = True
    llm_compact_followup_prompt_enabled: bool = True
    intent_classification_enabled: bool = True
    intent_classification_timeout: float = 5.0
    meal_plan_intent_routing_enabled: bool = True
    meal_plan_executor_enabled: bool = True
    meal_plan_shadow_mode_enabled: bool = False
    meal_plan_rollout_percent: int = Field(default=100, ge=0, le=100)
    meal_plan_rollout_kpi_gates_enabled: bool = True
    meal_plan_allow_unvalidated_rollout: bool = False
    meal_plan_unvalidated_rollout_reason: str = ""
    meal_plan_unvalidated_rollout_actor: str = ""
    meal_plan_unvalidated_rollout_expires_at: str = ""
    meal_plan_unvalidated_rollout_max_ttl_seconds: int = Field(default=86400, ge=60, le=604800)

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
    max_tool_calls: int = 10
    max_history_messages: int = 50

    # Хранилище (SQLite — legacy)
    database_path: str = "data/preferences.db"
    recipe_database_path: str = "data/recipes.db"

    @field_validator("storage_backend")
    @classmethod
    def _validate_storage_backend(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized == "redis":
            raise ValueError(
                "storage_backend='redis' is not yet supported for Telegram dialog history. "
                "Use 'memory' (default). Redis backend will be available in a future release."
            )
        if normalized != "memory":
            raise ValueError("storage_backend must be 'memory'")
        return normalized

    # Бэкенд хранилища: "memory" (Redis planned but not yet integrated)
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

    @field_validator("chat_engine")
    @classmethod
    def _validate_chat_engine(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized != "shopping_agent":
            raise ValueError("chat_engine must be 'shopping_agent' (legacy GigaChat removed)")
        return normalized

    @field_validator("llm_provider")
    @classmethod
    def _validate_llm_provider(cls, v: str) -> str:
        normalized = v.strip().lower()
        aliases = {
            "qwen": "qwen_openai",
            "openai": "qwen_openai",
            "openai_compatible": "qwen_openai",
        }
        normalized = aliases.get(normalized, normalized)
        allowed = {"qwen_openai"}
        if normalized not in allowed:
            allowed_values = ", ".join(sorted(allowed))
            raise ValueError(f"llm_provider must be one of: {allowed_values}")
        return normalized

    @field_validator("llm_routing_strategy")
    @classmethod
    def _validate_llm_routing_strategy(cls, v: str) -> str:
        normalized = v.strip().lower()
        allowed = {"single_provider"}
        if normalized not in allowed:
            allowed_values = ", ".join(sorted(allowed))
            raise ValueError(f"llm_routing_strategy must be one of: {allowed_values}")
        return normalized

    @model_validator(mode="after")
    def _validate_llm_routing_consistency(self) -> Config:
        if self.llm_provider != "qwen_openai":
            raise ValueError("llm_provider must be qwen_openai")
        if self.llm_routing_strategy != "single_provider":
            raise ValueError("llm_routing_strategy must be single_provider")
        return self

    @model_validator(mode="after")
    def _validate_webhook_host_when_enabled(self) -> Config:
        if self.use_webhook and not self.webhook_host.strip():
            raise ValueError(
                "USE_WEBHOOK=true requires non-empty WEBHOOK_HOST "
                "(public HTTPS origin without path, e.g. https://bot.example.com)"
            )
        return self

    @model_validator(mode="after")
    def _validate_webhook_tls_key_pair(self) -> Config:
        k = self.webhook_key_path.strip()
        c = self.webhook_cert_path.strip()
        if k and not c:
            raise ValueError(
                "WEBHOOK_KEY_PATH requires WEBHOOK_CERT_PATH (TLS + Telegram setWebhook)"
            )
        return self

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

    @field_validator("webhook_path")
    @classmethod
    def _normalize_webhook_path(cls, v: str) -> str:
        normalized = v.strip()
        if not normalized:
            return "/webhook"
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        if len(normalized) > 1:
            normalized = normalized.rstrip("/")
        return normalized

    # Webhook
    use_webhook: bool = False
    webhook_host: str = ""
    webhook_path: str = "/webhook"
    webhook_port: int = 8080
    webhook_cert_path: str = ""  # публичный PEM для setWebhook (самоподпись) и/или TLS
    webhook_key_path: str = ""  # приватный ключ: если задан вместе с cert — aiohttp слушает HTTPS

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
    debug_api_key: str = ""  # API key для stage-only /debug/* endpoint'ов

    # Промпты (переопределение через env / Lockbox для production)
    system_prompt: str = ""
    recipe_extraction_prompt: str = ""
    prompt_cache_ttl_seconds: int = 300
    prompt_label: str = "production"

    # Отладка
    debug: bool = False


config = Config()
