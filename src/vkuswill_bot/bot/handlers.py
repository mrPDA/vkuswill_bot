"""Обработчики команд и сообщений Telegram-бота."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from vkuswill_bot.config import config
from vkuswill_bot.bot.telegram_delivery import (
    MAX_TELEGRAM_MESSAGE_LENGTH,
    _extract_cart_link,
    _sanitize_telegram_html,
    _split_message,
    build_telegram_delivery_preview,
)
from vkuswill_bot.services.chat_engine import ChatEngineProtocol

if TYPE_CHECKING:
    from vkuswill_bot.services.stats_aggregator import StatsAggregator
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_TELEGRAM_MESSAGE_LENGTH",
    "_extract_cart_link",
    "_sanitize_telegram_html",
    "_split_message",
]


def _freemium_user_note() -> str:
    """Коротко описать условия freemium для пользовательских сообщений."""
    from vkuswill_bot.config import config as app_config

    return (
        "<b>Условия корзин:</b>\n"
        f"• Первые {app_config.free_trial_days} дней — без ограничений\n"
        f"• /survey — +{app_config.bonus_cart_limit} корзин\n"
        f"• /invite — +{app_config.referral_cart_bonus} корзины за друга "
        "после его первой успешной корзины\n"
        "• Оценка готовой корзины кнопками — "
        f"+{app_config.feedback_cart_bonus} корзины "
        f"(1 раз в {app_config.feedback_bonus_cooldown_days} дней)"
    )


router = Router()
admin_router = Router()


class AdminFilter(BaseFilter):
    """Фильтр: пропускает только администраторов.

    Проверяет db_user.role == 'admin'. Чистый фильтр без
    побочных эффектов — НЕ отправляет сообщения при отказе.

    ВАЖНО: это root-фильтр на admin_router (``admin_router.message.filter()``).
    В aiogram 3 root-фильтры проверяются в ``_propagate_event()`` **ДО**
    ``trigger()`` (где запускается inner middleware). Поэтому ``UserMiddleware``
    должен быть зарегистрирован как **outer_middleware** на dispatcher —
    outer middleware оборачивает ``propagate_event`` целиком и запускается
    ДО root-фильтров, гарантируя наличие ``db_user`` в kwargs.

    Сообщение об отказе отправляется отдельным хендлером
    ``handle_admin_unauthorized`` в основном router.
    """

    async def __call__(self, message: Message, **kwargs: object) -> bool:
        db_user = kwargs.get("db_user")
        is_admin = isinstance(db_user, dict) and db_user.get("role") == "admin"
        # Логируем ТОЛЬКО для admin-команд — не спамим на обычные сообщения
        if message.text and message.text.startswith("/admin_"):
            user_id = message.from_user.id if message.from_user else "?"
            role = db_user.get("role") if isinstance(db_user, dict) else "no_db_user"
            logger.info(
                "AdminFilter: user=%s role=%s is_admin=%s cmd=%s kwargs_keys=%s",
                user_id,
                role,
                is_admin,
                message.text.split()[0],
                list(kwargs.keys()),
            )
        return is_admin


# Применяем фильтр на весь admin_router — больше не нужно
# проверять роль в каждом хендлере отдельно.
admin_router.message.filter(AdminFilter())


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

    # Для новых пользователей — показываем consent notice + кнопку
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
            f"{_freemium_user_note()}\n\n"
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
            f"{_freemium_user_note()}\n\n"
            "<b>Команды:</b>\n"
            "/reset — начать новый диалог\n"
            "/link_voice — привязать Алису\n"
            "/unlink_voice — отвязать Алису\n"
            "/invite — пригласить друга\n"
            "/privacy — политика конфиденциальности\n"
            "/help — помощь"
        )


@router.callback_query(F.data == "consent_accept")
async def consent_accept_callback(
    callback: CallbackQuery,
    user_store: UserStore | None = None,
) -> None:
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
        f"{_freemium_user_note()}\n\n"
        "<b>Команды:</b>\n"
        "/reset — начать новый диалог\n"
        "/link_voice — привязать Алису\n"
        "/unlink_voice — отвязать Алису\n"
        "/invite — пригласить друга\n"
        "/privacy — политика конфиденциальности\n"
        "/help — помощь"
    )
    await callback.answer()


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
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
        from vkuswill_bot.config import config as app_config

        with contextlib.suppress(Exception):
            await message.bot.send_message(
                referrer_id,
                f"🎉 Ваш друг присоединился к боту!\n\n"
                f"Бонус +{app_config.referral_cart_bonus} корзины "
                "будет начислен после его первой успешной корзины.",
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
        f"{_freemium_user_note()}\n\n"
        "<b>Команды:</b>\n"
        "/reset — сбросить историю диалога\n"
        "/link_voice — привязать Алису\n"
        "/unlink_voice — отвязать Алису\n"
        "/invite — пригласить друга и получить бонусные корзины\n"
        "/survey — пройти опрос и получить бонусные корзины\n"
        "/privacy — политика конфиденциальности"
    )


@router.message(Command("me"))
async def cmd_me(
    message: Message,
    db_user: dict | None = None,
) -> None:
    """Диагностика: показать профиль и роль пользователя."""
    uid = message.from_user.id if message.from_user else "?"
    if db_user is None:
        await message.answer(f"user_id={uid}\ndb_user=None (UserStore не подключён)")
        return
    role = db_user.get("role", "?")
    status = db_user.get("status", "?")
    carts = db_user.get("carts_created", 0)
    limit = db_user.get("cart_limit", "?")
    survey = db_user.get("survey_completed", False)
    consent = db_user.get("consent_given_at")
    lines = [
        "<b>Профиль</b>",
        f"user_id: <code>{uid}</code>",
        f"role: <b>{role}</b>",
        f"status: {status}",
        f"carts: {carts}/{limit}",
        f"survey: {'✅' if survey else '❌'}",
        f"consent: {'✅' if consent else '❌'}",
    ]
    await message.answer("\n".join(lines))


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
        f"За каждого друга, который соберёт свою первую успешную корзину, "
        f"вы получите <b>+{bonus} корзины</b>.\n\n"
        f"🔗 Ваша ссылка для приглашения:\n"
        f"<code>{referral_link}</code>\n\n"
    )

    if referral_count > 0:
        text += f"Приглашено друзей: <b>{referral_count}</b>\n"
    text += f"Корзин доступно: <b>{remaining}</b> из <b>{cart_limit}</b>"

    await message.answer(text)


@router.message(Command("link_voice"))
async def cmd_link_voice(
    message: Message,
    user_store: UserStore | None = None,
) -> None:
    """Сгенерировать одноразовый код привязки voice-аккаунта (Алиса)."""
    if not message.from_user:
        return
    if user_store is None:
        await message.answer("Привязка Алисы временно недоступна.")
        return

    from vkuswill_bot.config import config as app_config

    ttl_minutes = max(1, getattr(app_config, "voice_link_code_ttl_minutes", 10))
    try:
        code = await user_store.create_voice_link_code(
            user_id=message.from_user.id,
            provider="alice",
            ttl_minutes=ttl_minutes,
        )
        await user_store.log_event(
            message.from_user.id,
            "voice_link_code_issued",
            {"provider": "alice", "ttl_minutes": ttl_minutes},
        )
    except Exception as e:
        logger.error("Ошибка создания voice link code для %s: %s", message.from_user.id, e)
        await message.answer("Не удалось создать код привязки. Попробуйте позже.")
        return

    await message.answer(
        "<b>Привязка Алисы</b>\n\n"
        "1. Скажите в навыке Алисы: "
        f"<code>код {code}</code>\n"
        "2. Дождитесь подтверждения привязки.\n"
        "3. После этого можно заказывать голосом.\n\n"
        f"Код действует <b>{ttl_minutes} минут</b> и работает один раз."
    )


@router.message(Command("unlink_voice"))
async def cmd_unlink_voice(
    message: Message,
    user_store: UserStore | None = None,
) -> None:
    """Отвязать аккаунт Алисы от текущего Telegram-пользователя."""
    if not message.from_user:
        return
    if user_store is None:
        await message.answer("Отвязка Алисы временно недоступна.")
        return

    try:
        revoked = await user_store.revoke_voice_links_for_user(
            user_id=message.from_user.id,
            provider="alice",
        )
        await user_store.log_event(
            message.from_user.id,
            "voice_link_revoked",
            {"provider": "alice", "revoked": revoked},
        )
    except Exception as e:
        logger.error("Ошибка отвязки Алисы для %s: %s", message.from_user.id, e)
        await message.answer("Не удалось отвязать Алису. Попробуйте позже.")
        return

    if revoked > 0:
        await message.answer(
            "<b>Алиса отвязана.</b>\n\n"
            "Голосовые заказы больше не связаны с вашим аккаунтом.\n"
            "Чтобы привязать снова, используйте /link_voice."
        )
    else:
        await message.answer(
            "Активная привязка Алисы не найдена.\nЧтобы привязать аккаунт, используйте /link_voice."
        )


@router.message(Command("reset"))
async def cmd_reset(
    message: Message,
    chat_engine: ChatEngineProtocol,
) -> None:
    """Обработчик команды /reset — сброс диалога."""
    if message.from_user:
        await chat_engine.reset_conversation(message.from_user.id)
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


# ── Кнопки обратной связи по корзине ──────────────────────────────

# Маппинг callback_data → человекочитаемая причина
_CART_FB_REASONS: dict[str, str] = {
    "cart_fb_r_products": "Не те товары",
    "cart_fb_r_quantity": "Неправильное количество",
    "cart_fb_r_price": "Слишком дорого",
    "cart_fb_r_other": "Другое",
}


def _extract_cart_url_from_keyboard(
    markup: InlineKeyboardMarkup | None,
) -> str | None:
    """Извлечь URL корзины из первой URL-кнопки клавиатуры."""
    if not markup:
        return None
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.url:
                return btn.url
    return None


def _cart_only_keyboard(cart_url: str) -> InlineKeyboardMarkup:
    """Клавиатура с единственной кнопкой «Открыть корзину»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001f6d2 Открыть корзину",
                    url=cart_url,
                ),
            ],
        ],
    )


