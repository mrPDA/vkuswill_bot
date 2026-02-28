"""Минимальный протокол сообщений для истории диалогов (ADR-006).

Независим от gigachat.models. Используется в dialog_manager,
redis_dialog_manager, dialog_history_utils.
"""

from __future__ import annotations

from typing import Any

# Минимальный формат сообщения: role и content обязательны.
# name и function_call опциональны (для assistant/tool-сообщений).
MessageT = dict[str, Any]


def msg_role(m: MessageT) -> str:
    """Роль сообщения (system, user, assistant, function)."""
    return str(m.get("role", ""))


def msg_content(m: MessageT) -> str:
    """Текстовое содержимое сообщения."""
    return str(m.get("content", ""))


def msg_name(m: MessageT) -> str | None:
    """Имя для function-сообщений (опционально)."""
    v = m.get("name")
    return str(v) if v is not None else None


def msg_function_call(m: MessageT) -> dict[str, Any] | None:
    """function_call для assistant-сообщений (опционально).

    Формат: {"name": str, "arguments": dict | str}
    """
    fc = m.get("function_call")
    if fc is None:
        return None
    if isinstance(fc, dict):
        return fc
    return None
