"""Runtime timeout/deadline policy for meal-plan execution."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

TURN_DEADLINE_SECONDS = 120.0
TURN_DEADLINE_EXTENDED_SECONDS = 240.0
PHASE2_DEADLINE_SECONDS = 100.0
PHASE2_DEADLINE_EXTENDED_SECONDS = 210.0
_EXTENDED_DEADLINE_MIN_DAYS = 5
RECIPE_INGREDIENTS_TIMEOUT_SECONDS = 8.0
RECIPE_SEARCH_TIMEOUT_SECONDS = 10.0
CART_CREATE_TIMEOUT_SECONDS = 30.0
CART_CREATE_RESERVE_SECONDS = 28.0
MIN_SEARCH_BUDGET_SECONDS = 40.0
MCP_RETRY_ATTEMPTS = 1
MCP_RETRY_BACKOFF_SECONDS = 0.3

T = TypeVar("T")


def adaptive_deadlines(days: int) -> tuple[float, float]:
    """Return (turn_deadline_seconds, phase2_deadline_seconds) scaled to plan size."""
    if days >= _EXTENDED_DEADLINE_MIN_DAYS:
        return TURN_DEADLINE_EXTENDED_SECONDS, PHASE2_DEADLINE_EXTENDED_SECONDS
    return TURN_DEADLINE_SECONDS, PHASE2_DEADLINE_SECONDS


def deadline_after(seconds: float) -> float:
    return time.monotonic() + max(0.0, seconds)


def deadline_remaining(deadline_at: float) -> float:
    return max(0.0, deadline_at - time.monotonic())


def bounded_deadline(seconds: float, *, hard_deadline_at: float) -> float:
    """Return a child deadline that cannot outlive the enclosing hard deadline."""
    return min(hard_deadline_at, deadline_after(seconds))


def reserve_deadline(
    deadline_at: float,
    *,
    reserve_seconds: float,
    min_budget_seconds: float = 0.1,
) -> float:
    """Return a deadline that preserves a tail budget before ``deadline_at``.

    ``min_budget_seconds`` guarantees a floor — even when most of the time
    has already been consumed by earlier phases, the caller gets at least
    this much time.  The returned deadline never exceeds ``deadline_at``.
    """
    candidate = deadline_at - max(0.0, reserve_seconds)
    floor = time.monotonic() + min_budget_seconds
    return min(deadline_at, max(floor, candidate))


async def call_with_timeout_retry(
    *,
    operation: Callable[[], Awaitable[T]],
    timeout_seconds: float,
    hard_deadline_at: float,
    retries: int = MCP_RETRY_ATTEMPTS,
    backoff_seconds: float = MCP_RETRY_BACKOFF_SECONDS,
) -> T:
    """Execute async operation with bounded timeout and single retry policy."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        remaining = deadline_remaining(hard_deadline_at)
        if remaining <= 0:
            raise TimeoutError("deadline exceeded")
        effective_timeout = min(max(0.1, timeout_seconds), remaining)
        try:
            return await asyncio.wait_for(operation(), timeout=effective_timeout)
        except TimeoutError as exc:
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(backoff_seconds)
    raise last_error or TimeoutError("timeout")
