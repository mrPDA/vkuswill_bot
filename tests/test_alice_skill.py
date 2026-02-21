"""Тесты MVP-интеграции навыка Алисы."""

from __future__ import annotations

import asyncio
import importlib
import json

import httpx
import pytest

from vkuswill_bot.alice_skill.account_linking import (
    HttpAccountLinkStore,
    InMemoryAccountLinkStore,
    UnavailableAccountLinkStore,
)
from vkuswill_bot.alice_skill.delivery import AliceAppDeliveryAdapter
from vkuswill_bot.alice_skill.idempotency import InMemoryIdempotencyStore
from vkuswill_bot.alice_skill.idempotency import RedisIdempotencyStore
from vkuswill_bot.alice_skill.models import DeliveryResult, VoiceOrderResult
from vkuswill_bot.alice_skill.orchestrator import AliceOrderOrchestrator
from vkuswill_bot.alice_skill.rate_limit import InMemoryRateLimiter
from vkuswill_bot.alice_skill.voice_order_client import HttpVoiceOrderClient


class FakeMCPClient:
    """Фейковый MCP-клиент для тестов orchestration."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if name == "vkusvill_products_search":
            query = arguments.get("q")
            if query == "молоко":
                return json.dumps(
                    {
                        "ok": True,
                        "data": [{"xml_id": 101, "name": "Молоко 3.2%"}],
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"ok": True, "data": []}, ensure_ascii=False)

        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {
                    "ok": True,
                    "data": {
                        "link": "https://shop.example/cart/abc",
                        "price_summary": {"total": 430.0},
                        "products": arguments.get("products", []),
                    },
                },
                ensure_ascii=False,
            )

        raise AssertionError(f"Unexpected tool call: {name}")


class FakeMCPClientAltCartPayload(FakeMCPClient):
    """Фейковый MCP-клиент с альтернативной схемой cart payload."""

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if name == "vkusvill_products_search":
            return json.dumps(
                {"ok": True, "data": [{"xml_id": 101, "name": "Молоко 3.2%"}]},
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {
                    "ok": True,
                    "data": {
                        "cart_link": "https://shop.example/cart/alt",
                        "total": 640,
                        "products_count": 2,
                    },
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")


class FakeMCPClientLinkOnly(FakeMCPClient):
    """Фейковый MCP-клиент, который возвращает только ссылку на корзину."""

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if name == "vkusvill_products_search":
            return json.dumps(
                {"ok": True, "data": [{"xml_id": 101, "name": "Молоко 3.2%"}]},
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/link-only"}},
                ensure_ascii=False,
            )
        raise AssertionError(f"Unexpected tool call: {name}")


class DummyRedis:
    """Минимальный async-Redis для тестов идемпотентности."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        del ex
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def delete(self, key: str) -> int:
        existed = key in self.data
        self.data.pop(key, None)
        return int(existed)


class DummyBrokenRedis:
    """Redis-заглушка, имитирующая деградацию сети."""

    async def get(self, key: str) -> str | None:
        del key
        raise RuntimeError("redis unavailable")

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        del key, value, ex, nx
        raise RuntimeError("redis unavailable")

    async def delete(self, key: str) -> int:
        del key
        raise RuntimeError("redis unavailable")


class CountingLinkStore(InMemoryAccountLinkStore):
    def __init__(self) -> None:
        super().__init__(links={}, codes={})
        self.consume_calls = 0

    async def consume_link_code(
        self,
        voice_user_id: str,
        code: str,
    ) -> dict[str, object]:
        self.consume_calls += 1
        return await super().consume_link_code(voice_user_id, code)


class _DummyVoiceOrderClient:
    async def create_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, object]:
        assert user_id == 777
        assert voice_user_id == "alice-voice-777"
        assert "молоко" in utterance
        return {
            "ok": True,
            "assistant_text": "Готово",
            "cart_link": "https://shop.example/cart/voice-777",
            "total_rub": 510.0,
            "items_count": 2,
        }


class _FailingVoiceOrderClient:
    async def create_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, object]:
        del user_id, voice_user_id, utterance
        raise RuntimeError("voice-order unavailable")


class _ZeroCountVoiceOrderClient:
    async def create_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, object]:
        del user_id, voice_user_id, utterance
        return {
            "ok": True,
            "assistant_text": "Готово",
            "cart_link": "https://shop.example/cart/zero",
            "total_rub": 510.0,
            "items_count": 0,
        }


