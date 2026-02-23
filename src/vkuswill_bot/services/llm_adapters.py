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
import logging
import uuid
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_LLM_PROVIDER = "qwen_openai"


def normalize_llm_provider(value: str) -> str:
    """Normalize provider aliases to canonical values."""
    normalized = value.strip().lower()
    aliases = {
        "qwen": "qwen_openai",
        "qwen_openai": "qwen_openai",
        "openai": "qwen_openai",
        "openai_compatible": "qwen_openai",
        "gigachat": "gigachat_sdk",
        "gigachat_sdk": "gigachat_sdk",
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
    ) -> dict[str, Any]:
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
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


class GigaChatLLMAdapter:
    """Adapter for GigaChat SDK using normalized OpenAI-like response shape."""

    def __init__(
        self,
        *,
        credentials: str = "",
        scope: str = "GIGACHAT_API_PERS",
        timeout_seconds: float = 60.0,
        ca_bundle_file: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return

        try:
            from gigachat import GigaChat
        except Exception as exc:
            raise RuntimeError("ShoppingAgent requires package gigachat for provider") from exc

        import pathlib

        effective_ca_bundle: str | None = None
        if ca_bundle_file:
            ca_path = pathlib.Path(ca_bundle_file)
            if ca_path.exists():
                effective_ca_bundle = str(ca_path)
            else:
                logger.warning(
                    "GigaChat adapter CA bundle not found (%s), verify=False",
                    ca_bundle_file,
                )
        verify_ssl = bool(effective_ca_bundle)

        kwargs: dict[str, Any] = {
            "credentials": credentials,
            "scope": scope,
            "verify_ssl_certs": verify_ssl,
            "timeout": int(max(1.0, timeout_seconds)),
        }
        if effective_ca_bundle:
            kwargs["ca_bundle_file"] = effective_ca_bundle
        self._client = GigaChat(**kwargs)

    async def create_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        chat_payload = self._build_chat_payload(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        response = await asyncio.to_thread(self._client.chat, chat_payload)
        return self._normalize_response(response)

    async def close(self) -> None:
        close_method = getattr(self._client, "close", None)
        if close_method is None:
            return
        if asyncio.iscoroutinefunction(close_method):
            with contextlib.suppress(Exception):
                await close_method()
            return
        with contextlib.suppress(Exception):
            await asyncio.to_thread(close_method)

    @staticmethod
    def _build_chat_payload(
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> Any:
        from gigachat.models import Chat

        mode = "none" if tool_choice == "none" else "auto"
        payload: dict[str, Any] = {
            "model": model,
            "messages": GigaChatLLMAdapter._to_gigachat_messages(messages),
            "function_call": mode,
        }
        if mode != "none":
            payload["functions"] = GigaChatLLMAdapter._to_gigachat_functions(tools)
        return Chat(**payload)

    @staticmethod
    def _to_gigachat_messages(messages: list[dict[str, Any]]) -> list[Any]:
        from gigachat.models import FunctionCall, Messages, MessagesRole

        out: list[Any] = []
        for message in messages:
            role = str(message.get("role", "")).strip().lower()
            content = _normalize_text_content(message.get("content"))

            if role == "system":
                out.append(Messages(role=MessagesRole.SYSTEM, content=content))
                continue
            if role == "user":
                out.append(Messages(role=MessagesRole.USER, content=content))
                continue
            if role == "tool":
                out.append(
                    Messages(
                        role=MessagesRole.FUNCTION,
                        content=content,
                        name=str(message.get("name", "tool")),
                    )
                )
                continue
            if role == "assistant":
                tool_calls = _normalize_tool_calls(message.get("tool_calls"))
                function_call = None
                if tool_calls:
                    first_call = tool_calls[0]
                    function_call = FunctionCall(
                        name=first_call["function"]["name"],
                        arguments=_parse_function_arguments_for_gigachat(
                            first_call["function"]["arguments"]
                        ),
                    )
                out.append(
                    Messages(
                        role=MessagesRole.ASSISTANT,
                        content=content,
                        function_call=function_call,
                    )
                )
                continue

            # Unknown roles are passed through as USER text to avoid hard failure.
            out.append(Messages(role=MessagesRole.USER, content=content))
        return out

    @staticmethod
    def _to_gigachat_functions(tools: list[dict[str, Any]]) -> list[Any]:
        from gigachat.models import Function

        functions: list[Any] = []
        for tool in tools:
            function = tool.get("function", {}) if isinstance(tool, dict) else {}
            name = str(function.get("name", "")).strip()
            if not name:
                continue
            functions.append(
                Function(
                    name=name,
                    description=str(function.get("description", "")),
                    parameters=function.get("parameters", {"type": "object", "properties": {}}),
                )
            )
        return functions

    @staticmethod
    def _normalize_response(response: Any) -> dict[str, Any]:
        message = _extract_message(response)
        content = _normalize_text_content(_extract_content(message))
        function_call = _extract_function_call(message)
        tool_calls: list[dict[str, Any]] = []
        if function_call:
            arguments = function_call.get("arguments", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            tool_calls.append(
                {
                    "id": f"gc-{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": str(function_call.get("name", "")),
                        "arguments": arguments,
                    },
                }
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": content,
                        "tool_calls": tool_calls,
                    }
                }
            ]
        }


