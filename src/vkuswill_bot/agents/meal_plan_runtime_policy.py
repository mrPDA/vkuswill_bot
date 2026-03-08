"""Runtime timeout/deadline policy for meal-plan execution."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

TURN_DEADLINE_SECONDS = 120.0
PHASE2_DEADLINE_SECONDS = 100.0
RECIPE_INGREDIENTS_TIMEOUT_SECONDS = 8.0
RECIPE_SEARCH_TIMEOUT_SECONDS = 10.0
CART_CREATE_TIMEOUT_SECONDS = 12.0
CART_CREATE_RESERVE_SECONDS = 18.0
MCP_RETRY_ATTEMPTS = 1
MCP_RETRY_BACKOFF_SECONDS = 0.3

T = TypeVar("T")


def deadline_after(seconds: float) -> float:
    return time.monotonic() + max(0.0, seconds)


def deadline_remaining(deadline_at: float) -> float:
    return max(0.0, deadline_at - time.monotonic())


def bounded_deadline(seconds: float, *, hard_deadline_at: float) -> float:
    """Return a child deadline that cannot outlive the enclosing hard deadline."""
    return min(hard_deadline_at, deadline_after(seconds))


def reserve_deadline(deadline_at: float, *, reserve_seconds: float) -> float:
    """Return a deadline that preserves a tail budget before ``deadline_at``."""
    return max(time.monotonic() + 0.1, deadline_at - max(0.0, reserve_seconds))


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
