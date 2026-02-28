"""Тесты фабрики chat engine (feature-flag runtime)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vkuswill_bot.services.chat_engine_factory import create_chat_engine


def _cfg(**overrides: object) -> SimpleNamespace:
    """ADR-006: legacy GigaChat удалён, только shopping_agent."""
    base = {
        "chat_engine": "shopping_agent",
        "max_tool_calls": 20,
        "max_history_messages": 50,
        "llm_base_url": "https://llm.api.cloud.yandex.net/v1",
        "llm_provider": "qwen_openai",
        "llm_routing_strategy": "single_provider",
        "llm_api_key": "key",
        "llm_model": "gpt://folder/model/latest",
        "llm_max_concurrent": 10,
        "llm_max_tokens": 900,
        "llm_temperature": 0.2,
        "llm_prompt_profiles_enabled": False,
        "llm_compact_followup_prompt_enabled": True,
        "intent_classification_enabled": False,
        "intent_classification_timeout": 5.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _deps() -> dict[str, object]:
    return {
        "mcp_client": object(),
        "preferences_store": object(),
        "recipe_store": object(),
        "dialog_manager": object(),
        "tool_executor": object(),
        "langfuse_service": object(),
    }


def test_create_chat_engine_shopping_requires_api_key() -> None:
    cfg = _cfg(llm_api_key="", llm_model="model")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_requires_model() -> None:
    cfg = _cfg(llm_api_key="key", llm_model="")
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_rejects_gigachat_provider() -> None:
    cfg = _cfg(
        llm_provider="gigachat_sdk",
        llm_api_key="key",
        llm_model="gpt://folder/model/latest",
    )
    with pytest.raises(RuntimeError, match="qwen_openai"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_rejects_legacy_routing_strategy() -> None:
    cfg = _cfg(
        llm_routing_strategy="single_user_gigachat_multi_user_qwen",
        llm_provider="qwen_openai",
        llm_model="gpt://folder/model/latest",
        llm_api_key="key",
    )
    with pytest.raises(RuntimeError, match="single_provider"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_routing_invalid_strategy() -> None:
    cfg = _cfg(
        llm_routing_strategy="unknown",
    )
    with pytest.raises(RuntimeError, match="LLM_ROUTING_STRATEGY"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_module_unavailable() -> None:
    cfg = _cfg(llm_api_key="key", llm_model="model")
    with (
        patch(
            "vkuswill_bot.services.chat_engine_factory.importlib.import_module",
            side_effect=ModuleNotFoundError("no module"),
        ),
        pytest.raises(RuntimeError, match="ShoppingAgent is unavailable"),
    ):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_success() -> None:
    cfg = _cfg(llm_max_concurrent=12)
    deps = _deps()
    shopping_cls = MagicMock()
    fake_module = SimpleNamespace(ShoppingAgent=shopping_cls)

    with patch(
        "vkuswill_bot.services.chat_engine_factory.importlib.import_module",
        return_value=fake_module,
    ) as import_module:
        engine = create_chat_engine(cfg=cfg, **deps)

    import_module.assert_called_once_with("vkuswill_bot.agents.shopping_agent")
    assert engine is shopping_cls.return_value
    shopping_cls.assert_called_once_with(
        llm_base_url=cfg.llm_base_url,
        llm_api_key=cfg.llm_api_key,
        llm_model=cfg.llm_model,
        llm_max_concurrent=cfg.llm_max_concurrent,
        llm_provider=cfg.llm_provider,
        llm_routing_strategy=cfg.llm_routing_strategy,
        mcp_client=deps["mcp_client"],
        dialog_manager=deps["dialog_manager"],
        max_tool_calls=cfg.max_tool_calls,
        max_history=cfg.max_history_messages,
        langfuse_service=deps["langfuse_service"],
        llm_max_tokens=cfg.llm_max_tokens,
        llm_temperature=cfg.llm_temperature,
        prompt_profiles_enabled=cfg.llm_prompt_profiles_enabled,
        compact_followup_prompt_enabled=cfg.llm_compact_followup_prompt_enabled,
        preferences_store=deps["preferences_store"],
        intent_classification_enabled=cfg.intent_classification_enabled,
        intent_classification_timeout=cfg.intent_classification_timeout,
    )


def test_create_chat_engine_shopping_passes_prompt_profile_flags() -> None:
    cfg = _cfg(
        llm_prompt_profiles_enabled=True,
        llm_compact_followup_prompt_enabled=False,
    )
    deps = _deps()
    shopping_cls = MagicMock()
    fake_module = SimpleNamespace(ShoppingAgent=shopping_cls)

    with patch(
        "vkuswill_bot.services.chat_engine_factory.importlib.import_module",
        return_value=fake_module,
    ):
        create_chat_engine(cfg=cfg, **deps)

    kwargs = shopping_cls.call_args.kwargs
    assert kwargs["prompt_profiles_enabled"] is True
    assert kwargs["compact_followup_prompt_enabled"] is False
