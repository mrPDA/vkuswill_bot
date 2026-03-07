"""Types and turn state assembly for shopping turn execution."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from vkuswill_bot.agents.intent_classifier import IntentClassificationResult
from vkuswill_bot.agents.product_index_manager import build_product_index_from_history
from vkuswill_bot.agents.prompt_helpers import ensure_system_prompt, resolve_prompt_profile
from vkuswill_bot.agents.recipe_pantry import (
    extract_explicit_pantry_requests,
    has_explicit_egg_pack_request,
)
from vkuswill_bot.agents.recipe_parsing import extract_structured_ingredient_requests
from vkuswill_bot.agents.response_analysis import is_cart_intent
from vkuswill_bot.services.prompts import PromptProfile


class ShoppingTurnAgentProtocol(Protocol):
    _history: dict[int, list[dict[str, Any]]]
    _last_cart_snapshot: dict[int, dict[str, Any]]
    _prompt_profiles_enabled: bool
    _compact_followup_prompt_enabled: bool
    _max_tool_calls: int
    _max_input_chars_per_turn: int
    _llm_routing_strategy: str

    def _should_start_fresh_context(
        self,
        *,
        text: str,
        history: list[dict[str, Any]] | None,
    ) -> bool: ...

    def _normalize_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    async def _classify_intent(
        self,
        text: str,
        *,
        trace: Any | None = None,
    ) -> PromptProfile | IntentClassificationResult | None: ...

    async def _load_user_preferences(self, user_id: int) -> dict[str, str]: ...
    async def _load_user_preferences_bundle(
        self,
        user_id: int,
    ) -> tuple[dict[str, str], dict[str, Any]]: ...

    async def _get_tools(self) -> list[dict[str, Any]]: ...

    def _create_trace(
        self,
        *,
        user_id: int,
        text: str,
        llm_provider: str,
        prompt_profile: PromptProfile | None,
    ) -> Any | None: ...

    def _resolve_model_for_provider(self, llm_provider: str) -> str: ...

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
        max_tokens_override: int | None = None,
    ) -> Any: ...

    @staticmethod
    def _extract_usage_details(response: Any) -> dict[str, int] | None: ...

    def _ensure_cart_price_summary(
        self,
        *,
        cart_data: dict[str, Any],
        product_index: dict[int, dict[str, Any]],
    ) -> None: ...

    def _trim_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str: ...

    def _capture_cart_snapshot(
        self,
        *,
        user_id: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None: ...

    def _prepare_tool_result_for_history(self, tool_name: str, tool_result: str) -> str: ...

    async def _recover_cart_from_recipe_search_history(
        self,
        *,
        history: list[dict[str, Any]],
        llm_provider: str,
        call_cache: dict[str, str],
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]: ...

    async def _recover_cart_from_recipe_ingredients_history(
        self,
        *,
        history: list[dict[str, Any]],
        llm_provider: str,
        call_cache: dict[str, str],
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]: ...


@dataclass(slots=True)
class TurnState:
    history: list[dict[str, Any]]
    previous_cart_products: list[Any]
    prompt_profile: PromptProfile
    product_index_this_turn: dict[int, dict[str, Any]]
    cart_intent: bool
    explicit_pantry_requests: set[str]
    explicit_egg_pack_request: bool
    requested_ingredients: list[dict[str, Any]]
    user_preferences: dict[str, str]
    user_preference_profile: dict[str, Any] = field(default_factory=dict)
    llm_prompt_profile: PromptProfile | None = None
    llm_prompt_confidence: float | None = None
    llm_prompt_reason: str | None = None
    heuristic_prompt_profile: PromptProfile = "general"
    intent_conflict: bool = False
    intent_conflict_severity: str | None = None
    cart_data_this_turn: dict[str, Any] | None = None
    manual_recovery_used: bool = False
    cart_creation_recovery_used: bool = False
    search_batch_recovery_used: bool = False
    cart_flow_recovery_used: bool = False
    recipe_to_cart_recovery_used: bool = False
    step_budget_warning_used: bool = False
    single_search_steps_streak: int = 0
    tools_called_this_turn: bool = False
    recipe_flow_started_this_turn: bool = False
    total_llm_input_chars: int = 0
    mcp_call_cache: dict[str, str] = field(default_factory=dict)
    search_query_by_xml_id_this_turn: dict[int, str] = field(default_factory=dict)


def _normalize_llm_classification(
    result: PromptProfile | IntentClassificationResult | None,
) -> tuple[PromptProfile | None, float | None, str | None]:
    if isinstance(result, IntentClassificationResult):
        return result.profile, result.confidence, result.reason
    return result, None, None


def _resolve_intent_conflict_severity(
    *,
    llm_profile: PromptProfile | None,
    heuristic_profile: PromptProfile,
    llm_confidence: float | None,
) -> str | None:
    if llm_profile is None or llm_profile == heuristic_profile or heuristic_profile == "general":
        return None
    if llm_confidence is None or llm_confidence < 0.5:
        return "high"
    if llm_confidence < 0.8:
        return "medium"
    return "low"


async def build_turn_state(
    *,
    agent: ShoppingTurnAgentProtocol,
    user_id: int,
    text: str,
    trace: Any | None = None,
) -> TurnState:
    history = agent._history.get(user_id)
    previous_cart_snapshot = agent._last_cart_snapshot.get(user_id)
    previous_cart_products = (
        previous_cart_snapshot.get("products")
        if isinstance(previous_cart_snapshot, dict)
        and isinstance(previous_cart_snapshot.get("products"), list)
        else []
    )
    if agent._should_start_fresh_context(text=text, history=history):
        history = None

    # LLM-классификация и загрузка preferences — независимы, запускаем параллельно.
    if trace is None:
        classify_task = asyncio.create_task(agent._classify_intent(text))
    else:
        classify_task = asyncio.create_task(agent._classify_intent(text, trace=trace))
    bundle_loader = getattr(type(agent), "_load_user_preferences_bundle", None)
    if callable(bundle_loader):
        prefs_bundle_task = asyncio.create_task(agent._load_user_preferences_bundle(user_id))
        prefs_task: asyncio.Task[dict[str, str]] | None = None
    else:
        prefs_bundle_task = None
        prefs_task = asyncio.create_task(agent._load_user_preferences(user_id))

    llm_profile: PromptProfile | None = None
    llm_confidence: float | None = None
    llm_reason: str | None = None
    with contextlib.suppress(Exception):
        llm_profile, llm_confidence, llm_reason = _normalize_llm_classification(await classify_task)

    heuristic_profile = resolve_prompt_profile(text=text, history=history)
    prompt_profile = llm_profile or heuristic_profile
    intent_conflict_severity = _resolve_intent_conflict_severity(
        llm_profile=llm_profile,
        heuristic_profile=heuristic_profile,
        llm_confidence=llm_confidence,
    )
    if prompt_profile == "meal_plan" and not getattr(
        agent, "_meal_plan_intent_routing_enabled", True
    ):
        prompt_profile = "cart" if is_cart_intent(text) else "recipe"
    history = ensure_system_prompt(
        history=history,
        prompt_profile=prompt_profile,
        mode="start",
        prompt_profiles_enabled=agent._prompt_profiles_enabled,
    )

    product_index_this_turn: dict[int, dict[str, Any]] = build_product_index_from_history(history)
    history.append({"role": "user", "content": text})
    normalized_history = agent._normalize_history(history)

    user_preferences: dict[str, str] = {}
    user_preference_profile: dict[str, Any] = {}
    if prefs_bundle_task is not None:
        with contextlib.suppress(Exception):
            prefs_bundle = await prefs_bundle_task
            if isinstance(prefs_bundle, tuple) and len(prefs_bundle) == 2:
                prefs_value, profile_value = prefs_bundle
                if isinstance(prefs_value, dict):
                    user_preferences = prefs_value
                if isinstance(profile_value, dict):
                    user_preference_profile = profile_value
    elif prefs_task is not None:
        user_preferences = await prefs_task

    cart_intent = is_cart_intent(text) or prompt_profile in ("cart", "recipe", "meal_plan")

    return TurnState(
        history=normalized_history,
        previous_cart_products=previous_cart_products,
        prompt_profile=prompt_profile,
        product_index_this_turn=product_index_this_turn,
        cart_intent=cart_intent,
        explicit_pantry_requests=extract_explicit_pantry_requests(text),
        explicit_egg_pack_request=has_explicit_egg_pack_request(text),
        requested_ingredients=extract_structured_ingredient_requests(text),
        user_preferences=user_preferences,
        user_preference_profile=user_preference_profile,
        llm_prompt_profile=llm_profile,
        llm_prompt_confidence=llm_confidence,
        llm_prompt_reason=llm_reason,
        heuristic_prompt_profile=heuristic_profile,
        intent_conflict=intent_conflict_severity is not None,
        intent_conflict_severity=intent_conflict_severity,
    )
