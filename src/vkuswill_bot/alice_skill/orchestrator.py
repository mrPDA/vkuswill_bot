"""Voice-orchestrator для сценария заказа через Алису."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from vkuswill_bot.alice_skill.account_linking import AccountLinkStore
from vkuswill_bot.alice_skill.delivery import LinkDeliveryAdapter
from vkuswill_bot.alice_skill.idempotency import IdempotencyStore
from vkuswill_bot.alice_skill.idempotency import InMemoryIdempotencyStore
from vkuswill_bot.alice_skill.models import VoiceOrderResult
from vkuswill_bot.alice_skill.rate_limit import RateLimiter
from vkuswill_bot.services.mcp_client import VkusvillMCPClient

logger = logging.getLogger(__name__)

_VERB_RE = re.compile(r"\b(закажи|заказать|добавь|купи|нужен|нужно)\b", re.IGNORECASE)
_LINK_CODE_PREFIX_RE = re.compile(r"(?:код|code)\s*[:\-]?\s*", re.IGNORECASE)
_LINK_CODE_TOKEN_RE = re.compile(r"[0-9]+|[a-zа-яё]+", re.IGNORECASE)
_LINK_CODE_SEPARATOR_TOKENS = {
    "и",
    "запятая",
    "точка",
    "тире",
    "дефис",
    "минус",
    "dash",
    "hyphen",
}
_LINK_CODE_DIGIT_WORDS = {
    "ноль": "0",
    "нуль": "0",
    "zero": "0",
    "один": "1",
    "раз": "1",
    "one": "1",
    "два": "2",
    "two": "2",
    "три": "3",
    "three": "3",
    "четыре": "4",
    "four": "4",
    "пять": "5",
    "five": "5",
    "шесть": "6",
    "six": "6",
    "семь": "7",
    "seven": "7",
    "восемь": "8",
    "eight": "8",
    "девять": "9",
    "nine": "9",
}
_LINK_CODE_HUNDREDS_WORDS = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}
_LINK_CODE_TENS_WORDS = {
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")
_STATUS_CHECK_PHRASES = (
    "проверь заказ",
    "проверить заказ",
    "статус заказа",
    "где заказ",
    "что с заказом",
    "готова корзина",
    "готов ли заказ",
    "проверь корзину",
)


def _format_rub(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


class AliceOrderOrchestrator:
    """Сборка корзины по голосовой команде через MCP-инструменты."""

    def __init__(
        self,
        mcp_client: VkusvillMCPClient,
        *,
        voice_order_client: VoiceOrderClient | None = None,
        voice_order_async_mode: bool = True,
        voice_api_fallback_to_mcp: bool = True,
        account_links: AccountLinkStore | None = None,
        delivery_adapter: LinkDeliveryAdapter | None = None,
        idempotency_store: IdempotencyStore | None = None,
        require_linked_account: bool = False,
        idempotency_ttl_seconds: int = 90,
        max_utterance_chars: int = 512,
        max_products_per_order: int = 20,
        order_rate_limiter: RateLimiter | None = None,
        link_code_rate_limiter: RateLimiter | None = None,
        order_rate_limit: int = 0,
        order_rate_window_seconds: int = 60,
        link_code_rate_limit: int = 0,
        link_code_rate_window_seconds: int = 600,
    ) -> None:
        self._mcp_client = mcp_client
        self._voice_order_client = voice_order_client
        self._voice_order_async_mode = voice_order_async_mode
        self._voice_api_fallback_to_mcp = voice_api_fallback_to_mcp
        self._account_links = account_links
        self._delivery = delivery_adapter
        self._idempotency_store = idempotency_store or InMemoryIdempotencyStore()
        self._require_linked_account = require_linked_account
        self._idempotency_ttl_seconds = idempotency_ttl_seconds
        self._max_utterance_chars = max_utterance_chars
        self._max_products_per_order = max_products_per_order
        self._order_rate_limiter = order_rate_limiter
        self._link_code_rate_limiter = link_code_rate_limiter
        self._order_rate_limit = max(0, order_rate_limit)
        self._order_rate_window_seconds = max(1, order_rate_window_seconds)
        self._link_code_rate_limit = max(0, link_code_rate_limit)
        self._link_code_rate_window_seconds = max(1, link_code_rate_window_seconds)

    @staticmethod
    def extract_product_queries(utterance: str) -> list[str]:
        """Извлечь список продуктовых запросов из голосовой фразы."""
        text = utterance.strip().lower()
        if not text:
            return []

        if "закажи" in text:
            text = text.split("закажи", 1)[1]

        for phrase in (
            "алиса",
            "запусти навык",
            "покупка во вкусвилле",
            "покупка в вкусвилле",
            "навык",
        ):
            text = text.replace(phrase, " ")

        text = _VERB_RE.sub(" ", text)
        text = text.replace(" и ", ",")
        text = text.replace(";", ",")

        items: list[str] = []
        for raw in text.split(","):
            item = " ".join(raw.split()).strip()
            if not item:
                continue
            if item.startswith("еще "):
                item = item[4:].strip()
            if item:
                items.append(item)

        # dedup в порядке появления
        return list(dict.fromkeys(items))

    @staticmethod
    def _normalize_utterance_for_idempotency(utterance: str) -> str:
        normalized = utterance.strip().lower()
        normalized = _NON_WORD_RE.sub(" ", normalized)
        normalized = _SPACES_RE.sub(" ", normalized).strip()
        return normalized

    @staticmethod
    def _build_idempotency_key(
        voice_user_id: str,
        utterance: str,
        minute_bucket: int,
    ) -> str:
        normalized = AliceOrderOrchestrator._normalize_utterance_for_idempotency(utterance)
        payload = f"{voice_user_id}|{normalized}|{minute_bucket}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _current_minute_bucket() -> int:
        return int(datetime.now(UTC).timestamp() // 60)

    @staticmethod
    def is_order_status_request(utterance: str) -> bool:
        normalized = utterance.strip().lower()
        normalized = _NON_WORD_RE.sub(" ", normalized)
        normalized = _SPACES_RE.sub(" ", normalized)
        return any(phrase in normalized for phrase in _STATUS_CHECK_PHRASES)

    @staticmethod
    def extract_link_code(utterance: str) -> str | None:
        """Извлечь 6-значный код привязки из голосовой фразы."""
        prefix = _LINK_CODE_PREFIX_RE.search(utterance)
        if not prefix:
            return None

        tail = utterance[prefix.end() :].lower()
        digits: list[str] = []
        started = False
        compound_starters = set(_LINK_CODE_HUNDREDS_WORDS) | set(_LINK_CODE_TENS_WORDS)
        tokens = [m.group(0).replace("ё", "е") for m in _LINK_CODE_TOKEN_RE.finditer(tail)]

        # Ограничиваемся первыми токенами после слова "код", чтобы не захватывать
        # случайные числа из дальнейшей части фразы.
        token_idx = 0
        idx = 0
        while idx < len(tokens):
            token_idx += 1
            if token_idx > 16:
                break
            token = tokens[idx]

            if token.isdigit():
                started = True
                digits.extend(list(token))
            elif token in _LINK_CODE_DIGIT_WORDS:
                started = True
                digits.append(_LINK_CODE_DIGIT_WORDS[token])
            elif token in compound_starters:
                parsed, consumed = AliceOrderOrchestrator._parse_compound_number_tokens(
                    tokens,
                    start=idx,
                )
                if parsed is not None and consumed > 0:
                    started = True
                    digits.extend(list(str(parsed)))
                    idx += consumed - 1
                elif started:
                    break
            elif token in _LINK_CODE_SEPARATOR_TOKENS:
                if started:
                    idx += 1
                    continue
            elif started:
                break

            if len(digits) >= 6:
                return "".join(digits[:6])
            idx += 1
        return "".join(digits[:6]) if len(digits) >= 6 else None

    @staticmethod
    def _parse_compound_number_tokens(tokens: list[str], *, start: int) -> tuple[int | None, int]:
        value = 0
        consumed = 0
        idx = start
        has_component = False
        has_hundreds = False
        has_tens = False
        has_units = False
        last_was_tens = False

        while idx < len(tokens):
            token = tokens[idx]
            if token in _LINK_CODE_HUNDREDS_WORDS:
                if has_component:
                    # Начинается следующая числовая группа (например 842 182).
                    break
                value += _LINK_CODE_HUNDREDS_WORDS[token]
                has_component = True
                has_hundreds = True
                last_was_tens = False
            elif token in _LINK_CODE_TENS_WORDS:
                if has_tens or has_units:
                    break
                value += _LINK_CODE_TENS_WORDS[token]
                has_component = True
                has_tens = True
                last_was_tens = _LINK_CODE_TENS_WORDS[token] >= 20
                # 10..19 — завершённая форма числа.
                if _LINK_CODE_TENS_WORDS[token] < 20:
                    consumed += 1
                    break
            elif token in _LINK_CODE_DIGIT_WORDS and (
                last_was_tens or (has_hundreds and not has_tens and not has_units)
            ):
                if has_units:
                    break
                value += int(_LINK_CODE_DIGIT_WORDS[token])
                has_component = True
                has_units = True
                last_was_tens = False
            else:
                break

            consumed += 1
            idx += 1

        if not has_component:
            return None, 0
        if value < 0 or value > 999:
            return None, 0
        return value, consumed

    async def create_order_from_utterance(
        self,
        voice_user_id: str,
        utterance: str,
    ) -> VoiceOrderResult:
        """Обработать голосовую команду и собрать корзину."""
        if len(utterance.strip()) > self._max_utterance_chars:
            return VoiceOrderResult(
                ok=False,
                voice_text=(
                    "Слишком длинная команда. "
                    f"Опишите заказ короче (до {self._max_utterance_chars} символов)."
                ),
                error_code="utterance_too_long",
            )

        link_code = self.extract_link_code(utterance)
        if link_code is not None:
            if self._link_code_rate_limiter is not None and self._link_code_rate_limit > 0:
                allowed = await self._link_code_rate_limiter.allow(
                    f"link:{voice_user_id}",
                    limit=self._link_code_rate_limit,
                    window_seconds=self._link_code_rate_window_seconds,
                )
                if not allowed:
                    return VoiceOrderResult(
                        ok=False,
                        voice_text=("Слишком много попыток ввода кода привязки. Попробуйте позже."),
                        error_code="link_rate_limited",
                    )

            if self._account_links is None:
                return VoiceOrderResult(
                    ok=False,
                    voice_text="Привязка аккаунта сейчас недоступна. Попробуйте позже.",
                    error_code="linking_unavailable",
                )
            link_result = await self._account_links.consume_link_code(
                voice_user_id=voice_user_id,
                code=link_code,
            )
            if bool(link_result.get("ok")):
                return VoiceOrderResult(
                    ok=True,
                    voice_text=(
                        "Аккаунт привязан. Теперь скажите, что добавить в корзину. "
                        "Например: закажи молоко и яйца."
                    ),
                )
            reason = str(link_result.get("reason", "invalid_code"))
            if reason == "code_expired":
                text = "Код привязки истёк. Запросите новый в Telegram через /link_voice."
            elif reason == "linking_unavailable":
                text = "Сервис привязки сейчас недоступен. Попробуйте позже."
            else:
                text = "Код неверный. Проверьте и повторите привязку."
            return VoiceOrderResult(
                ok=False,
                voice_text=text,
                error_code=reason,
            )

        linked_user_id: int | None = None
        if self._account_links is not None:
            linked_user_id = await self._account_links.resolve_internal_user_id(voice_user_id)

        if self._require_linked_account and linked_user_id is None:
            return VoiceOrderResult(
                ok=False,
                voice_text=(
                    "Чтобы оформить заказ, сначала привяжите аккаунт ВкусВилл. "
                    "Откройте Telegram-бот и выполните команду /link_voice."
                ),
                requires_linking=True,
                error_code="account_not_linked",
            )

        if self.is_order_status_request(utterance):
            status_result = await self._check_order_status_via_voice_api(
                linked_user_id=linked_user_id,
                voice_user_id=voice_user_id,
            )
            if status_result is not None:
                return status_result
            return VoiceOrderResult(
                ok=False,
                voice_text=(
                    "Я не вижу активной сборки корзины. "
                    "Скажите, что добавить: например, закажи молоко и яйца."
                ),
                error_code="order_status_not_available",
            )

        products = self.extract_product_queries(utterance)
        if not products:
            return VoiceOrderResult(
                ok=False,
                voice_text="Скажите, что заказать. Например: закажи молоко и яйца.",
                error_code="empty_order",
            )
        if len(products) > self._max_products_per_order:
            return VoiceOrderResult(
                ok=False,
                voice_text=(
                    f"За один запрос можно добавить до {self._max_products_per_order} "
                    "товаров. Разделите заказ на несколько команд."
                ),
                error_code="too_many_products",
            )
        if self._order_rate_limiter is not None and self._order_rate_limit > 0:
            allowed = await self._order_rate_limiter.allow(
                f"order:{voice_user_id}",
                limit=self._order_rate_limit,
                window_seconds=self._order_rate_window_seconds,
            )
            if not allowed:
                return VoiceOrderResult(
                    ok=False,
                    voice_text="Слишком много заказов за короткое время. Попробуйте позже.",
                    error_code="order_rate_limited",
                )

        idem_key = self._build_idempotency_key(
            voice_user_id,
            utterance,
            self._current_minute_bucket(),
        )
        cached = await self._idempotency_store.get_done(idem_key)
        if cached is not None:
            return cached

        started = await self._idempotency_store.try_start(idem_key, self._idempotency_ttl_seconds)
        if not started:
            return VoiceOrderResult(
                ok=False,
                voice_text="Уже формирую такую корзину. Проверьте приложение через пару секунд.",
                error_code="in_progress",
            )

        try:
            if linked_user_id is not None and self._voice_order_client is not None:
                if self._voice_order_async_mode and self._supports_async_voice_order_api():
                    api_result = await self._start_order_via_voice_api(
                        linked_user_id=linked_user_id,
                        voice_user_id=voice_user_id,
                        utterance=utterance,
                        products=products,
                    )
                else:
                    api_result = await self._create_order_via_voice_api(
                        linked_user_id=linked_user_id,
                        voice_user_id=voice_user_id,
                        utterance=utterance,
                        products=products,
                    )
                if api_result is not None:
                    await self._idempotency_store.mark_done(
                        idem_key,
                        result=api_result,
                        ttl_seconds=self._idempotency_ttl_seconds,
                    )
                    return api_result

            cart_products = await self._resolve_cart_products(products)
            if not cart_products:
                await self._idempotency_store.clear(idem_key)
                return VoiceOrderResult(
                    ok=False,
                    voice_text="Не удалось подобрать товары. Попробуйте уточнить запрос.",
                    error_code="products_not_found",
                )

            cart_raw = await self._call_json_tool(
                "vkusvill_cart_link_create",
                {"products": cart_products},
            )
            cart = self._extract_cart(cart_raw, fallback_items_count=len(cart_products))
            if not cart["link"]:
                await self._idempotency_store.clear(idem_key)
                return VoiceOrderResult(
                    ok=False,
                    voice_text="Не удалось создать корзину. Попробуйте ещё раз.",
                    error_code="cart_create_failed",
                )

            delivery = None
            if self._delivery is not None:
                delivery = await self._delivery.deliver_cart_link(
                    user_ref=voice_user_id,
                    cart_link=cart["link"],
                    total_rub=cart["total_rub"],
                    items_count=cart["items_count"],
                )

            result = VoiceOrderResult(
                ok=True,
                voice_text=self._build_success_text(cart["total_rub"], cart["items_count"]),
                cart_link=cart["link"],
                total_rub=cart["total_rub"],
                items_count=cart["items_count"],
                delivery=delivery,
            )
            await self._idempotency_store.mark_done(
                idem_key,
                result=result,
                ttl_seconds=self._idempotency_ttl_seconds,
            )
            return result
        except Exception:
            await self._idempotency_store.clear(idem_key)
            logger.exception("Alice order orchestration failed")
            return VoiceOrderResult(
                ok=False,
                voice_text="Не удалось обработать заказ. Попробуйте ещё раз.",
                error_code="unexpected_error",
            )

    def _supports_async_voice_order_api(self) -> bool:
        client = self._voice_order_client
        if client is None:
            return False
        return callable(getattr(client, "start_order", None)) and callable(
            getattr(client, "get_order_status", None),
        )

    async def _check_order_status_via_voice_api(
        self,
        *,
        linked_user_id: int | None,
        voice_user_id: str,
    ) -> VoiceOrderResult | None:
        if (
            linked_user_id is None
            or self._voice_order_client is None
            or not self._supports_async_voice_order_api()
        ):
            return None

        try:
            payload = await self._voice_order_client.get_order_status(  # type: ignore[union-attr]
                user_id=linked_user_id,
                voice_user_id=voice_user_id,
            )
        except Exception:
            logger.warning("Voice order status API unavailable", exc_info=True)
            return VoiceOrderResult(
                ok=False,
                voice_text=(
                    "Не удалось получить статус заказа. Повторите проверку через 20-30 секунд."
                ),
                error_code="voice_order_status_unavailable",
            )

        status = str(payload.get("status", "")).strip().lower()
        if status in {"processing", "queued", "started"}:
            return VoiceOrderResult(
                ok=True,
                voice_text=("Корзину еще собираю. Скажите: проверь заказ через 20-30 секунд."),
                error_code="order_processing",
            )
        if status == "not_found":
            return VoiceOrderResult(
                ok=False,
                voice_text=(
                    "Сейчас нет активной сборки корзины. Продиктуйте новый заказ одной фразой."
                ),
                error_code="order_not_found",
            )

        if status == "done" or payload.get("cart_link"):
            order_data = self._extract_voice_order_api_payload(
                payload,
                fallback_items_count=0,
            )
            if order_data["link"]:
                delivery = None
                if self._delivery is not None:
                    delivery = await self._delivery.deliver_cart_link(
                        user_ref=voice_user_id,
                        cart_link=order_data["link"],
                        total_rub=order_data["total_rub"],
                        items_count=order_data["items_count"],
                    )
                return VoiceOrderResult(
                    ok=True,
                    voice_text=self._build_success_text(
                        order_data["total_rub"],
                        order_data["items_count"],
                    ),
                    cart_link=order_data["link"],
                    total_rub=order_data["total_rub"],
                    items_count=order_data["items_count"],
                    delivery=delivery,
                )

        api_text = str(payload.get("assistant_text", "")).strip()
        if not api_text:
            api_text = "Пока не удалось завершить сборку корзины. Повторите запрос позже."
        api_error = str(payload.get("error", payload.get("error_code", "voice_order_failed")))
        return VoiceOrderResult(
            ok=False,
            voice_text=api_text,
            error_code=api_error,
        )

    async def _start_order_via_voice_api(
        self,
        *,
        linked_user_id: int,
        voice_user_id: str,
        utterance: str,
        products: list[str],
    ) -> VoiceOrderResult | None:
        start_order = getattr(self._voice_order_client, "start_order", None)
        if not callable(start_order):
            return await self._create_order_via_voice_api(
                linked_user_id=linked_user_id,
                voice_user_id=voice_user_id,
                utterance=utterance,
                products=products,
            )

        try:
            payload = await start_order(
                user_id=linked_user_id,
                voice_user_id=voice_user_id,
                utterance=self._build_llm_utterance(products, utterance),
            )
        except Exception:
            logger.warning(
                "Voice order start API unavailable, fallback_to_mcp=%s",
                self._voice_api_fallback_to_mcp,
                exc_info=True,
            )
            if not self._voice_api_fallback_to_mcp:
                return VoiceOrderResult(
                    ok=False,
                    voice_text=(
                        "Сервис заказа сейчас перегружен. "
                        "Повторите запрос одной фразой через несколько секунд."
                    ),
                    error_code="voice_order_api_unavailable",
                )
            return None

        status = str(payload.get("status", "")).strip().lower()
        if status in {"processing", "queued", "accepted", "started"}:
            return VoiceOrderResult(
                ok=True,
                voice_text=(
                    "Приняла заказ и начала сборку корзины. "
                    "Скажите: проверь заказ через 20-30 секунд."
                ),
                error_code="order_processing",
            )

        if status == "done" or payload.get("cart_link"):
            order_data = self._extract_voice_order_api_payload(
                payload,
                fallback_items_count=len(products),
            )
            if order_data["link"]:
                delivery = None
                if self._delivery is not None:
                    delivery = await self._delivery.deliver_cart_link(
                        user_ref=voice_user_id,
                        cart_link=order_data["link"],
                        total_rub=order_data["total_rub"],
                        items_count=order_data["items_count"],
                    )
                return VoiceOrderResult(
                    ok=True,
                    voice_text=self._build_success_text(
                        order_data["total_rub"],
                        order_data["items_count"],
                    ),
                    cart_link=order_data["link"],
                    total_rub=order_data["total_rub"],
                    items_count=order_data["items_count"],
                    delivery=delivery,
                )

        api_text = str(payload.get("assistant_text", "")).strip()
        if not api_text:
            api_text = "Не удалось поставить заказ в очередь. Попробуйте ещё раз."
        api_error = str(payload.get("error", payload.get("error_code", "voice_order_failed")))
        return VoiceOrderResult(
            ok=False,
            voice_text=api_text,
            error_code=api_error,
        )

    async def _create_order_via_voice_api(
        self,
        *,
        linked_user_id: int,
        voice_user_id: str,
        utterance: str,
        products: list[str],
    ) -> VoiceOrderResult | None:
        try:
            payload = await self._voice_order_client.create_order(  # type: ignore[union-attr]
                user_id=linked_user_id,
                voice_user_id=voice_user_id,
                utterance=self._build_llm_utterance(products, utterance),
            )
        except Exception:
            logger.warning(
                "Voice order API unavailable, fallback_to_mcp=%s",
                self._voice_api_fallback_to_mcp,
                exc_info=True,
            )
            if not self._voice_api_fallback_to_mcp:
                return VoiceOrderResult(
                    ok=False,
                    voice_text=(
                        "Сервис заказа сейчас перегружен. "
                        "Повторите запрос одной фразой через несколько секунд."
                    ),
                    error_code="voice_order_api_unavailable",
                )
            return None

        order_data = self._extract_voice_order_api_payload(
            payload,
            fallback_items_count=len(products),
        )

        if order_data["link"]:
            delivery = None
            if self._delivery is not None:
                delivery = await self._delivery.deliver_cart_link(
                    user_ref=voice_user_id,
                    cart_link=order_data["link"],
                    total_rub=order_data["total_rub"],
                    items_count=order_data["items_count"],
                )
            return VoiceOrderResult(
                ok=True,
                voice_text=self._build_success_text(
                    order_data["total_rub"],
                    order_data["items_count"],
                ),
                cart_link=order_data["link"],
                total_rub=order_data["total_rub"],
                items_count=order_data["items_count"],
                delivery=delivery,
            )

        api_text = order_data["assistant_text"]
        if not api_text:
            api_text = "Не удалось создать корзину. Попробуйте уточнить состав заказа."
        return VoiceOrderResult(
            ok=False,
            voice_text=api_text,
            error_code=order_data["error_code"] or "cart_create_failed",
        )

    @staticmethod
    def _build_llm_utterance(products: list[str], utterance: str) -> str:
        if products:
            return "Собери корзину во ВкусВилл: " + ", ".join(products)
        return utterance.strip()

    @staticmethod
    def _extract_voice_order_api_payload(
        payload: dict[str, Any],
        *,
        fallback_items_count: int,
    ) -> dict[str, Any]:
        link_raw = payload.get("cart_link")
        link = link_raw if isinstance(link_raw, str) and link_raw.strip() else None
        total_rub = AliceOrderOrchestrator._coerce_float(payload.get("total_rub"))
        items_count = AliceOrderOrchestrator._coerce_non_negative_int(payload.get("items_count"))
        if (items_count is None or items_count <= 0) and fallback_items_count > 0:
            items_count = fallback_items_count
        assistant_text_raw = payload.get("assistant_text")
        assistant_text = assistant_text_raw.strip() if isinstance(assistant_text_raw, str) else ""
        error_code_raw = payload.get("error_code", payload.get("error"))
        error_code = error_code_raw.strip() if isinstance(error_code_raw, str) else None
        return {
            "link": link,
            "total_rub": total_rub,
            "items_count": max(items_count, 0),
            "assistant_text": assistant_text,
            "error_code": error_code,
        }

    async def _resolve_cart_products(self, products: list[str]) -> list[dict[str, Any]]:
        cart_products: list[dict[str, Any]] = []
        for query in products:
            raw = await self._call_json_tool(
                "vkusvill_products_search",
                {"q": query, "limit": 5},
            )
            product = self._pick_product(raw)
            if product is None:
                logger.info("Alice search: no product found for query=%r", query)
                continue
            cart_products.append({"xml_id": product["xml_id"], "q": 1})
        return cart_products

    async def _call_json_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = await self._mcp_client.call_tool(tool_name, arguments)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Tool %s returned non-JSON payload: %r", tool_name, raw[:200])
            return {"ok": False, "error": "invalid_json", "raw": raw}
        return parsed if isinstance(parsed, dict) else {"ok": False, "error": "invalid_payload"}

    @staticmethod
    def _pick_product(search_result: dict[str, Any]) -> dict[str, Any] | None:
        data = search_result.get("data")

        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [x for x in data if isinstance(x, dict)]
        elif isinstance(data, dict):
            raw_items = data.get("items")
            if isinstance(raw_items, list):
                items = [x for x in raw_items if isinstance(x, dict)]
        elif isinstance(search_result.get("products"), list):
            items = [x for x in search_result["products"] if isinstance(x, dict)]

        for item in items:
            xml_id = item.get("xml_id")
            if isinstance(xml_id, int):
                return {"xml_id": xml_id, "name": item.get("name")}
            if isinstance(xml_id, str) and xml_id.isdigit():
                return {"xml_id": int(xml_id), "name": item.get("name")}
        return None

    @staticmethod
    def _extract_cart(
        cart_result: dict[str, Any],
        *,
        fallback_items_count: int = 0,
    ) -> dict[str, Any]:
        data_raw = cart_result.get("data")
        data = data_raw if isinstance(data_raw, dict) else cart_result
        if not isinstance(data, dict):
            return {"link": None, "total_rub": None, "items_count": fallback_items_count}

        link = data.get("link", data.get("cart_link"))
        if not isinstance(link, str) or not link:
            link = None

        summary = data.get("price_summary")
        total_source = None
        if isinstance(summary, dict):
            total_source = summary.get("total")
        if total_source is None:
            total_source = data.get("total")
        total_rub = AliceOrderOrchestrator._coerce_float(total_source)

        items_count = 0
        products = data.get("products")
        if isinstance(products, list) and products:
            items_count = len(products)

        if items_count == 0 and isinstance(summary, dict):
            summary_count = AliceOrderOrchestrator._coerce_non_negative_int(summary.get("count"))
            if summary_count is not None and summary_count > 0:
                items_count = summary_count

        if items_count == 0:
            count_fields = ("products_count", "items_count", "count")
            for field in count_fields:
                count_value = AliceOrderOrchestrator._coerce_non_negative_int(data.get(field))
                if count_value is not None and count_value > 0:
                    items_count = count_value
                    break

        if items_count == 0:
            items = data.get("items")
            if isinstance(items, list) and items:
                items_count = len(items)

        if items_count == 0 and fallback_items_count > 0:
            items_count = fallback_items_count

        return {"link": link, "total_rub": total_rub, "items_count": items_count}

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            normalized = value.strip().replace(",", ".")
            if not normalized:
                return None
            try:
                return float(normalized)
            except ValueError:
                return None
        return None

    @staticmethod
    def _coerce_non_negative_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float):
            if value.is_integer() and value >= 0:
                return int(value)
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                parsed = int(normalized)
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None

    @staticmethod
    def _build_success_text(total_rub: float | None, items_count: int) -> str:
        if total_rub is not None and items_count > 0:
            return (
                "Готово. Собрала корзину: "
                f"{items_count} позиций на {_format_rub(total_rub)} рублей, "
                "ссылку отправила в приложение."
            )
        if total_rub is not None:
            return (
                f"Готово. Собрала корзину на {_format_rub(total_rub)} рублей, "
                "ссылку отправила в приложение."
            )
        if items_count > 0:
            return f"Готово. Собрала корзину: {items_count} позиций, ссылку отправила в приложение."
        return "Готово. Корзину собрала, ссылку отправила в приложение."


class VoiceOrderClient(Protocol):
    async def create_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, Any]:
        """Создать корзину через internal API стандартного LLM-цикла."""

    async def start_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, Any]:
        """Поставить задачу сборки корзины в очередь и вернуть job status."""

    async def get_order_status(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Получить статус фоновой сборки корзины."""
