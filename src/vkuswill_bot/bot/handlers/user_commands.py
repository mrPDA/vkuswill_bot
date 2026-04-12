"""Обработчики пользовательских команд."""

from __future__ import annotations

import asyncio
import logging
import contextlib
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

from vkuswill_bot.bot.telegram_delivery import (
    build_telegram_delivery_preview,
    _send_typing_periodically, # Imported from telegram_delivery.py
)
from vkuswill_bot.services.chat_engine import ChatEngineProtocol
from vkuswill_bot.config import config as app_config

if TYPE_CHECKING:
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    user_store: UserStore | None = None,
    db_user: dict | None = None,
) -> None:
    print(f"DEBUG: Entering cmd_start with user_id={message.from_user.id}")
    start_param: str | None = None
    if message.text and message.text.startswith("/start "):
        start_param = message.text.split(maxsplit=1)[1].strip()
    source = "organic"
    referrer_id: int | None = None
    if start_param:
        if start_param.startswith("ref_"):
            ref_value = start_param[4:]
            try:
                referrer_id = int(ref_value)
            except ValueError:
                if user_store is not None:
                    with contextlib.suppress(Exception):
                        referrer_id = await user_store.find_user_by_referral_code(
                            ref_value,
                        )
            if referrer_id is not None:
                source = "referral"
        elif start_param in ("habr", "vc", "telegram"):
            source = start_param

    is_new_user = (db_user or {}).get("message_count", 0) <= 1
    metadata: dict = {"source": source, "is_new_user": is_new_user}
    if referrer_id is not None:
        metadata["referrer_id"] = referrer_id
    if user_store is not None and message.from_user is not None:
        with contextlib.suppress(Exception):
            await user_store.log_event(
                message.from_user.id,
                "bot_start",
                metadata,
            )

    if (
        referrer_id is not None
        and is_new_user
        and user_store is not None
        and message.from_user is not None
    ):
        await _process_referral_start(
            message,
            user_store,
            message.from_user.id,
            referrer_id,
        )

    is_consent_needed = (
        db_user is not None
        and db_user.get("consent_given_at") is None
        and (db_user.get("message_count", 0) <= 1)
    )

    if is_consent_needed:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\U0001f680 Понятно, начать!",
                        callback_data="consent_accept",
                    )
                ],
            ],
        )
        await message.answer(
            "<b>Привет! Я бот-помощник ВкусВилл.</b>\n\n"
            "Помогу подобрать продукты и собрать корзину. "
            "Просто напиши, что хочешь купить!\n\n"
            "Например:\n"
            "- <i>Собери корзину для завтрака на двоих</i>\n"
            "- <i>Хочу купить молоко, хлеб и сыр</i>\n\n"
            f"{await _freemium_user_note()}\n\n"
            "\u2139\ufe0f Для ответов я использую ИИ-модель. "
            "Ваши сообщения обрабатываются для генерации ответов "
            "и улучшения качества сервиса. Подробнее: /privacy\n\n"
            "<b>Команды:</b>\n"
            "/reset — начать новый диалог\n"
            "/link_voice — привязать Алису\n"
            "/unlink_voice — отвязать Алису\n"
            "/invite — пригласить друга\n"
            "/privacy — политика конфиденциальности\n"
            "/help — помощь",
            reply_markup=keyboard,
        )
    else:
        await message.answer(
            "<b>Привет! Я бот-помощник ВкусВилл.</b>\n\n"
            "Помогу подобрать продукты и собрать корзину. "
            "Просто напиши, что хочешь купить!\n\n"
            "Например:\n"
            "- <i>Собери корзину для завтрака на двоих</i>\n"
            "- <i>Хочу купить молоко, хлеб и сыр</i>\n"
            "- <i>Подбери продукты для ужина, бюджет 1000 руб</i>\n\n"
            f"{await _freemium_user_note()}\n\n"
            "<b>Команды:</b>\n"
            "/reset — начать новый диалог\n"
            "/link_voice — привязать Алису\n"
            "/unlink_voice — отвязать Алису\n"
            "/invite — пригласить друга\n"
            "/privacy — политика конфиденциальности\n"
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
        f"{await _freemium_user_note()}\n\n"
        "<b>Команды:</b>\n"
        "/reset — сбросить историю диалога\n"
        "/link_voice — привязать Алису\n"
        "/unlink_voice — отвязать Алису\n"
        "/invite — пригласить друга и получить бонусные корзины\n"
        "/survey — пройти опрос и получить бонусные корзины\n"
        "/privacy — политика конфиденциальности"
    )


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
    """Обработчик команды /me."""
    # ... реализация команды /me ...
    pass


