from __future__ import annotations

import json
from typing import Any

import pytest

from vkuswill_bot.agents.meal_plan_request_extractor import parse_meal_plan_request_with_llm


class _FakeExtractorAgent:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._llm_max_tokens_recipe = 600
        self._meal_plan_request_extraction_enabled = True
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        llm_provider: str,
        max_tokens_override: int | None = None,
        temperature_override: float | None = None,
        tool_choice_override: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "llm_provider": llm_provider,
                "max_tokens_override": max_tokens_override,
                "temperature_override": temperature_override,
                "tool_choice_override": tool_choice_override,
            }
        )
        if not self._responses:
            raise RuntimeError("No scripted LLM response")
        return self._responses.pop(0)

    def _resolve_model_for_provider(self, llm_provider: str) -> str:
        _ = llm_provider
        return "test-model"


def _resp(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


@pytest.mark.asyncio
async def test_parse_meal_plan_request_with_llm_uses_model_output() -> None:
    agent = _FakeExtractorAgent(
        [
            _resp(
                json.dumps(
                    {
                        "days": 2,
                        "people_total": None,
                        "requested_meal_types": ["lunch"],
                        "child_count": None,
                        "child_age_years": None,
                        "diet": None,
                        "cuisines": [],
                        "allergens_excluded": ["глютен"],
                        "confidence": 0.96,
                        "reason": "обеды на два дня",
                    },
                    ensure_ascii=False,
                )
            )
        ]
    )

    request, debug = await parse_meal_plan_request_with_llm(
        agent=agent,
        text="собери мне обеды без глютена на два дня",
        stored_profile={},
        llm_provider="qwen_openai",
    )

    assert request.days == 2
    assert request.requested_meal_types == ["lunch"]
    assert request.groups[0].hard_constraints["allergens_excluded"] == ["глютен"]
    assert debug.source == "llm"
    assert debug.confidence == 0.96
    assert agent.calls[0]["temperature_override"] == 0.0
    assert agent.calls[0]["tool_choice_override"] == "none"


@pytest.mark.asyncio
async def test_parse_meal_plan_request_with_llm_ignores_hallucinated_people_total() -> None:
    agent = _FakeExtractorAgent(
        [
            _resp(
                json.dumps(
                    {
                        "days": 2,
                        "people_total": 2,
                        "requested_meal_types": ["lunch"],
                        "child_count": None,
                        "child_age_years": None,
                        "diet": None,
                        "cuisines": [],
                        "allergens_excluded": [],
                        "confidence": 0.91,
                        "reason": "обеды на два дня",
                    },
                    ensure_ascii=False,
                )
            )
        ]
    )

    request, debug = await parse_meal_plan_request_with_llm(
        agent=agent,
        text="собери мне обеды для здорового питания на два дня",
        stored_profile={},
        llm_provider="qwen_openai",
    )

    assert request.days == 2
    assert request.people_total == 1
    assert request.requested_meal_types == ["lunch"]
    assert debug.source == "llm"


@pytest.mark.asyncio
async def test_parse_meal_plan_request_with_llm_falls_back_to_deterministic_builder() -> None:
    agent = _FakeExtractorAgent([_resp("not json at all"), _resp("still not json")])

    request, debug = await parse_meal_plan_request_with_llm(
        agent=agent,
        text="меню на неделю для 2 человек",
        stored_profile={},
        llm_provider="qwen_openai",
    )

    assert request.days == 7
    assert request.people_total == 2
    assert debug.source == "deterministic_fallback"
    assert debug.used_retry is True
