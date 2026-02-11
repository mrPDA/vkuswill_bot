"""Точка входа — запуск Telegram-бота.

Поддерживает два режима работы:
- **Polling** (по умолчанию, для разработки): ``USE_WEBHOOK=false``
- **Webhook** (для production): ``USE_WEBHOOK=true``

В webhook-режиме поднимается aiohttp-сервер с эндпоинтами:
- ``/webhook`` — приём Telegram Update-ов
- ``/health``  — health check (Redis, PostgreSQL, MCP)
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from vkuswill_bot.bot.handlers import admin_router, router
from vkuswill_bot.bot.middlewares import ThrottlingMiddleware, UserMiddleware
from vkuswill_bot.config import config
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.dialog_manager import DialogManager
from vkuswill_bot.services.gigachat_service import GigaChatService
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.price_cache import PriceCache, TwoLevelPriceCache
from vkuswill_bot.services.recipe_store import RecipeStore
from vkuswill_bot.services.redis_client import close_redis_client, create_redis_client
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.tool_executor import ToolExecutor
from vkuswill_bot.services.user_store import UserStore

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Логирование: JSON для production, текст для разработки
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = "bot.log"


class _JSONFormatter(logging.Formatter):
    """Структурированные JSON-логи для Yandex Cloud Logging / ELK."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging() -> None:
    """Настроить логирование в зависимости от режима."""
    level = logging.DEBUG if config.debug else logging.INFO
    handlers: list[logging.Handler] = []

    if config.use_webhook and not config.debug:
        # Production: JSON в stdout (для Cloud Logging / Docker)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(_JSONFormatter())
        handlers.append(stream_handler)
    else:
        # Разработка: человекочитаемый формат
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handlers.append(stream_handler)
        # Файл ТОЛЬКО в debug-режиме (в production K8s логи собираются из stdout)
        if config.debug:
            handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))

    # S3 логирование: долгосрочное хранение в Yandex Object Storage
    if config.s3_log_enabled and config.s3_log_bucket:
        try:
            from vkuswill_bot.services.s3_log_handler import create_s3_log_handler

            s3_handler = create_s3_log_handler(
                bucket=config.s3_log_bucket,
                access_key=config.s3_log_access_key,
                secret_key=config.s3_log_secret_key,
                prefix=config.s3_log_prefix,
                endpoint_url=config.s3_log_endpoint,
                region_name=config.s3_log_region,
                flush_interval=config.s3_log_flush_interval,
                flush_size=config.s3_log_flush_size,
                level=logging.INFO,
            )
            handlers.append(s3_handler)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[WARNING] S3 логирование не запущено: {exc}",
                file=sys.stderr,
            )

    logging.basicConfig(level=level, handlers=handlers, force=True)


_setup_logging()
logger = logging.getLogger(__name__)

# Увеличенный пул потоков для синхронного SDK GigaChat (asyncio.to_thread)
THREAD_POOL_WORKERS = 50

# Путь для приёма webhook-обновлений от Telegram
WEBHOOK_PATH = "/webhook"


# ---------------------------------------------------------------------------
# Health check endpoint
# ---------------------------------------------------------------------------

async def _health_handler(request: web.Request) -> web.Response:
    """Проверка работоспособности бота и зависимостей.

    Возвращает:
        200 — все зависимости доступны (``status: ok``)
        503 — хотя бы одна зависимость недоступна (``status: degraded``)
    """
    checks: dict = {"status": "ok", "redis": False, "postgres": False, "mcp": False}

    # Redis
    redis_client = request.app.get("redis_client")
    if redis_client is not None:
        try:
            await redis_client.ping()
            checks["redis"] = True
        except Exception:
            checks["status"] = "degraded"
    else:
        # Redis не сконфигурирован — не считаем деградацией
        checks["redis"] = None

    # PostgreSQL
    pg_pool = request.app.get("pg_pool")
    if pg_pool is not None:
        try:
            async with pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["postgres"] = True
        except Exception:
            checks["status"] = "degraded"
    else:
        checks["postgres"] = None

    # MCP
    mcp_client_ref = request.app.get("mcp_client")
    if mcp_client_ref is not None:
        try:
            await mcp_client_ref.get_tools()
            checks["mcp"] = True
        except Exception:
            checks["status"] = "degraded"

    status_code = 200 if checks["status"] == "ok" else 503
    return web.json_response(checks, status=status_code)


