"""Runtime/process flow mixin for ShoppingAgent."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from vkuswill_bot.agents.shopping_turn_executor import run_locked_turn

if TYPE_CHECKING:
    from vkuswill_bot.services.chat_engine import ProgressCallback

_DEFAULT_LLM_ROUTING_STRATEGY = "single_provider"


class ShoppingAgentRuntimeMixin:
    def _compute_providers_in_use(self) -> set[str]:
        if self._llm_routing_strategy != _DEFAULT_LLM_ROUTING_STRATEGY:
            raise ValueError(
                "ShoppingAgent supports only llm_routing_strategy='single_provider'",
            )
        return {self._llm_provider}

    async def close(self) -> None:
        """Корректно закрыть клиент."""
        for adapter in set(self._llm_adapters.values()):
            with contextlib.suppress(Exception):
                await adapter.close()

    async def reset_conversation(self, user_id: int) -> None:
        self._history.pop(user_id, None)
        self._last_cart_snapshot.pop(user_id, None)
        self._last_trace_id.pop(user_id, None)
        self._last_turn_diagnostics.pop(user_id, None)
        self._active_users.pop(user_id, None)

    async def get_last_cart_snapshot(self, user_id: int) -> dict[str, object] | None:
        return self._last_cart_snapshot.get(user_id)

    async def get_last_trace_id(self, user_id: int) -> str | None:
        return self._last_trace_id.get(user_id)

    async def get_last_turn_diagnostics(self, user_id: int) -> dict[str, object] | None:
        return self._last_turn_diagnostics.get(user_id)

    async def process_message(
        self,
        user_id: int,
        text: str,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        """Обработать сообщение пользователя с tool-loop через MCP."""
        async with self._dialog_manager.get_lock(user_id):
            self._touch_active_user(user_id)
            return await self._process_locked(
                user_id=user_id,
                text=text,
                on_progress=on_progress,
                llm_provider=self._llm_provider,
            )

    def _touch_active_user(self, user_id: int) -> None:
        """Mark user as active and prune oldest in-memory state if needed."""
        self._active_users.pop(user_id, None)
        self._active_users[user_id] = None
        while len(self._active_users) > self._max_active_users:
            stale_user_id, _ = self._active_users.popitem(last=False)
            self._history.pop(stale_user_id, None)
            self._last_cart_snapshot.pop(stale_user_id, None)
            self._last_trace_id.pop(stale_user_id, None)
            self._last_turn_diagnostics.pop(stale_user_id, None)

    async def _process_locked(
        self,
        *,
        user_id: int,
        text: str,
        on_progress: ProgressCallback | None,
        llm_provider: str,
    ) -> str:
        return await run_locked_turn(
            agent=self,
            user_id=user_id,
            text=text,
            on_progress=on_progress,
            llm_provider=llm_provider,
        )
