"""ShoppingAgent: OpenAI-compatible chat engine поверх MCP."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from vkuswill_bot.agents.intent_markers import (
    PANTRY_TAG_PEPPER,
    PANTRY_TAG_SALT,
    PANTRY_TAG_SUGAR,
)
from vkuswill_bot.agents.history_manager import (
    history_char_count,
    trim_history,
    trim_history_by_chars,
)
from vkuswill_bot.agents.recipe_helpers import (
    apply_requested_quantity_overrides,
    extract_explicit_pantry_requests,
    extract_structured_ingredient_requests,
    has_explicit_egg_pack_request,
    sanitize_recipe_ingredients_tool_result,
)
from vkuswill_bot.agents.cart_price_builder import (
    ensure_cart_price_summary,
)
from vkuswill_bot.agents.product_index_manager import (
    build_cart_snapshot,
    build_product_index_from_history,
    update_product_index_from_tool_result,
    update_search_query_by_xml_id,
)
from vkuswill_bot.agents.mcp_response_parser import (
    extract_cart_data,
    extract_recipe_products_from_history,
    has_recipe_search_candidates,
)
from vkuswill_bot.agents.recipe_fallback import (
    fallback_recipe_ingredients,
    fallback_recipe_search,
    extract_recipe_ingredients_with_llm,
)
from vkuswill_bot.agents.mcp_helpers import (
    is_successful_tool_result,
    make_mcp_call_cache_key,
    tool_progress_text,
    with_virtual_recipe_tools,
)
from vkuswill_bot.agents.prompt_helpers import (
    build_llm_input_messages,
    ensure_system_prompt,
    resolve_prompt_mode,
    resolve_prompt_profile,
)
from vkuswill_bot.agents.response_analysis import (
    is_cart_intent,
    looks_like_partial_recipe_reply,
    should_start_fresh_context,
)
from vkuswill_bot.agents.llm_helpers import (
    assistant_msg,
    estimate_usage_details,
    extract_message,
    extract_text,
    extract_tool_calls,
    parse_tool_args,
)
from vkuswill_bot.agents.tool_preprocessor import (
    collect_requested_products_snapshot,
    preprocess_tool_args,
    restore_previous_quantities_for_additive_update,
)
from vkuswill_bot.agents.cart_output_renderer import (
    extract_cart_safety_note,
    looks_like_cart_ready_reply,
    looks_like_manual_cart_reply,
    render_stable_cart_output,
)
from vkuswill_bot.agents.recovery_hints import (
    FORCE_BATCH_SEARCH_HINT,
    FORCE_CART_FLOW_CONTINUATION_HINT,
    FORCE_CART_LINK_SOURCE_HINT,
    FORCE_CART_RECOVERY_HINT,
    FORCE_RECIPE_TO_CART_HINT,
)
from vkuswill_bot.agents.tool_result_compactor import ToolResultCompactor
from vkuswill_bot.services.llm_adapters import (
    LLMAdapterProtocol,
    OpenAICompatibleLLMAdapter,
    create_llm_adapter,
    extract_usage_details,
    normalize_llm_provider,
)
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.preferences_parser import parse_preferences
from vkuswill_bot.services.prompts import PromptProfile

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
_DEFAULT_MAX_ACTIVE_USERS = 2000
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
        max_active_users: int = _DEFAULT_MAX_ACTIVE_USERS,
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
        self._max_active_users = max(1, max_active_users)
        self._preferences_store = preferences_store
        self._api_semaphore = asyncio.Semaphore(max(1, llm_max_concurrent))
        self._langfuse = langfuse_service
        self._tools_cache: list[dict[str, Any]] | None = None
        self._mcp_tool_names: set[str] = set()
        self._history: dict[int, list[dict[str, Any]]] = {}
        self._last_cart_snapshot: dict[int, dict[str, Any]] = {}
        self._active_users: OrderedDict[int, None] = OrderedDict()
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
        self._last_cart_snapshot.pop(user_id, None)
        self._active_users.pop(user_id, None)

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
            self._touch_active_user(user_id)
            return await self._process_locked(
                user_id=user_id,
                text=text,
                on_progress=on_progress,
                llm_provider=provider,
            )

    def _touch_active_user(self, user_id: int) -> None:
        """Mark user as active and prune oldest in-memory state if needed."""
        self._active_users.pop(user_id, None)
        self._active_users[user_id] = None
        while len(self._active_users) > self._max_active_users:
            stale_user_id, _ = self._active_users.popitem(last=False)
            self._history.pop(stale_user_id, None)
            self._last_cart_snapshot.pop(stale_user_id, None)

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
        prompt_profile = resolve_prompt_profile(text=text, history=history)
        history = ensure_system_prompt(
            history=history,
            prompt_profile=prompt_profile,
            mode="start",
            prompt_profiles_enabled=self._prompt_profiles_enabled,
        )

        product_index_this_turn: dict[int, dict[str, Any]] = build_product_index_from_history(
            history
        )
        history.append({"role": "user", "content": text})
        history = self._normalize_history(history)
        cart_data_this_turn: dict[str, Any] | None = None
        manual_recovery_used = False
        cart_creation_recovery_used = False
        search_batch_recovery_used = False
        cart_flow_recovery_used = False
        recipe_to_cart_recovery_used = False
        single_search_steps_streak = 0
        tools_called_this_turn = False
        recipe_flow_started_this_turn = False
        cart_intent = is_cart_intent(text)
        explicit_pantry_requests = extract_explicit_pantry_requests(text)
        explicit_egg_pack_request = has_explicit_egg_pack_request(text)
        requested_ingredients = extract_structured_ingredient_requests(text)
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
            prompt_mode = resolve_prompt_mode(
                step=step,
                expecting_final_answer=cart_data_this_turn is not None,
                compact_followup_prompt_enabled=self._compact_followup_prompt_enabled,
            )
            llm_input = build_llm_input_messages(
                history=history,
                prompt_profile=prompt_profile,
                mode=prompt_mode,
                prompt_profiles_enabled=self._prompt_profiles_enabled,
            )
            llm_input_chars = history_char_count(llm_input)
            total_llm_input_chars += llm_input_chars
            if step > 1 and total_llm_input_chars > self._max_input_chars_per_turn:
                logger.warning(
                    "ShoppingAgent prompt budget exceeded: total_chars=%d step=%d",
                    total_llm_input_chars,
                    step,
                )
                self._history[user_id] = self._normalize_history(history)
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

            message = extract_message(response)
            usage_details = self._extract_usage_details(response)
            usage_source = "provider"
            if usage_details is None:
                usage_details = estimate_usage_details(messages=llm_input, message=message)
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

            tool_calls = extract_tool_calls(message)
            if not tool_calls:
                final_text = extract_text(message) or _ERROR_GENERIC
                manual_cart_reply = looks_like_manual_cart_reply(final_text)

                if (
                    cart_data_this_turn is None
                    and cart_intent
                    and tools_called_this_turn
                    and recipe_flow_started_this_turn
                    and looks_like_partial_recipe_reply(final_text)
                    and not cart_flow_recovery_used
                    and step < self._max_tool_calls
                ):
                    cart_flow_recovery_used = True
                    logger.info("Recipe flow recovery: continue tool-chain until cart_link_create")
                    history.append(assistant_msg(message))
                    history.append(
                        {"role": "system", "content": _FORCE_CART_FLOW_CONTINUATION_HINT}
                    )
                    history = self._normalize_history(history)
                    continue

                if (
                    cart_data_this_turn is None
                    and cart_intent
                    and manual_cart_reply
                    and not manual_recovery_used
                    and step < self._max_tool_calls
                ):
                    manual_recovery_used = True
                    history.append(assistant_msg(message))
                    history.append({"role": "system", "content": _FORCE_CART_RECOVERY_HINT})
                    history = self._normalize_history(history)
                    continue

                if (
                    cart_data_this_turn is None
                    and cart_intent
                    and looks_like_cart_ready_reply(final_text)
                    and not cart_creation_recovery_used
                    and step < self._max_tool_calls
                ):
                    cart_creation_recovery_used = True
                    history.append(assistant_msg(message))
                    history.append({"role": "system", "content": _FORCE_CART_LINK_SOURCE_HINT})
                    history = self._normalize_history(history)
                    continue

                if cart_data_this_turn is not None:
                    self._ensure_cart_price_summary(
                        cart_data=cart_data_this_turn,
                        product_index=product_index_this_turn,
                    )
                    # При наличии фактической корзины всегда отдаём детерминированный
                    # формат из данных MCP (а не свободный рерайт LLM).
                    safety_note = extract_cart_safety_note(final_text)
                    final_text = render_stable_cart_output(
                        cart_data_this_turn,
                        safety_note=safety_note,
                    )

                self._history[user_id] = self._trim_history(
                    [*history, assistant_msg(message)],
                )
                if trace is not None:
                    trace.update(
                        output=final_text,
                        metadata={"tool_calls": step - 1, "provider": llm_provider},
                    )
                return final_text

            history.append(assistant_msg(message))
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
                raw_tool_args = parse_tool_args(tool_call.get("arguments"))
                raw_tool_args_snapshot = copy.deepcopy(raw_tool_args)
                requested_quantity_overrides: dict[int, float] = {}
                tool_args = preprocess_tool_args(
                    tool_name,
                    raw_tool_args,
                    user_preferences=user_preferences,
                    product_index=product_index_this_turn,
                    explicit_egg_pack_request=explicit_egg_pack_request,
                    requested_ingredients=requested_ingredients,
                    search_query_by_xml_id=search_query_by_xml_id_this_turn,
                    requested_quantity_overrides=requested_quantity_overrides,
                )
                tool_args = restore_previous_quantities_for_additive_update(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    user_text=text,
                    previous_products=previous_cart_products,
                    requested_quantity_overrides=requested_quantity_overrides,
                )
                requested_products_snapshot = (
                    apply_requested_quantity_overrides(
                        collect_requested_products_snapshot(
                            raw_tool_args_snapshot,
                            product_index=product_index_this_turn,
                            explicit_egg_pack_request=explicit_egg_pack_request,
                        ),
                        requested_quantity_overrides,
                    )
                    if tool_name == "vkusvill_cart_link_create"
                    else []
                )

                await _progress(tool_progress_text(tool_name))
                tool_span = trace.span(name=f"tool:{tool_name}", input=tool_args) if trace else None

                tool_result = await self._call_mcp_tool(
                    name=tool_name,
                    arguments=tool_args,
                    llm_provider=llm_provider,
                    call_cache=mcp_call_cache,
                )
                if tool_name == "recipe_ingredients":
                    tool_result = sanitize_recipe_ingredients_tool_result(
                        tool_result=tool_result,
                        explicit_pantry_requests=explicit_pantry_requests,
                    )
                update_product_index_from_tool_result(
                    product_index=product_index_this_turn,
                    tool_name=tool_name,
                    tool_result=tool_result,
                )
                if tool_name == "vkusvill_products_search":
                    update_search_query_by_xml_id(
                        search_query_by_xml_id=search_query_by_xml_id_this_turn,
                        tool_args=tool_args,
                        tool_result=tool_result,
                    )
                tools_called_this_turn = True
                if tool_name in {"recipe_ingredients", "recipe_search"}:
                    recipe_flow_started_this_turn = True
                cart_data = extract_cart_data(tool_name=tool_name, tool_result=tool_result)
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

            history = self._normalize_history(history)
            if (
                cart_intent
                and recipe_flow_started_this_turn
                and cart_data_this_turn is None
                and not recipe_to_cart_recovery_used
                and has_recipe_search_candidates(history)
                and step < self._max_tool_calls
            ):
                recipe_to_cart_recovery_used = True
                history.append({"role": "system", "content": _FORCE_RECIPE_TO_CART_HINT})
                history = self._normalize_history(history)
            if (
                cart_intent
                and cart_data_this_turn is None
                and single_search_steps_streak >= 3
                and not search_batch_recovery_used
                and step < self._max_tool_calls
            ):
                search_batch_recovery_used = True
                history.append({"role": "system", "content": _FORCE_BATCH_SEARCH_HINT})
                history = self._normalize_history(history)

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
            final_text = render_stable_cart_output(cart_data_this_turn)
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

    async def _get_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is not None:
            return self._tools_cache

        raw_tools = await self._mcp_client.get_tools()
        self._mcp_tool_names = {
            str(tool.get("name", "")).strip()
            for tool in raw_tools
            if isinstance(tool, dict) and str(tool.get("name", "")).strip()
        }
        raw_tools = with_virtual_recipe_tools(raw_tools)
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
            cache_key = make_mcp_call_cache_key(name=name, arguments=arguments)
            cached = call_cache.get(cache_key)
            if isinstance(cached, str):
                return cached

        if name == "recipe_ingredients" and name not in self._mcp_tool_names:
            fallback = await self._fallback_recipe_ingredients(arguments, llm_provider)
            if cache_key is not None and is_successful_tool_result(fallback):
                call_cache[cache_key] = fallback
            return fallback
        if name == "recipe_search" and name not in self._mcp_tool_names:
            fallback = await self._fallback_recipe_search(arguments)
            if cache_key is not None and is_successful_tool_result(fallback):
                call_cache[cache_key] = fallback
            return fallback

        last_error: Exception | None = None
        for attempt in range(self._mcp_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._mcp_client.call_tool(name, arguments),
                    timeout=self._mcp_timeout_seconds,
                )
                if cache_key is not None and is_successful_tool_result(result):
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
                    if cache_key is not None and is_successful_tool_result(fallback):
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
        return await fallback_recipe_ingredients(
            arguments,
            adapter=self._llm_adapters.get(llm_provider),
            model=self._resolve_model_for_provider(llm_provider),
            timeout_seconds=self._llm_timeout_seconds,
        )

    async def _extract_recipe_ingredients_with_llm(
        self,
        *,
        dish: str,
        servings: int,
        llm_provider: str,
    ) -> list[dict[str, Any]]:
        return await extract_recipe_ingredients_with_llm(
            dish=dish,
            servings=servings,
            adapter=self._llm_adapters.get(llm_provider),
            model=self._resolve_model_for_provider(llm_provider),
            timeout_seconds=self._llm_timeout_seconds,
        )

    async def _fallback_recipe_search(self, arguments: dict[str, Any]) -> str:
        return await fallback_recipe_search(
            arguments,
            search_fn=self._search_products_for_recipe,
        )

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

    async def _load_user_preferences(self, user_id: int) -> dict[str, str]:
        if self._preferences_store is None:
            return {}
        try:
            raw = await self._preferences_store.get_formatted(user_id)
        except Exception as exc:
            logger.warning("Failed to load user preferences for %s: %s", user_id, exc)
            return {}
        return parse_preferences(raw)

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
        snapshot = build_cart_snapshot(args=args, result=result)
        if snapshot is not None:
            self._last_cart_snapshot[user_id] = snapshot

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

    def _normalize_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Нормализовать историю по count и символьному бюджету."""
        return self._trim_history_by_chars(self._trim_history(history))

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

    def _compact_tool_result(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._compactor.compact_tool_result(tool_name, payload)

    def _compact_products_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._compactor._compact_products_search(payload)

    def _compact_recipe_ingredients(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._compactor._compact_recipe_ingredients(payload)

    def _compact_recipe_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._compactor._compact_recipe_search(payload)

    async def _recover_cart_from_recipe_search_history(
        self,
        *,
        history: list[dict[str, Any]],
        llm_provider: str,
        call_cache: dict[str, str],
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
        products, _not_found_count = extract_recipe_products_from_history(history)
        if not products:
            return None, {}, ""

        cart_args = CartProcessor.fix_cart_args({"products": products})
        cart_result = await self._call_mcp_tool(
            name="vkusvill_cart_link_create",
            arguments=cart_args,
            llm_provider=llm_provider,
            call_cache=call_cache,
        )
        cart_data = extract_cart_data(
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

    def _ensure_cart_price_summary(
        self,
        *,
        cart_data: dict[str, Any],
        product_index: dict[int, dict[str, Any]],
    ) -> None:
        ensure_cart_price_summary(cart_data=cart_data, product_index=product_index)

    def _should_start_fresh_context(
        self,
        *,
        text: str,
        history: list[dict[str, Any]] | None,
    ) -> bool:
        return should_start_fresh_context(text=text, history=history)