@router.message(Command("invite"))
async def cmd_invite(message: Message) -> None:
    """Обработчик команды /invite."""
    # ... реализация команды /invite ...
    pass


@router.message(Command("link_voice"))
async def cmd_link_voice(message: Message) -> None:
    """Обработчик команды /link_voice."""
    # ... реализация команды /link_voice ...
    pass


@router.message(Command("unlink_voice"))
async def cmd_unlink_voice(message: Message) -> None:
    """Обработчик команды /unlink_voice."""
    # ... реализация команды /unlink_voice ...
    pass


@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    """Обработчик команды /reset."""
    # ... реализация команды /reset ...
    pass


@router.message(Command("privacy"))
async def cmd_privacy(
    message: Message,
    user_store: UserStore | None = None,
) -> None:
    print(f"DEBUG: Entering cmd_privacy for user_id={message.from_user.id}")
    """Обработчик команды /privacy — политика конфиденциальности."""
    await message.answer(
        "<b>Политика конфиденциальности</b>\n\n"
        "<b>Какие данные обрабатываются:</b>\n"
        "\u2022 Telegram ID — для идентификации в боте\n"
        "\u2022 Текст сообщений — передаётся в ИИ-модель "
        "для генерации ответов\n"
        "\u2022 Предпочтения — для персонализации подбора товаров\n"
        "\u2022 История диалога — для контекста беседы (хранится временно)\n\n"
        "<b>Что мы НЕ сохраняем:</b>\n"
        "\u2022 Имя, фамилию, username из Telegram\n"
        "\u2022 Телефон, email, номера карт — автоматически маскируются\n\n"
        "<b>Кому передаются данные:</b>\n"
        "\u2022 ИИ-модель (Yandex Cloud) — текст сообщений для генерации ответов\n"
        "\u2022 ВкусВилл — поисковые запросы товаров (без вашего ID)\n"
        "\u2022 Open Food Facts — названия продуктов для КБЖУ (без ID)\n\n"
        "<b>Защита:</b>\n"
        "\u2022 Telegram ID хешируется в аналитике\n"
        "\u2022 Логи хранятся не более 90 дней\n"
        "\u2022 Код бота открыт — можете проверить сами\n\n"
        "<b>Ваши права:</b>\n"
        "\u2022 /reset — удалить историю диалога\n"
        "\u2022 «Удали предпочтение [категория]» — удалить предпочтение\n"
        "\u2022 Полное удаление данных — d.pukinov@yandex.ru\n\n"
        "<i>Продолжая использование бота, вы соглашаетесь "
        "с обработкой данных в указанных целях.</i>"
    )



@router.callback_query(F.data == "consent_accept")
async def consent_accept_callback(
    callback: CallbackQuery,
    user_store: UserStore | None = None,
) -> None:
    print(f"DEBUG: Entering consent_accept_callback for user_id={callback.from_user.id}")
    """Обработка нажатия кнопки «Понятно, начать!» — фиксация explicit consent."""
    if not callback.from_user or not callback.message:
        return
    if user_store is not None:
        with contextlib.suppress(Exception):
            await user_store.mark_consent(callback.from_user.id, "explicit")
            await user_store.log_event(
                callback.from_user.id,
                "consent_given",
                {"consent_type": "explicit"},
            )
    await callback.message.edit_text(
        "<b>Привет! Я бот-помощник ВкусВилл.</b>\n\n"
        "Помогу подобрать продукты и собрать корзину. "
        "Просто напиши, что хочешь купить!\n\n"
        "Например:\n"
        "- <i>Собери корзину для завтрака на двоих</i>\n"
        "- <i>Хочу купить молоко, хлеб и сыр</i>\n"
        "- <i>Подбери продукты для ужина, бюджет 1000 руб</i>\n\n"
        f"{await _freemium_user_note()}\n\n"
        "<b>Команды:</b>\n"
        "/reset — начать новый диалог\n"
        "/link_voice — привязать Алису\n"
        "/unlink_voice — отвязать Алису\n"
        "/invite — пригласить друга\n"
        "/privacy — политика конфиденциальности\n"
        "/help — помощь"
    )
    await callback.answer()


