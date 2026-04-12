"""Handlers package for the Telegram bot."""

from __future__ import annotations

from aiogram import Router

# Импортируем все роутеры
from vkuswill_bot.bot.handlers.user_commands import router as user_commands_router
from vkuswill_bot.bot.handlers.survey_handlers import router as survey_handlers_router
from vkuswill_bot.bot.handlers.cart_feedback_handlers import router as cart_feedback_handlers_router
from vkuswill_bot.bot.handlers.admin_commands import router as admin_commands_router

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
]
__all__.sort()
