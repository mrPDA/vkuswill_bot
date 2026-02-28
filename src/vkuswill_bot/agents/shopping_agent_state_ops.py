"""State-oriented helper operations for ShoppingAgent service mixin."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.cart_price_builder import ensure_cart_price_summary
from vkuswill_bot.agents.product_index_manager import build_cart_snapshot
from vkuswill_bot.agents.response_analysis import should_start_fresh_context


def capture_cart_snapshot(
    *,
    last_cart_snapshot: dict[int, dict[str, Any]],
    user_id: int,
    tool_name: str,
    args: dict[str, Any],
    result: str,
) -> None:
    if tool_name != "vkusvill_cart_link_create":
        return
    snapshot = build_cart_snapshot(args=args, result=result)
    if snapshot is not None:
        last_cart_snapshot[user_id] = snapshot


def ensure_cart_summary(
    *,
    cart_data: dict[str, Any],
    product_index: dict[int, dict[str, Any]],
) -> None:
    ensure_cart_price_summary(cart_data=cart_data, product_index=product_index)


def should_start_fresh(*, text: str, history: list[dict[str, Any]] | None) -> bool:
    return should_start_fresh_context(text=text, history=history)
