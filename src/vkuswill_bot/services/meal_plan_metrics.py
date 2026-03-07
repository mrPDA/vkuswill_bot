"""Meal-plan rollout metrics sink with optional centralized event backend."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import math
import threading
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

MealPlanMetricsEventLogger = Callable[[int, str, dict[str, Any]], Awaitable[None]]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = round((len(sorted_values) - 1) * p)
    idx = max(0, min(len(sorted_values) - 1, idx))
    return sorted_values[idx]


def _normalize_profile(value: str) -> str:
    normalized = str(value).strip().lower()
    return normalized if normalized else "unknown"


def _hash_user_id(user_id: int | None) -> str:
    if user_id is None:
        return "unknown"
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]


def _wilson_lower_bound(*, successes: int, trials: int, z: float = 1.96) -> float:
    if trials <= 0:
        return 0.0
    n = float(trials)
    p = float(successes) / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return max(0.0, min(1.0, (center - margin) / denominator))


def _normalize_rollout_stage(percent: int) -> int:
    value = max(0, min(100, int(percent)))
    if value <= 0:
        return 0
    if value <= 10:
        return 10
    if value <= 50:
        return 50
    return 100


@dataclass(slots=True)
class MealPlanRolloutMetrics:
    precision: float
    precision_sample_size: int
    precision_wilson_lower_95: float
    success_rate: float
    fallback_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    error_rate: float
    baseline_error_rate: float


class PostgresMealPlanMetricsReader:
    """Read centralized rollout metrics from user_events."""

    def __init__(self, *, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def _fetch_precision_counts(self, *, window_days: int) -> tuple[int, int]:
        sql = """
            WITH routing AS (
                SELECT metadata->>'trace_id' AS trace_id,
                       LOWER(COALESCE(metadata->>'prediction', 'unknown')) AS prediction
                FROM user_events
                WHERE event_type = 'meal_plan_routing_event'
                  AND created_at >= NOW() - ($1::int || ' days')::interval
            ),
            labels AS (
                SELECT metadata->>'trace_id' AS trace_id,
                       LOWER(COALESCE(metadata->>'ground_truth', 'unknown')) AS ground_truth
                FROM user_events
                WHERE event_type = 'meal_plan_shadow_label'
                  AND created_at >= NOW() - ($1::int || ' days')::interval
            ),
            joined AS (
                SELECT r.trace_id, r.prediction, l.ground_truth
                FROM routing r
                JOIN labels l ON l.trace_id = r.trace_id
            )
            SELECT
                COUNT(*) FILTER (
                    WHERE prediction = 'meal_plan' AND ground_truth = 'meal_plan'
                ) AS tp,
                COUNT(*) FILTER (
                    WHERE prediction = 'meal_plan' AND ground_truth <> 'meal_plan'
                ) AS fp
            FROM joined
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, window_days)
        tp = int(row["tp"] or 0) if row is not None else 0
        fp = int(row["fp"] or 0) if row is not None else 0
        return tp, fp

    async def _fetch_execution_metrics(
        self, *, window_days: int
    ) -> tuple[int, int, int, float, float]:
        sql = """
            WITH execution AS (
                SELECT
                    LOWER(COALESCE(metadata->>'outcome', 'unknown')) AS outcome,
                    CASE
                        WHEN COALESCE(metadata->>'latency_ms', '') ~ '^[0-9]+(\\.[0-9]+)?$'
                        THEN (metadata->>'latency_ms')::double precision
                        ELSE NULL
                    END AS latency_ms
                FROM user_events
                WHERE event_type = 'meal_plan_execution_event'
                  AND created_at >= NOW() - ($1::int || ' days')::interval
            )
            SELECT
                COUNT(*) FILTER (WHERE outcome = 'success') AS success_count,
                COUNT(*) FILTER (WHERE outcome = 'fallback') AS fallback_count,
                COUNT(*) AS total_count,
                COALESCE(
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms), 0
                ) AS latency_p50_ms,
                COALESCE(
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms), 0
                ) AS latency_p95_ms
            FROM execution
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, window_days)
        if row is None:
            return 0, 0, 0, 0.0, 0.0
        return (
            int(row["success_count"] or 0),
            int(row["fallback_count"] or 0),
            int(row["total_count"] or 0),
            float(row["latency_p50_ms"] or 0.0),
            float(row["latency_p95_ms"] or 0.0),
        )

    async def _fetch_baseline_error_rate(self, *, window_days: int) -> float:
        sql = """
            WITH baseline AS (
                SELECT LOWER(COALESCE(metadata->>'outcome', 'unknown')) AS outcome
                FROM user_events
                WHERE event_type = 'meal_plan_execution_event'
                  AND created_at >= NOW() - (($1::int * 2) || ' days')::interval
                  AND created_at < NOW() - ($1::int || ' days')::interval
            )
            SELECT
                COUNT(*) FILTER (WHERE outcome = 'fallback') AS fallback_count,
                COUNT(*) AS total_count
            FROM baseline
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, window_days)
        if row is None:
            return 0.0
        fallback_count = int(row["fallback_count"] or 0)
        total_count = int(row["total_count"] or 0)
        if total_count <= 0:
            return 0.0
        return fallback_count / total_count

    async def fetch_rollout_metrics(self, *, window_days: int = 7) -> MealPlanRolloutMetrics:
        tp, fp = await self._fetch_precision_counts(window_days=window_days)
        (
            success_count,
            fallback_count,
            total_count,
            latency_p50_ms,
            latency_p95_ms,
        ) = await self._fetch_execution_metrics(window_days=window_days)
        baseline_error_rate = await self._fetch_baseline_error_rate(window_days=window_days)
        sample_size = tp + fp
        precision = (tp / sample_size) if sample_size else 0.0
        success_rate = (success_count / total_count) if total_count else 0.0
        fallback_rate = (fallback_count / total_count) if total_count else 0.0
        return MealPlanRolloutMetrics(
            precision=precision,
            precision_sample_size=sample_size,
            precision_wilson_lower_95=_wilson_lower_bound(successes=tp, trials=sample_size),
            success_rate=success_rate,
            fallback_rate=fallback_rate,
            latency_p50_ms=latency_p50_ms,
            latency_p95_ms=latency_p95_ms,
            error_rate=fallback_rate,
            baseline_error_rate=baseline_error_rate,
        )


