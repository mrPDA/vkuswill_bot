"""Unit tests for Langfuse tracing wrapper."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.services.langfuse_tracing import LangfuseGeneration


class _GenerationSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def end(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def test_langfuse_generation_builds_legacy_usage_with_snake_case_costs() -> None:
    spy = _GenerationSpy()
    generation = LangfuseGeneration(spy)

    generation.end(
        output="ok",
        usage_details={"input": 100, "output": 20, "total": 120},
        cost_details={"input": 0.1, "output": 0.05, "total": 0.15},
    )

    assert spy.calls
    payload = spy.calls[-1]
    assert payload["usage_details"] == {"input": 100, "output": 20, "total": 120}
    assert payload["cost_details"] == {"input": 0.1, "output": 0.05, "total": 0.15}
    assert payload["usage"]["input"] == 100
    assert payload["usage"]["output"] == 20
    assert payload["usage"]["total"] == 120
    assert payload["usage"]["unit"] == "TOKENS"
    assert payload["usage"]["input_cost"] == 0.1
    assert payload["usage"]["output_cost"] == 0.05
    assert payload["usage"]["total_cost"] == 0.15
