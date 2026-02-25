"""ShoppingAgent: OpenAI-compatible chat engine поверх MCP."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import html
import json
import logging
import math
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

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
_FORCE_CART_RECOVERY_HINT = (
    "[Системная корректировка] Пользователь ожидает готовую корзину. "
    "Запрещено предлагать ручную сборку или отправлять пользователя собирать корзину самому. "
    "Используй доступные инструменты и обязательно создай корзину через "
    "vkusvill_cart_link_create. После успешного создания дай финальный ответ."
)
_FORCE_CART_LINK_SOURCE_HINT = (
    "[Системная корректировка] Ты сообщил итог/ссылку корзины без фактического "
    "результата vkusvill_cart_link_create. Сначала ОБЯЗАТЕЛЬНО вызови "
    "vkusvill_cart_link_create, затем выдай ответ. "
    "Ссылку бери только из data.link результата инструмента."
)
_FORCE_CART_FLOW_CONTINUATION_HINT = (
    "[Системная корректировка] Ты начал сборку корзины, но преждевременно остановился "
    "текстовым ответом. Сборка должна быть доведена до конца через инструменты. "
    "Продолжи вызовы поиска (vkusvill_products_search / recipe_search) и затем "
    "ОБЯЗАТЕЛЬНО вызови vkusvill_cart_link_create. Только после успешного "
    "vkusvill_cart_link_create выдай финальный текст пользователю."
)
_FORCE_BATCH_SEARCH_HINT = (
    "[Системная корректировка] Ты слишком долго выполняешь одиночные поиски. "
    "Для независимых ингредиентов возвращай НЕСКОЛЬКО вызовов "
    "vkusvill_products_search в одном ответе (batch tool-calls). "
    "Цель: уложиться в лимит шагов. "
    "Как только подобраны товары по ингредиентам — сразу вызывай "
    "vkusvill_cart_link_create и завершай сборку корзины."
)
_FORCE_RECIPE_TO_CART_HINT = (
    "[Системная корректировка] Результаты recipe_search уже получены. "
    "Не делай дополнительную пагинацию и не вызывай vkusvill_product_details "
    "для каждого ингредиента. Используй найденные xml_id и suggested_q из recipe_search, "
    "вызови vkusvill_cart_link_create и завершай сборку корзины."
)
_PANTRY_TAG_SALT = "salt"
_PANTRY_TAG_SUGAR = "sugar"
_PANTRY_TAG_PEPPER = "pepper"
_EGG_PACK_SIZE = 10
_MODIFY_EXISTING_CART_MARKERS = (
    "добав",
    "ещё",
    "еще",
    "объедин",
    "к предыдущ",
    "к прошл",
    "ту же корзин",
    "к этой корзин",
    "дополни",
    "продолж",
    "и еще",
    "убер",
    "удал",
    "замен",
    "измени",
    "поменя",
    "исключ",
)
_EXPLICIT_NEW_CART_MARKERS = (
    "новая корзин",
    "новый заказ",
    "с нуля",
    "заново",
    "собери заново",
    "отдельную корзин",
    "другую корзин",
    "не в эту корзин",
)
_STATUS_QUERY_MARKERS = (
    "статус",
    "что с корзин",
    "где корзин",
    "проверь корзин",
)


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
        user_preferences = await self._load_user_preferences(user_id)
        total_llm_input_chars = 0
        mcp_call_cache: dict[str, str] = {}

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
                tool_args = self._preprocess_tool_args(
                    tool_name,
                    self._parse_tool_args(tool_call.get("arguments")),
                    user_preferences=user_preferences,
                    product_index=product_index_this_turn,
                    explicit_egg_pack_request=explicit_egg_pack_request,
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
                tools_called_this_turn = True
                if tool_name in {"recipe_ingredients", "recipe_search"}:
                    recipe_flow_started_this_turn = True
                cart_data = self._extract_cart_data(tool_name=tool_name, tool_result=tool_result)
                if cart_data is not None:
                    products = tool_args.get("products")
                    if isinstance(products, list) and "products" not in cart_data:
                        cart_data["products"] = products
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
                    "Для q используй kg_equivalent/l_equivalent/pack_equivalent. "
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
            best_match = {
                "xml_id": best.get("xml_id"),
                "name": best.get("name"),
                "price": best.get("price"),
                "unit": best.get("unit", "шт"),
                "suggested_q": self._suggested_q_from_ingredient(row, best),
            }
            alternatives = [
                {
                    "xml_id": item.get("xml_id"),
                    "name": item.get("name"),
                    "price": item.get("price"),
                    "unit": item.get("unit", "шт"),
                    "suggested_q": self._suggested_q_from_ingredient(row, item),
                }
                for item in items[1:4]
            ]
            found.append(
                {
                    "ingredient": ingredient_name,
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
            return row_raw
        if not isinstance(row_raw, str):
            return {}

        text = row_raw.strip()
        if not text:
            return {}
        cleaned = text.replace("по вкусу", "").strip(" ,.-")
        tokens = cleaned.split()
        head: list[str] = []
        for token in tokens:
            if any(ch.isdigit() for ch in token):
                break
            head.append(token)
        query = " ".join(head).strip() or cleaned
        return {
            "name": cleaned or text,
            "search_query": query,
            "quantity": 1.0,
            "unit": "шт",
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
    def _suggested_q_from_ingredient(
        ingredient: dict[str, Any],
        item: dict[str, Any],
    ) -> int | float:
        pack_equivalent = ShoppingAgent._safe_float(ingredient.get("pack_equivalent"), default=0.0)
        if pack_equivalent > 0:
            return max(1, math.ceil(pack_equivalent))

        unit = str(item.get("unit", "")).lower().strip()
        kg_equivalent = ShoppingAgent._safe_float(ingredient.get("kg_equivalent"), default=0.0)
        l_equivalent = ShoppingAgent._safe_float(ingredient.get("l_equivalent"), default=0.0)
        quantity = ShoppingAgent._safe_float(ingredient.get("quantity"), default=1.0)
        ingredient_unit = str(ingredient.get("unit", "")).lower().strip()

        if unit == "кг":
            if kg_equivalent > 0:
                return round(kg_equivalent, 3)
            if ingredient_unit == "г":
                return round(quantity / 1000, 3)
        if unit == "л":
            if l_equivalent > 0:
                return round(l_equivalent, 3)
            if ingredient_unit == "мл":
                return round(quantity / 1000, 3)
        if unit in {"шт", "уп", "пач", "бут", "бан", "пак"}:
            return max(1, math.ceil(quantity))
        if quantity <= 0:
            return 1
        return round(quantity, 3)

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
                name = str(product.get("name", "")).strip().lower()
                if not any(stem in name for stem in ("яйц", "яиц", "яйк")):
                    continue
                q = ShoppingAgent._safe_float(item.get("q"), default=1.0)
                if q <= 1:
                    item["q"] = 1
                    continue
                item["q"] = max(1, math.ceil(q / _EGG_PACK_SIZE))
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
        if len(history) <= self._max_history:
            return history
        system_msg = history[0]
        tail = history[-(self._max_history - 1) :]
        return [system_msg, *tail]

    def _trim_history_by_chars(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Обрезать историю по символьному бюджету с сохранением system-первым."""
        if not history:
            return history

        trimmed = self._recompact_tool_history(list(history))
        while self._history_char_count(trimmed) > self._max_history_chars and len(trimmed) > 2:
            del trimmed[1]
            trimmed = self._sanitize_tool_history(trimmed)
        return trimmed

    def _recompact_tool_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Повторно сжать старые tool-сообщения в истории, чтобы убрать раздувшийся контекст."""
        if len(history) <= 2:
            return history

        compacted = [history[0]]
        seen_tool_signatures: set[str] = set()
        for message in history[1:]:
            if message.get("role") != "tool":
                compacted.append(message)
                continue

            name = str(message.get("name", "")).strip()
            content = message.get("content")
            if not name or not isinstance(content, str):
                compacted.append(message)
                continue

            compact_content = self._prepare_tool_result_for_history(name, content)
            signature = f"{name}:{compact_content}"
            if signature in seen_tool_signatures:
                compact_content = self._build_cached_tool_stub(
                    tool_name=name,
                    compact_content=compact_content,
                )
            else:
                seen_tool_signatures.add(signature)

            compacted.append({**message, "content": compact_content})
        return compacted

    @staticmethod
    def _sanitize_tool_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(history) <= 2:
            return history

        sanitized = [history[0]]
        for msg in history[1:]:
            if msg.get("role") != "tool":
                sanitized.append(msg)
                continue

            prev = sanitized[-1] if sanitized else None
            if not isinstance(prev, dict):
                continue
            tool_calls = prev.get("tool_calls")
            if prev.get("role") == "assistant" and isinstance(tool_calls, list) and tool_calls:
                sanitized.append(msg)
        return sanitized

    @staticmethod
    def _history_char_count(history: list[dict[str, Any]]) -> int:
        total = 0
        for message in history:
            with contextlib.suppress(Exception):
                total += len(json.dumps(message, ensure_ascii=False))
        return total

    def _prepare_tool_result_for_history(self, tool_name: str, tool_result: str) -> str:
        """Сжать tool-result для history, чтобы не переполнять контекст LLM."""
        with contextlib.suppress(Exception):
            parsed = json.loads(tool_result)
            if isinstance(parsed, dict):
                compact = self._compact_tool_result(tool_name, parsed)
                return self._fit_payload_to_limit(compact)
        return tool_result[: self._max_tool_result_chars]

    def _build_cached_tool_stub(self, *, tool_name: str, compact_content: str) -> str:
        """Построить сверх-компактный stub для повторного tool-результата в history."""
        base: dict[str, Any] = {"ok": True, "cached": True, "duplicate": True}
        with contextlib.suppress(Exception):
            parsed = json.loads(compact_content)
            if isinstance(parsed, dict):
                if "ok" in parsed:
                    base["ok"] = bool(parsed.get("ok"))
                if tool_name == "vkusvill_products_search":
                    meta = parsed.get("meta")
                    if isinstance(meta, dict):
                        q = str(meta.get("q", "")).strip()
                        if q:
                            base["meta"] = {"q": q}
                    items = parsed.get("items")
                    if isinstance(items, list) and items:
                        first = items[0]
                        if isinstance(first, dict):
                            base["item"] = {
                                key: first.get(key)
                                for key in ("xml_id", "name", "price", "unit")
                                if first.get(key) is not None
                            }
                elif tool_name == "vkusvill_product_details":
                    data = parsed.get("data")
                    if isinstance(data, dict):
                        base["data"] = {
                            key: data.get(key)
                            for key in ("xml_id", "name", "price", "unit")
                            if data.get(key) is not None
                        }
                elif tool_name == "recipe_ingredients":
                    dish = str(parsed.get("dish", "")).strip()
                    if dish:
                        base["dish"] = dish
                    servings = parsed.get("servings")
                    if isinstance(servings, int | float) and not isinstance(servings, bool):
                        base["servings"] = servings
                elif tool_name == "recipe_search":
                    found = parsed.get("found")
                    if isinstance(found, list):
                        base["found_count"] = len(found)
                    not_found = parsed.get("not_found")
                    if isinstance(not_found, list):
                        base["not_found_count"] = len(not_found)
        return self._fit_payload_to_limit(base)

    def _fit_payload_to_limit(self, payload: dict[str, Any]) -> str:
        """Уместить JSON-пейлоад в лимит, сохранив валидный JSON."""
        compact = dict(payload)
        encoded = json.dumps(compact, ensure_ascii=False)
        if len(encoded) <= self._max_tool_result_chars:
            return encoded

        def _trim_list(key: str, keep: int) -> None:
            value = compact.get(key)
            if isinstance(value, list):
                compact[key] = value[:keep]

        for key in ("items", "found", "ingredients", "not_found"):
            _trim_list(key, 1)
            encoded = json.dumps(compact, ensure_ascii=False)
            if len(encoded) <= self._max_tool_result_chars:
                return encoded

        for key in ("relevance_warning", "message"):
            value = compact.get(key)
            if isinstance(value, str) and len(value) > 160:
                compact[key] = value[:160]
            encoded = json.dumps(compact, ensure_ascii=False)
            if len(encoded) <= self._max_tool_result_chars:
                return encoded

        tiny = {
            "ok": payload.get("ok"),
            "error": payload.get("error"),
            "message": "tool_result_truncated",
        }
        return json.dumps(tiny, ensure_ascii=False)

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
        if tool_name == "vkusvill_products_search":
            return self._compact_products_search(payload)
        if tool_name == "vkusvill_product_details":
            return self._compact_product_details(payload)
        if tool_name == "recipe_ingredients":
            return self._compact_recipe_ingredients(payload)
        if tool_name == "recipe_search":
            return self._compact_recipe_search(payload)
        if tool_name == "vkusvill_cart_link_create":
            return self._compact_cart_link(payload)
        return self._compact_generic(payload)

    def _compact_products_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": payload.get("ok")}
        data = payload.get("data")
        if not isinstance(data, dict):
            # Идемпотентность компактизации: поддержать уже-compact shape
            # {"ok":true,"meta":...,"items":[...]}.
            has_compact_shape = any(
                key in payload for key in ("meta", "items", "relevance_warning")
            )
            if not has_compact_shape:
                return result
            data = payload

        query_text = ""
        meta = data.get("meta", {})
        if isinstance(meta, dict):
            compact_meta: dict[str, Any] = {}
            for key in ("q", "total", "has_more"):
                if key in meta:
                    compact_meta[key] = meta.get(key)
            if compact_meta:
                result["meta"] = compact_meta
                query_text = str(compact_meta.get("q", "")).strip()

        items = data.get("items", [])
        if isinstance(items, list):
            scored_items: list[dict[str, Any]] = []
            query_terms = self._tokenize_query_terms(query_text)
            for item in items[:10]:
                if not isinstance(item, dict):
                    continue
                xml_id_raw = item.get("xml_id")
                if isinstance(xml_id_raw, bool):
                    continue
                xml_id: int | None = None
                with contextlib.suppress(TypeError, ValueError):
                    xml_id = int(xml_id_raw)
                if not isinstance(xml_id, int):
                    continue

                name = self._normalize_compact_text(item.get("name"))
                if not name:
                    continue
                rating = item.get("rating")
                rating_avg = rating.get("average") if isinstance(rating, dict) else rating
                if not isinstance(rating_avg, int | float) or isinstance(rating_avg, bool):
                    rating_avg = None
                price = item.get("price")
                if isinstance(price, dict):
                    price = price.get("current")
                price_value = self._safe_float(price, default=-1.0)
                unit = str(item.get("unit", "")).strip()
                score, confidence = self._score_search_candidate(
                    query_terms=query_terms,
                    product_name=name,
                    rating=rating_avg,
                )
                scored_items.append(
                    {
                        "xml_id": xml_id,
                        "name": name,
                        "price": price_value if price_value >= 0 else None,
                        "unit": unit or None,
                        "rating": rating_avg,
                        "confidence": confidence,
                        "_score": score,
                    }
                )

            scored_items.sort(key=lambda row: row.get("_score", 0.0), reverse=True)
            top_items = []
            for row in scored_items[:3]:
                top_items.append({k: v for k, v in row.items() if k != "_score" and v is not None})
            result["items"] = top_items

        relevance_warning = data.get("relevance_warning")
        if relevance_warning:
            result["relevance_warning"] = relevance_warning
        return result

    @classmethod
    def _compact_product_details(cls, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": payload.get("ok")}
        data = payload.get("data")
        if isinstance(data, dict):
            price = data.get("price")
            if isinstance(price, dict):
                price = price.get("current")
            rating = data.get("rating")
            rating_value = rating.get("average") if isinstance(rating, dict) else rating
            weight = data.get("weight")
            compact_weight: dict[str, Any] | None = None
            if isinstance(weight, dict):
                compact_weight = {}
                if "value" in weight:
                    compact_weight["value"] = weight.get("value")
                if "unit" in weight:
                    compact_weight["unit"] = weight.get("unit")
                if not compact_weight:
                    compact_weight = None

            compact_data: dict[str, Any] = {
                "xml_id": data.get("xml_id", data.get("id")),
                "name": cls._normalize_compact_text(data.get("name")),
                "brand": cls._normalize_compact_text(data.get("brand")),
                "price": price,
                "unit": cls._normalize_compact_text(data.get("unit")),
                "weight": compact_weight,
                "rating": rating_value,
            }
            result["data"] = {
                key: value
                for key, value in compact_data.items()
                if value is not None and value != ""
            }

        if "error" in payload:
            result["error"] = payload.get("error")
        if "message" in payload:
            result["message"] = payload.get("message")
        return result

    def _compact_recipe_ingredients(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": payload.get("ok")}
        data = payload.get("data")
        if not isinstance(data, dict):
            # Идемпотентность компактизации: поддержать уже-compact shape.
            has_compact_shape = any(key in payload for key in ("dish", "servings", "ingredients"))
            if not has_compact_shape:
                return result
            data = payload

        result["dish"] = data.get("dish", payload.get("dish"))
        result["servings"] = data.get("servings", payload.get("servings"))
        ingredients = data.get("ingredients")
        if not isinstance(ingredients, list):
            ingredients = payload.get("ingredients", [])
        if isinstance(ingredients, list):
            compact_ingredients: list[dict[str, Any]] = []
            for row in ingredients[:30]:
                if not isinstance(row, dict):
                    continue

                compact_row: dict[str, Any] = {
                    "name": row.get("name"),
                    "quantity": row.get("quantity"),
                    "unit": row.get("unit"),
                }
                if row.get("optional") is True:
                    compact_row["optional"] = True
                for field in ("search_query", "kg_equivalent", "l_equivalent", "pack_equivalent"):
                    value = row.get(field)
                    if value is not None and value != "":
                        compact_row[field] = value
                compact_ingredients.append(compact_row)

            result["ingredients"] = compact_ingredients
        return result

    def _compact_recipe_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": payload.get("ok")}
        data = payload.get("data")
        compact_found: list[dict[str, Any]] = []
        not_found: list[Any] = []

        if isinstance(data, dict):
            found = data.get("found", [])
            if isinstance(found, list):
                for row in found[:40]:
                    if not isinstance(row, dict):
                        continue
                    item = row.get("item")
                    compact_found.append(
                        {
                            "ingredient": self._normalize_compact_text(row.get("ingredient")),
                            "suggested_q": row.get("suggested_q"),
                            "xml_id": item.get("xml_id") if isinstance(item, dict) else None,
                            "name": (
                                self._normalize_compact_text(item.get("name"))
                                if isinstance(item, dict)
                                else None
                            ),
                            "price": (
                                self._extract_price_value(item.get("price"))
                                if isinstance(item, dict)
                                else None
                            ),
                        }
                    )
            raw_not_found = data.get("not_found", [])
            if isinstance(raw_not_found, list):
                not_found = raw_not_found

        # Идемпотентность компактизации: поддержать уже-compact shape
        # {"ok":true,"found":[...],"not_found":[...]}.
        if not compact_found:
            raw_found = payload.get("found", [])
            if isinstance(raw_found, list):
                for row in raw_found[:40]:
                    if not isinstance(row, dict):
                        continue
                    compact_found.append(
                        {
                            "ingredient": self._normalize_compact_text(row.get("ingredient")),
                            "suggested_q": row.get("suggested_q"),
                            "xml_id": row.get("xml_id"),
                            "name": self._normalize_compact_text(row.get("name")),
                            "price": self._extract_price_value(row.get("price")),
                        }
                    )
            if not not_found:
                raw_not_found = payload.get("not_found", [])
                if isinstance(raw_not_found, list):
                    not_found = raw_not_found

        # Совместимость с fallback-форматом: top-level results/best_match.
        if not compact_found:
            results = payload.get("results", [])
            if isinstance(results, list):
                for row in results[:40]:
                    if not isinstance(row, dict):
                        continue
                    best_match = row.get("best_match")
                    if not isinstance(best_match, dict):
                        continue
                    compact_found.append(
                        {
                            "ingredient": self._normalize_compact_text(row.get("ingredient")),
                            "suggested_q": best_match.get("suggested_q"),
                            "xml_id": best_match.get("xml_id"),
                            "name": self._normalize_compact_text(best_match.get("name")),
                            "price": self._extract_price_value(best_match.get("price")),
                        }
                    )
            if not not_found:
                raw_not_found = payload.get("not_found", [])
                if isinstance(raw_not_found, list):
                    not_found = raw_not_found

        result["found"] = compact_found
        if isinstance(not_found, list):
            result["not_found"] = not_found[:40]
        return result

    @staticmethod
    def _normalize_compact_text(value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""
        text = html.unescape(text)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _tokenize_query_terms(cls, query: str) -> list[str]:
        normalized = cls._normalize_compact_text(query).lower().replace("ё", "е")
        tokens = re.findall(r"[a-zа-я0-9]+", normalized, flags=re.IGNORECASE)
        return [token for token in tokens if len(token) >= 2][:6]

    @classmethod
    def _score_search_candidate(
        cls,
        *,
        query_terms: list[str],
        product_name: str,
        rating: float | None,
    ) -> tuple[float, float]:
        normalized_name = cls._normalize_compact_text(product_name).lower().replace("ё", "е")
        if not query_terms:
            rating_bonus = (rating or 0.0) / 10 if rating is not None else 0.0
            return rating_bonus, 0.5

        matched = sum(1 for token in query_terms if token in normalized_name)
        coverage = matched / max(1, len(query_terms))
        prefix_bonus = 0.2 if normalized_name.startswith(query_terms[0]) else 0.0
        rating_bonus = (rating or 0.0) / 10 if rating is not None else 0.0
        score = coverage * 2.5 + prefix_bonus + rating_bonus
        confidence = min(0.99, max(0.0, 0.3 + coverage * 0.7))
        return score, round(confidence, 2)

    def _compact_cart_link(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": payload.get("ok")}
        data = payload.get("data")
        if isinstance(data, dict):
            result["link"] = data.get("link")
            price_summary = data.get("price_summary")
            if isinstance(price_summary, dict):
                result["price_summary"] = price_summary
        if "error" in payload:
            result["error"] = payload.get("error")
        if "message" in payload:
            result["message"] = payload.get("message")
        return result

    @staticmethod
    def _compact_generic(payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ("ok", "error", "message", "data"):
            if key in payload:
                result[key] = payload[key]
        return result or payload

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

        lines: list[str] = []
        total = 0.0
        all_priced = True
        original_total_text = ""
        original_total_value = -1.0
        if isinstance(summary, dict):
            total_text_raw = summary.get("total_text")
            if isinstance(total_text_raw, str) and total_text_raw.strip():
                original_total_text = total_text_raw.strip()
            original_total_value = self._safe_float(summary.get("total"), default=-1.0)

        for item in products:
            if not isinstance(item, dict):
                continue
            xml_id = item.get("xml_id")
            xml_id_int: int | None = None
            with contextlib.suppress(TypeError, ValueError):
                xml_id_int = int(xml_id)
            quantity = self._safe_float(item.get("q"), default=1.0)
            if quantity <= 0:
                quantity = 1.0

            normalized = product_index.get(xml_id_int) if isinstance(xml_id_int, int) else None
            if normalized is None:
                normalized = self._normalize_product_row(item)
            raw_name = (
                str(normalized.get("name", f"Товар {xml_id}")).strip()
                if normalized
                else f"Товар {xml_id}"
            )
            name = self._normalize_compact_text(raw_name) or f"Товар {xml_id}"
            price = (
                self._safe_float(normalized.get("price"), default=-1.0)
                if isinstance(normalized, dict)
                else -1.0
            )
            if float(quantity).is_integer():
                quantity_text = str(int(quantity))
            else:
                quantity_text = f"{quantity:.3f}".rstrip("0").rstrip(".")

            if price >= 0:
                subtotal = price * quantity
                total += subtotal
                lines.append(f"- {name} x {quantity_text} = {subtotal:.2f} руб")
            else:
                all_priced = False
                lines.append(f"- {name} x {quantity_text} = цена уточняется")

        if not lines:
            return

        synthesized: dict[str, Any] = {
            "items": lines,
            "count": len(lines),
        }
        if all_priced:
            synthesized["total"] = round(total, 2)
            synthesized["total_text"] = f"Итого: {total:.2f} руб"
        elif original_total_text:
            synthesized["total_text"] = original_total_text
        elif original_total_value >= 0:
            synthesized["total"] = round(original_total_value, 2)
            synthesized["total_text"] = f"Итого: {original_total_value:.2f} руб"
        else:
            synthesized["total_text"] = "Итого: будет рассчитано при открытии корзины"

        cart_data["price_summary"] = synthesized

    @staticmethod
    def _is_cart_intent(user_text: str) -> bool:
        normalized = user_text.lower()
        markers = (
            "собери",
            "корзин",
            "закаж",
            "добав",
            "купить",
            "заказ",
            "ингредиент",
            "ингридиент",
            "рецепт",
            "приготов",
            "сдела",
            "свари",
            "испеч",
            "убер",
            "удал",
            "замен",
            "измени",
            "поменя",
            "объедин",
        )
        return any(marker in normalized for marker in markers)

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

    @classmethod
    def _render_stable_cart_output(
        cls,
        cart_data: dict[str, Any],
        *,
        safety_note: str = "",
    ) -> str:
        link = str(cart_data.get("link", "")).strip()
        summary = cart_data.get("price_summary")
        summary_dict = summary if isinstance(summary, dict) else {}
        items = summary_dict.get("items")

        lines: list[str] = []
        if isinstance(items, list):
            for index, row in enumerate(items[:20], start=1):
                if not isinstance(row, str):
                    continue
                normalized = ShoppingAgent._normalize_compact_text(row)
                if not normalized:
                    continue
                if normalized.startswith("-"):
                    normalized = normalized.lstrip("-").strip()
                lines.append(f"{index}. {normalized}")

        total_text = ""
        total_text_raw = summary_dict.get("total_text")
        if isinstance(total_text_raw, str) and total_text_raw.strip():
            total_text = ShoppingAgent._normalize_compact_text(total_text_raw)
        else:
            total_raw = summary_dict.get("total")
            total = ShoppingAgent._safe_float(total_raw, default=-1.0)
            if total >= 0:
                total_text = f"Итого: {total:.2f} руб"
        purchase_total_text = ShoppingAgent._normalize_compact_text(
            summary_dict.get("purchase_total_text")
        )
        recipe_total_text = ShoppingAgent._normalize_compact_text(
            summary_dict.get("recipe_total_text")
        )
        overbuy_total_text = ShoppingAgent._normalize_compact_text(
            summary_dict.get("overbuy_total_text")
        )
        dual_pricing = bool(summary_dict.get("dual_pricing"))
        purchase_total = ShoppingAgent._safe_float(summary_dict.get("purchase_total"), default=-1.0)
        recipe_total = ShoppingAgent._safe_float(summary_dict.get("recipe_total"), default=-1.0)
        has_meaningful_dual = dual_pricing or (
            purchase_total >= 0 and recipe_total >= 0 and abs(purchase_total - recipe_total) >= 0.01
        )

        chunks = ["Собрала корзину по вашему запросу."]
        if lines:
            chunks.append("\n".join(lines))
        if total_text:
            chunks.append(f"<b>{total_text}</b>")
        if has_meaningful_dual:
            if recipe_total_text:
                chunks.append(f"<b>{recipe_total_text}</b>")
            if purchase_total_text:
                chunks.append(f"<b>{purchase_total_text}</b>")
            if overbuy_total_text:
                chunks.append(overbuy_total_text)
        cleaned_safety_note = cls._normalize_compact_text(safety_note)
        if cleaned_safety_note:
            chunks.append(cleaned_safety_note)
        if link:
            chunks.append(f'<a href="{link}">Открыть корзину</a>')
        chunks.append(
            "<i>Наличие и точное количество товаров будет проверено при открытии ссылки на "
            "корзину. Цены и состав уточняйте на сайте.</i>"
        )
        return "\n\n".join(chunks)

    @classmethod
    def _extract_cart_safety_note(cls, text: str) -> str:
        normalized_text = cls._normalize_compact_text(text)
        if not normalized_text:
            return ""

        candidates = re.split(r"(?<=[.!?])\s+|\n+", normalized_text)
        if not candidates:
            candidates = [normalized_text]

        keywords = (
            "аллерг",
            "состав",
            "неперенос",
            "индивидуал",
            "чувствител",
            "противопоказ",
        )
        for candidate in candidates:
            line = cls._normalize_compact_text(candidate).strip(" -\t")
            if not line:
                continue
            line_low = line.lower()
            if "http://" in line_low or "https://" in line_low:
                continue
            if "открыть корзину" in line_low:
                continue
            if any(token in line_low for token in keywords):
                return line
        return ""

    def _stabilize_cart_output(self, *, final_text: str, cart_data: dict[str, Any]) -> str:
        items_count = 0
        summary_dict: dict[str, Any] = {}
        summary = cart_data.get("price_summary")
        if isinstance(summary, dict):
            summary_dict = summary
            count_raw = summary.get("count")
            if isinstance(count_raw, int) and count_raw > 0:
                items_count = count_raw
            elif isinstance(count_raw, float) and count_raw.is_integer() and count_raw > 0:
                items_count = int(count_raw)
            else:
                items = summary.get("items")
                if isinstance(items, list):
                    items_count = len(items)

        if self._looks_like_manual_cart_reply(final_text):
            return self._render_stable_cart_output(cart_data)
        if self._looks_like_wrong_cart_summary(final_text, items_count=items_count):
            return self._render_stable_cart_output(cart_data)
        if self._looks_like_wrong_cart_link(final_text, cart_data=cart_data):
            return self._render_stable_cart_output(cart_data)
        if self._looks_like_missing_cart_prices(final_text, summary=summary_dict):
            return self._render_stable_cart_output(cart_data)
        return final_text

    @staticmethod
    def _extract_first_url(text: str) -> str:
        match = re.search(r"https?://[^\s\"<>]+", text)
        if not match:
            return ""
        return match.group(0).strip().rstrip("\\).,;:!?]")

    @classmethod
    def _looks_like_wrong_cart_link(cls, text: str, *, cart_data: dict[str, Any]) -> bool:
        expected_link = str(cart_data.get("link", "")).strip()
        if not expected_link:
            return False
        actual_link = cls._extract_first_url(text)
        if not actual_link:
            return True
        return actual_link != expected_link

    @staticmethod
    def _looks_like_missing_cart_prices(text: str, *, summary: dict[str, Any]) -> bool:
        """Определить, что финальный ответ не содержит цен по позициям корзины."""
        items = summary.get("items")
        if not isinstance(items, list) or not any(isinstance(row, str) for row in items):
            return False

        normalized = text.strip()
        if not normalized:
            return True

        # Ищем строки формата:
        # 1. Товар ... x 2 = 198 ₽
        # 2. Товар ... × 2 шт = 198 руб
        priced_row = re.compile(
            r"(?im)^\s*\d+\.\s+.+?(?:x|×).+?=\s*[\d\s.,]+(?:₽|руб(?:\.|ля|лей)?)"
        )
        has_priced_rows = bool(priced_row.search(normalized))

        # Минимальная проверка: если есть total_text в summary, он тоже должен быть в ответе.
        total_text_raw = summary.get("total_text")
        has_total_text = isinstance(total_text_raw, str) and bool(total_text_raw.strip())
        total_text = total_text_raw.strip().lower() if has_total_text else ""
        has_total_in_text = total_text in normalized.lower() if total_text else True

        return not (has_priced_rows and has_total_in_text)
