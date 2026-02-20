"""Voice-orchestrator для сценария заказа через Алису."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from vkuswill_bot.alice_skill.account_linking import AccountLinkStore
from vkuswill_bot.alice_skill.delivery import LinkDeliveryAdapter
from vkuswill_bot.alice_skill.idempotency import InMemoryIdempotencyStore
from vkuswill_bot.alice_skill.models import VoiceOrderResult
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
_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")


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
        account_links: AccountLinkStore | None = None,
        delivery_adapter: LinkDeliveryAdapter | None = None,
        idempotency_store: InMemoryIdempotencyStore | None = None,
        require_linked_account: bool = False,
        idempotency_ttl_seconds: int = 90,
    ) -> None:
        self._mcp_client = mcp_client
        self._account_links = account_links
        self._delivery = delivery_adapter
        self._idempotency_store = idempotency_store or InMemoryIdempotencyStore()
        self._require_linked_account = require_linked_account
        self._idempotency_ttl_seconds = idempotency_ttl_seconds

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
    def extract_link_code(utterance: str) -> str | None:
        """Извлечь 6-значный код привязки из голосовой фразы."""
        prefix = _LINK_CODE_PREFIX_RE.search(utterance)
        if not prefix:
            return None

        tail = utterance[prefix.end() :].lower()
        digits: list[str] = []
        started = False

        # Ограничиваемся первыми токенами после слова "код", чтобы не захватывать
        # случайные числа из дальнейшей части фразы.
        for token_idx, match in enumerate(_LINK_CODE_TOKEN_RE.finditer(tail), start=1):
            if token_idx > 16:
                break
            token = match.group(0)

            if token.isdigit():
                started = True
                digits.extend(list(token))
            elif token in _LINK_CODE_DIGIT_WORDS:
                started = True
                digits.append(_LINK_CODE_DIGIT_WORDS[token])
            elif token in _LINK_CODE_SEPARATOR_TOKENS:
                if started:
                    continue
            elif started:
                break

            if len(digits) >= 6:
                return "".join(digits[:6])
        return None

    async def create_order_from_utterance(
        self,
        voice_user_id: str,
        utterance: str,
    ) -> VoiceOrderResult:
        """Обработать голосовую команду и собрать корзину."""
        link_code = self.extract_link_code(utterance)
        if link_code is not None:
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
            else:
                text = "Код неверный. Проверьте и повторите привязку."
            return VoiceOrderResult(
                ok=False,
                voice_text=text,
                error_code=reason,
            )

        products = self.extract_product_queries(utterance)
        if not products:
            return VoiceOrderResult(
                ok=False,
                voice_text="Скажите, что заказать. Например: закажи молоко и яйца.",
                error_code="empty_order",
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
            cart = self._extract_cart(cart_raw)
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
    def _extract_cart(cart_result: dict[str, Any]) -> dict[str, Any]:
        data = cart_result.get("data")
        if not isinstance(data, dict):
            return {"link": None, "total_rub": None, "items_count": 0}

        link = data.get("link")
        if not isinstance(link, str) or not link:
            link = None

        total_rub = None
        summary = data.get("price_summary")
        if isinstance(summary, dict):
            total = summary.get("total")
            if isinstance(total, int | float):
                total_rub = float(total)

        items_count = 0
        products = data.get("products")
        if isinstance(products, list):
            items_count = len(products)
        return {"link": link, "total_rub": total_rub, "items_count": items_count}

    @staticmethod
    def _build_success_text(total_rub: float | None, items_count: int) -> str:
        if total_rub is None:
            return f"Готово. Корзина на {items_count} позиций, ссылку отправила в приложение."
        return f"Готово. Корзина на {_format_rub(total_rub)} рублей, ссылку отправила в приложение."