@router.callback_query(F.data == "cart_fb_pos")
async def cart_feedback_positive(
    callback: CallbackQuery,
    user_store: UserStore | None = None,
) -> None:
    """Положительный фидбек по корзине."""
    if not callback.message or not callback.from_user:
        return

    cart_url = _extract_cart_url_from_keyboard(
        callback.message.reply_markup,  # type: ignore[union-attr]
    )
    user_id = callback.from_user.id
    feedback_bonus_granted = False
    feedback_bonus_amount = 0
    feedback_cooldown_days = 30

    if user_store is not None:
        from vkuswill_bot.config import config as app_config

        feedback_bonus_amount = app_config.feedback_cart_bonus
        feedback_cooldown_days = app_config.feedback_bonus_cooldown_days
        with contextlib.suppress(Exception):
            await user_store.log_event(
                user_id,
                "cart_feedback",
                {
                    "rating": "positive",
                    "cart_link": cart_url or "",
                },
            )
            bonus_result = await user_store.grant_feedback_bonus_if_due(
                user_id,
                amount=feedback_bonus_amount,
                cooldown_days=feedback_cooldown_days,
            )
            if isinstance(bonus_result, dict) and bonus_result.get("granted"):
                feedback_bonus_granted = True
                await user_store.log_event(
                    user_id,
                    "feedback_bonus_granted",
                    {
                        "amount": feedback_bonus_amount,
                        "new_limit": bonus_result.get("new_limit", 0),
                    },
                )

    # Убираем кнопки фидбека, оставляем только корзину + благодарность
    if cart_url:
        await callback.message.edit_reply_markup(  # type: ignore[union-attr]
            reply_markup=_cart_only_keyboard(cart_url),
        )
    answer_text = "Спасибо за отзыв! \U0001f44d"
    if feedback_bonus_granted:
        answer_text += f" Начислено +{feedback_bonus_amount} корзины."
    else:
        answer_text += f" Бонус за отзыв доступен 1 раз в {feedback_cooldown_days} дней."
    await callback.answer(answer_text)


