"""Тесты MVP-интеграции навыка Алисы."""

from __future__ import annotations

import importlib
import json

import httpx
import pytest

from vkuswill_bot.alice_skill.account_linking import HttpAccountLinkStore, InMemoryAccountLinkStore
from vkuswill_bot.alice_skill.delivery import AliceAppDeliveryAdapter
from vkuswill_bot.alice_skill.idempotency import InMemoryIdempotencyStore
from vkuswill_bot.alice_skill.models import DeliveryResult, VoiceOrderResult
from vkuswill_bot.alice_skill.orchestrator import AliceOrderOrchestrator


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


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("код 123456", "123456"),
        ("код 1,2,3,4,5,6", "123456"),
        ("код 1 2 3 4 5 6", "123456"),
        ("код 1-2-3-4-5-6", "123456"),
        ("код один два три четыре пять шесть", "123456"),
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
