"""Typed internal model for Meal Plan Response Contract v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ContractSlotDish:
    name: str
    audience_groups: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContractDaySlot:
    meal_type: str
    dishes: list[ContractSlotDish] = field(default_factory=list)


@dataclass(slots=True)
class ContractDayPlan:
    day: int
    slots: list[ContractDaySlot] = field(default_factory=list)


@dataclass(slots=True)
class ContractGroupAdaptation:
    group_id: str
    rules_applied: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContractRequestGroup:
    id: str
    count: int


@dataclass(slots=True)
class ContractCartProduct:
    category: str
    name: str
    quantity_text: str


@dataclass(slots=True)
class ContractCartSummary:
    items_count: int | None
    total_rub: int | None
    link: str
    total_text: str = ""
    not_found: list[str] = field(default_factory=list)
    products: list[ContractCartProduct] = field(default_factory=list)
    overflow_products: list[ContractCartProduct] = field(default_factory=list)


@dataclass(slots=True)
class ContractRequestSummary:
    days: int
    people_total: int
    groups: list[ContractRequestGroup] = field(default_factory=list)
    hard_constraints: list[str] = field(default_factory=list)
    operational_preferences: dict[str, Any] = field(default_factory=dict)
    preference_sources: dict[str, int] = field(default_factory=dict)
    applied_preferences_summary: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ContractConstraintsCheck:
    hard_constraints_passed: bool
    soft_coverage_by_group: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class MealPlanResponseContractV1:
    schema_version: int
    request_summary: ContractRequestSummary
    weekly_plan: list[ContractDayPlan]
    group_adaptations: list[ContractGroupAdaptation]
    cart_summary: ContractCartSummary
    constraints_check: ContractConstraintsCheck
    notes: list[str] = field(default_factory=list)
    fallback_message: str = ""
