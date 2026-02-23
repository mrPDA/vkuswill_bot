"""Тесты фабрики chat engine (feature-flag runtime)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vkuswill_bot.services.chat_engine_factory import create_chat_engine


def _cfg(**overrides: object) -> SimpleNamespace:
    base = {
        "chat_engine": "legacy",
        "gigachat_credentials": "creds",
        "gigachat_model": "GigaChat-2-Max",
        "gigachat_scope": "GIGACHAT_API_PERS",
        "max_tool_calls": 20,
        "max_history_messages": 50,
        "gigachat_max_concurrent": 15,
        "gigachat_ca_bundle": "certs/russian_ca_bundle.pem",
        "llm_base_url": "https://llm.api.cloud.yandex.net/v1",
        "llm_provider": "qwen_openai",
        "llm_routing_strategy": "single_provider",
        "llm_singleton_provider": "gigachat_sdk",
        "llm_burst_provider": "qwen_openai",
        "llm_api_key": "",
        "llm_model": "",
        "llm_max_concurrent": 10,
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


def test_create_chat_engine_legacy() -> None:
    cfg = _cfg(chat_engine="legacy")
    deps = _deps()

    with patch("vkuswill_bot.services.chat_engine_factory.GigaChatService") as cls:
        engine = create_chat_engine(cfg=cfg, **deps)

    assert engine is cls.return_value
    cls.assert_called_once_with(
        credentials=cfg.gigachat_credentials,
        model=cfg.gigachat_model,
        scope=cfg.gigachat_scope,
        mcp_client=deps["mcp_client"],
        preferences_store=deps["preferences_store"],
        recipe_store=deps["recipe_store"],
        max_tool_calls=cfg.max_tool_calls,
        max_history=cfg.max_history_messages,
        dialog_manager=deps["dialog_manager"],
        tool_executor=deps["tool_executor"],
        gigachat_max_concurrent=cfg.gigachat_max_concurrent,
        langfuse_service=deps["langfuse_service"],
        ca_bundle_file=cfg.gigachat_ca_bundle,
    )


def test_create_chat_engine_shopping_requires_api_key() -> None:
    cfg = _cfg(chat_engine="shopping_agent", llm_api_key="", llm_model="model")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_requires_model() -> None:
    cfg = _cfg(chat_engine="shopping_agent", llm_api_key="key", llm_model="")
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_gigachat_provider_uses_fallback_model() -> None:
    cfg = _cfg(
        chat_engine="shopping_agent",
        llm_provider="gigachat_sdk",
        llm_api_key="",
        llm_model="",
        gigachat_model="GigaChat-2-Max",
        gigachat_credentials="creds",
    )
    deps = _deps()
    shopping_cls = MagicMock()
    fake_module = SimpleNamespace(ShoppingAgent=shopping_cls)

    with patch(
        "vkuswill_bot.services.chat_engine_factory.importlib.import_module",
        return_value=fake_module,
    ):
        create_chat_engine(cfg=cfg, **deps)

    shopping_cls.assert_called_once()
    kwargs = shopping_cls.call_args.kwargs
    assert kwargs["llm_provider"] == "gigachat_sdk"
    assert kwargs["gigachat_model"] == "GigaChat-2-Max"


def test_create_chat_engine_shopping_routing_requires_burst_model() -> None:
    cfg = _cfg(
        chat_engine="shopping_agent",
        llm_provider="gigachat_sdk",
        llm_routing_strategy="single_user_gigachat_multi_user_qwen",
        llm_singleton_provider="gigachat_sdk",
        llm_burst_provider="qwen_openai",
        llm_model="",
        llm_api_key="key",
        gigachat_credentials="creds",
    )
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_routing_invalid_strategy() -> None:
    cfg = _cfg(
        chat_engine="shopping_agent",
        llm_provider="qwen_openai",
        llm_routing_strategy="unknown",
        llm_model="gpt://folder/model/latest",
        llm_api_key="key",
    )
    with pytest.raises(RuntimeError, match="LLM_ROUTING_STRATEGY"):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_module_unavailable() -> None:
    cfg = _cfg(chat_engine="shopping_agent", llm_api_key="key", llm_model="model")
    with (
        patch(
            "vkuswill_bot.services.chat_engine_factory.importlib.import_module",
            side_effect=ModuleNotFoundError("no module"),
        ),
        pytest.raises(RuntimeError, match="ShoppingAgent is unavailable"),
    ):
        create_chat_engine(cfg=cfg, **_deps())


def test_create_chat_engine_shopping_success() -> None:
    cfg = _cfg(
        chat_engine="shopping_agent",
        llm_api_key="key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=12,
    )
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
        llm_singleton_provider=cfg.llm_singleton_provider,
        llm_burst_provider=cfg.llm_burst_provider,
        mcp_client=deps["mcp_client"],
        dialog_manager=deps["dialog_manager"],
        max_tool_calls=cfg.max_tool_calls,
        max_history=cfg.max_history_messages,
        langfuse_service=deps["langfuse_service"],
        gigachat_credentials=cfg.gigachat_credentials,
        gigachat_scope=cfg.gigachat_scope,
        gigachat_ca_bundle=cfg.gigachat_ca_bundle,
        gigachat_model=cfg.gigachat_model,
    )
