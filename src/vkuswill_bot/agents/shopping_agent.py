"""ShoppingAgent: OpenAI-compatible chat engine поверх MCP."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import math
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from vkuswill_bot.services.llm_adapters import (
    LLMAdapterProtocol,
    OpenAICompatibleLLMAdapter,
    create_llm_adapter,
    normalize_llm_provider,
)
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.prompts import RECIPE_EXTRACTION_PROMPT, get_system_prompt

if TYPE_CHECKING:
    from vkuswill_bot.services.chat_engine import ProgressCallback
    from vkuswill_bot.services.dialog_manager import DialogManager
    from vkuswill_bot.services.langfuse_tracing import LangfuseService
    from vkuswill_bot.services.mcp_client import VkusvillMCPClient
    from vkuswill_bot.services.redis_dialog_manager import RedisDialogManager

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOOL_CALLS = 20
_DEFAULT_MAX_HISTORY = 50
_DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
_DEFAULT_LLM_RETRIES = 2
_DEFAULT_MCP_TIMEOUT_SECONDS = 15.0
_DEFAULT_MCP_RETRIES = 2
_DEFAULT_LLM_ROUTING_STRATEGY = "single_provider"
_DEFAULT_MAX_TOOL_RESULT_CHARS = 1800
_DEFAULT_MAX_HISTORY_CHARS = 60000
_DEFAULT_MAX_INPUT_CHARS_PER_TURN = 250000
_ERROR_GENERIC = "Не удалось обработать запрос. Попробуйте позже."
_ERROR_TOO_MANY_TOOLS = (
    "Не удалось завершить подбор в пределах лимита шагов. Уточните запрос и попробуйте ещё раз."
)
_MCP_TOOL_NOT_FOUND = "method not found"
_FORCE_CART_RECOVERY_HINT = (
    "[Системная корректировка] Пользователь ожидает готовую корзину. "
    "Запрещено предлагать ручную сборку или отправлять пользователя собирать корзину самому. "
    "Используй доступные инструменты и обязательно создай корзину через "
    "vkusvill_cart_link_create. После успешного создания дай финальный ответ."
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
        self._llm_retries = max(0, llm_retries)
        self._mcp_timeout_seconds = max(1.0, mcp_timeout_seconds)
        self._mcp_retries = max(0, mcp_retries)
        self._max_tool_result_chars = max(300, max_tool_result_chars)
        self._max_history_chars = max(10000, max_history_chars)
        self._max_input_chars_per_turn = max(10000, max_input_chars_per_turn)
        self._api_semaphore = asyncio.Semaphore(max(1, llm_max_concurrent))
        self._langfuse = langfuse_service
        self._tools_cache: list[dict[str, Any]] | None = None
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
        if history is None:
            history = [{"role": "system", "content": get_system_prompt()}]

        history.append({"role": "user", "content": text})
        history = self._trim_history(history)
        history = self._trim_history_by_chars(history)
        cart_data_this_turn: dict[str, Any] | None = None
        manual_recovery_used = False
        total_llm_input_chars = 0

        tools = await self._get_tools()
        trace = self._create_trace(user_id=user_id, text=text, llm_provider=llm_provider)

        async def _progress(message: str) -> None:
            if on_progress is None:
                return
            with contextlib.suppress(Exception):
                await on_progress(message)

        await _progress("\u2699\ufe0f Анализирую запрос...")

        for step in range(1, self._max_tool_calls + 1):
            llm_input = list(history)
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
                    },
                )

            try:
                response = await self._call_llm(
                    messages=history,
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
            if gen is not None:
                gen.end(output=message, usage_details=usage_details)

            tool_calls = self._extract_tool_calls(message)
            if not tool_calls:
                final_text = self._extract_text(message) or _ERROR_GENERIC
                cart_intent = self._is_cart_intent(text)
                manual_cart_reply = self._looks_like_manual_cart_reply(final_text)

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

                if cart_data_this_turn is not None:
                    final_text = self._stabilize_cart_output(
                        final_text=final_text,
                        cart_data=cart_data_this_turn,
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
            for tool_call in tool_calls:
                tool_name = str(tool_call.get("name", "")).strip()
                tool_call_id = str(tool_call.get("id", "")).strip()
                tool_args = self._preprocess_tool_args(
                    tool_name,
                    self._parse_tool_args(tool_call.get("arguments")),
                )

                await _progress(self._tool_progress_text(tool_name))
                tool_span = trace.span(name=f"tool:{tool_name}", input=tool_args) if trace else None

                tool_result = await self._call_mcp_tool(
                    name=tool_name,
                    arguments=tool_args,
                    llm_provider=llm_provider,
                )
                cart_data = self._extract_cart_data(tool_name=tool_name, tool_result=tool_result)
                if cart_data is not None:
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

        self._history[user_id] = history
        if trace is not None:
            trace.update(
                output=_ERROR_TOO_MANY_TOOLS,
                metadata={"reason": "max_tool_calls", "provider": llm_provider},
            )
        return _ERROR_TOO_MANY_TOOLS

    def _create_trace(self, *, user_id: int, text: str, llm_provider: str) -> Any | None:
        if self._langfuse is None:
            return None
        with contextlib.suppress(Exception):
            return self._langfuse.trace(
                name="chat",
                user_id=str(user_id),
                session_id=str(user_id),
                input=text,
                tags=["shopping_agent", "telegram_or_voice", llm_provider],
            )
        return None

    async def _get_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is not None:
            return self._tools_cache

        raw_tools = await self._mcp_client.get_tools()
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
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(self._mcp_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._mcp_client.call_tool(name, arguments),
                    timeout=self._mcp_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                fallback = await self._fallback_missing_mcp_tool(
                    tool_name=name,
                    arguments=arguments,
                    llm_provider=llm_provider,
                    error=exc,
                )
                if fallback is not None:
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
            items = data.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        products = payload.get("products")
        if isinstance(products, list):
            return [item for item in products if isinstance(item, dict)]
        return []

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
    def _preprocess_tool_args(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "vkusvill_cart_link_create":
            # MCP cart-link expects explicit quantities for each item.
            return CartProcessor.fix_cart_args(tool_args)
        return tool_args

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
        for message in history[1:]:
            if message.get("role") != "tool":
                compacted.append(message)
                continue

            name = str(message.get("name", "")).strip()
            content = message.get("content")
            if not name or not isinstance(content, str):
                compacted.append(message)
                continue

            compacted.append(
                {
                    **message,
                    "content": self._prepare_tool_result_for_history(name, content),
                }
            )
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
        usage = None
        if isinstance(response, dict):
            usage = response.get("usage")
        else:
            usage = getattr(response, "usage", None)

        if usage is None:
            return None

        if isinstance(usage, dict):
            prompt = usage.get("input", usage.get("prompt_tokens"))
            completion = usage.get("output", usage.get("completion_tokens"))
            total = usage.get("total", usage.get("total_tokens"))
        else:
            prompt = getattr(usage, "input", getattr(usage, "prompt_tokens", None))
            completion = getattr(usage, "output", getattr(usage, "completion_tokens", None))
            total = getattr(usage, "total", getattr(usage, "total_tokens", None))

        result: dict[str, int] = {}
        if isinstance(prompt, int):
            result["input"] = prompt
        if isinstance(completion, int):
            result["output"] = completion
        if isinstance(total, int):
            result["total"] = total
        return result or None

    def _compact_tool_result(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "vkusvill_products_search":
            return self._compact_products_search(payload)
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

        result["meta"] = data.get("meta", {})
        items = data.get("items", [])
        if isinstance(items, list):
            top_items = []
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                rating = item.get("rating")
                top_items.append(
                    {
                        "xml_id": item.get("xml_id"),
                        "name": item.get("name"),
                        "price": item.get("price"),
                        "unit": item.get("unit"),
                        "rating": rating.get("average") if isinstance(rating, dict) else None,
                    }
                )
            result["items"] = top_items

        relevance_warning = data.get("relevance_warning")
        if relevance_warning:
            result["relevance_warning"] = relevance_warning
        return result

    def _compact_recipe_ingredients(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": payload.get("ok")}
        data = payload.get("data")
        if not isinstance(data, dict):
            return result

        result["dish"] = data.get("dish")
        result["servings"] = data.get("servings")
        ingredients = data.get("ingredients", [])
        if isinstance(ingredients, list):
            result["ingredients"] = [
                {
                    "name": row.get("name"),
                    "quantity": row.get("quantity"),
                    "unit": row.get("unit"),
                    "optional": row.get("optional"),
                }
                for row in ingredients[:30]
                if isinstance(row, dict)
            ]
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
                            "ingredient": row.get("ingredient"),
                            "suggested_q": row.get("suggested_q"),
                            "xml_id": item.get("xml_id") if isinstance(item, dict) else None,
                            "name": item.get("name") if isinstance(item, dict) else None,
                            "price": item.get("price") if isinstance(item, dict) else None,
                        }
                    )
            raw_not_found = data.get("not_found", [])
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
                            "ingredient": row.get("ingredient"),
                            "suggested_q": best_match.get("suggested_q"),
                            "xml_id": best_match.get("xml_id"),
                            "name": best_match.get("name"),
                            "price": best_match.get("price"),
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
            "рецепт",
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

    @staticmethod
    def _render_stable_cart_output(cart_data: dict[str, Any]) -> str:
        link = str(cart_data.get("link", "")).strip()
        summary = cart_data.get("price_summary")
        summary_dict = summary if isinstance(summary, dict) else {}
        items = summary_dict.get("items")

        lines: list[str] = []
        if isinstance(items, list):
            for index, row in enumerate(items[:20], start=1):
                if not isinstance(row, str):
                    continue
                normalized = row.strip()
                if not normalized:
                    continue
                if normalized.startswith("-"):
                    normalized = normalized.lstrip("-").strip()
                lines.append(f"{index}. {normalized}")

        total_text = ""
        total_text_raw = summary_dict.get("total_text")
        if isinstance(total_text_raw, str) and total_text_raw.strip():
            total_text = total_text_raw.strip()
        else:
            total_raw = summary_dict.get("total")
            total = ShoppingAgent._safe_float(total_raw, default=-1.0)
            if total >= 0:
                total_text = f"Итого: {total:.2f} руб"

        chunks = ["Собрала корзину по вашему запросу."]
        if lines:
            chunks.append("\n".join(lines))
        if total_text:
            chunks.append(f"<b>{total_text}</b>")
        if link:
            chunks.append(f'<a href="{link}">Открыть корзину</a>')
        chunks.append(
            "<i>Наличие и точное количество товаров будет проверено при открытии ссылки на "
            "корзину. Цены и состав уточняйте на сайте.</i>"
        )
        return "\n\n".join(chunks)

    def _stabilize_cart_output(self, *, final_text: str, cart_data: dict[str, Any]) -> str:
        items_count = 0
        summary = cart_data.get("price_summary")
        if isinstance(summary, dict):
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
        return final_text
