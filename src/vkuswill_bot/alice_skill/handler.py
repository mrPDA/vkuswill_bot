"""Yandex Cloud Function handler для навыка Алисы."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from dataclasses import dataclass
from dataclasses import field
from typing import Any

try:
    import sniffio
except ImportError:  # pragma: no cover - optional in local dev, bundled in function artifact
    sniffio = None

try:
    import asyncpg
except Exception as asyncpg_import_error:  # pragma: no cover - runtime guard for broken artifacts
    asyncpg = None
    _ASYNCPG_IMPORT_ERROR = asyncpg_import_error
else:
    _ASYNCPG_IMPORT_ERROR = None

from vkuswill_bot.alice_skill.account_linking import (
    HttpAccountLinkStore,
    InMemoryAccountLinkStore,
    PostgresAccountLinkStore,
    UnavailableAccountLinkStore,
)
from vkuswill_bot.alice_skill.delivery import AliceAppDeliveryAdapter
from vkuswill_bot.alice_skill.idempotency import InMemoryIdempotencyStore
from vkuswill_bot.alice_skill.idempotency import RedisIdempotencyStore
from vkuswill_bot.alice_skill.models import VoiceOrderResult
from vkuswill_bot.alice_skill.orchestrator import AliceOrderOrchestrator
from vkuswill_bot.alice_skill.rate_limit import InMemoryRateLimiter
from vkuswill_bot.alice_skill.rate_limit import RedisRateLimiter
from vkuswill_bot.services.langfuse_tracing import LangfuseService
from vkuswill_bot.services.mcp_client import VkusvillMCPClient

try:
    from vkuswill_bot.services.redis_client import create_redis_client
except Exception as redis_import_error:  # pragma: no cover - runtime guard for broken artifacts
    create_redis_client = None
    _REDIS_IMPORT_ERROR = redis_import_error
else:
    _REDIS_IMPORT_ERROR = None

DEFAULT_MCP_URL = "https://mcp001.vkusvill.ru/mcp"
DEFAULT_WEBHOOK_HEADER_NAME = "X-Alice-Webhook-Token"
_RUNTIME: _Runtime | None = None
_EVENT_LOOP: asyncio.AbstractEventLoop | None = None
logger = logging.getLogger(__name__)


@dataclass
class _Runtime:
    orchestrator: AliceOrderOrchestrator
    langfuse: LangfuseService = field(default_factory=lambda: LangfuseService(enabled=False))


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
    linking_fail_closed = _parse_bool_env("ALICE_LINKING_FAIL_CLOSED", default=True)
    degrade_to_guest = _parse_bool_env("ALICE_DEGRADE_TO_GUEST_ON_DB_ERROR", default=False)
    idempotency_ttl = _parse_int_env("ALICE_IDEMPOTENCY_TTL_SECONDS", default=90)
    idempotency_key_prefix = os.getenv("ALICE_IDEMPOTENCY_KEY_PREFIX", "alice:idem:")
    rate_limit_key_prefix = os.getenv("ALICE_RATE_LIMIT_KEY_PREFIX", "alice:rl:")
    order_rate_limit = _parse_int_env("ALICE_ORDER_RATE_LIMIT", default=12)
    order_rate_window_seconds = _parse_int_env("ALICE_ORDER_RATE_WINDOW_SECONDS", default=60)
    link_code_rate_limit = _parse_int_env("ALICE_LINK_CODE_RATE_LIMIT", default=6)
    link_code_rate_window_seconds = _parse_int_env(
        "ALICE_LINK_CODE_RATE_WINDOW_SECONDS",
        default=600,
    )
    max_utterance_chars = _parse_int_env("ALICE_MAX_UTTERANCE_CHARS", default=512)
    max_products_per_order = _parse_int_env("ALICE_MAX_PRODUCTS_PER_ORDER", default=20)
    langfuse_enabled = _parse_bool_env(
        "ALICE_LANGFUSE_ENABLED",
        default=_parse_bool_env("LANGFUSE_ENABLED", default=False),
    )
    langfuse_public_key = os.getenv("ALICE_LANGFUSE_PUBLIC_KEY") or os.getenv(
        "LANGFUSE_PUBLIC_KEY",
        "",
    )
    langfuse_secret_key = os.getenv("ALICE_LANGFUSE_SECRET_KEY") or os.getenv(
        "LANGFUSE_SECRET_KEY",
        "",
    )
    langfuse_host = os.getenv("ALICE_LANGFUSE_HOST") or os.getenv(
        "LANGFUSE_HOST",
        "https://cloud.langfuse.com",
    )
    langfuse_anonymize_messages = _parse_bool_env(
        "ALICE_LANGFUSE_ANONYMIZE_MESSAGES",
        default=_parse_bool_env("LANGFUSE_ANONYMIZE_MESSAGES", default=True),
    )
    redis_url = (os.getenv("ALICE_REDIS_URL", "") or os.getenv("REDIS_URL", "")).strip()
    db_connect_timeout = _parse_float_env("ALICE_DB_CONNECT_TIMEOUT_SECONDS", default=3.0)
    link_api_timeout = _parse_float_env("ALICE_LINK_API_TIMEOUT_SECONDS", default=5.0)
    link_api_verify_ssl = _parse_bool_env("ALICE_LINK_API_VERIFY_SSL", default=True)
    link_api_url = os.getenv("ALICE_LINK_API_URL", "").strip()
    link_api_key = os.getenv("ALICE_LINK_API_KEY", "").strip()
    database_url = os.getenv("ALICE_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    effective_require_linked = require_linked_account
    misconfigured_link_api = bool(link_api_url or link_api_key) and not (
        link_api_url and link_api_key
    )

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
    elif misconfigured_link_api:
        logger.warning(
            "Alice skill: ALICE_LINK_API_URL/ALICE_LINK_API_KEY misconfigured, fail_closed=%s",
            linking_fail_closed,
        )
        if require_linked_account and linking_fail_closed:
            account_links = UnavailableAccountLinkStore()
        else:
            account_links = InMemoryAccountLinkStore(_load_links(), codes=_load_codes())
    elif database_url:
        if asyncpg is None:
            logger.warning(
                "Alice skill: asyncpg import failed (%r), fallback mode",
                _ASYNCPG_IMPORT_ERROR,
            )
            if require_linked_account and linking_fail_closed and not degrade_to_guest:
                account_links = UnavailableAccountLinkStore()
            else:
                account_links = InMemoryAccountLinkStore(_load_links(), codes=_load_codes())
            if degrade_to_guest:
                effective_require_linked = False
        else:
            try:
                from vkuswill_bot.services.user_store import UserStore

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
                if require_linked_account and linking_fail_closed and not degrade_to_guest:
                    account_links = UnavailableAccountLinkStore()
                else:
                    account_links = InMemoryAccountLinkStore(_load_links(), codes=_load_codes())
                if degrade_to_guest:
                    effective_require_linked = False
    else:
        if require_linked_account and linking_fail_closed:
            account_links = UnavailableAccountLinkStore()
        else:
            account_links = InMemoryAccountLinkStore(_load_links(), codes=_load_codes())

    fallback_rate_limiter = InMemoryRateLimiter()
    order_rate_limiter = fallback_rate_limiter
    link_code_rate_limiter = fallback_rate_limiter
    idempotency_store = InMemoryIdempotencyStore()
    if redis_url:
        if create_redis_client is None:
            logger.warning(
                "Alice skill: Redis client import failed (%r), fallback to in-memory",
                _REDIS_IMPORT_ERROR,
            )
        else:
            try:
                redis_client = await create_redis_client(
                    redis_url,
                    decode_responses=False,
                    socket_connect_timeout=db_connect_timeout,
                    socket_timeout=max(db_connect_timeout, 5.0),
                )
                idempotency_store = RedisIdempotencyStore(
                    redis_client,
                    key_prefix=idempotency_key_prefix,
                )
                shared_rate_limiter = RedisRateLimiter(
                    redis_client,
                    key_prefix=rate_limit_key_prefix,
                    fallback_limiter=fallback_rate_limiter,
                )
                order_rate_limiter = shared_rate_limiter
                link_code_rate_limiter = shared_rate_limiter
            except Exception:
                logger.exception(
                    "Alice skill: Redis init failed for idempotency/rate-limit, "
                    "fallback to in-memory",
                )

    orchestrator = AliceOrderOrchestrator(
        mcp_client,
        account_links=account_links,
        delivery_adapter=AliceAppDeliveryAdapter(),
        idempotency_store=idempotency_store,
        require_linked_account=effective_require_linked,
        idempotency_ttl_seconds=idempotency_ttl,
        max_utterance_chars=max_utterance_chars,
        max_products_per_order=max_products_per_order,
        order_rate_limiter=order_rate_limiter,
        link_code_rate_limiter=link_code_rate_limiter,
        order_rate_limit=order_rate_limit,
        order_rate_window_seconds=order_rate_window_seconds,
        link_code_rate_limit=link_code_rate_limit,
        link_code_rate_window_seconds=link_code_rate_window_seconds,
    )
    _RUNTIME = _Runtime(
        orchestrator=orchestrator,
        langfuse=LangfuseService(
            enabled=langfuse_enabled,
            public_key=langfuse_public_key,
            secret_key=langfuse_secret_key,
            host=langfuse_host,
            anonymize_messages=langfuse_anonymize_messages,
        ),
    )
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


def _extract_session_id(event: dict[str, Any]) -> str | None:
    session = event.get("session", {})
    if not isinstance(session, dict):
        return None
    raw = session.get("session_id")
    if raw is None:
        return None
    return str(raw)


def _extract_skill_id(event: dict[str, Any]) -> str | None:
    session = event.get("session", {})
    if not isinstance(session, dict):
        return None
    raw = session.get("skill_id")
    if raw is None:
        return None
    return str(raw)


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


def _forbidden_alice_response(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": event.get("version", "1.0"),
        "session": event.get("session", {}),
        "response": {
            "text": "Доступ к навыку ограничен. Попробуйте позже.",
            "end_session": True,
        },
    }


def _is_http_proxy_event(event: dict[str, Any]) -> bool:
    return "httpMethod" in event or "requestContext" in event


def _header_value(event: dict[str, Any], header_name: str) -> str | None:
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return None
    lookup = header_name.strip().lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == lookup and isinstance(value, str):
            return value
    return None


def _is_allowed_http_event(event: dict[str, Any]) -> bool:
    token = os.getenv("ALICE_WEBHOOK_TOKEN", "").strip()
    if not token:
        return True
    header_name = os.getenv("ALICE_WEBHOOK_TOKEN_HEADER", DEFAULT_WEBHOOK_HEADER_NAME).strip()
    if not header_name:
        header_name = DEFAULT_WEBHOOK_HEADER_NAME
    provided = _header_value(event, header_name)
    if provided is None:
        return False
    return hmac.compare_digest(provided.strip(), token)


def _is_allowed_alice_event(event: dict[str, Any]) -> bool:
    expected_skill_id = os.getenv("ALICE_SKILL_ID", "").strip()
    if not expected_skill_id:
        return True

    session = event.get("session")
    if not isinstance(session, dict):
        return False
    skill_id = session.get("skill_id")
    if not isinstance(skill_id, str):
        return False
    return hmac.compare_digest(skill_id.strip(), expected_skill_id)


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
    trace = runtime.langfuse.trace(
        name="alice-order",
        user_id=voice_user_id,
        session_id=_extract_session_id(event),
        input=utterance,
        metadata={
            "channel": "alice",
            "skill_id": _extract_skill_id(event),
            "utterance_len": len(utterance),
        },
        tags=["alice", "voice-order"],
    )
    orchestration_span = trace.span(
        name="alice-orchestrator",
        input={"utterance_len": len(utterance)},
    )

    if not utterance.strip():
        orchestration_span.end(
            output={"ok": False, "reason": "empty_utterance"},
            metadata={"error_code": "empty_utterance"},
        )
        trace.update(
            output="Привет! Скажите, что добавить в корзину.",
            metadata={"result": "empty_utterance"},
        )
        runtime.langfuse.flush()
        return {
            "version": event.get("version", "1.0"),
            "session": event.get("session", {}),
            "response": {
                "text": (
                    "Привет! Скажите, что добавить в корзину. Например: закажи молоко и яйца."
                ),
                "end_session": False,
            },
        }

    try:
        result = await runtime.orchestrator.create_order_from_utterance(
            voice_user_id=voice_user_id,
            utterance=utterance,
        )
    except Exception as exc:
        orchestration_span.end(
            output={"ok": False},
            metadata={"status": "error", "exception": str(exc)},
            level="ERROR",
            status_message="alice_orchestrator_failed",
        )
        trace.update(
            output="internal_error",
            metadata={"status": "error", "exception": str(exc)},
        )
        runtime.langfuse.flush()
        raise

    orchestration_span.end(
        output={
            "ok": result.ok,
            "items_count": result.items_count,
            "total_rub": result.total_rub,
            "cart_link_present": bool(result.cart_link),
            "error_code": result.error_code,
        },
        metadata={
            "delivery_status": result.delivery.status if result.delivery else None,
            "requires_linking": result.requires_linking,
        },
    )
    trace.update(
        output=result.voice_text,
        metadata={
            "ok": result.ok,
            "items_count": result.items_count,
            "total_rub": result.total_rub,
            "cart_link_present": bool(result.cart_link),
            "error_code": result.error_code,
        },
    )
    runtime.langfuse.flush()
    return _to_alice_response(event, result)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Точка входа Yandex Cloud Function."""
    del context
    alice_event = event
    if _is_http_proxy_event(event):
        if not _is_allowed_http_event(event):
            logger.warning("Alice skill: rejected unauthorized HTTP event")
            return _wrap_http_response({"error": "forbidden"}, status_code=403)
        alice_event = _unwrap_http_event(event)
        if not alice_event:
            return _wrap_http_response(
                {"error": "invalid_request_body"},
                status_code=400,
            )
        if not _is_allowed_alice_event(alice_event):
            logger.warning("Alice skill: rejected event with invalid skill_id (HTTP)")
            return _wrap_http_response({"error": "forbidden"}, status_code=403)
    elif not _is_allowed_alice_event(event):
        logger.warning("Alice skill: rejected event with invalid skill_id")
        return _forbidden_alice_response(event)

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
