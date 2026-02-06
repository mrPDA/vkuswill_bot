"""Точка входа — запуск Telegram-бота."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from vkuswill_bot.bot.handlers import router
from vkuswill_bot.config import config
from vkuswill_bot.services.gigachat_service import GigaChatService
from vkuswill_bot.services.mcp_client import VkusvillMCPClient

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


async def main() -> None:
    """Инициализация сервисов и запуск бота."""
    # Telegram-бот
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    # MCP-клиент для ВкусВилл
    mcp_client = VkusvillMCPClient(config.mcp_server_url)

    # GigaChat-сервис
    gigachat_service = GigaChatService(
        credentials=config.gigachat_credentials,
        model=config.gigachat_model,
        scope=config.gigachat_scope,
        mcp_client=mcp_client,
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

    logger.info("Бот запускается...")
    try:
        await dp.start_polling(bot)
    finally:
        await gigachat_service.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
