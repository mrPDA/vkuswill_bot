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
from pydantic_settings.exceptions import SettingsError

from vkuswill_bot.config import Config


# Корень проекта
PROJECT_ROOT = Path(__file__).parent.parent

# Минимальный набор env-переменных для создания Config (ADR-006: legacy GigaChat удалён)
MINIMAL_ENV = {
    "BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
    "LLM_API_KEY": "test-llm-key",
    "LLM_MODEL": "test-model",
}


# ============================================================================
# Обязательные поля
# ============================================================================


@pytest.mark.security
class TestRequiredFields:
    """Проверка обязательных полей конфигурации."""

    def test_bot_token_required(self):
        """BOT_TOKEN обязателен — без него Config не создаётся."""
        env = {"LLM_API_KEY": "key", "LLM_MODEL": "model"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Config(
                    _env_file=None,  # type: ignore[call-arg]
                )
            errors = exc_info.value.errors()
            field_names = [e["loc"][0] for e in errors]
            assert "bot_token" in field_names

    def test_config_loads_with_shopping_agent_env(self):
        """Config создаётся с минимальным набором для shopping_agent."""
        env = {
            "BOT_TOKEN": "123:ABC",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "gpt://folder/model/latest",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.chat_engine == "shopping_agent"
        assert cfg.llm_api_key == "test-key"


# ============================================================================
# Безопасные значения по умолчанию
# ============================================================================


@pytest.mark.security
class TestDefaultValues:
    """Проверка безопасности значений по умолчанию."""

    def test_chat_engine_default_shopping_agent(self):
        """chat_engine по умолчанию — shopping_agent (ADR-006)."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.chat_engine == "shopping_agent"

    def test_llm_base_url_default_https(self):
        """llm_base_url по умолчанию использует HTTPS."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_base_url.startswith("https://")

    def test_llm_provider_default_qwen_openai(self):
        """llm_provider по умолчанию — qwen_openai."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_provider == "qwen_openai"

    def test_llm_routing_strategy_default_single_provider(self):
        """llm_routing_strategy по умолчанию — single_provider."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_routing_strategy == "single_provider"

    def test_legacy_routing_provider_env_keys_are_ignored(self):
        """Legacy routing provider env-поля не влияют на конфиг."""
        custom_env = {
            **MINIMAL_ENV,
            "LLM_SINGLETON_PROVIDER": "gigachat_sdk",
            "LLM_BURST_PROVIDER": "qwen_openai",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_provider == "qwen_openai"

    def test_llm_max_concurrent_default(self):
        """llm_max_concurrent по умолчанию — 10."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_max_concurrent == 10

    def test_llm_max_tokens_default(self):
        """llm_max_tokens по умолчанию — 900."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_max_tokens == 900

    def test_llm_temperature_default(self):
        """llm_temperature по умолчанию — 0.2."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_temperature == 0.2

    def test_llm_prompt_profiles_default_disabled(self):
        """Профили промптов по умолчанию отключены (безопасный rollout)."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_prompt_profiles_enabled is False

    def test_llm_compact_followup_prompt_default_enabled(self):
        """Компактный follow-up промпт включён по умолчанию."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_compact_followup_prompt_enabled is True

    def test_meal_plan_intent_routing_default_disabled(self):
        """Маршрутизация meal-plan выключена по умолчанию для безопасного rollout."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.meal_plan_intent_routing_enabled is False

    def test_meal_plan_executor_default_disabled(self):
        """Отдельный meal-plan executor выключен по умолчанию для staged rollout."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.meal_plan_executor_enabled is False

    def test_meal_plan_shadow_mode_default_disabled(self):
        """Shadow-mode по умолчанию выключен."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.meal_plan_shadow_mode_enabled is False

    def test_meal_plan_rollout_percent_default_full(self):
        """Rollout percent по умолчанию 100 (управляется флагами)."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.meal_plan_rollout_percent == 100

    def test_meal_plan_allow_unvalidated_rollout_default_disabled(self):
        """Unsafe rollout override по умолчанию выключен."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.meal_plan_allow_unvalidated_rollout is False

    def test_meal_plan_unvalidated_rollout_audit_defaults_empty(self):
        """Поля аудита non-prod bypass по умолчанию пустые."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.meal_plan_unvalidated_rollout_reason == ""
        assert cfg.meal_plan_unvalidated_rollout_actor == ""
        assert cfg.meal_plan_unvalidated_rollout_expires_at == ""

    def test_meal_plan_unvalidated_rollout_max_ttl_default_and_bounds(self):
        """TTL bypass-а ограничен и имеет безопасный default."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.meal_plan_unvalidated_rollout_max_ttl_seconds == 86400

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
            f"max_tool_calls={cfg.max_tool_calls} — должен быть в диапазоне [1, 50]"
        )

    def test_max_tool_calls_default(self):
        """max_tool_calls по умолчанию — 10."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.max_tool_calls == 10

    def test_max_history_reasonable(self):
        """Лимит истории разумный (не > 200)."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert 1 <= cfg.max_history_messages <= 200, (
            f"max_history_messages={cfg.max_history_messages} — должен быть в диапазоне [1, 200]"
        )

    def test_mcp_server_url_is_https(self):
        """MCP-сервер по умолчанию использует HTTPS."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.mcp_server_url.startswith("https://"), (
            f"MCP URL должен быть HTTPS, получено: {cfg.mcp_server_url}"
        )

    def test_mcp_server_api_keys_default_empty(self):
        """mcp_server_api_keys по умолчанию пустой словарь."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.mcp_server_api_keys == {}

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

    def test_webhook_path_default(self):
        """webhook_path по умолчанию — /webhook."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.webhook_path == "/webhook"

    def test_db_pool_min_default(self):
        """db_pool_min по умолчанию — 2."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.db_pool_min == 2

    def test_db_pool_max_default(self):
        """db_pool_max по умолчанию — 10."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.db_pool_max == 10

    def test_db_pool_min_max_reasonable(self):
        """Размеры пула разумные: 1 <= min <= max <= 100."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert 1 <= cfg.db_pool_min <= cfg.db_pool_max <= 100

    def test_admin_user_ids_default_empty(self):
        """admin_user_ids по умолчанию — пустой список."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.admin_user_ids == []

    # --- Freemium лимиты ---

    def test_free_trial_days_default(self):
        """free_trial_days по умолчанию — 10."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.free_trial_days == 10

    def test_free_cart_limit_default(self):
        """free_cart_limit по умолчанию — 0 (после trial)."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.free_cart_limit == 0

    def test_bonus_cart_limit_default(self):
        """bonus_cart_limit по умолчанию — 5."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.bonus_cart_limit == 5

    def test_referral_cart_bonus_default(self):
        """referral_cart_bonus по умолчанию — 3."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.referral_cart_bonus == 3

    def test_feedback_cart_bonus_default(self):
        """feedback_cart_bonus по умолчанию — 2."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.feedback_cart_bonus == 2

    def test_feedback_bonus_cooldown_default(self):
        """feedback_bonus_cooldown_days по умолчанию — 30."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.feedback_bonus_cooldown_days == 30

    def test_freemium_limits_reasonable(self):
        """Freemium лимиты в разумных пределах."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert 1 <= cfg.free_trial_days <= 365
        assert 0 <= cfg.free_cart_limit <= 100
        assert 1 <= cfg.bonus_cart_limit <= 100
        assert 1 <= cfg.referral_cart_bonus <= 100
        assert 1 <= cfg.feedback_cart_bonus <= 100
        assert 1 <= cfg.feedback_bonus_cooldown_days <= 365

    def test_webhook_cert_path_default_empty(self):
        """webhook_cert_path по умолчанию — пустая строка."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.webhook_cert_path == ""

    # --- S3 Log ---

    def test_s3_log_disabled_by_default(self):
        """S3-логирование отключено по умолчанию."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_enabled is False

    def test_s3_log_bucket_default_empty(self):
        """s3_log_bucket по умолчанию — пустая строка."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_bucket == ""

    def test_s3_log_prefix_default(self):
        """s3_log_prefix по умолчанию — 'logs'."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_prefix == "logs"

    def test_s3_log_endpoint_default_https(self):
        """s3_log_endpoint по умолчанию использует HTTPS."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_endpoint.startswith("https://")

    def test_s3_log_region_default(self):
        """s3_log_region по умолчанию — ru-central1."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_region == "ru-central1"

    def test_s3_log_access_key_default_empty(self):
        """s3_log_access_key по умолчанию — пустая строка."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_access_key == ""

    def test_s3_log_secret_key_default_empty(self):
        """s3_log_secret_key по умолчанию — пустая строка."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_secret_key == ""

    def test_s3_log_flush_interval_default(self):
        """s3_log_flush_interval по умолчанию — 60 секунд."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_flush_interval == 60

    def test_s3_log_flush_size_default(self):
        """s3_log_flush_size по умолчанию — 500 записей."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_flush_size == 500

    def test_s3_log_flush_interval_reasonable(self):
        """s3_log_flush_interval разумный: [5, 3600]."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert 5 <= cfg.s3_log_flush_interval <= 3600

    def test_s3_log_flush_size_reasonable(self):
        """s3_log_flush_size разумный: [10, 50000]."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert 10 <= cfg.s3_log_flush_size <= 50000

    # --- Langfuse ---

    def test_langfuse_disabled_by_default(self):
        """Langfuse отключён по умолчанию."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.langfuse_enabled is False

    def test_langfuse_host_default_https(self):
        """langfuse_host по умолчанию использует HTTPS."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.langfuse_host.startswith("https://")

    def test_langfuse_keys_default_empty(self):
        """Langfuse-ключи по умолчанию — пустые строки."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.langfuse_public_key == ""
        assert cfg.langfuse_secret_key == ""


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

        required_keys = ["BOT_TOKEN"]
        for key in required_keys:
            assert key in content, f"{key} отсутствует в .env.example"

    def test_config_loads_from_env_not_hardcoded(self):
        """Config загружает значения из env, а не из кода."""
        custom_env = {
            "BOT_TOKEN": "custom-token-123",
            "LLM_API_KEY": "custom-llm-key-456",
            "LLM_MODEL": "gpt://folder/model/latest",
            "CHAT_ENGINE": "shopping_agent",
            "MCP_SERVER_URL": "https://custom-mcp.example.com/mcp",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]

        assert cfg.bot_token == "custom-token-123"  # noqa: S105
        assert cfg.llm_api_key == "custom-llm-key-456"
        assert cfg.chat_engine == "shopping_agent"
        assert cfg.mcp_server_url == "https://custom-mcp.example.com/mcp"

    def test_chat_engine_customizable(self):
        """chat_engine настраивается через env."""
        custom_env = {**MINIMAL_ENV, "CHAT_ENGINE": "shopping_agent"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.chat_engine == "shopping_agent"

    def test_chat_engine_invalid_rejected(self):
        """Некорректный chat_engine отклоняется валидатором."""
        custom_env = {**MINIMAL_ENV, "CHAT_ENGINE": "unknown"}
        with patch.dict(os.environ, custom_env, clear=True), pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_llm_settings_customizable(self):
        """LLM-параметры настраиваются через env."""
        custom_env = {
            **MINIMAL_ENV,
            "LLM_PROVIDER": "qwen_openai",
            "LLM_BASE_URL": "https://llm.api.cloud.yandex.net/v1",
            "LLM_API_KEY": "yc-llm-test-key",
            "LLM_MODEL": "gpt://folder/qwen3-235b-a22b-fp8/latest",
            "LLM_MAX_CONCURRENT": "16",
            "LLM_MAX_TOKENS": "1024",
            "LLM_TEMPERATURE": "0.1",
            "LLM_PROMPT_PROFILES_ENABLED": "true",
            "LLM_COMPACT_FOLLOWUP_PROMPT_ENABLED": "false",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_provider == "qwen_openai"
        assert cfg.llm_base_url == "https://llm.api.cloud.yandex.net/v1"
        assert cfg.llm_api_key == "yc-llm-test-key"
        assert cfg.llm_model == "gpt://folder/qwen3-235b-a22b-fp8/latest"
        assert cfg.llm_max_concurrent == 16
        assert cfg.llm_max_tokens == 1024
        assert cfg.llm_temperature == 0.1
        assert cfg.llm_prompt_profiles_enabled is True
        assert cfg.llm_compact_followup_prompt_enabled is False

    def test_llm_provider_alias_openai_compatible(self):
        """llm_provider принимает алиас openai_compatible."""
        custom_env = {**MINIMAL_ENV, "LLM_PROVIDER": "openai_compatible"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.llm_provider == "qwen_openai"

    def test_llm_provider_invalid_rejected(self):
        """Некорректный llm_provider отклоняется валидатором."""
        custom_env = {**MINIMAL_ENV, "LLM_PROVIDER": "unknown_provider"}
        with patch.dict(os.environ, custom_env, clear=True), pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_llm_max_tokens_invalid_rejected(self):
        """llm_max_tokens ниже минимального отклоняется валидатором."""
        custom_env = {**MINIMAL_ENV, "LLM_MAX_TOKENS": "0"}
        with patch.dict(os.environ, custom_env, clear=True), pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_llm_temperature_invalid_rejected(self):
        """llm_temperature вне диапазона [0.0, 1.0] отклоняется валидатором."""
        custom_env = {**MINIMAL_ENV, "LLM_TEMPERATURE": "1.5"}
        with patch.dict(os.environ, custom_env, clear=True), pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_llm_routing_strategy_invalid_rejected(self):
        """Некорректный llm_routing_strategy отклоняется валидатором."""
        custom_env = {**MINIMAL_ENV, "LLM_ROUTING_STRATEGY": "invalid"}
        with patch.dict(os.environ, custom_env, clear=True), pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_llm_routing_strategy_legacy_multi_rejected(self):
        """Legacy multi-provider стратегия отклоняется валидатором."""
        custom_env = {
            **MINIMAL_ENV,
            "LLM_ROUTING_STRATEGY": "single_user_gigachat_multi_user_qwen",
        }
        with patch.dict(os.environ, custom_env, clear=True), pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_storage_backend_redis_rejected(self):
        """storage_backend='redis' is not yet supported — must fail fast."""
        custom_env = {**MINIMAL_ENV, "STORAGE_BACKEND": "redis"}
        with patch.dict(os.environ, custom_env, clear=True), pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_storage_backend_memory_accepted(self):
        """storage_backend='memory' is the only supported value."""
        custom_env = {**MINIMAL_ENV, "STORAGE_BACKEND": "memory"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.storage_backend == "memory"

    def test_mcp_server_api_keys_customizable(self):
        """mcp_server_api_keys настраивается через JSON в env."""
        custom_env = {
            **MINIMAL_ENV,
            "MCP_SERVER_API_KEYS": '{"agent_a":"key-a","agent_b":"key-b"}',
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.mcp_server_api_keys == {"agent_a": "key-a", "agent_b": "key-b"}

    def test_mcp_server_api_keys_invalid_json_rejected(self):
        """Некорректный JSON в MCP_SERVER_API_KEYS приводит к ошибке парсинга."""
        custom_env = {**MINIMAL_ENV, "MCP_SERVER_API_KEYS": "not-a-json"}
        with patch.dict(os.environ, custom_env, clear=True), pytest.raises(SettingsError):
            Config(_env_file=None)  # type: ignore[call-arg]

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
            "WEBHOOK_PATH": "/webhook-stg/",
            "WEBHOOK_PORT": "443",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.use_webhook is True
        assert cfg.webhook_host == "https://bot.example.com"
        assert cfg.webhook_path == "/webhook-stg"
        assert cfg.webhook_port == 443

    def test_webhook_path_without_leading_slash_is_normalized(self):
        """WEBHOOK_PATH без префикса / нормализуется."""
        custom_env = {**MINIMAL_ENV, "WEBHOOK_PATH": "webhook-stg"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.webhook_path == "/webhook-stg"

    def test_db_pool_customizable(self):
        """db_pool_min/max настраиваются через переменные окружения."""
        custom_env = {**MINIMAL_ENV, "DB_POOL_MIN": "5", "DB_POOL_MAX": "20"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.db_pool_min == 5
        assert cfg.db_pool_max == 20

    def test_admin_user_ids_customizable(self):
        """admin_user_ids настраивается через переменную окружения."""
        custom_env = {**MINIMAL_ENV, "ADMIN_USER_IDS": "[111, 222, 333]"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.admin_user_ids == [111, 222, 333]

    def test_database_url_customizable(self):
        """database_url (PostgreSQL) настраивается через env."""
        custom_env = {**MINIMAL_ENV, "DATABASE_URL": "postgresql://user:pass@localhost:5432/bot"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.database_url == "postgresql://user:pass@localhost:5432/bot"

    def test_freemium_limits_customizable(self):
        """Freemium лимиты настраиваются через переменные окружения."""
        custom_env = {
            **MINIMAL_ENV,
            "FREE_TRIAL_DAYS": "14",
            "FREE_CART_LIMIT": "10",
            "BONUS_CART_LIMIT": "8",
            "REFERRAL_CART_BONUS": "5",
            "FEEDBACK_CART_BONUS": "4",
            "FEEDBACK_BONUS_COOLDOWN_DAYS": "21",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.free_trial_days == 14
        assert cfg.free_cart_limit == 10
        assert cfg.bonus_cart_limit == 8
        assert cfg.referral_cart_bonus == 5
        assert cfg.feedback_cart_bonus == 4
        assert cfg.feedback_bonus_cooldown_days == 21

    def test_webhook_cert_path_customizable(self):
        """webhook_cert_path настраивается через env."""
        custom_env = {**MINIMAL_ENV, "WEBHOOK_CERT_PATH": "/etc/ssl/bot.pem"}
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.webhook_cert_path == "/etc/ssl/bot.pem"

    def test_s3_log_settings_customizable(self):
        """S3-логирование полностью настраивается через env."""
        custom_env = {
            **MINIMAL_ENV,
            "S3_LOG_ENABLED": "true",
            "S3_LOG_BUCKET": "my-logs",
            "S3_LOG_PREFIX": "bot-logs",
            "S3_LOG_ENDPOINT": "https://s3.custom.com",
            "S3_LOG_REGION": "eu-west-1",
            "S3_LOG_ACCESS_KEY": "AKIA_TEST",
            "S3_LOG_SECRET_KEY": "secret_test_key",
            "S3_LOG_FLUSH_INTERVAL": "30",
            "S3_LOG_FLUSH_SIZE": "1000",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.s3_log_enabled is True
        assert cfg.s3_log_bucket == "my-logs"
        assert cfg.s3_log_prefix == "bot-logs"
        assert cfg.s3_log_endpoint == "https://s3.custom.com"
        assert cfg.s3_log_region == "eu-west-1"
        assert cfg.s3_log_access_key == "AKIA_TEST"
        assert cfg.s3_log_secret_key == "secret_test_key"  # noqa: S105
        assert cfg.s3_log_flush_interval == 30
        assert cfg.s3_log_flush_size == 1000

    def test_langfuse_settings_customizable(self):
        """Langfuse полностью настраивается через env."""
        custom_env = {
            **MINIMAL_ENV,
            "LANGFUSE_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
            "LANGFUSE_HOST": "https://langfuse.custom.com",
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.langfuse_enabled is True
        assert cfg.langfuse_public_key == "pk-lf-test"
        assert cfg.langfuse_secret_key == "sk-lf-test"  # noqa: S105
        assert cfg.langfuse_host == "https://langfuse.custom.com"


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
            assert ".env" in content, ".env существует, но не указан в .gitignore!"

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
            matches = [m for m in matches if ".venv" not in str(m) and "node_modules" not in str(m)]
            assert not matches, f"Найдены чувствительные файлы: {[str(m) for m in matches]}"


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
            "DATABASE_PATH": "/tmp/custom/prefs.db",  # noqa: S108
        }
        with patch.dict(os.environ, custom_env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.database_path == "/tmp/custom/prefs.db"  # noqa: S108


@pytest.mark.security
class TestEnvExampleCompleteness:
    """Проверка полноты .env.example."""

    def test_env_example_has_optional_keys(self):
        """.env.example документирует важные опциональные ключи."""
        env_example = PROJECT_ROOT / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        # Все обязательные ключи
        required_keys = ["BOT_TOKEN"]
        for key in required_keys:
            assert key in content, f"{key} отсутствует в .env.example"

        # Важные опциональные ключи для документации (ADR-006: GigaChat удалён)
        optional_keys = [
            "CHAT_ENGINE",
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_BASE_URL",
            "MCP_SERVER_URL",
            "DEBUG",
        ]
        for key in optional_keys:
            assert key in content, f"{key} отсутствует в .env.example — важно для документации"

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
                ), f".env.example: значение {key.strip()} похоже на настоящий токен"


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
        assert 1 <= READ_TIMEOUT <= 300, f"READ_TIMEOUT={READ_TIMEOUT} — должен быть в [1, 300]"

    def test_mcp_retries_limited(self):
        """Количество retry ограничено."""
        from vkuswill_bot.services.mcp_client import MAX_RETRIES

        assert 1 <= MAX_RETRIES <= 10, f"MAX_RETRIES={MAX_RETRIES} — должен быть в [1, 10]"

    def test_mcp_client_uses_https(self):
        """MCP-клиент по умолчанию подключается по HTTPS."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.mcp_server_url.startswith("https://"), "MCP URL должен использовать HTTPS"


class TestAdminUserIdsValidator:
    """Тесты валидатора admin_user_ids — парсинг из разных форматов.

    pydantic-settings JSON-парсит env-строку для complex-типов (list[int])
    ДО передачи в pydantic field_validator. Поэтому:
    - "391887253" → json.loads → int 391887253 → validator → [391887253]
    - "[111,222]" → json.loads → list [111,222] → validator → [111,222]
    """

    def test_single_int_from_env(self):
        """Lockbox передаёт одно число — должен стать списком из 1 элемента."""
        env = {**MINIMAL_ENV, "ADMIN_USER_IDS": "391887253"}
        with patch.dict(os.environ, env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.admin_user_ids == [391887253]

    def test_json_array_from_env(self):
        """JSON-массив — несколько админов."""
        env = {**MINIMAL_ENV, "ADMIN_USER_IDS": "[111, 222, 333]"}
        with patch.dict(os.environ, env, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.admin_user_ids == [111, 222, 333]

    def test_not_set_default(self):
        """Не задан → пустой список (дефолт)."""
        with patch.dict(os.environ, MINIMAL_ENV, clear=True):
            cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.admin_user_ids == []
