"""Unit tests for meal-plan rollout policy helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vkuswill_bot.services.meal_plan_rollout_policy import (
    evaluate_non_prod_rollout_bypass,
    resolve_rollout_percent,
)


def test_evaluate_non_prod_rollout_bypass_requires_full_audit_fields() -> None:
    now = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    decision = evaluate_non_prod_rollout_bypass(
        enabled=True,
        environment="staging",
        reason="",
        actor="qa-bot",
        expires_at="2026-03-07T12:05:00+00:00",
        max_ttl_seconds=3600,
        now_utc=now,
    )

    assert decision.allow_unvalidated is False
    assert decision.audit.blocked_by == "missing_reason"
    assert decision.audit.as_dict()["active"] is False


def test_evaluate_non_prod_rollout_bypass_blocks_expired_ttl() -> None:
    now = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    decision = evaluate_non_prod_rollout_bypass(
        enabled=True,
        environment="staging",
        reason="load-test",
        actor="qa-bot",
        expires_at="2026-03-07T11:59:00+00:00",
        max_ttl_seconds=3600,
        now_utc=now,
    )

    assert decision.allow_unvalidated is False
    assert decision.audit.blocked_by == "expired"
    assert decision.audit.ttl_seconds == -60


def test_evaluate_non_prod_rollout_bypass_returns_active_audit_when_valid() -> None:
    now = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    decision = evaluate_non_prod_rollout_bypass(
        enabled=True,
        environment="staging",
        reason="staging smoke",
        actor="qa-bot",
        expires_at="2026-03-07T12:10:00+00:00",
        max_ttl_seconds=3600,
        now_utc=now,
    )

    assert decision.allow_unvalidated is True
    audit = decision.audit.as_dict()
    assert audit["active"] is True
    assert audit["reason"] == "staging smoke"
    assert audit["actor"] == "qa-bot"
    assert audit["ttl_seconds"] == 600


@pytest.mark.asyncio
async def test_resolve_rollout_percent_returns_zero_when_controller_fails_without_bypass() -> None:
    class _BrokenController:
        async def resolve_rollout_percent(self, *, configured_percent: int) -> int:
            _ = configured_percent
            raise RuntimeError("controller unavailable")

    rollout_percent = await resolve_rollout_percent(
        shadow_mode=False,
        configured_percent=50,
        controller=_BrokenController(),
        allow_unvalidated=False,
    )
    assert rollout_percent == 0
