"""Точка входа — запуск Telegram-бота."""

import asyncio
import logging
import signal
from concurrent.futures import ThreadPoolExecutor

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

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

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = "bot.log"

logging.basicConfig(
    level=logging.DEBUG if config.debug else logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# Увеличенный пул потоков для синхронного SDK GigaChat (asyncio.to_thread)
THREAD_POOL_WORKERS = 50


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
    cart_snapshot_store = None
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
            price_cache = PriceCache()
            dialog_manager = DialogManager(
                max_history=config.max_history_messages,
            )
    else:
        price_cache = PriceCache()
        dialog_manager = DialogManager(
            max_history=config.max_history_messages,
        )

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

    # Graceful shutdown: обработка SIGTERM / SIGINT
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: int, _frame: object) -> None:
        sig_name = signal.Signals(sig).name
        logger.info("Получен сигнал %s, останавливаю бота...", sig_name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    logger.info("Бот запускается...")
    try:
        # Запускаем polling в фоне и ждём сигнала завершения
        polling_task = asyncio.create_task(dp.start_polling(bot))
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            {polling_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Если получен сигнал — останавливаем polling
        if shutdown_task in done:
            logger.info("Graceful shutdown: останавливаю polling...")
            await dp.stop_polling()
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
    finally:
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


if __name__ == "__main__":
    asyncio.run(main())
