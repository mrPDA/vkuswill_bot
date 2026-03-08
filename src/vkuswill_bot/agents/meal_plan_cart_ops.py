"""Runtime helpers for meal-plan cart creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vkuswill_bot.agents.meal_plan_runtime_policy import call_with_timeout_retry
from vkuswill_bot.agents.mcp_response_parser import extract_cart_data
from vkuswill_bot.services.cart_processor import CartProcessor


@dataclass(slots=True)
class CartCreateStats:
    attempted: bool
    requested_products_count: int
    not_found_count: int
    cart_created: bool = False
    returned_products_count: int = 0
    has_link: bool = False
    failed_before_response: bool = False
    error_type: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "requested_products_count": self.requested_products_count,
            "not_found_count": self.not_found_count,
            "cart_created": self.cart_created,
            "returned_products_count": self.returned_products_count,
            "has_link": self.has_link,
            "failed_before_response": self.failed_before_response,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


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
) -> tuple[dict[str, Any] | None, CartCreateStats]:
    stats = CartCreateStats(
        attempted=bool(products),
        requested_products_count=len(products),
        not_found_count=len(not_found),
    )
    if not products:
        return ({"not_found": not_found} if not_found else None), stats

    cart_args = CartProcessor.fix_cart_args({"products": products})
    cart_result = ""
    try:
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
    except Exception as exc:
        stats.failed_before_response = True
        stats.error_type = type(exc).__name__
        stats.error_message = str(exc)[:240]
    if not cart_result:
        stats.failed_before_response = True
    cart_data = extract_cart_data(tool_name="vkusvill_cart_link_create", tool_result=cart_result)
    if cart_data is None:
        return {"products": products, "not_found": not_found}, stats

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
    stats.cart_created = True
    stats.has_link = bool(str(cart_data.get("link", "")).strip())
    returned_products = cart_data.get("products")
    stats.returned_products_count = (
        len(returned_products) if isinstance(returned_products, list) else 0
    )
    return cart_data, stats
