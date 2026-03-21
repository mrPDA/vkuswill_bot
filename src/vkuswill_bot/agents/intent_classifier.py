"""LLM-based user intent classification for prompt profile routing."""

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from vkuswill_bot.agents.llm_helpers import extract_message, extract_text, estimate_usage_details
from vkuswill_bot.services.llm_adapters import extract_usage_details
from vkuswill_bot.services.prompt_registry import get_registry
from vkuswill_bot.services.prompts import PromptProfile

logger = logging.getLogger(__name__)

_VALID_PROFILES: frozenset[str] = frozenset(
    {"recipe", "cart", "meal_plan", "status", "linking", "general"}
)

_CLASSIFY_PROMPT_STUB = (
    "Определи intent пользователя для маршрутизации в боте ВкусВилл.\n"
    "Верни только JSON без markdown и без дополнительного текста:\n"
    "{{"
    '"profile":"meal_plan","confidence":0.93,'
    '"reason":"запрос меню на неделю для нескольких человек"'
    "}}\n\n"
    "Допустимые profile:\n"
    "- recipe: конкретное блюдо или несколько блюд с известными названиями,"
    " рецепт, ингредиенты, порции.\n"
    "- meal_plan: ТОЛЬКО когда пользователь просит СГЕНЕРИРОВАТЬ меню/рацион/план"
    " питания на период (дни/неделю). Ключевое: пользователь НЕ называет блюда,"
    " а просит подобрать.\n"
    "- cart: купить, добавить в корзину или подобрать готовые товары"
    " без задачи составить рацион или приготовить блюдо.\n"
    "- status: статус заказа, доставки, корзины или уже оформленного заказа.\n"
    "- linking: привязка аккаунта, кода, номера телефона, Алисы"
    " или другого внешнего профиля.\n"
    "- general: любой другой запрос.\n\n"
    "Правила:\n"
    "1. meal_plan ТОЛЬКО если пользователь просит СГЕНЕРИРОВАТЬ план/меню/рацион"
    " на период без указания конкретных блюд. Обязательные слова-индикаторы:"
    " 'меню', 'рацион', 'план питания', 'на неделю' (без конкретных блюд).\n"
    "2. Если названо конкретное блюдо (борщ, паста, лазанья, шашлык, хинкали)"
    " — ВСЕГДА recipe, даже если есть 'на N порций' или 'на N человек'.\n"
    "3. Если перечислены конкретные блюда для разных приёмов пищи"
    " (завтрак X, обед Y, ужин Z) — это recipe, НЕ meal_plan.\n"
    "4. Если названы только приёмы пищи без конкретных блюд"
    " ('завтрак и обед', 'что-нибудь на ужин') — это cart, НЕ recipe.\n"
    "5. Если перечислены товары/продукты для покупки без блюда — cart.\n"
    "6. 'на N человек' БЕЗ слов меню/рацион/план — это НЕ meal_plan."
    " Мероприятия (день рождения, шашлык, пикник) = cart или recipe.\n"
    "7. Запросы с диетой (кето, веган) + конкретный приём пищи (завтрак)"
    " = recipe, если НЕ просят генерировать план на период.\n"
    "8. confidence должен быть числом от 0 до 1.\n"
    "9. reason должен быть коротким, не более 12 слов.\n\n"
    "Примеры:\n"
    'Сообщение: "собери корзину на неделю для 4 человек"\n'
    'Ответ: {{"profile":"meal_plan","confidence":0.98,'
    '"reason":"генерация меню на неделю без конкретных блюд"}}\n\n'
    'Сообщение: "рацион на 3 дня для 2 человек"\n'
    'Ответ: {{"profile":"meal_plan","confidence":0.97,'
    '"reason":"план питания на период"}}\n\n'
    'Сообщение: "собери корзину для борща на 4 порции"\n'
    'Ответ: {{"profile":"recipe","confidence":0.99,'
    '"reason":"конкретное блюдо — борщ на порции"}}\n\n'
    'Сообщение: "завтрак овсянку, обед суп, ужин пасту на двоих"\n'
    'Ответ: {{"profile":"recipe","confidence":0.96,'
    '"reason":"перечислены конкретные блюда"}}\n\n'
    'Сообщение: "собери мне завтрак и обед"\n'
    'Ответ: {{"profile":"cart","confidence":0.94,'
    '"reason":"абстрактные приёмы пищи без блюд"}}\n\n'
    'Сообщение: "шашлык на 8 человек: свинина 4 кг, лук, помидоры"\n'
    'Ответ: {{"profile":"recipe","confidence":0.97,'
    '"reason":"конкретное блюдо шашлык с ингредиентами"}}\n\n'
    'Сообщение: "день рождения на 10 человек: чипсы, сыр, колбаса, торт"\n'
    'Ответ: {{"profile":"cart","confidence":0.98,'
    '"reason":"список продуктов для мероприятия"}}\n\n'
    'Сообщение: "куриная грудка 2 кг, творог, яйца, батончики"\n'
    'Ответ: {{"profile":"cart","confidence":0.99,'
    '"reason":"список конкретных товаров"}}\n\n'
    'Сообщение: "кето-завтрак на двоих: авокадо, бекон, яйца"\n'
    'Ответ: {{"profile":"recipe","confidence":0.95,'
    '"reason":"конкретное блюдо с ингредиентами"}}\n\n'
    'Сообщение: "перекусы в школу для ребёнка на 5 дней"\n'
    'Ответ: {{"profile":"cart","confidence":0.92,'
    '"reason":"подбор готовых перекусов без плана"}}\n\n'
    'Сообщение: "собери продукты для лазаньи на 6 порций"\n'
    'Ответ: {{"profile":"recipe","confidence":0.99,'
    '"reason":"ингредиенты для конкретного блюда"}}\n\n'
    'Сообщение: "добавь молоко и хлеб"\n'
    'Ответ: {{"profile":"cart","confidence":0.99,'
    '"reason":"список товаров для покупки"}}\n\n'
    'Сообщение: "где мой заказ"\n'
    'Ответ: {{"profile":"status","confidence":0.99,'
    '"reason":"запрос статуса заказа"}}\n\n'
    "Сообщение: {text}"
)