def create_llm_adapter(
    *,
    provider: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_timeout_seconds: float,
    gigachat_credentials: str,
    gigachat_scope: str,
    gigachat_ca_bundle: str | None = None,
) -> LLMAdapterProtocol:
    """Create adapter for a configured provider."""
    normalized = normalize_llm_provider(provider)
    if normalized == "qwen_openai":
        return OpenAICompatibleLLMAdapter(
            api_key=llm_api_key,
            base_url=llm_base_url,
            timeout_seconds=llm_timeout_seconds,
        )
    if normalized == "gigachat_sdk":
        return GigaChatLLMAdapter(
            credentials=gigachat_credentials,
            scope=gigachat_scope,
            timeout_seconds=llm_timeout_seconds,
            ca_bundle_file=gigachat_ca_bundle,
        )
    raise ValueError(f"Unsupported llm provider: {provider}")


def normalize_chat_response(response: Any) -> dict[str, Any]:
    """Normalize chat completion response to OpenAI-like dict."""
    message = _extract_message(response)
    content = _normalize_text_content(_extract_content(message))
    tool_calls = _normalize_tool_calls(_extract_tool_calls(message))
    usage = _extract_usage_details(response)
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


def _extract_usage_details(response: Any) -> dict[str, int] | None:
    """Extract usage details from provider response into canonical shape."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None

    if isinstance(usage, dict):
        prompt = usage.get("input", usage.get("prompt_tokens"))
        completion = usage.get("output", usage.get("completion_tokens"))
        total = usage.get("total", usage.get("total_tokens"))
    else:
        prompt = getattr(usage, "input", getattr(usage, "prompt_tokens", None))
        completion = getattr(usage, "output", getattr(usage, "completion_tokens", None))
        total = getattr(usage, "total", getattr(usage, "total_tokens", None))

    result: dict[str, int] = {}
    if isinstance(prompt, int):
        result["input"] = prompt
    if isinstance(completion, int):
        result["output"] = completion
    if isinstance(total, int):
        result["total"] = total
    return result or None


def _extract_function_call(message: Any) -> dict[str, Any] | None:
    if isinstance(message, dict):
        function_call = message.get("function_call")
    else:
        function_call = getattr(message, "function_call", None)
    if function_call is None:
        return None
    if isinstance(function_call, dict):
        return {
            "name": str(function_call.get("name", "")),
            "arguments": function_call.get("arguments", "{}"),
        }
    return {
        "name": str(getattr(function_call, "name", "")),
        "arguments": getattr(function_call, "arguments", "{}"),
    }


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


def _parse_function_arguments_for_gigachat(raw_arguments: Any) -> dict[str, Any]:
    """Convert OpenAI-like function arguments into dict expected by GigaChat SDK."""
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        stripped = raw_arguments.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            logger.warning("Cannot parse tool-call arguments as JSON: %s", raw_arguments)
            return {}
        if isinstance(parsed, dict):
            return parsed
        logger.warning("Tool-call arguments JSON is not object: %s", type(parsed).__name__)
        return {}
    return {}
