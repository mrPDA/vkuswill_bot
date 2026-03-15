"""Domain types and parsers for meal-plan requests."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from vkuswill_bot.agents.meal_plan_request_profile import (
    build_shared_constraints,
    extract_segmented_adult_preferences,
)

_DAYS_RE = re.compile(r"на\s+(\d+)\s+д", flags=re.IGNORECASE)
_PEOPLE_RE = re.compile(r"для\s+(\d+)\s+(?:чел|человек)", flags=re.IGNORECASE)
_CHILD_COUNT_RE = re.compile(r"(\d+)\s*(?:ребен(?:ок|ка|ку|ком)|дет(?:и|ей|ям|ьми))", re.IGNORECASE)
_CHILD_AGE_RE = re.compile(r"ребен\w*[^0-9]{0,12}(\d+)\s*(?:года|лет|год|г)", re.IGNORECASE)
_ALLERGY_RE = re.compile(r"аллерг\w*\s+на\s+([^\n,.;:]+)", re.IGNORECASE)
_SEGMENTED_ADULTS_RE = re.compile(r"один[^.]{0,60}(другой|второй)", re.IGNORECASE)


def dish_count_range(days: int) -> tuple[int, int]:
    """Acceptable (min, max) dish count for a given plan duration."""
    if days <= 2:
        return 3, 6
    if days <= 7:
        return 7, 10
    return days, min(days + 4, 21)


_DIET_KEYWORDS = {
    "vegan": ("веган", "vegan"),
    "vegetarian": ("вегетариан", "vegetarian"),
    "halal": ("халяль", "halal"),
}
_CUISINE_KEYWORDS = {
    "italian": ("итальян", "italian"),
    "asian": ("азиат", "asian"),
    "georgian": ("грузин", "georgian"),
    "russian": ("русск", "russian"),
    "mediterranean": ("средиземномор", "mediterranean"),
}


@dataclass(slots=True)
class MealPlanRequestGroup:
    id: str
    count: int
    hard_constraints: dict[str, Any] = field(default_factory=dict)
    soft_preferences: dict[str, Any] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "count": self.count,
            "hard_constraints": self.hard_constraints,
            "soft_preferences": self.soft_preferences,
        }


@dataclass(slots=True)
class MealPlanRequest:
    people_total: int
    days: int
    groups: list[MealPlanRequestGroup]
    operational_preferences: dict[str, Any] = field(default_factory=dict)
    preferences_trace: list[dict[str, Any]] = field(default_factory=list)
    applied_preferences_trace: list[dict[str, Any]] = field(default_factory=list)

    def group_ids(self) -> set[str]:
        return {group.id for group in self.groups}

    def to_prompt_dict(self) -> dict[str, Any]:
        min_d, max_d = dish_count_range(self.days)
        return {
            "people_total": self.people_total,
            "days": self.days,
            "min_dishes": min_d,
            "max_dishes": max_d,
            "groups": [group.to_prompt_dict() for group in self.groups],
            "operational_preferences": self.operational_preferences,
            "preferences_trace": self.preferences_trace,
            "applied_preferences_trace": self.applied_preferences_trace,
        }


@dataclass(slots=True)
class MealPlanDish:
    name: str
    day: int
    meal_type: str
    servings_total: int
    audience_groups: list[str]
    cuisine_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "day": self.day,
            "meal_type": self.meal_type,
            "servings_total": self.servings_total,
            "audience_groups": list(self.audience_groups),
            "cuisine_tags": list(self.cuisine_tags),
        }


@dataclass(slots=True)
class MealPlan:
    schema_version: int
    dishes: list[MealPlanDish]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dishes": [dish.to_dict() for dish in self.dishes],
        }


def _extract_days(text: str) -> int:
    low = text.lower()
    if "на неделю" in low:
        return 7
    match = _DAYS_RE.search(low)
    if not match:
        return 7
    try:
        return max(1, min(14, int(match.group(1))))
    except ValueError:
        return 7


def _extract_people_total(text: str) -> int:
    match = _PEOPLE_RE.search(text.lower())
    if not match:
        return 2
    try:
        return max(1, min(20, int(match.group(1))))
    except ValueError:
        return 2


def _extract_child_group(text: str, people_total: int) -> tuple[str | None, int, int | None]:
    low = text.lower()
    if "реб" not in low and "дет" not in low:
        return None, 0, None
    count = 1
    count_match = _CHILD_COUNT_RE.search(low)
    if count_match and count_match.group(1).isdigit():
        count = max(1, int(count_match.group(1)))
    count = min(count, people_total)
    age_match = _CHILD_AGE_RE.search(low)
    age = int(age_match.group(1)) if age_match and age_match.group(1).isdigit() else None
    group_id = f"child_{age}y" if age is not None else "child"
    return group_id, count, age


def _extract_diet(text: str) -> str | None:
    low = text.lower()
    if _SEGMENTED_ADULTS_RE.search(low):
        return None
    for diet, markers in _DIET_KEYWORDS.items():
        if any(marker in low for marker in markers):
            return diet
    return None


def _extract_cuisines(text: str) -> list[str]:
    low = text.lower()
    if _SEGMENTED_ADULTS_RE.search(low):
        return []
    result: list[str] = []
    for cuisine, markers in _CUISINE_KEYWORDS.items():
        if any(marker in low for marker in markers):
            result.append(cuisine)
    return result


def _extract_allergens(text: str) -> list[str]:
    match = _ALLERGY_RE.search(text.lower())
    if not match:
        return []
    raw = match.group(1).strip()
    if not raw:
        return []
    parts = re.split(r"\s+и\s+|,\s*", raw)
    return [p.strip() for p in parts if p.strip()]


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in [*left, *right]:
        value = str(item).strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def parse_meal_plan_request(text: str, stored_profile: dict[str, Any]) -> MealPlanRequest:
    """Parse user text + stored profile into a structured MealPlanRequest."""
    days = _extract_days(text)
    people_total = _extract_people_total(text)
    child_group_id, child_count, child_age = _extract_child_group(text, people_total)
    adults_count = max(0, people_total - child_count)

    segmented_adults = extract_segmented_adult_preferences(
        text=text,
        segmented_pattern=_SEGMENTED_ADULTS_RE,
        diet_keywords=_DIET_KEYWORDS,
        cuisine_keywords=_CUISINE_KEYWORDS,
    )
    explicit_allergens = _extract_allergens(text)
    explicit_diet = (
        None if any(segment.get("diet") for segment in segmented_adults) else _extract_diet(text)
    )
    explicit_cuisines = (
        []
        if any(segment.get("cuisine") for segment in segmented_adults)
        else _extract_cuisines(text)
    )
    hard, soft, operational, preferences_trace = build_shared_constraints(
        stored_profile=stored_profile,
        explicit_allergens=explicit_allergens,
        explicit_diet=explicit_diet,
        explicit_cuisines=explicit_cuisines,
        merge_unique=_merge_unique,
    )

    groups: list[MealPlanRequestGroup] = []
    used_segmented = 0
    if adults_count > 0 and segmented_adults:
        for segment in segmented_adults:
            if used_segmented >= adults_count:
                break
            group_hard = dict(hard)
            group_soft = dict(soft)
            diet = segment.get("diet")
            cuisine = segment.get("cuisine")
            if diet:
                group_hard["diet"] = diet
                preferences_trace.append(
                    {
                        "source": "explicit",
                        "field": f"groups.{segment['id']}.hard_constraints.diet",
                        "value": diet,
                    }
                )
            if cuisine:
                group_soft["cuisines"] = _merge_unique([cuisine], group_soft.get("cuisines", []))
                preferences_trace.append(
                    {
                        "source": "explicit",
                        "field": f"groups.{segment['id']}.soft_preferences.cuisines",
                        "value": [cuisine],
                    }
                )
            groups.append(
                MealPlanRequestGroup(
                    id=segment["id"],
                    count=1,
                    hard_constraints=group_hard,
                    soft_preferences=group_soft,
                )
            )
            used_segmented += 1
    if adults_count > used_segmented:
        groups.append(
            MealPlanRequestGroup(
                id="adults",
                count=adults_count - used_segmented,
                hard_constraints=dict(hard),
                soft_preferences=dict(soft),
            )
        )
    if child_group_id is not None and child_count > 0:
        child_hard = dict(hard)
        child_soft = dict(soft)
        if child_age is not None and child_age <= 3:
            operational["meal_slots_child"] = max(
                int(operational.get("meal_slots_child", 5)),
                5,
            )
            child_soft["mild_spice"] = True
        groups.append(
            MealPlanRequestGroup(
                id=child_group_id,
                count=child_count,
                hard_constraints=child_hard,
                soft_preferences=child_soft,
            )
        )

    if not groups:
        groups.append(
            MealPlanRequestGroup(
                id="all",
                count=people_total,
                hard_constraints=dict(hard),
                soft_preferences=dict(soft),
            )
        )

    return MealPlanRequest(
        people_total=people_total,
        days=days,
        groups=groups,
        operational_preferences=operational,
        preferences_trace=preferences_trace,
    )
