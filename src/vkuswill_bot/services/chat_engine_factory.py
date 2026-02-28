"""Фабрика chat engine (ADR-006: shopping_agent only)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from vkuswill_bot.services.chat_engine import ChatEngineProtocol
from vkuswill_bot.services.llm_adapters import normalize_llm_provider

if TYPE_CHECKING:
    from vkuswill_bot.config import Config
    from vkuswill_bot.services.dialog_manager import DialogManager
    from vkuswill_bot.services.langfuse_tracing import LangfuseService
    from vkuswill_bot.services.mcp_client import VkusvillMCPClient
    from vkuswill_bot.services.preferences_store import PreferencesStore
    from vkuswill_bot.services.recipe_store import RecipeStore
    from vkuswill_bot.services.redis_dialog_manager import RedisDialogManager


def create_chat_engine(
    *,
    cfg: Config,
    mcp_client: VkusvillMCPClient,
    preferences_store: PreferencesStore | None,
    recipe_store: RecipeStore | None,
    dialog_manager: DialogManager | RedisDialogManager,
    tool_executor: Any = None,
    langfuse_service: LangfuseService,
) -> ChatEngineProtocol:
    """Создать ShoppingAgent chat engine (legacy GigaChat удалён)."""

    provider = normalize_llm_provider(cfg.llm_provider)
    routing_strategy = cfg.llm_routing_strategy.strip().lower()

    if provider != "qwen_openai":
        raise RuntimeError(
            "Only LLM_PROVIDER=qwen_openai is supported (gigachat_sdk removed)",
        )
    if not cfg.llm_api_key.strip():
        raise RuntimeError("LLM_API_KEY is required")
    if not cfg.llm_model.strip():
        raise RuntimeError("LLM_MODEL is required")
    if routing_strategy != "single_provider":
        raise RuntimeError(
            "LLM_ROUTING_STRATEGY must be single_provider",
        )

    try:
        module = importlib.import_module("vkuswill_bot.agents.shopping_agent")
        shopping_agent_cls = module.ShoppingAgent
    except Exception as exc:
        raise RuntimeError(
            "ShoppingAgent is unavailable",
        ) from exc

    return shopping_agent_cls(
        llm_base_url=cfg.llm_base_url,
        llm_api_key=cfg.llm_api_key,
        llm_model=cfg.llm_model.strip(),
        llm_max_concurrent=cfg.llm_max_concurrent,
        llm_provider=provider,
        llm_routing_strategy=routing_strategy,
        mcp_client=mcp_client,
        dialog_manager=dialog_manager,
        max_tool_calls=cfg.max_tool_calls,
        max_history=cfg.max_history_messages,
        langfuse_service=langfuse_service,
        llm_max_tokens=cfg.llm_max_tokens,
        llm_temperature=cfg.llm_temperature,
        prompt_profiles_enabled=cfg.llm_prompt_profiles_enabled,
        compact_followup_prompt_enabled=cfg.llm_compact_followup_prompt_enabled,
        preferences_store=preferences_store,
        intent_classification_enabled=cfg.intent_classification_enabled,
        intent_classification_timeout=cfg.intent_classification_timeout,
    )
