"""Обработчики команд и сообщений Telegram-бота.

Этот файл был рефакторингом и теперь служит только для обратной совместимости.
Все функции перемещены в отдельные модули в пакете vkuswill_bot.bot.handlers.
"""

from __future__ import annotations

# Импортируем все из новых модулей для обратной совместимости
from vkuswill_bot.bot.handlers import (
    admin_commands_router,
    cart_feedback_handlers_router,
    router,
    survey_handlers_router,
    user_commands_router,
)

# Экспортируем все, как раньше
__all__ = [
    "admin_commands_router",
    "cart_feedback_handlers_router",
    "router",
    "survey_handlers_router",
    "user_commands_router",
    # Старые функции, которые были в этом файле
    "_freemium_user_note",
    "is_survey_pending",
    "_extract_cart_url_from_keyboard",
    "_cart_only_keyboard",
]