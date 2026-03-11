"""Runtime helpers for meal-plan cart creation."""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from vkuswill_bot.agents.meal_plan_runtime_policy import call_with_timeout_retry
from vkuswill_bot.agents.mcp_response_parser import extract_cart_data
from vkuswill_bot.services.cart_processor import CartProcessor

logger = logging.getLogger(__name__)

_MAX_CART_PRODUCTS = 30


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
    cart_raw_preview: str | None = None
    products_truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
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
        if self.cart_raw_preview:
            d["cart_raw_preview"] = self.cart_raw_preview
        if self.products_truncated:
            d["products_truncated"] = True
        return d


def _split_products(
    products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split products into cart batch (<=_MAX_CART_PRODUCTS) and overflow."""
    if len(products) <= _MAX_CART_PRODUCTS:
        return products, []
    return products[:_MAX_CART_PRODUCTS], products[_MAX_CART_PRODUCTS:]


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
    products_for_cart, overflow = _split_products(products)
    truncated = bool(overflow)
    stats = CartCreateStats(
        attempted=bool(products_for_cart),
        requested_products_count=len(products_for_cart),
        not_found_count=len(not_found),
        products_truncated=truncated,
    )
    if truncated:
        logger.warning(
            "Cart products split: %d in cart, %d overflow (API limit %d)",
            len(products_for_cart),
            len(overflow),
            _MAX_CART_PRODUCTS,
        )
    if not products_for_cart:
        return ({"not_found": not_found} if not_found else None), stats

    cart_args = CartProcessor.fix_cart_args({"products": products_for_cart})
    cart_result = ""
    try:
        cart_result = await call_with_timeout_retry(
            operation=lambda: agent._call_mcp_tool(
                name="vkusvill_cart_link_create",
                arguments=cart_args,
                llm_provider=llm_provider,
                call_cache=None,
                user_id=user_id,
            ),
            timeout_seconds=timeout_seconds,
            hard_deadline_at=phase2_deadline_at,
            retries=0,
        )
    except Exception as exc:
        stats.failed_before_response = True
        stats.error_type = type(exc).__name__
        stats.error_message = str(exc)[:240]
    if not cart_result:
        stats.failed_before_response = True
    cart_data = extract_cart_data(tool_name="vkusvill_cart_link_create", tool_result=cart_result)
    if cart_data is None:
        _record_cart_failure_diagnostics(stats, cart_result)
        return {"products": products, "not_found": not_found}, stats

    cart_data["products"] = products
    if overflow:
        cart_data["overflow_products"] = overflow
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


def _record_cart_failure_diagnostics(stats: CartCreateStats, cart_result: str) -> None:
    """Extract diagnostics from raw MCP response when cart creation fails."""
    if not cart_result:
        return
    stats.cart_raw_preview = cart_result[:300]
    with contextlib.suppress(Exception):
        payload = json.loads(cart_result)
        if isinstance(payload, dict):
            error = payload.get("error")
            message = payload.get("message")
            if error and not stats.error_type:
                stats.error_type = str(error)[:120]
            if message and not stats.error_message:
                stats.error_message = str(message)[:240]
