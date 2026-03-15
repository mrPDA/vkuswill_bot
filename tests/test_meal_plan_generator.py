"""Tests for meal-plan LLM generator and validation/retry behavior."""

from __future__ import annotations

import json
from typing import Any

import pytest

from vkuswill_bot.agents.meal_plan_generator import generate_meal_plan
from vkuswill_bot.agents.meal_plan_types import parse_meal_plan_request


class _FakeGeneratorAgent:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._llm_max_tokens_recipe = 777
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "llm_provider": llm_provider,
                "max_tokens_override": max_tokens_override,
            }
        )
        if not self._responses:
            raise RuntimeError("No scripted LLM response")
        return self._responses.pop(0)


def _resp(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def _build_dishes(
    *, audience: str = "adults", cuisine: str = "italian", days: int = 7,
) -> list[dict[str, Any]]:
    meal_types = ["breakfast", "lunch", "dinner"]
    dishes: list[dict[str, Any]] = []
    idx = 0
    for day in range(1, days + 1):
        for mt in meal_types:
            idx += 1
            dishes.append(
                {
                    "name": f"Блюдо {idx}",
                    "day": day,
                    "meal_type": mt,
                    "servings_total": 2,
                    "audience_groups": [audience],
                    "cuisine_tags": [cuisine],
                }
            )
    return dishes


@pytest.mark.asyncio
async def test_generate_meal_plan_accepts_repaired_json_without_retry() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    dishes = _build_dishes(audience="adults", cuisine="russian")
    payload = {"schema_version": 1, "dishes": dishes}
    repaired_payload = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    agent = _FakeGeneratorAgent([_resp(repaired_payload)])

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert error == ""
    assert plan is not None
    assert plan.schema_version == 1
    assert len(plan.dishes) == 21
    assert plan.dishes[0].name == "Блюдо 1"
    assert isinstance(request.applied_preferences_trace, list)
    assert len(agent.calls) == 1
    assert agent.calls[0]["max_tokens_override"] == 777


@pytest.mark.asyncio
async def test_generate_meal_plan_retries_after_validation_error() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    invalid = {
        "schema_version": 1,
        "dishes": _build_dishes(audience="unknown_group", cuisine="italian"),
    }
    valid = {
        "schema_version": 1,
        "dishes": _build_dishes(audience="adults", cuisine="italian"),
    }
    agent = _FakeGeneratorAgent(
        [
            _resp(json.dumps(invalid, ensure_ascii=False)),
            _resp(json.dumps(valid, ensure_ascii=False)),
        ]
    )

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert error == ""
    assert plan is not None
    assert len(agent.calls) == 2
    retry_prompt = agent.calls[1]["messages"][0]["content"]
    assert "Ответ в прошлой попытке не прошел валидацию." in retry_prompt
    assert "неизвестные audience_groups" in retry_prompt


@pytest.mark.asyncio
async def test_generate_meal_plan_accepts_low_soft_coverage() -> None:
    """Low soft_coverage is a warning, not a hard rejection (BUG-1 fix)."""
    request = parse_meal_plan_request(
        "меню на неделю для 2 человек с итальянской кухней",
        {},
    )
    payload = {
        "schema_version": 1,
        "dishes": _build_dishes(audience="adults", cuisine="russian"),
    }
    agent = _FakeGeneratorAgent([_resp(json.dumps(payload, ensure_ascii=False))])

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert plan is not None
    assert error == ""
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_generate_meal_plan_returns_error_when_retry_not_json() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    agent = _FakeGeneratorAgent([_resp("это не json"), _resp("и это тоже")])

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert plan is None
    assert error == "retry не вернул валидный JSON"
    assert len(agent.calls) == 2


@pytest.mark.asyncio
async def test_generate_meal_plan_logs_phase1_violation_as_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Phase 1 (dish-name heuristic) is non-blocking for diet violations."""
    request = parse_meal_plan_request("меню на 2 дня для 2 человек, веган", {})
    meal_types = ["breakfast", "lunch", "dinner"]
    dishes = [
        {
            "name": f"Куриное блюдо {idx}",
            "day": (idx - 1) // 3 + 1,
            "meal_type": meal_types[(idx - 1) % 3],
            "servings_total": 2,
            "audience_groups": ["adults"],
            "cuisine_tags": ["russian"],
        }
        for idx in range(1, 7)
    ]
    payload = {"schema_version": 1, "dishes": dishes}
    agent = _FakeGeneratorAgent(
        [
            _resp(json.dumps(payload, ensure_ascii=False)),
        ]
    )

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert plan is not None
    assert error == ""
    assert "phase1 dish-name heuristic (non-blocking)" in caplog.text


@pytest.mark.asyncio
async def test_generate_meal_plan_rejects_invalid_dishes_count() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    payload = {"schema_version": 1, "dishes": _build_dishes()[:6]}
    agent = _FakeGeneratorAgent(
        [
            _resp(json.dumps(payload, ensure_ascii=False)),
            _resp(json.dumps(payload, ensure_ascii=False)),
        ]
    )

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert plan is None
    assert "14..21" in error


@pytest.mark.asyncio
async def test_generate_meal_plan_rejects_invalid_day_range() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    dishes = _build_dishes()
    dishes[0]["day"] = 0
    payload = {"schema_version": 1, "dishes": dishes}
    agent = _FakeGeneratorAgent(
        [
            _resp(json.dumps(payload, ensure_ascii=False)),
            _resp(json.dumps(payload, ensure_ascii=False)),
        ]
    )

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert plan is None
    assert "day вне диапазона" in error


@pytest.mark.asyncio
async def test_generate_meal_plan_rejects_invalid_meal_type() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    dishes = _build_dishes()
    dishes[0]["meal_type"] = "brunch"
    payload = {"schema_version": 1, "dishes": dishes}
    agent = _FakeGeneratorAgent(
        [
            _resp(json.dumps(payload, ensure_ascii=False)),
            _resp(json.dumps(payload, ensure_ascii=False)),
        ]
    )

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert plan is None
    assert "meal_type не поддержан" in error


@pytest.mark.asyncio
async def test_generate_meal_plan_rejects_non_positive_servings() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    dishes = _build_dishes()
    dishes[0]["servings_total"] = 0
    payload = {"schema_version": 1, "dishes": dishes}
    agent = _FakeGeneratorAgent(
        [
            _resp(json.dumps(payload, ensure_ascii=False)),
            _resp(json.dumps(payload, ensure_ascii=False)),
        ]
    )

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert plan is None
    assert "servings_total должен быть >=1" in error


@pytest.mark.asyncio
async def test_generate_meal_plan_rejects_duplicate_names_case_insensitive() -> None:
    request = parse_meal_plan_request("меню на неделю для 2 человек", {})
    dishes = _build_dishes()
    dishes[0]["name"] = "Борщ"
    dishes[1]["name"] = "борщ"
    payload = {"schema_version": 1, "dishes": dishes}
    agent = _FakeGeneratorAgent(
        [
            _resp(json.dumps(payload, ensure_ascii=False)),
            _resp(json.dumps(payload, ensure_ascii=False)),
        ]
    )

    plan, error = await generate_meal_plan(
        agent=agent,
        request=request,
        llm_provider="qwen_openai",
    )

    assert plan is None
    assert "повтор блюд не допускается" in error
