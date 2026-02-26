"""ShoppingAgent: OpenAI-compatible chat engine поверх MCP."""

from __future__ import annotations

import asyncio
import copy
import contextlib
import datetime as dt
import json
import logging
import math
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from vkuswill_bot.agents.intent_markers import (
    ADDITIVE_CART_MARKERS,
    CART_INTENT_MARKERS,
    EXPLICIT_NEW_CART_MARKERS,
    MODIFY_EXISTING_CART_MARKERS,
    PANTRY_TAG_PEPPER,
    PANTRY_TAG_SALT,
    PANTRY_TAG_SUGAR,
    STATUS_QUERY_MARKERS,
)
from vkuswill_bot.agents.recipe_quantity_calculator import RecipeQuantityCalculator
from vkuswill_bot.agents.history_manager import (
    history_char_count,
    sanitize_tool_history,
    trim_history,
    trim_history_by_chars,
)
from vkuswill_bot.agents.cart_output_renderer import (
    extract_cart_safety_note,
    extract_first_url,
    looks_like_cart_ready_reply,
    looks_like_manual_cart_reply,
    looks_like_missing_cart_prices,
    looks_like_wrong_cart_link,
    looks_like_wrong_cart_summary,
    render_stable_cart_output,
    stabilize_cart_output,
)
from vkuswill_bot.agents.recovery_hints import (
    FORCE_BATCH_SEARCH_HINT,
    FORCE_CART_FLOW_CONTINUATION_HINT,
    FORCE_CART_LINK_SOURCE_HINT,
    FORCE_CART_RECOVERY_HINT,
    FORCE_RECIPE_TO_CART_HINT,
)
from vkuswill_bot.agents.tool_result_compactor import (
    ToolResultCompactor,
    extract_price_value,
    normalize_compact_text,
    score_search_candidate,
    tokenize_query_terms,
)
from vkuswill_bot.services.llm_adapters import (
    LLMAdapterProtocol,
    OpenAICompatibleLLMAdapter,
    create_llm_adapter,
    extract_usage_details,
    normalize_llm_provider,
)
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.prompts import (
    RECIPE_EXTRACTION_PROMPT,
    RECIPE_SEARCH_TOOL,
    RECIPE_TOOL,
    PromptMode,
    PromptProfile,
    detect_prompt_profile,
    get_profiled_system_prompt,
    get_system_prompt,
)

if TYPE_CHECKING:
    from vkuswill_bot.services.chat_engine import ProgressCallback
    from vkuswill_bot.services.dialog_manager import DialogManager
    from vkuswill_bot.services.langfuse_tracing import LangfuseService
    from vkuswill_bot.services.mcp_client import VkusvillMCPClient
    from vkuswill_bot.services.preferences_store import PreferencesStore
    from vkuswill_bot.services.redis_dialog_manager import RedisDialogManager

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOOL_CALLS = 20
_DEFAULT_MAX_HISTORY = 30
_DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
_DEFAULT_LLM_RETRIES = 2
_DEFAULT_MCP_TIMEOUT_SECONDS = 15.0
_DEFAULT_MCP_RETRIES = 2
_DEFAULT_LLM_ROUTING_STRATEGY = "single_provider"
_DEFAULT_MAX_TOOL_RESULT_CHARS = 1800
_DEFAULT_MAX_HISTORY_CHARS = 16000
_DEFAULT_MAX_INPUT_CHARS_PER_TURN = 250000
_ERROR_GENERIC = "Не удалось обработать запрос. Попробуйте позже."
_ERROR_TOO_MANY_TOOLS = (
    "Не удалось завершить подбор в пределах лимита шагов. Уточните запрос и попробуйте ещё раз."
)
_MCP_TOOL_NOT_FOUND = "method not found"
_MCP_CACHEABLE_TOOLS = frozenset(
    {
        "vkusvill_products_search",
        "vkusvill_product_details",
        "recipe_ingredients",
        "recipe_search",
    }
)
# Backward-compatible aliases for constants moved to dedicated modules.
_FORCE_CART_RECOVERY_HINT = FORCE_CART_RECOVERY_HINT
_FORCE_CART_LINK_SOURCE_HINT = FORCE_CART_LINK_SOURCE_HINT
_FORCE_CART_FLOW_CONTINUATION_HINT = FORCE_CART_FLOW_CONTINUATION_HINT
_FORCE_BATCH_SEARCH_HINT = FORCE_BATCH_SEARCH_HINT
_FORCE_RECIPE_TO_CART_HINT = FORCE_RECIPE_TO_CART_HINT
_PANTRY_TAG_SALT = PANTRY_TAG_SALT
_PANTRY_TAG_SUGAR = PANTRY_TAG_SUGAR
_PANTRY_TAG_PEPPER = PANTRY_TAG_PEPPER
_MODIFY_EXISTING_CART_MARKERS = MODIFY_EXISTING_CART_MARKERS
_ADDITIVE_CART_MARKERS = ADDITIVE_CART_MARKERS
_EXPLICIT_NEW_CART_MARKERS = EXPLICIT_NEW_CART_MARKERS
_STATUS_QUERY_MARKERS = STATUS_QUERY_MARKERS
_EGG_PACK_SIZE = 10
_DISCRETE_UNITS = frozenset({"шт", "уп", "пач", "бут", "бан", "пак"})


# Backward-compatible alias used throughout this module.
_RecipeQuantityCalculator = RecipeQuantityCalculator


