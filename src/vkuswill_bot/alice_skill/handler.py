"""Yandex Cloud Function handler для навыка Алисы."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import asyncpg

try:
    import sniffio
except ImportError:  # pragma: no cover - optional in local dev, bundled in function artifact
    sniffio = None

from vkuswill_bot.alice_skill.account_linking import (
    HttpAccountLinkStore,
    InMemoryAccountLinkStore,
    PostgresAccountLinkStore,
)
from vkuswill_bot.alice_skill.delivery import AliceAppDeliveryAdapter
from vkuswill_bot.alice_skill.idempotency import InMemoryIdempotencyStore
from vkuswill_bot.alice_skill.models import VoiceOrderResult
from vkuswill_bot.alice_skill.orchestrator import AliceOrderOrchestrator
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.user_store import UserStore

DEFAULT_MCP_URL = "https://mcp001.vkusvill.ru/mcp"
_RUNTIME: _Runtime | None = None
_EVENT_LOOP: asyncio.AbstractEventLoop | None = None
logger = logging.getLogger(__name__)


@dataclass
class _Runtime:
    orchestrator: AliceOrderOrchestrator


def _parse_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _load_links() -> dict[str, int]:
    raw = os.getenv("ALICE_ACCOUNT_LINKS_JSON", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    links: dict[str, int] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, int):
            links[key] = value
        elif isinstance(key, str) and isinstance(value, str) and value.isdigit():
            links[key] = int(value)
    return links


def _load_codes() -> dict[str, int]:
    raw = os.getenv("ALICE_LINK_CODES_JSON", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    codes: dict[str, int] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, int):
            codes[key] = value
        elif isinstance(key, str) and isinstance(value, str) and value.isdigit():
            codes[key] = int(value)
    return codes


async def _get_runtime() -> _Runtime:
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME

    mcp_url = os.getenv("ALICE_MCP_SERVER_URL", DEFAULT_MCP_URL)
    mcp_api_key = os.getenv("ALICE_MCP_API_KEY", "")
    require_linked_account = _parse_bool_env("ALICE_REQUIRE_LINKED_ACCOUNT", default=False)
    degrade_to_guest = _parse_bool_env("ALICE_DEGRADE_TO_GUEST_ON_DB_ERROR", default=False)
    idempotency_ttl = _parse_int_env("ALICE_IDEMPOTENCY_TTL_SECONDS", default=90)
    db_connect_timeout = _parse_float_env("ALICE_DB_CONNECT_TIMEOUT_SECONDS", default=3.0)
    link_api_timeout = _parse_float_env("ALICE_LINK_API_TIMEOUT_SECONDS", default=5.0)
    link_api_verify_ssl = _parse_bool_env("ALICE_LINK_API_VERIFY_SSL", default=True)
    link_api_url = os.getenv("ALICE_LINK_API_URL", "").strip()
    link_api_key = os.getenv("ALICE_LINK_API_KEY", "").strip()
    database_url = os.getenv("ALICE_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    effective_require_linked = require_linked_account

    mcp_client = VkusvillMCPClient(
        server_url=mcp_url,
        api_key=mcp_api_key or None,
    )
    if link_api_url and link_api_key:
        account_links = HttpAccountLinkStore(
            base_url=link_api_url,
            api_key=link_api_key,
            provider="alice",
            timeout_seconds=link_api_timeout,
            verify_ssl=link_api_verify_ssl,
        )
    elif database_url:
        try:
            pool = await asyncpg.create_pool(
                dsn=database_url,
                min_size=1,
                max_size=3,
                timeout=db_connect_timeout,
                command_timeout=db_connect_timeout,
            )
            user_store = UserStore(pool, schema_ready=True)
            account_links = PostgresAccountLinkStore(user_store, provider="alice")
        except Exception:
            logger.exception("Alice skill: DB init failed, fallback mode")
            account_links = InMemoryAccountLinkStore(_load_links(), codes=_load_codes())
            if degrade_to_guest:
                effective_require_linked = False
    else:
        account_links = InMemoryAccountLinkStore(_load_links(), codes=_load_codes())

    orchestrator = AliceOrderOrchestrator(
        mcp_client,
        account_links=account_links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=InMemoryIdempotencyStore(),
        require_linked_account=effective_require_linked,
        idempotency_ttl_seconds=idempotency_ttl,
    )
    _RUNTIME = _Runtime(orchestrator=orchestrator)
    return _RUNTIME


def _extract_utterance(event: dict[str, Any]) -> str:
    request = event.get("request", {})
    if not isinstance(request, dict):
        return ""
    utterance = request.get("original_utterance") or request.get("command")
    return utterance if isinstance(utterance, str) else ""


def _extract_voice_user_id(event: dict[str, Any]) -> str:
    session = event.get("session", {})
    if not isinstance(session, dict):
        return "anonymous"
    user = session.get("user", {})
    if not isinstance(user, dict):
        return "anonymous"
    user_id = user.get("user_id")
    return str(user_id) if user_id is not None else "anonymous"


def _to_alice_response(event: dict[str, Any], result: VoiceOrderResult) -> dict[str, Any]:
    response: dict[str, Any] = {
        "text": result.voice_text,
        "end_session": False,
    }
    if result.delivery and result.delivery.button_url and result.delivery.button_title:
        response["buttons"] = [
            {
                "title": result.delivery.button_title,
                "url": result.delivery.button_url,
                "hide": True,
            }
        ]
    return {
        "version": event.get("version", "1.0"),
        "session": event.get("session", {}),
        "response": response,
    }


def _is_http_proxy_event(event: dict[str, Any]) -> bool:
    return "httpMethod" in event or "requestContext" in event


def _unwrap_http_event(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if not isinstance(body, str) or not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _wrap_http_response(payload: dict[str, Any], status_code: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


async def _handle_async(event: dict[str, Any]) -> dict[str, Any]:
    runtime = await _get_runtime()
    utterance = _extract_utterance(event)
    voice_user_id = _extract_voice_user_id(event)

    if not utterance.strip():
        return {
            "version": event.get("version", "1.0"),
            "session": event.get("session", {}),
            "response": {
                "text": (
                    "Привет! Скажите, что добавить в корзину. "
                    "Например: закажи молоко и яйца."
                ),
                "end_session": False,
            },
        }

    result = await runtime.orchestrator.create_order_from_utterance(
        voice_user_id=voice_user_id,
        utterance=utterance,
    )
    return _to_alice_response(event, result)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Точка входа Yandex Cloud Function."""
    del context
    alice_event = event
    if _is_http_proxy_event(event):
        alice_event = _unwrap_http_event(event)
        if not alice_event:
            return _wrap_http_response(
                {"error": "invalid_request_body"},
                status_code=400,
            )

    global _EVENT_LOOP
    if _EVENT_LOOP is None or _EVENT_LOOP.is_closed():
        _EVENT_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_EVENT_LOOP)
    token = None
    if sniffio is not None and hasattr(sniffio, "current_async_library_cvar"):
        token = sniffio.current_async_library_cvar.set("asyncio")
    try:
        response = _EVENT_LOOP.run_until_complete(_handle_async(alice_event))
    finally:
        if token is not None and sniffio is not None:
            sniffio.current_async_library_cvar.reset(token)
    if _is_http_proxy_event(event):
        return _wrap_http_response(response)
    return response
