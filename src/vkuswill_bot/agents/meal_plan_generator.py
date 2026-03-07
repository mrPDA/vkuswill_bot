"""LLM meal-plan generation with schema validation and single retry."""

from __future__ import annotations

import re
from typing import Any, Protocol

from vkuswill_bot.agents.llm_helpers import extract_message, extract_text
from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.agents.meal_plan_quality import (
    build_applied_preferences_trace,
    calculate_soft_coverage,
    format_soft_coverage_error,
    low_soft_coverage_groups,
    validate_hard_constraints_with_trace,
)
from vkuswill_bot.agents.meal_plan_types import (
    MealPlan,
    MealPlanDish,
    MealPlanRequest,
)
from vkuswill_bot.services.prompts import get_meal_plan_generation_prompt

_MEAL_TYPES = frozenset({"breakfast", "lunch", "dinner", "snack_1", "snack_2", "snack_3"})


class MealPlanGeneratorAgentProtocol(Protocol):
    _llm_max_tokens_recipe: int

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
        max_tokens_override: int | None = None,
    ) -> Any: ...


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    payload = parse_json_payload(raw)
    if isinstance(payload, dict) and payload:
        return payload

    # Fallback: extract first JSON object candidate from free-form text.
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = raw[start : end + 1]
    with_value = parse_json_payload(candidate)
    if isinstance(with_value, dict) and with_value:
        return with_value
    return None


def _repair_json_text(raw: str) -> dict[str, Any] | None:
    cleaned = raw.strip()
    if not cleaned:
        return None
    # Remove markdown fences and trailing commas before closing braces/brackets.
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return _extract_json_object(cleaned)


def _validate_meal_plan_payload(
    payload: dict[str, Any],
    request: MealPlanRequest,
) -> tuple[MealPlan | None, str]:
    schema_version = payload.get("schema_version")
    if schema_version != 1:
        return None, "schema_version должен быть 1"

    dishes_raw = payload.get("dishes")
    if not isinstance(dishes_raw, list):
        return None, "dishes должен быть списком"
    if not (7 <= len(dishes_raw) <= 10):
        return None, "dishes должен содержать 7..10 блюд"

    group_ids = request.group_ids()
    result_dishes: list[MealPlanDish] = []
    seen_names: set[str] = set()

    for row in dishes_raw:
        if not isinstance(row, dict):
            return None, "каждый элемент dishes должен быть объектом"
        name = str(row.get("name", "")).strip()
        if not name:
            return None, "name обязателен"
        marker = name.lower()
        if marker in seen_names:
            return None, f"повтор блюд не допускается: {name}"
        seen_names.add(marker)

        try:
            day = int(row.get("day", 0))
        except (TypeError, ValueError):
            return None, f"day должен быть числом для блюда {name}"
        if not (1 <= day <= request.days):
            return None, f"day вне диапазона 1..{request.days} для блюда {name}"

        meal_type = str(row.get("meal_type", "")).strip().lower()
        if meal_type not in _MEAL_TYPES:
            return None, f"meal_type не поддержан для блюда {name}"

        try:
            servings_total = int(row.get("servings_total", 0))
        except (TypeError, ValueError):
            return None, f"servings_total должен быть числом для блюда {name}"
        if servings_total < 1:
            return None, f"servings_total должен быть >=1 для блюда {name}"

        audience_raw = row.get("audience_groups")
        if not isinstance(audience_raw, list) or not audience_raw:
            return None, f"audience_groups обязателен для блюда {name}"
        audience = [str(group).strip() for group in audience_raw if str(group).strip()]
        if not audience:
            return None, f"audience_groups пустой для блюда {name}"
        unknown = [group for group in audience if group not in group_ids]
        if unknown:
            return None, f"неизвестные audience_groups для {name}: {', '.join(unknown)}"

        cuisine_tags_raw = row.get("cuisine_tags")
        cuisine_tags = (
            [str(tag).strip().lower() for tag in cuisine_tags_raw if str(tag).strip()]
            if isinstance(cuisine_tags_raw, list)
            else []
        )
        result_dishes.append(
            MealPlanDish(
                name=name,
                day=day,
                meal_type=meal_type,
                servings_total=servings_total,
                audience_groups=audience,
                cuisine_tags=cuisine_tags,
            )
        )

    meal_plan = MealPlan(schema_version=1, dishes=result_dishes)
    hard_violations, phase1_trace = validate_hard_constraints_with_trace(
        request=request,
        dishes=meal_plan.dishes,
    )
    if hard_violations:
        details = "; ".join(hard_violations[:3])
        return None, f"hard_constraints violated: {details}"
    coverage = calculate_soft_coverage(request=request, dishes=meal_plan.dishes)
    low_groups = low_soft_coverage_groups(coverage_by_group=coverage)
    if low_groups:
        return None, format_soft_coverage_error(low_groups=low_groups)
    request.applied_preferences_trace = build_applied_preferences_trace(
        request=request,
        phase1_applied_trace=phase1_trace,
        soft_coverage_by_group=coverage,
    )

    return meal_plan, ""


async def generate_meal_plan(
    *,
    agent: MealPlanGeneratorAgentProtocol,
    request: MealPlanRequest,
    llm_provider: str,
) -> tuple[MealPlan | None, str]:
    """Generate meal-plan JSON with one repair and one retry."""
    request_payload = request.to_prompt_dict()
    prompt = get_meal_plan_generation_prompt(
        request_payload=request_payload,
    )
    messages = [{"role": "user", "content": prompt}]
    response = await agent._call_llm(
        messages=messages,
        tools=[],
        llm_provider=llm_provider,
        max_tokens_override=getattr(agent, "_llm_max_tokens_recipe", None),
    )
    message = extract_message(response)
    raw_text = extract_text(message).strip()

    payload = _extract_json_object(raw_text) or _repair_json_text(raw_text)
    if isinstance(payload, dict):
        parsed, error = _validate_meal_plan_payload(payload, request)
        if parsed is not None:
            return parsed, ""
    else:
        error = "LLM не вернул JSON-объект"

    retry_prompt = (
        f"{prompt}\n\n"
        "Ответ в прошлой попытке не прошел валидацию.\n"
        f"Ошибка: {error}\n"
        "Верни только валидный JSON без пояснений."
    )
    retry_messages = [{"role": "user", "content": retry_prompt}]
    retry_response = await agent._call_llm(
        messages=retry_messages,
        tools=[],
        llm_provider=llm_provider,
        max_tokens_override=getattr(agent, "_llm_max_tokens_recipe", None),
    )
    retry_message = extract_message(retry_response)
    retry_text = extract_text(retry_message).strip()
    retry_payload = _extract_json_object(retry_text) or _repair_json_text(retry_text)
    if not isinstance(retry_payload, dict):
        return None, "retry не вернул валидный JSON"
    parsed_retry, retry_error = _validate_meal_plan_payload(retry_payload, request)
    if parsed_retry is None:
        return None, retry_error
    return parsed_retry, ""
