"""Contracts and shared DTOs for shopping turn execution components."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

ProgressReporter = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class NoToolCallsOutcome:
    continue_loop: bool
    final_text: str | None = None


class ToolStepProcessor(Protocol):
    async def run_step(
        self,
        *,
        agent: Any,
        state: Any,
        message: Any,
        tool_calls: list[dict[str, Any]],
        step: int,
        user_id: int,
        text: str,
        llm_provider: str,
        trace: Any | None,
        on_progress: ProgressReporter,
        max_tool_calls: int,
    ) -> None: ...


class FinalResponseBuilder(Protocol):
    def handle_no_tool_calls(
        self,
        *,
        agent: Any,
        state: Any,
        message: Any,
        final_text: str,
        step: int,
        user_id: int,
        llm_provider: str,
        trace: Any | None,
        max_tool_calls: int,
    ) -> NoToolCallsOutcome: ...

    async def finalize_after_max_steps(
        self,
        *,
        agent: Any,
        state: Any,
        user_id: int,
        llm_provider: str,
        trace: Any | None,
        max_tool_calls: int,
        too_many_tools_error: str,
    ) -> str: ...