class MealPlanRolloutController:
    """Resolve effective rollout stage based on centralized KPI gates."""

    def __init__(
        self,
        *,
        metrics_reader: PostgresMealPlanMetricsReader,
        min_sample_size: int = 300,
        precision_target: float = 0.97,
        max_error_rate_regression_pp: float = 0.01,
        min_success_rate: float = 0.95,
    ) -> None:
        self._metrics_reader = metrics_reader
        self._min_sample_size = max(1, int(min_sample_size))
        self._precision_target = max(0.0, min(1.0, float(precision_target)))
        self._max_error_rate_regression_pp = max(0.0, float(max_error_rate_regression_pp))
        self._min_success_rate = max(0.0, min(1.0, float(min_success_rate)))

    async def resolve_rollout_percent(self, *, configured_percent: int) -> int:
        requested = _normalize_rollout_stage(configured_percent)
        if requested == 0:
            return 0
        metrics = await self._metrics_reader.fetch_rollout_metrics(window_days=7)
        gate_10 = (
            metrics.precision_sample_size >= self._min_sample_size
            and metrics.precision_wilson_lower_95 >= self._precision_target
        )
        gate_50 = gate_10 and (
            metrics.error_rate <= (metrics.baseline_error_rate + self._max_error_rate_regression_pp)
        )
        gate_100 = gate_50 and metrics.success_rate >= self._min_success_rate
        highest_allowed = 0
        if gate_10:
            highest_allowed = 10
        if gate_50:
            highest_allowed = 50
        if gate_100:
            highest_allowed = 100
        return min(requested, highest_allowed)


