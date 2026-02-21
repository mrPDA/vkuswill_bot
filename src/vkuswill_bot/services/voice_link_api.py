"""HTTP API для voice account linking (вариант 1: через VM-бота)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from vkuswill_bot.services.gigachat_service import GigaChatService
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

_APP_KEY = "voice_link_api_key"
_APP_STORE = "voice_link_user_store"
_APP_CHAT_SERVICE = "voice_link_gigachat_service"


def register_voice_link_routes(
    app: web.Application,
    *,
    user_store: UserStore | None,
    gigachat_service: GigaChatService | None = None,
    api_key: str,
) -> None:
    """Зарегистрировать маршруты voice-link API."""
    app[_APP_STORE] = user_store
    app[_APP_CHAT_SERVICE] = gigachat_service
    app[_APP_KEY] = api_key
    app.router.add_post("/voice-link/consume", _consume_handler)
    app.router.add_post("/voice-link/resolve", _resolve_handler)
    app.router.add_post("/voice-link/order", _order_handler)


def _is_authorized(request: web.Request) -> bool:
    api_key = str(request.app.get(_APP_KEY, "")).strip()
    if not api_key:
        return False
    provided = request.headers.get("X-Voice-Link-Api-Key", "").strip()
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


async def _consume_handler(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return _json_error(401, "unauthorized", "Invalid API key")

    user_store: UserStore | None = request.app.get(_APP_STORE)
    if user_store is None:
        return _json_error(503, "unavailable", "Voice linking unavailable")

    payload = await _parse_json(request)
    if payload is None:
        return _json_error(400, "invalid_json", "Body must be JSON object")

    provider = str(payload.get("provider", "")).strip().lower()
    voice_user_id = str(payload.get("voice_user_id", "")).strip()
    code = str(payload.get("code", "")).strip()
    if not provider or not voice_user_id or not code:
        return _json_error(400, "invalid_input", "provider, voice_user_id, code required")

    result = await user_store.consume_voice_link_code(
        provider=provider,
        voice_user_id=voice_user_id,
        code=code,
    )
    return web.json_response(result, status=200)


async def _resolve_handler(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return _json_error(401, "unauthorized", "Invalid API key")

    user_store: UserStore | None = request.app.get(_APP_STORE)
    if user_store is None:
        return _json_error(503, "unavailable", "Voice linking unavailable")

    payload = await _parse_json(request)
    if payload is None:
        return _json_error(400, "invalid_json", "Body must be JSON object")

    provider = str(payload.get("provider", "")).strip().lower()
    voice_user_id = str(payload.get("voice_user_id", "")).strip()
    if not provider or not voice_user_id:
        return _json_error(400, "invalid_input", "provider, voice_user_id required")

    user_id = await user_store.resolve_voice_link(
        provider=provider,
        voice_user_id=voice_user_id,
    )
    return web.json_response({"ok": True, "user_id": user_id}, status=200)


def _parse_user_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _snapshot_signature(snapshot: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(snapshot, dict):
        return ("", "")
    link = snapshot.get("link")
    created_at = snapshot.get("created_at")
    return (
        link if isinstance(link, str) else "",
        created_at if isinstance(created_at, str) else "",
    )


def _snapshot_items_count(snapshot: dict[str, Any] | None) -> int:
    if not isinstance(snapshot, dict):
        return 0
    products = snapshot.get("products")
    if isinstance(products, list):
        return len(products)
    return 0


def _snapshot_total(snapshot: dict[str, Any] | None) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    total = snapshot.get("total")
    if isinstance(total, bool):
        return None
    if isinstance(total, int | float):
        return float(total)
    if isinstance(total, str):
        normalized = total.strip().replace(",", ".")
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


async def _order_handler(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return _json_error(401, "unauthorized", "Invalid API key")

    gigachat_service: GigaChatService | None = request.app.get(_APP_CHAT_SERVICE)
    if gigachat_service is None:
        return _json_error(503, "unavailable", "Voice order processing unavailable")

    payload = await _parse_json(request)
    if payload is None:
        return _json_error(400, "invalid_json", "Body must be JSON object")

    user_id = _parse_user_id(payload.get("user_id"))
    utterance = str(payload.get("utterance", "")).strip()
    voice_user_id = str(payload.get("voice_user_id", "")).strip()
    if user_id is None or not utterance:
        return _json_error(400, "invalid_input", "user_id and utterance required")
    if len(utterance) > 512:
        return _json_error(400, "invalid_input", "utterance is too long")

    before_snapshot = await gigachat_service.get_last_cart_snapshot(user_id)
    before_signature = _snapshot_signature(before_snapshot)
    try:
        assistant_text = await gigachat_service.process_message(user_id=user_id, text=utterance)
    except Exception:
        logger.exception(
            "voice-order failed: user_id=%s voice_user_id=%s",
            user_id,
            voice_user_id or "-",
        )
        return _json_error(502, "llm_error", "Voice order processing failed")

    after_snapshot = await gigachat_service.get_last_cart_snapshot(user_id)
    after_signature = _snapshot_signature(after_snapshot)
    cart_link = (
        after_snapshot.get("link")
        if isinstance(after_snapshot, dict) and isinstance(after_snapshot.get("link"), str)
        else ""
    )
    cart_created = bool(cart_link) and after_signature != before_signature

    if not cart_created:
        return web.json_response(
            {
                "ok": False,
                "assistant_text": assistant_text,
                "error": "cart_not_created",
                "cart_link": None,
                "total_rub": None,
                "items_count": 0,
            },
            status=200,
        )

    return web.json_response(
        {
            "ok": True,
            "assistant_text": assistant_text,
            "cart_link": cart_link,
            "total_rub": _snapshot_total(after_snapshot),
            "items_count": _snapshot_items_count(after_snapshot),
        },
        status=200,
    )
