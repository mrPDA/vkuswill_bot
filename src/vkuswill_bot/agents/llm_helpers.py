"""LLM response parsing and message construction helpers."""

from __future__ import annotations

import contextlib
import json
import math
from typing import Any


def extract_text(message: Any) -> str:
    """Извлечь текстовое содержимое из LLM-сообщения."""
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def extract_tool_calls(message: Any) -> list[dict[str, Any]]:
    """Извлечь tool-вызовы из LLM-сообщения."""
    if isinstance(message, dict):
        raw_calls = message.get("tool_calls")
    else:
        raw_calls = getattr(message, "tool_calls", None)
    if not raw_calls:
        return []

    result: list[dict[str, Any]] = []
    for call in raw_calls:
        if isinstance(call, dict):
            fn = call.get("function", {})
            result.append(
                {
                    "id": str(call.get("id", "")),
                    "name": str(fn.get("name", "")),
                    "arguments": fn.get("arguments", "{}"),
                }
            )
            continue

        function_obj = getattr(call, "function", None)
        result.append(
            {
                "id": str(getattr(call, "id", "")),
                "name": str(getattr(function_obj, "name", "")),
                "arguments": getattr(function_obj, "arguments", "{}"),
            }
        )
    return result


def assistant_msg(message: Any) -> dict[str, Any]:
    """Построить нормализованное assistant-сообщение для history."""
    content = extract_text(message)
    tool_calls = []
    for call in extract_tool_calls(message):
        tool_calls.append(
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
        )
    payload: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


def parse_tool_args(raw_args: Any) -> dict[str, Any]:
    """Безопасно распарсить аргументы tool-вызова."""
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                return parsed
    return {}


def extract_message(response: Any) -> Any:
    """Извлечь message из OpenAI-compatible LLM-ответа."""
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        first = choices[0]
        if hasattr(first, "message"):
            return first.message
        if isinstance(first, dict):
            return first.get("message", {})
        return {}
    if isinstance(response, dict):
        choices_dict = response.get("choices")
        if isinstance(choices_dict, list) and choices_dict:
            first = choices_dict[0]
            if isinstance(first, dict):
                return first.get("message", {})
    return {}


def estimate_usage_details(
    *,
    messages: list[dict[str, Any]],
    message: dict[str, Any],
) -> dict[str, int]:
    """Fallback-оценка токенов, если провайдер не вернул usage.

    Используем грубую эвристику ~4 символа на токен для кириллицы/латиницы.
    Это лучше, чем пустое usage в Langfuse для cost-контроля.
    """
    input_chars = 0
    for item in messages:
        with contextlib.suppress(Exception):
            input_chars += len(json.dumps(item, ensure_ascii=False))

    output_chars = 0
    with contextlib.suppress(Exception):
        output_chars = len(json.dumps(message, ensure_ascii=False))

    input_tokens = max(1, math.ceil(input_chars / 4)) if input_chars > 0 else 0
    output_tokens = max(1, math.ceil(output_chars / 4)) if output_chars > 0 else 0
    total_tokens = input_tokens + output_tokens
    return {
        "input": input_tokens,
        "output": output_tokens,
        "total": total_tokens,
    }
