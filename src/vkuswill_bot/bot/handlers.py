"""Обработчики команд и сообщений Telegram-бота."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from vkuswill_bot.services.gigachat_service import GigaChatService

if TYPE_CHECKING:
    from vkuswill_bot.services.stats_aggregator import StatsAggregator
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

# Максимальная длина одного сообщения в Telegram
MAX_TELEGRAM_MESSAGE_LENGTH = 4096

# ---------------------------------------------------------------------------
# HTML-санитизация: whitelist безопасных Telegram-тегов
# ---------------------------------------------------------------------------

# Теги, которые поддерживает Telegram Bot API в ParseMode.HTML
_ALLOWED_TAGS = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "code",
        "pre",
        "a",
        "blockquote",
        "tg-spoiler",
        "tg-emoji",
    }
)

# Regex: находит все HTML-теги  <tag ...>, </tag>, <tag/>
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^>]*)?)(/?\s*)>")

# Regex: валидирует атрибут href с http/https URL (для <a>)
_SAFE_HREF_RE = re.compile(r'^\s+href\s*=\s*"https?://[^"]*"\s*$')


def _sanitize_telegram_html(text: str) -> str:
    """Санитизация HTML по whitelist-принципу.

    Разрешённые теги Telegram (b, i, a href, code, pre и др.) —
    пропускаются. Все остальные теги (script, img, iframe и пр.) —
    экранируются в &lt;/&gt;.

    HTML-сущности (&nbsp;, &amp; и др.) сохраняются как есть.
    """

    def _check_tag(match: re.Match) -> str:
        full = match.group(0)
        closing = match.group(1)  # "/" для закрывающих тегов
        tag = match.group(2).lower()
        attrs = match.group(3)  # строка атрибутов

        # Тег не в whitelist — экранируем
        if tag not in _ALLOWED_TAGS:
            return full.replace("<", "&lt;").replace(">", "&gt;")

        # Закрывающий тег — безопасен
        if closing:
            return full

        # <a href="https://..."> — проверяем что href безопасен
        if tag == "a" and attrs.strip():
            if not _SAFE_HREF_RE.match(attrs):
                return full.replace("<", "&lt;").replace(">", "&gt;")
            return full

        # Остальные разрешённые теги — убираем атрибуты для безопасности
        # (предотвращает <b onclick="..."> и подобное)
        if attrs.strip():
            return f"<{tag}>"

        return full

    return _TAG_RE.sub(_check_tag, text)


router = Router()
admin_router = Router()


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    user_store: UserStore | None = None,
    db_user: dict | None = None,
) -> None:
    """Обработчик команды /start."""
    # Парсинг deep link для определения источника
    start_param: str | None = None
    if message.text and message.text.startswith("/start "):
        start_param = message.text.split(maxsplit=1)[1].strip()
    source = "organic"
    referrer_id: int | None = None
    if start_param:
        if start_param.startswith("ref_"):
            ref_value = start_param[4:]
            # Обратная совместимость: ref_<user_id> (число)
            try:
                referrer_id = int(ref_value)
            except ValueError:
                # Новый формат: ref_<referral_code> (строка)
                if user_store is not None:
                    with contextlib.suppress(Exception):
                        referrer_id = await user_store.find_user_by_referral_code(
                            ref_value,
                        )
            # source = "referral" только если реферер найден
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

    # --- Обработка реферала для новых пользователей ---
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
        "/invite — пригласить друга\n"
        "/help — помощь"
    )


async def _process_referral_start(
    message: Message,
    user_store: UserStore,
    new_user_id: int,
    referrer_id: int,
) -> None:
    """Обработать реферальную привязку при /start ref_*.

    Начисляет бонус рефереру и отправляет ему уведомление.
    """
    from vkuswill_bot.config import config as app_config

    try:
        result = await user_store.process_referral(
            new_user_id,
            referrer_id,
            app_config.referral_cart_bonus,
        )
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

    # Логируем начисление бонуса
    with contextlib.suppress(Exception):
        await user_store.log_event(
            referrer_id,
            "referral_bonus_granted",
            {
                "referred_user_id": new_user_id,
                "bonus": result["bonus"],
                "new_limit": result["new_limit"],
            },
        )

    # Уведомляем реферера
    if message.bot is not None:
        with contextlib.suppress(Exception):
            await message.bot.send_message(
                referrer_id,
                f"🎉 Ваш друг присоединился к боту!\n\n"
                f"+{result['bonus']} корзин. "
                f"Новый лимит: {result['new_limit']}.",
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
        "<b>Команды:</b>\n"
        "/reset — сбросить историю диалога\n"
        "/invite — пригласить друга и получить бонусные корзины\n"
        "/survey — пройти опрос и получить бонусные корзины"
    )


@router.message(Command("invite"))
async def cmd_invite(
    message: Message,
    user_store: UserStore | None = None,
    db_user: dict | None = None,
) -> None:
    """Обработчик команды /invite — реферальная ссылка."""
    if not message.from_user or not db_user:
        return
    if user_store is None:
        await message.answer("Функция временно недоступна.")
        return

    user_id = message.from_user.id

    try:
        referral_code = await user_store.get_or_create_referral_code(user_id)
        referral_count = await user_store.count_referrals(user_id)
    except Exception as e:
        logger.error("Ошибка получения реферального кода для %d: %s", user_id, e)
        await message.answer("Произошла ошибка. Попробуйте позже.")
        return

    # Получаем username бота для формирования ссылки
    if message.bot is None:
        await message.answer("Произошла ошибка. Попробуйте позже.")
        return
    bot_info = await message.bot.get_me()
    bot_username = bot_info.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{referral_code}"

    from vkuswill_bot.config import config as app_config

    bonus = app_config.referral_cart_bonus

    # Информация о текущих корзинах
    cart_limit = db_user.get("cart_limit", app_config.free_cart_limit)
    carts_created = db_user.get("carts_created", 0)
    remaining = max(0, cart_limit - carts_created)

    text = (
        "<b>👫 Пригласи друга — получи корзины!</b>\n\n"
        f"За каждого друга, который начнёт пользоваться ботом, "
        f"вы получите <b>+{bonus} корзины</b>.\n\n"
        f"🔗 Ваша ссылка для приглашения:\n"
        f"<code>{referral_link}</code>\n\n"
    )

    if referral_count > 0:
        text += f"Приглашено друзей: <b>{referral_count}</b>\n"
    text += f"Корзин доступно: <b>{remaining}</b> из <b>{cart_limit}</b>"

    await message.answer(text)


@router.message(Command("reset"))
async def cmd_reset(
    message: Message,
    gigachat_service: GigaChatService,
) -> None:
    """Обработчик команды /reset — сброс диалога."""
    if message.from_user:
        await gigachat_service.reset_conversation(message.from_user.id)
    await message.answer("Диалог сброшен. Напиши, что хочешь купить!")


# ---------------------------------------------------------------------------
# Survey Flow — опрос для получения бонусных корзин (freemium)
# ---------------------------------------------------------------------------
# Вопрос 1: Sean Ellis PMF-тест (product-market fit).
# Вопрос 2: Самая полезная функция бота.
# Вопрос 3: Открытый отзыв — текст или кнопка «Всё отлично».
# ---------------------------------------------------------------------------

# PMF-ответы (Sean Ellis test)
_PMF_LABELS = {
    "very": "Очень расстроюсь",
    "somewhat": "Немного",
    "not": "Не расстроюсь",
}

# Маппинг фич для отображения
_FEATURE_LABELS = {
    "search": "Поиск товаров",
    "recipe": "Подбор рецепта",
    "cart": "Сборка корзины",
    "other": "Другое",
}

# Промежуточное состояние: ожидание текстового отзыва (шаг 3).
# user_id → {"pmf": ..., "feature": ...}
_survey_pending: dict[int, dict[str, str]] = {}
_SURVEY_PENDING_MAX = 1000


def is_survey_pending(user_id: int) -> bool:
    """Проверить, ожидается ли текстовый отзыв от пользователя."""
    return user_id in _survey_pending


async def _finish_survey(
    user_id: int,
    user_store: UserStore,
    pmf: str,
    feature: str,
    feedback: str | None,
) -> tuple[bool, str]:
    """Завершить опрос: сохранить результаты, выдать бонус.

    Returns:
        (success, response_text) — результат и текст для пользователя.
    """
    try:
        was_marked = await user_store.mark_survey_completed_if_not(user_id)
        if not was_marked:
            return True, "Вы уже прошли опрос. Спасибо!"

        metadata: dict = {
            "pmf": pmf,
            "useful_feature": feature,
        }
        if feedback:
            metadata["feedback"] = feedback[:500]

        await user_store.log_event(user_id, "survey_completed", metadata)

        from vkuswill_bot.config import config as app_config

        bonus = app_config.bonus_cart_limit
        new_limit = await user_store.grant_bonus_carts(user_id, bonus)
        await user_store.log_event(
            user_id,
            "bonus_carts_granted",
            {"reason": "survey", "amount": bonus, "new_limit": new_limit},
        )
    except Exception as e:
        logger.error("Ошибка сохранения survey для %d: %s", user_id, e)
        return False, "Произошла ошибка при сохранении. Попробуйте позже: /survey"

    pmf_label = _PMF_LABELS.get(pmf, pmf)
    feature_label = _FEATURE_LABELS.get(feature, feature)
    return True, (
        f"{pmf_label} | {feature_label}\n\n"
        "<b>Спасибо за обратную связь!</b>\n\n"
        f"🎁 Вам добавлено {bonus} корзин. "
        f"Теперь доступно {new_limit} корзин.\n"
        "Напишите, что хотите заказать!"
    )


@router.message(Command("survey"))
async def cmd_survey(
    message: Message,
    user_store: UserStore | None = None,
    db_user: dict | None = None,
) -> None:
    """Запуск опроса для получения бонусных корзин."""
    if not message.from_user or not db_user:
        return
    if user_store is None:
        await message.answer("Опрос временно недоступен.")
        return

    if db_user.get("survey_completed"):
        await message.answer("Вы уже прошли опрос. Спасибо за обратную связь!")
        return

    # Очищаем возможное незавершённое состояние
    _survey_pending.pop(message.from_user.id, None)

    # Шаг 1: PMF (Sean Ellis test)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="😢 Очень расстроюсь",
                    callback_data="survey_pmf_very",
                )
            ],
            [
                InlineKeyboardButton(
                    text="😐 Немного расстроюсь",
                    callback_data="survey_pmf_somewhat",
                )
            ],
            [
                InlineKeyboardButton(
                    text="😊 Не расстроюсь",
                    callback_data="survey_pmf_not",
                )
            ],
        ]
    )
    await message.answer(
        "<b>Короткий опрос (3 вопроса)</b>\n\n"
        "Как бы вы расстроились, если бот перестанет работать?",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("survey_pmf_"))
async def survey_pmf_callback(callback: CallbackQuery) -> None:
    """Шаг 1: PMF → переход к выбору полезной фичи."""
    if not callback.data or not callback.message:
        return
    # survey_pmf_<pmf>
    pmf = callback.data.split("_")[2]  # very / somewhat / not
    pmf_label = _PMF_LABELS.get(pmf, pmf)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔍 Поиск товаров",
                    callback_data=f"survey_feat_search_{pmf}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🍳 Подбор рецепта",
                    callback_data=f"survey_feat_recipe_{pmf}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛒 Сборка корзины",
                    callback_data=f"survey_feat_cart_{pmf}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Другое",
                    callback_data=f"survey_feat_other_{pmf}",
                )
            ],
        ]
    )
    await callback.message.edit_text(
        f"{pmf_label}\n\nКакая функция для вас самая полезная?",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("survey_feat_"))
async def survey_feature_callback(callback: CallbackQuery) -> None:
    """Шаг 2: Фича → переход к открытому вопросу об улучшениях."""
    if not callback.data or not callback.message or not callback.from_user:
        return
    parts = callback.data.split("_")
    # survey_feat_<feature>_<pmf>
    feature = parts[2]
    pmf = parts[3]
    feature_label = _FEATURE_LABELS.get(feature, feature)
    pmf_label = _PMF_LABELS.get(pmf, pmf)

    # Сохраняем промежуточное состояние для шага 3 (текстовый ввод)
    user_id = callback.from_user.id
    if len(_survey_pending) >= _SURVEY_PENDING_MAX:
        # Простая очистка: удаляем первую половину
        keys = list(_survey_pending.keys())
        for k in keys[: len(keys) // 2]:
            del _survey_pending[k]
    _survey_pending[user_id] = {"pmf": pmf, "feature": feature}

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👍 Всё отлично",
                    callback_data=f"survey_done_{pmf}_{feature}",
                )
            ],
        ]
    )
    await callback.message.edit_text(
        f"{pmf_label} | {feature_label}\n\n"
        "Что бы вы хотели улучшить в боте?\n"
        "Напишите текстом или нажмите кнопку:",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("survey_done_"))
async def survey_done_callback(
    callback: CallbackQuery,
    user_store: UserStore | None = None,
) -> None:
    """Шаг 3 (кнопка «Всё отлично»): завершение survey, выдача бонуса."""
    if not callback.data or not callback.message or not callback.from_user:
        return
    if user_store is None:
        await callback.answer("Ошибка сохранения.")
        return

    # survey_done_<pmf>_<feature>
    parts = callback.data.split("_")
    pmf = parts[2]
    feature = parts[3]
    user_id = callback.from_user.id

    # Убираем из pending
    _survey_pending.pop(user_id, None)

    _ok, text = await _finish_survey(user_id, user_store, pmf, feature, None)
    await callback.message.edit_text(text)
    await callback.answer()


@router.message(F.text)
async def handle_text(
    message: Message,
    gigachat_service: GigaChatService,
    user_store: UserStore | None = None,
) -> None:
    """Обработчик текстовых сообщений — основная логика бота."""
    if not message.from_user or not message.text:
        return

    user_id = message.from_user.id

    # Survey шаг 3: перехватываем текстовый отзыв, если ожидается.
    # Всегда очищаем pending, даже если user_store недоступен,
    # чтобы пользователь не застрял в цикле перехвата.
    if user_id in _survey_pending:
        pending = _survey_pending.pop(user_id)
        if user_store is not None:
            feedback = message.text[:500]
            _ok, text = await _finish_survey(
                user_id,
                user_store,
                pending["pmf"],
                pending["feature"],
                feedback,
            )
            await message.answer(text)
            return
        # user_store недоступен — pending очищен, сообщаем об ошибке,
        # пользователь сможет повторить опрос через /survey
        await message.answer("Не удалось сохранить отзыв. Попробуйте позже: /survey")
        return

    # Показываем индикатор набора текста во время обработки
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_send_typing_periodically(message, stop_typing))

    try:
        response = await gigachat_service.process_message(user_id, message.text)
    except Exception as e:
        logger.error(
            "Ошибка обработки сообщения пользователя %d: %s",
            user_id,
            e,
            exc_info=True,
        )
        if user_store is not None:
            with contextlib.suppress(Exception):
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

    # Санитизация: пропускаем только Telegram-безопасные HTML-теги,
    # экранируем опасные (script, img, iframe и пр.)
    safe_response = _sanitize_telegram_html(response)

    # Разбиваем длинные сообщения по лимиту Telegram
    chunks = _split_message(safe_response, MAX_TELEGRAM_MESSAGE_LENGTH)
    for chunk in chunks:
        await message.answer(chunk)


async def _send_typing_periodically(
    message: Message,
    stop_event: asyncio.Event,
) -> None:
    """Периодически отправляет индикатор 'печатает...' в чат."""
    while not stop_event.is_set():
        try:
            await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        except Exception as e:
            logger.debug("Ошибка отправки typing indicator: %s", e)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)


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


# ---------------------------------------------------------------------------
# Админ-команды (admin_router)
# ---------------------------------------------------------------------------


async def _check_admin(message: Message) -> UserStore | None:
    """Проверить, что отправитель — администратор.

    Returns:
        UserStore если проверка пройдена, None если нет прав.
    """
    if not message.from_user:
        return None

    # user_store инжектируется через UserMiddleware → data
    # Для admin_router он передаётся через dp["user_store"]
    # и доступен как keyword-аргумент
    return None  # pragma: no cover — заглушка, реальная проверка ниже


@admin_router.message(Command("admin_block"))
async def cmd_admin_block(
    message: Message,
    user_store: UserStore,
    db_user: dict | None = None,
) -> None:
    """Заблокировать пользователя: /admin_block <user_id> <причина>."""
    if not message.from_user:
        return

    # Проверка прав администратора
    if not db_user or db_user.get("role") != "admin":
        await message.answer("У вас нет прав администратора.")
        return

    if not message.text:
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer("Использование: /admin_block &lt;user_id&gt; [причина]")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return

    reason = parts[2] if len(parts) > 2 else ""

    # Нельзя заблокировать самого себя
    if target_id == message.from_user.id:
        await message.answer("Нельзя заблокировать самого себя.")
        return

    success = await user_store.block(target_id, reason)
    if success:
        await message.answer(f"Пользователь {target_id} заблокирован.")
    else:
        await message.answer(f"Пользователь {target_id} не найден.")


@admin_router.message(Command("admin_unblock"))
async def cmd_admin_unblock(
    message: Message,
    user_store: UserStore,
    db_user: dict | None = None,
) -> None:
    """Разблокировать пользователя: /admin_unblock <user_id>."""
    if not message.from_user:
        return

    if not db_user or db_user.get("role") != "admin":
        await message.answer("У вас нет прав администратора.")
        return

    if not message.text:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /admin_unblock &lt;user_id&gt;")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return

    success = await user_store.unblock(target_id)
    if success:
        await message.answer(f"Пользователь {target_id} разблокирован.")
    else:
        await message.answer(f"Пользователь {target_id} не найден.")


@admin_router.message(Command("admin_stats"))
async def cmd_admin_stats(
    message: Message,
    user_store: UserStore,
    db_user: dict | None = None,
) -> None:
    """Общая статистика бота: /admin_stats."""
    if not message.from_user:
        return

    if not db_user or db_user.get("role") != "admin":
        await message.answer("У вас нет прав администратора.")
        return

    total = await user_store.count_users()
    active_today = await user_store.count_active_today()

    await message.answer(
        "<b>Статистика бота</b>\n\n"
        f"Всего пользователей: <b>{total}</b>\n"
        f"Активных сегодня (DAU): <b>{active_today}</b>"
    )


@admin_router.message(Command("admin_user"))
async def cmd_admin_user(
    message: Message,
    user_store: UserStore,
    db_user: dict | None = None,
) -> None:
    """Информация о пользователе: /admin_user <user_id>."""
    if not message.from_user:
        return

    if not db_user or db_user.get("role") != "admin":
        await message.answer("У вас нет прав администратора.")
        return

    if not message.text:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /admin_user &lt;user_id&gt;")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return

    target = await user_store.get(target_id)
    if not target:
        await message.answer(f"Пользователь {target_id} не найден.")
        return

    role = target.get("role", "user")
    status = target.get("status", "active")
    msg_count = target.get("message_count", 0)
    carts = target.get("carts_created", 0)
    cart_limit = target.get("cart_limit", 5)
    created = target.get("created_at", "—")
    last_msg = target.get("last_message_at") or "—"
    blocked_reason = target.get("blocked_reason") or "—"

    text = f"<b>Пользователь {target_id}</b>\n\nРоль: <b>{role}</b>\nСтатус: <b>{status}</b>\n"
    if status == "blocked":
        text += f"Причина блокировки: {blocked_reason}\n"
    text += (
        f"\nСообщений: {msg_count}"
        f"\nКорзины: {carts}/{cart_limit}"
        f"\nЗарегистрирован: {created}"
        f"\nПоследнее сообщение: {last_msg}"
    )

    await message.answer(text)


@admin_router.message(Command("admin_analytics"))
async def cmd_admin_analytics(
    message: Message,
    db_user: dict | None = None,
    stats_aggregator: StatsAggregator | None = None,
) -> None:
    """Аналитика за N дней: /admin_analytics [days].

    Выводит агрегированные метрики из daily_stats:
    DAU, новые пользователи, сессии, корзины, GMV, ошибки.
    """
    if not message.from_user:
        return
    if not db_user or db_user.get("role") != "admin":
        await message.answer("У вас нет прав администратора.")
        return
    if stats_aggregator is None:
        await message.answer("StatsAggregator не настроен.")
        return

    # Парсим количество дней (по умолчанию 7)
    days = 7
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            with contextlib.suppress(ValueError):
                days = max(1, min(int(parts[1]), 365))

    try:
        s = await stats_aggregator.get_summary(days)
    except Exception as e:
        logger.error("Ошибка получения аналитики: %s", e)
        await message.answer("Ошибка получения данных.")
        return

    avg_dau = float(s.get("avg_dau", 0))
    total_new = int(s.get("total_new_users", 0))
    total_sessions = int(s.get("total_sessions", 0))
    total_carts = int(s.get("total_carts", 0))
    total_gmv = float(s.get("total_gmv", 0))
    avg_cart = float(s.get("avg_cart_value", 0))
    total_searches = int(s.get("total_searches", 0))
    total_errors = int(s.get("total_errors", 0))
    total_limits = int(s.get("total_limits", 0))
    total_surveys = int(s.get("total_surveys", 0))
    period_start = s.get("period_start", "—")
    period_end = s.get("period_end", "—")

    # Конверсия: корзины / сессии
    conv = (total_carts / total_sessions * 100) if total_sessions > 0 else 0

    text = (
        f"<b>Аналитика за {days} дн.</b>\n"
        f"<i>{period_start} — {period_end}</i>\n\n"
        f"DAU (средн.): <b>{avg_dau:.0f}</b>\n"
        f"Новых пользователей: <b>{total_new}</b>\n"
        f"Сессий: <b>{total_sessions}</b>\n\n"
        f"Корзин создано: <b>{total_carts}</b>\n"
        f"GMV: <b>{total_gmv:,.0f} ₽</b>\n"
        f"Средний чек: <b>{avg_cart:,.0f} ₽</b>\n"
        f"Конверсия (корзины/сессии): <b>{conv:.1f}%</b>\n\n"
        f"Поисков: <b>{total_searches}</b>\n"
        f"Ошибок: <b>{total_errors}</b>\n"
        f"Лимитов корзин: <b>{total_limits}</b>\n"
        f"Опросов: <b>{total_surveys}</b>"
    )
    await message.answer(text)


@admin_router.message(Command("admin_funnel"))
async def cmd_admin_funnel(
    message: Message,
    db_user: dict | None = None,
    stats_aggregator: StatsAggregator | None = None,
) -> None:
    """Воронка за N дней: /admin_funnel [days].

    Показывает пользовательскую воронку:
    Старт → Активные → Искали → Создали корзину → Лимит → Опрос.
    """
    if not message.from_user:
        return
    if not db_user or db_user.get("role") != "admin":
        await message.answer("У вас нет прав администратора.")
        return
    if stats_aggregator is None:
        await message.answer("StatsAggregator не настроен.")
        return

    days = 7
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            with contextlib.suppress(ValueError):
                days = max(1, min(int(parts[1]), 365))

    try:
        f = await stats_aggregator.get_funnel(days)
    except Exception as e:
        logger.error("Ошибка получения воронки: %s", e)
        await message.answer("Ошибка получения данных.")
        return

    started = int(f.get("started", 0))
    active = int(f.get("active", 0))
    searched = int(f.get("searched", 0))
    carted = int(f.get("carted", 0))
    hit_limit = int(f.get("hit_limit", 0))
    surveyed = int(f.get("surveyed", 0))

    def _pct(part: int, total: int) -> str:
        if total == 0:
            return "—"
        return f"{part / total * 100:.0f}%"

    text = (
        f"<b>Воронка за {days} дн.</b>\n\n"
        f"1. /start: <b>{started}</b>\n"
        f"2. Активные (сессии): <b>{active}</b> ({_pct(active, started)})\n"
        f"3. Искали товары: <b>{searched}</b> ({_pct(searched, active)})\n"
        f"4. Создали корзину: <b>{carted}</b> ({_pct(carted, searched)})\n"
        f"5. Достигли лимита: <b>{hit_limit}</b> ({_pct(hit_limit, carted)})\n"
        f"6. Прошли опрос: <b>{surveyed}</b> ({_pct(surveyed, hit_limit)})\n\n"
        f"<i>Конверсия start→cart: {_pct(carted, started)}</i>"
    )
    await message.answer(text)


@admin_router.message(Command("admin_grant_carts"))
async def cmd_admin_grant_carts(
    message: Message,
    user_store: UserStore | None = None,
    db_user: dict | None = None,
) -> None:
    """Выдать корзины пользователю: /admin_grant_carts <user_id> <amount>."""
    if not message.from_user:
        return
    if not db_user or db_user.get("role") != "admin":
        await message.answer("У вас нет прав администратора.")
        return
    if user_store is None:
        await message.answer("База данных недоступна.")
        return
    if not message.text:
        return

    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: /admin_grant_carts &lt;user_id&gt; &lt;amount&gt;")
        return

    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("user_id и amount должны быть числами.")
        return

    if amount < 1 or amount > 100:
        await message.answer("amount должен быть от 1 до 100.")
        return

    new_limit = await user_store.grant_bonus_carts(target_id, amount)
    if new_limit > 0:
        await user_store.log_event(
            target_id,
            "bonus_carts_granted",
            {
                "reason": "admin",
                "amount": amount,
                "new_limit": new_limit,
                "granted_by": message.from_user.id,
            },
        )
        await message.answer(
            f"Пользователю {target_id} добавлено {amount} корзин. Новый лимит: {new_limit}."
        )
    else:
        await message.answer(f"Пользователь {target_id} не найден.")


@admin_router.message(Command("admin_survey_stats"))
async def cmd_admin_survey_stats(
    message: Message,
    user_store: UserStore | None = None,
    db_user: dict | None = None,
) -> None:
    """Статистика по survey: /admin_survey_stats."""
    if not message.from_user:
        return
    if not db_user or db_user.get("role") != "admin":
        await message.answer("У вас нет прав администратора.")
        return
    if user_store is None:
        await message.answer("База данных недоступна.")
        return

    try:
        stats = await user_store.get_survey_stats()
    except Exception as e:
        logger.error("Ошибка получения survey статистики: %s", e)
        await message.answer("Ошибка получения данных.")
        return

    total = stats["total"]
    if total == 0:
        await message.answer("Ни один пользователь ещё не прошёл опрос.")
        return

    # PMF distribution
    pmf_lines = "\n".join(
        f"  {_PMF_LABELS.get(r['answer'], r['answer'] or '—')}: {r['cnt']}" for r in stats["pmf"]
    )

    # PMF score: % "very disappointed" — ключевая метрика PMF
    very_count = sum(r["cnt"] for r in stats["pmf"] if r.get("answer") == "very")
    pmf_score = (very_count / total * 100) if total > 0 else 0

    # Features
    feats = "\n".join(
        f"  {_FEATURE_LABELS.get(r['feat'], r['feat'] or '—')}: {r['cnt']}"
        for r in stats["features"]
    )

    # Feedback
    fb_count = stats.get("feedback_count", 0)
    fb_lines = ""
    for r in stats.get("recent_feedback", [])[:5]:
        fb_text = r.get("text", "")
        if fb_text:
            fb_lines += f"  \u2022 {fb_text[:100]}\n"

    text = (
        f"<b>Survey статистика</b>\n\n"
        f"Заполнили: <b>{total}</b>\n"
        f"PMF score: <b>{pmf_score:.0f}%</b> (очень расстроятся)\n\n"
        f"Как расстроятся:\n{pmf_lines}\n\n"
        f"Полезная фича:\n{feats}"
    )

    if fb_count > 0:
        text += f"\n\nОтзывов: <b>{fb_count}</b>"
        if fb_lines:
            text += f"\n\nПоследние:\n{fb_lines}"

    await message.answer(text)
