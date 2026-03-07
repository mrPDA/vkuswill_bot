"""Trace metadata helpers for meal-plan executor."""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from typing import Any


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def history_char_count(history: list[dict[str, Any]]) -> int:
    total = 0
    for message in history:
        with contextlib.suppress(Exception):
            total += len(json.dumps(message, ensure_ascii=False))
    return total


def resolve_metrics_trace_id(*, trace: Any | None, user_id: int) -> str:
    if trace is not None:
        for attr in ("id", "trace_id"):
            value = getattr(trace, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return f"meal-plan-turn-{user_id}-{uuid.uuid4().hex}"


def update_success_trace(
    *,
    trace: Any | None,
    output: str,
    dishes_payload: list[dict[str, Any]],
    aggregated_ingredients: list[dict[str, Any]],
    products: list[dict[str, Any]],
    soft_coverage_by_group: dict[str, float],
    request: Any,
    request_payload: dict[str, Any],
    phase1_elapsed_ms: int,
    phase2_started_at: float,
    started_at: float,
    used_chunk_fallback: bool,
    phase2_deadline_seconds: float,
    turn_deadline_seconds: float,
) -> None:
    if trace is None:
        return
    coverage_values = list(soft_coverage_by_group.values())
    trace.update(
        output=output,
        metadata={
            "reason": "meal_plan_executor_completed",
            "dishes_count": len(dishes_payload),
            "ingredients_count": len(aggregated_ingredients),
            "products_count": len(products),
            "soft_coverage_by_group": soft_coverage_by_group,
            "soft_coverage_min": min(coverage_values) if coverage_values else 1.0,
            "preferences_trace_count": len(request.preferences_trace),
            "applied_preferences_trace_count": len(request.applied_preferences_trace),
            "preference_sources": request_payload.get("preference_sources", {}),
            "phase1_elapsed_ms": phase1_elapsed_ms,
            "phase2_elapsed_ms": _elapsed_ms(phase2_started_at),
            "total_elapsed_ms": _elapsed_ms(started_at),
            "phase2_deadline_seconds": phase2_deadline_seconds,
            "turn_deadline_seconds": turn_deadline_seconds,
            "used_chunk_fallback": used_chunk_fallback,
        },
    )