def _flush_s3_handlers() -> None:
    """Сбросить и закрыть все S3LogHandler-ы (вызывается при shutdown)."""
    from vkuswill_bot.services.s3_log_handler import S3LogHandler

    root = logging.getLogger()
    for handler in root.handlers[:]:
        if isinstance(handler, S3LogHandler):
            handler.close()
            root.removeHandler(handler)


async def main() -> None:
    """Инициализация сервисов и запуск бота."""
    # Увеличить пул потоков для asyncio.to_thread
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=THREAD_POOL_WORKERS))

    # Telegram-бот
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(router)

    # ------------------------------------------------------------------
    # PostgreSQL: пул соединений + UserStore
    # ------------------------------------------------------------------
    pg_pool: asyncpg.Pool | None = None
    user_store: UserStore | None = None

    if config.database_url:
        try:
            pg_pool = await asyncpg.create_pool(
                dsn=config.database_url,
                min_size=config.db_pool_min,
                max_size=config.db_pool_max,
            )
            user_store = UserStore(pg_pool)
            await user_store.ensure_schema()

            # Установить начальных админов из .env
            if config.admin_user_ids:
                await user_store.ensure_admins(config.admin_user_ids)

            # Мидлварь: UserMiddleware → ThrottlingMiddleware (порядок важен!)
            dp.message.middleware(UserMiddleware(user_store))
            logger.info(
                "PostgreSQL подключён, UserStore готов (pool %d-%d)",
                config.db_pool_min,
                config.db_pool_max,
            )
        except Exception as e:
            logger.warning(
                "PostgreSQL недоступен (%s), UserMiddleware отключён", e,
            )
            pg_pool = None
            user_store = None
    else:
        logger.info("DATABASE_URL не задан — UserStore отключён")

    # Rate-limiting: 5 сообщений / 60 секунд на пользователя
    # (ThrottlingMiddleware идёт ПОСЛЕ UserMiddleware)
    dp.message.middleware(ThrottlingMiddleware(rate_limit=5, period=60.0))

    # MCP-клиент для ВкусВилл
    mcp_client = VkusvillMCPClient(config.mcp_server_url)

    # Хранилище предпочтений (SQLite, отдельная БД)
    prefs_store = PreferencesStore(config.database_path)

    # Кеш рецептов (SQLite, отдельная БД — исключает конфликты блокировок)
    recipe_store = RecipeStore(config.recipe_database_path)

    # Менеджер диалогов, кэш цен, снимок корзины: Redis или in-memory
    redis_client = None
    if config.storage_backend == "redis" and config.redis_url:
        try:
            from vkuswill_bot.services.cart_snapshot_store import (
                CartSnapshotStore,
            )
            from vkuswill_bot.services.redis_dialog_manager import (
                RedisDialogManager,
            )

            redis_client = await create_redis_client(config.redis_url)
            dialog_manager = RedisDialogManager(
                redis=redis_client,
                max_history=config.max_history_messages,
            )
            # Двухуровневый кэш цен: L1 (in-memory) + L2 (Redis)
            price_cache = TwoLevelPriceCache(redis=redis_client)
            # Снимок корзины в Redis (24h TTL)
            cart_snapshot_store = CartSnapshotStore(redis=redis_client)
            logger.info(
                "Redis-бэкенд: диалоги, кэш цен (L1+L2), снимки корзины",
            )
        except Exception as e:
            logger.warning(
                "Redis недоступен (%s), fallback на in-memory", e,
            )
            from vkuswill_bot.services.cart_snapshot_store import (
                InMemoryCartSnapshotStore,
            )
            price_cache = PriceCache()
            dialog_manager = DialogManager(
                max_history=config.max_history_messages,
            )
            cart_snapshot_store = InMemoryCartSnapshotStore()
    else:
        from vkuswill_bot.services.cart_snapshot_store import (
            InMemoryCartSnapshotStore,
        )
        price_cache = PriceCache()
        dialog_manager = DialogManager(
            max_history=config.max_history_messages,
        )
        cart_snapshot_store = InMemoryCartSnapshotStore()

    # Процессоры: поиск и корзина (получают PriceCache через DI)
    search_processor = SearchProcessor(price_cache)
    cart_processor = CartProcessor(price_cache)

    # Исполнитель инструментов (маршрутизация MCP/локальных вызовов)
    tool_executor = ToolExecutor(
        mcp_client=mcp_client,
        search_processor=search_processor,
        cart_processor=cart_processor,
        preferences_store=prefs_store,
        cart_snapshot_store=cart_snapshot_store,
    )

    # GigaChat-сервис — все зависимости инжектируются явно
    gigachat_service = GigaChatService(
        credentials=config.gigachat_credentials,
        model=config.gigachat_model,
        scope=config.gigachat_scope,
        mcp_client=mcp_client,
        preferences_store=prefs_store,
        recipe_store=recipe_store,
        max_tool_calls=config.max_tool_calls,
        max_history=config.max_history_messages,
        dialog_manager=dialog_manager,
        tool_executor=tool_executor,
        gigachat_max_concurrent=config.gigachat_max_concurrent,
    )

    # Предзагрузка MCP-инструментов
    try:
        tools = await mcp_client.get_tools()
        logger.info("MCP инструменты загружены: %s", [t["name"] for t in tools])
    except Exception as e:
        logger.warning("Не удалось загрузить MCP инструменты при старте: %s", e)
        logger.warning("Инструменты будут загружены при первом запросе")

    # Передаём сервисы в хендлеры через DI
    dp["gigachat_service"] = gigachat_service
    if user_store is not None:
        dp["user_store"] = user_store

    # ------------------------------------------------------------------
    # Функция очистки ресурсов
    # ------------------------------------------------------------------

    async def _cleanup() -> None:
        logger.info("Закрытие ресурсов...")
        await gigachat_service.close()
        await recipe_store.close()
        await prefs_store.close()
        await close_redis_client(redis_client)
        await mcp_client.close()
        if pg_pool is not None:
            await pg_pool.close()
            logger.info("PostgreSQL pool закрыт")
        await bot.session.close()
        logger.info("Бот остановлен.")

        # Сбросить оставшиеся логи в S3 перед завершением
        _flush_s3_handlers()

    # ------------------------------------------------------------------
    # Запуск: webhook (production) или polling (разработка)
    # ------------------------------------------------------------------

    if config.use_webhook:
        await _run_webhook(
            bot, dp,
            redis_client=redis_client,
            pg_pool=pg_pool,
            mcp_client=mcp_client,
            cleanup=_cleanup,
        )
    else:
        await _run_polling(bot, dp, cleanup=_cleanup)


