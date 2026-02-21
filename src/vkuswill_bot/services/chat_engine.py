"""Контракт chat engine для Telegram/Voice контуров.

Фаза 0 (ADR-005): фиксируем минимальный интерфейс движка диалога,
чтобы безопасно переключать runtime между legacy и ShoppingAgent.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol, runtime_checkable

# Async callback для статусов прогресса (typing indicator / промежуточный статус)
ProgressCallback = Callable[[str], Coroutine[Any, Any, None]]


@runtime_checkable
class ChatEngineProtocol(Protocol):
    """Единый контракт chat engine для handlers и voice-link API."""

    async def process_message(
        self,
        user_id: int,
        text: str,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        """Обработать сообщение пользователя и вернуть финальный текст ответа."""

    async def reset_conversation(self, user_id: int) -> None:
        """Сбросить историю диалога пользователя."""

    async def close(self) -> None:
        """Освободить ресурсы движка перед остановкой процесса."""

    async def get_last_cart_snapshot(self, user_id: int) -> dict[str, Any] | None:
        """Вернуть последний снимок корзины для post-processing и voice API."""
