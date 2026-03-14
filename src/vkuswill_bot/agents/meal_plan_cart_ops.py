"""Runtime helpers for meal-plan cart creation."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from vkuswill_bot.agents.meal_plan_runtime_policy import call_with_timeout_retry
from vkuswill_bot.agents.mcp_response_parser import extract_cart_data
from vkuswill_bot.services.cart_processor import CartProcessor

logger = logging.getLogger(__name__)

_MAX_CART_PRODUCTS = 30

MergeFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


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
    groups_count: int = 1

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
            "groups_count": self.groups_count,
        }
        if self.cart_raw_preview:
            d["cart_raw_preview"] = self.cart_raw_preview
        return d


@dataclass(slots=True)
class CartGroupResult:
    day_label: str
    day_numbers: list[int] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    link: str = ""
    cart_created: bool = False
    error_type: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "day_label": self.day_label,
            "day_numbers": self.day_numbers,
            "link": self.link,
            "products": self.products,
            "cart_created": self.cart_created,
        }


def _day_range_label(days: list[int]) -> str:
    if not days:
        return "Все дни"
    if len(days) == 1:
        return f"День {days[0]}"
    return f"Дни {days[0]}-{days[-1]}"


def group_days_for_carts(
    products_by_day: dict[int, list[dict[str, Any]]],
    merge_fn: MergeFn,
) -> list[tuple[list[int], list[dict[str, Any]]]]:
    """Greedy grouping: consecutive days into batches that fit _MAX_CART_PRODUCTS."""
    groups: list[tuple[list[int], list[dict[str, Any]]]] = []
    current_days: list[int] = []
    current_raw: list[dict[str, Any]] = []

    for day in sorted(products_by_day.keys()):
        day_products = products_by_day[day]
        if not day_products:
            continue
        trial_merged = merge_fn(current_raw + day_products)
        if len(trial_merged) <= _MAX_CART_PRODUCTS or not current_days:
            current_days.append(day)
            current_raw.extend(day_products)
        else:
            groups.append((list(current_days), merge_fn(current_raw)))
            current_days = [day]
            current_raw = list(day_products)

    if current_days:
        groups.append((list(current_days), merge_fn(current_raw)))
    return groups


async def create_grouped_carts(
    *,
    agent: Any,
    state: Any,
    user_id: int,
    llm_provider: str,
    products: list[dict[str, Any]],
    products_by_day: dict[int, list[dict[str, Any]]],
    not_found: list[str],
    phase2_deadline_at: float,
    timeout_seconds: float,
    merge_fn: MergeFn,
) -> tuple[dict[str, Any] | None, CartCreateStats]:
    """Create one or more carts, grouping days to respect _MAX_CART_PRODUCTS."""
    total_products = len(products)
    stats = CartCreateStats(
        attempted=bool(products),
        requested_products_count=total_products,
        not_found_count=len(not_found),
    )
    if not products:
        return ({"not_found": not_found} if not_found else None), stats

    if total_products <= _MAX_CART_PRODUCTS or not products_by_day:
        cart_data, single_stats = await _create_single_cart(
            agent=agent,
            state=state,
            user_id=user_id,
            llm_provider=llm_provider,
            products=products,
            phase2_deadline_at=phase2_deadline_at,
            timeout_seconds=timeout_seconds,
        )
        stats.cart_created = single_stats.cart_created
        stats.has_link = single_stats.has_link
        stats.returned_products_count = single_stats.returned_products_count
        stats.failed_before_response = single_stats.failed_before_response
        stats.error_type = single_stats.error_type
        stats.error_message = single_stats.error_message
        stats.cart_raw_preview = single_stats.cart_raw_preview
        if cart_data is None:
            cart_data = {"products": products}
        cart_data.setdefault("products", products)
        if not_found:
            cart_data.setdefault("not_found", not_found)
        agent._ensure_cart_price_summary(
            cart_data=cart_data,
            product_index=state.product_index_this_turn,
        )
        return cart_data, stats

    day_groups = group_days_for_carts(products_by_day, merge_fn)
    stats.groups_count = len(day_groups)
    logger.info(
        "Creating %d grouped carts (total products=%d, API limit=%d)",
        len(day_groups),
        total_products,
        _MAX_CART_PRODUCTS,
    )

    group_results: list[CartGroupResult] = []
    per_cart_timeout = max(5.0, timeout_seconds / max(1, len(day_groups)))

    for day_numbers, group_products in day_groups:
        label = _day_range_label(day_numbers)
        gr = CartGroupResult(day_label=label, day_numbers=day_numbers, products=group_products)

        cart_data_single, single_stats = await _create_single_cart(
            agent=agent,
            state=state,
            user_id=user_id,
            llm_provider=llm_provider,
            products=group_products,
            phase2_deadline_at=phase2_deadline_at,
            timeout_seconds=per_cart_timeout,
        )
        gr.cart_created = single_stats.cart_created
        gr.link = str((cart_data_single or {}).get("link", "")).strip()
        gr.error_type = single_stats.error_type
        gr.error_message = single_stats.error_message
        group_results.append(gr)

        if single_stats.cart_created:
            stats.cart_created = True
            stats.has_link = True
            stats.returned_products_count += len(group_products)
        elif single_stats.cart_raw_preview and not stats.cart_raw_preview:
            stats.cart_raw_preview = single_stats.cart_raw_preview

    cart_data_out: dict[str, Any] = {
        "products": products,
        "groups": [g.as_dict() for g in group_results],
    }
    first_link = next((g.link for g in group_results if g.link), "")
    if first_link:
        cart_data_out["link"] = first_link
    if not_found:
        cart_data_out["not_found"] = not_found

    agent._ensure_cart_price_summary(
        cart_data=cart_data_out,
        product_index=state.product_index_this_turn,
    )
    return cart_data_out, stats


async def _create_single_cart(
    *,
    agent: Any,
    state: Any,
    user_id: int,
    llm_provider: str,
    products: list[dict[str, Any]],
    phase2_deadline_at: float,
    timeout_seconds: float,
) -> tuple[dict[str, Any] | None, CartCreateStats]:
    """Create a single cart for a batch of products (<= _MAX_CART_PRODUCTS)."""
    stats = CartCreateStats(
        attempted=bool(products),
        requested_products_count=len(products),
        not_found_count=0,
    )
    if not products:
        return None, stats

    if len(products) > _MAX_CART_PRODUCTS:
        logger.warning(
            "Truncating cart from %d to %d products (API limit)",
            len(products),
            _MAX_CART_PRODUCTS,
        )
        products = products[:_MAX_CART_PRODUCTS]

    cart_args = CartProcessor.fix_cart_args({"products": products})
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
        return None, stats

    agent._capture_cart_snapshot(
        user_id=user_id,
        tool_name="vkusvill_cart_link_create",
        args=cart_args,
        result=cart_result,
    )
    stats.cart_created = True
    stats.has_link = bool(str(cart_data.get("link", "")).strip())
    stats.returned_products_count = len(products)
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
