"""Управление LLM history: обрезка, реcompaction, санитизация."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from vkuswill_bot.agents.tool_result_compactor import ToolResultCompactor


def trim_history(
    history: list[dict[str, Any]],
    *,
    max_history: int,
) -> list[dict[str, Any]]:
    """Обрезать историю по количеству сообщений, сохраняя system-первым."""
    if len(history) <= max_history:
        return history
    system_msg = history[0]
    tail = history[-(max_history - 1) :]
    return [system_msg, *tail]


def trim_history_by_chars(
    history: list[dict[str, Any]],
    *,
    max_history_chars: int,
    compactor: ToolResultCompactor,
) -> list[dict[str, Any]]:
    """Обрезать историю по символьному бюджету с сохранением system-первым."""
    if not history:
        return history

    trimmed = recompact_tool_history(list(history), compactor=compactor)
    while history_char_count(trimmed) > max_history_chars and len(trimmed) > 2:
        del trimmed[1]
        trimmed = sanitize_tool_history(trimmed)
    return trimmed


def recompact_tool_history(
    history: list[dict[str, Any]],
    *,
    compactor: ToolResultCompactor,
) -> list[dict[str, Any]]:
    """Повторно сжать старые tool-сообщения в истории."""
    if len(history) <= 2:
        return history

    compacted = [history[0]]
    seen_tool_signatures: set[str] = set()
    for message in history[1:]:
        if message.get("role") != "tool":
            compacted.append(message)
            continue

        name = str(message.get("name", "")).strip()
        content = message.get("content")
        if not name or not isinstance(content, str):
            compacted.append(message)
            continue

        compact_content = compactor.prepare_tool_result_for_history(name, content)
        signature = f"{name}:{compact_content}"
        if signature in seen_tool_signatures:
            compact_content = compactor.build_cached_tool_stub(
                tool_name=name,
                compact_content=compact_content,
            )
        else:
            seen_tool_signatures.add(signature)

        compacted.append({**message, "content": compact_content})
    return compacted


def sanitize_tool_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Удалить tool-сообщения без предшествующего assistant + tool_calls."""
    if len(history) <= 2:
        return history

    sanitized = [history[0]]
    for msg in history[1:]:
        if msg.get("role") != "tool":
            sanitized.append(msg)
            continue

        prev = sanitized[-1] if sanitized else None
        if not isinstance(prev, dict):
            continue
        tool_calls = prev.get("tool_calls")
        if prev.get("role") == "assistant" and isinstance(tool_calls, list) and tool_calls:
            sanitized.append(msg)
    return sanitized


def history_char_count(history: list[dict[str, Any]]) -> int:
    """Посчитать общее количество символов в сериализованной истории."""
    total = 0
    for message in history:
        with contextlib.suppress(Exception):
            total += len(json.dumps(message, ensure_ascii=False))
    return total
