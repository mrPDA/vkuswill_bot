"""Tests for meal-plan rollout metrics sink and rollout sampler."""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from vkuswill_bot.agents.shopping_turn_executor import _is_user_in_rollout
from vkuswill_bot.services.meal_plan_metrics import (
    MealPlanMetricsSink,
    MealPlanRolloutController,
    MealPlanRolloutMetrics,
    PostgresMealPlanMetricsReader,
)


@pytest.mark.asyncio
async def test_meal_plan_metrics_sink_aggregates_core_rollout_metrics() -> None:
    sink = MealPlanMetricsSink()
    await sink.record_routing(
        profile="meal_plan",
        executed_via_executor=True,
        shadow_mode=False,
        trace_id="t1",
    )
    await sink.record_routing(
        profile="meal_plan",
        executed_via_executor=False,
        shadow_mode=True,
        trace_id="t2",
    )
    await sink.record_shadow_label(
        trace_id="t1",
        ground_truth_profile="meal_plan",
        reviewer_id="qa-1",
    )
    await sink.record_shadow_label(
        trace_id="t2",
        ground_truth_profile="cart",
        reviewer_id="qa-1",
    )
    await sink.record_executor_result(outcome="success", latency_ms=100)
    await sink.record_executor_result(outcome="fallback", latency_ms=200)

    snapshot = sink.snapshot()
    assert snapshot["routing_precision_meal_plan"] == 0.5
    assert snapshot["routing_precision_sample_size"] == 2
    assert snapshot["success_rate"] == 0.5
    assert snapshot["fallback_rate"] == 0.5
    assert snapshot["latency_p50_ms"] >= 100
    assert snapshot["latency_p95_ms"] >= snapshot["latency_p50_ms"]


@pytest.mark.asyncio
async def test_meal_plan_metrics_sink_emits_centralized_events_when_logger_present() -> None:
    logger = AsyncMock()
    sink = MealPlanMetricsSink(event_logger=logger)
    await sink.record_routing(
        profile="meal_plan",
        executed_via_executor=True,
        shadow_mode=True,
        user_id=42,
        trace_id="trace-centralized",
        rollout_bypass={
            "active": True,
            "reason": "staging gate exception",
            "actor": "qa-bot",
            "expires_at": "2030-01-01T00:00:00+00:00",
            "ttl_seconds": 1200,
        },
    )
    await sink.record_executor_result(
        outcome="success",
        latency_ms=120,
        user_id=42,
        trace_id="trace-centralized",
    )

    assert logger.await_count == 2
    event_types = [call.args[1] for call in logger.await_args_list]
    assert "meal_plan_routing_event" in event_types
    assert "meal_plan_execution_event" in event_types
    routing_event = next(call for call in logger.await_args_list if call.args[1] == "meal_plan_routing_event")
    routing_metadata = routing_event.args[2]
    assert routing_metadata["rollout_bypass"]["active"] is True
    assert routing_metadata["rollout_bypass"]["actor"] == "qa-bot"


def test_rollout_sampler_respects_boundaries() -> None:
    assert _is_user_in_rollout(user_id=42, rollout_percent=0) is False
    assert _is_user_in_rollout(user_id=42, rollout_percent=100) is True


@pytest.mark.asyncio
async def test_meal_plan_metrics_sink_counts_dropped_events_without_user_id() -> None:
    logger = AsyncMock()
    sink = MealPlanMetricsSink(event_logger=logger)
    await sink.record_routing(
        profile="meal_plan",
        executed_via_executor=True,
        shadow_mode=False,
        user_id=None,
        trace_id="trace-no-user",
    )

    assert logger.await_count == 0
    assert sink.snapshot()["emit_dropped_missing_user_id"] == 1


class _FakeConn:
    def __init__(self, rows: list[dict[str, float | int]]) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, int]] = []

    async def fetchrow(self, sql: str, window_days: int) -> dict[str, float | int]:
        self.calls.append((sql, window_days))
        return self._rows.pop(0)


class _AcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakePool:
    def __init__(self, rows: list[dict[str, float | int]]) -> None:
        self.conn = _FakeConn(rows)

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self.conn)


@pytest.mark.asyncio
async def test_postgres_reader_fetches_centralized_rollout_metrics() -> None:
    pool = _FakePool(
        [
            {"tp": 194, "fp": 6},
            {
                "success_count": 380,
                "fallback_count": 20,
                "total_count": 400,
                "latency_p50_ms": 480.0,
                "latency_p95_ms": 1550.0,
            },
            {"fallback_count": 15, "total_count": 500},
        ]
    )
    reader = PostgresMealPlanMetricsReader(pool=pool)  # type: ignore[arg-type]

    metrics = await reader.fetch_rollout_metrics(window_days=7)
    metrics_dict = asdict(metrics)

    assert metrics_dict["precision"] == pytest.approx(0.97)
    assert metrics_dict["precision_sample_size"] == 200
    assert metrics_dict["success_rate"] == pytest.approx(0.95)
    assert metrics_dict["fallback_rate"] == pytest.approx(0.05)
    assert metrics_dict["latency_p50_ms"] == 480.0
    assert metrics_dict["latency_p95_ms"] == 1550.0
    assert metrics_dict["baseline_error_rate"] == pytest.approx(0.03)
    assert len(pool.conn.calls) == 3


@pytest.mark.asyncio
async def test_rollout_controller_applies_kpi_gates() -> None:
    metrics_reader = AsyncMock()
    controller = MealPlanRolloutController(metrics_reader=metrics_reader)

    metrics_reader.fetch_rollout_metrics.return_value = MealPlanRolloutMetrics(
        precision=0.99,
        precision_sample_size=100,
        precision_wilson_lower_95=0.98,
        success_rate=0.99,
        fallback_rate=0.01,
        latency_p50_ms=500.0,
        latency_p95_ms=1200.0,
        error_rate=0.01,
        baseline_error_rate=0.01,
    )
    assert await controller.resolve_rollout_percent(configured_percent=100) == 0

    metrics_reader.fetch_rollout_metrics.return_value = MealPlanRolloutMetrics(
        precision=0.98,
        precision_sample_size=800,
        precision_wilson_lower_95=0.975,
        success_rate=0.99,
        fallback_rate=0.05,
        latency_p50_ms=450.0,
        latency_p95_ms=1300.0,
        error_rate=0.05,
        baseline_error_rate=0.02,
    )
    assert await controller.resolve_rollout_percent(configured_percent=100) == 10

    metrics_reader.fetch_rollout_metrics.return_value = MealPlanRolloutMetrics(
        precision=0.98,
        precision_sample_size=800,
        precision_wilson_lower_95=0.975,
        success_rate=0.94,
        fallback_rate=0.02,
        latency_p50_ms=440.0,
        latency_p95_ms=1200.0,
        error_rate=0.02,
        baseline_error_rate=0.02,
    )
    assert await controller.resolve_rollout_percent(configured_percent=100) == 50

    metrics_reader.fetch_rollout_metrics.return_value = MealPlanRolloutMetrics(
        precision=0.98,
        precision_sample_size=800,
        precision_wilson_lower_95=0.975,
        success_rate=0.97,
        fallback_rate=0.02,
        latency_p50_ms=440.0,
        latency_p95_ms=1200.0,
        error_rate=0.02,
        baseline_error_rate=0.02,
    )
    assert await controller.resolve_rollout_percent(configured_percent=100) == 100