_CLASSIFY_MAX_TOKENS = 120
_CLASSIFY_TEMPERATURE = 0.0


@dataclass(slots=True)
class IntentClassificationResult:
    profile: PromptProfile | None
    confidence: float | None = None
    reason: str | None = None
    raw_output: str = ""


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


def _strip_json_fence(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if not lines:
        return cleaned
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _normalize_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return max(0.0, min(1.0, numeric))


def _parse_classification_result(raw: str) -> IntentClassificationResult:
    cleaned = raw.strip()
    json_payload = _strip_json_fence(cleaned)
    if json_payload:
        try:
            parsed = json.loads(json_payload)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", json_payload, re.DOTALL)
            if match is not None:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    parsed = None
            else:
                parsed = None
        if isinstance(parsed, dict):
            raw_profile = parsed.get("profile")
            reason = parsed.get("reason")
            return IntentClassificationResult(
                profile=_parse_profile(str(raw_profile)) if raw_profile is not None else None,
                confidence=_normalize_confidence(parsed.get("confidence")),
                reason=str(reason).strip() if isinstance(reason, str) and reason.strip() else None,
                raw_output=raw,
            )
    return IntentClassificationResult(
        profile=_parse_profile(cleaned),
        raw_output=raw,
    )


async def classify_user_intent(
    text: str,
    adapter: LLMAdapterProtocol,
    model: str,
    timeout_seconds: float = 5.0,
    trace: Any | None = None,
) -> IntentClassificationResult | None:
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
    result = _parse_classification_result(content)
    if result.profile is None:
        logger.info("Intent classification returned unparsable response: %r", content)
    if generation is not None:
        usage_details = extract_usage_details(response)
        if usage_details is None:
            usage_details = estimate_usage_details(messages=messages, message=message)
        generation.end(
            output={
                "raw": result.raw_output,
                "profile": result.profile,
                "confidence": result.confidence,
                "reason": result.reason,
            },
            usage_details=usage_details,
            metadata={
                "prompt": prompt_metadata,
                "resolved_profile": result.profile,
                "confidence": result.confidence,
                "reason": result.reason,
            },
        )
    return result
