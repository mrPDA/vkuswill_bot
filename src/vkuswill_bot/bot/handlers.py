"""Обработчики команд и сообщений Telegram-бота."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from vkuswill_bot.services.gigachat_service import GigaChatService

logger = logging.getLogger(__name__)

# Максимальная длина одного сообщения в Telegram
MAX_TELEGRAM_MESSAGE_LENGTH = 4096

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start."""
    await message.answer(
        "<b>Привет! Я бот-помощник ВкусВилл.</b>\n\n"
        "Помогу подобрать продукты и собрать корзину. "
        "Просто напиши, что хочешь купить!\n\n"
        "Например:\n"
        "- <i>Собери корзину для завтрака на двоих</i>\n"
        "- <i>Хочу купить молоко, хлеб и сыр</i>\n"
        "- <i>Подбери продукты для ужина, бюджет 1000 руб</i>\n\n"
        "<b>Команды:</b>\n"
        "/reset — начать новый диалог\n"
        "/help — помощь"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Обработчик команды /help."""
    await message.answer(
        "<b>Как пользоваться ботом:</b>\n\n"
        "1. Напиши, какие продукты тебе нужны\n"
        "2. Я подберу варианты и предложу 3 корзины:\n"
        "   <b>Выгодно</b> — лучшие цены\n"
        "   <b>Любимое</b> — высший рейтинг\n"
        "   <b>Лайт</b> — минимум калорий\n"
        "3. Перейди по ссылке на сайт ВкусВилл для оформления заказа\n\n"
        "/reset — сбросить историю диалога"
    )


@router.message(Command("reset"))
async def cmd_reset(
    message: Message,
    gigachat_service: GigaChatService,
) -> None:
    """Обработчик команды /reset — сброс диалога."""
    if message.from_user:
        gigachat_service.reset_conversation(message.from_user.id)
    await message.answer("Диалог сброшен. Напиши, что хочешь купить!")


@router.message(F.text)
async def handle_text(
    message: Message,
    gigachat_service: GigaChatService,
) -> None:
    """Обработчик текстовых сообщений — основная логика бота."""
    if not message.from_user or not message.text:
        return

    user_id = message.from_user.id

    # Показываем индикатор набора текста во время обработки
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        _send_typing_periodically(message, stop_typing)
    )

    try:
        response = await gigachat_service.process_message(user_id, message.text)
    except Exception as e:
        logger.error(
            "Ошибка обработки сообщения пользователя %d: %s",
            user_id,
            e,
            exc_info=True,
        )
        response = (
            "Произошла ошибка при обработке запроса. "
            "Попробуйте позже или начните новый диалог: /reset"
        )
    finally:
        stop_typing.set()
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    # Разбиваем длинные сообщения по лимиту Telegram
    chunks = _split_message(response, MAX_TELEGRAM_MESSAGE_LENGTH)
    for chunk in chunks:
        await message.answer(chunk)


async def _send_typing_periodically(
    message: Message,
    stop_event: asyncio.Event,
) -> None:
    """Периодически отправляет индикатор 'печатает...' в чат."""
    while not stop_event.is_set():
        try:
            await message.bot.send_chat_action(
                message.chat.id, ChatAction.TYPING
            )
        except Exception as e:
            logger.debug("Ошибка отправки typing indicator: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass


def _split_message(text: str, max_length: int) -> list[str]:
    """Разбить длинное сообщение на части для Telegram."""
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Ищем подходящее место для разрыва
        split_pos = text.rfind("\n\n", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind(" ", 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()

    return chunks
