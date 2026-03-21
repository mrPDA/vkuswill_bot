"""Domain types and parsers for meal-plan requests."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from vkuswill_bot.agents.meal_plan_request_model import MealPlanRequestExtraction
from vkuswill_bot.agents.meal_plan_request_profile import (
    build_shared_constraints,
    extract_segmented_adult_preferences,
)
from vkuswill_bot.services.tool_input_normalizers import normalize_colloquial_numerals

_DAYS_RE = re.compile(r"(?:на\s+)?(\d+)\s+д(?:ней|ня|ень)", flags=re.IGNORECASE)
_DAYS_WORD_RE = re.compile(
    r"(?:на\s+)?"
    r"(один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|"
    r"одиннадцать|двенадцать|тринадцать|четырнадцать)\s+д(?:ней|ня|ень)",
    flags=re.IGNORECASE,
)
_PEOPLE_RE = re.compile(r"для\s+(\d+)\s+(?:чел|человек)", flags=re.IGNORECASE)
_CHILD_COUNT_RE = re.compile(r"(\d+)\s*(?:ребен(?:ок|ка|ку|ком)|дет(?:и|ей|ям|ьми))", re.IGNORECASE)
_CHILD_AGE_RE = re.compile(r"ребен\w*[^0-9]{0,12}(\d+)\s*(?:года|лет|год|г)", re.IGNORECASE)
_ALLERGY_RE = re.compile(r"аллерг\w*\s+на\s+([^\n,.;:]+)", re.IGNORECASE)
_WITHOUT_RE = re.compile(
    r"без\s+(лактоз\w*|глютен\w*|молочн\w*|сахар\w*|мяс\w*|рыб\w*|яиц\w*|оре\w*)",
    re.IGNORECASE,
)
_SEGMENTED_ADULTS_RE = re.compile(r"один[^.]{0,60}(другой|второй)", re.IGNORECASE)

_REQUESTED_MEAL_MARKERS: dict[str, tuple[str, ...]] = {
    "breakfast": ("завтрак",),
    "lunch": ("обед",),
    "dinner": ("ужин",),
    "snack": ("перекус", "полдник"),
}

_MEAL_EXCLUSION_RE = re.compile(
    r"(?:без\s+(?:обед|завтрак|ужин|перекус)\w*"
    r"|(?:обед|завтрак|ужин|перекус)\w*\s+не\s+(?:нужн|надо|нуж)\w*)",
    re.IGNORECASE,
)

_EXCLUSION_NORMALIZE: dict[str, str] = {
    "лактоз": "лактоза",
    "глютен": "глютен",
    "молочн": "молочные",
    "сахар": "сахар",
    "мяс": "мясо",
    "рыб": "рыба",
    "яиц": "яйца",
    "оре": "орехи",
}


def dish_count_range(days: int, meals_per_day: int = 1) -> tuple[int, int]:
    """Acceptable (min, max) dish count for a given plan duration.

    When meals_per_day > 1 (e.g. breakfast+lunch+dinner=3), the range
    scales up to ensure adequate coverage across all daily slots.
    """
    if meals_per_day <= 1:
        if days <= 2:
            return 3, 6
        if days <= 7:
            return 7, 10
        return days, min(days + 4, 21)

    total_slots = days * meals_per_day
    if days <= 2:
        return max(3, total_slots - 1), total_slots
    min_dishes = max(7, total_slots * 2 // 3)
    max_dishes = min(total_slots, 28)
    return min_dishes, max_dishes


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
    requested_meal_types: list[str] = field(default_factory=list)

    @property
    def meals_per_day(self) -> int:
        if self.requested_meal_types:
            return len(self.requested_meal_types)
        return 3 if self.days >= 3 else 1

    def group_ids(self) -> set[str]:
        return {group.id for group in self.groups}

    def to_prompt_dict(self) -> dict[str, Any]:
        min_d, max_d = dish_count_range(self.days, self.meals_per_day)
        d: dict[str, Any] = {
            "people_total": self.people_total,
            "days": self.days,
            "min_dishes": min_d,
            "max_dishes": max_d,
            "groups": [group.to_prompt_dict() for group in self.groups],
            "operational_preferences": self.operational_preferences,
            "preferences_trace": self.preferences_trace,
            "applied_preferences_trace": self.applied_preferences_trace,
        }
        meal_types = self.requested_meal_types
        if not meal_types and self.days >= 3:
            meal_types = ["breakfast", "lunch", "dinner"]
        if meal_types:
            d["requested_meal_types"] = meal_types
        return d

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


_WORKING_WEEK_RE = re.compile(r"рабоч\w+\s+недел", re.IGNORECASE)
_DAYS_WORD_VALUES = {
    "один": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
}


def parse_request_days(text: str, *, default: int = 7, max_days: int = 14) -> int:
    normalized = normalize_colloquial_numerals(text).lower().replace("ё", "е")
    if _WORKING_WEEK_RE.search(normalized):
        return min(max_days, 5)
    if "на неделю" in normalized or "неделю" in normalized:
        return min(max_days, 7)

    match = _DAYS_RE.search(normalized)
    if match:
        try:
            return max(1, min(max_days, int(match.group(1))))
        except ValueError:
            return default

    match = _DAYS_WORD_RE.search(normalized)
    if not match:
        return default
    value = _DAYS_WORD_VALUES.get(match.group(1), default)
    return max(1, min(max_days, value))


def _extract_days(text: str) -> int:
    return parse_request_days(text, default=7, max_days=14)


_SOLO_RE = re.compile(
    r"для\s+(?:одного|одной|себя|меня)|на\s+одного|для\s+1\b",
    re.IGNORECASE,
)


def _extract_people_total(text: str) -> int:
    low = text.lower()
    if _SOLO_RE.search(low):
        return 1
    match = _PEOPLE_RE.search(low)
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
    low = text.lower()
    result: list[str] = []
    match = _ALLERGY_RE.search(low)
    if match:
        raw = match.group(1).strip()
        if raw:
            parts = re.split(r"\s+и\s+|,\s*", raw)
            result.extend(p.strip() for p in parts if p.strip())
    for m in _WITHOUT_RE.finditer(low):
        word = m.group(1).strip()
        for prefix, normalized in _EXCLUSION_NORMALIZE.items():
            if word.startswith(prefix):
                if normalized not in result:
                    result.append(normalized)
                break
    return result


def _extract_excluded_meal_types(text: str) -> set[str]:
    """Detect meal types the user explicitly excludes."""
    excluded: set[str] = set()
    for match in _MEAL_EXCLUSION_RE.finditer(text.lower()):
        fragment = match.group(0)
        for meal_type, markers in _REQUESTED_MEAL_MARKERS.items():
            if any(marker in fragment for marker in markers):
                excluded.add(meal_type)
    return excluded


def _extract_requested_meal_types(text: str) -> list[str]:
    """Extract meal types explicitly mentioned by the user, respecting exclusions."""
    low = text.lower()
    excluded = _extract_excluded_meal_types(text)
    types: list[str] = []
    for meal_type, markers in _REQUESTED_MEAL_MARKERS.items():
        if any(marker in low for marker in markers) and meal_type not in excluded:
            types.append(meal_type)
    return types


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


def parse_meal_plan_request(
    text: str,
    stored_profile: dict[str, Any],
    *,
    extracted: MealPlanRequestExtraction | None = None,
) -> MealPlanRequest:
    """Parse user text + stored profile into a structured MealPlanRequest."""
    days = (
        extracted.days
        if extracted is not None and isinstance(extracted.days, int)
        else _extract_days(text)
    )
    people_total = (
        extracted.people_total
        if extracted is not None and isinstance(extracted.people_total, int)
        else _extract_people_total(text)
    )
    child_group_id, child_count, child_age = _extract_child_group(text, people_total)
    if extracted is not None and (
        isinstance(extracted.child_count, int) or isinstance(extracted.child_age_years, int)
    ):
        child_count = (
            max(1, min(people_total, extracted.child_count))
            if isinstance(extracted.child_count, int)
            else child_count
        )
        child_age = (
            max(0, extracted.child_age_years)
            if isinstance(extracted.child_age_years, int)
            else child_age
        )
        child_group_id = f"child_{child_age}y" if child_age is not None else "child"
    adults_count = max(0, people_total - child_count)

    segmented_adults = extract_segmented_adult_preferences(
        text=text,
        segmented_pattern=_SEGMENTED_ADULTS_RE,
        diet_keywords=_DIET_KEYWORDS,
        cuisine_keywords=_CUISINE_KEYWORDS,
    )
    explicit_allergens = _extract_allergens(text)
    if extracted is not None and extracted.allergens_excluded is not None:
        explicit_allergens = _merge_unique(extracted.allergens_excluded, explicit_allergens)
    explicit_diet = (
        None
        if any(segment.get("diet") for segment in segmented_adults)
        else (
            extracted.diet
            if extracted is not None and extracted.diet is not None
            else _extract_diet(text)
        )
    )
    explicit_cuisines = (
        []
        if any(segment.get("cuisine") for segment in segmented_adults)
        else (
            _merge_unique(extracted.cuisines or [], _extract_cuisines(text))
            if extracted is not None and extracted.cuisines is not None
            else _extract_cuisines(text)
        )
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

    requested_meal_types = (
        _merge_unique(extracted.requested_meal_types or [], _extract_requested_meal_types(text))
        if extracted is not None and extracted.requested_meal_types is not None
        else _extract_requested_meal_types(text)
    )

    return MealPlanRequest(
        people_total=people_total,
        days=days,
        groups=groups,
        operational_preferences=operational,
        preferences_trace=preferences_trace,
        requested_meal_types=requested_meal_types,
    )