@router.callback_query(F.data == "cart_fb_neg")
async def cart_feedback_negative(
    callback: CallbackQuery,
) -> None:
    """Негативный фидбек → показать уточняющие причины."""
    if not callback.message:
        return

    cart_url = _extract_cart_url_from_keyboard(
        callback.message.reply_markup,  # type: ignore[union-attr]
    )

    rows: list[list[InlineKeyboardButton]] = []
    if cart_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text="\U0001f6d2 Открыть корзину",
                    url=cart_url,
                ),
            ],
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="\U0001f50d Не те товары",
                    callback_data="cart_fb_r_products",
                ),
                InlineKeyboardButton(
                    text="\U0001f522 Количество",
                    callback_data="cart_fb_r_quantity",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4b8 Дорого",
                    callback_data="cart_fb_r_price",
                ),
                InlineKeyboardButton(
                    text="\U00002753 Другое",
                    callback_data="cart_fb_r_other",
                ),
            ],
        ],
    )

    await callback.message.edit_reply_markup(  # type: ignore[union-attr]
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer("Что именно не так?")


@router.callback_query(F.data.startswith("cart_fb_r_"))
async def cart_feedback_reason(
    callback: CallbackQuery,
    user_store: UserStore | None = None,
) -> None:
    """Конкретная причина негативного фидбека."""
    if not callback.data or not callback.message or not callback.from_user:
        return

    reason_key = callback.data  # e.g. cart_fb_r_products
    reason_label = _CART_FB_REASONS.get(reason_key, reason_key)
    cart_url = _extract_cart_url_from_keyboard(
        callback.message.reply_markup,  # type: ignore[union-attr]
    )
    user_id = callback.from_user.id
    feedback_bonus_granted = False
    feedback_bonus_amount = 0
    feedback_cooldown_days = 30

    if user_store is not None:
        from vkuswill_bot.config import config as app_config

        feedback_bonus_amount = app_config.feedback_cart_bonus
        feedback_cooldown_days = app_config.feedback_bonus_cooldown_days
        with contextlib.suppress(Exception):
            await user_store.log_event(
                user_id,
                "cart_feedback",
                {
                    "rating": "negative",
                    "reason": reason_label,
                    "cart_link": cart_url or "",
                },
            )
            bonus_result = await user_store.grant_feedback_bonus_if_due(
                user_id,
                amount=feedback_bonus_amount,
                cooldown_days=feedback_cooldown_days,
            )
            if isinstance(bonus_result, dict) and bonus_result.get("granted"):
                feedback_bonus_granted = True
                await user_store.log_event(
                    user_id,
                    "feedback_bonus_granted",
                    {
                        "amount": feedback_bonus_amount,
                        "new_limit": bonus_result.get("new_limit", 0),
                    },
                )

    # Оставляем только кнопку корзины
    if cart_url:
        await callback.message.edit_reply_markup(  # type: ignore[union-attr]
            reply_markup=_cart_only_keyboard(cart_url),
        )
    answer_text = "Спасибо! Учтём при улучшении бота \U0001f4dd"
    if feedback_bonus_granted:
        answer_text += f" Начислено +{feedback_bonus_amount} корзины."
    else:
        answer_text += f" Бонус за отзыв доступен 1 раз в {feedback_cooldown_days} дней."
    await callback.answer(answer_text)


class _IsAdminCommandFilter(BaseFilter):
    """Фильтр: сообщение начинается с /admin_.

    Используем явный BaseFilter вместо F.text.startswith —
    magic-filter может не вызывать startswith корректно
    в некоторых версиях aiogram/magic-filter.
    """

    async def __call__(self, message: Message) -> bool:
        return bool(message.text and message.text.startswith("/admin_"))


@router.message(_IsAdminCommandFilter())
async def handle_admin_unauthorized(message: Message) -> None:
    """Перехват admin-команд от неавторизованных пользователей.

    Когда AdminFilter в admin_router отклоняет сообщение (без
    побочных эффектов), команда проваливается в основной router.
    Этот хендлер ловит /admin_* и отправляет корректный отказ,
    не пропуская команду в GigaChat.
    """
    user_id = message.from_user.id if message.from_user else "?"
    cmd = message.text.split()[0] if message.text else "?"
    logger.warning(
        "Admin-команда отклонена: user=%s cmd=%s",
        user_id,
        cmd,
    )
    await message.answer("У вас нет прав администратора.")


@router.message(F.text)
async def handle_text(
    message: Message,
    chat_engine: ChatEngineProtocol,
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

    # Implicit consent: если пользователь отправил текст без явного согласия,
    # фиксируем факт использования как implicit consent (ADR-002)
    if user_store is not None:
        with contextlib.suppress(Exception):
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
        # Удаляем прогресс-сообщение перед отправкой ответа
        if progress_msg is not None:
            with contextlib.suppress(Exception):
                await progress_msg.delete()

    preview = build_telegram_delivery_preview(response)
    for i, chunk in enumerate(preview.chunks):
        is_last = i == len(preview.chunks) - 1
        await message.answer(chunk, reply_markup=preview.cart_keyboard if is_last else None)


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
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=config.telegram_handler_timeout_seconds,
            )


# ---------------------------------------------------------------------------
# Админ-команды (admin_router)
# ---------------------------------------------------------------------------


@admin_router.message(Command("admin_block"))
async def cmd_admin_block(
    message: Message,
    user_store: UserStore,
) -> None:
    """Заблокировать пользователя: /admin_block <user_id> <причина>."""
    if not message.from_user:
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
) -> None:
    """Разблокировать пользователя: /admin_unblock <user_id>."""
    if not message.from_user:
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
) -> None:
    """Общая статистика бота: /admin_stats."""
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
) -> None:
    """Информация о пользователе: /admin_user <user_id>."""
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


