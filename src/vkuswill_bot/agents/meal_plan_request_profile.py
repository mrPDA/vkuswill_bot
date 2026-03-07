"""Profile/trace helpers for meal-plan request parsing."""

from __future__ import annotations

import re
from typing import Any


def extract_segmented_adult_preferences(
    *,
    text: str,
    segmented_pattern: re.Pattern[str],
    diet_keywords: dict[str, tuple[str, ...]],
    cuisine_keywords: dict[str, tuple[str, ...]],
) -> list[dict[str, str]]:
    low = text.lower()
    paired = segmented_pattern.search(low) is not None
    single_first = re.search(
        r"один[^.,:;]{0,80}" if paired else r"один\s+из\s+них[^.,:;]{0,80}",
        low,
    )
    second = re.search(r"(другой|второй)[^.,:;]{0,80}", low)
    if not paired and single_first is None:
        return []

    def _extract_diet(fragment: str) -> str | None:
        for diet, markers in diet_keywords.items():
            if any(marker in fragment for marker in markers):
                return diet
        return None

    def _extract_cuisine(fragment: str) -> str | None:
        for cuisine, markers in cuisine_keywords.items():
            if any(marker in fragment for marker in markers):
                return cuisine
        return None

    result: list[dict[str, str]] = []
    if single_first:
        fragment = single_first.group(0)
        diet = _extract_diet(fragment)
        cuisine = _extract_cuisine(fragment)
        if diet:
            result.append({"id": f"{diet}_user", "diet": diet})
        elif cuisine:
            result.append({"id": f"{cuisine}_user", "cuisine": cuisine})

    if second:
        fragment = second.group(0)
        diet = _extract_diet(fragment)
        cuisine = _extract_cuisine(fragment)
        if diet:
            result.append({"id": f"{diet}_user", "diet": diet})
        elif cuisine:
            result.append({"id": f"{cuisine}_user", "cuisine": cuisine})
    return result


def build_shared_constraints(
    *,
    stored_profile: dict[str, Any],
    explicit_allergens: list[str],
    explicit_diet: str | None,
    explicit_cuisines: list[str],
    merge_unique: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    hard: dict[str, Any] = {}
    soft: dict[str, Any] = {}
    operational: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []

    profile_hard = stored_profile.get("hard_constraints")
    if isinstance(profile_hard, dict):
        hard.update(profile_hard)
        trace.extend(
            {"source": "stored", "field": f"hard_constraints.{k}", "value": v}
            for k, v in profile_hard.items()
        )

    profile_soft = stored_profile.get("soft_preferences")
    if isinstance(profile_soft, dict):
        soft.update(profile_soft)
        freeform = profile_soft.get("freeform_preferences")
        if isinstance(freeform, dict):
            trace.extend(
                {
                    "source": "freeform",
                    "field": f"soft_preferences.freeform_preferences.{k}",
                    "value": v,
                }
                for k, v in freeform.items()
            )
        trace.extend(
            {"source": "stored", "field": f"soft_preferences.{k}", "value": v}
            for k, v in profile_soft.items()
            if k != "freeform_preferences"
        )

    profile_operational = stored_profile.get("operational_preferences")
    if isinstance(profile_operational, dict):
        operational.update(profile_operational)
        trace.extend(
            {"source": "stored", "field": f"operational_preferences.{k}", "value": v}
            for k, v in profile_operational.items()
        )

    if explicit_diet:
        hard["diet"] = explicit_diet
        trace.append(
            {"source": "explicit", "field": "hard_constraints.diet", "value": explicit_diet}
        )

    profile_allergens = hard.get("allergens_excluded")
    profile_allergens_list = profile_allergens if isinstance(profile_allergens, list) else []
    hard["allergens_excluded"] = merge_unique(explicit_allergens, profile_allergens_list)
    if explicit_allergens:
        trace.append(
            {
                "source": "explicit",
                "field": "hard_constraints.allergens_excluded",
                "value": explicit_allergens,
            }
        )

    profile_cuisines = soft.get("cuisines")
    soft["cuisines"] = merge_unique(
        explicit_cuisines,
        profile_cuisines if isinstance(profile_cuisines, list) else [],
    )
    if explicit_cuisines:
        trace.append(
            {
                "source": "explicit",
                "field": "soft_preferences.cuisines",
                "value": explicit_cuisines,
            }
        )

    return hard, soft, operational, trace
