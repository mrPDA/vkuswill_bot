"""Фабрика chat engine для feature-flag выбора runtime."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from vkuswill_bot.services.chat_engine import ChatEngineProtocol
from vkuswill_bot.services.gigachat_service import GigaChatService
from vkuswill_bot.services.llm_adapters import normalize_llm_provider

if TYPE_CHECKING:
    from vkuswill_bot.config import Config
    from vkuswill_bot.services.dialog_manager import DialogManager
    from vkuswill_bot.services.langfuse_tracing import LangfuseService
    from vkuswill_bot.services.mcp_client import VkusvillMCPClient
    from vkuswill_bot.services.preferences_store import PreferencesStore
    from vkuswill_bot.services.recipe_store import RecipeStore
    from vkuswill_bot.services.redis_dialog_manager import RedisDialogManager
    from vkuswill_bot.services.tool_executor import ToolExecutor


def create_chat_engine(
    *,
    cfg: Config,
    mcp_client: VkusvillMCPClient,
    preferences_store: PreferencesStore | None,
    recipe_store: RecipeStore | None,
    dialog_manager: DialogManager | RedisDialogManager,
    tool_executor: ToolExecutor,
    langfuse_service: LangfuseService,
) -> ChatEngineProtocol:
    """Создать chat engine согласно feature-flag `chat_engine`."""

    def _validate_provider_requirements(*, provider_name: str) -> str:
        normalized_provider = normalize_llm_provider(provider_name)
        if normalized_provider == "qwen_openai":
            if not cfg.llm_api_key.strip():
                raise RuntimeError("CHAT_ENGINE=shopping_agent requires LLM_API_KEY")
            model_name_local = cfg.llm_model.strip()
            if not model_name_local:
                raise RuntimeError("CHAT_ENGINE=shopping_agent requires LLM_MODEL")
            return model_name_local
        if normalized_provider == "gigachat_sdk":
            if not cfg.gigachat_credentials.strip():
                raise RuntimeError("CHAT_ENGINE=shopping_agent requires GIGACHAT_CREDENTIALS")
            model_name_local = cfg.gigachat_model.strip()
            if not model_name_local:
                raise RuntimeError("CHAT_ENGINE=shopping_agent requires GIGACHAT_MODEL")
            return model_name_local
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider_name}")

    engine_name = cfg.chat_engine
    if engine_name == "legacy":
        return GigaChatService(
            credentials=cfg.gigachat_credentials,
            model=cfg.gigachat_model,
            scope=cfg.gigachat_scope,
            mcp_client=mcp_client,
            preferences_store=preferences_store,
            recipe_store=recipe_store,
            max_tool_calls=cfg.max_tool_calls,
            max_history=cfg.max_history_messages,
            dialog_manager=dialog_manager,
            tool_executor=tool_executor,
            gigachat_max_concurrent=cfg.gigachat_max_concurrent,
            langfuse_service=langfuse_service,
            ca_bundle_file=cfg.gigachat_ca_bundle,
        )

    if engine_name == "shopping_agent":
        provider = normalize_llm_provider(cfg.llm_provider)
        routing_strategy = cfg.llm_routing_strategy.strip().lower()
        _validate_provider_requirements(provider_name=provider)
        if routing_strategy == "single_user_gigachat_multi_user_qwen":
            _validate_provider_requirements(provider_name=cfg.llm_singleton_provider)
            _validate_provider_requirements(provider_name=cfg.llm_burst_provider)
        elif routing_strategy != "single_provider":
            raise RuntimeError(f"Unsupported LLM_ROUTING_STRATEGY: {cfg.llm_routing_strategy}")

        try:
            module = importlib.import_module("vkuswill_bot.agents.shopping_agent")
            shopping_agent_cls = module.ShoppingAgent
        except Exception as exc:  # pragma: no cover - fail-fast в runtime/интеграции
            raise RuntimeError(
                "CHAT_ENGINE=shopping_agent configured, but ShoppingAgent is unavailable",
            ) from exc

        return shopping_agent_cls(
            llm_base_url=cfg.llm_base_url,
            llm_api_key=cfg.llm_api_key,
            llm_model=cfg.llm_model.strip(),
            llm_max_concurrent=cfg.llm_max_concurrent,
            llm_provider=provider,
            llm_routing_strategy=routing_strategy,
            llm_singleton_provider=cfg.llm_singleton_provider,
            llm_burst_provider=cfg.llm_burst_provider,
            mcp_client=mcp_client,
            dialog_manager=dialog_manager,
            max_tool_calls=cfg.max_tool_calls,
            max_history=cfg.max_history_messages,
            langfuse_service=langfuse_service,
            llm_max_tokens=cfg.llm_max_tokens,
            llm_temperature=cfg.llm_temperature,
            prompt_profiles_enabled=cfg.llm_prompt_profiles_enabled,
            compact_followup_prompt_enabled=cfg.llm_compact_followup_prompt_enabled,
            gigachat_credentials=cfg.gigachat_credentials,
            gigachat_scope=cfg.gigachat_scope,
            gigachat_ca_bundle=cfg.gigachat_ca_bundle,
            gigachat_model=cfg.gigachat_model,
            preferences_store=preferences_store,
        )

    raise ValueError(f"Unsupported chat_engine: {engine_name}")
