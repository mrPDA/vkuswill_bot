"""HTTP API для voice account linking (вариант 1: через VM-бота)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

_APP_KEY = "voice_link_api_key"
_APP_STORE = "voice_link_user_store"


def register_voice_link_routes(
    app: web.Application,
    *,
    user_store: UserStore | None,
    api_key: str,
) -> None:
    """Зарегистрировать маршруты voice-link API."""
    app[_APP_STORE] = user_store
    app[_APP_KEY] = api_key
    app.router.add_post("/voice-link/consume", _consume_handler)
    app.router.add_post("/voice-link/resolve", _resolve_handler)


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
