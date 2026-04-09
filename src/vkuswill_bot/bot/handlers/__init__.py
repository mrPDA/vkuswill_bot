"""Handlers package for the Telegram bot."""

from __future__ import annotations

from aiogram import Router

# Импортируем все роутеры
from vkuswill_bot.bot.handlers.user_commands import router as user_commands_router
from vkuswill_bot.bot.handlers.survey_handlers import router as survey_handlers_router
from vkuswill_bot.bot.handlers.cart_feedback_handlers import router as cart_feedback_handlers_router
from vkuswill_bot.bot.handlers.admin_commands import router as admin_commands_router

# Импортируем функции и классы для обратной совместимости
from vkuswill_bot.bot.handlers.admin_commands import (
    AdminFilter,
    cmd_admin_block,
    cmd_admin_stats,
    cmd_admin_unblock,
    cmd_admin_user,
    cmd_admin_reset_carts,
    cmd_admin_analytics,
    cmd_admin_funnel,
    cmd_admin_grant_carts,
    cmd_admin_survey_stats,
    cmd_admin_cart_feedback,
    handle_admin_unauthorized,
)
from vkuswill_bot.bot.handlers.cart_feedback_handlers import (
    cart_feedback_positive,
    cart_feedback_negative,
    cart_feedback_reason,
    _extract_cart_url_from_keyboard,
    _cart_only_keyboard,
)
from vkuswill_bot.bot.handlers.survey_handlers import (
    is_survey_pending,
    cmd_survey,
    survey_pmf_callback,
    survey_feature_callback,
    survey_done_callback,
)
from vkuswill_bot.bot.handlers.user_commands import (
    cmd_start,
    cmd_help,
    cmd_me,
    cmd_invite,
    cmd_link_voice,
    cmd_unlink_voice,
    cmd_reset,
    cmd_privacy,
    consent_accept_callback,
    handle_text,
    _send_typing_periodically,
    _freemium_user_note,
    _process_referral_start,
)

# Импортируем функции из telegram_delivery для обратной совместимости
from vkuswill_bot.bot.telegram_delivery import (
    _extract_cart_link,
    _sanitize_telegram_html,
    _split_message,
)

# Импортируем внутреннюю переменную для обратной совместимости с тестами
# (это не публичный API, но требуется для корректной работы тестов)
from vkuswill_bot.bot.handlers.survey_handlers import is_survey_pending as _survey_pending

# Создаем главный роутер и включаем в него все подроутеры
router = Router()
router.include_router(user_commands_router)
router.include_router(survey_handlers_router)
router.include_router(cart_feedback_handlers_router)
router.include_router(admin_commands_router)

__all__ = [
    "router",
    "user_commands_router",
    "survey_handlers_router",
    "cart_feedback_handlers_router",
    "admin_commands_router",
    
    # Для обратной совместимости
    "AdminFilter",
    "cmd_admin_block",
    "cmd_admin_stats",
    "cmd_admin_unblock",
    "cmd_admin_user",
    "handle_admin_unauthorized",
    "cart_feedback_positive",
    "cart_feedback_negative",
    "cart_feedback_reason",
    "is_survey_pending",
    "cmd_start",
    "cmd_help",
    "cmd_me",
    "cmd_invite",
    "cmd_link_voice",
    "cmd_unlink_voice",
    "cmd_reset",
    "cmd_privacy",
    "consent_accept_callback",
    "handle_text",
    "_send_typing_periodically",
    "_freemium_user_note",
    "_process_referral_start",
    "_extract_cart_url_from_keyboard",
    "_cart_only_keyboard",
    "_extract_cart_link",
    "_sanitize_telegram_html",
    "_split_message",
    "_survey_pending",
]