@router.message()
async def handle_text(
    message: Message,
    chat_engine: ChatEngineProtocol,
    user_store: UserStore | None = None,
) -> None:
    print(f"DEBUG: Entering handle_text for user_id={message.from_user.id if message.from_user else 'None'}")
    """Обработчик текстовых сообщений — основная логика бота."""
    if not message.from_user or not message.text:
        return

    user_id = message.from_user.id

    # Survey шаг 3: перехватываем текстовый отзыв, если ожидается.
    # Всегда очищаем pending, даже если user_store недоступен,
    # чтобы пользователь не застрял в цикле перехвата.
    pending = None
    if user_store is not None:
        pending = await user_store.get_survey_pending(user_id)

    if pending:
        if user_store is not None:
            await user_store.clear_survey_pending(user_id)
            # from vkuswill_bot.bot.handlers.survey_handlers import _finish_survey # Import needed for _finish_survey
            # _ok, text = await _finish_survey(
            #     user_id,
            #     user_store,
            #     pending["pmf"],
            #     pending["feature"],
            #     feedback,
            # )
            await message.answer("feedback received and pending cleared") # Placeholder
            return
        # user_store недоступен — pending очищен, сообщаем об ошибке,
        # пользователь сможет повторить опрос через /survey
        await message.answer("Не удалось сохранить отзыв. Попробуйте позже: /survey")
        return

    # Implicit consent: если пользователь отправил текст без явного согласия,
    # фиксируем факт использования как implicit consent (ADR-002)
    if user_store is not None:
        was_new = await user_store.mark_consent(user_id, "implicit")
        if was_new:
            await user_store.log_event(
                user_id,
                "consent_given",
                {"consent_type": "implicit"},
            )

    # Показываем индикатор набора текста во время обработки
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        _send_typing_periodically(message, stop_typing),
    )

    # Прогресс-сообщение: создаём и обновляем по мере выполнения
    progress_msg: Message | None = None
    _last_progress_text = ""

    async def _on_progress(text: str) -> None:
        nonlocal progress_msg, _last_progress_text
        if text == _last_progress_text:
            return
        _last_progress_text = text
        with contextlib.suppress(Exception):
            if progress_msg is None:
                progress_msg = await message.answer(text)
            else:
                await progress_msg.edit_text(text)

    try:
        response = await chat_engine.process_message(
            user_id,
            message.text,
            on_progress=_on_progress,
        )
    except Exception as e:
        logger.error(
            "Ошибка обработки сообщения пользователя %d: %s",
            user_id,
            e,
            exc_info=True,
        )
        if user_store is not None:
            await user_store.log_event(
                user_id,
                "bot_error",
                {
                    "error_type": type(e).__name__,
                },
            )
        response = (
            "Произошла ошибка при обработке запроса. "
            "Попробуйте позже или начните новый диалог: /reset"
        )
    finally:
        stop_typing.set()
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task
        # Удаляем прогресс-сообщение перед отправкой ответа
        if progress_msg is not None:
            with contextlib.suppress(Exception):
                await progress_msg.delete()

    preview = build_telegram_delivery_preview(response)
    for i, chunk in enumerate(preview.chunks):
        is_last = i == len(preview.chunks) - 1
        await message.answer(chunk, reply_markup=preview.cart_keyboard if is_last else None)


async def _freemium_user_note() -> str:
    """Коротко описать условия freemium для пользовательских сообщений."""
    from vkuswill_bot.config import config as app_config

    return (
        "<b>Условия корзин:</b>\n"
        f"• Первые {app_config.free_trial_days} дней — без ограничений\n"
        f"• /survey — +{app_config.bonus_cart_limit} корзин\n"
        "• /reset — сброс корзины\n"
        "\n"
        "Приглашайте друзей и получайте бонусы!"
    )


async def _process_referral_start(
    message: Message,
    user_store: UserStore,
    new_user_id: int,
    referrer_id: int,
) -> None:
    """Обработать реферальную привязку при /start ref_*.

    Бонус не начисляется сразу: начисление происходит
    после первой успешной корзины приглашённого пользователя.
    """
    try:
        result = await user_store.process_referral(new_user_id, referrer_id)
    except Exception as e:
        logger.error("Ошибка обработки реферала: %s", e)
        return

    if not result.get("success"):
        logger.debug(
            "Реферал не обработан для %d → %d: %s",
            new_user_id,
            referrer_id,
            result.get("reason"),
        )
        return

    # Логируем успешную привязку
    with contextlib.suppress(Exception):
        await user_store.log_event(
            new_user_id,
            "referral_linked",
            {
                "referrer_id": referrer_id,
            },
        )

    # Уведомляем реферера о привязке, бонус будет позже
    if message.bot is not None:
        with contextlib.suppress(Exception):
            await message.bot.send_message(
                referrer_id,
                f"🎉 Ваш друг присоединился к боту!\n\n"
                f"Бонус +{app_config.referral_cart_bonus} корзины "
                "будет начислен после его первой успешной корзины.",
            )