class _AsyncVoiceOrderClient:
    def __init__(self, status_payload: dict[str, object] | None = None) -> None:
        self.start_calls = 0
        self.status_calls = 0
        self.status_payload = status_payload or {
            "ok": True,
            "status": "done",
            "cart_link": "https://shop.example/cart/async",
            "total_rub": 620.0,
            "items_count": 3,
        }

    async def create_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, object]:
        del user_id, voice_user_id, utterance
        raise AssertionError("Legacy /order path should not be used in async mode")

    async def start_order(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        utterance: str,
    ) -> dict[str, object]:
        self.start_calls += 1
        assert user_id == 781
        assert voice_user_id == "alice-voice-781"
        assert "молоко" in utterance
        return {"ok": True, "status": "processing", "job_id": "job-781"}

    async def get_order_status(
        self,
        *,
        user_id: int,
        voice_user_id: str,
        job_id: str | None = None,
    ) -> dict[str, object]:
        self.status_calls += 1
        assert user_id == 781
        assert voice_user_id == "alice-voice-781"
        assert job_id is None
        return self.status_payload


@pytest.mark.asyncio
async def test_redis_idempotency_store_roundtrip():
    redis = DummyRedis()
    store = RedisIdempotencyStore(redis)
    key = "k-1"

    started = await store.try_start(key, ttl_seconds=30)
    assert started is True
    started_again = await store.try_start(key, ttl_seconds=30)
    assert started_again is False

    expected = VoiceOrderResult(
        ok=True,
        voice_text="OK",
        cart_link="https://shop.example/cart/123",
        total_rub=350.0,
        items_count=2,
        delivery=DeliveryResult(
            status="delivered",
            channel="alice_app_card",
            button_title="Открыть корзину",
            button_url="https://shop.example/cart/123",
        ),
    )
    await store.mark_done(key, expected, ttl_seconds=30)
    loaded = await store.get_done(key)

    assert loaded == expected

    await store.clear(key)
    assert await store.get_done(key) is None


@pytest.mark.asyncio
async def test_redis_idempotency_store_fallbacks_to_memory_on_errors():
    store = RedisIdempotencyStore(DummyBrokenRedis())
    key = "k-broken"
    started = await store.try_start(key, ttl_seconds=30)
    assert started is True
    started_again = await store.try_start(key, ttl_seconds=30)
    assert started_again is False

    expected = VoiceOrderResult(ok=True, voice_text="OK")
    await store.mark_done(key, expected, ttl_seconds=30)
    loaded = await store.get_done(key)
    assert loaded == expected


@pytest.mark.asyncio
async def test_orchestrator_success():
    mcp = FakeMCPClient()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-1",
        utterance="Алиса, запусти навык, покупка во вкусвилле. И закажи молоко",
    )

    assert result.ok is True
    assert result.cart_link == "https://shop.example/cart/abc"
    assert result.total_rub == 430.0
    assert result.delivery is not None
    assert result.delivery.channel == "alice_app_card"
    assert "ссылку отправила в приложение" in result.voice_text.lower()
    assert ("vkusvill_products_search", {"q": "молоко", "limit": 5}) in mcp.calls


@pytest.mark.asyncio
async def test_orchestrator_rejects_too_long_utterance():
    mcp = FakeMCPClient()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
        max_utterance_chars=12,
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-long",
        utterance="закажи молоко и яйца",
    )

    assert result.ok is False
    assert result.error_code == "utterance_too_long"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_orchestrator_rejects_too_many_products():
    mcp = FakeMCPClient()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
        max_products_per_order=2,
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-many",
        utterance="закажи молоко, хлеб, сыр",
    )

    assert result.ok is False
    assert result.error_code == "too_many_products"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_orchestrator_order_rate_limited():
    mcp = FakeMCPClient()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
        order_rate_limiter=InMemoryRateLimiter(),
        order_rate_limit=1,
        order_rate_window_seconds=60,
    )

    first = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-rate-order",
        utterance="закажи молоко",
    )
    second = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-rate-order",
        utterance="закажи кефир",
    )

    assert first.ok is True
    assert second.ok is False
    assert second.error_code == "order_rate_limited"


