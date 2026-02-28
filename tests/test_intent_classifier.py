"""Tests for LLM-based user intent classification."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from vkuswill_bot.agents.intent_classifier import (
    _parse_profile,
    classify_user_intent,
)
from vkuswill_bot.services.prompts import PromptProfile


def _llm_response(content: str) -> dict:
    """Build a minimal OpenAI-compatible response dict."""
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


class TestParseProfile:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("cart", "cart"),
            ("recipe", "recipe"),
            ("status", "status"),
            ("linking", "linking"),
            ("general", "general"),
            ("  cart  ", "cart"),
            ("cart.", "cart"),
            ("CART", "cart"),
            ("Recipe", "recipe"),
            ("I think it is cart", "cart"),
            ("", None),
            ("unknown", None),
            ("buy_something", None),
        ],
    )
    def test_parse_profile(self, raw: str, expected: PromptProfile | None):
        assert _parse_profile(raw) == expected


class TestClassifyUserIntent:
    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        adapter = AsyncMock()
        return adapter

    async def test_returns_cart_for_order_intent(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("cart")
        result = await classify_user_intent("закажи суп", mock_adapter, "test-model")
        assert result == "cart"

    async def test_returns_recipe_for_cooking_intent(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("recipe")
        result = await classify_user_intent("приготовь борщ", mock_adapter, "test-model")
        assert result == "recipe"

    async def test_returns_status(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("status")
        result = await classify_user_intent("где мой заказ", mock_adapter, "test-model")
        assert result == "status"

    async def test_returns_none_on_invalid_response(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("something_invalid")
        result = await classify_user_intent("hello", mock_adapter, "test-model")
        assert result is None

    async def test_returns_none_on_timeout(self, mock_adapter):
        async def slow_call(**kwargs):
            await asyncio.sleep(10)
            return _llm_response("cart")

        mock_adapter.create_completion.side_effect = slow_call
        result = await classify_user_intent(
            "закажи суп",
            mock_adapter,
            "test-model",
            timeout_seconds=0.05,
        )
        assert result is None

    async def test_returns_none_on_adapter_error(self, mock_adapter):
        mock_adapter.create_completion.side_effect = RuntimeError("API error")
        result = await classify_user_intent("закажи суп", mock_adapter, "test-model")
        assert result is None

    async def test_passes_correct_params_to_adapter(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("cart")
        await classify_user_intent("закажи суп", mock_adapter, "my-model", timeout_seconds=3.0)
        mock_adapter.create_completion.assert_awaited_once()
        call_kwargs = mock_adapter.create_completion.call_args.kwargs
        assert call_kwargs["model"] == "my-model"
        assert call_kwargs["tools"] == []
        assert call_kwargs["tool_choice"] == "none"
        assert call_kwargs["max_tokens"] == 20
        assert call_kwargs["temperature"] == 0.0
        assert "закажи суп" in call_kwargs["messages"][0]["content"]

    async def test_returns_none_on_empty_response(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("")
        result = await classify_user_intent("привет", mock_adapter, "test-model")
        assert result is None


class TestBuildTurnStateIntegration:
    """Verify that build_turn_state uses LLM classification with keyword fallback."""

    async def test_llm_profile_overrides_keywords(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._should_start_fresh_context.return_value = True
        agent._normalize_history.side_effect = lambda h: h
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = "cart"

        state = await build_turn_state(agent=agent, user_id=1, text="закажи суп")

        assert state.prompt_profile == "cart"
        agent._classify_intent.assert_awaited_once_with("закажи суп")

    async def test_falls_back_to_keywords_when_llm_returns_none(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._should_start_fresh_context.return_value = True
        agent._normalize_history.side_effect = lambda h: h
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = None

        state = await build_turn_state(agent=agent, user_id=1, text="закажи молоко")

        assert state.prompt_profile == "cart"

    async def test_falls_back_to_keywords_when_llm_raises(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._should_start_fresh_context.return_value = True
        agent._normalize_history.side_effect = lambda h: h
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.side_effect = RuntimeError("boom")

        state = await build_turn_state(agent=agent, user_id=1, text="рецепт борща")

        assert state.prompt_profile == "recipe"


class TestShoppingAgentClassifyIntent:
    """Verify ShoppingAgent._classify_intent respects feature flag."""

    async def test_returns_none_when_disabled(self):
        from vkuswill_bot.agents.shopping_agent import ShoppingAgent

        agent = ShoppingAgent(
            llm_base_url="http://fake",
            llm_api_key="fake-key",
            llm_model="test-model",
            llm_max_concurrent=1,
            mcp_client=AsyncMock(),
            dialog_manager=AsyncMock(),
            intent_classification_enabled=False,
            llm_adapters={"qwen_openai": AsyncMock()},
        )
        result = await agent._classify_intent("закажи суп")
        assert result is None

    async def test_calls_llm_when_enabled(self):
        from vkuswill_bot.agents.shopping_agent import ShoppingAgent

        mock_adapter = AsyncMock()
        mock_adapter.create_completion.return_value = _llm_response("cart")

        agent = ShoppingAgent(
            llm_base_url="http://fake",
            llm_api_key="fake-key",
            llm_model="test-model",
            llm_max_concurrent=1,
            mcp_client=AsyncMock(),
            dialog_manager=AsyncMock(),
            intent_classification_enabled=True,
            intent_classification_timeout=3.0,
            llm_adapters={"qwen_openai": mock_adapter},
        )
        result = await agent._classify_intent("закажи суп")
        assert result == "cart"
        mock_adapter.create_completion.assert_awaited_once()
