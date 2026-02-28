"""Tool-step processing component for shopping turn execution."""

from __future__ import annotations

from typing import Any

from vkuswill_bot.agents.shopping_tool_recovery import apply_post_step_recovery_hints
from vkuswill_bot.agents.shopping_tool_runtime_ops import (
    apply_requested_ingredient_overrides,
    execute_tool_calls,
)
from vkuswill_bot.agents.shopping_turn_contracts import ProgressReporter


class DefaultToolStepProcessor:
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
    ) -> None:
        await execute_tool_calls(
            agent=agent,
            state=state,
            message=message,
            tool_calls=tool_calls,
            user_id=user_id,
            text=text,
            llm_provider=llm_provider,
            trace=trace,
            on_progress=on_progress,
        )
        apply_post_step_recovery_hints(
            agent=agent,
            state=state,
            step=step,
            max_tool_calls=max_tool_calls,
        )


__all__ = [
    "DefaultToolStepProcessor",
    "apply_requested_ingredient_overrides",
]