@pytest.mark.asyncio
async def test_orchestrator_link_code_rate_limited():
    mcp = FakeMCPClient()
    links = CountingLinkStore()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        account_links=links,
        idempotency_store=InMemoryIdempotencyStore(),
        link_code_rate_limiter=InMemoryRateLimiter(),
        link_code_rate_limit=1,
        link_code_rate_window_seconds=600,
    )

    first = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-rate-link",
        utterance="код 111111",
    )
    second = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-rate-link",
        utterance="код 222222",
    )

    assert first.ok is False
    assert first.error_code == "invalid_code"
    assert second.ok is False
    assert second.error_code == "link_rate_limited"
    assert links.consume_calls == 1


@pytest.mark.asyncio
async def test_orchestrator_supports_alternative_cart_payload():
    mcp = FakeMCPClientAltCartPayload()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-7",
        utterance="закажи молоко",
    )

    assert result.ok is True
    assert result.cart_link == "https://shop.example/cart/alt"
    assert result.total_rub == 640.0
    assert result.items_count == 2
    assert "2 позиций" in result.voice_text
    assert "640" in result.voice_text


@pytest.mark.asyncio
async def test_orchestrator_items_count_falls_back_to_requested_products():
    mcp = FakeMCPClientLinkOnly()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-8",
        utterance="закажи молоко",
    )

    assert result.ok is True
    assert result.cart_link == "https://shop.example/cart/link-only"
    assert result.items_count == 1
    assert "1 позиций" in result.voice_text
    assert "0 позиций" not in result.voice_text


@pytest.mark.asyncio
async def test_orchestrator_requires_linking():
    mcp = FakeMCPClient()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        account_links=InMemoryAccountLinkStore({}),
        require_linked_account=True,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-2",
        utterance="закажи молоко",
    )

    assert result.ok is False
    assert result.requires_linking is True
    assert result.error_code == "account_not_linked"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_orchestrator_link_code_then_order():
    mcp = FakeMCPClient()
    links = InMemoryAccountLinkStore(links={}, codes={"123456": 777})
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        account_links=links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        require_linked_account=True,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    link_result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-5",
        utterance="код 123456",
    )
    assert link_result.ok is True
    assert "аккаунт привязан" in link_result.voice_text.lower()

    order_result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-5",
        utterance="закажи молоко",
    )
    assert order_result.ok is True
    assert order_result.cart_link == "https://shop.example/cart/abc"


@pytest.mark.asyncio
async def test_orchestrator_uses_voice_order_api_for_linked_user():
    mcp = FakeMCPClient()
    links = InMemoryAccountLinkStore(links={"alice-voice-777": 777}, codes={})
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        voice_order_client=_DummyVoiceOrderClient(),  # type: ignore[arg-type]
        account_links=links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        require_linked_account=True,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-voice-777",
        utterance="закажи молоко и яйца",
    )

    assert result.ok is True
    assert result.cart_link == "https://shop.example/cart/voice-777"
    assert result.total_rub == 510.0
    assert result.items_count == 2
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_orchestrator_fallbacks_to_mcp_when_voice_order_api_unavailable():
    mcp = FakeMCPClient()
    links = InMemoryAccountLinkStore(links={"alice-voice-778": 778}, codes={})
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        voice_order_client=_FailingVoiceOrderClient(),  # type: ignore[arg-type]
        account_links=links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        require_linked_account=True,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-voice-778",
        utterance="закажи молоко",
    )

    assert result.ok is True
    assert result.cart_link == "https://shop.example/cart/abc"
    assert ("vkusvill_products_search", {"q": "молоко", "limit": 5}) in mcp.calls


@pytest.mark.asyncio
async def test_orchestrator_returns_fast_error_when_voice_api_unavailable_and_no_fallback():
    mcp = FakeMCPClient()
    links = InMemoryAccountLinkStore(links={"alice-voice-780": 780}, codes={})
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        voice_order_client=_FailingVoiceOrderClient(),  # type: ignore[arg-type]
        voice_api_fallback_to_mcp=False,
        account_links=links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        require_linked_account=True,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-voice-780",
        utterance="закажи молоко",
    )

    assert result.ok is False
    assert result.error_code == "voice_order_api_unavailable"
    assert "перегружен" in result.voice_text.lower()
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_orchestrator_voice_order_zero_items_uses_fallback_count():
    mcp = FakeMCPClient()
    links = InMemoryAccountLinkStore(links={"alice-voice-779": 779}, codes={})
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        voice_order_client=_ZeroCountVoiceOrderClient(),  # type: ignore[arg-type]
        account_links=links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        require_linked_account=True,
        idempotency_store=InMemoryIdempotencyStore(),
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-voice-779",
        utterance="закажи молоко и яйца",
    )

    assert result.ok is True
    assert result.items_count == 2
    assert "0 позиций" not in result.voice_text