class MealPlanMetricsSink:
    """Aggregates rollout metrics and emits centralized observability events."""

    def __init__(self, *, event_logger: MealPlanMetricsEventLogger | None = None) -> None:
        self._event_logger = event_logger
        self._lock = threading.Lock()
        self._routing = Counter()
        self._executor_outcomes = Counter()
        self._executor_latencies_ms: list[float] = []
        self._emit_dropped_missing_user_id = 0
        self._predictions_by_trace_id: dict[str, str] = {}
        self._labels_by_trace_id: dict[str, str] = {}

    async def _emit(
        self,
        *,
        user_id: int | None,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        if self._event_logger is None:
            return
        if not isinstance(user_id, int) or user_id <= 0:
            with self._lock:
                self._emit_dropped_missing_user_id += 1
            return
        event_user_id = user_id
        await self._event_logger(event_user_id, event_type, metadata)

    async def record_routing(
        self,
        *,
        profile: str,
        executed_via_executor: bool,
        shadow_mode: bool,
        user_id: int | None = None,
        trace_id: str | None = None,
        ground_truth_profile: str | None = None,
        label_source: str = "unlabeled",
        rollout_bypass: dict[str, Any] | None = None,
    ) -> str:
        prediction = _normalize_profile(profile)
        routed_trace_id = str(trace_id).strip() if trace_id else uuid.uuid4().hex

        with self._lock:
            self._routing["routing_events_total"] += 1
            if prediction == "meal_plan":
                self._routing["meal_plan_predicted"] += 1
                if executed_via_executor:
                    self._routing["meal_plan_executor_selected"] += 1
                if shadow_mode:
                    self._routing["meal_plan_shadow_turns"] += 1
            self._predictions_by_trace_id[routed_trace_id] = prediction
            if ground_truth_profile:
                self._labels_by_trace_id[routed_trace_id] = _normalize_profile(ground_truth_profile)

        await self._emit(
            user_id=user_id,
            event_type="meal_plan_routing_event",
            metadata={
                "trace_id": routed_trace_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "user_id_hash": _hash_user_id(user_id),
                "prediction": prediction,
                "shadow_mode": bool(shadow_mode),
                "executed_via_executor": bool(executed_via_executor),
                "label_source": label_source,
                "rollout_bypass": dict(rollout_bypass) if isinstance(rollout_bypass, dict) else {},
            },
        )
        return routed_trace_id

    async def record_shadow_label(
        self,
        *,
        trace_id: str,
        ground_truth_profile: str,
        reviewer_id: str,
        user_id: int | None = None,
        label_source: str = "manual_shadow_review",
    ) -> None:
        normalized_label = _normalize_profile(ground_truth_profile)
        with self._lock:
            self._labels_by_trace_id[trace_id] = normalized_label
            self._routing["shadow_labels_total"] += 1

        await self._emit(
            user_id=user_id,
            event_type="meal_plan_shadow_label",
            metadata={
                "trace_id": trace_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "ground_truth": normalized_label,
                "reviewer_id": reviewer_id,
                "label_source": label_source,
            },
        )

    async def record_executor_result(
        self,
        *,
        outcome: str,
        latency_ms: float,
        user_id: int | None = None,
        trace_id: str | None = None,
        phase: str = "full_turn",
    ) -> None:
        normalized_outcome = str(outcome).strip().lower() or "unknown"
        latency_value = max(0.0, float(latency_ms))
        with self._lock:
            self._executor_outcomes[normalized_outcome] += 1
            self._executor_latencies_ms.append(latency_value)

        await self._emit(
            user_id=user_id,
            event_type="meal_plan_execution_event",
            metadata={
                "trace_id": trace_id or uuid.uuid4().hex,
                "timestamp": datetime.now(UTC).isoformat(),
                "outcome": normalized_outcome,
                "latency_ms": latency_value,
                "phase": phase,
            },
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            tp = 0
            fp = 0
            for trace_id, prediction in self._predictions_by_trace_id.items():
                if prediction != "meal_plan":
                    continue
                label = self._labels_by_trace_id.get(trace_id)
                if label is None:
                    continue
                if label == "meal_plan":
                    tp += 1
                else:
                    fp += 1

            success = self._executor_outcomes.get("success", 0)
            fallback = self._executor_outcomes.get("fallback", 0)
            total_results = sum(self._executor_outcomes.values())
            precision_denominator = tp + fp

            return {
                "routing_precision_meal_plan": (
                    tp / precision_denominator if precision_denominator else 0.0
                ),
                "routing_precision_sample_size": precision_denominator,
                "success_rate": (success / total_results) if total_results else 0.0,
                "fallback_rate": (fallback / total_results) if total_results else 0.0,
                "latency_p50_ms": _percentile(self._executor_latencies_ms, 0.50),
                "latency_p95_ms": _percentile(self._executor_latencies_ms, 0.95),
                "centralized_backend_enabled": self._event_logger is not None,
                "counters": {
                    "routing": dict(self._routing),
                    "outcomes": dict(self._executor_outcomes),
                },
                "emit_dropped_missing_user_id": self._emit_dropped_missing_user_id,
            }


def get_meal_plan_metrics_sink(
    *,
    event_logger: MealPlanMetricsEventLogger | None = None,
) -> MealPlanMetricsSink:
    """Create a metrics sink instance.

    The sink keeps local counters for fast snapshots and optionally emits each
    event to a centralized backend via ``event_logger`` (e.g. PostgreSQL user_events).
    """
    return MealPlanMetricsSink(event_logger=event_logger)