class ShoppingAgent:
    """Новый chat engine для варианта E (Qwen over MCP)."""

    def __init__(
        self,
        *,
        llm_base_url: str,
        llm_api_key: str,
        llm_model: str,
        llm_max_concurrent: int,
        mcp_client: VkusvillMCPClient,
        dialog_manager: DialogManager | RedisDialogManager,
        llm_provider: str = "qwen_openai",
        llm_routing_strategy: str = _DEFAULT_LLM_ROUTING_STRATEGY,
        llm_singleton_provider: str = "gigachat_sdk",
        llm_burst_provider: str = "qwen_openai",
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
        max_history: int = _DEFAULT_MAX_HISTORY,
        langfuse_service: LangfuseService | None = None,
        llm_timeout_seconds: float = _DEFAULT_LLM_TIMEOUT_SECONDS,
        llm_max_tokens: int | None = None,
        llm_temperature: float | None = None,
        prompt_profiles_enabled: bool = False,
        compact_followup_prompt_enabled: bool = True,
        llm_retries: int = _DEFAULT_LLM_RETRIES,
        mcp_timeout_seconds: float = _DEFAULT_MCP_TIMEOUT_SECONDS,
        mcp_retries: int = _DEFAULT_MCP_RETRIES,
        max_tool_result_chars: int = _DEFAULT_MAX_TOOL_RESULT_CHARS,
        max_history_chars: int = _DEFAULT_MAX_HISTORY_CHARS,
        max_input_chars_per_turn: int = _DEFAULT_MAX_INPUT_CHARS_PER_TURN,
        gigachat_credentials: str = "",
        gigachat_scope: str = "GIGACHAT_API_PERS",
        gigachat_ca_bundle: str | None = None,
        gigachat_model: str = "GigaChat-2-Max",
        preferences_store: PreferencesStore | None = None,
        llm_client: Any | None = None,
        llm_adapters: dict[str, LLMAdapterProtocol] | None = None,
    ) -> None:
        self._llm_provider = normalize_llm_provider(llm_provider)
        self._llm_routing_strategy = llm_routing_strategy.strip().lower()
        self._llm_singleton_provider = normalize_llm_provider(llm_singleton_provider)
        self._llm_burst_provider = normalize_llm_provider(llm_burst_provider)
        self._model = llm_model
        self._gigachat_model = gigachat_model.strip() or llm_model
        self._mcp_client = mcp_client
        self._dialog_manager = dialog_manager
        self._max_tool_calls = max(1, max_tool_calls)
        self._max_history = max(10, max_history)
        self._llm_timeout_seconds = max(1.0, llm_timeout_seconds)
        self._llm_max_tokens = max(1, llm_max_tokens) if llm_max_tokens is not None else None
        self._llm_temperature = (
            max(0.0, min(1.0, llm_temperature)) if llm_temperature is not None else None
        )
        self._prompt_profiles_enabled = bool(prompt_profiles_enabled)
        self._compact_followup_prompt_enabled = bool(compact_followup_prompt_enabled)
        self._llm_retries = max(0, llm_retries)
        self._mcp_timeout_seconds = max(1.0, mcp_timeout_seconds)
        self._mcp_retries = max(0, mcp_retries)
        self._max_tool_result_chars = max(300, max_tool_result_chars)
        self._compactor = ToolResultCompactor(max_tool_result_chars=self._max_tool_result_chars)
        self._max_history_chars = max(10000, max_history_chars)
        self._max_input_chars_per_turn = max(10000, max_input_chars_per_turn)
        self._preferences_store = preferences_store
        self._api_semaphore = asyncio.Semaphore(max(1, llm_max_concurrent))
        self._langfuse = langfuse_service
        self._tools_cache: list[dict[str, Any]] | None = None
        self._mcp_tool_names: set[str] = set()
        self._history: dict[int, list[dict[str, Any]]] = {}
        self._last_cart_snapshot: dict[int, dict[str, Any]] = {}
        self._routing_lock = asyncio.Lock()
        self._active_llm_requests = 0

        self._providers_in_use = self._compute_providers_in_use()
        self._llm_adapters: dict[str, LLMAdapterProtocol] = {}

        if llm_adapters is not None:
            normalized_adapters = {
                normalize_llm_provider(name): adapter for name, adapter in llm_adapters.items()
            }
            self._llm_adapters.update(normalized_adapters)
            missing = self._providers_in_use - set(self._llm_adapters.keys())
            if missing:
                missing_str = ", ".join(sorted(missing))
                raise ValueError(f"llm_adapters are missing providers: {missing_str}")
            return

        if llm_client is not None:
            if len(self._providers_in_use) != 1:
                raise ValueError("llm_client injection supports single-provider routing only")
            provider = next(iter(self._providers_in_use))
            if isinstance(llm_client, LLMAdapterProtocol):
                self._llm_adapters[provider] = llm_client
            else:
                self._llm_adapters[provider] = OpenAICompatibleLLMAdapter(
                    timeout_seconds=self._llm_timeout_seconds,
                    client=llm_client,
                )
            return

        for provider in self._providers_in_use:
            self._llm_adapters[provider] = create_llm_adapter(
                provider=provider,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_timeout_seconds=self._llm_timeout_seconds,
                gigachat_credentials=gigachat_credentials,
                gigachat_scope=gigachat_scope,
                gigachat_ca_bundle=gigachat_ca_bundle,
            )

    def _compute_providers_in_use(self) -> set[str]:
        if self._llm_routing_strategy == _DEFAULT_LLM_ROUTING_STRATEGY:
            return {self._llm_provider}
        if self._llm_routing_strategy == "single_user_gigachat_multi_user_qwen":
            return {self._llm_singleton_provider, self._llm_burst_provider}
        raise ValueError(f"Unsupported llm_routing_strategy: {self._llm_routing_strategy}")

    async def close(self) -> None:
        """Корректно закрыть клиент."""
        for adapter in set(self._llm_adapters.values()):
            with contextlib.suppress(Exception):
                await adapter.close()

    async def reset_conversation(self, user_id: int) -> None:
        self._history.pop(user_id, None)

    async def get_last_cart_snapshot(self, user_id: int) -> dict[str, Any] | None:
        return self._last_cart_snapshot.get(user_id)

    async def process_message(
        self,
        user_id: int,
        text: str,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        """Обработать сообщение пользователя с tool-loop через MCP."""
        async with (
            self._select_provider_for_request() as provider,
            self._dialog_manager.get_lock(user_id),
        ):
            return await self._process_locked(
                user_id=user_id,
                text=text,
                on_progress=on_progress,
                llm_provider=provider,
            )

    @contextlib.asynccontextmanager
    async def _select_provider_for_request(self) -> AsyncIterator[str]:
        if self._llm_routing_strategy == _DEFAULT_LLM_ROUTING_STRATEGY:
            yield self._llm_provider
            return

        async with self._routing_lock:
            self._active_llm_requests += 1
            provider = (
                self._llm_singleton_provider
                if self._active_llm_requests <= 1
                else self._llm_burst_provider
            )
        try:
            yield provider
        finally:
            async with self._routing_lock:
                self._active_llm_requests = max(0, self._active_llm_requests - 1)

    async def _process_locked(
        self,
        *,
        user_id: int,
        text: str,
        on_progress: ProgressCallback | None,
        llm_provider: str,
    ) -> str:
        history = self._history.get(user_id)
        previous_cart_snapshot = self._last_cart_snapshot.get(user_id)
        previous_cart_products = (
            previous_cart_snapshot.get("products")
            if isinstance(previous_cart_snapshot, dict)
            and isinstance(previous_cart_snapshot.get("products"), list)
            else []
        )
        if self._should_start_fresh_context(text=text, history=history):
            history = None
        prompt_profile = self._resolve_prompt_profile(text=text, history=history)
        history = self._ensure_system_prompt(
            history=history,
            prompt_profile=prompt_profile,
            mode="start",
        )

        product_index_this_turn: dict[int, dict[str, Any]] = self._build_product_index_from_history(
            history
        )
        history.append({"role": "user", "content": text})
        history = self._trim_history(history)
        history = self._trim_history_by_chars(history)
        cart_data_this_turn: dict[str, Any] | None = None
        manual_recovery_used = False
        cart_creation_recovery_used = False
        search_batch_recovery_used = False
        cart_flow_recovery_used = False
        recipe_to_cart_recovery_used = False
        single_search_steps_streak = 0
        tools_called_this_turn = False
        recipe_flow_started_this_turn = False
        cart_intent = self._is_cart_intent(text)
        explicit_pantry_requests = self._extract_explicit_pantry_requests(text)
        explicit_egg_pack_request = self._has_explicit_egg_pack_request(text)
        requested_ingredients = self._extract_structured_ingredient_requests(text)
        user_preferences = await self._load_user_preferences(user_id)
        total_llm_input_chars = 0
        mcp_call_cache: dict[str, str] = {}
        search_query_by_xml_id_this_turn: dict[int, str] = {}

        tools = await self._get_tools()
        trace = self._create_trace(
            user_id=user_id,
            text=text,
            llm_provider=llm_provider,
            prompt_profile=prompt_profile,
        )

        async def _progress(message: str) -> None:
            if on_progress is None:
                return
            with contextlib.suppress(Exception):
                await on_progress(message)

        await _progress("\u2699\ufe0f Анализирую запрос...")

        for step in range(1, self._max_tool_calls + 1):
            prompt_mode = self._resolve_prompt_mode(
                step=step,
                expecting_final_answer=cart_data_this_turn is not None,
            )
            llm_input = self._build_llm_input_messages(
                history=history,
                prompt_profile=prompt_profile,
                mode=prompt_mode,
            )
            llm_input_chars = self._history_char_count(llm_input)
            total_llm_input_chars += llm_input_chars
            if step > 1 and total_llm_input_chars > self._max_input_chars_per_turn:
                logger.warning(
                    "ShoppingAgent prompt budget exceeded: total_chars=%d step=%d",
                    total_llm_input_chars,
                    step,
                )
                self._history[user_id] = self._trim_history(self._trim_history_by_chars(history))
                if trace is not None:
                    trace.update(
                        output=_ERROR_TOO_MANY_TOOLS,
                        metadata={
                            "reason": "prompt_budget_exceeded",
                            "provider": llm_provider,
                            "input_chars_total": total_llm_input_chars,
                        },
                    )
                return _ERROR_TOO_MANY_TOOLS
            gen = None
            if trace is not None:
                gen = trace.generation(
                    name=f"shopping-agent-{step}",
                    model=self._resolve_model_for_provider(llm_provider),
                    input=llm_input,
                    model_parameters={
                        "tools": len(tools),
                        "step": step,
                        "provider": llm_provider,
                        "routing_strategy": self._llm_routing_strategy,
                        "prompt_profile": prompt_profile,
                        "prompt_mode": prompt_mode,
                        "compact_prompt": prompt_mode == "compact",
                    },
                )

            try:
                response = await self._call_llm(
                    messages=llm_input,
                    tools=tools,
                    llm_provider=llm_provider,
                )
            except Exception as exc:
                logger.error("ShoppingAgent LLM error: %s", exc, exc_info=True)
                if gen is not None:
                    gen.end(output=str(exc), level="ERROR", status_message="LLM error")
                self._history[user_id] = history
                return _ERROR_GENERIC

            message = self._extract_message(response)
            usage_details = self._extract_usage_details(response)
            usage_source = "provider"
            if usage_details is None:
                usage_details = self._estimate_usage_details(messages=llm_input, message=message)
                usage_source = "estimated" if usage_details is not None else "missing"
                logger.warning(
                    "ShoppingAgent response has no usage details (provider=%s, step=%d, source=%s)",
                    llm_provider,
                    step,
                    usage_source,
                )
            if gen is not None:
                gen.end(
                    output=message,
                    usage_details=usage_details,
                    metadata={
                        "usage_source": usage_source,
                        "provider": llm_provider,
                        "prompt_profile": prompt_profile,
                    },
                )

            tool_calls = self._extract_tool_calls(message)
            if not tool_calls:
                final_text = self._extract_text(message) or _ERROR_GENERIC
                manual_cart_reply = self._looks_like_manual_cart_reply(final_text)

                if (
                    cart_data_this_turn is None
                    and cart_intent
                    and tools_called_this_turn
                    and recipe_flow_started_this_turn
                    and self._looks_like_partial_recipe_reply(final_text)
                    and not cart_flow_recovery_used
                    and step < self._max_tool_calls
                ):
                    cart_flow_recovery_used = True
                    logger.info("Recipe flow recovery: continue tool-chain until cart_link_create")
                    history.append(self._assistant_msg(message))
                    history.append(
                        {"role": "system", "content": _FORCE_CART_FLOW_CONTINUATION_HINT}
                    )
                    history = self._trim_history(history)
                    history = self._trim_history_by_chars(history)
                    continue

                if (
                    cart_data_this_turn is None
                    and cart_intent
                    and manual_cart_reply
                    and not manual_recovery_used
                    and step < self._max_tool_calls
                ):
                    manual_recovery_used = True
                    history.append(self._assistant_msg(message))
                    history.append({"role": "system", "content": _FORCE_CART_RECOVERY_HINT})
                    history = self._trim_history(history)
                    history = self._trim_history_by_chars(history)
                    continue

                if (
                    cart_data_this_turn is None
                    and cart_intent
                    and self._looks_like_cart_ready_reply(final_text)
                    and not cart_creation_recovery_used
                    and step < self._max_tool_calls
                ):
                    cart_creation_recovery_used = True
                    history.append(self._assistant_msg(message))
                    history.append({"role": "system", "content": _FORCE_CART_LINK_SOURCE_HINT})
                    history = self._trim_history(history)
                    history = self._trim_history_by_chars(history)
                    continue

                if cart_data_this_turn is not None:
                    self._ensure_cart_price_summary(
                        cart_data=cart_data_this_turn,
                        product_index=product_index_this_turn,
                    )
                    # При наличии фактической корзины всегда отдаём детерминированный
                    # формат из данных MCP (а не свободный рерайт LLM).
                    safety_note = self._extract_cart_safety_note(final_text)
                    final_text = self._render_stable_cart_output(
                        cart_data_this_turn,
                        safety_note=safety_note,
                    )

                self._history[user_id] = self._trim_history(
                    [*history, self._assistant_msg(message)],
                )
                if trace is not None:
                    trace.update(
                        output=final_text,
                        metadata={"tool_calls": step - 1, "provider": llm_provider},
                    )
                return final_text

            history.append(self._assistant_msg(message))
            step_tool_names = [
                str(tool_call.get("name", "")).strip()
                for tool_call in tool_calls
                if str(tool_call.get("name", "")).strip()
            ]
            if (
                cart_intent
                and cart_data_this_turn is None
                and len(step_tool_names) == 1
                and step_tool_names[0] == "vkusvill_products_search"
            ):
                single_search_steps_streak += 1
            else:
                single_search_steps_streak = 0

            for tool_call in tool_calls:
                tool_name = str(tool_call.get("name", "")).strip()
                tool_call_id = str(tool_call.get("id", "")).strip()
                raw_tool_args = self._parse_tool_args(tool_call.get("arguments"))
                raw_tool_args_snapshot = copy.deepcopy(raw_tool_args)
                requested_quantity_overrides: dict[int, float] = {}
                tool_args = self._preprocess_tool_args(
                    tool_name,
                    raw_tool_args,
                    user_preferences=user_preferences,
                    product_index=product_index_this_turn,
                    explicit_egg_pack_request=explicit_egg_pack_request,
                    requested_ingredients=requested_ingredients,
                    search_query_by_xml_id=search_query_by_xml_id_this_turn,
                    requested_quantity_overrides=requested_quantity_overrides,
                )
                tool_args = self._restore_previous_quantities_for_additive_update(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    user_text=text,
                    previous_products=previous_cart_products,
                    requested_quantity_overrides=requested_quantity_overrides,
                )
                requested_products_snapshot = (
                    self._apply_requested_quantity_overrides(
                        self._collect_requested_products_snapshot(
                            raw_tool_args_snapshot,
                            product_index=product_index_this_turn,
                            explicit_egg_pack_request=explicit_egg_pack_request,
                        ),
                        requested_quantity_overrides,
                    )
                    if tool_name == "vkusvill_cart_link_create"
                    else []
                )

                await _progress(self._tool_progress_text(tool_name))
                tool_span = trace.span(name=f"tool:{tool_name}", input=tool_args) if trace else None

                tool_result = await self._call_mcp_tool(
                    name=tool_name,
                    arguments=tool_args,
                    llm_provider=llm_provider,
                    call_cache=mcp_call_cache,
                )
                if tool_name == "recipe_ingredients":
                    tool_result = self._sanitize_recipe_ingredients_tool_result(
                        tool_result=tool_result,
                        explicit_pantry_requests=explicit_pantry_requests,
                    )
                self._update_product_index_from_tool_result(
                    product_index=product_index_this_turn,
                    tool_name=tool_name,
                    tool_result=tool_result,
                )
                if tool_name == "vkusvill_products_search":
                    self._update_search_query_by_xml_id(
                        search_query_by_xml_id=search_query_by_xml_id_this_turn,
                        tool_args=tool_args,
                        tool_result=tool_result,
                    )
                tools_called_this_turn = True
                if tool_name in {"recipe_ingredients", "recipe_search"}:
                    recipe_flow_started_this_turn = True
                cart_data = self._extract_cart_data(tool_name=tool_name, tool_result=tool_result)
                if cart_data is not None:
                    products = tool_args.get("products")
                    if isinstance(products, list) and "products" not in cart_data:
                        cart_data["products"] = products
                    if requested_products_snapshot and "requested_products" not in cart_data:
                        cart_data["requested_products"] = requested_products_snapshot
                    cart_data_this_turn = cart_data
                self._capture_cart_snapshot(
                    user_id=user_id,
                    tool_name=tool_name,
                    args=tool_args,
                    result=tool_result,
                )

                if tool_span is not None:
                    tool_span.end(output=tool_result[:5000])

                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": self._prepare_tool_result_for_history(tool_name, tool_result),
                    }
                )

            history = self._trim_history(history)
            history = self._trim_history_by_chars(history)
            if (
                cart_intent
                and recipe_flow_started_this_turn
                and cart_data_this_turn is None
                and not recipe_to_cart_recovery_used
                and self._has_recipe_search_candidates(history)
                and step < self._max_tool_calls
            ):
                recipe_to_cart_recovery_used = True
                history.append({"role": "system", "content": _FORCE_RECIPE_TO_CART_HINT})
                history = self._trim_history(history)
                history = self._trim_history_by_chars(history)
            if (
                cart_intent
                and cart_data_this_turn is None
                and single_search_steps_streak >= 3
                and not search_batch_recovery_used
                and step < self._max_tool_calls
            ):
                search_batch_recovery_used = True
                history.append({"role": "system", "content": _FORCE_BATCH_SEARCH_HINT})
                history = self._trim_history(history)
                history = self._trim_history_by_chars(history)

        if cart_data_this_turn is None and cart_intent and recipe_flow_started_this_turn:
            (
                cart_data_this_turn,
                recovered_cart_args,
                recovered_cart_result,
            ) = await self._recover_cart_from_recipe_search_history(
                history=history,
                llm_provider=llm_provider,
                call_cache=mcp_call_cache,
            )
            if cart_data_this_turn is not None:
                self._capture_cart_snapshot(
                    user_id=user_id,
                    tool_name="vkusvill_cart_link_create",
                    args=recovered_cart_args,
                    result=recovered_cart_result,
                )

        if cart_data_this_turn is not None:
            self._ensure_cart_price_summary(
                cart_data=cart_data_this_turn,
                product_index=product_index_this_turn,
            )
            final_text = self._render_stable_cart_output(cart_data_this_turn)
            self._history[user_id] = self._trim_history(
                [*history, {"role": "assistant", "content": final_text}],
            )
            if trace is not None:
                trace.update(
                    output=final_text,
                    metadata={
                        "reason": "max_tool_calls_recovered_with_cart",
                        "provider": llm_provider,
                        "tool_calls": self._max_tool_calls,
                    },
                )
            return final_text

        self._history[user_id] = history
        if trace is not None:
            trace.update(
                output=_ERROR_TOO_MANY_TOOLS,
                metadata={"reason": "max_tool_calls", "provider": llm_provider},
            )
        return _ERROR_TOO_MANY_TOOLS

    def _create_trace(
        self,
        *,
        user_id: int,
        text: str,
        llm_provider: str,
        prompt_profile: PromptProfile,
    ) -> Any | None:
        if self._langfuse is None:
            return None
        with contextlib.suppress(Exception):
            return self._langfuse.trace(
                name="chat",
                user_id=str(user_id),
                session_id=str(user_id),
                input=text,
                tags=[
                    "shopping_agent",
                    "telegram_or_voice",
                    llm_provider,
                    f"profile:{prompt_profile}",
                ],
            )
        return None

    def _build_llm_input_messages(
        self,
        *,
        history: list[dict[str, Any]],
        prompt_profile: PromptProfile,
        mode: PromptMode,
    ) -> list[dict[str, Any]]:
        return self._ensure_system_prompt(
            history=history,
            prompt_profile=prompt_profile,
            mode=mode,
        )

    def _resolve_prompt_mode(self, *, step: int, expecting_final_answer: bool) -> PromptMode:
        if expecting_final_answer:
            return "finalize"
        if step <= 1:
            return "start"
        if self._compact_followup_prompt_enabled:
            return "compact"
        return "start"

    def _ensure_system_prompt(
        self,
        *,
        history: list[dict[str, Any]] | None,
        prompt_profile: PromptProfile,
        mode: PromptMode,
    ) -> list[dict[str, Any]]:
        """Обеспечить первый system-message с нужной версией промпта."""
        prompt = self._resolve_system_prompt(prompt_profile=prompt_profile, mode=mode)
        prepared = list(history) if history is not None else []
        if prepared and prepared[0].get("role") == "system":
            prepared[0] = {"role": "system", "content": prompt}
            return prepared
        return [{"role": "system", "content": prompt}, *prepared]

    def _resolve_system_prompt(self, *, prompt_profile: PromptProfile, mode: PromptMode) -> str:
        if not self._prompt_profiles_enabled:
            return get_system_prompt()
        return get_profiled_system_prompt(profile=prompt_profile, mode=mode)

    def _resolve_prompt_profile(
        self,
        *,
        text: str,
        history: list[dict[str, Any]] | None,
    ) -> PromptProfile:
        profile = self._detect_prompt_profile(text)
        if profile != "general":
            return profile
        if self._is_recipe_followup(text=text, history=history):
            return "recipe"
        return profile

    @staticmethod
    def _detect_prompt_profile(text: str) -> PromptProfile:
        return detect_prompt_profile(text)

    async def _get_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is not None:
            return self._tools_cache

        raw_tools = await self._mcp_client.get_tools()
        self._mcp_tool_names = {
            str(tool.get("name", "")).strip()
            for tool in raw_tools
            if isinstance(tool, dict) and str(tool.get("name", "")).strip()
        }
        raw_tools = self._with_virtual_recipe_tools(raw_tools)
        normalized: list[dict[str, Any]] = []
        for tool in raw_tools:
            name = str(tool.get("name", "")).strip()
            if not name:
                continue
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(tool.get("description", "")),
                        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
            )

        self._tools_cache = normalized
        return normalized

    @staticmethod
    def _with_virtual_recipe_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = list(raw_tools)
        existing_names = {
            str(tool.get("name", "")).strip() for tool in prepared if isinstance(tool, dict)
        }
        virtual_tools = (
            {
                "name": str(RECIPE_TOOL.get("name", "")).strip(),
                "description": str(RECIPE_TOOL.get("description", "")),
                "parameters": RECIPE_TOOL.get("parameters", {}),
            },
            {
                "name": str(RECIPE_SEARCH_TOOL.get("name", "")).strip(),
                "description": str(RECIPE_SEARCH_TOOL.get("description", "")),
                "parameters": RECIPE_SEARCH_TOOL.get("parameters", {}),
            },
        )
        for virtual in virtual_tools:
            name = str(virtual.get("name", "")).strip()
            if not name or name in existing_names:
                continue
            prepared.append(virtual)
            existing_names.add(name)
        return prepared

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
    ) -> Any:
        llm_adapter = self._llm_adapters.get(llm_provider)
        if llm_adapter is None:
            raise RuntimeError(f"LLM adapter not configured for provider: {llm_provider}")
        last_error: Exception | None = None
        for attempt in range(self._llm_retries + 1):
            try:
                async with self._api_semaphore:
                    return await asyncio.wait_for(
                        llm_adapter.create_completion(
                            model=self._resolve_model_for_provider(llm_provider),
                            messages=messages,
                            tools=tools,
                            tool_choice="auto",
                            max_tokens=self._llm_max_tokens,
                            temperature=self._llm_temperature,
                        ),
                        timeout=self._llm_timeout_seconds,
                    )
            except Exception as exc:
                last_error = exc
                if attempt >= self._llm_retries:
                    break
                await asyncio.sleep(0.5 * (2**attempt))
        raise last_error or RuntimeError("LLM call failed")

    def _resolve_model_for_provider(self, llm_provider: str) -> str:
        if llm_provider == "gigachat_sdk":
            return self._gigachat_model
        return self._model

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
    ) -> str:
        cache_key: str | None = None
        if call_cache is not None and name in _MCP_CACHEABLE_TOOLS:
            cache_key = self._make_mcp_call_cache_key(name=name, arguments=arguments)
            cached = call_cache.get(cache_key)
            if isinstance(cached, str):
                return cached

        if name == "recipe_ingredients" and name not in self._mcp_tool_names:
            fallback = await self._fallback_recipe_ingredients(arguments, llm_provider)
            if cache_key is not None and self._is_successful_tool_result(fallback):
                call_cache[cache_key] = fallback
            return fallback
        if name == "recipe_search" and name not in self._mcp_tool_names:
            fallback = await self._fallback_recipe_search(arguments)
            if cache_key is not None and self._is_successful_tool_result(fallback):
                call_cache[cache_key] = fallback
            return fallback

        last_error: Exception | None = None
        for attempt in range(self._mcp_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._mcp_client.call_tool(name, arguments),
                    timeout=self._mcp_timeout_seconds,
                )
                if cache_key is not None and self._is_successful_tool_result(result):
                    call_cache[cache_key] = result
                return result
            except Exception as exc:
                last_error = exc
                fallback = await self._fallback_missing_mcp_tool(
                    tool_name=name,
                    arguments=arguments,
                    llm_provider=llm_provider,
                    error=exc,
                )
                if fallback is not None:
                    if cache_key is not None and self._is_successful_tool_result(fallback):
                        call_cache[cache_key] = fallback
                    return fallback
                if attempt >= self._mcp_retries:
                    break
                await asyncio.sleep(0.25 * (2**attempt))

        logger.warning("MCP tool failed: %s(%s): %s", name, arguments, last_error)
        return json.dumps(
            {
                "ok": False,
                "error": "mcp_error",
                "message": str(last_error) if last_error else "unknown",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _make_mcp_call_cache_key(*, name: str, arguments: dict[str, Any]) -> str:
        args_json = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"{name}:{args_json}"

    @staticmethod
    def _is_successful_tool_result(tool_result: str) -> bool:
        with contextlib.suppress(Exception):
            payload = json.loads(tool_result)
            if isinstance(payload, dict) and payload.get("ok") is True:
                return True
        return False

    async def _fallback_missing_mcp_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        error: Exception,
    ) -> str | None:
        message = str(error).lower()
        if _MCP_TOOL_NOT_FOUND not in message:
            return None

        if tool_name == "recipe_ingredients":
            logger.warning("MCP tool missing: recipe_ingredients. Using local fallback.")
            return await self._fallback_recipe_ingredients(arguments, llm_provider)

        if tool_name == "recipe_search":
            logger.warning("MCP tool missing: recipe_search. Using local fallback.")
            return await self._fallback_recipe_search(arguments)

        return None

    async def _fallback_recipe_ingredients(
        self,
        arguments: dict[str, Any],
        llm_provider: str,
    ) -> str:
        dish = str(arguments.get("dish", "")).strip()
        if not dish:
            return json.dumps(
                {"ok": False, "error": "Не указано название блюда"},
                ensure_ascii=False,
            )

        servings_raw = arguments.get("servings", 2)
        servings = servings_raw if isinstance(servings_raw, int) and servings_raw > 0 else 2

        ingredients = await self._extract_recipe_ingredients_with_llm(
            dish=dish,
            servings=servings,
            llm_provider=llm_provider,
        )
        if not ingredients and "борщ" in dish.lower():
            ingredients = self._fallback_borscht_ingredients(servings)

        if not ingredients:
            return json.dumps(
                {
                    "ok": False,
                    "error": "Не удалось получить рецепт",
                    "message": "recipe_ingredients unavailable and no fallback recipe",
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "ok": True,
                "dish": dish,
                "servings": servings,
                "ingredients": ingredients,
                "cached": False,
                "hint": (
                    "Сначала вызови recipe_search и передай ВЕСЬ массив ingredients. "
                    "Если recipe_search недоступен — ищи каждый ингредиент через "
                    "vkusvill_products_search (используй search_query). "
                    "Для q используй детерминированный расчет по quantity/unit ингредиента "
                    "и упаковке найденного товара. "
                    "Затем vkusvill_cart_link_create."
                ),
                "source": "shopping_agent_fallback",
            },
            ensure_ascii=False,
        )

    async def _extract_recipe_ingredients_with_llm(
        self,
        *,
        dish: str,
        servings: int,
        llm_provider: str,
    ) -> list[dict[str, Any]]:
        adapter = self._llm_adapters.get(llm_provider)
        if adapter is None:
            return []

        prompt = RECIPE_EXTRACTION_PROMPT.format(dish=dish, servings=servings)
        try:
            response = await asyncio.wait_for(
                adapter.create_completion(
                    model=self._resolve_model_for_provider(llm_provider),
                    messages=[{"role": "user", "content": prompt}],
                    tools=[],
                    tool_choice="none",
                ),
                timeout=self._llm_timeout_seconds,
            )
        except Exception:
            return []

        message = self._extract_message(response)
        content = self._extract_text(message)
        parsed = self._parse_json_payload(content)
        if isinstance(parsed, dict):
            parsed = parsed.get("ingredients")
        if not isinstance(parsed, list):
            return []

        normalized: list[dict[str, Any]] = []
        for row in parsed[:30]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            unit = str(row.get("unit", "шт")).strip() or "шт"
            quantity = self._safe_float(row.get("quantity"), default=1.0)
            if quantity <= 0:
                quantity = 1.0
            ingredient: dict[str, Any] = {
                "name": name,
                "quantity": round(quantity, 3),
                "unit": unit,
                "search_query": str(row.get("search_query", "")).strip() or name,
            }
            if bool(row.get("optional", False)):
                ingredient["optional"] = True
            self._enrich_recipe_equivalents(ingredient)
            normalized.append(ingredient)

        return normalized

    async def _fallback_recipe_search(self, arguments: dict[str, Any]) -> str:
        ingredients = arguments.get("ingredients")
        if not isinstance(ingredients, list) or not ingredients:
            return json.dumps(
                {"ok": False, "error": "Пустой список ingredients"},
                ensure_ascii=False,
            )

        results: list[dict[str, Any]] = []
        found: list[dict[str, Any]] = []
        not_found: list[str] = []
        search_log: dict[str, list[int]] = {}

        for row_raw in ingredients[:40]:
            row = self._normalize_recipe_ingredient_row(row_raw)
            if not row:
                continue
            query = str(row.get("search_query", "")).strip() or str(row.get("name", "")).strip()
            ingredient_name = str(row.get("name", "")).strip() or query
            if not query:
                continue

            raw = await self._search_products_for_recipe(query)
            parsed = self._parse_json_payload(raw)
            items = self._extract_search_items(parsed)
            if not items:
                not_found.append(query)
                results.append(
                    {
                        "ingredient": ingredient_name,
                        "search_query": query,
                        "best_match": None,
                        "alternatives": [],
                        "error": "Поиск не вернул items",
                    }
                )
                continue

            ids = [item.get("xml_id") for item in items if isinstance(item.get("xml_id"), int)]
            search_log[query] = [xml_id for xml_id in ids if isinstance(xml_id, int)]

            best = items[0]
            suggested_q = _RecipeQuantityCalculator.calculate_purchase_q(row, best)
            best_match = {
                "xml_id": best.get("xml_id"),
                "name": best.get("name"),
                "price": best.get("price"),
                "unit": best.get("unit", "шт"),
                "suggested_q": suggested_q,
            }
            alternatives = [
                {
                    "xml_id": item.get("xml_id"),
                    "name": item.get("name"),
                    "price": item.get("price"),
                    "unit": item.get("unit", "шт"),
                    "suggested_q": _RecipeQuantityCalculator.calculate_purchase_q(row, item),
                }
                for item in items[1:4]
            ]
            found.append(
                {
                    "ingredient": ingredient_name,
                    "quantity": row.get("quantity"),
                    "unit": row.get("unit"),
                    "search_query": query,
                    "item": {
                        "xml_id": best_match.get("xml_id"),
                        "name": best_match.get("name"),
                        "price": best_match.get("price"),
                        "unit": best_match.get("unit", "шт"),
                    },
                    "suggested_q": best_match.get("suggested_q"),
                    "alternatives": alternatives,
                }
            )
            results.append(
                {
                    "ingredient": ingredient_name,
                    "search_query": query,
                    "best_match": best_match,
                    "alternatives": alternatives,
                }
            )

        return json.dumps(
            {
                "ok": True,
                "results": results,
                "data": {
                    "found": found,
                    "not_found": not_found,
                    "search_log": search_log,
                },
                "not_found": not_found,
                "search_log": search_log,
                "source": "shopping_agent_fallback",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _normalize_recipe_ingredient_row(row_raw: Any) -> dict[str, Any]:
        if isinstance(row_raw, dict):
            row = dict(row_raw)
            quantity = ShoppingAgent._safe_float(row.get("quantity"), default=1.0)
            if quantity <= 0:
                quantity = 1.0
            unit = _RecipeQuantityCalculator._normalize_unit(row.get("unit")) or "шт"
            row["quantity"] = quantity
            row["unit"] = unit
            search_query = str(row.get("search_query", "")).strip()
            if not search_query:
                search_query = SearchProcessor.clean_search_query(str(row.get("name", "")).strip())
            if search_query:
                row["search_query"] = search_query
            return row
        if not isinstance(row_raw, str):
            return {}

        text = row_raw.strip()
        if not text:
            return {}
        cleaned = text.replace("по вкусу", "").strip(" ,.-")
        parsed = ShoppingAgent._parse_quantity_hint(cleaned)
        if parsed is not None:
            parsed_q, parsed_unit, fragment = parsed
        else:
            parsed_q, parsed_unit, fragment = _RecipeQuantityCalculator.parse_quantity_and_unit(
                cleaned
            )
        if fragment:
            name = re.sub(re.escape(fragment), " ", cleaned, flags=re.IGNORECASE).strip(" ,.-")
        else:
            name = cleaned
        if not name:
            name = cleaned
        query = SearchProcessor.clean_search_query(name) or name
        return {
            "name": name or text,
            "search_query": query,
            "quantity": parsed_q if parsed_q is not None else 1.0,
            "unit": parsed_unit or "шт",
        }

    @classmethod
    def _extract_explicit_pantry_requests(cls, user_text: str) -> set[str]:
        normalized = cls._normalize_text(user_text)
        requested: set[str] = set()
        if "соль" in normalized:
            requested.add(_PANTRY_TAG_SALT)
        if "сахар" in normalized:
            requested.add(_PANTRY_TAG_SUGAR)
        if cls._is_explicit_seasoning_pepper_request(normalized):
            requested.add(_PANTRY_TAG_PEPPER)
        return requested

    @classmethod
    def _extract_structured_ingredient_requests(cls, user_text: str) -> list[dict[str, Any]]:
        if not user_text.strip():
            return []
        rows: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, float, str]] = set()

        def _append_row(name_raw: str, quantity: float, unit: str) -> None:
            cleaned_name = cls._clean_structured_ingredient_name(name_raw)
            if not cleaned_name:
                return
            search_query = SearchProcessor.clean_search_query(cleaned_name) or cleaned_name
            key = (search_query.lower(), round(quantity, 4), unit)
            if key in seen_keys:
                return
            seen_keys.add(key)
            rows.append(
                {
                    "name": cleaned_name,
                    "quantity": quantity,
                    "unit": unit,
                    "search_query": search_query,
                }
            )

        unit_pattern = (
            r"кг|kg|г|гр|л|l|мл|ml|шт|штук|шт\.|ст\.?\s*л\.?|ч\.?\s*л\.?|"
            r"зубчик(?:а|ов)?|головк(?:а|и|ок)|лист(?:а|ов)?|"
            r"ст\.?\s*ложк[аи]?|ч\.?\s*ложк[аи]?|столов(?:ая|ые)\s+ложк[аи]|"
            r"чайн(?:ая|ые)\s+ложк[аи]"
        )
        inline_pattern = re.compile(
            rf"(?:^|[\n,;])\s*(?P<name>[^\n,;]+?)\s*[-–—:]\s*"
            rf"(?P<qty>\d+(?:[.,]\d+)?(?:\s*[-–—]\s*\d+(?:[.,]\d+)?)?\s*(?:{unit_pattern}))"
            r"(?=$|[\n,;])",
            flags=re.IGNORECASE,
        )
        normalized_text = user_text.replace("\r", "\n")
        for match in inline_pattern.finditer(normalized_text):
            name_part = str(match.group("name") or "").strip()
            qty_part = str(match.group("qty") or "").strip()
            parsed = cls._parse_quantity_hint(qty_part)
            if parsed is None:
                continue
            quantity, unit, _fragment = parsed
            _append_row(name_part, quantity, unit)

        for raw_line in normalized_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if inline_pattern.search(line):
                continue
            line = re.sub(r"^\s*\d+[.)]\s*", "", line).strip()
            line = line.lstrip("-• ").strip()
            if not line:
                continue
            name_part = line
            quantity_part = line
            split_match = re.match(r"^(.*?)(?:\s*[-–—:]\s*)(.+)$", line)
            if split_match is not None:
                left = split_match.group(1).strip()
                right = split_match.group(2).strip()
                if left and right:
                    name_part = left
                    quantity_part = right
            parsed = cls._parse_quantity_hint(quantity_part)
            if parsed is None and quantity_part != line:
                parsed = cls._parse_quantity_hint(line)
            if parsed is None:
                continue
            quantity, unit, _fragment = parsed
            _append_row(name_part, quantity, unit)
        return rows[:30]

    @staticmethod
    def _clean_structured_ingredient_name(name_raw: str) -> str:
        name = name_raw.strip(" ,.;:").strip()
        if not name:
            return ""
        prefixes = (
            r"^\s*собери(?:\s+мне)?\s+корзин[ауые]?\s+",
            r"^\s*добав(?:ь|ьте)(?:\s+в\s+корзин[ауые]?)?\s+",
            r"^\s*закаж(?:и|ите)\s+",
        )
        for pattern in prefixes:
            name = re.sub(pattern, "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"^\s*и\s+", "", name, flags=re.IGNORECASE).strip()
        return name

    @classmethod
    def _parse_quantity_hint(cls, text: str) -> tuple[float, str, str] | None:
        normalized = cls._normalize_text(text)
        if not normalized:
            return None
        range_pattern = (
            r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*"
            r"(кг|kg|г|гр|л|l|мл|ml|шт|штук|шт\.|"
            r"зубчик(?:а|ов)?|головк(?:а|и|ок)|лист(?:а|ов)?|"
            r"ст\.?\s*л\.?|ч\.?\s*л\.?|"
            r"ст\.?\s*ложк[аи]?|ч\.?\s*ложк[аи]?|столов(?:ая|ые)\s+ложк[аи]|"
            r"чайн(?:ая|ые)\s+ложк[аи])"
        )
        match = re.search(range_pattern, normalized, flags=re.IGNORECASE)
        if match is not None:
            low = cls._safe_float(match.group(1), default=-1.0)
            high = cls._safe_float(match.group(2), default=-1.0)
            if low > 0 and high > 0:
                unit = _RecipeQuantityCalculator._normalize_unit(match.group(3))
                if unit:
                    return max(low, high), unit, match.group(0)

        single_pattern = (
            r"(\d+(?:[.,]\d+)?)\s*"
            r"(кг|kg|г|гр|л|l|мл|ml|шт|штук|шт\.|"
            r"зубчик(?:а|ов)?|головк(?:а|и|ок)|лист(?:а|ов)?|"
            r"ст\.?\s*л\.?|ч\.?\s*л\.?|"
            r"ст\.?\s*ложк[аи]?|ч\.?\s*ложк[аи]?|столов(?:ая|ые)\s+ложк[аи]|"
            r"чайн(?:ая|ые)\s+ложк[аи])"
        )
        match = re.search(single_pattern, normalized, flags=re.IGNORECASE)
        if match is None:
            return None
        quantity = cls._safe_float(match.group(1), default=-1.0)
        if quantity <= 0:
            return None
        unit = _RecipeQuantityCalculator._normalize_unit(match.group(2))
        if not unit:
            return None
        return quantity, unit, match.group(0)

    @classmethod
    def _match_requested_ingredient(
        cls,
        *,
        product: dict[str, Any],
        xml_id: int,
        requested_ingredients: list[dict[str, Any]],
        search_query_by_xml_id: dict[int, str] | None,
    ) -> dict[str, Any] | None:
        if not requested_ingredients:
            return None

        def _score_pair(left: str, right: str) -> float:
            left_tokens = cls._tokenize_query_terms(left)
            right_tokens = cls._tokenize_query_terms(right)
            if not left_tokens or not right_tokens:
                return 0.0
            left_set = set(left_tokens)
            right_set = set(right_tokens)
            inter = len(left_set & right_set)
            if inter <= 0:
                return 0.0
            return inter / max(len(left_set), len(right_set))

        best_score = 0.0
        best_row: dict[str, Any] | None = None
        product_name = str(product.get("name", "")).strip()
        query_hint = ""
        if search_query_by_xml_id is not None:
            query_hint = str(search_query_by_xml_id.get(xml_id, "")).strip()

        for row in requested_ingredients:
            if not isinstance(row, dict):
                continue
            row_name = str(row.get("name", "")).strip()
            row_query = str(row.get("search_query", "")).strip()
            score = max(
                _score_pair(row_query, query_hint),
                _score_pair(row_name, query_hint),
                _score_pair(row_query, product_name),
                _score_pair(row_name, product_name),
            )
            if score > best_score:
                best_score = score
                best_row = row

        if best_row is None or best_score < 0.2:
            return None
        return best_row

    @staticmethod
    def _apply_requested_quantity_overrides(
        snapshot: list[dict[str, Any]],
        overrides: dict[int, float],
    ) -> list[dict[str, Any]]:
        if not snapshot or not overrides:
            return snapshot
        updated: list[dict[str, Any]] = []
        for row in snapshot:
            if not isinstance(row, dict):
                continue
            current = dict(row)
            xml_id_raw = current.get("xml_id")
            if isinstance(xml_id_raw, bool):
                updated.append(current)
                continue
            with contextlib.suppress(TypeError, ValueError):
                xml_id = int(xml_id_raw)
                if xml_id in overrides:
                    current["q"] = overrides[xml_id]
            updated.append(current)
        return updated

    def _sanitize_recipe_ingredients_tool_result(
        self,
        *,
        tool_result: str,
        explicit_pantry_requests: set[str],
    ) -> str:
        payload = self._parse_json_payload(tool_result)
        if not isinstance(payload, dict) or not payload:
            return tool_result

        removed_names: list[str] = []
        changed = False

        ingredients = payload.get("ingredients")
        if isinstance(ingredients, list):
            filtered, removed = self._filter_recipe_ingredients_list(
                ingredients=ingredients,
                explicit_pantry_requests=explicit_pantry_requests,
            )
            if len(filtered) != len(ingredients):
                payload["ingredients"] = filtered
                removed_names.extend(removed)
                changed = True

        data = payload.get("data")
        if isinstance(data, dict):
            nested = data.get("ingredients")
            if isinstance(nested, list):
                filtered, removed = self._filter_recipe_ingredients_list(
                    ingredients=nested,
                    explicit_pantry_requests=explicit_pantry_requests,
                )
                if len(filtered) != len(nested):
                    data["ingredients"] = filtered
                    removed_names.extend(removed)
                    changed = True

        if not changed:
            return tool_result

        unique_removed = sorted({name for name in removed_names if name})
        if unique_removed:
            payload["pantry_filtered"] = unique_removed
            logger.info("Filtered pantry ingredients from recipe_ingredients: %s", unique_removed)

        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def _filter_recipe_ingredients_list(
        cls,
        *,
        ingredients: list[Any],
        explicit_pantry_requests: set[str],
    ) -> tuple[list[Any], list[str]]:
        filtered: list[Any] = []
        removed: list[str] = []
        for row in ingredients:
            if not isinstance(row, dict):
                filtered.append(row)
                continue
            pantry_tag = cls._detect_pantry_tag_for_ingredient(row)
            if pantry_tag and pantry_tag not in explicit_pantry_requests:
                removed.append(str(row.get("name", "")).strip())
                continue
            filtered.append(row)
        return filtered, removed

    @classmethod
    def _detect_pantry_tag_for_ingredient(cls, row: dict[str, Any]) -> str | None:
        name = cls._normalize_text(str(row.get("name", "")))
        query = cls._normalize_text(str(row.get("search_query", "")))
        text = f"{name} {query}".strip()
        if not text:
            return None
        if "соль" in text:
            return _PANTRY_TAG_SALT
        if "сахар" in text:
            return _PANTRY_TAG_SUGAR
        if "перец" in text and not cls._looks_like_pepper_vegetable(text):
            return _PANTRY_TAG_PEPPER
        return None

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.strip().lower().replace("ё", "е")

    @classmethod
    def _looks_like_pepper_vegetable(cls, text: str) -> bool:
        normalized = cls._normalize_text(text)
        vegetable_markers = (
            "болгар",
            "сладк",
            "чили",
            "халапень",
            "пепперони",
            "перец овощ",
            "фаршированн",
        )
        return any(marker in normalized for marker in vegetable_markers)

    @classmethod
    def _is_explicit_seasoning_pepper_request(cls, text: str) -> bool:
        normalized = cls._normalize_text(text)
        if "перец" not in normalized:
            return False
        spice_markers = (
            "черн",
            "красн",
            "бел",
            "молот",
            "горош",
            "душист",
            "приправ",
        )
        if any(marker in normalized for marker in spice_markers):
            return True
        if cls._looks_like_pepper_vegetable(normalized):
            return False
        return "соль" in normalized or "сахар" in normalized

    @classmethod
    def _has_explicit_egg_pack_request(cls, text: str) -> bool:
        normalized = cls._normalize_text(text)
        if not any(stem in normalized for stem in ("яйц", "яиц", "яйк")):
            return False
        pack_markers = ("упаков", "десят", "дюжин")
        return any(marker in normalized for marker in pack_markers)

    @classmethod
    def _is_recipe_followup(cls, *, text: str, history: list[dict[str, Any]] | None) -> bool:
        if not history:
            return False
        normalized = cls._normalize_text(text)
        if not normalized or len(normalized) > 120:
            return False
        if any(marker in normalized for marker in ("привяз", "алис", "код", "статус", "отвяз")):
            return False

        recent_user_messages = [
            str(msg.get("content", "")).strip()
            for msg in reversed(history)
            if msg.get("role") == "user" and isinstance(msg.get("content"), str)
        ]
        for prev_text in recent_user_messages[:3]:
            if detect_prompt_profile(prev_text) == "recipe":
                return True

        for msg in reversed(history[-8:]):
            if msg.get("role") == "tool" and msg.get("name") in {
                "recipe_ingredients",
                "recipe_search",
            }:
                return True
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function_data = call.get("function")
                if not isinstance(function_data, dict):
                    continue
                name = str(function_data.get("name", "")).strip()
                if name in {"recipe_ingredients", "recipe_search"}:
                    return True
        return False

    async def _search_products_for_recipe(self, query: str) -> str:
        last_error: Exception | None = None
        args = {"q": query, "limit": 5}
        for attempt in range(self._mcp_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._mcp_client.call_tool("vkusvill_products_search", args),
                    timeout=self._mcp_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self._mcp_retries:
                    break
                await asyncio.sleep(0.25 * (2**attempt))
        return json.dumps(
            {
                "ok": False,
                "error": "mcp_error",
                "message": str(last_error) if last_error else "unknown",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _extract_search_items(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, dict):
            data_products = data.get("products")
            if isinstance(data_products, list):
                return [item for item in data_products if isinstance(item, dict)]
            items = data.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            if isinstance(data.get("xml_id"), int | str):
                return [data]
        item = payload.get("item")
        if isinstance(item, dict):
            return [item]
        items = payload.get("items")
        if isinstance(items, list):
            return [entry for entry in items if isinstance(entry, dict)]
        products = payload.get("products")
        if isinstance(products, list):
            return [item for item in products if isinstance(item, dict)]
        found = payload.get("found")
        if isinstance(found, list):
            result: list[dict[str, Any]] = []
            for row in found:
                if not isinstance(row, dict):
                    continue
                xml_id = row.get("xml_id")
                if xml_id is None:
                    continue
                result.append(
                    {
                        "xml_id": xml_id,
                        "name": row.get("name"),
                        "price": row.get("price"),
                        "unit": row.get("unit", "шт"),
                    }
                )
            if result:
                return result
        results = payload.get("results")
        if isinstance(results, list):
            expanded: list[dict[str, Any]] = []
            for row in results:
                if not isinstance(row, dict):
                    continue
                best_match = row.get("best_match")
                if isinstance(best_match, dict):
                    expanded.append(best_match)
            if expanded:
                return expanded
        return []

    def _build_product_index_from_history(
        self,
        history: list[dict[str, Any]] | None,
    ) -> dict[int, dict[str, Any]]:
        if not history:
            return {}
        product_index: dict[int, dict[str, Any]] = {}
        for msg in history[-20:]:
            if msg.get("role") != "tool":
                continue
            tool_name = str(msg.get("name", "")).strip()
            content = msg.get("content")
            if not tool_name or not isinstance(content, str) or not content.strip():
                continue
            self._update_product_index_from_tool_result(
                product_index=product_index,
                tool_name=tool_name,
                tool_result=content,
            )
        return product_index

    @staticmethod
    def _safe_float(value: Any, *, default: float = 0.0) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                return float(value.replace(",", "."))
        return default

    @staticmethod
    def _parse_json_payload(content: Any) -> Any:
        if not isinstance(content, str):
            return content
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(text)
        return {}

    @staticmethod
    def _enrich_recipe_equivalents(ingredient: dict[str, Any]) -> None:
        unit = str(ingredient.get("unit", "")).lower().strip()
        quantity = ShoppingAgent._safe_float(ingredient.get("quantity"), default=0.0)
        name = str(ingredient.get("name", "")).lower()
        if quantity <= 0:
            return
        if unit == "г":
            ingredient["kg_equivalent"] = round(quantity / 1000, 3)
            return
        if unit == "мл":
            ingredient["l_equivalent"] = round(quantity / 1000, 3)
            return
        if "яйц" in name and unit in {"шт", "штука", "штук"}:
            ingredient["pack_equivalent"] = max(1, math.ceil(quantity / 10))

    @staticmethod
    def _fallback_borscht_ingredients(servings: int) -> list[dict[str, Any]]:
        base_servings = 2
        factor = servings / base_servings if servings > 0 else 1.0
        base = [
            {"name": "свёкла", "quantity": 0.67, "unit": "шт", "search_query": "свекла"},
            {
                "name": "капуста белокочанная",
                "quantity": 0.17,
                "unit": "кг",
                "search_query": "капуста белокочанная",
            },
            {"name": "картофель", "quantity": 0.2, "unit": "кг", "search_query": "картофель"},
            {"name": "морковь", "quantity": 0.05, "unit": "кг", "search_query": "морковь"},
            {
                "name": "лук репчатый",
                "quantity": 0.03,
                "unit": "кг",
                "search_query": "лук репчатый",
            },
            {"name": "помидоры", "quantity": 0.1, "unit": "кг", "search_query": "помидоры свежие"},
            {"name": "говядина", "quantity": 0.4, "unit": "кг", "search_query": "говядина"},
            {"name": "чеснок", "quantity": 10, "unit": "г", "search_query": "чеснок"},
            {
                "name": "масло растительное",
                "quantity": 30,
                "unit": "мл",
                "search_query": "масло растительное",
            },
            {
                "name": "томатная паста",
                "quantity": 60,
                "unit": "г",
                "search_query": "томатная паста",
            },
        ]
        result: list[dict[str, Any]] = []
        for row in base:
            item = dict(row)
            base_quantity = ShoppingAgent._safe_float(row.get("quantity"), default=1.0)
            item["quantity"] = round(base_quantity * factor, 3)
            ShoppingAgent._enrich_recipe_equivalents(item)
            result.append(item)
        return result

    @staticmethod
    def _extract_message(response: Any) -> Any:
        choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            first = choices[0]
            if hasattr(first, "message"):
                return first.message
            if isinstance(first, dict):
                return first.get("message", {})
            return {}
        if isinstance(response, dict):
            choices_dict = response.get("choices")
            if isinstance(choices_dict, list) and choices_dict:
                first = choices_dict[0]
                if isinstance(first, dict):
                    return first.get("message", {})
        return {}

    @staticmethod
    def _preprocess_tool_args(
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        user_preferences: dict[str, str] | None = None,
        product_index: dict[int, dict[str, Any]] | None = None,
        explicit_egg_pack_request: bool = False,
        requested_ingredients: list[dict[str, Any]] | None = None,
        search_query_by_xml_id: dict[int, str] | None = None,
        requested_quantity_overrides: dict[int, float] | None = None,
    ) -> dict[str, Any]:
        if tool_name == "vkusvill_cart_link_create":
            # MCP cart-link expects explicit quantities for each item.
            normalized = CartProcessor.fix_cart_args(tool_args)
            products = normalized.get("products")
            if not isinstance(products, list) or explicit_egg_pack_request:
                return normalized
            product_lookup = product_index or {}
            for item in products:
                if not isinstance(item, dict):
                    continue
                xml_id_raw = item.get("xml_id")
                if isinstance(xml_id_raw, bool):
                    continue
                try:
                    xml_id = int(xml_id_raw)
                except (TypeError, ValueError):
                    continue
                product = product_lookup.get(xml_id)
                if not isinstance(product, dict):
                    continue
                if requested_ingredients:
                    matched_ingredient = ShoppingAgent._match_requested_ingredient(
                        product=product,
                        xml_id=xml_id,
                        requested_ingredients=requested_ingredients,
                        search_query_by_xml_id=search_query_by_xml_id,
                    )
                    if matched_ingredient is not None:
                        requested_q, purchase_q = (
                            _RecipeQuantityCalculator.calculate_requested_and_purchase_q(
                                ingredient=matched_ingredient,
                                item=product,
                            )
                        )
                        if requested_quantity_overrides is not None and requested_q > 0:
                            requested_quantity_overrides[xml_id] = requested_q
                        item["q"] = purchase_q
                        continue
                name = str(product.get("name", "")).strip().lower()
                q = ShoppingAgent._safe_float(item.get("q"), default=1.0)
                if q <= 0:
                    q = 1
                unit = str(product.get("unit", "")).strip().lower()
                if any(stem in name for stem in ("яйц", "яиц", "яйк")):
                    if q <= 1:
                        item["q"] = 1
                        continue
                    item["q"] = max(1, math.ceil(q / _EGG_PACK_SIZE))
                    continue
                if unit in _DISCRETE_UNITS:
                    item["q"] = max(1, math.ceil(q))
                    continue
                if unit in {"кг", "kg"}:
                    item["q"] = ShoppingAgent._round_kilogram_quantity(q)
            return normalized
        if tool_name == "vkusvill_products_search":
            normalized_search_args = dict(tool_args)
            # Для сборки корзины достаточно первой страницы; pagination page>1
            # часто создаёт лишние шаги без улучшения результата.
            if "page" in normalized_search_args:
                normalized_search_args.pop("page", None)
            prefs = user_preferences or {}
            if not prefs:
                return normalized_search_args
            query_key = None
            if isinstance(normalized_search_args.get("q"), str):
                query_key = "q"
            elif isinstance(normalized_search_args.get("query"), str):
                query_key = "query"
            if query_key is None:
                return normalized_search_args
            original_query = str(normalized_search_args.get(query_key, "")).strip()
            if not original_query:
                return normalized_search_args
            enhanced_query = ShoppingAgent._apply_preferences_to_query(original_query, prefs)
            if enhanced_query == original_query:
                return normalized_search_args
            return {**normalized_search_args, query_key: enhanced_query}
        if tool_name == "recipe_search":
            return ShoppingAgent._normalize_recipe_search_args(tool_args)
        return tool_args

    @staticmethod
    def _collect_requested_products_snapshot(
        tool_args: dict[str, Any],
        *,
        product_index: dict[int, dict[str, Any]] | None = None,
        explicit_egg_pack_request: bool = False,
    ) -> list[dict[str, Any]]:
        products = tool_args.get("products")
        if not isinstance(products, list):
            return []
        normalized = CartProcessor.fix_cart_args({"products": copy.deepcopy(products)})
        snapshot = normalized.get("products")
        if not isinstance(snapshot, list):
            return []
        if explicit_egg_pack_request:
            return [item for item in snapshot if isinstance(item, dict)]

        lookup = product_index or {}
        normalized_snapshot: list[dict[str, Any]] = []
        for item in snapshot:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            xml_id_raw = row.get("xml_id")
            if isinstance(xml_id_raw, bool):
                normalized_snapshot.append(row)
                continue
            xml_id: int | None = None
            with contextlib.suppress(TypeError, ValueError):
                xml_id = int(xml_id_raw)
            product = lookup.get(xml_id) if isinstance(xml_id, int) else None
            if not isinstance(product, dict):
                normalized_snapshot.append(row)
                continue
            name = str(product.get("name", "")).strip().lower()
            if not any(stem in name for stem in ("яйц", "яиц", "яйк")):
                unit = str(product.get("unit", "")).strip().lower()
                if unit in {"кг", "kg"}:
                    quantity = ShoppingAgent._safe_float(row.get("q"), default=0.1)
                    row["q"] = ShoppingAgent._round_kilogram_quantity(quantity)
                normalized_snapshot.append(row)
                continue
            quantity = ShoppingAgent._safe_float(row.get("q"), default=1.0)
            if quantity <= 1:
                row["q"] = 1
            else:
                row["q"] = max(1, math.ceil(quantity / _EGG_PACK_SIZE))
            normalized_snapshot.append(row)
        return normalized_snapshot

    @classmethod
    def _restore_previous_quantities_for_additive_update(
        cls,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        user_text: str,
        previous_products: list[dict[str, Any]],
        requested_quantity_overrides: dict[int, float] | None = None,
    ) -> dict[str, Any]:
        if tool_name != "vkusvill_cart_link_create":
            return tool_args
        if not cls._is_additive_cart_intent(user_text):
            return tool_args
        products = tool_args.get("products")
        if not isinstance(products, list) or not products or not previous_products:
            return tool_args

        prev_by_xml_id: dict[int, float] = {}
        for row in previous_products:
            if not isinstance(row, dict):
                continue
            xml_id_raw = row.get("xml_id")
            if isinstance(xml_id_raw, bool):
                continue
            xml_id: int | None = None
            with contextlib.suppress(TypeError, ValueError):
                xml_id = int(xml_id_raw)
            if not isinstance(xml_id, int):
                continue
            prev_q = cls._safe_float(row.get("q"), default=1.0)
            if prev_q <= 0:
                prev_q = 1.0
            prev_by_xml_id[xml_id] = prev_q

        if not prev_by_xml_id:
            return tool_args
        explicit_xml_ids = set(requested_quantity_overrides or {})
        updated = False
        for row in products:
            if not isinstance(row, dict):
                continue
            xml_id_raw = row.get("xml_id")
            if isinstance(xml_id_raw, bool):
                continue
            xml_id: int | None = None
            with contextlib.suppress(TypeError, ValueError):
                xml_id = int(xml_id_raw)
            if not isinstance(xml_id, int) or xml_id not in prev_by_xml_id:
                continue
            if xml_id in explicit_xml_ids:
                continue
            current_q = cls._safe_float(row.get("q"), default=1.0)
            if current_q <= 0:
                current_q = 1.0
            previous_q = prev_by_xml_id[xml_id]
            if abs(current_q - 1.0) > 1e-9:
                continue
            if abs(previous_q - 1.0) <= 1e-9:
                continue
            row["q"] = previous_q
            updated = True

        if updated:
            return CartProcessor.fix_cart_args({"products": products})
        return tool_args

    @staticmethod
    def _normalize_recipe_search_args(tool_args: dict[str, Any]) -> dict[str, Any]:
        ingredients = tool_args.get("ingredients")
        if not isinstance(ingredients, list):
            return tool_args

        normalized_rows: list[Any] = []
        changed = False
        for row in ingredients:
            if not isinstance(row, dict):
                normalized_rows.append(row)
                continue

            normalized = dict(row)
            raw_query = normalized.get("search_query", "")
            query = str(raw_query).strip() if raw_query is not None else ""
            if query:
                cleaned_query = SearchProcessor.clean_search_query(query)
                if cleaned_query and cleaned_query != query:
                    normalized["search_query"] = cleaned_query
                    changed = True
            else:
                name = str(normalized.get("name", "")).strip()
                if name:
                    normalized["search_query"] = SearchProcessor.clean_search_query(name)
                    changed = True

            normalized_rows.append(normalized)

        if not changed:
            return tool_args
        return {**tool_args, "ingredients": normalized_rows}

    async def _load_user_preferences(self, user_id: int) -> dict[str, str]:
        if self._preferences_store is None:
            return {}
        try:
            raw = await self._preferences_store.get_formatted(user_id)
        except Exception as exc:
            logger.warning("Failed to load user preferences for %s: %s", user_id, exc)
            return {}
        return self._parse_preferences(raw)

    @staticmethod
    def _parse_preferences(result_text: str) -> dict[str, str]:
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return {}
        prefs = data.get("preferences", [])
        if not isinstance(prefs, list):
            return {}
        result: dict[str, str] = {}
        for item in prefs:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category", "")).strip().lower()
            preference = str(item.get("preference", "")).strip()
            if category and preference:
                result[category] = preference
        return result

    @staticmethod
    def _apply_preferences_to_query(query: str, user_prefs: dict[str, str]) -> str:
        if not user_prefs or not query:
            return query
        query_lower = query.strip().lower()
        preference = user_prefs.get(query_lower)
        if preference is None:
            return query
        if query_lower in preference.lower():
            return preference
        return f"{query} {preference}"

    @staticmethod
    def _extract_text(message: Any) -> str:
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts).strip()
        return ""

    @staticmethod
    def _extract_tool_calls(message: Any) -> list[dict[str, Any]]:
        if isinstance(message, dict):
            raw_calls = message.get("tool_calls")
        else:
            raw_calls = getattr(message, "tool_calls", None)
        if not raw_calls:
            return []

        result: list[dict[str, Any]] = []
        for call in raw_calls:
            if isinstance(call, dict):
                fn = call.get("function", {})
                result.append(
                    {
                        "id": str(call.get("id", "")),
                        "name": str(fn.get("name", "")),
                        "arguments": fn.get("arguments", "{}"),
                    }
                )
                continue

            function_obj = getattr(call, "function", None)
            result.append(
                {
                    "id": str(getattr(call, "id", "")),
                    "name": str(getattr(function_obj, "name", "")),
                    "arguments": getattr(function_obj, "arguments", "{}"),
                }
            )
        return result

    @staticmethod
    def _assistant_msg(message: Any) -> dict[str, Any]:
        content = ShoppingAgent._extract_text(message)
        tool_calls = []
        for call in ShoppingAgent._extract_tool_calls(message):
            tool_calls.append(
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call["arguments"],
                    },
                }
            )
        payload: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            payload["tool_calls"] = tool_calls
        return payload

    @staticmethod
    def _parse_tool_args(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    return parsed
        return {}

    def _capture_cart_snapshot(
        self,
        *,
        user_id: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None:
        if tool_name != "vkusvill_cart_link_create":
            return

        with contextlib.suppress(Exception):
            parsed = json.loads(result)
            if not isinstance(parsed, dict) or not parsed.get("ok"):
                return
            data = parsed.get("data", {})
            if not isinstance(data, dict):
                return
            summary = data.get("price_summary", {})
            total: float | None = None
            items_count = 0
            if isinstance(summary, dict):
                total_raw = summary.get("total")
                if isinstance(total_raw, int | float) and not isinstance(total_raw, bool):
                    total = float(total_raw)
                count_raw = summary.get("count")
                if isinstance(count_raw, int) and count_raw >= 0:
                    items_count = count_raw
                elif isinstance(count_raw, float) and count_raw.is_integer() and count_raw >= 0:
                    items_count = int(count_raw)
                else:
                    items = summary.get("items")
                    if isinstance(items, list):
                        items_count = len(items)
            if items_count <= 0:
                products = args.get("products")
                if isinstance(products, list):
                    items_count = len(products)
            self._last_cart_snapshot[user_id] = {
                "products": args.get("products", []),
                "link": data.get("link", ""),
                "total": total,
                "items_count": items_count,
                "price_summary": summary if isinstance(summary, dict) else {},
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
            }

    def _trim_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return trim_history(history, max_history=self._max_history)

    def _trim_history_by_chars(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Обрезать историю по символьному бюджету с сохранением system-первым."""
        self._sync_compactor_limit()
        return trim_history_by_chars(
            history,
            max_history_chars=self._max_history_chars,
            compactor=self._compactor,
        )

    # Backward-compatible aliases for history helpers.
    _sanitize_tool_history = staticmethod(sanitize_tool_history)
    _history_char_count = staticmethod(history_char_count)

    def _sync_compactor_limit(self) -> None:
        """Sync compactor limit if _max_tool_result_chars was changed post-init."""
        if self._compactor._max_tool_result_chars != self._max_tool_result_chars:
            self._compactor._max_tool_result_chars = self._max_tool_result_chars

    def _prepare_tool_result_for_history(self, tool_name: str, tool_result: str) -> str:
        """Сжать tool-result для history, чтобы не переполнять контекст LLM."""
        self._sync_compactor_limit()
        return self._compactor.prepare_tool_result_for_history(tool_name, tool_result)

    def _build_cached_tool_stub(self, *, tool_name: str, compact_content: str) -> str:
        """Построить сверх-компактный stub для повторного tool-результата в history."""
        self._sync_compactor_limit()
        return self._compactor.build_cached_tool_stub(
            tool_name=tool_name,
            compact_content=compact_content,
        )

    def _fit_payload_to_limit(self, payload: dict[str, Any]) -> str:
        """Уместить JSON-пейлоад в лимит, сохранив валидный JSON."""
        self._sync_compactor_limit()
        return self._compactor.fit_payload_to_limit(payload)

    @staticmethod
    def _extract_usage_details(response: Any) -> dict[str, int] | None:
        """Извлечь usage-details из ответа LLM (OpenAI-compatible / normalized dict)."""
        return extract_usage_details(response)

    @staticmethod
    def _estimate_usage_details(
        *,
        messages: list[dict[str, Any]],
        message: dict[str, Any],
    ) -> dict[str, int]:
        """Fallback-оценка токенов, если провайдер не вернул usage.

        Используем грубую эвристику ~4 символа на токен для кириллицы/латиницы.
        Это лучше, чем пустое usage в Langfuse для cost-контроля.
        """
        input_chars = 0
        for item in messages:
            with contextlib.suppress(Exception):
                input_chars += len(json.dumps(item, ensure_ascii=False))

        output_chars = 0
        with contextlib.suppress(Exception):
            output_chars = len(json.dumps(message, ensure_ascii=False))

        input_tokens = max(1, math.ceil(input_chars / 4)) if input_chars > 0 else 0
        output_tokens = max(1, math.ceil(output_chars / 4)) if output_chars > 0 else 0
        total_tokens = input_tokens + output_tokens
        return {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        }

    def _compact_tool_result(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._compactor.compact_tool_result(tool_name, payload)

    def _compact_products_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._compactor._compact_products_search(payload)

    def _compact_recipe_ingredients(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._compactor._compact_recipe_ingredients(payload)

    def _compact_recipe_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._compactor._compact_recipe_search(payload)

    # Backward-compatible aliases delegating to module-level functions.
    _normalize_compact_text = staticmethod(normalize_compact_text)
    _tokenize_query_terms = staticmethod(tokenize_query_terms)  # type: ignore[assignment]
    _score_search_candidate = staticmethod(score_search_candidate)  # type: ignore[assignment]
    _extract_price_value = staticmethod(extract_price_value)  # type: ignore[assignment]

    @staticmethod
    def _tool_progress_text(tool_name: str) -> str:
        mapping = {
            "vkusvill_products_search": "\U0001f50d Ищу товары...",
            "vkusvill_cart_link_create": "\U0001f6d2 Формирую корзину...",
            "recipe_ingredients": "\U0001f373 Подбираю рецепт...",
            "recipe_search": "\U0001f50d Ищу продукты по рецепту...",
        }
        return mapping.get(tool_name, "\u2699\ufe0f Обрабатываю запрос...")

    @staticmethod
    def _extract_cart_data(*, tool_name: str, tool_result: str) -> dict[str, Any] | None:
        if tool_name != "vkusvill_cart_link_create":
            return None
        with contextlib.suppress(Exception):
            payload = json.loads(tool_result)
            if not isinstance(payload, dict) or not payload.get("ok"):
                return None
            data = payload.get("data")
            if not isinstance(data, dict):
                return None
            link = data.get("link")
            if not isinstance(link, str) or not link.strip():
                return None
            return data
        return None

    def _update_product_index_from_tool_result(
        self,
        *,
        product_index: dict[int, dict[str, Any]],
        tool_name: str,
        tool_result: str,
    ) -> None:
        if tool_name not in {
            "vkusvill_products_search",
            "vkusvill_product_details",
            "recipe_search",
            "get_previous_cart",
            "vkusvill_cart_link_create",
        }:
            return
        with contextlib.suppress(Exception):
            payload = json.loads(tool_result)
            for item in self._extract_search_items(payload):
                normalized = self._normalize_product_row(item)
                if normalized is None:
                    continue
                if (
                    normalized.get("name") == f"Товар {normalized['xml_id']}"
                    and "price" not in normalized
                ):
                    continue
                product_index[normalized["xml_id"]] = normalized

    def _update_search_query_by_xml_id(
        self,
        *,
        search_query_by_xml_id: dict[int, str],
        tool_args: dict[str, Any],
        tool_result: str,
    ) -> None:
        query = str(tool_args.get("q", tool_args.get("query", ""))).strip()
        if not query:
            return
        payload = self._parse_json_payload(tool_result)
        for item in self._extract_search_items(payload):
            normalized = self._normalize_product_row(item)
            if normalized is None:
                continue
            search_query_by_xml_id[normalized["xml_id"]] = query

    @staticmethod
    def _has_recipe_search_candidates(history: list[dict[str, Any]]) -> bool:
        products, _not_found_count = ShoppingAgent._extract_recipe_products_from_history(history)
        return bool(products)

    @staticmethod
    def _extract_recipe_products_from_history(
        history: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        for msg in reversed(history):
            if msg.get("role") != "tool" or msg.get("name") != "recipe_search":
                continue
            payload = ShoppingAgent._parse_json_payload(msg.get("content"))
            if not isinstance(payload, dict):
                continue
            found_raw = payload.get("found")
            if not isinstance(found_raw, list):
                results_raw = payload.get("results")
                if isinstance(results_raw, list):
                    found_raw = []
                    for row in results_raw:
                        if not isinstance(row, dict):
                            continue
                        best_match = row.get("best_match")
                        if not isinstance(best_match, dict):
                            continue
                        found_raw.append(
                            {
                                "xml_id": best_match.get("xml_id"),
                                "suggested_q": best_match.get("suggested_q"),
                            }
                        )
                if not isinstance(found_raw, list):
                    continue

            not_found_raw = payload.get("not_found")
            not_found_count = len(not_found_raw) if isinstance(not_found_raw, list) else 0
            quantities_by_xml_id: dict[int, float] = {}
            for row in found_raw:
                if not isinstance(row, dict):
                    continue
                xml_id_raw = row.get("xml_id")
                if isinstance(xml_id_raw, bool):
                    continue
                try:
                    xml_id = int(xml_id_raw)
                except (TypeError, ValueError):
                    continue
                suggested_q = ShoppingAgent._safe_float(row.get("suggested_q"), default=1.0)
                if suggested_q <= 0:
                    suggested_q = 1.0
                quantities_by_xml_id[xml_id] = quantities_by_xml_id.get(xml_id, 0.0) + suggested_q

            products: list[dict[str, Any]] = [
                {"xml_id": xml_id, "q": q} for xml_id, q in quantities_by_xml_id.items()
            ]
            return products, not_found_count
        return [], 0

    async def _recover_cart_from_recipe_search_history(
        self,
        *,
        history: list[dict[str, Any]],
        llm_provider: str,
        call_cache: dict[str, str],
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
        products, _not_found_count = self._extract_recipe_products_from_history(history)
        if not products:
            return None, {}, ""

        cart_args = CartProcessor.fix_cart_args({"products": products})
        cart_result = await self._call_mcp_tool(
            name="vkusvill_cart_link_create",
            arguments=cart_args,
            llm_provider=llm_provider,
            call_cache=call_cache,
        )
        cart_data = self._extract_cart_data(
            tool_name="vkusvill_cart_link_create",
            tool_result=cart_result,
        )
        if cart_data is None:
            return None, cart_args, cart_result
        if "products" not in cart_data:
            cart_data["products"] = cart_args.get("products", [])
        if "requested_products" not in cart_data:
            cart_data["requested_products"] = cart_args.get("products", [])
        return cart_data, cart_args, cart_result

    def _normalize_product_row(self, item: dict[str, Any]) -> dict[str, Any] | None:
        xml_id_raw = item.get("xml_id", item.get("id"))
        if isinstance(xml_id_raw, bool):
            return None
        with contextlib.suppress(TypeError, ValueError):
            xml_id = int(xml_id_raw)
        if not isinstance(xml_id, int):
            return None
        name = str(item.get("name", f"Товар {xml_id}")).strip() or f"Товар {xml_id}"
        unit = str(item.get("unit", "шт")).strip() or "шт"
        price = self._extract_price_value(item.get("price"))
        result: dict[str, Any] = {
            "xml_id": xml_id,
            "name": name,
            "unit": unit,
        }
        if price is not None:
            result["price"] = price
        return result

    def _extract_price_value(self, raw_price: Any) -> float | None:
        if isinstance(raw_price, dict):
            for key in ("current", "value", "amount", "price"):
                if key in raw_price:
                    price = self._safe_float(raw_price.get(key), default=-1.0)
                    if price >= 0:
                        return price
            return None
        price = self._safe_float(raw_price, default=-1.0)
        return price if price >= 0 else None

    def _ensure_cart_price_summary(
        self,
        *,
        cart_data: dict[str, Any],
        product_index: dict[int, dict[str, Any]],
    ) -> None:
        summary = cart_data.get("price_summary")
        if isinstance(summary, dict):
            items = summary.get("items")
            total_text = summary.get("total_text")
            has_total_text = isinstance(total_text, str) and bool(total_text.strip())
            if isinstance(items, list) and items and has_total_text:
                return

        products = cart_data.get("products")
        if not isinstance(products, list) or not products:
            return
        requested_products = cart_data.get("requested_products")
        if not isinstance(requested_products, list) or not requested_products:
            requested_products = products

        lines: list[str] = []
        recipe_lines: list[str] = []
        item_details: list[dict[str, Any]] = []
        total = 0.0
        recipe_total = 0.0
        dual_pricing = False
        all_priced = True
        original_total_text = ""
        original_total_value = -1.0
        if isinstance(summary, dict):
            total_text_raw = summary.get("total_text")
            if isinstance(total_text_raw, str) and total_text_raw.strip():
                original_total_text = total_text_raw.strip()
            original_total_value = self._safe_float(summary.get("total"), default=-1.0)

        purchase_by_xml_id, purchase_order = self._aggregate_products_by_xml_id(products)
        recipe_by_xml_id, _recipe_order = self._aggregate_products_by_xml_id(requested_products)
        for xml_id_int in purchase_order:
            quantity = purchase_by_xml_id.get(xml_id_int, 1.0)
            if quantity <= 0:
                quantity = 1.0
            recipe_quantity = recipe_by_xml_id.get(xml_id_int, quantity)
            if recipe_quantity <= 0:
                recipe_quantity = quantity
            if abs(quantity - recipe_quantity) >= 1e-9:
                dual_pricing = True

            normalized = product_index.get(xml_id_int)
            if normalized is None:
                normalized = self._normalize_product_row({"xml_id": xml_id_int, "q": quantity})
            raw_name = (
                str(normalized.get("name", f"Товар {xml_id_int}")).strip()
                if normalized
                else f"Товар {xml_id_int}"
            )
            name = self._normalize_compact_text(raw_name) or f"Товар {xml_id_int}"
            price = (
                self._safe_float(normalized.get("price"), default=-1.0)
                if isinstance(normalized, dict)
                else -1.0
            )
            unit = str(normalized.get("unit", "")).strip() if isinstance(normalized, dict) else ""
            quantity_text = self._format_quantity_text(quantity, unit=unit)

            if price >= 0:
                subtotal = price * quantity
                recipe_subtotal = price * recipe_quantity
                total += subtotal
                recipe_total += recipe_subtotal
                lines.append(f"- {name} x {quantity_text} = {subtotal:.2f} руб")
                recipe_lines.append(
                    f"- {name} x {self._format_quantity_text(recipe_quantity, unit=unit)} = "
                    f"{recipe_subtotal:.2f} руб"
                )
                item_details.append(
                    {
                        "xml_id": xml_id_int,
                        "name": name,
                        "unit": normalized.get("unit") if isinstance(normalized, dict) else None,
                        "unit_price": price,
                        "recipe_q": recipe_quantity,
                        "purchase_q": quantity,
                        "recipe_subtotal": round(recipe_subtotal, 2),
                        "purchase_subtotal": round(subtotal, 2),
                        "overbuy_subtotal": round(subtotal - recipe_subtotal, 2),
                    }
                )
            else:
                all_priced = False
                lines.append(f"- {name} x {quantity_text} = цена уточняется")
                recipe_lines.append(
                    "- "
                    f"{name} x {self._format_quantity_text(recipe_quantity, unit=unit)} = "
                    "цена уточняется"
                )
                item_details.append(
                    {
                        "xml_id": xml_id_int,
                        "name": name,
                        "recipe_q": recipe_quantity,
                        "purchase_q": quantity,
                    }
                )

        if not lines:
            return

        synthesized: dict[str, Any] = {
            "items": lines,
            "recipe_items": recipe_lines,
            "item_details": item_details,
            "dual_pricing": dual_pricing,
            "count": len(lines),
        }
        if all_priced:
            rounded_total = round(total, 2)
            rounded_recipe_total = round(recipe_total, 2)
            overbuy_total = round(rounded_total - rounded_recipe_total, 2)
            synthesized["total"] = rounded_total
            synthesized["total_text"] = f"Итого: {rounded_total:.2f} руб"
            synthesized["purchase_total"] = rounded_total
            synthesized["purchase_total_text"] = f"К покупке: {rounded_total:.2f} руб"
            synthesized["recipe_total"] = rounded_recipe_total
            synthesized["recipe_total_text"] = f"По рецепту: {rounded_recipe_total:.2f} руб"
            synthesized["overbuy_total"] = overbuy_total
            synthesized["overbuy_total_text"] = f"Переплата из-за упаковок: {overbuy_total:.2f} руб"
        elif original_total_text:
            synthesized["total_text"] = original_total_text
        elif original_total_value >= 0:
            synthesized["total"] = round(original_total_value, 2)
            synthesized["total_text"] = f"Итого: {original_total_value:.2f} руб"
        else:
            synthesized["total_text"] = "Итого: будет рассчитано при открытии корзины"

        cart_data["price_summary"] = synthesized

    @staticmethod
    def _aggregate_products_by_xml_id(
        products: list[dict[str, Any]],
    ) -> tuple[dict[int, float], list[int]]:
        totals: dict[int, float] = {}
        order: list[int] = []
        for item in products:
            if not isinstance(item, dict):
                continue
            xml_id_raw = item.get("xml_id")
            if isinstance(xml_id_raw, bool):
                continue
            with contextlib.suppress(TypeError, ValueError):
                xml_id = int(xml_id_raw)
            if not isinstance(xml_id, int):
                continue
            quantity = ShoppingAgent._safe_float(item.get("q"), default=1.0)
            if quantity <= 0:
                quantity = 1.0
            if xml_id not in totals:
                totals[xml_id] = 0.0
                order.append(xml_id)
            totals[xml_id] += quantity
        return totals, order

    @staticmethod
    def _format_quantity_text(quantity: float, *, unit: str = "") -> str:
        normalized_unit = unit.strip().lower()
        if normalized_unit in _DISCRETE_UNITS:
            return str(max(1, math.ceil(quantity)))
        if normalized_unit in {"кг", "kg"}:
            return f"{ShoppingAgent._round_kilogram_quantity(quantity):.1f}"
        if float(quantity).is_integer():
            return str(int(quantity))
        return f"{quantity:.3f}".rstrip("0").rstrip(".")

    @staticmethod
    def _round_kilogram_quantity(quantity: float) -> float:
        safe_quantity = quantity if quantity > 0 else 0.1
        rounded = math.ceil(safe_quantity * 10) / 10
        return round(max(0.1, rounded), 1)

    @staticmethod
    def _is_additive_cart_intent(user_text: str) -> bool:
        normalized = user_text.lower()
        return any(marker in normalized for marker in _ADDITIVE_CART_MARKERS)

    @staticmethod
    def _is_cart_intent(user_text: str) -> bool:
        normalized = user_text.lower()
        return any(marker in normalized for marker in CART_INTENT_MARKERS)

    @staticmethod
    def _looks_like_partial_recipe_reply(text: str) -> bool:
        normalized = text.lower()
        if not normalized.strip():
            return False
        if "открыть корзину" in normalized or "share_basket" in normalized:
            return False
        markers = (
            "ингредиент",
            "подобрал",
            "подобрала",
            "рецепт",
            "список продуктов",
            "могу продолж",
            "если нужно",
        )
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _looks_like_manual_cart_reply(text: str) -> bool:
        normalized = text.lower()
        markers = (
            "вручн",
            "самостоятель",
            "соберите корзину сами",
            "оформите заказ сами",
            "собрать корзину самому",
            "добавьте товары сами",
            "перейдите на сайт",
            "в приложении соберите",
        )
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _looks_like_cart_ready_reply(text: str) -> bool:
        normalized = text.lower()
        if "открыть корзину" in normalized:
            return True
        if "итого" in normalized and any(
            word in normalized for word in ("корзин", "собрал", "собрала")
        ):
            return True
        if "корзин" in normalized and any(
            word in normalized for word in ("собрал", "собрала", "собрано", "готова", "готово")
        ):
            return True
        return "share_basket" in normalized

    @staticmethod
    def _looks_like_wrong_cart_summary(text: str, *, items_count: int) -> bool:
        normalized = text.lower()
        if items_count > 0 and (
            "0 товар" in normalized
            or "0 пози" in normalized
            or "ноль товар" in normalized
            or "ноль пози" in normalized
        ):
            return True
        return "не удалось создать корзину" in normalized or "не могу создать корзину" in normalized

    def _should_start_fresh_context(
        self,
        *,
        text: str,
        history: list[dict[str, Any]] | None,
    ) -> bool:
        if not history or len(history) < 3:
            return False

        normalized = text.lower()
        if any(marker in normalized for marker in _MODIFY_EXISTING_CART_MARKERS):
            return False

        if not self._is_cart_intent(text):
            return False

        last_assistant_text = ""
        for msg in reversed(history):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                last_assistant_text = content
                break
        if not last_assistant_text:
            return False

        response_low = last_assistant_text.lower()
        has_last_cart = self._looks_like_cart_ready_reply(last_assistant_text) or (
            "<a href=" in response_low and "vkusvill.ru" in response_low
        )
        if not has_last_cart:
            return False

        # Статус/проверка не должны запускать новую корзину.
        if any(marker in normalized for marker in _STATUS_QUERY_MARKERS):
            return False

        if any(marker in normalized for marker in _EXPLICIT_NEW_CART_MARKERS):
            return True

        # Если корзина уже собрана и пользователь не просит явную модификацию,
        # трактуем сообщение как новый запрос на новую корзину.
        return True

    # Backward-compatible aliases delegating to cart_output_renderer module.
    _render_stable_cart_output = staticmethod(render_stable_cart_output)  # type: ignore[assignment]
    _extract_cart_safety_note = staticmethod(extract_cart_safety_note)  # type: ignore[assignment]
    _extract_first_url = staticmethod(extract_first_url)
    _looks_like_manual_cart_reply = staticmethod(looks_like_manual_cart_reply)
    _looks_like_cart_ready_reply = staticmethod(looks_like_cart_ready_reply)
    _looks_like_wrong_cart_summary = staticmethod(looks_like_wrong_cart_summary)
    _looks_like_wrong_cart_link = staticmethod(looks_like_wrong_cart_link)  # type: ignore[assignment]
    _looks_like_missing_cart_prices = staticmethod(looks_like_missing_cart_prices)

    def _stabilize_cart_output(self, *, final_text: str, cart_data: dict[str, Any]) -> str:
        return stabilize_cart_output(final_text=final_text, cart_data=cart_data)