@pytest.mark.asyncio
async def test_orchestrator_async_voice_order_start_returns_processing_prompt():
    mcp = FakeMCPClient()
    links = InMemoryAccountLinkStore(links={"alice-voice-781": 781}, codes={})
    voice_client = _AsyncVoiceOrderClient()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        voice_order_client=voice_client,  # type: ignore[arg-type]
        account_links=links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        require_linked_account=True,
        idempotency_store=InMemoryIdempotencyStore(),
        voice_order_async_mode=True,
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-voice-781",
        utterance="закажи молоко и яйца",
    )

    assert result.ok is True
    assert result.error_code == "order_processing"
    assert "проверь заказ" in result.voice_text.lower()
    assert voice_client.start_calls == 1
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_orchestrator_async_voice_order_status_returns_cart():
    mcp = FakeMCPClient()
    links = InMemoryAccountLinkStore(links={"alice-voice-781": 781}, codes={})
    voice_client = _AsyncVoiceOrderClient(
        status_payload={
            "ok": True,
            "status": "done",
            "cart_link": "https://shop.example/cart/async-status",
            "total_rub": 777.0,
            "items_count": 4,
        },
    )
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        voice_order_client=voice_client,  # type: ignore[arg-type]
        account_links=links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        require_linked_account=True,
        idempotency_store=InMemoryIdempotencyStore(),
        voice_order_async_mode=True,
    )

    result = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-voice-781",
        utterance="проверь заказ",
    )

    assert result.ok is True
    assert result.cart_link == "https://shop.example/cart/async-status"
    assert result.total_rub == 777.0
    assert result.items_count == 4
    assert voice_client.status_calls == 1
    assert mcp.calls == []


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("код 123456", "123456"),
        ("код 1,2,3,4,5,6", "123456"),
        ("код 1 2 3 4 5 6", "123456"),
        ("код 1-2-3-4-5-6", "123456"),
        ("код один два три четыре пять шесть", "123456"),
        ("код восемьсот сорок два сто восемьдесят два", "842182"),
        ("код восемьсот сорок два тире сто восемьдесят два", "842182"),
        ("код 842 182", "842182"),
        ("код восемьсот два сто пять", "802105"),
        ("code 1, 2, 3, 4, 5, 6", "123456"),
        ("закажи молоко", None),
    ],
)
def test_extract_link_code_variants(utterance: str, expected: str | None):
    assert AliceOrderOrchestrator.extract_link_code(utterance) == expected


def test_extract_cart_parses_root_fallback_payload():
    result = AliceOrderOrchestrator._extract_cart(
        {
            "cart_link": "https://shop.example/cart/root",
            "total": "780.5",
            "count": "3",
        },
    )
    assert result == {
        "link": "https://shop.example/cart/root",
        "total_rub": 780.5,
        "items_count": 3,
    }


@pytest.mark.asyncio
async def test_orchestrator_idempotent_duplicate_request():
    mcp = FakeMCPClient()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
    )

    first = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-3",
        utterance="закажи молоко",
    )
    second = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-3",
        utterance="закажи молоко",
    )

    assert first.ok is True
    assert second.ok is True
    assert first.cart_link == second.cart_link

    create_calls = [call for call in mcp.calls if call[0] == "vkusvill_cart_link_create"]
    assert len(create_calls) == 1


@pytest.mark.asyncio
async def test_orchestrator_idempotency_uses_minute_bucket(monkeypatch: pytest.MonkeyPatch):
    mcp = FakeMCPClient()
    orchestrator = AliceOrderOrchestrator(
        mcp_client=mcp,  # type: ignore[arg-type]
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
    )

    buckets = iter((100, 100, 101))

    def _fake_bucket() -> int:
        return next(buckets)

    monkeypatch.setattr(orchestrator, "_current_minute_bucket", _fake_bucket)

    first = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-6",
        utterance="Закажи молоко",
    )
    second = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-6",
        utterance="закажи   молоко",
    )
    third = await orchestrator.create_order_from_utterance(
        voice_user_id="alice-user-6",
        utterance="Закажи молоко",
    )

    assert first.ok is True
    assert second.ok is True
    assert third.ok is True

    create_calls = [call for call in mcp.calls if call[0] == "vkusvill_cart_link_create"]
    assert len(create_calls) == 2


