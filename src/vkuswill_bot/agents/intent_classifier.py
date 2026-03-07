"""LLM-based user intent classification for prompt profile routing."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Protocol

from vkuswill_bot.agents.llm_helpers import extract_message, extract_text
from vkuswill_bot.services.prompt_registry import get_registry
from vkuswill_bot.services.prompts import PromptProfile

logger = logging.getLogger(__name__)

_VALID_PROFILES: frozenset[str] = frozenset(
    {"recipe", "cart", "meal_plan", "status", "linking", "general"}
)

_CLASSIFY_PROMPT_STUB = (
    "Определи намерение покупателя в магазине ВкусВилл. Ответь ОДНИМ словом.\n"
    "- recipe — рецепт\n- cart — купить\n- meal_plan — план питания\n"
    "- status — статус\n- linking — привязка\n- general — другое\n\nСообщение: {text}"
)

_CLASSIFY_MAX_TOKENS = 20
_CLASSIFY_TEMPERATURE = 0.0


def _classify_prompt_bundle(text: str) -> tuple[str, dict[str, Any]]:
    registry = get_registry()
    if registry is not None:
        resolution = registry.resolve("classify-intent", text=text)
        if resolution.text:
            return resolution.text, resolution.as_dict()
    prompt = _CLASSIFY_PROMPT_STUB.format(text=text)
    return prompt, {
        "name": "classify-intent",
        "source": "stub",
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
    }


class LLMAdapterProtocol(Protocol):
    async def create_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]: ...


def _parse_profile(raw: str) -> PromptProfile | None:
    """Extract a valid profile name from raw LLM output."""
    cleaned = raw.strip().lower().rstrip(".")
    for token in cleaned.split():
        if token in _VALID_PROFILES:
            return token  # type: ignore[return-value]
    return None


async def classify_user_intent(
    text: str,
    adapter: LLMAdapterProtocol,
    model: str,
    timeout_seconds: float = 5.0,
    trace: Any | None = None,
) -> PromptProfile | None:
    """Classify user intent via a lightweight LLM call.

    Returns a PromptProfile on success, or None if classification
    failed (timeout, invalid response, adapter error) — caller
    should fall back to keyword-based detection.
    """
    prompt, prompt_metadata = _classify_prompt_bundle(text)
    messages = [{"role": "user", "content": prompt}]
    generation = None
    if trace is not None:
        generation = trace.generation(
            name="intent-classification",
            model=model,
            input=messages,
            model_parameters={
                "tools": 0,
                "tool_choice": "none",
                "max_tokens": _CLASSIFY_MAX_TOKENS,
                "temperature": _CLASSIFY_TEMPERATURE,
            },
            metadata={"prompt": prompt_metadata},
        )
    try:
        response = await asyncio.wait_for(
            adapter.create_completion(
                model=model,
                messages=messages,
                tools=[],
                tool_choice="none",
                max_tokens=_CLASSIFY_MAX_TOKENS,
                temperature=_CLASSIFY_TEMPERATURE,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning("Intent classification timed out (%.1fs)", timeout_seconds)
        if generation is not None:
            generation.end(
                output="timeout",
                level="WARNING",
                status_message="Intent classification timed out",
                metadata={"prompt": prompt_metadata},
            )
        return None
    except Exception:
        logger.warning("Intent classification failed", exc_info=True)
        if generation is not None:
            generation.end(
                output="error",
                level="ERROR",
                status_message="Intent classification failed",
                metadata={"prompt": prompt_metadata},
            )
        return None

    message = extract_message(response)
    content = extract_text(message)
    profile = _parse_profile(content)
    if profile is None:
        logger.info("Intent classification returned unparsable response: %r", content)
    if generation is not None:
        generation.end(
            output={"raw": content, "profile": profile},
            metadata={"prompt": prompt_metadata, "resolved_profile": profile},
        )
    return profile
