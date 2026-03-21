"""LLM-first extraction of structured meal-plan request parameters."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from vkuswill_bot.agents.llm_helpers import estimate_usage_details, extract_message, extract_text
from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.agents.meal_plan_request_model import MealPlanRequestExtraction
from vkuswill_bot.agents.meal_plan_types import (
    MealPlanRequest,
    parse_meal_plan_request,
)
from vkuswill_bot.services.llm_adapters import extract_usage_details
from vkuswill_bot.services.prompts import get_meal_plan_request_parse_prompt_with_metadata

logger = logging.getLogger(__name__)

_MEAL_PLAN_REQUEST_PARSE_MAX_TOKENS = 320
_MEAL_PLAN_REQUEST_PARSE_TEMPERATURE = 0.0
_VALID_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
_VALID_DIETS = {"vegan", "vegetarian", "halal"}
_VALID_CUISINES = {"italian", "asian", "georgian", "russian", "mediterranean"}


class MealPlanRequestExtractorAgentProtocol(Protocol):
    _llm_max_tokens_recipe: int

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
        max_tokens_override: int | None = None,
        temperature_override: float | None = None,
        tool_choice_override: str | None = None,
    ) -> Any: ...


@dataclass(slots=True)
class MealPlanRequestParseDebug:
    source: str
    raw_output: str = ""
    confidence: float | None = None
    reason: str | None = None
    used_retry: bool = False
    prompt_metadata: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "used_retry": self.used_retry,
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.reason:
            payload["reason"] = self.reason
        if self.raw_output:
            payload["raw_preview"] = self.raw_output[:300]
        if self.error_type:
            payload["error_type"] = self.error_type
        if self.error_message:
            payload["error_message"] = self.error_message
        if self.prompt_metadata:
            payload["prompt"] = self.prompt_metadata
        return payload


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    payload = parse_json_payload(raw)
    if isinstance(payload, dict) and payload:
        return payload

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
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return _extract_json_object(cleaned)


def _normalize_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    if numeric < minimum or numeric > maximum:
        return None
    return numeric


def _normalize_str_list(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item).strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return max(0.0, min(1.0, numeric))


def _parse_extraction_payload(
    payload: dict[str, Any],
) -> tuple[MealPlanRequestExtraction, float | None, str | None]:
    requested_meal_types_raw = _normalize_str_list(payload.get("requested_meal_types"))
    requested_meal_types = (
        [value for value in requested_meal_types_raw if value in _VALID_MEAL_TYPES]
        if requested_meal_types_raw is not None
        else None
    )
    cuisines_raw = _normalize_str_list(payload.get("cuisines"))
    cuisines = (
        [value for value in cuisines_raw if value in _VALID_CUISINES]
        if cuisines_raw is not None
        else None
    )
    allergens = _normalize_str_list(payload.get("allergens_excluded"))
    diet_raw = str(payload.get("diet", "")).strip().lower()
    diet = diet_raw if diet_raw in _VALID_DIETS else None
    extraction = MealPlanRequestExtraction(
        days=_normalize_int(payload.get("days"), minimum=1, maximum=14),
        people_total=_normalize_int(payload.get("people_total"), minimum=1, maximum=20),
        requested_meal_types=requested_meal_types,
        child_count=_normalize_int(payload.get("child_count"), minimum=1, maximum=20),
        child_age_years=_normalize_int(payload.get("child_age_years"), minimum=0, maximum=17),
        diet=diet,
        cuisines=cuisines,
        allergens_excluded=allergens,
    )
    if (
        extraction.child_count is not None
        and extraction.people_total is not None
        and extraction.child_count > extraction.people_total
    ):
        extraction.child_count = extraction.people_total
    reason = payload.get("reason")
    reason_text = str(reason).strip() if isinstance(reason, str) and reason.strip() else None
    return extraction, _normalize_confidence(payload.get("confidence")), reason_text


def _parse_model_output(
    raw: str,
) -> tuple[MealPlanRequestExtraction | None, float | None, str | None]:
    payload = _extract_json_object(raw) or _repair_json_text(raw)
    if not isinstance(payload, dict):
        return None, None, None
    return _parse_extraction_payload(payload)


async def parse_meal_plan_request_with_llm(
    *,
    agent: MealPlanRequestExtractorAgentProtocol,
    text: str,
    stored_profile: dict[str, Any],
    llm_provider: str,
    trace: Any | None = None,
) -> tuple[MealPlanRequest, MealPlanRequestParseDebug]:
    """Extract request parameters with LLM first, then build the request deterministically."""
    if not getattr(agent, "_meal_plan_request_extraction_enabled", False):
        return parse_meal_plan_request(text, stored_profile), MealPlanRequestParseDebug(
            source="deterministic_disabled",
        )

    prompt, prompt_metadata = get_meal_plan_request_parse_prompt_with_metadata(text=text)
    messages = [{"role": "user", "content": prompt}]
    max_tokens = getattr(agent, "_llm_max_tokens_recipe", None)
    if isinstance(max_tokens, int):
        max_tokens = min(max_tokens, _MEAL_PLAN_REQUEST_PARSE_MAX_TOKENS)
    else:
        max_tokens = _MEAL_PLAN_REQUEST_PARSE_MAX_TOKENS

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
            name="meal-plan-request-extraction",
            model=model,
            input=messages,
            model_parameters={
                "provider": llm_provider,
                "attempt": 1,
                "temperature": _MEAL_PLAN_REQUEST_PARSE_TEMPERATURE,
                "max_tokens": max_tokens,
            },
            metadata={"prompt": prompt_metadata},
        )
        if trace is not None
        else None
    )

    async def _call(
        prompt_text: str,
    ) -> tuple[
        MealPlanRequestExtraction | None,
        float | None,
        str | None,
        str,
        dict[str, int] | None,
    ]:
        response = await agent._call_llm(
            messages=[{"role": "user", "content": prompt_text}],
            tools=[],
            llm_provider=llm_provider,
            max_tokens_override=max_tokens,
            temperature_override=_MEAL_PLAN_REQUEST_PARSE_TEMPERATURE,
            tool_choice_override="none",
        )
        message = extract_message(response)
        raw_output = extract_text(message).strip()
        usage = extract_usage_details(response) or estimate_usage_details(
            messages=[{"role": "user", "content": prompt_text}],
            message=message if isinstance(message, dict) else {},
        )
        extraction, confidence, reason = _parse_model_output(raw_output)
        return extraction, confidence, reason, raw_output, usage

    try:
        extraction, confidence, reason, raw_output, usage = await _call(prompt)
        used_retry = False
        if extraction is None:
            retry_prompt = (
                f"{prompt}\n\n"
                "Предыдущий ответ не удалось распарсить.\n"
                "Верни только валидный JSON-объект без markdown и комментариев."
            )
            extraction, confidence, reason, raw_output, usage = await _call(retry_prompt)
            used_retry = True
        if extraction is not None:
            request = parse_meal_plan_request(text, stored_profile, extracted=extraction)
            if generation is not None:
                generation.end(
                    output={
                        "days": request.days,
                        "people_total": request.people_total,
                        "requested_meal_types": request.requested_meal_types,
                        "source": "llm",
                        "confidence": confidence,
                        "reason": reason,
                    },
                    usage_details=usage,
                    metadata={"prompt": prompt_metadata},
                )
            return request, MealPlanRequestParseDebug(
                source="llm",
                raw_output=raw_output,
                confidence=confidence,
                reason=reason,
                used_retry=used_retry,
                prompt_metadata=prompt_metadata,
            )
        request = parse_meal_plan_request(text, stored_profile)
        if generation is not None:
            generation.end(
                output={
                    "days": request.days,
                    "people_total": request.people_total,
                    "requested_meal_types": request.requested_meal_types,
                    "source": "deterministic_fallback",
                },
                level="WARNING",
                status_message="meal_plan_request_extraction_invalid_fallback",
                usage_details=usage,
                metadata={"prompt": prompt_metadata},
            )
        return request, MealPlanRequestParseDebug(
            source="deterministic_fallback",
            raw_output=raw_output,
            used_retry=used_retry,
            prompt_metadata=prompt_metadata,
            error_type="invalid_output",
            error_message="llm_request_extraction_invalid_json",
        )
    except Exception as exc:
        request = parse_meal_plan_request(text, stored_profile)
        logger.warning("meal-plan request extraction fell back to deterministic parse: %s", exc)
        if generation is not None:
            generation.end(
                output=str(exc),
                level="WARNING",
                status_message="meal_plan_request_extraction_failed_fallback",
                metadata={
                    "prompt": prompt_metadata,
                    "fallback_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                },
            )
        return request, MealPlanRequestParseDebug(
            source="deterministic_exception_fallback",
            prompt_metadata=prompt_metadata,
            error_type=type(exc).__name__,
            error_message=str(exc)[:240],
        )