@admin_router.message(Command("admin_reset_carts"))
async def cmd_admin_reset_carts(
    message: Message,
    user_store: UserStore,
) -> None:
    """Сбросить счётчик корзин: /admin_reset_carts <user_id>."""
    if not message.text:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /admin_reset_carts &lt;user_id&gt;")
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return

    result = await user_store.reset_carts(target_id)
    if result:
        await message.answer(
            f"Счётчик корзин сброшен для {target_id}.\n"
            f"carts_created: {result['carts_created']}, "
            f"cart_limit: {result['cart_limit']}, "
            f"survey_completed: {result['survey_completed']}"
        )
    else:
        await message.answer(f"Пользователь {target_id} не найден.")


@admin_router.message(Command("admin_analytics"))
async def cmd_admin_analytics(
    message: Message,
    stats_aggregator: StatsAggregator | None = None,
) -> None:
    """Аналитика за N дней: /admin_analytics [days].

    Выводит агрегированные метрики из daily_stats:
    DAU, новые пользователи, сессии, корзины, GMV, ошибки.
    """
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
    total_trial_carts = int(s.get("total_trial_carts", 0))
    total_ref_links = int(s.get("total_referral_links", 0))
    total_ref_bonuses = int(s.get("total_referral_bonuses", 0))
    total_feedback_bonuses = int(s.get("total_feedback_bonuses", 0))
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
        f"Опросов: <b>{total_surveys}</b>\n"
        f"Trial-корзин: <b>{total_trial_carts}</b>\n"
        f"Реферальных привязок: <b>{total_ref_links}</b>\n"
        f"Реферальных бонусов: <b>{total_ref_bonuses}</b>\n"
        f"Бонусов за feedback: <b>{total_feedback_bonuses}</b>"
    )
    await message.answer(text)