def test_cloud_function_handler_response_with_button(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")

    class DummyOrchestrator:
        async def create_order_from_utterance(
            self,
            voice_user_id: str,
            utterance: str,
        ) -> VoiceOrderResult:
            assert voice_user_id == "alice-user-4"
            assert utterance == "закажи молоко"
            return VoiceOrderResult(
                ok=True,
                voice_text="Готово",
                cart_link="https://shop.example/cart/xyz",
                total_rub=320.0,
                items_count=1,
                delivery=DeliveryResult(
                    status="delivered",
                    channel="alice_app_card",
                    button_title="Открыть корзину",
                    button_url="https://shop.example/cart/xyz",
                ),
            )

    module._RUNTIME = module._Runtime(orchestrator=DummyOrchestrator())

    event = {
        "version": "1.0",
        "session": {"user": {"user_id": "alice-user-4"}},
        "request": {"command": "закажи молоко"},
    }
    response = module.handler(event, None)

    assert response["response"]["text"] == "Готово"
    assert response["response"]["end_session"] is False
    assert response["response"]["buttons"][0]["url"] == "https://shop.example/cart/xyz"


def test_cloud_function_writes_langfuse_trace(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")

    class DummyOrchestrator:
        async def create_order_from_utterance(
            self,
            voice_user_id: str,
            utterance: str,
        ) -> VoiceOrderResult:
            assert voice_user_id == "alice-user-lf"
            assert utterance == "закажи молоко"
            return VoiceOrderResult(
                ok=True,
                voice_text="Готово",
                cart_link="https://shop.example/cart/lf",
                total_rub=250.0,
                items_count=1,
            )

    class SpanSpy:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs):
            self.end_calls.append(kwargs)

    class TraceSpy:
        def __init__(self) -> None:
            self.span_spy = SpanSpy()
            self.update_calls: list[dict[str, object]] = []

        def span(self, **kwargs):
            del kwargs
            return self.span_spy

        def update(self, **kwargs):
            self.update_calls.append(kwargs)

    class LangfuseSpy:
        def __init__(self) -> None:
            self.trace_calls: list[dict[str, object]] = []
            self.flush_calls = 0
            self.trace_spy = TraceSpy()

        def trace(self, **kwargs):
            self.trace_calls.append(kwargs)
            return self.trace_spy

        def flush(self) -> None:
            self.flush_calls += 1

    langfuse_spy = LangfuseSpy()
    module._RUNTIME = module._Runtime(
        orchestrator=DummyOrchestrator(),
        langfuse=langfuse_spy,  # type: ignore[arg-type]
    )

    event = {
        "version": "1.0",
        "session": {
            "session_id": "alice-session-1",
            "user": {"user_id": "alice-user-lf"},
            "skill_id": "alice.skill.id",
        },
        "request": {"command": "закажи молоко"},
    }
    response = module.handler(event, None)

    assert response["response"]["text"] == "Готово"
    assert langfuse_spy.flush_calls == 1
    assert len(langfuse_spy.trace_calls) == 1
    assert langfuse_spy.trace_calls[0]["name"] == "alice-order"
    assert langfuse_spy.trace_calls[0]["user_id"] == "alice-user-lf"
    assert langfuse_spy.trace_calls[0]["session_id"] == "alice-session-1"
    assert langfuse_spy.trace_spy.span_spy.end_calls[0]["output"]["ok"] is True
    assert langfuse_spy.trace_spy.update_calls[0]["metadata"]["ok"] is True


def test_cloud_function_returns_graceful_timeout(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    monkeypatch.setenv("ALICE_ORCHESTRATION_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("ALICE_HANDLER_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ALICE_LANGFUSE_FLUSH_TIMEOUT_SECONDS", "0.2")

    class SlowOrchestrator:
        async def create_order_from_utterance(
            self,
            voice_user_id: str,
            utterance: str,
        ) -> VoiceOrderResult:
            assert voice_user_id == "alice-user-timeout"
            assert utterance == "закажи молоко"
            await asyncio.sleep(0.7)
            return VoiceOrderResult(ok=True, voice_text="Late success")

    class SpanSpy:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs):
            self.end_calls.append(kwargs)

    class TraceSpy:
        def __init__(self) -> None:
            self.span_spy = SpanSpy()
            self.update_calls: list[dict[str, object]] = []

        def span(self, **kwargs):
            del kwargs
            return self.span_spy

        def update(self, **kwargs):
            self.update_calls.append(kwargs)

    class LangfuseSpy:
        def __init__(self) -> None:
            self.trace_spy = TraceSpy()
            self.flush_calls = 0

        def trace(self, **kwargs):
            del kwargs
            return self.trace_spy

        def flush(self) -> None:
            self.flush_calls += 1

    langfuse_spy = LangfuseSpy()
    module._RUNTIME = module._Runtime(
        orchestrator=SlowOrchestrator(),
        langfuse=langfuse_spy,  # type: ignore[arg-type]
    )

    event = {
        "version": "1.0",
        "session": {
            "session_id": "alice-session-timeout",
            "user": {"user_id": "alice-user-timeout"},
            "skill_id": "alice.skill.id",
        },
        "request": {"command": "закажи молоко"},
    }
    response = module.handler(event, None)

    assert "дольше обычного" in response["response"]["text"].lower()
    assert langfuse_spy.flush_calls == 1
    assert langfuse_spy.trace_spy.span_spy.end_calls[0]["metadata"]["status"] == "timeout"
    assert langfuse_spy.trace_spy.update_calls[0]["metadata"]["status"] == "timeout"


def test_cloud_function_http_proxy_response(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")

    class DummyOrchestrator:
        async def create_order_from_utterance(
            self,
            voice_user_id: str,
            utterance: str,
        ) -> VoiceOrderResult:
            assert voice_user_id == "alice-http-user"
            assert utterance == "закажи молоко"
            return VoiceOrderResult(ok=True, voice_text="OK")

    module._RUNTIME = module._Runtime(orchestrator=DummyOrchestrator())

    inner_event = {
        "version": "1.0",
        "session": {"user": {"user_id": "alice-http-user"}},
        "request": {"command": "закажи молоко"},
    }
    http_event = {
        "httpMethod": "POST",
        "requestContext": {},
        "body": json.dumps(inner_event, ensure_ascii=False),
    }

    response = module.handler(http_event, None)

    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload["response"]["text"] == "OK"
    assert response["headers"]["Content-Type"].startswith("application/json")


def test_cloud_function_raw_requires_skill_id(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    monkeypatch.setenv("ALICE_SKILL_ID", "alice.skill.expected")

    event = {
        "version": "1.0",
        "session": {"user": {"user_id": "alice-user-raw"}, "skill_id": "alice.skill.other"},
        "request": {"command": "закажи молоко"},
    }
    response = module.handler(event, None)

    assert response["response"]["end_session"] is True
    assert "доступ" in response["response"]["text"].lower()


def test_cloud_function_raw_accepts_skill_id(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    monkeypatch.setenv("ALICE_SKILL_ID", "alice.skill.expected")

    class DummyOrchestrator:
        async def create_order_from_utterance(
            self,
            voice_user_id: str,
            utterance: str,
        ) -> VoiceOrderResult:
            assert voice_user_id == "alice-user-raw"
            assert utterance == "закажи молоко"
            return VoiceOrderResult(ok=True, voice_text="OK")

    module._RUNTIME = module._Runtime(orchestrator=DummyOrchestrator())
    event = {
        "version": "1.0",
        "session": {"user": {"user_id": "alice-user-raw"}, "skill_id": "alice.skill.expected"},
        "request": {"command": "закажи молоко"},
    }
    response = module.handler(event, None)

    assert response["response"]["text"] == "OK"


def test_cloud_function_http_proxy_requires_webhook_token(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    monkeypatch.setenv("ALICE_WEBHOOK_TOKEN", "super-secret")

    event = {
        "httpMethod": "POST",
        "requestContext": {},
        "headers": {"content-type": "application/json"},
        "body": json.dumps(
            {
                "version": "1.0",
                "session": {"user": {"user_id": "alice-http-user"}},
                "request": {"command": "закажи молоко"},
            },
            ensure_ascii=False,
        ),
    }

    response = module.handler(event, None)
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["error"] == "forbidden"


def test_cloud_function_http_proxy_accepts_valid_webhook_token(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    monkeypatch.setenv("ALICE_WEBHOOK_TOKEN", "super-secret")

    class DummyOrchestrator:
        async def create_order_from_utterance(
            self,
            voice_user_id: str,
            utterance: str,
        ) -> VoiceOrderResult:
            assert voice_user_id == "alice-http-user"
            assert utterance == "закажи молоко"
            return VoiceOrderResult(ok=True, voice_text="OK")

    module._RUNTIME = module._Runtime(orchestrator=DummyOrchestrator())

    event = {
        "httpMethod": "POST",
        "requestContext": {},
        "headers": {"x-alice-webhook-token": "super-secret"},
        "body": json.dumps(
            {
                "version": "1.0",
                "session": {"user": {"user_id": "alice-http-user"}},
                "request": {"command": "закажи молоко"},
            },
            ensure_ascii=False,
        ),
    }

    response = module.handler(event, None)
    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload["response"]["text"] == "OK"


def test_cloud_function_http_proxy_rejects_wrong_skill_id(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    monkeypatch.setenv("ALICE_WEBHOOK_TOKEN", "super-secret")
    monkeypatch.setenv("ALICE_SKILL_ID", "alice.skill.expected")

    event = {
        "httpMethod": "POST",
        "requestContext": {},
        "headers": {"x-alice-webhook-token": "super-secret"},
        "body": json.dumps(
            {
                "version": "1.0",
                "session": {
                    "user": {"user_id": "alice-http-user"},
                    "skill_id": "alice.skill.other",
                },
                "request": {"command": "закажи молоко"},
            },
            ensure_ascii=False,
        ),
    }

    response = module.handler(event, None)
    assert response["statusCode"] == 403
    assert json.loads(response["body"])["error"] == "forbidden"


@pytest.mark.asyncio
async def test_runtime_db_failure_degrades_to_guest(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    module._RUNTIME = None

    class DummyMCPClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    async def raise_db_error(*args, **kwargs):
        raise TimeoutError("db timeout")

    monkeypatch.setattr(module, "VkusvillMCPClient", DummyMCPClient)
    monkeypatch.setattr(module.asyncpg, "create_pool", raise_db_error)
    monkeypatch.setenv("ALICE_DATABASE_URL", "postgresql://x:y@db:6432/vkuswill")
    monkeypatch.setenv("ALICE_REQUIRE_LINKED_ACCOUNT", "true")
    monkeypatch.setenv("ALICE_DEGRADE_TO_GUEST_ON_DB_ERROR", "true")
    monkeypatch.setenv("ALICE_DB_CONNECT_TIMEOUT_SECONDS", "1")

    runtime = await module._get_runtime()

    assert runtime.orchestrator._require_linked_account is False
    module._RUNTIME = None


@pytest.mark.asyncio
async def test_runtime_db_failure_fail_closed_linking(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    module._RUNTIME = None

    class DummyMCPClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    async def raise_db_error(*args, **kwargs):
        raise TimeoutError("db timeout")

    monkeypatch.setattr(module, "VkusvillMCPClient", DummyMCPClient)
    monkeypatch.setattr(module.asyncpg, "create_pool", raise_db_error)
    monkeypatch.setenv("ALICE_DATABASE_URL", "postgresql://x:y@db:6432/vkuswill")
    monkeypatch.setenv("ALICE_REQUIRE_LINKED_ACCOUNT", "true")
    monkeypatch.setenv("ALICE_DEGRADE_TO_GUEST_ON_DB_ERROR", "false")
    monkeypatch.setenv("ALICE_LINKING_FAIL_CLOSED", "true")

    runtime = await module._get_runtime()
    assert runtime.orchestrator._require_linked_account is True
    assert isinstance(runtime.orchestrator._account_links, UnavailableAccountLinkStore)
    module._RUNTIME = None


@pytest.mark.asyncio
async def test_runtime_uses_redis_idempotency_store(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    module._RUNTIME = None

    class DummyMCPClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class DummyRedisClient:
        async def get(self, key: str) -> str | None:
            del key
            return None

        async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False):
            del key, value, ex, nx
            return True

        async def delete(self, key: str) -> int:
            del key
            return 1

    async def fake_create_redis_client(*args, **kwargs):
        del args, kwargs
        return DummyRedisClient()

    monkeypatch.setattr(module, "VkusvillMCPClient", DummyMCPClient)
    monkeypatch.setattr(module, "create_redis_client", fake_create_redis_client)
    monkeypatch.setenv("ALICE_REDIS_URL", "redis://localhost:6379/0")

    runtime = await module._get_runtime()
    assert isinstance(runtime.orchestrator._idempotency_store, RedisIdempotencyStore)
    module._RUNTIME = None


@pytest.mark.asyncio
async def test_runtime_reads_async_order_flags(monkeypatch):
    module = importlib.import_module("vkuswill_bot.alice_skill.handler")
    module._RUNTIME = None

    class DummyMCPClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr(module, "VkusvillMCPClient", DummyMCPClient)
    monkeypatch.setenv("ALICE_ORDER_ASYNC_MODE", "false")
    monkeypatch.setenv("ALICE_VOICE_API_FALLBACK_TO_MCP", "true")

    runtime = await module._get_runtime()

    assert runtime.orchestrator._voice_order_async_mode is False
    assert runtime.orchestrator._voice_api_fallback_to_mcp is True
    module._RUNTIME = None


@pytest.mark.asyncio
async def test_http_account_link_store_resolve_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/resolve")
        assert request.headers["X-Voice-Link-Api-Key"] == "key-1"
        body = json.loads(request.content.decode("utf-8"))
        assert body["provider"] == "alice"
        assert body["voice_user_id"] == "alice-u-1"
        return httpx.Response(200, json={"ok": True, "user_id": 777})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test.local")
    store = HttpAccountLinkStore(
        base_url="http://test.local/voice-link",
        api_key="key-1",
        client=client,
    )

    user_id = await store.resolve_internal_user_id("alice-u-1")
    assert user_id == 777


@pytest.mark.asyncio
async def test_http_account_link_store_consume_unavailable():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"ok": False})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test.local")
    store = HttpAccountLinkStore(
        base_url="http://test.local/voice-link",
        api_key="key-1",
        client=client,
    )

    result = await store.consume_link_code("alice-u-2", "123456")
    assert result["ok"] is False
    assert result["reason"] == "linking_unavailable"


@pytest.mark.asyncio
async def test_http_voice_order_client_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/order")
        assert request.headers["X-Voice-Link-Api-Key"] == "key-1"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "user_id": 42,
            "voice_user_id": "alice-u-1",
            "utterance": "Собери корзину: молоко",
        }
        return httpx.Response(
            200,
            json={
                "ok": True,
                "assistant_text": "Готово",
                "cart_link": "https://shop.example/cart/42",
                "total_rub": 320.0,
                "items_count": 1,
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test.local")
    order_client = HttpVoiceOrderClient(
        base_url="http://test.local/voice-link",
        api_key="key-1",
        client=client,
    )

    payload = await order_client.create_order(
        user_id=42,
        voice_user_id="alice-u-1",
        utterance="Собери корзину: молоко",
    )
    assert payload["ok"] is True
    assert payload["cart_link"] == "https://shop.example/cart/42"


@pytest.mark.asyncio
async def test_http_voice_order_client_start_and_status():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if request.url.path.endswith("/order/start"):
            assert body == {
                "user_id": 42,
                "voice_user_id": "alice-u-1",
                "utterance": "Собери корзину: молоко",
            }
            return httpx.Response(200, json={"ok": True, "status": "processing", "job_id": "job-1"})
        assert request.url.path.endswith("/order/status")
        assert body == {
            "user_id": 42,
            "voice_user_id": "alice-u-1",
            "job_id": "job-1",
        }
        return httpx.Response(
            200,
            json={
                "ok": True,
                "status": "done",
                "cart_link": "https://shop.example/cart/42",
                "total_rub": 320.0,
                "items_count": 1,
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://test.local")
    order_client = HttpVoiceOrderClient(
        base_url="http://test.local/voice-link",
        api_key="key-1",
        client=client,
    )

    start_payload = await order_client.start_order(
        user_id=42,
        voice_user_id="alice-u-1",
        utterance="Собери корзину: молоко",
    )
    assert start_payload["status"] == "processing"

    status_payload = await order_client.get_order_status(
        user_id=42,
        voice_user_id="alice-u-1",
        job_id="job-1",
    )
    assert status_payload["status"] == "done"
    assert status_payload["cart_link"] == "https://shop.example/cart/42"
