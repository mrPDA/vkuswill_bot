"""LLM meal-plan generation with schema validation and single retry."""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from vkuswill_bot.agents.llm_helpers import (
    estimate_usage_details,
    extract_message,
    extract_text,
)
from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.agents.meal_plan_quality import (
    build_applied_preferences_trace,
    calculate_soft_coverage,
    low_soft_coverage_groups,
    validate_hard_constraints_with_trace,
)
from vkuswill_bot.agents.meal_plan_types import (
    MealPlan,
    MealPlanDish,
    MealPlanRequest,
    dish_count_range,
)
from vkuswill_bot.services.llm_adapters import extract_usage_details
from vkuswill_bot.services.prompts import get_meal_plan_generation_prompt_with_metadata

logger = logging.getLogger(__name__)

_MEAL_TYPES = frozenset({"breakfast", "lunch", "dinner", "snack_1", "snack_2", "snack_3"})
_MEAL_PLAN_GENERATION_MAX_TOKENS = 2400


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
    min_dishes, max_dishes = dish_count_range(request.days, request.meals_per_day)
    if not (min_dishes <= len(dishes_raw) <= max_dishes):
        return None, f"dishes должен содержать {min_dishes}..{max_dishes} блюд"

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

    if request.days >= 3:
        covered_days = {dish.day for dish in result_dishes}
        missing_days = set(range(1, request.days + 1)) - covered_days
        if missing_days:
            return None, f"не все дни покрыты: отсутствуют дни {sorted(missing_days)}"

    effective_meal_types = request.requested_meal_types
    if not effective_meal_types and request.days >= 3:
        effective_meal_types = ["breakfast", "lunch", "dinner"]
    total_slots = request.days * len(effective_meal_types) if effective_meal_types else 0
    can_enforce_full_coverage = len(result_dishes) >= total_slots
    if effective_meal_types and request.days >= 3 and can_enforce_full_coverage:
        by_day: dict[int, set[str]] = {}
        for dish in result_dishes:
            by_day.setdefault(dish.day, set()).add(dish.meal_type)
        gaps: list[str] = []
        for day_num in range(1, request.days + 1):
            day_types = by_day.get(day_num, set())
            for mt in effective_meal_types:
                if mt not in day_types:
                    gaps.append(f"день {day_num} без {mt}")
        if gaps:
            sample = "; ".join(gaps[:5])
            return None, f"не все приёмы пищи покрыты: {sample}"

    meal_plan = MealPlan(schema_version=1, dishes=result_dishes)
    hard_violations, phase1_trace = validate_hard_constraints_with_trace(
        request=request,
        dishes=meal_plan.dishes,
    )
    allergen_violations = [v for v in hard_violations if "аллерген" in v]
    if allergen_violations:
        details = "; ".join(allergen_violations[:5])
        return None, f"блюда нарушают диетические ограничения: {details}"
    non_allergen_violations = [v for v in hard_violations if "аллерген" not in v]
    if non_allergen_violations:
        details = "; ".join(non_allergen_violations[:3])
        logger.warning("phase1 dish-name heuristic (non-blocking): %s", details)
    coverage = calculate_soft_coverage(request=request, dishes=meal_plan.dishes)
    low_groups = low_soft_coverage_groups(coverage_by_group=coverage)
    if low_groups:
        logger.warning("soft_preferences coverage below target: %s", low_groups)
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
    trace: Any | None = None,
) -> tuple[MealPlan | None, str]:
    """Generate meal-plan JSON with one repair and one retry."""
    request_payload = request.to_prompt_dict()
    prompt, prompt_metadata = get_meal_plan_generation_prompt_with_metadata(
        request_payload=request_payload,
    )
    max_tokens = getattr(agent, "_llm_max_tokens_recipe", None)
    if isinstance(max_tokens, int):
        max_tokens = min(max_tokens, _MEAL_PLAN_GENERATION_MAX_TOKENS)
    else:
        max_tokens = _MEAL_PLAN_GENERATION_MAX_TOKENS
    messages = [{"role": "user", "content": prompt}]
    model = llm_provider
    resolve_model = getattr(agent, "_resolve_model_for_provider", None)
    if callable(resolve_model):
        try:
            resolved_model = resolve_model(llm_provider)
        except Exception:
            resolved_model = None
        if isinstance(resolved_model, str) and resolved_model.strip():
            model = resolved_model.strip()
    generation = (
        trace.generation(
            name="meal-plan-generation",
            model=model,
            input=messages,
            model_parameters={
                "provider": llm_provider,
                "attempt": 1,
                "temperature": 0.0,
                "max_tokens": max_tokens,
            },
            metadata={"prompt": prompt_metadata},
        )
        if trace is not None
        else None
    )
    response = await agent._call_llm(
        messages=messages,
        tools=[],
        llm_provider=llm_provider,
        max_tokens_override=max_tokens,
    )
    message = extract_message(response)
    raw_text = extract_text(message).strip()
    usage_details = extract_usage_details(response) or estimate_usage_details(
        messages=messages,
        message=message if isinstance(message, dict) else {},
    )

    payload = _extract_json_object(raw_text) or _repair_json_text(raw_text)
    if isinstance(payload, dict):
        parsed, error = _validate_meal_plan_payload(payload, request)
        if parsed is not None:
            if generation is not None:
                generation.end(
                    output=raw_text[:5000],
                    usage_details=usage_details,
                    metadata={"validation": "passed", "attempt": 1},
                )
            return parsed, ""
    else:
        error = "LLM не вернул JSON-объект"
    if generation is not None:
        generation.end(
            output=raw_text[:5000],
            usage_details=usage_details,
            metadata={"validation": "failed", "attempt": 1, "validation_error": error},
            level="WARNING",
            status_message="meal_plan_generation_retry",
        )

    retry_prompt = (
        f"{prompt}\n\n"
        "Ответ в прошлой попытке не прошел валидацию.\n"
        f"Ошибка: {error}\n"
        "Верни только валидный JSON без пояснений."
    )
    retry_messages = [{"role": "user", "content": retry_prompt}]
    retry_generation = (
        trace.generation(
            name="meal-plan-generation-retry",
            model=model,
            input=retry_messages,
            model_parameters={
                "provider": llm_provider,
                "attempt": 2,
                "temperature": 0.0,
                "max_tokens": max_tokens,
            },
            metadata={"prompt": prompt_metadata, "retry_reason": error},
        )
        if trace is not None
        else None
    )
    retry_response = await agent._call_llm(
        messages=retry_messages,
        tools=[],
        llm_provider=llm_provider,
        max_tokens_override=max_tokens,
    )
    retry_message = extract_message(retry_response)
    retry_text = extract_text(retry_message).strip()
    retry_usage = extract_usage_details(retry_response) or estimate_usage_details(
        messages=retry_messages,
        message=retry_message if isinstance(retry_message, dict) else {},
    )
    retry_payload = _extract_json_object(retry_text) or _repair_json_text(retry_text)
    if not isinstance(retry_payload, dict):
        if retry_generation is not None:
            retry_generation.end(
                output=retry_text[:5000],
                usage_details=retry_usage,
                metadata={"validation": "failed", "attempt": 2},
                level="ERROR",
                status_message="retry invalid json",
            )
        return None, "retry не вернул валидный JSON"
    parsed_retry, retry_error = _validate_meal_plan_payload(retry_payload, request)
    if parsed_retry is None:
        if retry_generation is not None:
            retry_generation.end(
                output=retry_text[:5000],
                usage_details=retry_usage,
                metadata={"validation": "failed", "attempt": 2, "validation_error": retry_error},
                level="ERROR",
                status_message="retry validation failed",
            )
        return None, retry_error
    if retry_generation is not None:
        retry_generation.end(
            output=retry_text[:5000],
            usage_details=retry_usage,
            metadata={"validation": "passed", "attempt": 2},
        )
    return parsed_retry, ""
