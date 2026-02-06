"""Точка входа — запуск Telegram-бота."""

import asyncio
import logging
import signal
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from vkuswill_bot.bot.handlers import router
from vkuswill_bot.bot.middlewares import ThrottlingMiddleware
from vkuswill_bot.config import config
from vkuswill_bot.services.gigachat_service import GigaChatService
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.preferences_store import PreferencesStore

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
    dp.include_router(router)

    # Rate-limiting: 5 сообщений / 60 секунд на пользователя
    dp.message.middleware(ThrottlingMiddleware(rate_limit=5, period=60.0))

    # MCP-клиент для ВкусВилл
    mcp_client = VkusvillMCPClient(config.mcp_server_url)

    # Хранилище предпочтений (SQLite)
    prefs_store = PreferencesStore(config.database_path)

    # GigaChat-сервис
    gigachat_service = GigaChatService(
        credentials=config.gigachat_credentials,
        model=config.gigachat_model,
        scope=config.gigachat_scope,
        mcp_client=mcp_client,
        preferences_store=prefs_store,
        max_tool_calls=config.max_tool_calls,
        max_history=config.max_history_messages,
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
        await prefs_store.close()
        await mcp_client.close()
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
