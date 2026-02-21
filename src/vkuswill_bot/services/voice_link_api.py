"""HTTP API для voice account linking (вариант 1: через VM-бота)."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from vkuswill_bot.services.chat_engine import ChatEngineProtocol
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

_APP_KEY = "voice_link_api_key"
_APP_STORE = "voice_link_user_store"
# Сохраняем историческое имя ключа для обратной совместимости.
_APP_CHAT_ENGINE = "voice_link_gigachat_service"
_APP_ORDER_JOBS = "voice_link_order_jobs"
_APP_ORDER_LATEST = "voice_link_order_latest"
_APP_ORDER_TASKS = "voice_link_order_tasks"
_APP_ORDER_JOB_TTL_SECONDS = "voice_link_order_job_ttl_seconds"
_APP_ORDER_MAX_JOBS = "voice_link_order_max_jobs"

_DEFAULT_ORDER_JOB_TTL_SECONDS = 30 * 60
_DEFAULT_ORDER_MAX_JOBS = 5000


@dataclass
class _OrderJob:
    job_id: str
    user_id: int
    voice_user_id: str
    utterance: str
    status: str
    created_at: float
    updated_at: float
    expires_at: float
    assistant_text: str = ""
    cart_link: str | None = None
    total_rub: float | None = None
    items_count: int = 0
    error: str | None = None


def register_voice_link_routes(
    app: web.Application,
    *,
    user_store: UserStore | None,
    chat_engine: ChatEngineProtocol | None = None,
    api_key: str,
) -> None:
    """Зарегистрировать маршруты voice-link API."""
    app[_APP_STORE] = user_store
    app[_APP_CHAT_ENGINE] = chat_engine
    app[_APP_KEY] = api_key
    _ensure_job_storage(app)
    app.router.add_post("/voice-link/consume", _consume_handler)
    app.router.add_post("/voice-link/resolve", _resolve_handler)
    app.router.add_post("/voice-link/order", _order_handler)
    app.router.add_post("/voice-link/order/start", _order_start_handler)
    app.router.add_post("/voice-link/order/status", _order_status_handler)


def _ensure_job_storage(app: MutableMapping[str, Any]) -> None:
    app.setdefault(_APP_ORDER_JOBS, {})
    app.setdefault(_APP_ORDER_LATEST, {})
    app.setdefault(_APP_ORDER_TASKS, set())
    app.setdefault(_APP_ORDER_JOB_TTL_SECONDS, _DEFAULT_ORDER_JOB_TTL_SECONDS)
    app.setdefault(_APP_ORDER_MAX_JOBS, _DEFAULT_ORDER_MAX_JOBS)


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


def _parse_user_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _parse_job_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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


def _order_owner_key(user_id: int, voice_user_id: str) -> str:
    return f"{user_id}:{voice_user_id.strip()}"


def _now() -> float:
    return time.time()


def _prune_order_jobs(app: MutableMapping[str, Any]) -> None:
    _ensure_job_storage(app)
    jobs: dict[str, _OrderJob] = app[_APP_ORDER_JOBS]
    latest: dict[str, str] = app[_APP_ORDER_LATEST]
    max_jobs = int(app.get(_APP_ORDER_MAX_JOBS, _DEFAULT_ORDER_MAX_JOBS))
    now = _now()

    stale_job_ids = [job_id for job_id, job in jobs.items() if job.expires_at <= now]
    for job_id in stale_job_ids:
        jobs.pop(job_id, None)

    stale_owner_keys = [owner for owner, job_id in latest.items() if job_id not in jobs]
    for owner in stale_owner_keys:
        latest.pop(owner, None)

    if len(jobs) <= max_jobs:
        return

    overflow = len(jobs) - max_jobs
    oldest = sorted(jobs.values(), key=lambda item: item.created_at)[:overflow]
    for item in oldest:
        jobs.pop(item.job_id, None)

    stale_owner_keys = [owner for owner, job_id in latest.items() if job_id not in jobs]
    for owner in stale_owner_keys:
        latest.pop(owner, None)


async def _run_order_job(app: MutableMapping[str, Any], job_id: str) -> None:
    _ensure_job_storage(app)
    jobs: dict[str, _OrderJob] = app[_APP_ORDER_JOBS]
    ttl_seconds = int(app.get(_APP_ORDER_JOB_TTL_SECONDS, _DEFAULT_ORDER_JOB_TTL_SECONDS))

    job = jobs.get(job_id)
    if job is None:
        return

    chat_engine: ChatEngineProtocol | None = app.get(_APP_CHAT_ENGINE)
    if chat_engine is None:
        job.status = "failed"
        job.error = "unavailable"
        job.assistant_text = "Сервис сборки корзины временно недоступен."
        job.updated_at = _now()
        job.expires_at = job.updated_at + ttl_seconds
        return

    try:
        order_result = await _execute_order_request(
            chat_engine=chat_engine,
            user_id=job.user_id,
            utterance=job.utterance,
            voice_user_id=job.voice_user_id,
        )
        if bool(order_result.get("ok")):
            job.status = "done"
            job.cart_link = (
                str(order_result.get("cart_link")) if order_result.get("cart_link") else None
            )
            job.total_rub = _snapshot_total({"total": order_result.get("total_rub")})
            job.items_count = int(order_result.get("items_count") or 0)
            job.assistant_text = str(order_result.get("assistant_text", "")).strip()
            job.error = None
        else:
            job.status = "failed"
            job.error = str(order_result.get("error", "cart_not_created"))
            job.assistant_text = str(order_result.get("assistant_text", "")).strip()
    except Exception:
        logger.exception(
            "voice-order job failed: user_id=%s voice_user_id=%s job_id=%s",
            job.user_id,
            job.voice_user_id or "-",
            job_id,
        )
        job.status = "failed"
        job.error = "llm_error"
        if not job.assistant_text:
            job.assistant_text = "Не удалось собрать корзину. Повторите запрос позже."

    job.updated_at = _now()
    job.expires_at = job.updated_at + ttl_seconds


def _order_job_payload(job: _OrderJob) -> dict[str, Any]:
    if job.status == "processing":
        return {"ok": True, "status": "processing", "job_id": job.job_id}
    if job.status == "done":
        return {
            "ok": True,
            "status": "done",
            "job_id": job.job_id,
            "assistant_text": job.assistant_text,
            "cart_link": job.cart_link,
            "total_rub": job.total_rub,
            "items_count": job.items_count,
        }
    return {
        "ok": False,
        "status": "failed",
        "job_id": job.job_id,
        "assistant_text": job.assistant_text,
        "error": job.error or "unexpected_error",
    }


def _resolve_order_job(
    app: MutableMapping[str, Any],
    *,
    user_id: int | None,
    voice_user_id: str,
    job_id: str | None,
) -> _OrderJob | None:
    _ensure_job_storage(app)
    jobs: dict[str, _OrderJob] = app[_APP_ORDER_JOBS]
    latest: dict[str, str] = app[_APP_ORDER_LATEST]

    resolved_id = job_id
    if resolved_id is None and user_id is not None:
        owner_key = _order_owner_key(user_id, voice_user_id)
        resolved_id = latest.get(owner_key)
    if resolved_id is None:
        return None
    return jobs.get(resolved_id)


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


async def _execute_order_request(
    *,
    chat_engine: ChatEngineProtocol,
    user_id: int,
    utterance: str,
    voice_user_id: str,
) -> dict[str, Any]:
    before_snapshot = await chat_engine.get_last_cart_snapshot(user_id)
    before_signature = _snapshot_signature(before_snapshot)
    try:
        assistant_text = await chat_engine.process_message(user_id=user_id, text=utterance)
    except Exception:
        logger.exception(
            "voice-order failed: user_id=%s voice_user_id=%s",
            user_id,
            voice_user_id or "-",
        )
        return {
            "ok": False,
            "assistant_text": "",
            "error": "llm_error",
            "cart_link": None,
            "total_rub": None,
            "items_count": 0,
        }

    after_snapshot = await chat_engine.get_last_cart_snapshot(user_id)
    after_signature = _snapshot_signature(after_snapshot)
    cart_link = (
        after_snapshot.get("link")
        if isinstance(after_snapshot, dict) and isinstance(after_snapshot.get("link"), str)
        else ""
    )
    cart_created = bool(cart_link) and after_signature != before_signature

    if not cart_created:
        return {
            "ok": False,
            "assistant_text": assistant_text,
            "error": "cart_not_created",
            "cart_link": None,
            "total_rub": None,
            "items_count": 0,
        }

    return {
        "ok": True,
        "assistant_text": assistant_text,
        "cart_link": cart_link,
        "total_rub": _snapshot_total(after_snapshot),
        "items_count": _snapshot_items_count(after_snapshot),
    }


async def _order_handler(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return _json_error(401, "unauthorized", "Invalid API key")

    chat_engine: ChatEngineProtocol | None = request.app.get(_APP_CHAT_ENGINE)
    if chat_engine is None:
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

    result = await _execute_order_request(
        chat_engine=chat_engine,
        user_id=user_id,
        utterance=utterance,
        voice_user_id=voice_user_id,
    )
    if result.get("error") == "llm_error":
        return _json_error(502, "llm_error", "Voice order processing failed")
    return web.json_response(result, status=200)


async def _order_start_handler(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return _json_error(401, "unauthorized", "Invalid API key")

    chat_engine: ChatEngineProtocol | None = request.app.get(_APP_CHAT_ENGINE)
    if chat_engine is None:
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

    del chat_engine  # actual service is resolved in background task
    _ensure_job_storage(request.app)
    _prune_order_jobs(request.app)
    jobs: dict[str, _OrderJob] = request.app[_APP_ORDER_JOBS]
    latest: dict[str, str] = request.app[_APP_ORDER_LATEST]
    tasks: set[asyncio.Task[None]] = request.app[_APP_ORDER_TASKS]
    ttl_seconds = int(request.app.get(_APP_ORDER_JOB_TTL_SECONDS, _DEFAULT_ORDER_JOB_TTL_SECONDS))

    owner_key = _order_owner_key(user_id, voice_user_id)
    existing_id = latest.get(owner_key)
    if existing_id:
        existing_job = jobs.get(existing_id)
        if (
            existing_job is not None
            and existing_job.status == "processing"
            and existing_job.utterance.strip().lower() == utterance.strip().lower()
        ):
            return web.json_response(
                {
                    "ok": True,
                    "status": "processing",
                    "job_id": existing_job.job_id,
                    "eta_seconds": 30,
                },
                status=200,
            )

    now = _now()
    job_id = uuid.uuid4().hex
    job = _OrderJob(
        job_id=job_id,
        user_id=user_id,
        voice_user_id=voice_user_id,
        utterance=utterance,
        status="processing",
        created_at=now,
        updated_at=now,
        expires_at=now + ttl_seconds,
    )
    jobs[job_id] = job
    latest[owner_key] = job_id

    task: asyncio.Task[None] = asyncio.create_task(_run_order_job(request.app, job_id))
    tasks.add(task)

    def _cleanup_task(done_task: asyncio.Task[None]) -> None:
        tasks.discard(done_task)

    task.add_done_callback(_cleanup_task)

    return web.json_response(
        {
            "ok": True,
            "status": "processing",
            "job_id": job_id,
            "eta_seconds": 30,
        },
        status=200,
    )


async def _order_status_handler(request: web.Request) -> web.Response:
    if not _is_authorized(request):
        return _json_error(401, "unauthorized", "Invalid API key")

    payload = await _parse_json(request)
    if payload is None:
        return _json_error(400, "invalid_json", "Body must be JSON object")

    user_id = _parse_user_id(payload.get("user_id"))
    voice_user_id = str(payload.get("voice_user_id", "")).strip()
    job_id = _parse_job_id(payload.get("job_id"))

    if user_id is None and not voice_user_id and not job_id:
        return _json_error(
            400,
            "invalid_input",
            "user_id or voice_user_id or job_id required",
        )

    _ensure_job_storage(request.app)
    _prune_order_jobs(request.app)
    job = _resolve_order_job(
        request.app,
        user_id=user_id,
        voice_user_id=voice_user_id,
        job_id=job_id,
    )
    if job is None:
        return web.json_response({"ok": True, "status": "not_found"}, status=200)

    if user_id is not None and job.user_id != user_id:
        return web.json_response({"ok": True, "status": "not_found"}, status=200)
    if voice_user_id and job.voice_user_id != voice_user_id:
        return web.json_response({"ok": True, "status": "not_found"}, status=200)

    return web.json_response(_order_job_payload(job), status=200)
