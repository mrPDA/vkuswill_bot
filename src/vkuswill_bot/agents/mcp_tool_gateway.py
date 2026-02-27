"""MCP tool gateway: retries, in-turn cache, graceful fallbacks."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from vkuswill_bot.agents.mcp_helpers import (
    is_successful_tool_result,
    make_mcp_call_cache_key,
)
from vkuswill_bot.agents.recipe_fallback import (
    fallback_recipe_ingredients,
    fallback_recipe_search,
)
from vkuswill_bot.services.llm_adapters import LLMAdapterProtocol

if TYPE_CHECKING:
    from vkuswill_bot.services.mcp_client import VkusvillMCPClient

logger = logging.getLogger(__name__)

_MCP_TOOL_NOT_FOUND = "method not found"
_MCP_CACHEABLE_TOOLS = frozenset(
    {
        "vkusvill_products_search",
        "vkusvill_product_details",
        "recipe_ingredients",
        "recipe_search",
    }
)


class McpToolGateway:
    """Executes MCP calls with retries/caching and local recipe fallbacks."""

    def __init__(
        self,
        *,
        mcp_client: VkusvillMCPClient,
        mcp_timeout_seconds: float,
        mcp_retries: int,
        llm_timeout_seconds: float,
        llm_adapters: dict[str, LLMAdapterProtocol],
        resolve_model_for_provider: Callable[[str], str],
        get_mcp_tool_names: Callable[[], set[str]],
    ) -> None:
        self._mcp_client = mcp_client
        self._mcp_timeout_seconds = mcp_timeout_seconds
        self._mcp_retries = mcp_retries
        self._llm_timeout_seconds = llm_timeout_seconds
        self._llm_adapters = llm_adapters
        self._resolve_model_for_provider = resolve_model_for_provider
        self._get_mcp_tool_names = get_mcp_tool_names

    async def call_tool(
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

        mcp_tool_names = self._get_mcp_tool_names()
        if name == "recipe_ingredients" and name not in mcp_tool_names:
            fallback = await self._fallback_recipe_ingredients(arguments, llm_provider)
            self._put_cache_if_successful(
                call_cache=call_cache,
                cache_key=cache_key,
                result=fallback,
            )
            return fallback

        if name == "recipe_search" and name not in mcp_tool_names:
            fallback = await self._fallback_recipe_search(arguments)
            self._put_cache_if_successful(
                call_cache=call_cache,
                cache_key=cache_key,
                result=fallback,
            )
            return fallback

        last_error: Exception | None = None
        for attempt in range(self._mcp_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._mcp_client.call_tool(name, arguments),
                    timeout=self._mcp_timeout_seconds,
                )
                self._put_cache_if_successful(
                    call_cache=call_cache,
                    cache_key=cache_key,
                    result=result,
                )
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
                    self._put_cache_if_successful(
                        call_cache=call_cache,
                        cache_key=cache_key,
                        result=fallback,
                    )
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

    def _put_cache_if_successful(
        self,
        *,
        call_cache: dict[str, str] | None,
        cache_key: str | None,
        result: str,
    ) -> None:
        if (
            call_cache is None
            or cache_key is None
            or not isinstance(result, str)
            or not is_successful_tool_result(result)
        ):
            return
        call_cache[cache_key] = result

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
