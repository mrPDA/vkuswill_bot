"""Runtime helpers for meal-plan cart creation."""

from __future__ import annotations

import contextlib
from typing import Any

from vkuswill_bot.agents.meal_plan_runtime_policy import call_with_timeout_retry
from vkuswill_bot.agents.mcp_response_parser import extract_cart_data
from vkuswill_bot.services.cart_processor import CartProcessor


async def maybe_create_cart_from_products(
    *,
    agent: Any,
    state: Any,
    user_id: int,
    llm_provider: str,
    products: list[dict[str, Any]],
    not_found: list[str],
    phase2_deadline_at: float,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    if not products:
        return {"not_found": not_found} if not_found else None

    cart_args = CartProcessor.fix_cart_args({"products": products})
    cart_result = ""
    with contextlib.suppress(Exception):
        cart_result = await call_with_timeout_retry(
            operation=lambda: agent._call_mcp_tool(
                name="vkusvill_cart_link_create",
                arguments=cart_args,
                llm_provider=llm_provider,
                call_cache=state.mcp_call_cache,
                user_id=user_id,
            ),
            timeout_seconds=timeout_seconds,
            hard_deadline_at=phase2_deadline_at,
        )
    cart_data = extract_cart_data(tool_name="vkusvill_cart_link_create", tool_result=cart_result)
    if cart_data is None:
        return {"products": products, "not_found": not_found}

    if "products" not in cart_data:
        cart_data["products"] = products
    if not_found and "not_found" not in cart_data:
        cart_data["not_found"] = not_found
    agent._ensure_cart_price_summary(
        cart_data=cart_data,
        product_index=state.product_index_this_turn,
    )
    agent._capture_cart_snapshot(
        user_id=user_id,
        tool_name="vkusvill_cart_link_create",
        args=cart_args,
        result=cart_result,
    )
    return cart_data
