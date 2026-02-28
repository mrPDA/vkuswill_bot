"""LLM-based user intent classification for prompt profile routing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from vkuswill_bot.agents.llm_helpers import extract_message, extract_text
from vkuswill_bot.services.prompt_registry import get_registry
from vkuswill_bot.services.prompts import PromptProfile

logger = logging.getLogger(__name__)

_VALID_PROFILES: frozenset[str] = frozenset({"recipe", "cart", "status", "linking", "general"})

_CLASSIFY_PROMPT_STUB = (
    "Определи намерение покупателя в магазине ВкусВилл. Ответь ОДНИМ словом.\n"
    "- recipe — рецепт\n- cart — купить\n- status — статус\n"
    "- linking — привязка\n- general — другое\n\nСообщение: {text}"
)

_CLASSIFY_MAX_TOKENS = 20
_CLASSIFY_TEMPERATURE = 0.0


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
) -> PromptProfile | None:
    """Classify user intent via a lightweight LLM call.

    Returns a PromptProfile on success, or None if classification
    failed (timeout, invalid response, adapter error) — caller
    should fall back to keyword-based detection.
    """
    registry = get_registry()
    if registry is not None:
        prompt = registry.get("classify-intent", text=text)
    else:
        prompt = _CLASSIFY_PROMPT_STUB.format(text=text)
    messages = [{"role": "user", "content": prompt}]
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
        return None
    except Exception:
        logger.warning("Intent classification failed", exc_info=True)
        return None

    message = extract_message(response)
    content = extract_text(message)
    profile = _parse_profile(content)
    if profile is None:
        logger.info("Intent classification returned unparsable response: %r", content)
    return profile
