"""Deterministic fast path for explicit cart-like product lists."""

from __future__ import annotations

import copy
import contextlib
from typing import Any

from vkuswill_bot.agents.cart_output_renderer import render_stable_cart_output
from vkuswill_bot.agents.llm_helpers import assistant_msg
from vkuswill_bot.agents.meal_plan_runtime_ops import (
    extract_products_from_recipe_search,
    merge_products,
)
from vkuswill_bot.agents.mcp_response_parser import extract_cart_data
from vkuswill_bot.agents.product_index_manager import update_product_index_from_tool_result
from vkuswill_bot.services.cart_processor import CartProcessor

_QUESTION_RECIPE_MARKERS = (
    "что можно приготовить",
    "что приготовить",
    "как приготовить",
    "рецепт",
    "ингредиент",
)


def _reject_explicit_query_match(*, query: str, product_name: str) -> bool:
    normalized_query = str(query).lower().replace("ё", "е")
    normalized_name = str(product_name).lower().replace("ё", "е")
    if "трюфел" in normalized_query and any(
        marker in normalized_name for marker in ("конфет", "шоколад")
    ):
        return True
    return "фуа" in normalized_query and "гра" in normalized_query and "паштет" in normalized_name


def should_use_explicit_cart_fast_path(*, state: Any, text: str) -> bool:
    if state.prompt_profile != "cart":
        return False
    if state.previous_cart_products:
        return False
    if len(state.direct_cart_requests) < 2:
        return False
    normalized = text.lower().replace("ё", "е")
    if any(marker in normalized for marker in _QUESTION_RECIPE_MARKERS):
        return False
    has_explicit_separator = any(separator in text for separator in (":", ",", ";", "\n"))
    return has_explicit_separator or len(state.direct_cart_requests) >= 4


async def try_explicit_cart_fast_path(
    *,
    agent: Any,
    state: Any,
    user_id: int,
    text: str,
    llm_provider: str,
    trace: Any | None,
) -> str | None:
    """Build a cart directly from an explicit product list without the LLM loop."""
    if not should_use_explicit_cart_fast_path(state=state, text=text):
        return None

    search_args = {
        "ingredients": [
            {
                "name": str(row.get("name", "")).strip(),
                "quantity": row.get("quantity", 1.0),
                "unit": str(row.get("unit", "шт")).strip() or "шт",
                "search_query": str(row.get("search_query", "")).strip()
                or str(row.get("name", "")).strip(),
            }
            for row in state.direct_cart_requests
            if isinstance(row, dict) and str(row.get("name", "")).strip()
        ]
    }
    if len(search_args["ingredients"]) < 2:
        return None

    try:
        recipe_search_result = await agent._call_mcp_tool(
            name="recipe_search",
            arguments=search_args,
            llm_provider=llm_provider,
            call_cache=state.mcp_call_cache,
            user_id=user_id,
        )
    except Exception:
        return None

    update_product_index_from_tool_result(
        product_index=state.product_index_this_turn,
        tool_name="recipe_search",
        tool_result=recipe_search_result,
    )

    products, not_found = extract_products_from_recipe_search(recipe_search_result)
    filtered_products: list[dict[str, Any]] = []
    filtered_not_found = list(not_found)
    for request_row, product_row in zip(search_args["ingredients"], products, strict=False):
        query = str(request_row.get("search_query", "")).strip()
        name = str(product_row.get("name", "")).strip()
        if name and _reject_explicit_query_match(query=query, product_name=name):
            filtered_not_found.append(query or str(request_row.get("name", "")).strip())
            continue
        filtered_products.append(product_row)

    merged_products = merge_products(filtered_products)
    compact_not_found: list[str] = []
    seen_not_found: set[str] = set()
    for raw in filtered_not_found:
        value = str(raw).strip()
        if not value or value in seen_not_found:
            continue
        seen_not_found.add(value)
        compact_not_found.append(value)

    if not merged_products:
        fallback_text = (
            f"Не нашла точных товаров в каталоге: {', '.join(compact_not_found)}."
            if compact_not_found
            else "Не нашла точных товаров в каталоге."
        )
        agent._history[user_id] = agent._trim_history(
            [*state.history, assistant_msg(fallback_text)]
        )
        if trace is not None:
            with contextlib.suppress(Exception):
                trace.update(
                    output=fallback_text,
                    metadata={
                        "explicit_cart_fast_path": True,
                        "explicit_cart_not_found": compact_not_found,
                    },
                )
        return fallback_text

    cart_args = CartProcessor.fix_cart_args(
        {"products": [{"xml_id": row["xml_id"], "q": row["q"]} for row in merged_products]}
    )
    try:
        cart_result = await agent._call_mcp_tool(
            name="vkusvill_cart_link_create",
            arguments=cart_args,
            llm_provider=llm_provider,
            call_cache=state.mcp_call_cache,
            user_id=user_id,
        )
    except Exception:
        return None

    cart_data = extract_cart_data(tool_name="vkusvill_cart_link_create", tool_result=cart_result)
    if cart_data is None:
        return None

    cart_data["products"] = cart_args.get("products", [])
    cart_data["requested_products"] = list(cart_args.get("products", []))
    if compact_not_found:
        cart_data["not_found"] = compact_not_found
    state.cart_data_this_turn = cart_data

    agent._capture_cart_snapshot(
        user_id=user_id,
        tool_name="vkusvill_cart_link_create",
        args=cart_args,
        result=cart_result,
    )
    agent._ensure_cart_price_summary(
        cart_data=cart_data,
        product_index=state.product_index_this_turn,
    )
    summary = cart_data.get("price_summary")
    if "total" not in cart_data and isinstance(summary, dict) and "total" in summary:
        cart_data["total"] = summary.get("total")
    if "items_count" not in cart_data and isinstance(summary, dict):
        items = summary.get("items")
        if isinstance(items, list):
            cart_data["items_count"] = len(items)
        elif isinstance(summary.get("count"), int):
            cart_data["items_count"] = summary.get("count")
    agent._last_cart_snapshot[user_id] = copy.deepcopy(cart_data)

    safety_note = f"Не нашлось: {', '.join(compact_not_found)}." if compact_not_found else ""
    final_text = render_stable_cart_output(cart_data, safety_note=safety_note)
    recipe_search_history = {
        "role": "tool",
        "name": "recipe_search",
        "content": agent._prepare_tool_result_for_history("recipe_search", recipe_search_result),
    }
    cart_history = {
        "role": "tool",
        "name": "vkusvill_cart_link_create",
        "content": agent._prepare_tool_result_for_history("vkusvill_cart_link_create", cart_result),
    }
    agent._history[user_id] = agent._trim_history(
        [*state.history, recipe_search_history, cart_history, assistant_msg(final_text)]
    )
    if trace is not None:
        with contextlib.suppress(Exception):
            trace.update(
                output=final_text,
                metadata={
                    "explicit_cart_fast_path": True,
                    "explicit_cart_not_found": compact_not_found,
                    "explicit_cart_items_count": len(merged_products),
                },
            )
    return final_text
