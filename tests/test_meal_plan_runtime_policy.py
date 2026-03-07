"""Unit tests for meal-plan runtime policy helpers."""

from __future__ import annotations

import asyncio

import pytest

from vkuswill_bot.agents.meal_plan_runtime_policy import (
    call_with_timeout_retry,
    deadline_after,
    deadline_remaining,
)


@pytest.mark.asyncio
async def test_call_with_timeout_retry_success() -> None:
    async def _op() -> str:
        await asyncio.sleep(0.001)
        return "ok"

    result = await call_with_timeout_retry(
        operation=_op,
        timeout_seconds=0.1,
        hard_deadline_at=deadline_after(0.2),
    )

    assert result == "ok"


@pytest.mark.asyncio
async def test_call_with_timeout_retry_times_out() -> None:
    async def _op() -> str:
        await asyncio.sleep(0.05)
        return "late"

    with pytest.raises(TimeoutError):
        await call_with_timeout_retry(
            operation=_op,
            timeout_seconds=0.01,
            hard_deadline_at=deadline_after(0.03),
            retries=0,
        )


def test_deadline_remaining_non_negative() -> None:
    assert deadline_remaining(deadline_after(0.001)) >= 0
