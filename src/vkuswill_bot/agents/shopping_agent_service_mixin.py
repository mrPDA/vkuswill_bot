"""Service/helper mixin for ShoppingAgent."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from vkuswill_bot.agents.history_manager import trim_history, trim_history_by_chars
from vkuswill_bot.agents.mcp_helpers import (
    PREFERENCE_TOOL_NAMES,
    handle_local_preference_tool,
    with_virtual_preference_tools,
    with_virtual_recipe_tools,
)
from vkuswill_bot.agents.mcp_response_parser import (
    extract_cart_data,
    extract_recipe_products_from_history,
)
from vkuswill_bot.agents.mcp_tool_gateway import McpToolGateway
from vkuswill_bot.agents.shopping_agent_state_ops import (
    capture_cart_snapshot,
    ensure_cart_summary,
    should_start_fresh,
)
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.llm_adapters import extract_usage_details
from vkuswill_bot.services.preferences_parser import parse_preferences
from vkuswill_bot.services.prompts import PromptProfile

if TYPE_CHECKING:
    from vkuswill_bot.services.llm_adapters import LLMAdapterProtocol

logger = logging.getLogger(__name__)


class ShoppingAgentServiceMixin:
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
        if self._preferences_store is not None:
            raw_tools = with_virtual_preference_tools(raw_tools)
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
        max_tokens_override: int | None = None,
    ) -> Any:
        from vkuswill_bot.agents.exceptions import LLMOverloadedError

        llm_adapter: LLMAdapterProtocol | None = self._llm_adapters.get(llm_provider)
        if llm_adapter is None:
            raise RuntimeError(f"LLM adapter not configured for provider: {llm_provider}")
        max_tokens = (
            max_tokens_override if max_tokens_override is not None else self._llm_max_tokens
        )

        try:
            await asyncio.wait_for(
                self._api_semaphore.acquire(),
                timeout=self._llm_queue_timeout_seconds,
            )
        except TimeoutError:
            raise LLMOverloadedError(
                f"LLM queue full: semaphore not acquired within {self._llm_queue_timeout_seconds}s"
            ) from None

        try:
            last_error: Exception | None = None
            for attempt in range(self._llm_retries + 1):
                try:
                    return await asyncio.wait_for(
                        llm_adapter.create_completion(
                            model=self._resolve_model_for_provider(llm_provider),
                            messages=messages,
                            tools=tools,
                            tool_choice="auto",
                            max_tokens=max_tokens,
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
        finally:
            self._api_semaphore.release()

    def _resolve_model_for_provider(self, llm_provider: str) -> str:
        if llm_provider != self._llm_provider:
            raise RuntimeError(
                f"Unsupported llm provider for ShoppingAgent runtime: {llm_provider}",
            )
        return self._model

    def _create_mcp_gateway(self) -> McpToolGateway:
        return McpToolGateway(
            mcp_client=self._mcp_client,
            mcp_timeout_seconds=self._mcp_timeout_seconds,
            mcp_retries=self._mcp_retries,
            llm_timeout_seconds=self._llm_timeout_seconds,
            llm_adapters=self._llm_adapters,
            resolve_model_for_provider=self._resolve_model_for_provider,
            get_mcp_tool_names=lambda: self._mcp_tool_names,
        )

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str:
        if name in PREFERENCE_TOOL_NAMES and self._preferences_store is not None:
            return await handle_local_preference_tool(
                name,
                arguments,
                store=self._preferences_store,
                user_id=user_id,
            )

        gateway = self._mcp_gateway
        if gateway is None:
            gateway = self._create_mcp_gateway()
            self._mcp_gateway = gateway
        return await gateway.call_tool(
            name=name,
            arguments=arguments,
            llm_provider=llm_provider,
            call_cache=call_cache,
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
        capture_cart_snapshot(
            last_cart_snapshot=self._last_cart_snapshot,
            user_id=user_id,
            tool_name=tool_name,
            args=args,
            result=result,
        )

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

    @staticmethod
    def _extract_usage_details(response: Any) -> dict[str, int] | None:
        """Извлечь usage-details из ответа LLM (OpenAI-compatible / normalized dict)."""
        return extract_usage_details(response)

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
        ensure_cart_summary(cart_data=cart_data, product_index=product_index)

    def _should_start_fresh_context(
        self,
        *,
        text: str,
        history: list[dict[str, Any]] | None,
    ) -> bool:
        return should_start_fresh(text=text, history=history)