# ---------------------------------------------------------------------------
# Режим Polling (разработка)
# ---------------------------------------------------------------------------

async def _run_polling(
    bot: Bot,
    dp: Dispatcher,
    *,
    cleanup: object,
) -> None:
    """Запуск бота в режиме long polling."""
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: int, _frame: object) -> None:
        sig_name = signal.Signals(sig).name
        logger.info("Получен сигнал %s, останавливаю бота...", sig_name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    logger.info("Бот запускается (polling)...")
    try:
        polling_task = asyncio.create_task(dp.start_polling(bot))
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, _pending = await asyncio.wait(
            {polling_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if shutdown_task in done:
            logger.info("Graceful shutdown: останавливаю polling...")
            await dp.stop_polling()
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
    finally:
        await cleanup()  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Режим Webhook (production)
# ---------------------------------------------------------------------------

async def _run_webhook(
    bot: Bot,
    dp: Dispatcher,
    *,
    redis_client: object,
    pg_pool: asyncpg.Pool | None,
    mcp_client: VkusvillMCPClient,
    cleanup: object,
) -> None:
    """Запуск бота в режиме webhook через aiohttp."""
    webhook_url = f"https://{config.webhook_host}{WEBHOOK_PATH}"
    logger.info("Бот запускается (webhook: %s, порт %d)...", webhook_url, config.webhook_port)

    # aiohttp-приложение
    app = web.Application()

    # Сохраняем ссылки на зависимости для health check
    app["redis_client"] = redis_client
    app["pg_pool"] = pg_pool
    app["mcp_client"] = mcp_client

    # Health check
    app.router.add_get("/health", _health_handler)

    # Webhook handler от aiogram
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Установить webhook в Telegram
    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
    )
    logger.info("Telegram webhook установлен: %s", webhook_url)

    # Запуск HTTP-сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.webhook_port)
    await site.start()

    # Ожидание сигнала завершения
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: int, _frame: object) -> None:
        sig_name = signal.Signals(sig).name
        logger.info("Получен сигнал %s, останавливаю бота...", sig_name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    logger.info(
        "Webhook-сервер запущен на 0.0.0.0:%d. Ожидание...",
        config.webhook_port,
    )
    try:
        await shutdown_event.wait()
    finally:
        logger.info("Graceful shutdown: удаляю webhook, останавливаю сервер...")
        await bot.delete_webhook()
        await runner.cleanup()
        await cleanup()  # type: ignore[operator]


if __name__ == "__main__":
    asyncio.run(main())
