"""Tests for LLM-based user intent classification."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from vkuswill_bot.agents.intent_classifier import (
    IntentClassificationResult,
    _parse_profile,
    classify_user_intent,
)
from vkuswill_bot.services.prompts import PromptProfile
from vkuswill_bot.services.prompt_registry import init_registry, reset_registry


class _TraceGenerationSpy:
    def __init__(self) -> None:
        self.end_calls: list[dict] = []

    def end(self, **kwargs):  # type: ignore[no-untyped-def]
        self.end_calls.append(kwargs)


class _TraceSpy:
    def __init__(self) -> None:
        self.generation_calls: list[dict] = []

    def generation(self, **kwargs):  # type: ignore[no-untyped-def]
        self.generation_calls.append(kwargs)
        return _TraceGenerationSpy()


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
            ("meal_plan", "meal_plan"),
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
    @pytest.fixture(autouse=True)
    def _cleanup_registry(self):
        reset_registry()
        yield
        reset_registry()

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        adapter = AsyncMock()
        return adapter

    async def test_returns_cart_for_order_intent(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("cart")
        result = await classify_user_intent("закажи суп", mock_adapter, "test-model")
        assert result == IntentClassificationResult(profile="cart", raw_output="cart")

    async def test_returns_recipe_for_cooking_intent(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("recipe")
        result = await classify_user_intent("приготовь борщ", mock_adapter, "test-model")
        assert result == IntentClassificationResult(profile="recipe", raw_output="recipe")

    async def test_returns_status(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("status")
        result = await classify_user_intent("где мой заказ", mock_adapter, "test-model")
        assert result == IntentClassificationResult(profile="status", raw_output="status")

    async def test_parses_structured_classification_response(self, mock_adapter):
        raw_output = (
            '{"profile":"meal_plan","confidence":0.93,"reason":"weekly menu for several people"}'
        )
        mock_adapter.create_completion.return_value = _llm_response(raw_output)
        result = await classify_user_intent(
            "собери корзину на неделю для 4 человек",
            mock_adapter,
            "test-model",
        )

        assert result == IntentClassificationResult(
            profile="meal_plan",
            confidence=0.93,
            reason="weekly menu for several people",
            raw_output=raw_output,
        )

    async def test_returns_none_on_invalid_response(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("something_invalid")
        result = await classify_user_intent("hello", mock_adapter, "test-model")
        assert result == IntentClassificationResult(profile=None, raw_output="something_invalid")

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
        assert call_kwargs["max_tokens"] == 120
        assert call_kwargs["temperature"] == 0.0
        assert "закажи суп" in call_kwargs["messages"][0]["content"]

    async def test_returns_none_on_empty_response(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("")
        result = await classify_user_intent("привет", mock_adapter, "test-model")
        assert result == IntentClassificationResult(profile=None, raw_output="")

    async def test_records_separate_generation_in_trace(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("cart")
        trace = _TraceSpy()

        result = await classify_user_intent("закажи суп", mock_adapter, "test-model", trace=trace)

        assert result == IntentClassificationResult(profile="cart", raw_output="cart")
        assert trace.generation_calls
        generation = trace.generation_calls[-1]
        assert generation["name"] == "intent-classification"
        assert generation["model_parameters"]["max_tokens"] == 120
        assert generation["model_parameters"]["temperature"] == 0.0
        assert generation["metadata"]["prompt"]["name"] == "classify-intent"

    async def test_uses_registry_prompt_metadata_in_trace(self, mock_adapter):
        mock_adapter.create_completion.return_value = _llm_response("recipe")
        init_registry(env_overrides={"classify-intent": "Classify {text}"}, label="staging")
        trace = _TraceSpy()

        result = await classify_user_intent(
            "приготовь суп",
            mock_adapter,
            "test-model",
            trace=trace,
        )

        assert result == IntentClassificationResult(profile="recipe", raw_output="recipe")
        generation = trace.generation_calls[-1]
        assert generation["metadata"]["prompt"]["source"] == "env"
        assert generation["metadata"]["prompt"]["label"] == "staging"


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
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = "cart"

        state = await build_turn_state(agent=agent, user_id=1, text="закажи суп")

        assert state.prompt_profile == "cart"
        agent._classify_intent.assert_awaited_once_with("закажи суп")
        assert state.llm_prompt_profile == "cart"
        assert state.llm_prompt_confidence is None
        assert state.intent_conflict is False

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
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = None

        state = await build_turn_state(agent=agent, user_id=1, text="закажи молоко")

        assert state.prompt_profile == "cart"
        assert state.llm_prompt_profile is None

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
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.side_effect = RuntimeError("boom")

        state = await build_turn_state(agent=agent, user_id=1, text="рецепт борща")

        assert state.prompt_profile == "recipe"
        assert state.llm_prompt_profile is None

    async def test_loads_structured_profile_from_bundle_loader(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        class _BundleAgent:
            def __init__(self) -> None:
                self._history: dict[int, list[dict[str, str]]] = {}
                self._last_cart_snapshot: dict[int, dict[str, str]] = {}
                self._prompt_profiles_enabled = True
                self._compact_followup_prompt_enabled = True
                self._max_tool_calls = 5
                self._max_input_chars_per_turn = 250000
                self._llm_routing_strategy = "single_provider"

            def _should_start_fresh_context(self, *, text: str, history):  # type: ignore[no-untyped-def]
                return True

            def _normalize_history(self, history):  # type: ignore[no-untyped-def]
                return history

            async def _classify_intent(self, text: str):  # type: ignore[no-untyped-def]
                return "cart"

            async def _load_user_preferences(self, user_id: int):  # type: ignore[no-untyped-def]
                return {"legacy": "value"}

            async def _load_user_preferences_bundle(self, user_id: int):  # type: ignore[no-untyped-def]
                return (
                    {"молоко": "безлактозное"},
                    {"schema_version": 1, "hard_constraints": {"diet": "vegan"}},
                )

        state = await build_turn_state(agent=_BundleAgent(), user_id=1, text="закажи молоко")
        assert state.user_preferences == {"молоко": "безлактозное"}
        assert state.user_preference_profile["hard_constraints"]["diet"] == "vegan"

    async def test_meal_plan_profile_marks_cart_intent(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = "meal_plan"

        state = await build_turn_state(agent=agent, user_id=1, text="собери меню на неделю")

        assert state.prompt_profile == "meal_plan"
        assert state.cart_intent is True

    async def test_meal_plan_profile_falls_back_when_intent_routing_disabled(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._meal_plan_intent_routing_enabled = False
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = "meal_plan"

        state = await build_turn_state(
            agent=agent, user_id=1, text="собери меню на неделю для 2 человек"
        )

        assert state.prompt_profile == "cart"
        assert state.cart_intent is True

    async def test_tracks_conflict_metadata_when_llm_disagrees_with_heuristic(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = IntentClassificationResult(
            profile="recipe",
            confidence=0.41,
            reason="mentions food preparation",
            raw_output='{"profile":"recipe","confidence":0.41}',
        )

        state = await build_turn_state(
            agent=agent,
            user_id=1,
            text="собери корзину на неделю для 4 человек",
        )

        assert state.prompt_profile == "meal_plan"
        assert state.heuristic_prompt_profile == "meal_plan"
        assert state.llm_prompt_profile == "recipe"
        assert state.llm_prompt_confidence == pytest.approx(0.41)
        assert state.llm_prompt_reason == "mentions food preparation"
        assert state.intent_conflict is True
        assert state.intent_conflict_severity == "high"
        assert state.route_override_applied is True
        assert state.route_override_from == "recipe"
        assert state.route_override_to == "meal_plan"
        assert state.route_override_reason == "heuristic_meal_plan_override"

    async def test_abstract_meal_slots_override_recipe_to_cart(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = IntentClassificationResult(
            profile="recipe",
            confidence=0.96,
            reason="перечислены конкретные приёмы пищи",
            raw_output='{"profile":"recipe","confidence":0.96}',
        )

        state = await build_turn_state(
            agent=agent,
            user_id=1,
            text="собери мне завтрак и обед",
        )

        assert state.prompt_profile == "cart"
        assert state.heuristic_prompt_profile == "cart"
        assert state.llm_prompt_profile == "recipe"
        assert state.intent_conflict is True
        assert state.route_override_applied is True
        assert state.route_override_from == "recipe"
        assert state.route_override_to == "cart"
        assert state.route_override_reason == "abstract_meal_slots_override"
        assert state.multi_course_expected == 0
        assert all(
            "Мульти-курс:" not in str(message.get("content", ""))
            for message in state.history
            if isinstance(message, dict)
        )

    async def test_single_meal_request_with_constraints_stays_recipe(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = IntentClassificationResult(
            profile="recipe",
            confidence=0.95,
            reason="конкретный приём пищи с ограничениями",
            raw_output='{"profile":"recipe","confidence":0.95}',
        )

        state = await build_turn_state(
            agent=agent,
            user_id=1,
            text="собери на завтрак, но без яиц и без глютена",
        )

        assert state.prompt_profile == "recipe"
        assert state.route_override_applied is False
        assert state.multi_course_expected == 1

    async def test_explicit_product_list_override_recipe_to_cart(self):
        from vkuswill_bot.agents.shopping_turn_types import build_turn_state

        agent = AsyncMock()
        agent._history = {}
        agent._last_cart_snapshot = {}
        agent._prompt_profiles_enabled = True
        agent._compact_followup_prompt_enabled = True
        agent._max_tool_calls = 5
        agent._max_input_chars_per_turn = 250000
        agent._llm_routing_strategy = "single_provider"
        agent._should_start_fresh_context = MagicMock(return_value=True)
        agent._normalize_history = MagicMock(side_effect=lambda h: h)
        agent._load_user_preferences.return_value = {}
        agent._classify_intent.return_value = IntentClassificationResult(
            profile="recipe",
            confidence=0.94,
            reason="meal-themed request",
            raw_output='{"profile":"recipe","confidence":0.94}',
        )

        state = await build_turn_state(
            agent=agent,
            user_id=1,
            text="кето-завтрак на двоих: авокадо, бекон, яйца, сливочный сыр",
        )

        assert state.prompt_profile == "cart"
        assert state.route_override_applied is True
        assert state.route_override_from == "recipe"
        assert state.route_override_to == "cart"
        assert state.route_override_reason == "explicit_list_cart_override"
        assert [row["search_query"] for row in state.direct_cart_requests] == [
            "авокадо",
            "бекон",
            "яйца",
            "сливочный сыр",
        ]


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
        assert result == IntentClassificationResult(profile="cart", raw_output="cart")
        mock_adapter.create_completion.assert_awaited_once()
