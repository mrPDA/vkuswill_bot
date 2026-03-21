"""Rollout policy helpers for meal-plan executor routing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_PROD_ENVIRONMENTS = {"production", "prod"}


@dataclass(slots=True)
class RolloutBypassAudit:
    enabled: bool
    environment: str
    reason: str
    actor: str
    expires_at: str
    max_ttl_seconds: int
    active: bool = False
    blocked_by: str = ""
    ttl_seconds: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "enabled": self.enabled,
            "environment": self.environment,
            "reason": self.reason,
            "actor": self.actor,
            "expires_at": self.expires_at,
            "max_ttl_seconds": self.max_ttl_seconds,
            "active": self.active,
        }
        if self.blocked_by:
            payload["blocked_by"] = self.blocked_by
        if self.ttl_seconds is not None:
            payload["ttl_seconds"] = self.ttl_seconds
        return payload


@dataclass(slots=True)
class RolloutBypassDecision:
    allow_unvalidated: bool
    audit: RolloutBypassAudit


def _parse_utc_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def evaluate_non_prod_rollout_bypass(
    *,
    enabled: bool,
    environment: str,
    reason: str,
    actor: str,
    expires_at: str,
    max_ttl_seconds: int,
    now_utc: datetime | None = None,
) -> RolloutBypassDecision:
    env = environment.strip().lower() or "production"
    now = now_utc if isinstance(now_utc, datetime) else datetime.now(UTC)
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

    reason_text = reason.strip()
    actor_text = actor.strip()
    expires_dt = _parse_utc_datetime(expires_at)
    ttl_limit = max(1, int(max_ttl_seconds))

    ttl_seconds: int | None = None
    if isinstance(expires_dt, datetime):
        ttl_seconds = int((expires_dt - now).total_seconds())

    audit = RolloutBypassAudit(
        enabled=bool(enabled),
        environment=env,
        reason=reason_text,
        actor=actor_text,
        expires_at=expires_at.strip(),
        max_ttl_seconds=ttl_limit,
    )
    if env in _PROD_ENVIRONMENTS:
        audit.blocked_by = "production_environment"
        return RolloutBypassDecision(allow_unvalidated=False, audit=audit)
    if not enabled:
        audit.blocked_by = "flag_disabled"
        return RolloutBypassDecision(allow_unvalidated=False, audit=audit)
    if not reason_text:
        audit.blocked_by = "missing_reason"
        return RolloutBypassDecision(allow_unvalidated=False, audit=audit)
    if not actor_text:
        audit.blocked_by = "missing_actor"
        return RolloutBypassDecision(allow_unvalidated=False, audit=audit)
    if expires_dt is None or ttl_seconds is None:
        audit.blocked_by = "invalid_expires_at"
        return RolloutBypassDecision(allow_unvalidated=False, audit=audit)

    audit.ttl_seconds = ttl_seconds
    if ttl_seconds <= 0:
        audit.blocked_by = "expired"
        return RolloutBypassDecision(allow_unvalidated=False, audit=audit)
    if ttl_seconds > ttl_limit:
        audit.blocked_by = "ttl_exceeds_limit"
        return RolloutBypassDecision(allow_unvalidated=False, audit=audit)

    audit.active = True
    return RolloutBypassDecision(allow_unvalidated=True, audit=audit)


async def resolve_rollout_percent(
    *,
    shadow_mode: bool,
    configured_percent: int,
    controller: Any,
    kpi_gates_enabled: bool,
    allow_unvalidated: bool,
) -> int:
    rollout_percent = int(configured_percent)
    if shadow_mode:
        return rollout_percent
    if not kpi_gates_enabled:
        return rollout_percent
    if allow_unvalidated:
        return rollout_percent
    if controller is None:
        return 0
    try:
        return await controller.resolve_rollout_percent(configured_percent=rollout_percent)
    except Exception:
        return 0


def resolve_executor_gate_reason(
    *,
    prompt_profile: str,
    executor_enabled: bool,
    shadow_mode: bool,
    rollout_percent: int,
    is_user_in_rollout: bool,
) -> str:
    if prompt_profile != "meal_plan":
        return "prompt_profile_not_meal_plan"
    if not executor_enabled:
        return "executor_disabled"
    if shadow_mode:
        return "shadow_mode_enabled"
    if rollout_percent <= 0:
        return "rollout_percent_zero"
    if not is_user_in_rollout:
        return "user_not_in_rollout_bucket"
    return "executor_enabled"