@admin_router.message(Command("admin_funnel"))
async def cmd_admin_funnel(
    message: Message,
    stats_aggregator: StatsAggregator | None = None,
) -> None:
    """Воронка за N дней: /admin_funnel [days].

    Показывает пользовательскую воронку:
    Старт → Активные → Искали → Создали корзину → Лимит → Опрос.
    """
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
) -> None:
    """Выдать корзины пользователю: /admin_grant_carts <user_id> <amount>."""
    if not message.from_user:
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
) -> None:
    """Статистика по survey: /admin_survey_stats."""
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


@admin_router.message(Command("admin_cart_feedback"))
async def cmd_admin_cart_feedback(
    message: Message,
    user_store: UserStore | None = None,
) -> None:
    """Статистика обратной связи по корзинам: /admin_cart_feedback."""
    if user_store is None:
        await message.answer("База данных недоступна.")
        return

    try:
        stats = await user_store.get_cart_feedback_stats()
    except Exception as e:
        logger.error("Ошибка получения cart feedback статистики: %s", e)
        await message.answer("Ошибка получения данных.")
        return

    total = stats["total"]
    if total == 0:
        await message.answer("Пока нет отзывов по корзинам.")
        return

    pos = stats["positive"]
    neg = stats["negative"]
    sat = stats["satisfaction_pct"]

    # Причины негативного фидбека
    reason_lines = "\n".join(f"  {r['reason']}: {r['cnt']}" for r in stats["reasons"])

    # Последние негативные
    recent_lines = ""
    for r in stats.get("recent_negative", [])[:5]:
        reason = r.get("reason") or "—"
        dt = r.get("created_at")
        dt_str = dt.strftime("%d.%m %H:%M") if dt else "—"
        recent_lines += f"  \u2022 {reason} ({dt_str})\n"

    text = (
        f"<b>\U0001f4ca Обратная связь по корзинам</b>\n\n"
        f"Всего оценок: <b>{total}</b>\n"
        f"\U0001f44d Позитивных: <b>{pos}</b>\n"
        f"\U0001f44e Негативных: <b>{neg}</b>\n"
        f"Satisfaction: <b>{sat}%</b>\n"
    )

    if reason_lines:
        text += f"\n<b>Причины негатива:</b>\n{reason_lines}\n"

    if recent_lines:
        text += f"\n<b>Последние негативные:</b>\n{recent_lines}"

    await message.answer(text)
