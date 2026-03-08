"""Runtime helpers for meal-plan recipe_search execution and diagnostics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast
from typing import Any, Protocol

from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.agents.meal_plan_runtime_ops import (
    extract_products_from_recipe_search,
    merge_products,
)
from vkuswill_bot.agents.meal_plan_runtime_policy import (
    RECIPE_SEARCH_TIMEOUT_SECONDS,
    call_with_timeout_retry,
)
from vkuswill_bot.agents.recipe_fallback import fallback_recipe_search

_PRIMARY_RECIPE_SEARCH_MAX_INGREDIENTS = 24
_RECIPE_SEARCH_CHUNK_SIZE = 5
_RECIPE_SEARCH_CHUNK_CONCURRENCY = 3
_LOCAL_PRODUCTS_SEARCH_LIMIT = 5
_LOCAL_PRODUCTS_SEARCH_TIMEOUT_SECONDS = 6.0


class MealPlanRecipeSearchAgentProtocol(Protocol):
    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str: ...


@dataclass(slots=True)
class RecipeSearchStats:
    aggregated_ingredients_count: int
    primary_attempted: bool = False
    primary_products_count: int = 0
    primary_not_found_count: int = 0
    final_products_count: int = 0
    final_not_found_count: int = 0
    used_chunk_fallback: bool = False
    chunk_count: int = 0
    chunk_products_count: int = 0
    chunk_not_found_count: int = 0
    fallback_reason: str = ""
    primary_error_type: str | None = None
    primary_error_message: str | None = None
    chunk_failure_count: int = 0
    chunk_sample_failures: list[dict[str, Any]] | None = None
    local_fallback_chunk_count: int = 0
    local_fallback_products_count: int = 0
    local_fallback_not_found_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "aggregated_ingredients_count": self.aggregated_ingredients_count,
            "primary_attempted": self.primary_attempted,
            "primary_products_count": self.primary_products_count,
            "primary_not_found_count": self.primary_not_found_count,
            "final_products_count": self.final_products_count,
            "final_not_found_count": self.final_not_found_count,
            "used_chunk_fallback": self.used_chunk_fallback,
            "chunk_count": self.chunk_count,
            "chunk_products_count": self.chunk_products_count,
            "chunk_not_found_count": self.chunk_not_found_count,
            "fallback_reason": self.fallback_reason,
            "primary_error_type": self.primary_error_type,
            "primary_error_message": self.primary_error_message,
            "chunk_failure_count": self.chunk_failure_count,
            "chunk_sample_failures": list(self.chunk_sample_failures or []),
            "local_fallback_chunk_count": self.local_fallback_chunk_count,
            "local_fallback_products_count": self.local_fallback_products_count,
            "local_fallback_not_found_count": self.local_fallback_not_found_count,
        }


def _should_use_chunk_fallback(
    *,
    aggregated_ingredients_count: int,
    products_count: int,
    not_found_count: int,
    primary_failed: bool = False,
) -> tuple[bool, str]:
    if primary_failed:
        return True, "primary_search_failed"
    if products_count <= 0:
        return True, "primary_search_empty"
    unresolved_ratio = not_found_count / max(1, aggregated_ingredients_count)
    if unresolved_ratio > 0.4:
        return True, "primary_search_high_not_found"
    if aggregated_ingredients_count <= 12:
        return False, ""
    coverage_ratio = products_count / max(1, aggregated_ingredients_count)
    if coverage_ratio < 0.5:
        return True, "primary_search_low_coverage"
    return False, ""


async def search_products(
    *,
    agent: MealPlanRecipeSearchAgentProtocol,
    state: Any,
    user_id: int,
    llm_provider: str,
    aggregated_ingredients: list[dict[str, Any]],
    phase2_deadline_at: float,
    prefer_local_only: bool = False,
) -> tuple[list[dict[str, Any]], list[str], bool, RecipeSearchStats]:
    def _raise_tool_error_if_any(raw: str) -> str:
        payload = parse_json_payload(raw)
        if not isinstance(payload, dict):
            return raw
        ok = payload.get("ok")
        error = payload.get("error")
        if ok is False or error:
            error_text = str(error or payload.get("message") or "tool_error").strip()
            message = str(payload.get("message") or error_text).strip()
            raise RuntimeError(f"{error_text}: {message}"[:240])
        return raw

    async def _call_products_search(query: str) -> str:
        raw = await call_with_timeout_retry(
            operation=lambda: agent._call_mcp_tool(
                name="vkusvill_products_search",
                arguments={"q": query, "limit": _LOCAL_PRODUCTS_SEARCH_LIMIT},
                llm_provider=llm_provider,
                call_cache=state.mcp_call_cache,
                user_id=user_id,
            ),
            timeout_seconds=min(
                RECIPE_SEARCH_TIMEOUT_SECONDS,
                _LOCAL_PRODUCTS_SEARCH_TIMEOUT_SECONDS,
            ),
            hard_deadline_at=phase2_deadline_at,
            retries=0,
        )
        return _raise_tool_error_if_any(cast(str, raw))

    async def _fallback_chunk_with_products_search(
        chunk: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        fallback_raw = await fallback_recipe_search(
            {"ingredients": chunk},
            search_fn=_call_products_search,
            max_concurrent=_RECIPE_SEARCH_CHUNK_CONCURRENCY,
        )
        return extract_products_from_recipe_search(fallback_raw)

    async def _call_recipe_search(ingredients: list[dict[str, Any]]) -> str:
        return await call_with_timeout_retry(
            operation=lambda: agent._call_mcp_tool(
                name="recipe_search",
                arguments={"ingredients": ingredients},
                llm_provider=llm_provider,
                call_cache=state.mcp_call_cache,
                user_id=user_id,
            ),
            timeout_seconds=RECIPE_SEARCH_TIMEOUT_SECONDS,
            hard_deadline_at=phase2_deadline_at,
        )

    used_chunk_fallback = False
    stats = RecipeSearchStats(aggregated_ingredients_count=len(aggregated_ingredients))
    prefer_local_chunk_fallback = False
    if prefer_local_only:
        products, not_found = [], []
        should_fallback, fallback_reason = True, "local_search_only"
        prefer_local_chunk_fallback = True
    elif len(aggregated_ingredients) > _PRIMARY_RECIPE_SEARCH_MAX_INGREDIENTS:
        products, not_found = [], []
        should_fallback, fallback_reason = True, "primary_search_skipped_large_batch"
        prefer_local_chunk_fallback = True
    else:
        stats.primary_attempted = True
        primary_failed = False
        try:
            primary = await _call_recipe_search(aggregated_ingredients)
            products, not_found = extract_products_from_recipe_search(primary)
        except Exception as exc:
            products, not_found = [], []
            primary_failed = True
            stats.primary_error_type = type(exc).__name__
            stats.primary_error_message = str(exc)[:240]
        should_fallback, fallback_reason = _should_use_chunk_fallback(
            aggregated_ingredients_count=len(aggregated_ingredients),
            products_count=len(products),
            not_found_count=len(not_found),
            primary_failed=primary_failed,
        )
    stats.primary_products_count = len(products)
    stats.primary_not_found_count = len(not_found)
    if not should_fallback:
        merged = merge_products(products)
        stats.final_products_count = len(merged)
        stats.final_not_found_count = len(not_found)
        return merged, not_found, used_chunk_fallback, stats

    used_chunk_fallback = True
    stats.used_chunk_fallback = True
    stats.fallback_reason = fallback_reason
    merged_products: list[dict[str, Any]] = []
    merged_not_found: list[str] = []
    semaphore = asyncio.Semaphore(_RECIPE_SEARCH_CHUNK_CONCURRENCY)
    stats.chunk_count = (
        len(aggregated_ingredients) + _RECIPE_SEARCH_CHUNK_SIZE - 1
    ) // _RECIPE_SEARCH_CHUNK_SIZE

    async def _run_chunk(
        chunk: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
        async with semaphore:
            if prefer_local_chunk_fallback:
                try:
                    (
                        fallback_products,
                        fallback_not_found,
                    ) = await _fallback_chunk_with_products_search(chunk)
                except Exception as fallback_exc:
                    return (
                        [],
                        [],
                        {
                            "error_type": type(fallback_exc).__name__,
                            "error_message": str(fallback_exc)[:240],
                            "chunk_size": len(chunk),
                            "local_fallback_error_type": type(fallback_exc).__name__,
                            "local_fallback_error_message": str(fallback_exc)[:240],
                        },
                    )
                return (
                    fallback_products,
                    fallback_not_found,
                    {
                        "fallback_used": "local_products_search",
                        "source_error_type": None,
                        "source_error_message": fallback_reason,
                        "chunk_size": len(chunk),
                        "products_count": len(fallback_products),
                        "not_found_count": len(fallback_not_found),
                    },
                )
            try:
                chunk_result = await _call_recipe_search(chunk)
            except Exception as exc:
                try:
                    (
                        fallback_products,
                        fallback_not_found,
                    ) = await _fallback_chunk_with_products_search(chunk)
                except Exception as fallback_exc:
                    return (
                        [],
                        [],
                        {
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:240],
                            "chunk_size": len(chunk),
                            "local_fallback_error_type": type(fallback_exc).__name__,
                            "local_fallback_error_message": str(fallback_exc)[:240],
                        },
                    )
                return (
                    fallback_products,
                    fallback_not_found,
                    {
                        "fallback_used": "local_products_search",
                        "source_error_type": type(exc).__name__,
                        "source_error_message": str(exc)[:240],
                        "chunk_size": len(chunk),
                        "products_count": len(fallback_products),
                        "not_found_count": len(fallback_not_found),
                    },
                )
            chunk_products, chunk_not_found = extract_products_from_recipe_search(chunk_result)
            return chunk_products, chunk_not_found, None

    chunks = [
        aggregated_ingredients[start : start + _RECIPE_SEARCH_CHUNK_SIZE]
        for start in range(0, len(aggregated_ingredients), _RECIPE_SEARCH_CHUNK_SIZE)
    ]
    chunk_results = await asyncio.gather(*[_run_chunk(chunk) for chunk in chunks])
    chunk_sample_failures: list[dict[str, Any]] = []
    for chunk_products, chunk_not_found, chunk_error in chunk_results:
        merged_products.extend(chunk_products)
        for item in chunk_not_found:
            if item not in merged_not_found:
                merged_not_found.append(item)
        if isinstance(chunk_error, dict):
            if chunk_error.get("fallback_used") == "local_products_search":
                stats.local_fallback_chunk_count += 1
                stats.local_fallback_products_count += len(chunk_products)
                stats.local_fallback_not_found_count += len(chunk_not_found)
            else:
                stats.chunk_failure_count += 1
            if len(chunk_sample_failures) < 5:
                chunk_sample_failures.append(chunk_error)
    stats.chunk_sample_failures = chunk_sample_failures
    merged_chunk_products = merge_products(merged_products)
    stats.chunk_products_count = len(merged_chunk_products)
    stats.chunk_not_found_count = len(merged_not_found)
    combined_products = merge_products([*products, *merged_products])
    combined_not_found: list[str] = []
    for item in [*not_found, *merged_not_found]:
        value = str(item).strip()
        if value and value not in combined_not_found:
            combined_not_found.append(value)
    stats.final_products_count = len(combined_products)
    stats.final_not_found_count = len(combined_not_found)
    return combined_products, combined_not_found, used_chunk_fallback, stats
