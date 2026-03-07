"""Recovery корзины из recipe_search / recipe_ingredients."""

from __future__ import annotations

import logging
from typing import Any

from vkuswill_bot.agents.mcp_response_parser import (
    extract_all_recipe_ingredients_from_history,
    extract_cart_data,
    extract_recipe_products_from_history,
)
from vkuswill_bot.services.cart_processor import CartProcessor

logger = logging.getLogger(__name__)

_EMPTY: tuple[None, dict[str, Any], str] = (None, {}, "")


def _finalize_cart_data(
    cart_data: dict[str, Any] | None,
    cart_args: dict[str, Any],
    cart_result: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    if cart_data is None:
        return None, cart_args, cart_result
    if "products" not in cart_data:
        cart_data["products"] = cart_args.get("products", [])
    if "requested_products" not in cart_data:
        cart_data["requested_products"] = cart_args.get("products", [])
    return cart_data, cart_args, cart_result


async def _create_cart_from_products(
    products: list[dict[str, Any]],
    call_mcp_tool: Any,
    llm_provider: str,
    call_cache: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    cart_args = CartProcessor.fix_cart_args({"products": products})
    try:
        cart_result = await call_mcp_tool(
            name="vkusvill_cart_link_create",
            arguments=cart_args,
            llm_provider=llm_provider,
            call_cache=call_cache,
        )
    except Exception as exc:
        logger.warning("Recovery cart_link_create failed: %s", exc)
        return None, cart_args, ""
    cart_data = extract_cart_data(
        tool_name="vkusvill_cart_link_create",
        tool_result=cart_result,
    )
    return _finalize_cart_data(cart_data, cart_args, cart_result)


async def recover_cart_from_recipe_search(
    *,
    history: list[dict[str, Any]],
    call_mcp_tool: Any,
    llm_provider: str,
    call_cache: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    """Собрать корзину из recipe_search результатов в history."""
    products, _ = extract_recipe_products_from_history(history)
    if not products:
        return _EMPTY
    return await _create_cart_from_products(products, call_mcp_tool, llm_provider, call_cache)


async def recover_cart_from_recipe_ingredients(
    *,
    history: list[dict[str, Any]],
    call_mcp_tool: Any,
    llm_provider: str,
    call_cache: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    """Собрать корзину: recipe_ingredients → recipe_search → cart_link_create."""
    ingredients = extract_all_recipe_ingredients_from_history(history)
    if not ingredients:
        return _EMPTY
    try:
        search_result = await call_mcp_tool(
            name="recipe_search",
            arguments={"ingredients": ingredients},
            llm_provider=llm_provider,
            call_cache=call_cache,
        )
    except Exception as exc:
        logger.warning("Recovery recipe_search failed: %s", exc)
        return _EMPTY
    fake_history = [{"role": "tool", "name": "recipe_search", "content": search_result}]
    products, _ = extract_recipe_products_from_history(fake_history)
    if not products:
        return _EMPTY
    return await _create_cart_from_products(products, call_mcp_tool, llm_provider, call_cache)
