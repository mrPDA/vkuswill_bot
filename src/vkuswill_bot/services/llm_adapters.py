"""LLM adapters for ShoppingAgent.

Normalize provider-specific SDK responses into one OpenAI-like shape:
{
  "choices": [{"message": {"content": str, "tool_calls": [...]}}]
}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from typing import Any, Protocol, runtime_checkable

_INLINE_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

DEFAULT_LLM_PROVIDER = "qwen_openai"


def normalize_llm_provider(value: str) -> str:
    """Normalize provider aliases to canonical values."""
    normalized = value.strip().lower()
    aliases = {
        "qwen": "qwen_openai",
        "qwen_openai": "qwen_openai",
        "openai": "qwen_openai",
        "openai_compatible": "qwen_openai",
    }
    return aliases.get(normalized, normalized)


@runtime_checkable
class LLMAdapterProtocol(Protocol):
    """Unified interface for provider SDK adapters."""

    async def create_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Create one chat completion and return normalized response."""

    async def close(self) -> None:
        """Release resources if needed."""


class OpenAICompatibleLLMAdapter:
    """Adapter for OpenAI-compatible chat APIs (Qwen via YC AI Studio)."""

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        timeout_seconds: float = 30.0,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return

        try:
            from openai import AsyncOpenAI
        except Exception as exc:
            msg = "ShoppingAgent requires package openai>=1.0 for OpenAI-compatible provider."
            raise RuntimeError(msg) from exc

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=max(1.0, timeout_seconds),
            max_retries=0,
        )

    async def create_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if temperature is not None:
            request["temperature"] = temperature
        response = await self._client.chat.completions.create(
            **request,
        )
        return normalize_chat_response(response)

    async def close(self) -> None:
        close_method = getattr(self._client, "close", None)
        if close_method is None:
            return
        result = close_method()
        if asyncio.iscoroutine(result):
            with contextlib.suppress(Exception):
                await result


def create_llm_adapter(
    *,
    provider: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_timeout_seconds: float,
) -> LLMAdapterProtocol:
    """Create adapter for a configured provider."""
    normalized = normalize_llm_provider(provider)
    if normalized == "qwen_openai":
        return OpenAICompatibleLLMAdapter(
            api_key=llm_api_key,
            base_url=llm_base_url,
            timeout_seconds=llm_timeout_seconds,
        )
    raise ValueError(f"Unsupported llm provider: {provider}")


def normalize_chat_response(response: Any) -> dict[str, Any]:
    """Normalize chat completion response to OpenAI-like dict."""
    message = _extract_message(response)
    content = _normalize_text_content(_extract_content(message))
    tool_calls = _normalize_tool_calls(_extract_tool_calls(message))
    if not tool_calls and content:
        parsed_tool_calls, cleaned_content = _extract_inline_tool_calls(content)
        if parsed_tool_calls:
            tool_calls = parsed_tool_calls
            content = cleaned_content
    usage = extract_usage_details(response)
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                    "tool_calls": tool_calls,
                }
            }
        ],
        "usage": usage,
    }


def _extract_message(response: Any) -> Any:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        first = choices[0]
        if hasattr(first, "message"):
            return first.message
        if isinstance(first, dict):
            return first.get("message", {})
    if isinstance(response, dict):
        choices_dict = response.get("choices")
        if isinstance(choices_dict, list) and choices_dict:
            first = choices_dict[0]
            if isinstance(first, dict):
                return first.get("message", {})
    return {}


def _extract_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _extract_tool_calls(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("tool_calls")
    return getattr(message, "tool_calls", None)


def _extract_inline_tool_calls(content: str) -> tuple[list[dict[str, Any]], str]:
    """Parse textual ``<tool_call>...</tool_call>`` blocks from model content."""
    matches = list(_INLINE_TOOL_CALL_RE.finditer(content))
    if not matches:
        return [], content

    parsed_calls: list[dict[str, Any]] = []
    for match in matches:
        parsed = _parse_inline_tool_call_payload(match.group(1))
        if parsed is not None:
            parsed_calls.append(parsed)

    if not parsed_calls:
        return [], content

    cleaned_content = _INLINE_TOOL_CALL_RE.sub("", content).strip()
    return parsed_calls, cleaned_content


def _parse_inline_tool_call_payload(raw_payload: str) -> dict[str, Any] | None:
    with contextlib.suppress(Exception):
        payload = json.loads(raw_payload.strip())
        if not isinstance(payload, dict):
            return None

        name = str(payload.get("name", "")).strip()
        if not name:
            return None

        if "arguments" in payload:
            arguments = payload.get("arguments")
        elif "params" in payload:
            arguments = payload.get("params")
        else:
            arguments = {k: v for k, v in payload.items() if k != "name"}

        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)

        return {
            "id": f"inline-{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            },
        }
    return None


def extract_usage_details(response: Any) -> dict[str, int] | None:
    """Extract usage details from provider response into canonical shape."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None

    usage_dict = _usage_to_dict(usage)
    if usage_dict is None:
        return None

    prompt = _pick_usage_value(
        usage_dict,
        "input",
        "prompt_tokens",
        "promptTokens",
        "input_tokens",
        "inputTokens",
        "input_text_tokens",
        "inputTextTokens",
    )
    completion = _pick_usage_value(
        usage_dict,
        "output",
        "completion_tokens",
        "completionTokens",
        "output_tokens",
        "outputTokens",
        "output_text_tokens",
        "outputTextTokens",
        "generated_tokens",
    )
    total = _pick_usage_value(
        usage_dict,
        "total",
        "total_tokens",
        "totalTokens",
        "token_count",
        "tokenCount",
    )
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion

    result: dict[str, int] = {}
    if prompt is not None:
        result["input"] = prompt
    if completion is not None:
        result["output"] = completion
    if total is not None:
        result["total"] = total
    return result or None


def _extract_usage_details(response: Any) -> dict[str, int] | None:
    """Backward-compatible alias."""
    return extract_usage_details(response)


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if isinstance(usage, dict):
        return usage

    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        with contextlib.suppress(Exception):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped

    to_dict = getattr(usage, "dict", None)
    if callable(to_dict):
        with contextlib.suppress(Exception):
            dumped = to_dict()
            if isinstance(dumped, dict):
                return dumped

    values: dict[str, Any] = {}
    for key in (
        "input",
        "output",
        "total",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "promptTokens",
        "completionTokens",
        "totalTokens",
        "input_tokens",
        "output_tokens",
        "inputTokens",
        "outputTokens",
        "inputTextTokens",
        "outputTextTokens",
        "tokenCount",
    ):
        value = getattr(usage, key, None)
        if value is not None:
            values[key] = value
    return values or None


def _pick_usage_value(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in usage:
            continue
        normalized = _to_int(usage.get(key))
        if normalized is not None:
            return normalized
    return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _normalize_text_content(content: Any) -> str:
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
    if content is None:
        return ""
    return str(content)


def _normalize_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
    if not raw_calls:
        return []

    result: list[dict[str, Any]] = []
    for call in raw_calls:
        if isinstance(call, dict):
            function = call.get("function", {}) if isinstance(call.get("function"), dict) else {}
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            result.append(
                {
                    "id": str(call.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": str(function.get("name", "")),
                        "arguments": arguments,
                    },
                }
            )
            continue

        function_obj = getattr(call, "function", None)
        arguments = getattr(function_obj, "arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        result.append(
            {
                "id": str(getattr(call, "id", "")),
                "type": "function",
                "function": {
                    "name": str(getattr(function_obj, "name", "")),
                    "arguments": arguments,
                },
            }
        )
    return result
