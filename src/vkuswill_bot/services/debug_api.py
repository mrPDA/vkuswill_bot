"""Stage-only debug HTTP API for direct ShoppingAgent runs."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from vkuswill_bot.services.chat_engine import ChatEngineProtocol

logger = logging.getLogger(__name__)

_APP_KEY = "debug_api_key"
_APP_CHAT_ENGINE = "debug_chat_engine"


def register_debug_routes(
    app: web.Application,
    *,
    chat_engine: ChatEngineProtocol | None,
    api_key: str,
) -> None:
    """Register stage-only debug routes."""
    app[_APP_CHAT_ENGINE] = chat_engine
    app[_APP_KEY] = api_key
    app.router.add_post("/debug/run-shopping", _run_shopping_handler)
    app.router.add_post("/debug/reset-history", _reset_history_handler)


def should_enable_debug_api(*, api_key: str, environment: str) -> bool:
    """Expose debug API only outside production and only when key is configured."""
    return bool(str(api_key).strip()) and str(environment).strip().lower() != "production"


def _is_authorized(request: web.Request) -> bool:
    api_key = str(request.app.get(_APP_KEY, "")).strip()
    if not api_key:
        return False
    provided = request.headers.get("X-Debug-Api-Key", "").strip()
    return bool(provided) and provided == api_key


async def _parse_json(request: web.Request) -> dict[str, Any] | None:
    try:
        payload = await request.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"ok": False, "error": code, "message": message},
        status=status,
    )


def _parse_user_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _snapshot_items_count(snapshot: dict[str, Any] | None) -> int:
    if not isinstance(snapshot, dict):
        return 0
    direct = snapshot.get("items_count")
    if isinstance(direct, int) and direct >= 0:
        return direct
    if isinstance(direct, float) and direct.is_integer() and direct >= 0:
        return int(direct)
    price_summary = snapshot.get("price_summary")
    if isinstance(price_summary, dict):
        count = price_summary.get("count")
        if isinstance(count, int) and count >= 0:
            return count
    products = snapshot.get("products")
    return len(products) if isinstance(products, list) else 0


def _snapshot_total(snapshot: dict[str, Any] | None) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    total = snapshot.get("total")
    if isinstance(total, bool):
        return None
    if isinstance(total, (int, float)):
        return float(total)
    if isinstance(total, str):
        normalized = total.strip().replace(",", ".")
        if not normalized:
            return None
        with contextlib.suppress(ValueError):
            return float(normalized)
    return None


async def _read_debug_state(
    chat_engine: ChatEngineProtocol,
    *,
    user_id: int,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    cart_snapshot = await chat_engine.get_last_cart_snapshot(user_id)
    trace_id_getter = getattr(chat_engine, "get_last_trace_id", None)
    diagnostics_getter = getattr(chat_engine, "get_last_turn_diagnostics", None)
    trace_id: str | None = None
    diagnostics: dict[str, Any] | None = None
    if callable(trace_id_getter):
        with contextlib.suppress(Exception):
            resolved = await trace_id_getter(user_id)
            if resolved is not None:
                trace_id = str(resolved).strip() or None
    if callable(diagnostics_getter):
        with contextlib.suppress(Exception):
            resolved = await diagnostics_getter(user_id)
            if isinstance(resolved, dict):
                diagnostics = resolved
    return cart_snapshot, trace_id, diagnostics


async def _run_shopping_handler(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return _json_error(401, "unauthorized", "Invalid API key")

    chat_engine: ChatEngineProtocol | None = request.app.get(_APP_CHAT_ENGINE)
    if chat_engine is None:
        return _json_error(503, "unavailable", "Debug shopping unavailable")

    payload = await _parse_json(request)
    if payload is None:
        return _json_error(400, "invalid_json", "Body must be JSON object")

    user_id = _parse_user_id(payload.get("user_id"))
    text = str(payload.get("text", "")).strip()
    reset_history = _parse_bool(payload.get("reset_history"), default=True)
    if user_id is None or not text:
        return _json_error(400, "invalid_input", "user_id and text required")
    if len(text) > 4000:
        return _json_error(400, "invalid_input", "text is too long")

    if reset_history:
        reset = getattr(chat_engine, "reset_conversation", None)
        if callable(reset):
            with contextlib.suppress(Exception):
                await reset(user_id)

    try:
        response_text = await chat_engine.process_message(user_id=user_id, text=text)
    except Exception as exc:
        logger.exception("debug run-shopping failed: user_id=%s", user_id)
        cart_snapshot, trace_id, diagnostics = await _read_debug_state(chat_engine, user_id=user_id)
        return web.json_response(
            {
                "ok": False,
                "error": "llm_error",
                "message": "Debug shopping failed",
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "trace_id": trace_id,
                "cart_snapshot": cart_snapshot,
                "diagnostics": diagnostics,
            },
            status=502,
            dumps=lambda body: json.dumps(body, ensure_ascii=False),
        )

    cart_snapshot, trace_id, diagnostics = await _read_debug_state(chat_engine, user_id=user_id)

    cart_link = (
        str(cart_snapshot.get("link")).strip()
        if isinstance(cart_snapshot, dict) and cart_snapshot.get("link")
        else None
    )
    return web.json_response(
        {
            "ok": True,
            "user_id": user_id,
            "response": response_text,
            "trace_id": trace_id,
            "history_reset": reset_history,
            "cart_link": cart_link,
            "items_count": _snapshot_items_count(cart_snapshot),
            "total_rub": _snapshot_total(cart_snapshot),
            "cart_snapshot": cart_snapshot,
            "diagnostics": diagnostics,
        },
        status=200,
        dumps=lambda body: json.dumps(body, ensure_ascii=False),
    )


async def _reset_history_handler(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return _json_error(401, "unauthorized", "Invalid API key")

    chat_engine: ChatEngineProtocol | None = request.app.get(_APP_CHAT_ENGINE)
    if chat_engine is None:
        return _json_error(503, "unavailable", "Debug shopping unavailable")

    payload = await _parse_json(request)
    if payload is None:
        return _json_error(400, "invalid_json", "Body must be JSON object")

    user_id = _parse_user_id(payload.get("user_id"))
    if user_id is None:
        return _json_error(400, "invalid_input", "user_id required")

    reset = getattr(chat_engine, "reset_conversation", None)
    if callable(reset):
        with contextlib.suppress(Exception):
            await reset(user_id)
    return web.json_response({"ok": True, "user_id": user_id}, status=200)
